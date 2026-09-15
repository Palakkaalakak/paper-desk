"""Read-only GUNS scanner/history, called only on MarketEngine's asyncio loop."""
import asyncio
import datetime as dt
import re
import time
import os
import json
import base64
import binascii
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
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


async def scanner_capabilities(engine):
    """Inspect the running Gateway's advertised fields; never invent float tags."""
    xml=await asyncio.wait_for(engine._ib.reqScannerParametersAsync(),15)
    if not isinstance(xml,str) or len(xml)>5000000 or '<!DOCTYPE' in xml.upper() or '<!ENTITY' in xml.upper():
        raise ValueError('Invalid scanner parameter XML')
    root=ET.fromstring(xml);fields=[];seen=set()
    for node in root.iter():
        values={c.tag.split('}')[-1]:c.text.strip() for c in node if c.text and c.text.strip() and len(c.text.strip())<240}
        code=values.get('code') or values.get('filterCode') or values.get('tag')
        if code and code not in seen and re.search(r'float|shares.?out|outstanding',json.dumps(values),re.I):
            seen.add(code);fields.append(values)
    return dict(source='IBKR reqScannerParameters',at=int(time.time()*1000),shareFields=fields[:40],
                note='Advertised fields only. Counts, units and as-of dates are not established by this capability response; no inferred float is published.')


async def scan(engine):
    from ib_async import ScannerSubscription, TagValue
    sub = ScannerSubscription(numberOfRows=50, instrument='STK', locationCode='STK.US.MAJOR',
                              scanCode='HOT_BY_VOLUME', abovePrice=1.5, aboveVolume=30000,
                              stockTypeFilter='CORP')
    rows = await asyncio.wait_for(engine._ib.reqScannerDataAsync(
        sub, scannerSubscriptionFilterOptions=[TagValue('changePercAbove','5')]),15)
    if rows is None: raise ValueError('IBKR scanner response pending')
    return dict(at=int(time.time()*1000),source='IB Gateway scanner',preliminary=True,
                warning='Scanner returns contracts, not verified prices or premarket volume. Separate quote/history checks required.',
                rows=[dict(conid=r.contractDetails.contract.conId,symbol=r.contractDetails.contract.symbol,
                           name=r.contractDetails.longName,stockType=r.contractDetails.stockType,
                           secType='STK',exch='SMART',brokerId=True,rank=r.rank) for r in (rows or [])[:50]
                      if r.contractDetails.contract.secType == 'STK' and r.contractDetails.contract.currency == 'USD'])


def close_idle(engine):
    streams = getattr(engine,'_guns_streams',{})
    for sym,entry in list(streams.items()):
        if time.monotonic()-entry['used']>120:
            if entry['generation']==engine.info.get('generation',0):
                for rows in entry['lists']:
                    engine._ib.cancelHistoricalData(rows)
            streams.pop(sym,None)


async def bars(engine,ticker):
    # One acquisition at a time across all symbols, on the existing owner loop.
    ticker=symbol(ticker)
    entry=getattr(engine,'_guns_streams',{}).get(ticker)
    if entry and entry['generation']==engine.info.get('generation',0) and time.monotonic()-entry['used']<=120:
        return await _bars(engine,ticker)  # Cached charts must not wait for new acquisitions.
    if not hasattr(engine,'_guns_bars_lock'): engine._guns_bars_lock=asyncio.Lock()
    async with engine._guns_bars_lock:
        return await _bars(engine,ticker)


