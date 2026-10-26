"""BPSX read-only IBKR acquisitions on MarketEngine's owner loop."""
import asyncio
import re
import copy
import datetime as dt
import math
import time
from vmi_data import symbol
from vmi_options import chain, unchanged


def finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


# IB scanner filter tags (reqScannerParameters; full list published in the ib_async scanners notebook):
# avgVolumeAbove, ivRank52wAbove / ivRank26wAbove / ivRank13wAbove, ivPercntl52wAbove, marketCapAbove1e6, priceAbove.
# Adam's "IV Rank (52-week), 35 to 100" is ivRank52wAbove = 35.
KNOWN_FILTERS = dict(volume='avgVolumeAbove', ivrank='ivRank52wAbove')
IV_RANK_CODE = re.compile(r'^ivRank\d+wAbove$')


def scanner_filters(xml):
    """The volume and 52-week IV-rank 'Above' tags this IB build offers. When the parameter list cannot be read at
    all, the documented tags are tried; when it is readable but lacks them, they are not sent."""
    codes = set(re.findall(r'<code>([^<]+)</code>', xml or ''))
    if not codes:
        return dict(KNOWN_FILTERS, ivrank_candidates=[], count=0, source='documented defaults')
    ivs = sorted((c for c in codes if IV_RANK_CODE.match(c)), key=lambda c: ('52' not in c, c))
    return dict(volume=KNOWN_FILTERS['volume'] if KNOWN_FILTERS['volume'] in codes else None,
                ivrank=ivs[0] if ivs else None, ivrank_candidates=ivs, count=len(codes), source='IB scanner parameters')


