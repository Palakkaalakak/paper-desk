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
        self.capacity = max(1, int(capacity or os.environ.get('PAPER_TWS_LINES', '500')))
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
        q.update(prevClose=number(getattr(t,'close',None),True),
                 status={1:'LIVE',2:'FROZEN',3:'DELAYED',4:'DELAYED FROZEN'}.get(getattr(t,'marketDataType',1),'UNKNOWN'),
                 halted=number(getattr(t,'halted',None)) in (1,2), source='IB Gateway',
                 at=int(stamp.timestamp()*1000) if isinstance(stamp,dt.datetime) else now,
                 receivedAt=now, brokerConid=getattr(t.contract,'conId',0))
        if q['bid'] is not None and q['ask'] is not None and q['ask'] >= q['bid']:
            q['mid'] = (q['bid']+q['ask'])/2
        if q['prevClose'] and q['last'] is not None:
            q['change'] = q['last']-q['prevClose']
            q['changePct'] = q['change']/q['prevClose']*100
        g = getattr(t,'modelGreeks',None)
        if g:
            q['greeks'] = {k:number(getattr(g,n,None)) for k,n in
                           [('iv','impliedVol'),('delta','delta'),('gamma','gamma'),('theta','theta'),('vega','vega')]}
        return q

    def _ticks(self, tickers):
        now = int(time.time()*1000)
        self._last_received = now
        for t in tickers:
            ids = self._ticker_ids.get(id(t),())
            if ids:
                q = self._quote(t,now)
                for cid in tuple(ids):
                    self.publish(cid,q)

    def _error(self, req_id, code, text, contract=None):
        if code in (1100,1101,1102):
            if code == 1101:
                self._clear_active()
            self._status(problem=None if code in (1101,1102) else str(text), feedHealthy=code != 1100)
        elif code in (100,101,354,10167,10168,10197,200):
            self._status(problem='%s: %s' % (code,text))

    def _clear_active(self):
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
            c = m.Contract(conId=cid,exchange=row.get('exch') or 'SMART',currency='USD')
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
        try:
            c = await self._contract(row)
            ticker = self._ib.reqMktData(c,'',False,False)
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
                    ttl = 20 if name == 'guns_scan' else 60 if name == 'guns_verify' else 30 if name == 'guns_news' else 300 if name in ('search','chain','guns_article') else 1
                    self._cache[key] = (time.monotonic()+ttl,result)
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

    async def _search(self,pattern):
        rows = await self._ib.reqMatchingSymbolsAsync(pattern)
        return [dict(symbol=r.contract.symbol,name=getattr(r.contract,'description','') or r.contract.symbol,
                     conid=r.contract.conId,exchange=r.contract.primaryExchange,type=r.contract.secType)
                for r in rows or [] if r.contract.secType in ('STK','IND')][:30]

    async def _stock(self,symbol):
        import ib_async as m
        key = ('stock',symbol)
        if key not in self._contracts:
            c = m.Stock(symbol,'SMART','USD')
            if not await self._ib.qualifyContractsAsync(c) or not c.conId:
                raise ValueError('Could not resolve '+symbol)
            self._contracts[key] = c
        return self._contracts[key]

    async def _quote_symbol(self,symbol):
        c = await self._stock(symbol)
        # Diagnostic-only transient read; the UI uses persistent subscriptions.
        existing = next((v['ticker'] for v in self._active.values() if v['contract'].conId == c.conId),None)
        t = existing or self._ib.reqMktData(c,'',False,False)
        try:
            for _ in range(40):
                if number(t.bid,True) is not None and number(t.ask,True) is not None:
                    break
                await asyncio.sleep(0.05)
            return self._quote(t)
        finally:
            if existing is None:
                self._ib.cancelMktData(c)

    async def _chain(self,symbol,expiry=None):
        import ib_async as m
        import tws
        und = await self._stock(symbol)
        key = ('params',symbol)
        hit = self._cache.get(key)
        if hit and time.monotonic()<hit[0]:
            params = hit[1]
        else:
            params = await self._ib.reqSecDefOptParamsAsync(symbol,'','STK',und.conId)
            self._cache[key] = (time.monotonic()+300,params)
        params = [p for p in params or [] if p.exchange=='SMART'] or params
        if not params:
            raise ValueError('No option definitions for '+symbol)
        p = next((x for x in params if x.tradingClass==symbol),params[0])
        today = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d')
        exps = sorted(x for x in p.expirations if x>=today)
        out = dict(expirations=exps,underConid=und.conId,calls=[],puts=[],served_by='tws')
        if not expiry:
            return out
        if expiry not in exps:
            raise ValueError('Expiry is not listed for '+symbol)
        stored = tws._disk().get(symbol+'|'+expiry)
        if isinstance(stored,dict) and time.time()-float(stored.get('ts',0)) < tws.DEF_TTL:
            contracts = [m.Option(symbol,expiry,r['strike'],r['right'],'SMART',conId=r['conId'],
                         tradingClass=r.get('tradingClass') or '',currency='USD') for r in stored['rows']]
        else:
            template = m.Option(symbol,expiry,0,'','SMART',tradingClass=p.tradingClass,currency='USD')
            details = await asyncio.wait_for(self._ib.reqContractDetailsAsync(template),90)
            contracts = [d.contract for d in details if d.contract.conId]
            tws._disk_save((symbol,expiry),[dict(conId=c.conId,strike=c.strike,right=c.right,
                                                tradingClass=c.tradingClass) for c in contracts])
        out['expiry'] = expiry
        for c in sorted(contracts,key=lambda x:(x.strike,x.right)):
            if c.right not in ('C','P'):
                continue
            out['calls' if c.right=='C' else 'puts'].append(dict(conid=c.conId,
                symbol='%s %s %s %s' % (symbol,expiry,c.strike,c.right),strike=c.strike,right=c.right,
                expiry=expiry,mult=number(c.multiplier) or 100,bid=None,ask=None,last=None))
        return out

    async def _guns_scan(self):
        from guns_data import scan
        return await scan(self)

    async def _guns_bars(self, symbol):
        from guns_data import bars
        return await bars(self, symbol)

    async def _guns_news(self, symbol):
        from guns_data import news
        return await news(self, symbol)

    async def _guns_verify(self, conid):
        from guns_data import verify
        return await verify(self, conid)

    async def _guns_article(self, provider, article_id):
        from guns_data import article
        return await article(self, provider, article_id)

    async def _depth(self,symbol):
        c = await self._stock(symbol)
        t = self._ib.reqMktDepth(c,numRows=5,isSmartDepth=True)
        try:
            for _ in range(20):
                if t.domBids or t.domAsks:
                    break
                await asyncio.sleep(0.05)
            return dict(bids=[dict(price=x.price,size=number(x.size)) for x in t.domBids],
                        asks=[dict(price=x.price,size=number(x.size)) for x in t.domAsks])
        finally:
            self._ib.cancelMktDepth(c,isSmartDepth=True)


ENGINE = MarketEngine()