async def _bars(engine,ticker):
    from market import number
    ticker = symbol(ticker)
    if not hasattr(engine,'_guns_streams'):
        engine._guns_streams = {}
    streams = engine._guns_streams
    close_idle(engine)
    entry = streams.get(ticker)
    if entry and entry['generation']!=engine.info.get('generation',0):
        # Old request IDs must never cancel requests on a new connection.
        streams.pop(ticker,None)
        entry = None
    if not entry:
        if len(streams)>=6:  # Four chart symbols plus two pending-entry symbols.
            old = streams.pop(min(streams,key=lambda s:streams[s]['used']))
            if old['generation']==engine.info.get('generation',0):
                for rows in old['lists']:
                    engine._ib.cancelHistoricalData(rows)
        ib, generation = engine._ib, engine.info.get('generation',0)
        c = await engine._stock(ticker)
        details = await asyncio.wait_for(ib.reqContractDetailsAsync(c),12)
        if not details:
            raise ValueError('Stock definition unavailable')
        if engine._ib is not ib or engine.info.get('generation',0)!=generation:
            raise ValueError('Connection changed during chart definition')
        async def get(duration,interval,rth):
            return await ib.reqHistoricalDataAsync(c,'',duration,interval,'TRADES',rth,
                              formatDate=2,keepUpToDate=True,timeout=20)
        pending = asyncio.gather(get('2 D','1 min',False),get('1 Y','1 day',True),return_exceptions=True)
        try:
            results = await asyncio.shield(pending)
        except asyncio.CancelledError:
            results = await pending
            if engine._ib is ib and engine.info.get('generation',0)==generation:
                for rows in results:
                    if rows is not None and not isinstance(rows,BaseException): ib.cancelHistoricalData(rows)
            raise
        if engine._ib is not ib or engine.info.get('generation',0)!=generation:
            raise ValueError('Connection changed during chart acquisition; retry on current generation')
        if any(isinstance(x,BaseException) or not x for x in results):
            for x in results:
                if not isinstance(x,BaseException) and x is not None:
                    ib.cancelHistoricalData(x)
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
    lines, notices = [], []
    for line in raw.splitlines():
        if re.match(r'(?i)^\s*Copyright\s*(?:\(c\)|©|\d{4})',line): notices.append(line)
        else: lines.append(line)
    body = '\n'.join(lines).strip()
    footer = re.search(r'(?im)^\s*(?:\(END\)(?:\s|$)|The statements in this document shall not)',body)
    if footer:
        notices.append(body[footer.start():].strip())
        body = body[:footer.start()].strip()
    words = len(re.findall(r'\b\w+\b',body))
    status = 'body_returned' if words>=25 else 'brief' if words>=4 else 'incomplete'
    if truncated: status='incomplete'
    warning = 'Response exceeds reader limit' if truncated else 'Provider response contains no story text' if status=='incomplete' else ''
    return dict(text=body,rawText=raw,legalText='\n'.join(notices),contentStatus=status,warning=warning,
                completeness='Provider text, not independently verified for completeness or accuracy.')



async def article(engine, provider, article_id):
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,32}', provider or ''):
        raise ValueError('Invalid news provider')
    if not article_id or len(article_id)>256 or any(ord(c)<32 for c in article_id):
        raise ValueError('Invalid article ID')
    result = await asyncio.wait_for(engine._ib.reqNewsArticleAsync(provider, article_id),12)
    if result is None:
        raise ValueError('Article unavailable or API entitlement missing')
    if result.articleType != 0:
        try:
            encoded = re.sub(r'\s','',result.articleText)
            if len(encoded)>8000000: raise ValueError('PDF too large')
            pdf = base64.b64decode(encoded,validate=True)
            if not pdf.startswith(b'%PDF-'): raise ValueError('Invalid PDF')
            return dict(provider=provider,articleId=article_id,text='',rawText='',legalText='',
                        contentStatus='pdf',pdfBase64=encoded,source='IBKR licensed PDF',warning='')
        except (ValueError,TypeError,binascii.Error):
            return dict(provider=provider,articleId=article_id,text='',rawText='',legalText='',
                        contentStatus='incomplete',warning='IBKR returned an invalid or oversized PDF')
    return dict(provider=provider,articleId=article_id,at=int(time.time()*1000),source='IB Gateway licensed news article',**article_content(result.articleText))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Provider redirect refused')