async def scan(engine):
    """Adam's scan, run by IB over the whole US stock universe with his four filters together:
    price $50-5000, market cap >= $30bn, average volume >= 1M, 52-week IV rank 35-100
    (OPTIONS_MASTER_DOCUMENT.md, BPSX scanner). IB returns at most 50 rows per scan, so a full result is split into
    market-cap and price bands and merged. A filter IB does not offer, or that empties the result, is dropped step by
    step and named in the note; anything not applied by IB is verified per stock afterwards."""
    from ib_async import ScannerSubscription, TagValue
    ib, generation = engine._ib, engine.info.get('generation', 0)
    notes = []
    def collect(req_id, code, text, *args):
        if not 2100 <= code < 2200 and code not in (1100, 1101, 1102):   # farm/connectivity notices are not scan failures
            notes.append('%s: %s' % (code, text))
    ib.errorEvent += collect
    try:
        cached = getattr(engine, '_scanner_filters', None)
        if not cached or time.monotonic() - cached[0] > 6 * 3600:
            try:
                xml = await asyncio.wait_for(ib.reqScannerParametersAsync(), 20)
            except Exception:
                xml = ''
            engine._scanner_filters = cached = (time.monotonic(), scanner_filters(xml))
        found = cached[1]
        async def run(price=(50, 5000), cap=(30000, None), tags=(), corp=True):
            sub = ScannerSubscription(numberOfRows=50, instrument='STK', locationCode='STK.US.MAJOR',
                scanCode='HIGH_OPT_IMP_VOLAT', abovePrice=price[0], belowPrice=price[1], marketCapAbove=cap[0])
            if cap[1]: sub.marketCapBelow = cap[1]
            if corp: sub.stockTypeFilter = 'CORP'
            rows = await asyncio.wait_for(ib.reqScannerDataAsync(sub, [], [TagValue(k, str(v)) for k, v in tags]), 15)
            unchanged(engine, ib, generation)
            return rows or []
        async def banded(tags, corp):
            rows = await run(tags=tags, corp=corp)
            if len(rows) < 50:
                return rows
            merged = {}
            for lo, hi in [(30000, 60000), (60000, 150000), (150000, 400000), (400000, None)]:
                part = await run(cap=(lo, hi), tags=tags, corp=corp)
                if len(part) >= 50:           # still capped: split this band by price too
                    part = []
                    for plo, phi in [(50, 150), (150, 400), (400, 5000)]:
                        part += await run(price=(plo, phi), cap=(lo, hi), tags=tags, corp=corp)
                for r in part:
                    merged.setdefault(r.contractDetails.contract.conId, r)
                if len(merged) >= 300: break
            return list(merged.values())
        want = []
        if found['volume']: want.append((found['volume'], 1000000))
        if found['ivrank']: want.append((found['ivrank'], 35))
        ladder = [('price $50–5,000 · cap ≥$30bn · corporate stocks' + ''.join(' · %s ≥ %s' % t for t in want), want, True, True)]
        if want: ladder.append(('same without IB-side volume / IV-rank filters (verified per stock instead)', [], True, True))
        ladder += [('price ≥$50 · cap ≥$30bn (corporate-stock filter dropped)', [], True, False),
                   ('price ≥$50 only (market-cap filter dropped: confirm ≥$30bn yourself)', [], False, False)]
        rows, used = [], None
        for label, tags, cap_ok, corp in ladder:
            if cap_ok:
                rows = await banded(tuple(tags), corp)
            else:
                sub = ScannerSubscription(numberOfRows=50, instrument='STK', locationCode='STK.US.MAJOR',
                    scanCode='HIGH_OPT_IMP_VOLAT', abovePrice=50)
                rows = await asyncio.wait_for(ib.reqScannerDataAsync(sub), 15) or []
            if rows:
                used = (label, cap_ok, bool(tags))
                break
            await asyncio.sleep(.5)
    finally:
        ib.errorEvent -= collect
    if not rows and any('different IP address' in n for n in notes):
        raise ValueError('IB is refusing this connection\'s historical-data and scanner requests: your account is logged in to another TWS / '
            'Client Portal / mobile session from a different IP address than this Gateway (IB: "Trading TWS session is connected from a different IP address"). '
            'Log out of the other session, or run TWS/Gateway and this app on the same network/IP (turn VPNs off), then reload. '
            'The scan is not at fault. Pre-selected stock needs the same data and will fail until this is resolved.')
    if not rows:
        raise ValueError('IB returned no HIGH_OPT_IMP_VOLAT scanner results, even with the filters relaxed.'
            + (' IB said: ' + ' | '.join(dict.fromkeys(notes)) + '.' if notes else ' IB sent no error message.')
            + ' Use Pre-selected stock to enter a ticker meanwhile.')
    label, cap_ok, ib_tags = used
    out = [dict(symbol=r.contractDetails.contract.symbol, conid=r.contractDetails.contract.conId,
        name=r.contractDetails.longName, industry=r.contractDetails.industry, category=r.contractDetails.category,
        marketCapScreen=cap_ok)
        for r in rows if r.contractDetails.contract.secType == 'STK' and r.contractDetails.contract.currency == 'USD']
    first = label.startswith('price $50')
    return dict(rows=out[:300], source='IBKR HIGH_OPT_IMP_VOLAT · ' + label,
        note=('' if first else 'IB returned nothing with Adam\'s full filter, so a looser one was used. ')
             + ('IB-side filters (%s): volume %s, IV rank %s. ' % (found.get('source',''), found['volume'] or 'not offered', found['ivrank'] or ('not found among %d codes' % found['count'])))
             + '%d stocks; average volume and IV percentile are verified per stock%s. Run during regular market hours.' % (
                 len(out), '' if ib_tags else ' (not filtered by IB)'),
        filters=found, retrievedAt=int(time.time()*1000))


