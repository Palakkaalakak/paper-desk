"""Read-only IB Gateway streaming engine, owned by one continuously running asyncio loop.
HTTP workers only submit subscriptions or read immutable snapshots; slow contract
lookups never hold up ticks. This module never imports or constructs order objects.
"""
import asyncio
import concurrent.futures
import datetime as dt
import math
import os
import re
import threading
import time

MAX_INSTRUMENTS = 2000
CLIENT_TTL = 60
try:
    from zoneinfo import ZoneInfo
    NEW_YORK = ZoneInfo('America/New_York')
except Exception:  # no tz database (bare Windows Python): UTC is a safe superset
    NEW_YORK = dt.timezone.utc
# Fatal per-contract market-data errors (IB error-codes page) -> seconds before
# retrying. 101: no free market-data line; 200: no such contract; 354/10089/10168/
# 10186: no (API) market-data subscription for this instrument.
CONTRACT_ERRORS = {101:20, 200:600, 354:120, 10089:300, 10168:120, 10186:120}
# Partial: data keeps flowing but something is missing (e.g. greeks need the
# underlying's subscription too, or IB substituted delayed data).
CONTRACT_NOTICES = {10090, 10091, 10167}
# Farm notices: 2103/2105/2157 broken, 2104/2106/2158 OK.
FARM_CODES = {2103:('market',False), 2104:('market',True), 2105:('history',False),
              2106:('history',True), 2157:('secdef',False), 2158:('secdef',True)}


def standard_classes(params, symbol):
    """SMART chains of ordinary 100-multiplier classes. Adjusted classes after
    corporate actions carry digits (e.g. 2AMZN / AMZN1) and non-standard deliverables."""
    return [p for p in params or [] if p.exchange == 'SMART' and str(p.multiplier) in ('100','100.0','')
            and re.fullmatch(r'[A-Z]{1,6}', p.tradingClass or '')]


def option_greeks(t):
    """IB's model greeks (what TWS shows) first, then bid/ask/last computations.
    IB marks missing values with -1 (iv/price) or -2 (greeks); ib_async 2.1 strips
    -2 for delta/gamma but passes it through for vega and theta."""
    for name in ('modelGreeks','bidGreeks','askGreeks','lastGreeks'):
        g = getattr(t,name,None)
        delta = number(getattr(g,'delta',None)) if g else None
        if delta is None or not -1 <= delta <= 1:
            continue
        def clean(attr, missing):
            v = number(getattr(g,attr,None))
            return None if v is None or v == missing else v
        out = dict(delta=delta, iv=clean('impliedVol',-1), gamma=clean('gamma',-2),
                   theta=clean('theta',-2), vega=clean('vega',-2),
                   undPrice=clean('undPrice',-1), optPrice=clean('optPrice',-1), source=name[:-6])
        if out['iv'] is not None and out['iv'] < 0: out['iv'] = None
        if out['vega'] is not None and out['vega'] < 0: out['vega'] = None
        return out
    return None


def number(value, price=False):
    try:
        n = float(value)
        return n if math.isfinite(n) and (not price or n >= 0) else None
    except (ValueError, TypeError):
        return None


def instruments(rows):
    if not isinstance(rows, list) or len(rows) > MAX_INSTRUMENTS:
        raise ValueError('Expected at most %s instruments' % MAX_INSTRUMENTS)
    out = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Invalid instrument')
        cid = row.get('conid')
        if isinstance(cid, bool) or not isinstance(cid, int) or not 0 < cid < 2**53:
            raise ValueError('Each instrument needs a positive integer contract ID')
        symbol = str(row.get('symbol') or '').strip().upper()
        kind = str(row.get('secType') or 'STK').upper()
        if not symbol or len(symbol) > 120 or kind not in ('STK','OPT','FUT','FOP','IND','CASH'):
            raise ValueError('Invalid symbol or security type')
        out[cid] = dict(conid=cid, symbol=symbol, secType=kind,
                        exch=str(row.get('exch') or 'SMART')[:40],
                        expiry=str(row.get('expiry') or '')[:16],
                        right=str(row.get('right') or '')[:1],
                        strike=number(row.get('strike')), mult=number(row.get('mult')),
                        brokerId=row.get('brokerId') is True,
                        priority=max(0, min(3, number(row.get('priority')) or 0)))
    return out


