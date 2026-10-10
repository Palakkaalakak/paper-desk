"""Independent public financial-source recovery for the local VMI scanner."""
import json
import math
import re
import time
import urllib.error
import vmi_data as v

_FINVIZ_UNTIL=0


def finviz_fetch(url):
    global _FINVIZ_UNTIL
    if time.monotonic()<_FINVIZ_UNTIL:
        raise ValueError('Finviz cooldown after HTTP 429; use independent source')
    try: return v.fetch(url)
    except urllib.error.HTTPError as exc:
        if exc.code==429: _FINVIZ_UNTIL=time.monotonic()+900
        raise


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
    base='https://finance.yahoo.com/quote/'+v.symbol(ticker).replace('.','-')+'/'
    attempts=[]
    # Some analysis pages lack quoteSummary while key-statistics has the data.
    for suffix in ('analysis/', 'key-statistics/', ''):
        url=base+suffix
        try:
            row=decode_yahoo(v.fetch(url),ticker,url)
            row['attempts']=attempts
            return row
        except (ValueError, urllib.error.URLError) as exc:
            attempts.append(dict(source=url,error=str(exc)))
    raise ValueError('Yahoo financial page recovery exhausted: '+json.dumps(attempts))
