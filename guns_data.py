"""Read-only GUNS scanner/history, called only on MarketEngine's asyncio loop."""
import asyncio
import datetime as dt
import re
import time
from html.parser import HTMLParser
from html import unescape
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
    return dict(at=int(time.time()*1000),source='IB Gateway scanner',preliminary=True,
                warning='Scanner returns contracts, not verified prices or premarket volume. Separate quote/history checks required.',
                rows=[dict(conid=r.contractDetails.contract.conId,symbol=r.contractDetails.contract.symbol,
                           name=r.contractDetails.longName,stockType=r.contractDetails.stockType,
                           secType='STK',exch='SMART',brokerId=True,rank=r.rank) for r in (rows or [])[:30]
                      if r.contractDetails.contract.secType == 'STK' and r.contractDetails.contract.currency == 'USD'])


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
    info = [dict(code=p.code, name=p.name) for p in (providers or [])]
    out = dict(rows=[], providers=info, at=int(time.time()*1000), source='IB Gateway API news')
    if not providers:
        return dict(out, warning='No API news providers returned for this Gateway username. TWS news and API entitlements can differ.')
    result = await asyncio.wait_for(engine._ib.reqHistoricalNewsAsync(c.conId,'+'.join(p.code for p in providers),'','',20),12)
    if result is None:
        raise ValueError('News request timed out or was not entitled; no catalyst has been verified')
    out['rows'] = [dict(time=stamp(x.time),provider=x.providerCode,articleId=x.articleId,headline=x.headline)
                   for x in result]
    if not result:
        out['warning'] = 'No symbol headlines returned. This does not establish that no news exists.'
    return out


class ArticleText(HTMLParser):
    """Convert publisher HTML into display-only text; never execute remote markup."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'): self.hidden += 1
        if tag in ('p', 'div', 'br', 'li', 'h1', 'h2', 'h3', 'tr', 'blockquote'): self.parts.append('\n')
    def handle_endtag(self, tag):
        if tag in ('script', 'style'): self.hidden = max(0, self.hidden-1)
        if tag in ('p', 'div', 'li', 'h1', 'h2', 'h3', 'tr', 'blockquote'): self.parts.append('\n')
    def handle_data(self, data):
        if not self.hidden: self.parts.append(data)


def article_content(source):
    """Expose missing story bodies; preserve publisher/legal text without inventing it."""
    source = source or ''
    truncated = len(source)>200000
    source = source[:200000]
    if re.match(r'\s*&lt;(?:!doctype|html|body|div|p)[\s&>]', source, re.I):
        source = unescape(source)
    parser = ArticleText()
    parser.feed(source)
    parser.close()
    raw = '\n'.join(re.sub(r'[ \t\xa0]+', ' ', line).strip() for line in ''.join(parser.parts).splitlines())
    raw = re.sub(r'\n{3,}', '\n\n', raw).strip()
    footer = re.search(r'(?im)^\s*(?:\(END\)(?:\s|$)|Copyright\s*(?:\(c\)|©)|The statements in this document shall not)', raw)
    text = raw[:footer.start()].strip() if footer else raw
    legal = raw[footer.start():].strip() if footer else ''
    meaningful = len(re.findall(r'\b\w+\b', text)) >= 25
    status = 'body_returned' if meaningful and not truncated else 'incomplete'
    warning = '' if status=='body_returned' else ('Article response was truncated; review the original licensed source.' if truncated else 'Gateway returned only a footer/disclaimer, headline or short fragment. A usable full story has NOT been verified. Check another article or your licensed TWS/news terminal.')
    return dict(text=text,rawText=raw,legalText=legal,contentStatus=status,warning=warning,
                completeness='Text presence is checked, not publisher completeness or factual accuracy.')


async def article(engine, provider, article_id):
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,32}', provider or ''):
        raise ValueError('Invalid news provider')
    if not article_id or len(article_id)>256 or any(ord(c)<32 for c in article_id):
        raise ValueError('Invalid article ID')
    result = await asyncio.wait_for(engine._ib.reqNewsArticleAsync(provider, article_id),12)
    if result is None:
        raise ValueError('Article unavailable or API entitlement missing')
    if result.articleType != 0:
        return dict(provider=provider,articleId=article_id,text='',rawText='',legalText='',contentStatus='binary',warning='Binary/PDF article: review in your licensed news terminal.')
    return dict(provider=provider,articleId=article_id,at=int(time.time()*1000),source='IB Gateway licensed news article',**article_content(result.articleText))


def verification(detail, minute, daily, now):
    """Independent scanner evidence. Missing observations remain unknown, not zero."""
    from market import number
    ny = ZoneInfo('America/New_York')
    day = dt.datetime.fromtimestamp(now/1000, ny).date()
    sessions = detail.liquidSessions()
    session = next((s for s in sessions if s.start.astimezone(ny).date()==day), None)
    prev = [(str(b.date)[:10], number(b.close, True)) for b in daily if str(b.date)[:10]<day.isoformat()]
    prev = sorted((date,close) for date,close in prev if close is not None and close>0)
    start = int(dt.datetime.combine(day,dt.time(4),ny).timestamp()*1000)
    end = min(now, stamp(session.start)) if session else None
    pre = [b for b in minute if isinstance(stamp(b.date), int) and end is not None and start<=stamp(b.date) and stamp(b.date)+60000<=end]
    volumes = [number(b.volume, True) for b in pre]
    return dict(conid=detail.contract.conId,stockType=detail.stockType,source='IB Gateway TRADES / RTH daily close',
                at=now,sessionDate=day.isoformat(),sessionKnown=session is not None,
                previousClose=prev[-1][1] if prev else None,previousCloseDate=prev[-1][0] if prev else None,
                premarketVolume=sum(volumes) if volumes and all(v is not None for v in volumes) else None,
                premarketStart=start,premarketEnd=stamp(session.start) if session else None,
                lastPremarketBar=max((stamp(b.date) for b in pre),default=None),observedBars=len(pre),
                volumeUnit='shares as returned by IB; no display-lot multiplier',
                coverage='Observed completed TRADES bars only. Missing minutes may be inactivity or unavailable data; not proof of complete tape coverage.')


async def verify(engine, conid):
    from ib_async import Contract
    if not str(conid).isdigit() or not 0<int(conid)<2**53:
        raise ValueError('Invalid contract ID')
    # Shared lock paces separate browser requests, not the quote/event loop.
    if not hasattr(engine, '_guns_verify_lock'): engine._guns_verify_lock = asyncio.Lock()
    async with engine._guns_verify_lock:
        wait = getattr(engine, '_guns_verify_next', 0)-time.monotonic()
        if wait>0: await asyncio.sleep(wait)
        engine._guns_verify_next = time.monotonic()+3
        ib = engine._ib
        details = await asyncio.wait_for(ib.reqContractDetailsAsync(Contract(conId=int(conid),exchange='SMART')),12)
        if not details or details[0].contract.conId!=int(conid) or details[0].contract.secType!='STK' or details[0].contract.currency!='USD':
            raise ValueError('US dollar stock definition unavailable')
        d = details[0]
        async def get(duration, interval, rth):
            return await ib.reqHistoricalDataAsync(d.contract,'',duration,interval,'TRADES',rth,formatDate=2,keepUpToDate=False,timeout=20)
        minute,daily = await asyncio.gather(get('2 D','1 min',False),get('1 M','1 day',True))
        if not minute or not daily: raise ValueError('Scanner verification history incomplete')
        return verification(d,minute,daily,int(time.time()*1000))
