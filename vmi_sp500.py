"""Explicit S&P 500 scanner acquisition. Read-only, no IB/chart subscriptions."""
import csv
import io
import json
import re
import threading
import time
import urllib.parse
import providers
import vmi_data as v
from vmi_sources import finviz_fetch, yahoo_market

CSV_URL='https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv'
WIKI_URL='https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
MODES=('conservative','moderate','optimistic')
# Transient request cache, not historical storage. Never cache raw CompanyFacts blobs.
_CACHE={}
_LOCK=threading.RLock()


def cached(key, ttl, fn):
    now=time.monotonic()
    with _LOCK:
        hit=_CACHE.get(key)
        if hit and now-hit[0]<ttl: return hit[1]
    result=fn()
    with _LOCK:
        if len(_CACHE)>=2400:
            for k in sorted(_CACHE,key=lambda k:_CACHE[k][0])[:300]: del _CACHE[k]
        _CACHE[key]=(time.monotonic(),result)
    return result


def validate_members(rows):
    if not 490<=len(rows)<=520 or len({r['symbol'] for r in rows})!=len(rows):
        raise ValueError('Incomplete/duplicate constituent list')
    for r in rows:
        r['symbol']=v.symbol(r['symbol'])
        if not r['name'] or not r['industry'] or not 0<int(r['cik'])<10**10: raise ValueError('Incomplete constituent identity')
    return sorted(rows,key=lambda r:r['symbol'])


def universe():
    attempts=[]
    for source in (CSV_URL,WIKI_URL):
        try:
            text=v.fetch(source)
            if source==CSV_URL:
                rows=[dict(symbol=r['Symbol'],name=r['Security'],sector=r['GICS Sector'],industry=r['GICS Sub-Industry'],cik=int(r['CIK'])) for r in csv.DictReader(io.StringIO(text))]
            else:
                p=v.Tables('wikitable');p.feed(text)
                rows=[dict(symbol=r[0],name=r[1],sector=r[2],industry=r[3],cik=int(r[6])) for r in p.rows if len(r)>=8 and r[6].isdigit()]
            return dict(rows=validate_members(rows),source=source,retrievedAt=int(time.time()*1000),attempts=attempts,
                        basis='Public current-constituent mirror; share classes retained. Retrieval time is not an index effective date.')
        except Exception as exc: attempts.append(dict(source=source,error=str(exc)))
    raise ValueError('Constituent retrieval exhausted independent sources: '+json.dumps(attempts))


def members(): return cached(('members',),3600,universe)


class Screener(v.Tables):
    """Read the quote link, not ticker text containing hidden duplicate initials."""
    def __init__(self):
        self.link_symbol=None
        super().__init__('screener_table')
    def handle_starttag(self,tag,attrs):
        super().handle_starttag(tag,attrs)
        if self.depth and tag=='a':
            href=dict(attrs).get('href','')
            if 'stock?' in href or 'quote.ashx?' in href:
                ticker=urllib.parse.parse_qs(urllib.parse.urlsplit(href).query).get('t',[None])[0]
                if ticker: self.link_symbol=v.symbol(ticker)
    def finish_row(self):
        self.finish_cell()
        if self.row and self.link_symbol and len(self.row)>1: self.row[1]=self.link_symbol
        super().finish_row();self.link_symbol=None


def snapshot(ticker):
    ticker=v.symbol(ticker);alias=ticker.replace('.','-');url='https://finviz.com/quote.ashx?t='+alias
    text=finviz_fetch(url)
    if not re.search(r'(?:[?&]t=|"ticker"\s*:\s*")'+re.escape(alias)+r'(?:[&"\s]|$)',text): raise ValueError('Snapshot listing identity mismatch')
    p=v.Tables('snapshot-table2');p.feed(text);fields={}
    for r in p.rows: fields.update(zip(r[::2],r[1::2]))
    if not fields: raise ValueError('Snapshot table absent')
    return dict(symbol=ticker,fields=fields,source=url,retrievedAt=int(time.time()*1000))


def bulk(view,tickers):
    if view not in ('financial','growth') or not 1<=len(tickers)<=20 or len(set(tickers))!=len(tickers): raise ValueError('Invalid bulk view/size/duplicates')
    aliases={v.symbol(s).replace('.','-'):s for s in tickers}
    url='https://finviz.com/screener.ashx?'+urllib.parse.urlencode(dict(v=161 if view=='financial' else 121,t=','.join(aliases),o='ticker'))
    p=Screener();p.feed(finviz_fetch(url));header=next((r for r in p.rows if 'Ticker' in r),None)
    if not header: raise ValueError('Bulk financial table absent')
    result={}
    for row in p.rows:
        if row==header or len(row)!=len(header): continue
        fields=dict(zip(header,row));alias=fields.get('Ticker','').replace('.','-')
        if alias in aliases:
            ticker=aliases[alias];result[ticker]=dict(symbol=ticker,fields=fields,source=url,retrievedAt=int(time.time()*1000))
    return result