class MarketEngine:
    def __init__(self, capacity=None):
        self.capacity = max(1, int(capacity or os.environ.get('PAPER_TWS_LINES', '100')))
        self.changed = threading.Condition()
        self.clients, self.quotes, self.versions = {}, {}, {}
        self.seq = 1
        self.info = dict(connected=False, readonly=True, sealed=True, active=0,
                         problem='Connecting to IB Gateway', generation=0)
        self._thread = None
        self._start_lock = threading.Lock()
        self._ready, self._stop = threading.Event(), threading.Event()
        self._ib, self._loop, self._connect_task = None, None, None
        self._active, self._loading, self._failed = {}, {}, {}
        self._contracts, self._cache, self._inflight, self._ticker_ids = {}, {}, {}, {}
        self._retry_at, self._last_received = 0, 0
        self._farms, self._notes, self._symbol_lock, self._symbol_at = {}, {}, None, 0

    def start(self):
        with self._start_lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name='ib-market-owner', daemon=True)
                self._thread.start()
        if not self._ready.wait(2):
            raise RuntimeError('Market-data loop did not start')

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        try:
            self._loop.run_until_complete(self._maintain())
        finally:
            if self._ib:
                self._ib.disconnect()
            tasks = asyncio.all_tasks(self._loop)
            for task in tasks:
                task.cancel()
            if tasks:
                self._loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
            self._loop.close()

    def close(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _status(self, **changes):
        with self.changed:
            if any(self.info.get(k) != v for k,v in changes.items()):
                self.info = dict(self.info, **changes)
                self.seq += 1
                self.changed.notify_all()

    def subscribe(self, client, rows):
        if not isinstance(client, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', client):
            raise ValueError('Invalid stream client ID')
        desired = instruments(rows)
        with self.changed:
            now = time.monotonic()
            self.clients = {k:v for k,v in self.clients.items() if now-v[0] < CLIENT_TTL}
            if client not in self.clients and len(self.clients) >= 32:
                raise ValueError('Too many active browser sessions')
            self.clients[client] = (now, desired)
        self.start()
        return self.snapshot(client)

    def snapshot(self, client, since=0):
        with self.changed:
            entry = self.clients.get(client)
            wanted = entry[1] if entry else {}
            if entry:
                self.clients[client] = (time.monotonic(), wanted)
            quotes = {str(cid):self.quotes[cid] for cid in wanted
                      if cid in self.quotes and self.versions.get(cid,0) > since}
            loaded = sum(1 for cid in wanted if cid in self.quotes and
                         self.quotes[cid].get('bid') is not None and self.quotes[cid].get('ask') is not None)
            return dict(self.info, quotes=quotes, seq=self.seq, requested=len(wanted), loaded=loaded,
                        capacity=self.capacity, source='IB Gateway', receivedAt=self._last_received,
                        serverTime=int(time.time()*1000))

    def wait(self, client, since, timeout=10):
        with self.changed:
            self.changed.wait_for(lambda:self.seq > since or self._stop.is_set(), timeout)
        return self.snapshot(client, since)

    def publish(self, cid, quote):
        with self.changed:
            self.seq += 1
            self.quotes[cid] = quote
            self.versions[cid] = self.seq
            self.changed.notify_all()

    def _quote(self, t, received=None):
        now = received or int(time.time()*1000)
        stamp = getattr(t, 'time', None)
        q = {k:number(getattr(t,k,None), k in ('bid','ask','last'))
             for k in ('bid','ask','last','bidSize','askSize','volume')}
        trade_stamps=[x for x in (getattr(t,'lastTimestamp',None),getattr(t,'rtTime',None)) if isinstance(x,dt.datetime)]
        q.update(tradeAt=max((int(x.timestamp()*1000) for x in trade_stamps),default=None),
                 prevClose=number(getattr(t,'close',None),True),
                 status={1:'LIVE',2:'FROZEN',3:'DELAYED',4:'DELAYED FROZEN'}.get(getattr(t,'marketDataType',1),'UNKNOWN'),
                 halted=number(getattr(t,'halted',None)) in (1,2), source='IB Gateway',
                 at=int(stamp.timestamp()*1000) if isinstance(stamp,dt.datetime) else now,
                 receivedAt=now, brokerConid=getattr(t.contract,'conId',0))
        if q['bid'] is not None and q['ask'] is not None and q['ask'] >= q['bid']:
            q['mid'] = (q['bid']+q['ask'])/2
        if q['prevClose'] and q['last'] is not None:
            q['change'] = q['last']-q['prevClose']
            q['changePct'] = q['change']/q['prevClose']*100
        greeks = option_greeks(t)
        if greeks:
            q['greeks'] = greeks
        return q

    def _ticks(self, tickers):
        now = int(time.time()*1000)
        self._last_received = now
        for t in tickers:
            ids = self._ticker_ids.get(id(t),())
            if ids:
                q = self._quote(t,now)
                for cid in tuple(ids):
                    note = self._notes.get(cid)
                    self.publish(cid,dict(q,notice=note) if note else q)

    def _error(self, req_id, code, text, contract=None):
        if code in (1100,1101,1102):
            if code == 1101:
                self._clear_active()
            self._status(problem=None if code in (1101,1102) else str(text), feedHealthy=code != 1100)
        elif code == 317:
            for entry in getattr(self,'_depth_streams',{}).values():
                if contract is None or entry['contract'].conId==contract.conId:
                    entry['at']=0;entry['revision']+=1
                    entry['ticker'].domBids.clear();entry['ticker'].domAsks.clear()
        elif code in FARM_CODES:
            self._farms[FARM_CODES[code][0]] = FARM_CODES[code][1]
        elif code in CONTRACT_ERRORS:
            cid = self._cid_for(req_id, contract)
            if cid is None:
                self._status(problem='%s: %s' % (code,text))
                return
            # The subscription stays dead after these: free its line and retry later
            # instead of leaving a permanently empty quote that looks like it is loading.
            self._remove(cid)
            self._failed[cid] = time.monotonic()+CONTRACT_ERRORS[code]
            self.publish(cid,dict(error='%s: %s' % (code,text),errorCode=code,status='ERROR',at=0))
        elif code in CONTRACT_NOTICES:
            cid = self._cid_for(req_id, contract)
            if cid is not None:
                self._notes[cid] = '%s: %s' % (code,text)
        elif code in (100,10197):
            self._status(problem='%s: %s' % (code,text))

    def _cid_for(self, req_id, contract=None):
        # Only this engine's own streaming request: ib_async shares one Ticker per
        # contract, so a snapshot or BPSX request for the same conId must not be
        # mistaken for the browser stream (its line/permission errors are its own).
        wrapper = getattr(self._ib,'wrapper',None)
        ticker = getattr(wrapper,'reqId2Ticker',{}).get(req_id)
        if ticker is None or getattr(wrapper,'ticker2ReqId',{}).get('mktData',{}).get(ticker) != req_id:
            return None
        return next((cid for cid,row in self._active.items() if row['ticker'] is ticker), None)

    def farm_problem(self, kind):
        return self._farms.get(kind) is False

    def _clear_active(self):
        import bpsx_stream
        bpsx_stream.close_all(self)
        for sym in list(getattr(self,'_depth_streams',{})): self._close_depth(sym)
        for task in list(self._loading.values()):
            task.cancel()
        self._loading.clear()
        for cid in list(self._active):
            self._remove(cid)
        self._failed.clear()
        with self.changed:
            self.quotes.clear()
            self.versions.clear()
        self._status(active=0, generation=self.info['generation']+1)

    async def _connect(self):
        try:
            import ib_async as m
        except ImportError:
            raise RuntimeError('Install IB Gateway support: python3 -m pip install -r requirements.txt')
        import tws
        ib = m.IB()
        tws._seal(ib)
        tws._seal(ib.client)
        ib.errorEvent += self._error
        ib.pendingTickersEvent += self._ticks
        host = os.environ.get('PAPER_TWS_HOST','127.0.0.1')
        ports = [int(p) for p in os.environ.get('PAPER_TWS_PORTS','4001,4002,7496,7497').split(',')]
        last = ''
        for port in ports:
            try:
                await ib.connectAsync(host,port,clientId=int(os.environ.get('PAPER_TWS_CLIENT_ID','77')),
                                      timeout=15,readonly=True,fetchFields=m.StartupFetchNONE)
                if ib.isConnected():
                    self._ib = ib
                    ib.client.cancelPositions()
                    # Unsubscribe through Client, not IB's blocking subscription helper.
                    ib.client.reqAccountUpdates(False,'')
                    for name in ('positions','portfolio','accountValues'):
                        store = getattr(ib.wrapper,name,None)
                        if hasattr(store,'clear'):
                            store.clear()
                    ib.reqMarketDataType(3 if os.environ.get('PAPER_TWS_DATA') == 'delayed' else 1)
                    self._status(connected=True, sealed=tws.is_sealed(ib) and tws.is_sealed(ib.client),
                                 problem=None,port=port,feedHealthy=True)
                    return ib
            except Exception as e:
                last = str(e) or type(e).__name__
                ib.disconnect()
        raise ConnectionError('IB Gateway unavailable on %s (%s). Enable its read-only socket API. %s' %
                              (host,', '.join(map(str,ports)),last))

    async def _ensure(self):
        if self._ib and self._ib.isConnected():
            return self._ib
        if self._connect_task and not self._connect_task.done():
            return await asyncio.shield(self._connect_task)
        if time.monotonic() < self._retry_at:
            raise ConnectionError(self.info.get('problem') or 'Reconnecting')
        self._connect_task = asyncio.create_task(self._connect())
        try:
            return await asyncio.shield(self._connect_task)
        except Exception as e:
            self._retry_at = time.monotonic()+5
            self._status(connected=False,problem=str(e),feedHealthy=False)
            raise

    async def _maintain(self):
        while not self._stop.is_set():
            now = time.monotonic()
            with self.changed:
                self.clients = {k:v for k,v in self.clients.items() if now-v[0] < CLIENT_TTL}
                desired = {}
                for _,rows in self.clients.values():
                    for cid,row in rows.items():
                        if cid not in desired or row['priority'] > desired[cid]['priority']:
                            desired[cid] = row
                has_clients = bool(self.clients)
            if self._ib and not self._ib.isConnected() and self.info.get('connected'):
                self._status(connected=False,problem='IB Gateway disconnected; reconnecting',feedHealthy=False)
                self._clear_active()
            if has_clients:
                try:
                    await self._ensure()
                except Exception:
                    await asyncio.sleep(0.5)
                    continue
            if self._ib and self._ib.isConnected() and getattr(self, '_guns_streams', None):
                from guns_data import close_idle
                close_idle(self)
            for sym,entry in list(getattr(self,'_depth_streams',{}).items()):
                if now-entry['used']>20: self._close_depth(sym)
            wanted = dict(sorted(desired.items(), key=lambda x: -x[1]['priority'])[:self.capacity])
            for cid in list(self._active):
                if cid not in wanted:
                    self._remove(cid)
            for cid,task in list(self._loading.items()):
                if cid not in wanted:
                    task.cancel()
            for cid,row in wanted.items():
                if cid not in self._active and cid not in self._loading and now >= self._failed.get(cid,0):
                    self._loading[cid] = asyncio.create_task(self._subscribe_one(cid,row))
            self._status(active=len(self._active),pending=len(self._loading),
                         overCapacity=max(0,len(desired)-self.capacity))
            with self.changed:
                for cid in list(self.quotes):
                    if cid not in desired:
                        self.quotes.pop(cid,None)
                        self.versions.pop(cid,None)
            await asyncio.sleep(0.1)

    def _remove(self,cid):
        self._notes.pop(cid,None)
        row = self._active.pop(cid,None)
        if not row:
            return
        ids = self._ticker_ids.get(id(row['ticker']),set())
        ids.discard(cid)
        if not ids:
            self._ticker_ids.pop(id(row['ticker']),None)
            try:
                self._ib.cancelMktData(row['contract'])
            except Exception:
                pass

    async def _contract(self,row):
        import ib_async as m
        cid,kind,sym = row['conid'],row['secType'],row['symbol']
        key = (cid,kind,sym,bool(row.get('brokerId')))
        if key in self._contracts:
            return self._contracts[key]
        legacy = 900000000 <= cid < 990000000 and not row.get('brokerId')
        if not legacy:
            c = m.Contract(conId=cid,secType=kind,exchange=row.get('exch') or 'SMART',currency='USD')
        elif kind == 'STK':
            c = m.Stock(sym,'SMART','USD')
        elif kind == 'OPT' and row.get('expiry') and row.get('right') in ('C','P') and row.get('strike') is not None:
            c = m.Option(sym.split()[0],row['expiry'],row['strike'],row['right'],'SMART',currency='USD')
        elif kind == 'OPT':
            match = re.fullmatch(r'([A-Z.]+)\s+(\d{8})\s+([\d.]+)\s+([CP])',sym)
            if not match:
                raise ValueError('Re-select this legacy option from the IB Gateway chain')
            root,expiry,strike,right = match.groups()
            c = m.Option(root,expiry,float(strike),right,'SMART',currency='USD')
        else:
            raise ValueError('Re-select this legacy contract from IB Gateway')
        if legacy:
            qualified = await asyncio.wait_for(self._ib.qualifyContractsAsync(c),30)
            if not qualified or not c.conId:
                raise ValueError('IB Gateway did not resolve '+sym)
        self._contracts[key] = c
        if len(self._contracts)>10000:
            self._contracts.pop(next(iter(self._contracts)))
        return c

    async def _subscribe_one(self,cid,row):
        ib,generation=self._ib,self.info.get('generation',0)
        try:
            c = await self._contract(row)
            if self._ib is not ib or self.info.get('generation',0)!=generation:
                return
            # Market type 2 still streams live data when it exists, and falls back to
            # the last recorded quote (labelled FROZEN) when the market is closed, so
            # after-hours chains show IB's last bid/ask/greeks instead of -1 blanks.
            # Switched just around this request; no await in between.
            frozen = os.environ.get('PAPER_TWS_DATA') != 'delayed' and self._data_type(ib,2)
            try:
                ticker = ib.reqMktData(c,'233' if getattr(c,'secType',None)=='STK' else '',False,False)
            finally:
                if frozen: self._data_type(ib,1)
            self._active[cid] = dict(contract=c,ticker=ticker)
            self._ticker_ids.setdefault(id(ticker),set()).add(cid)
            if any(number(getattr(ticker,k,None),True) is not None for k in ('bid','ask','last')):
                self.publish(cid,self._quote(ticker))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._failed[cid] = time.monotonic()+15
            self.publish(cid,dict(error=str(e),status='ERROR',at=0))
        finally:
            self._loading.pop(cid,None)

    @staticmethod
    def _data_type(ib, kind):
        try:
            ib.client.reqMarketDataType(kind)
            return True
        except Exception:
            return False

    def call(self,name,*args,timeout=40):
        self.start()
        future = asyncio.run_coroutine_threadsafe(self._request(name,*args),self._loop)
        try:
            return future.result(timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise TimeoutError('IB Gateway is still resolving this request; streaming quotes continue')

    async def _request(self,name,*args):
        await self._ensure()
        key = (name,)+args
        hit = self._cache.get(key)
        if hit and time.monotonic()<hit[0]:
            return hit[1]
        if key not in self._inflight:
            async def work():
                try:
                    result = await getattr(self,'_'+name)(*args)
                    ttl = 0 if name in ('bpsx_chain','bcs_chain','bpsx_quotes','bpsx_close') else 30 if name == 'bpsx_chart' else 15 if name == 'vmi_observation' else 21600 if name == 'guns_float' and result.get('status')=='returned' else 30 if name == 'guns_float' else 3600 if name in ('guns_schedule','guns_capabilities') else .25 if name == 'guns_bars' else 0 if name == 'guns_scan' else 10 if name == 'guns_verify' else 30 if name == 'guns_news' else 300 if name in ('search','chain') or name == 'guns_article' and result.get('contentStatus') in ('body_returned','brief','pdf') else 1
                    self._cache[key] = (time.monotonic()+(ttl if result else 0),result)
                    if len(self._cache)>1000:
                        self._cache.pop(next(iter(self._cache)))
                    return result
                finally:
                    self._inflight.pop(key,None)
            self._inflight[key] = asyncio.create_task(work())
            # A client can disconnect/time out while shared work continues.
            # Retrieve failures even if no HTTP waiter remains.
            self._inflight[key].add_done_callback(lambda t: None if t.cancelled() else t.exception())
        return await asyncio.shield(self._inflight[key])

    async def _matching_symbols(self,pattern):
        # IB: "There must be an interval of at least 1 second between successive calls
        # to reqMatchingSymbols". A call inside that interval is not answered and
        # ib_async gives up after 4s with None, which is how a debounced search for
        # A/AM/AMZ/AMZN used to come back as "No tradable match".
        if self._symbol_lock is None:
            self._symbol_lock = asyncio.Lock()
        async with self._symbol_lock:
            wait = self._symbol_at+1.1-time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                return await self._ib.reqMatchingSymbolsAsync(pattern) or []
            finally:
                self._symbol_at = time.monotonic()

    async def _search(self,pattern):
        # Retry an empty answer once, then fall back to qualifying the typed text as an
        # exact stock symbol, which does not depend on the symbol-search service at all.
        keep=('STK','IND')
        rows=await self._matching_symbols(pattern)
        if not rows:
            rows=await self._matching_symbols(pattern)
        out=[dict(symbol=r.contract.symbol,name=getattr(r.contract,'description','') or r.contract.symbol,
                  conid=r.contract.conId,exchange=r.contract.primaryExchange,type=r.contract.secType)
             for r in rows or [] if r.contract.secType in keep]
        sym=pattern.strip().upper()
        if re.fullmatch(r'[A-Z0-9.\-]{1,12}',sym) and not any(r['symbol']==sym for r in out):
            try:
                c=await self._stock(sym)
                out.insert(0,dict(symbol=c.symbol,name=getattr(c,'description','') or c.symbol,
                                  conid=c.conId,exchange=c.primaryExchange,type=c.secType))
            except Exception:
                pass
        return out[:30]

    async def _stock(self,symbol):
        import ib_async as m
        key = ('stock',symbol)
        if key not in self._contracts:
            c = m.Stock(symbol,'SMART','USD')
            if not await self._ib.qualifyContractsAsync(c) or not c.conId:
                raise ValueError('Could not resolve '+symbol)
            self._contracts[key] = c
        return self._contracts[key]

    async def _vmi_stock(self, symbol):
        from vmi_data import symbol as validate_symbol
        c = await self._stock(validate_symbol(symbol))
        if c.secType != 'STK' or c.currency != 'USD' or not c.conId:
            raise ValueError('Expected a verified USD stock contract')
        return dict(symbol=c.symbol, conid=c.conId, secType=c.secType,
                    currency=c.currency, exch='SMART', mult=1, served_by='tws')

    async def _vmi_observation(self, symbol):
        from vmi_ib import observe
        return await observe(self, symbol)

    async def _bpsx_scan(self):
        from bpsx_data import scan
        return await scan(self)

    async def _bpsx_stock(self, symbol):
        from bpsx_data import stock
        return await stock(self, symbol)

    async def _bpsx_chain(self, symbol, expiry=None, spot=None, ids=None, mode='live', count=25, center=90):
        from bpsx_data import options
        return await options(self, symbol, expiry, spot, ids, mode, count, center)

    async def _bcs_chain(self, symbol, expiry=None, spot=None, ids=None, mode='live', count=25):
        from bpsx_data import call_options
        return await call_options(self, symbol, expiry, spot, ids, mode, count)

    async def _bpsx_quotes(self, sid):
        import bpsx_stream
        return bpsx_stream.read(self, sid)

    async def _bpsx_close(self, sid):
        import bpsx_stream
        session=bpsx_stream.sessions(self).pop(sid,None)
        if session: session.close()
        return {'closed':True}

    async def _bpsx_chart(self, symbol, frame='d'):
        from bpsx_data import chart
        return await chart(self, symbol, frame)

    def _open_chain_stream(self, contract):
        # Isolated ticker and request ID: never overwrite the browser's mktData
        # ownership map, and never cancel another subscription for the same conId.
        from ib_async import Ticker
        ib, generation = self._ib, self.info.get('generation',0)
        req_id = ib.client.getReqId()
        ticker = Ticker(contract=contract, defaults=ib.wrapper.defaults)
        handle = dict(ib=ib,generation=generation,id=req_id,ticker=ticker,errors=[])
        def error(rid,code,text,*args):
            if rid==req_id: handle['errors'].append(dict(code=code,message=str(text)))
        handle['errorHandler']=error
        ib.errorEvent += error
        ib.wrapper.reqId2Ticker[req_id]=ticker
        ib.wrapper._reqId2Contract[req_id]=contract
        try:
            ib.client.reqMktData(req_id,contract,'',False,False,[])
        except BaseException:
            self._close_chain_stream(handle)
            raise
        return handle

    def _close_chain_stream(self, handle):
        ib,rid,ticker=handle['ib'],handle['id'],handle['ticker']
        ib.errorEvent -= handle['errorHandler']
        if self._ib is ib and self.info.get('generation',0)==handle['generation']:
            try: ib.client.cancelMktData(rid)
            except Exception: pass
        if ib.wrapper.reqId2Ticker.get(rid) is ticker:
            ib.wrapper.reqId2Ticker.pop(rid,None)
            ib.wrapper._reqId2Contract.pop(rid,None)
        ib.wrapper.pendingTickers.discard(ticker)

    async def _vmi_chain(self, symbol, expiry=None):
        from vmi_options import chain
        return await chain(self, symbol, expiry)

    async def _vmi_option(self, symbol, conid):
        from vmi_options import quote
        return await quote(self, symbol, conid)

    @staticmethod
    def _screen_quote_complete(q,now):
        return (q.get('status')=='LIVE' and not q.get('halted') and not q.get('error') and
                all(number(q.get(k),True) is not None and q[k]>0 for k in ('bid','ask','last')) and
                q['ask']>=q['bid'] and isinstance(q.get('at'),(int,float)) and 0<=now-q['at']<15000)

    async def _snapshot_quote(self,c,require_greeks=False):
        # ib_async 2.1 keys tickers by conId. A temporary streaming request can
        # overwrite ticker2ReqId['mktData']; cancelMktData(contract) then cancels
        # the browser stream. Snapshot ownership and cancellation must use IDs.
        ib,generation=self._ib,self.info.get('generation',0)
        req_id=ib.client.getReqId()
        future=ib.wrapper.startReq(req_id,c)
        ticker=ib.wrapper.startTicker(req_id,c,'snapshot')
        try:
            requested_at=int(time.time()*1000);expires=time.monotonic()+12
            ib.client.reqMktData(req_id,c,'',True,False,[])
            while True:
                if self._ib is not ib or self.info.get('generation',0)!=generation:
                    raise ConnectionError('Gateway changed during quote snapshot')
                q=self._quote(ticker)
                complete=self._screen_quote_complete(q,int(time.time()*1000))
                if require_greeks:
                    complete=(q.get('status')=='LIVE' and not q.get('error') and not q.get('halted')
                        and number(q.get('bid'),True) is not None and number(q.get('ask'),True) is not None
                        and q['bid']>0 and q['ask']>=q['bid'] and number(q.get('greeks',{}).get('delta')) is not None)
                if complete and q['at']>=requested_at:
                    return q  # Do not wait ~11s for tickSnapshotEnd after the fields arrive.
                if future.done():
                    future.result()  # Propagate request errors rather than returning fabricated data.
                    return q
                left=expires-time.monotonic()
                if left<=0:raise TimeoutError('IBKR quote acquisition deadline')
                await asyncio.wait([future],timeout=min(.05,left))
        finally:
            if self._ib is ib and self.info.get('generation',0)==generation:
                try: ib.client.cancelMktData(req_id)
                except Exception: pass
            if ib.wrapper.ticker2ReqId['snapshot'].get(ticker)==req_id:
                ib.wrapper.endTicker(ticker,'snapshot')
            ib.wrapper.reqId2Ticker.pop(req_id,None)
            ib.wrapper._endReq(req_id)

    async def _quote_symbol(self,symbol):
        c = await self._stock(symbol)
        existing=next((v['ticker'] for v in self._active.values() if v['contract'].conId==c.conId),None)
        if existing is not None:
            q=self._quote(existing)
            if self._screen_quote_complete(q,int(time.time()*1000)):
                return q
        return await self._snapshot_quote(c)

    async def _guns_ib_quote(self,conid):
        if not str(conid).isdigit() or not 0<int(conid)<2**53:
            raise ValueError('Invalid quote contract ID')
        from ib_async import Contract
        cid=int(conid)
        active=self._active.get(cid)
        if active:
            q=self._quote(active['ticker'])
            if self._screen_quote_complete(q,int(time.time()*1000)):
                return q
        c=active['contract'] if active else Contract(conId=cid,secType='STK',exchange='SMART',currency='USD')
        q=await self._snapshot_quote(c)
        if not self._screen_quote_complete(q,int(time.time()*1000)):
            raise ValueError('IBKR snapshot did not return current two-sided market and trade')
        if q.get('brokerConid')!=cid:
            raise ValueError('IBKR snapshot contract mismatch')
        q['screeningOnly']=True
        return q

    async def _option_params(self,symbol,und):
        key = ('params',symbol)
        hit = self._cache.get(key)
        if hit and time.monotonic()<hit[0]:
            return hit[1]
        params = await asyncio.wait_for(self._ib.reqSecDefOptParamsAsync(symbol,'','STK',und.conId),30) or []
        # An empty answer is usually a sec-def farm hiccup, never cache it.
        self._cache[key] = (time.monotonic()+(900 if params else 0),params)
        return params

    async def _chain(self,symbol,expiry=None):
        import ib_async as m
        import tws
        und = await self._stock(symbol)
        params = await self._option_params(symbol,und)
        classes = standard_classes(params,symbol)
        if not classes:
            if self.farm_problem('secdef'):
                raise ValueError('IB\'s security-definition farm is disconnected, so option chains '
                                 'cannot be listed right now. It usually reconnects within a minute.')
            raise ValueError('IB lists no standard (100-share) option chain for '+symbol+
                             '. Check the symbol has listed options and your account has option permissions.')
        today = dt.datetime.now(NEW_YORK).strftime('%Y%m%d')
        by_expiry = {}
        # SPX/SPXW, NDX/NDXP, RUT/RUTW...: weeklies live in a second trading class.
        for p in classes:
            for x in p.expirations:
                if re.fullmatch(r'\d{8}',x) and x>=today and (x not in by_expiry or p.tradingClass==symbol):
                    by_expiry[x] = p.tradingClass
        exps = sorted(by_expiry)
        out = dict(expirations=exps,underConid=und.conId,calls=[],puts=[],served_by='tws')
        if not expiry:
            return out
        if expiry not in by_expiry:
            raise ValueError('Expiry is not listed for '+symbol)
        klass = by_expiry[expiry]
        stored = tws._disk().get(symbol+'|'+expiry)
        rows = None
        if isinstance(stored,dict) and time.time()-float(stored.get('ts',0)) < tws.DEF_TTL:
            rows = [r for r in stored.get('rows') or [] if r.get('tradingClass') in (klass,'',None)]
        if rows:
            contracts = [m.Option(symbol,expiry,r['strike'],r['right'],'SMART',multiplier='100',conId=r['conId'],
                         tradingClass=r.get('tradingClass') or klass,currency='USD') for r in rows]
        else:
            # IB: derivative lookups are internally paced; give every field except
            # strike/right so this one request returns exactly one expiry of one class.
            # (Unticking "Expose entire trading schedule to the API" in Gateway makes
            # this reply several times smaller and faster.)
            template = m.Option(symbol,expiry,0,'','SMART',multiplier='100',tradingClass=klass,currency='USD')
            details = await asyncio.wait_for(self._ib.reqContractDetailsAsync(template),90) or []
            contracts = [d.contract for d in details if d.contract and d.contract.conId and
                         d.contract.secType=='OPT' and d.contract.right in ('C','P') and
                         d.contract.lastTradeDateOrContractMonth==expiry and d.contract.tradingClass==klass and
                         str(d.contract.multiplier) in ('100','100.0')]
            if not contracts:
                if self.farm_problem('secdef'):
                    raise ValueError('IB\'s security-definition farm is disconnected; try this expiry again in a minute.')
                raise ValueError('IB returned no %s option contracts expiring %s. Try again; if it repeats, '
                                 'the expiry may have just been delisted.' % (symbol,expiry))
            tws._disk_save((symbol,expiry),[dict(conId=c.conId,strike=c.strike,right=c.right,
                                                tradingClass=c.tradingClass) for c in contracts])
        out.update(expiry=expiry,tradingClass=klass)
        for c in sorted(contracts,key=lambda x:(x.strike,x.right)):
            if c.right not in ('C','P'):
                continue
            out['calls' if c.right=='C' else 'puts'].append(dict(conid=c.conId,
                symbol='%s %s %s %s' % (symbol,expiry,c.strike,c.right),strike=c.strike,right=c.right,
                expiry=expiry,mult=100,bid=None,ask=None,last=None))
        return out

    async def _guns_float(self, symbol):
        from guns_data import float_data
        return await float_data(self, symbol)

    async def _guns_schedule(self):
        from guns_data import schedule
        return await schedule(self)

    async def _guns_scan(self):
        from guns_data import scan
        return await scan(self)

    async def _guns_bars(self, symbol):
        from guns_data import bars
        return await bars(self, symbol)

    async def _guns_capabilities(self):
        from guns_data import scanner_capabilities
        return await scanner_capabilities(self)

    async def _guns_news(self, symbol):
        from guns_data import news
        return await news(self, symbol)

    async def _guns_verify(self, conid, include_comparison=True):
        from guns_data import verify
        return await verify(self, conid, include_comparison)

    async def _guns_article(self, provider, article_id):
        from guns_data import article
        return await article(self, provider, article_id)

    def _close_depth(self,symbol):
        entry=getattr(self,'_depth_streams',{}).pop(symbol,None)
        if not entry: return
        entry['ticker'].updateEvent-=entry['handler']
        if self._ib is entry['ib'] and self.info.get('generation',0)==entry['generation']:
            try: self._ib.cancelMktDepth(entry['contract'],isSmartDepth=True)
            except Exception: pass

    async def _depth(self,symbol):
        from guns_data import symbol as validate_symbol
        symbol=validate_symbol(symbol)
        if not hasattr(self,'_depth_streams'): self._depth_streams={}
        entry=self._depth_streams.get(symbol)
        if entry and (entry['ib'] is not self._ib or entry['generation']!=self.info.get('generation',0)):
            self._close_depth(symbol);entry=None
        if entry is None:
            ib,generation=self._ib,self.info.get('generation',0)
            c=await self._stock(symbol)
            if self._ib is not ib or self.info.get('generation',0)!=generation:
                raise ConnectionError('Gateway changed during depth qualification')
            # Stock qualification yields; another task may have acquired depth.
            entry=self._depth_streams.get(symbol)
            if entry is None:
                if len(self._depth_streams)>=3:
                    oldest=min(self._depth_streams,key=lambda s:self._depth_streams[s]['used'])
                    self._close_depth(oldest)
                t=self._ib.reqMktDepth(c,numRows=5,isSmartDepth=True)
                t.domBids.clear();t.domAsks.clear()
                self._depth_serial=getattr(self,'_depth_serial',0)+1
                entry=dict(ib=self._ib,generation=self.info.get('generation',0),contract=c,ticker=t,used=time.monotonic(),at=0,revision=0,stream=self._depth_serial)
                def update(ticker):
                    if (self._depth_streams.get(symbol) is entry and self._ib is entry['ib'] and
                            self.info.get('generation',0)==entry['generation'] and getattr(ticker,'domTicks',None)):
                        entry['at']=int(time.time()*1000);entry['revision']+=1
                entry['handler']=update;t.updateEvent+=update;self._depth_streams[symbol]=entry
        entry['used']=time.monotonic();t=entry['ticker']
        if not entry['at']:
            for _ in range(20):
                if entry['at']: break
                await asyncio.sleep(.05)
        if self._depth_streams.get(symbol) is not entry: raise ValueError('Depth subscription replaced')
        live=(self._ib is entry['ib'] and self.info.get('generation',0)==entry['generation'] and self._ib.isConnected() and self.info.get('feedHealthy') is not False and getattr(t,'marketDataType',1)==1 and os.environ.get('PAPER_TWS_DATA')!='delayed')
        return dict(symbol=symbol,conid=entry['contract'].conId,source='IB Gateway SMART depth',status='LIVE' if live else 'UNAVAILABLE',
                    updatedAt=entry['at'],revision=entry['revision'],stream=str(entry['generation'])+':'+str(entry['stream']),
                    bids=[dict(price=number(x.price,True),size=number(x.size,True)) for x in t.domBids],
                    asks=[dict(price=number(x.price,True),size=number(x.size,True)) for x in t.domAsks],
                    coverage='Displayed entitled SMART depth only; not the complete market or hidden liquidity')


ENGINE = MarketEngine()
