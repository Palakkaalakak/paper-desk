"""Read-only GUNS scanner/history, called only on MarketEngine's asyncio loop."""
import asyncio
import datetime as dt
import re
import time
from zoneinfo import ZoneInfo


def symbol(value):
    value = str(value).strip().upper()
    if not re.fullmatch(r'[A-Z0-9. -]{1,24}', value):
        raise ValueError('Invalid stock symbol')
    return value


def stamp(value):
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=ZoneInfo('America/New_York'))
        return int(value.timestamp()*1000)
    return str(value)


async def scan(engine):
    from ib_async import ScannerSubscription, TagValue
    sub = ScannerSubscription(numberOfRows=30, instrument='STK', locationCode='STK.US.MAJOR',
                              scanCode='TOP_PERC_GAIN', abovePrice=1.5, aboveVolume=30000,
                              stockTypeFilter='CORP')
    rows = await asyncio.wait_for(engine._ib.reqScannerDataAsync(
        sub, scannerSubscriptionFilterOptions=[TagValue('changePercAbove','5')]),15)
    return dict(at=int(time.time()*1000),source='IB Gateway',preliminary=True,
                rows=[dict(conid=r.contractDetails.contract.conId,symbol=r.contractDetails.contract.symbol,
                           name=r.contractDetails.longName,stockType=r.contractDetails.stockType,
                           secType='STK',exch='SMART',brokerId=True,rank=r.rank) for r in rows[:30]])


def close_idle(engine):
    streams = getattr(engine,'_guns_streams',{})
    for sym,entry in list(streams.items()):
        if time.monotonic()-entry['used']>120:
            for rows in entry['lists']:
                engine._ib.cancelHistoricalData(rows)
            streams.pop(sym,None)


async def bars(engine,ticker):
    from market import number
    ticker = symbol(ticker)
    if not hasattr(engine,'_guns_streams'):
        engine._guns_streams = {}
    streams = engine._guns_streams
    close_idle(engine)
    entry = streams.get(ticker)
    if entry and entry['generation']!=engine.info.get('generation',0):
        for rows in entry['lists']:
            engine._ib.cancelHistoricalData(rows)
        streams.pop(ticker,None)
        entry = None
    if not entry:
        if len(streams)>=4:
            old = streams.pop(min(streams,key=lambda s:streams[s]['used']))
            for rows in old['lists']:
                engine._ib.cancelHistoricalData(rows)
        c = await engine._stock(ticker)
        details = await asyncio.wait_for(engine._ib.reqContractDetailsAsync(c),12)
        if not details:
            raise ValueError('Stock definition unavailable')
        async def get(duration,interval,rth):
            return await engine._ib.reqHistoricalDataAsync(c,'',duration,interval,'TRADES',rth,
                              formatDate=2,keepUpToDate=True,timeout=20)
        results = await asyncio.gather(get('2 D','1 min',False),get('1 Y','1 day',True),return_exceptions=True)
        if any(isinstance(x,BaseException) or not x for x in results):
            for x in results:
                if not isinstance(x,BaseException) and x:
                    engine._ib.cancelHistoricalData(x)
            raise ValueError('Chart history incomplete; check IB history entitlement and connection')
        entry = dict(lists=results,detail=details[0],generation=engine.info.get('generation',0),
                     used=time.monotonic(),updated=int(time.time()*1000))
        def updated(*args):
            entry['updated'] = int(time.time()*1000)
        results[0].updateEvent += updated
        streams[ticker] = entry
    entry['used'] = time.monotonic()
    d = entry['detail']
    try:
        sessions = [dict(start=stamp(s.start),end=stamp(s.end)) for s in d.liquidSessions()]
    except (ValueError,KeyError):
        sessions = []
    def encode(rows):
        return [dict(t=stamp(b.date),o=number(b.open),h=number(b.high),l=number(b.low),c=number(b.close),v=number(b.volume))
                for b in rows if all(number(v) is not None for v in (b.open,b.high,b.low,b.close))]
    return dict(symbol=ticker,conid=d.contract.conId,source='IB Gateway TRADES',updatedAt=entry['updated'],
                minTick=number(d.minTick),stockType=d.stockType,sessions=sessions,
                minute=encode(entry['lists'][0]),daily=encode(entry['lists'][1]))


async def news(engine,ticker):
    c = await engine._stock(symbol(ticker))
    providers = await asyncio.wait_for(engine._ib.reqNewsProvidersAsync(),10)
    if not providers:
        return dict(rows=[])
    result = await asyncio.wait_for(engine._ib.reqHistoricalNewsAsync(c.conId,'+'.join(p.code for p in providers[:5]),'','',10),12)
    return dict(rows=[dict(time=stamp(x.time),provider=x.providerCode,headline=x.headline)
                      for x in (result or [])])