def source_fetch(host, path, params, headers=None):
    """Fixed provider hosts only. No caller URLs, redirects, credential logs or scraping."""
    if host not in ('feeds.finance.yahoo.com', 'news.google.com', 'api.benzinga.com', 'financialmodelingprep.com', 'data.alpaca.markets'):
        raise ValueError('Unsupported source')
    if host == 'data.alpaca.markets' and path not in ('/v2/stocks/snapshots', '/v2/stocks/bars'):
        raise ValueError('Unsupported Alpaca read-only data path')
    url = 'https://' + host + path + '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'User-Agent':'PaperDesk/1.0', 'Accept':'application/json, application/rss+xml, application/xml', **(headers or {})})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=8) as response:
            payload = response.read(2000001)
            if len(payload)>2000000: raise ValueError('Response too large')
            return payload
    except urllib.error.HTTPError as error:
        # Report status, never credential-bearing URLs or provider response bodies.
        raise ValueError('Provider HTTP '+str(error.code)+': '+('credentials or data entitlement required' if error.code in (401,403) else 'rate limit' if error.code==429 else 'data request failed')) from None
    except Exception:
        # urllib errors can contain a URL with an API key. Never propagate those.
        raise ValueError('Source unavailable; check network, entitlement or provider limits') from None


def public_link(value):
    try:
        p = urllib.parse.urlsplit(str(value or ''))
        return str(value) if p.scheme=='https' and p.hostname and not p.username and not p.password else ''
    except ValueError:
        return ''


def source_news(ticker):
    """Public RSS excerpts/links plus optional licensed full bodies. Not an IB session."""
    ticker = symbol(ticker)
    rows, diagnostics = [], []
    key = os.environ.get('PAPER_BENZINGA_KEY')
    if key:
        try:
            data = json.loads(source_fetch('api.benzinga.com','/api/v2/news',dict(token=key,tickers=ticker,displayOutput='full',pageSize=20,sort='created:desc')))
            if not isinstance(data,list): raise ValueError('Unexpected response')
            for item in data[:20]:
                if ticker not in [str(s.get('name','')).upper() for s in item.get('stocks',[])]: continue
                body = article_content(item.get('body',''))
                rows.append(dict(headline=str(item.get('title','')),time=item.get('created'),provider='Benzinga API',articleId=str(item.get('id','')),url=public_link(item.get('url')),**body))
        except Exception:
            diagnostics.append('Benzinga full-body API unavailable; check PAPER_BENZINGA_KEY entitlement.')
    else:
        diagnostics.append('Optional full-body API not configured. Public feeds supply excerpts and publisher links, not guaranteed full text.')
    for host,path,params,label in [
        ('feeds.finance.yahoo.com','/rss/2.0/headline',dict(s=ticker,region='US',lang='en-US'),'Yahoo Finance RSS'),
        ('news.google.com','/rss/search',dict(q='"'+ticker+'" stock company when:7d',hl='en-US',gl='US',ceid='US:en'),'Google News RSS')]:
        try:
            payload = source_fetch(host,path,params)
            if b'<!DOCTYPE' in payload.upper() or b'<!ENTITY' in payload.upper(): raise ValueError('Unsafe XML')
            feed = ET.fromstring(payload)
            for item in feed.findall('./channel/item')[:20]:
                title=item.findtext('title') or ''
                link=public_link(item.findtext('link'))
                if not link or not title: continue
                excerpt=article_content(item.findtext('description') or '')['rawText']
                rows.append(dict(headline=title,time=item.findtext('pubDate'),provider=label,articleId='',url=link,text=excerpt,rawText=excerpt,contentStatus='excerpt',warning='Feed excerpt, not a full article. Open the linked source to read the story.'))
            if rows: break
        except Exception:
            diagnostics.append(label+' unavailable; trying the next configured source.')
    seen=set()
    unique=[]
    for row in rows:
        identity=re.sub(r'\W+','',row['headline']).lower()
        if identity not in seen:
            seen.add(identity); unique.append(row)
    return dict(symbol=ticker,rows=unique[:40],source='Independent company news sources',at=int(time.time()*1000),diagnostics=diagnostics)