async def stock(engine, ticker):
    ticker=symbol(ticker)
    ib, generation=engine._ib, engine.info.get('generation',0)
    under=await engine._stock(ticker)
    if under.secType!='STK' or under.currency!='USD' or not under.conId:
        raise ValueError('A qualified USD stock is required')
    async def history(kind):
        return await asyncio.wait_for(ib.reqHistoricalDataAsync(under,endDateTime='',
            durationStr='2 Y' if kind=='OPTION_IMPLIED_VOLATILITY' else '1 Y',barSizeSetting='1 day',
            whatToShow=kind,useRTH=True,formatDate=1,keepUpToDate=False),35)
    daily, iv, details=await asyncio.gather(history('TRADES'),history('OPTION_IMPLIED_VOLATILITY'),
        asyncio.wait_for(ib.reqContractDetailsAsync(under),20),return_exceptions=True)
    unchanged(engine,ib,generation)
    if isinstance(daily,BaseException):
        raise ValueError('Daily chart unavailable: '+str(daily))
    today=dt.datetime.now(dt.timezone.utc).date().isoformat()
    bars=[]
    for b in daily:
        date=str(b.date)[:10]
        if date>=today or not all(finite(getattr(b,k,None)) and getattr(b,k)>0 for k in ('open','high','low','close')):
            continue
        bars.append(dict(date=date,o=b.open,h=b.high,l=b.low,c=b.close,
            v=float(b.volume) if finite(b.volume) and b.volume>=0 else None))
    bars.sort(key=lambda x:x['date'])
    ivs=[] if isinstance(iv,BaseException) else sorted([dict(date=str(b.date)[:10],value=b.close)
        for b in iv if str(b.date)[:10]<today and finite(b.close) and b.close>0],key=lambda x:x['date'])
    detail=None if isinstance(details,BaseException) or not details else details[0]
    return dict(symbol=ticker,underConid=under.conId,bars=bars,ivHistory=ivs,
        industry=getattr(detail,'industry',''),category=getattr(detail,'category',''),
        ivError=str(iv) if isinstance(iv,BaseException) else None,earningsDate=None,
        served_by='tws',source='IBKR completed daily TRADES / OPTION_IMPLIED_VOLATILITY',
        retrievedAt=int(time.time()*1000))


def strike_window(definitions, spot, count=25, center=90, ids=()):
    """Start at the nearest listed ATM put at/below spot and walk only downward."""
    if isinstance(count,bool) or count not in (13,25,49):
        raise ValueError('Choose an initial batch of 13, 25 or 49 strikes')
    if not finite(spot) or spot<=0:raise ValueError('A positive share quote is required')
    out=copy.deepcopy(definitions)
    puts=sorted(out['puts'],key=lambda r:r['strike'])
    eligible=[r for r in puts if r['strike']<=spot]
    if not eligible:raise ValueError('No verified at/below-market puts in this expiry')
    available=eligible
    # Initial batch is extended by observed deltas, not by guessed price buffers.
    selected=available[-count:]
    if ids:
        positions=[i for i,r in enumerate(available) if r['conid'] in ids]
        if len(positions)!=len(set(ids)):raise ValueError('Selected put is above the current ATM boundary')
        selected=available[min(min(positions),max(0,len(available)-count)):]
    out['puts']=copy.deepcopy(selected)
    strikes={r['strike'] for r in selected}
    out['calls']=[r for r in out.get('calls',[]) if r['strike'] in strikes]
    out.update(listedPutCount=len(puts),listedStrikes=sorted({r['strike'] for r in puts}),
        atmStrike=available[-1]['strike'],referenceSpot=spot,atmAnchored=True,
        windowCount=count,windowCenter=100,
        remainingPuts=copy.deepcopy(available[:-len(selected)]),
        remainingCalls=[r for r in definitions.get('calls',[]) if r['strike']<selected[0]['strike']])
    return out


async def reference_quote(engine,ticker,mode):
    """Small owned stock stream; never anchor the ATM range to yesterday's candle."""
    import os
    under=await engine._stock(ticker)
    existing=engine._active.get(under.conId) if mode=='live' else None
    handle=None;ib=engine._ib;generation=engine.info.get('generation',0)
    try:
        if existing:ticker_obj=existing['ticker']
        else:
            try:
                if mode=='frozen':ib.client.reqMarketDataType(2)
                handle=engine._open_chain_stream(under)
            finally:
                if mode=='frozen':ib.client.reqMarketDataType(3 if os.environ.get('PAPER_TWS_DATA')=='delayed' else 1)
            ticker_obj=handle['ticker']
        deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            unchanged(engine,ib,generation)
            q=engine._quote(ticker_obj)
            prices=[q.get('last')]
            if finite(q.get('bid')) and finite(q.get('ask')) and 0<q['bid']<=q['ask']:
                prices.append((q['bid']+q['ask'])/2)
            if mode=='frozen':prices.append(getattr(ticker_obj,'close',None))
            price=next((p for p in prices if finite(p) and p>0),None)
            if price and q.get('status') in (('LIVE','FROZEN') if mode=='frozen' else ('LIVE',)) and getattr(ticker_obj,'time',None):
                return price,q
            if handle and handle.get('errors'):break
            await asyncio.sleep(.08)
        raise ValueError('IB share quote unavailable; cannot mark ATM safely. Check stock market-data permissions/session.')
    finally:
        if handle:engine._close_chain_stream(handle)


