"""BPSX read-only IBKR acquisitions on MarketEngine's owner loop."""
import asyncio
import copy
import datetime as dt
import math
import time
from vmi_data import symbol
from vmi_options import chain, unchanged


def finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


async def scan(engine):
    from ib_async import ScannerSubscription
    ib, generation = engine._ib, engine.info.get('generation', 0)
    sub = ScannerSubscription(numberOfRows=50, instrument='STK', locationCode='STK.US.MAJOR',
        scanCode='HIGH_OPT_IMP_VOLAT', abovePrice=50, marketCapAbove=30000, stockTypeFilter='CORP')
    rows = await asyncio.wait_for(ib.reqScannerDataAsync(sub), 20)
    unchanged(engine, ib, generation)
    if rows is None:
        raise ValueError('IB scanner response pending')
    return dict(rows=[dict(symbol=r.contractDetails.contract.symbol,
        conid=r.contractDetails.contract.conId, name=r.contractDetails.longName,
        industry=r.contractDetails.industry, category=r.contractDetails.category, marketCapScreen=True)
        for r in rows[:50] if r.contractDetails.contract.secType=='STK' and r.contractDetails.contract.currency=='USD'],
        source='IBKR HIGH_OPT_IMP_VOLAT · price ≥$50 · market cap ≥$30bn',
        note='Up to 50 ranked US optionable corporate stocks, not an exhaustive market scan. Historical IV percentile and average volume are verified separately. Run during regular market hours.',
        retrievedAt=int(time.time()*1000))


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


async def options(engine,ticker,expiry=None,spot=None,ids=None):
    from ib_async import Contract
    ticker=symbol(ticker)
    ib,generation=engine._ib,engine.info.get('generation',0)
    if not hasattr(engine,'_bpsx_definitions'):
        engine._bpsx_definitions={}
    key=(ticker,expiry,generation)
    cached=engine._bpsx_definitions.get(key)
    if cached and time.monotonic()-cached[0]<300:
        out=copy.deepcopy(cached[1])
    else:
        out=await chain(engine,ticker,expiry,right='P')
        unchanged(engine,ib,generation)
        engine._bpsx_definitions[key]=(time.monotonic(),copy.deepcopy(out))
        if len(engine._bpsx_definitions)>50:
            engine._bpsx_definitions.pop(next(iter(engine._bpsx_definitions)))
    if not expiry:
        return out
    rows=out['puts']
    if ids:
        wanted=set(ids)
        if len(wanted)>4 or not wanted.issubset({r['conid'] for r in rows}):
            raise ValueError('Selected put is not in this verified chain')
        chosen=[r for r in rows if r['conid'] in wanted]
    else:
        if not finite(spot) or spot<=0:
            raise ValueError('Positive reference stock price required')
        chosen=sorted((r for r in rows if r['strike']<spot),key=lambda r:abs(r['strike']-spot*.85))[:32]
    if not hasattr(engine,'_bpsx_quote_slots'):
        engine._bpsx_quote_slots=asyncio.Semaphore(4)
    async def acquire(row):
        async with engine._bpsx_quote_slots:
            try:
                q=await engine._snapshot_quote(Contract(conId=row['conid'],secType='OPT',exchange='SMART',currency='USD'),require_greeks=True)
                unchanged(engine,ib,generation)
                if q.get('brokerConid')!=row['conid']:
                    raise ValueError('Put quote contract mismatch')
                row['quote']=q
            except Exception as exc:
                row['quoteError']=str(exc)
    await asyncio.gather(*(acquire(r) for r in chosen))
    unchanged(engine,ib,generation)
    out['quotedCount']=len(chosen)
    out['note']='Quotes cover up to 32 OTM strikes near the 15% buffer. All listed strikes are editable; refresh selected legs. No observed delta = no automatic recommendation.'
    return out