def float_reference(ticker):
    ticker=symbol(ticker)
    key=os.environ.get('PAPER_FMP_KEY')
    if not key:
        return dict(symbol=ticker,floatShares=None,source='Not configured',date=None,status='unavailable',setupRequired=True,warning='Connect FMP shares-float using PAPER_FMP_KEY. IBKR removed its fundamental-data API in 10.47; Gateway subscriptions cannot supply this field.')
    try:
        data=json.loads(source_fetch('financialmodelingprep.com','/stable/shares-float',dict(symbol=ticker,apikey=key)))
        row=next(x for x in data if str(x.get('symbol','')).upper()==ticker)
        value=row.get('floatShares')
        if isinstance(value,bool) or not isinstance(value,(float,int)) or not 0<value<1e15: raise ValueError('Missing float')
        date=str(row.get('date') or '')
        dated=dt.datetime.fromisoformat(date.replace('Z','+00:00'))
        if dated.tzinfo is None: dated=dated.replace(tzinfo=dt.timezone.utc)
        age=(dt.datetime.now(dt.timezone.utc)-dated).total_seconds()
        if not 0<=age<45*86400: raise ValueError('Float as-of date outside freshness policy')
        return dict(symbol=ticker,floatShares=value,date=date,basis='free-float',source='Financial Modeling Prep / shares-float',status='returned',retrievedAt=int(time.time()*1000))
    except Exception:
        return dict(symbol=ticker,floatShares=None,date=None,source='Financial Modeling Prep',status='unavailable',warning='Free float unavailable; check key, plan and symbol coverage.')


def broker_float(xml, ticker, conid, now):
    """Explicit dated share counts only. Outstanding shares are an upper bound,
    never an exact free-float substitute. Percentages/market caps are not counts.
    """
    if not xml or len(xml)>2000000 or '<!DOCTYPE' in xml.upper() or '<!ENTITY' in xml.upper(): return None
    tree=ET.fromstring(xml)
    candidates=[]
    for node in tree.iter():
        name=re.sub(r'[^a-z]','',node.tag.split('}')[-1].lower())
        kind=re.sub(r'[^a-z]','',str(node.get('FieldName') or node.get('Type') or '').lower())
        exact=name in ('floatshares','sharesfloat','freefloatshares') or kind in ('floatshares','sharesfloat','freefloatshares')
        bound=name in ('sharesout','sharesoutstanding')
        if not exact and not bound: continue
        unit=str(node.get('Unit') or node.get('Units') or 'shares').strip().lower()
        scale={'shares':1,'units':1,'thousands':1000,'millions':1000000}.get(unit)
        date=node.get('Date') or node.get('AsOfDate')
        try:
            value=float((node.text or '').strip().replace(',',''))*scale
            dated=dt.datetime.fromisoformat(date.replace('Z','+00:00')).date()
            age=(dt.datetime.fromtimestamp(now/1000,dt.timezone.utc).date()-dated).days
            if not 0<value<1e15 or not 0<=age<45: continue
        except (ValueError,TypeError,AttributeError): continue
        item=dict(symbol=ticker,conid=conid,date=dated.isoformat(),retrievedAt=now,
                  source='IBKR ReportSnapshot',status='returned',floatShares=value if exact else None,
                  basis='free-float' if exact else 'outstanding-upper-bound')
        if bound: item['upperBoundShares']=value
        candidates.append(item)
    return next((x for x in candidates if x['basis']=='free-float'),candidates[0] if candidates else None)


async def float_data(engine,ticker):
    # IBKR 10.47 removed reqFundamentalData. Never wait on that retired service.
    return await asyncio.to_thread(float_reference,ticker)