async def options(engine,ticker,expiry=None,spot=None,ids=None,mode='live',count=25,center=90):
    import bpsx_stream
    ticker=symbol(ticker)
    if mode not in ('live','frozen'):
        raise ValueError('Invalid chain data mode')
    ib,generation=engine._ib,engine.info.get('generation',0)
    if not hasattr(engine,'_bpsx_definitions'):engine._bpsx_definitions={}
    key=(ticker,expiry,generation)
    cached=engine._bpsx_definitions.get(key)
    if cached and time.monotonic()-cached[0]<900:
        out=copy.deepcopy(cached[1])
    else:
        if expiry:
            out,call_chain=await asyncio.gather(chain(engine,ticker,expiry,right='P'),chain(engine,ticker,expiry,right='C'))
            out['calls']=call_chain['calls']
            out['excluded']+=call_chain.get('excluded',0)
        else:
            out=await chain(engine,ticker,expiry,right='P')
        unchanged(engine,ib,generation)
        engine._bpsx_definitions[key]=(time.monotonic(),copy.deepcopy(out))
        if len(engine._bpsx_definitions)>50:engine._bpsx_definitions.pop(next(iter(engine._bpsx_definitions)))
    if not expiry:return out
    if not finite(spot) or spot<=0:raise ValueError('Positive reference stock price required')
    wanted=set(ids or ())
    if len(wanted)>4 or not wanted.issubset({r['conid'] for r in out['puts']}):
        raise ValueError('Selected put is not in this verified chain')
    stream=next((s for s in bpsx_stream.sessions(engine).values() if wanted and s.out['symbol']==ticker and s.out['expiry']==expiry and s.mode==mode and wanted.issubset(s.rows)),None)
    if stream is None:
        spot,share=await reference_quote(engine,ticker,mode)
        out=strike_window(out,spot,count,100,wanted)
        out['underlyingQuote']=share
        stream=bpsx_stream.start(engine,out,spot,mode)
    if wanted:
        requested=int(time.time()*1000)
        stream.priority=list(wanted)+[c for c in stream.priority if c not in wanted]
        # Selected-leg validation waits only for these legs, never the whole chain.
        deadline=time.monotonic()+8
        while time.monotonic()<deadline and not stream.closed:
            result=stream.snapshot()
            selected=[r for r in result['puts'] if r['conid'] in wanted]
            if all(bpsx_stream.quote_ready(r.get('quote')) and r['quote'].get('at',0)>=requested-2000 for r in selected):break
            if any(r.get('quoteState') in ('PERMISSION','IB_ERROR') for r in selected):break
            await asyncio.sleep(.08)
    return stream.snapshot()


async def chart(engine,ticker,frame='d'):
    frames={'1m':('2 D','1 min'), '5m':('1 W','5 mins'), '15m':('2 W','15 mins'),
            '1h':('2 M','1 hour'), 'd':('2 Y','1 day'), 'w':('10 Y','1 week')}
    if frame not in frames:raise ValueError('Unsupported chart timeframe')
    ib,generation=engine._ib,engine.info.get('generation',0)
    under=await engine._stock(symbol(ticker))
    duration,interval=frames[frame]
    rows=await asyncio.wait_for(ib.reqHistoricalDataAsync(under,endDateTime='',durationStr=duration,
        barSizeSetting=interval,whatToShow='TRADES',useRTH=True,formatDate=2,keepUpToDate=False),30)
    unchanged(engine,ib,generation)
    bars=[]
    for b in rows:
        if not all(finite(getattr(b,k,None)) and getattr(b,k)>0 for k in ('open','high','low','close')):continue
        value=b.date
        label=value.isoformat() if hasattr(value,'isoformat') else str(value)
        bars.append(dict(date=label,o=b.open,h=b.high,l=b.low,c=b.close,v=float(b.volume) if finite(b.volume) else None))
    return dict(symbol=ticker,underConid=under.conId,frame=frame,bars=bars,served_by='tws',
        source='IBKR historical TRADES · regular session · last candle may be forming',retrievedAt=int(time.time()*1000))