def financial_row(ticker):
    try: return bulk('financial',[ticker]).get(ticker) or snapshot(ticker)
    except Exception: return cached(('yahoo-market',ticker),1800,lambda:yahoo_market(ticker))


def sector(member):
    industry=member['industry'].lower()
    return dict(bank='bank' in industry,insurance='insurance' in industry or 'reinsurance' in industry,
                reit='reit' in industry,utility=member['sector']=='Utilities',
                excluded=member['sector']=='Energy' or any(x in industry for x in ('airline','real estate development','mining','steel','aluminum','gold','copper')))


def quick(member,fields,mode):
    if mode not in MODES: raise ValueError('Invalid preset')
    flags=sector(member);threshold=15 if mode=='conservative' else 12;reasons=[]
    roe=v.number(fields.get('ROE'));roic=v.number(fields.get('ROIC'));growth=v.number(fields.get('EPS Next 5Y',fields.get('EPS next 5Y')))
    if flags['excluded']: reasons.append('Course industry exclusion: commodity/property developer/airline')
    # Negative ROE may be the source's profitable treasury-stock exception.
    # Require the actual balance sheet before deciding; never invent infinite ROE.
    if roe is not None and roe>=0 and (roe<=threshold if mode=='conservative' else roe<threshold): reasons.append(f'Current ROE {roe:g}% below preset threshold')
    if not any(flags[k] for k in ('bank','insurance','utility')) and roic is not None and roic<threshold: reasons.append(f'Current ROIC {roic:g}% < {threshold}%')
    if growth is not None and growth<=0: reasons.append(f'Projected earnings growth {growth:g}% is not positive')
    return dict(rejected=bool(reasons),reasons=reasons)


def batch(view,symbols,mode='moderate'):
    tickers=[v.symbol(s) for s in symbols.split(',')]
    if view not in ('financial','growth') or not 1<=len(tickers)<=20 or len(set(tickers))!=len(tickers) or mode not in MODES: raise ValueError('Invalid batch request')
    membership={r['symbol']:r for r in members()['rows']}
    if not set(tickers)<=membership.keys(): raise ValueError('Non-constituent in batch')
    def load():
        result={};attempts=[]
        try: result=bulk(view,tickers)
        except Exception as exc: attempts.append(dict(source='Finviz bulk',error=str(exc)))
        for ticker in tickers:
            if ticker not in result:
                try: result[ticker]=cached(('snapshot',ticker),900,lambda:snapshot(ticker))
                except Exception as exc:
                    attempts.append(dict(symbol=ticker,source='Finviz individual fallback',error=str(exc)))
                    try: result[ticker]=cached(('yahoo-market',ticker),1800,lambda:yahoo_market(ticker))
                    except Exception as other: attempts.append(dict(symbol=ticker,source='Yahoo independent source',error=str(other)))
        with _LOCK:
            for ticker,row in result.items(): _CACHE[('row',view,ticker)]=(time.monotonic(),row)
        return dict(rows=list(result.values()),missing=[s for s in tickers if s not in result],attempts=attempts,retrievedAt=int(time.time()*1000))
    response=cached(('batch',view,tuple(tickers)),900,load)
    # Preset results are not stored in the provider cache.
    return dict(response,rows=[dict(row,screen=quick(membership[row['symbol']],row['fields'],mode)) for row in response['rows']])


def market_fields(ticker):
    fields={};sources=[];attempts=[]
    for view in ('financial','growth'):
        def load():
            try: return bulk(view,[ticker]).get(ticker) or snapshot(ticker)
            except Exception: return cached(('yahoo-market',ticker),1800,lambda:yahoo_market(ticker))
        try:
            row=cached(('row',view,ticker),900,load);fields.update(row['fields']);sources.append(row['source'])
        except Exception as exc: attempts.append(dict(source='Finviz '+view,error=str(exc)))
    for a,b in [('EPS Next 5Y','EPS next 5Y'),('Curr R','Current Ratio'),('Gross M','Gross Margin'),('Profit M','Profit Margin')]:
        if a in fields: fields[b]=fields[a]
    return dict(fields=fields,sources=sources,attempts=attempts)