def required_float(ticker):
    result=float_reference(ticker)
    if result.get('status')!='returned': raise ValueError(result['warning'])
    return result


def alpaca_headers():
    key=os.environ.get('PAPER_ALPACA_KEY') or os.environ.get('APCA_API_KEY_ID')
    secret=os.environ.get('PAPER_ALPACA_SECRET') or os.environ.get('APCA_API_SECRET_KEY')
    if not key or not secret: raise ValueError('Connect Alpaca SIP using PAPER_ALPACA_KEY and PAPER_ALPACA_SECRET')
    return {'APCA-API-KEY-ID':key,'APCA-API-SECRET-KEY':secret}


def scanner_sources():
    try: alpaca_headers(); alpaca=True
    except ValueError: alpaca=False
    return dict(floatConfigured=bool(os.environ.get('PAPER_FMP_KEY')),quoteFallbackConfigured=alpaca,
                floatSource='FMP shares-float',quoteFallback='Alpaca SIP',
                retired='IBKR fundamental data removed in API 10.47',
                note='Configured keys are not proof of entitlement; SIP requests require consolidated real-time access.')


def alpaca_data(path,params):
    # Explicit SIP only. Never accept the API default (free, single-exchange IEX).
    data=json.loads(source_fetch('data.alpaca.markets',path,{**params,'feed':'sip'},headers=alpaca_headers()))
    if not isinstance(data,dict) or data.get('message') or data.get('error'):
        raise ValueError('Alpaca SIP did not return market data')
    return data


def iso_stamp(value):
    date=dt.datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if date.tzinfo is None: raise ValueError('Provider timestamp must include timezone')
    return int(date.timestamp()*1000)


def sip_quote(ticker,now=None):
    from market import number
    ticker=symbol(ticker)
    data=alpaca_data('/v2/stocks/snapshots',{'symbols':ticker})
    snap=data.get(ticker)
    if not isinstance(snap,dict): raise ValueError('Alpaca SIP symbol not returned')
    q,t=snap.get('latestQuote') or {},snap.get('latestTrade') or {}
    bid,ask,last=(number(v,True) for v in (q.get('bp'),q.get('ap'),t.get('p')))
    now=int(time.time()*1000) if now is None else now
    quote_at,trade_at=iso_stamp(q.get('t')),iso_stamp(t.get('t'))
    if bid is None or ask is None or last is None or bid<=0 or ask<bid or last<=0:
        raise ValueError('Alpaca SIP returned no complete positive bid/ask/trade')
    if not 0<=now-quote_at<15000 or not 0<=now-trade_at<15000:
        raise ValueError('Alpaca SIP quote/trade is not real-time; delayed data cannot screen')
    return dict(symbol=ticker,bid=bid,ask=ask,last=last,tradeLast=last,
                bidSize=number(q.get('bs')),askSize=number(q.get('as')),status='LIVE',feed='sip',
                at=quote_at,tradeAt=trade_at,receivedAt=now,source='Alpaca SIP consolidated NBBO',screeningOnly=True)


def sip_bars(ticker,start,end,timeframe):
    params=dict(symbols=symbol(ticker),start=start,end=end,timeframe=timeframe,limit=10000,adjustment='split',sort='asc')
    result=[];seen=set()
    for _ in range(4):
        data=alpaca_data('/v2/stocks/bars',params)
        batch=(data.get('bars') or {}).get(ticker)
        if not isinstance(batch,list): raise ValueError('Alpaca SIP bars missing for requested symbol')
        result.extend(batch)
        token=data.get('next_page_token')
        if not token: return result
        if token in seen: raise ValueError('Alpaca repeated history page')
        seen.add(token);params['page_token']=token
    raise ValueError('Alpaca history pagination incomplete')


