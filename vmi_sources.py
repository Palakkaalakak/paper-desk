"""Independent public financial-source recovery for the local VMI scanner."""
import json
import math
import re
import time
import threading
import urllib.error
import vmi_data as v
import vmi_trace as trace

_FINVIZ_UNTIL=0
_FINVIZ_LOCK=threading.Lock()
_YAHOO_LOCK=threading.Lock()
_YAHOO_UNTIL=0


def cooldowns():
    now=time.monotonic()
    return dict(finvizSeconds=max(0,round(_FINVIZ_UNTIL-now)),yahooSeconds=max(0,round(_YAHOO_UNTIL-now)))


def finviz_fetch(url, *, fast=False):
    global _FINVIZ_UNTIL
    if time.monotonic()<_FINVIZ_UNTIL:
        trace.event('cooldown','Finviz cooldown; independent recovery required',provider='finviz.com',waitSeconds=round(_FINVIZ_UNTIL-time.monotonic()))
        raise ValueError('Finviz cooldown; independent recovery required')
    trace.event('provider_wait','Waiting for Finviz slot',provider='finviz.com')
    if not _FINVIZ_LOCK.acquire(timeout=3 if fast else 25): raise ValueError('Finviz slot busy; defer recovery')
    try:
        if time.monotonic()<_FINVIZ_UNTIL: raise ValueError('Finviz cooldown; independent recovery required')
        try: return v.fetch(url,retry_429=False,timeout=8 if fast else 15,retries=0 if fast else 1)
        except urllib.error.HTTPError as exc:
            if exc.code in (403,429): _FINVIZ_UNTIL=time.monotonic()+(900 if exc.code==429 else 120)
            elif exc.code>=500: _FINVIZ_UNTIL=time.monotonic()+30
            raise
        except (urllib.error.URLError,TimeoutError,OSError):
            _FINVIZ_UNTIL=time.monotonic()+30
            raise
    finally: _FINVIZ_LOCK.release()


def decode_yahoo(text,ticker,url):
    alias=v.symbol(ticker).replace('.','-');modules=None
    for attributes,body in re.findall(r'<script([^>]*)>(.*?)</script>',text,re.S):
        if 'application/json' not in attributes: continue
        try:
            outer=json.loads(body)
            decoded=json.loads(outer['body']) if isinstance(outer,dict) and isinstance(outer.get('body'),str) else outer
            for item in decoded.get('quoteSummary',{}).get('result') or []:
                if item.get('price',{}).get('symbol')==alias: modules=item;break
        except (ValueError,TypeError,AttributeError): continue
        if modules: break
    if modules is None: raise ValueError('No identity-verified Yahoo financial payload')
    financial=modules.get('financialData',{});price=modules.get('price',{});fields={}
    if price.get('currency')!='USD' or financial.get('financialCurrency')!='USD': raise ValueError('Yahoo listing/financial currency must be USD')
    def number(obj,key):
        value=obj.get(key,{});value=value.get('raw') if isinstance(value,dict) else value
        return value if type(value) in (int,float) and math.isfinite(value) else None
    for key,label,scale in [('returnOnEquity','ROE',100),('currentRatio','Curr R',1),('grossMargins','Gross M',100),('profitMargins','Profit M',100),('operatingMargins','Oper M',100)]:
        value=number(financial,key)
        if value is not None: fields[label]=str(value*scale)
    for key,label in [('marketCap','Market Cap'),('regularMarketPrice','Price')]:
        value=number(price,key)
        if value is not None: fields[label]=str(value)
    for module,key,label in [('defaultKeyStatistics','beta','Beta'),('defaultKeyStatistics','bookValue','Book/sh'),('defaultKeyStatistics','priceToBook','P/B'),('summaryDetail','priceToSalesTrailing12Months','P/S'),('summaryDetail','dividendRate','Dividend/share')]:
        value=number(modules.get(module,{}),key)
        if value is not None: fields[label]=str(value)
    revenue=next((r for r in modules.get('earningsTrend',{}).get('trend',[]) if r.get('period')=='+1y'),None)
    if revenue:
        growth=number(revenue.get('revenueEstimate',{}),'growth')
        if growth is not None:
            fields['Revenue Growth Forecast']=str(growth*100)
            fields['Revenue Forecast Horizon']='+1y'
    for period in ('+5y','+1y'):
        forecast=next((r for r in modules.get('earningsTrend',{}).get('trend',[]) if r.get('period')==period),None)
        if forecast:
            value=number(forecast,'growth')
            if value is not None:
                fields['Forecast Growth']=str(value*100);fields['Forecast Horizon']=period
                if period=='+5y': fields['EPS next 5Y']=str(value*100)
                break
    return dict(symbol=ticker,fields=fields,source=url,retrievedAt=int(time.time()*1000),basis='Yahoo public financialData; forecast horizon retained. One-year growth is NOT a five-year DCF assumption.')


def yahoo_market(ticker):
    global _YAHOO_UNTIL
    base='https://finance.yahoo.com/quote/'+v.symbol(ticker).replace('.','-')+'/'
    attempts=[]
    # Some analysis pages lack quoteSummary while key-statistics has the data.
    for suffix in ('analysis/', 'key-statistics/', ''):
        url=base+suffix
        with _YAHOO_LOCK: until=_YAHOO_UNTIL
        if time.monotonic()<until:
            trace.event('cooldown','Yahoo public-page cooldown; statements remain independent',provider='finance.yahoo.com',waitSeconds=round(until-time.monotonic()))
            raise ValueError('Yahoo public-page cooldown; statement recovery remains independent')
        trace.event('fallback','Yahoo '+ticker+' '+(suffix or 'quote'),provider='finance.yahoo.com')
        try:
            row=decode_yahoo(v.fetch(url,retry_429=False,timeout=12,retries=0),ticker,url)
            row['attempts']=attempts
            return row
        except (ValueError,urllib.error.URLError,TimeoutError,OSError) as exc:
            attempts.append(dict(source=url,error=str(exc)))
            if isinstance(exc,urllib.error.HTTPError) and exc.code in (403,429):
                with _YAHOO_LOCK: _YAHOO_UNTIL=time.monotonic()+(900 if exc.code==429 else 120)
                trace.event('cooldown','Yahoo blocked; further page retries suppressed',provider='finance.yahoo.com',status=exc.code)
                break
    raise ValueError('Yahoo financial page recovery exhausted: '+json.dumps(attempts))