def company(ticker,mode='moderate'):
    from vmi_metrics import evidence,evaluate
    ticker=v.symbol(ticker)
    if mode not in MODES: raise ValueError('Invalid preset')
    member=next((r for r in members()['rows'] if r['symbol']==ticker),None)
    if not member: raise ValueError('Not an S&P 500 constituent')
    data=cached(('evidence',ticker),21600,lambda:evidence(member))
    import copy
    import market
    from vmi_ib import available,valid_quote
    data=copy.deepcopy(data)
    broker=available(market.ENGINE,ticker)
    data['ibkr']=broker
    if broker.get('available'):
        data['providerAlternatives']=dict(data['market'])
        data['market'].update(broker.get('fields',{}))
        data['sources'].append('IBKR API / conid '+str(broker['conid']))
        if valid_quote(broker.get('quote',{}),broker['conid']):
            data['market']['Price']=str(broker['quote']['last'])
            data['market']['Price source']='IBKR LIVE / conid '+str(broker['conid'])
    result=evaluate(member,data,mode)
    if not result['failures']:
        check=peer_check(member,data)
        result['checks']=[check if c['key']=='peers' else c for c in result['checks']]
        result['failures']=[c['label'] for c in result['checks'] if c['status']=='FAIL']
        result['pending']=[c['key'] for c in result['checks'] if c['status']=='ACQUIRE']
        result['status']='REJECTED' if result['failures'] else 'ACQUIRING' if result['pending'] else 'QUALIFIED'
    return dict(member=member,evidence=data,result=result,research=research_payload(member,data))


def peer_check(member,data):
    peers=[];seen={member['cik']};errors=[]
    for r in members()['rows']:
        if r['industry']==member['industry'] and r['cik'] not in seen:
            seen.add(r['cik'])
            try:
                row=cached(('row','financial',r['symbol']),900,lambda r=r:financial_row(r['symbol']))
                peers.append(row)
            except Exception as exc: errors.append(dict(symbol=r['symbol'],error=str(exc)))
    peers.sort(key=lambda r:v.number(r['fields'].get('Market Cap')) or 0,reverse=True)
    selected=peers[:3];comparisons=[];missing=not selected or bool(errors)
    keys=['Profit M'] if sector(member)['bank'] or sector(member)['insurance'] else ['Gross M','Profit M']
    for key in keys:
        own=v.number(data['market'].get(key,data['market'].get({'Gross M':'Gross Margin','Profit M':'Profit Margin'}[key])))
        for row in selected:
            value=v.number(row['fields'].get(key))
            if own is None or value is None: missing=True
            comparisons.append(dict(metric=key,company=own,peer=row['symbol'],value=value,source=row['source'],retrievedAt=row['retrievedAt']))
    failed=any(r['company'] is not None and r['value'] is not None and r['company']<r['value'] for r in comparisons)
    return dict(key='peers',label='Margins vs comparable businesses',status='FAIL' if failed else 'ACQUIRE' if missing else 'PASS',detail='Each of up to three largest distinct S&P 500 issuers in the same GICS sub-industry. Industry-comparable proxy, not an exhaustive direct-competitor map.',observations=comparisons,attempts=errors)


def research_payload(member,data):
    from vmi_metrics import calculate,latest
    current=data['current'];s=calculate(data['series']);metrics={}
    for key,target in [('roe','roe'),('roic','roic'),('currentRatio','current'),('service','service'),('debtEbitda','debtEbitda')]:
        row=latest(s.get(key,[]))
        if row: metrics[target]=row['val']
    growth=v.number(data['market'].get('EPS next 5Y'));metrics['growth']=growth
    metrics['years']=len(data['series'].get('revenue',[]));valuation=None
    ocf,capex,cash,debt,shares=[current.get(k) for k in ('ocf','capex','cash','debt','shares')]
    # No missing balance or absent capex is silently zeroed.
    if all(r is not None for r in (ocf,capex,cash,debt,shares)) and ocf['end']==capex['end'] and shares['val']>0 and ocf['val']>capex['val'] and debt.get('completeDebt',True):
        valuation=dict(method='FCF',base=ocf['val']-capex['val'],cash=cash['val'],debt=debt['val'],shares=shares['val'],forecast=growth,fx=1,adr=1,flowPeriod=ocf['end'],inputs=[ocf,capex,cash,debt,shares],basis='Trailing cash flow and latest available balances; method suitability remains separate')
    return dict(symbol=member['symbol'],name=member['name'],cik=member['cik'],currency='USD',metrics=metrics,flows=data['series'],balances=current,market=data['market'],sources=data['sources'],provenance={},staleMetrics=[],valuationInputs=valuation,retrievedAt=data['retrievedAt'],scannerEvidence=data)