def sip_verification(detail,now):
    from types import SimpleNamespace
    from market import number
    ny=ZoneInfo('America/New_York');day=dt.datetime.fromtimestamp(now/1000,ny).date()
    session=next((s for s in detail.liquidSessions() if s.start.astimezone(ny).date()==day),None)
    if not session: raise ValueError('IBKR contract session required for SIP verification')
    ticker=symbol(detail.contract.symbol)
    start=dt.datetime.combine(day,dt.time(4),ny)
    end=min(dt.datetime.fromtimestamp(now/1000,dt.timezone.utc),session.start)
    if end<=start: raise ValueError('Premarket session has not started')
    minute=sip_bars(ticker,start.isoformat(),end.isoformat(),'1Min')
    daily=sip_bars(ticker,(day-dt.timedelta(days=14)).isoformat(),day.isoformat(),'1Day')
    def bar(x,daily=False):
        timestamp=iso_stamp(x.get('t'))
        date=dt.datetime.fromtimestamp(timestamp/1000,ny)
        return SimpleNamespace(date=date.date().isoformat() if daily else date,close=number(x.get('c'),True),volume=number(x.get('v'),True))
    result=verification(detail,[bar(x) for x in minute],[bar(x,True) for x in daily],now)
    if result['premarketVolume'] is None or result['previousClose'] is None:
        raise ValueError('Alpaca SIP did not supply premarket volume and prior close')
    result.update(source='Alpaca SIP TRADES / daily close; IBKR contract and session',
                  volumeUnit='consolidated SIP shares',coverage='Completed 04:00 ET to regular-open SIP minute bars; all returned pages checked.')
    return result


async def schedule(engine):
    c=await engine._stock('SPY')
    details=await asyncio.wait_for(engine._ib.reqContractDetailsAsync(c),12)
    sessions=[dict(start=stamp(s.start),end=stamp(s.end)) for s in details[0].liquidSessions()] if details else []
    return dict(sessions=sessions,source='IB US equity liquid session schedule',at=int(time.time()*1000))


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
    # Bound history concurrency independently from start pacing. The old lock
    # serialized the entire network response, defeating parallel screening.
    if not hasattr(engine, '_guns_verify_lock'): engine._guns_verify_lock = asyncio.Lock()
    if not hasattr(engine, '_guns_verify_slots'): engine._guns_verify_slots = asyncio.Semaphore(3)
    async with engine._guns_verify_slots:
        async with engine._guns_verify_lock:
            wait = getattr(engine, '_guns_verify_next', 0)-time.monotonic()
            if wait>0: await asyncio.sleep(wait)
            engine._guns_verify_next = time.monotonic()+3
        ib = engine._ib
        details = await asyncio.wait_for(ib.reqContractDetailsAsync(Contract(conId=int(conid),exchange='SMART')),12)
        if not details or details[0].contract.conId!=int(conid) or details[0].contract.secType!='STK' or details[0].contract.currency!='USD':
            raise ValueError('US dollar stock definition unavailable')
        d = details[0]
        fallback=scanner_sources()['quoteFallbackConfigured']
        async def get(duration, interval, rth):
            return await ib.reqHistoricalDataAsync(d.contract,'',duration,interval,'TRADES',rth,formatDate=2,keepUpToDate=False,timeout=8 if fallback else 20)
        try:
            minute,daily = await asyncio.gather(get('2 D','1 min',False),get('1 M','1 day',True))
            if not minute or not daily: raise ValueError('Scanner verification history incomplete')
            result=verification(d,minute,daily,int(time.time()*1000))
            if result['premarketVolume'] is None or result['previousClose'] is None:
                raise ValueError('IBKR history lacks observed premarket volume/prior close')
        except (ValueError,ConnectionError,asyncio.TimeoutError):
            if not fallback: raise
            result=await asyncio.to_thread(sip_verification,d,int(time.time()*1000))
        if engine._ib is not ib: raise ValueError('Gateway connection changed during contract verification')
        return result
