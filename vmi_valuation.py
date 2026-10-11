"""Course-led automatic valuation; missing evidence is not an assumed input.
See VMI_MASTER_DOCUMENT.md 2708-2733, 3234-3248, 5570-5572.
Shape tolerances and public macro-series choice are disclosed app policies.
"""
import datetime as dt
import json
import math
import time
import urllib.parse
import vmi_data as v

MACRO_URL='https://www.stern.nyu.edu/~adamodar/pc/datasets/histimpl.xls'


def finite(x): return type(x) in (int,float) and math.isfinite(x)


def macro_rows(rows,today=None):
    today=today or dt.date.today()
    header=next((r for r in rows if all(k in r for k in ('Year','T.Bond Rate','Implied ERP (FCFE)'))),None)
    if not header: raise ValueError('Macro workbook column definitions changed')
    yi,ri,ei=[header.index(k) for k in ('Year','T.Bond Rate','Implied ERP (FCFE)')]
    years=list(range(today.year-5,today.year));values={}
    for row in rows:
        if len(row)<=max(yi,ri,ei): continue
        year=row[yi]
        if not finite(year) or year not in years: continue
        rf,erp=row[ri],row[ei]
        if not finite(rf) or not finite(erp) or not 0<=rf<.25 or not 0<erp<.3: raise ValueError('Invalid macro units/value')
        obs=dict(year=int(year),riskFree=rf*100,erp=erp*100)
        if int(year) in values and values[int(year)]!=obs: raise ValueError('Conflicting macro year')
        values[int(year)]=obs
    if sorted(values)!=years: raise ValueError('Five completed consecutive macro years through '+str(today.year-1)+' required')
    obs=[values[y] for y in years]
    return dict(riskFree=sum(r['riskFree'] for r in obs)/5,erp=sum(r['erp'] for r in obs)/5,observations=obs,source=MACRO_URL,retrievedAt=int(time.time()*1000),
        basis='Arithmetic mean of five completed year-end T.Bond Rate and Implied ERP (FCFE) observations from Damodaran. Public implementation of the course formula; NOT the private course beta lookup table or daily five-year averages.')


def macro():
    import xlrd
    sheet=xlrd.open_workbook(file_contents=v.fetch(MACRO_URL,binary=True)).sheet_by_name('Historical Impl Premiums')
    return macro_rows([sheet.row_values(i) for i in range(sheet.nrows)])


def discount(beta,assumptions,source,retrieved_at):
    if not finite(beta) or not source or not finite(retrieved_at) or not 0<=time.time()*1000-retrieved_at<=86400000:
        raise ValueError('Fresh sourced company beta required')
    rate=assumptions['riskFree']+beta*assumptions['erp']
    if not finite(rate) or not 0<rate<=100: raise ValueError('Unsupported beta-derived discount rate')
    return dict(assumptions,beta=beta,betaSource=source,betaRetrievedAt=retrieved_at,rate=rate,formula='Five-year mean risk-free + beta × five-year mean market risk premium')


def quote_field(ticker,key):
    import vmi_sp500 as api
    errors=[]
    for cache_key,loader in [(('snapshot',ticker),lambda:api.snapshot(ticker)),(('yahoo-market',ticker),lambda:api.yahoo_market(ticker))]:
        try:
            row=api.cached(cache_key,900,loader)
            value=v.number(row['fields'].get(key))
            if value is not None: return value,row
        except Exception as exc: errors.append(str(exc))
    raise ValueError('Missing sourced '+key+': '+'; '.join(errors))


def company_discount(ticker):
    import vmi_sp500 as api
    beta,row=quote_field(ticker,'Beta')
    assumptions=api.cached(('valuation-macro',dt.date.today().year),86400,macro)
    return discount(beta,assumptions,row['source'],row['retrievedAt'])


def historical_multiple(ticker,method):
    if method not in ('PB','PS'): raise ValueError('No validated provider NAV history adapter')
    key='annualPbRatio' if method=='PB' else 'annualPsRatio';alias=v.symbol(ticker).replace('.','-')
    url='https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/'+alias+'?'+urllib.parse.urlencode(dict(type=key,period1=1262304000,period2=int(time.time())))
    dates={}
    for item in json.loads(v.fetch(url)).get('timeseries',{}).get('result') or []:
        if alias not in item.get('meta',{}).get('symbol',[]): raise ValueError('Historical multiple listing mismatch')
        for r in item.get(key,[]):
            value,end=r.get('reportedValue',{}).get('raw'),r.get('asOfDate')
            if not finite(value) or value<=0 or not end or end>dt.date.today().isoformat(): continue
            dt.date.fromisoformat(end)
            if end in dates and dates[end]['val']!=value: raise ValueError('Conflicting historical multiple')
            dates[end]=dict(val=value,end=end,source=url,tag=key,unit='ratio')
    return sorted(dates.values(),key=lambda r:r['end'])


def five(rows):
    from vmi_metrics import window
    return window(rows,5)


def rising(rows):
    from vmi_metrics import slope
    rows=five(rows)
    return bool(rows and all(r['val']>0 for r in rows) and rows[-1]['val']>rows[0]['val'] and slope([r['val'] for r in rows])>0 and sum(b['val']<a['val'] for a,b in zip(rows,rows[1:]))<=2)


def select(member,data):
    from vmi_metrics import match,calc
    industry=member.get('industry','').lower();sector=member.get('sector','')
    s=data['series'];c=data['current'];ocf=c.get('ocf');ni=c.get('income');capex=c.get('capex')
    if not industry: return dict(method=None,reason='Industry classification not established')
    if ('banks' in industry or industry.endswith('bank')) and 'investment banking' not in industry:
        return dict(method='PB',reason='Banks: course book valuation, never DCF')
    if any(k in industry for k in ('reit','real estate','property')):
        return dict(method='PB',reason='REIT/property: course P/B alternative, not accounting equity relabeled NAV',yieldHurdle=5)
    if sector in ('Utilities','Energy','Materials') or any(k in industry for k in ('airline','shipping','construction')):
        return dict(method='PS',reason='Cyclical business: five-year mean P/S × revenue/share')
    if ni and ni['val']<=0:
        growth=v.number(data.get('market',{}).get('Revenue Growth Forecast'))
        speculative=member.get('category')=='speculative' or growth is not None and growth>=50
        return dict(method='PSG' if speculative else 'PB',reason='Loss-making business: no cash-flow extrapolation',distressed=not speculative)
    # Payment networks/exchanges can have ordinary operating cash flows (the
    # course uses Mastercard/SGX as FCF examples); GICS Financials alone is NOT NI.
    if any(k in industry for k in ('insurance','broker','asset management','capital markets','financial services','consumer finance','mortgage finance')):
        return dict(method='NI',reason='Financial business with loan/fund-flow-distorted cash flows; income consistency required')
    if not rising(s.get('ocf',[])): return dict(method=None,reason='Consistent positive five-year CFO trend not established')
    caps=five(s.get('capex',[]))
    lumpy=bool(caps and all(r['val']>=0 for r in caps) and max(r['val'] for r in caps)>0 and (min(r['val'] for r in caps)==0 or max(r['val'] for r in caps)>2*min(r['val'] for r in caps) or caps[-1]['val']>1.5*sum(r['val'] for r in caps)/5))
    if lumpy: return dict(method='NORMALIZED',reason='Consistent CFO, lumpy capex: five-year average capex. App trigger max/min >2 or latest >1.5× mean')
    if ocf and ni and ocf['end']==ni['end'] and 0<ocf['val']<=1.2*ni['val']:
        return dict(method='OCF',reason='Consistent CFO ≤1.2× positive same-period NI (course main lesson)')
    fcf=[calc(r['end'],r['val']-p['val'],'CFO - capex',[r,p]) for r in s.get('ocf',[]) if (p:=match(s.get('capex',[]),r['end']))]
    if rising(fcf) and ocf and capex and ocf['end']==capex['end'] and ocf['val']>capex['val']:
        return dict(method='FCF',reason='Consistently rising CFO and positive rising FCF')
    return dict(method=None,reason='No supported cash-flow pattern established; valuation acquisition/assessment unfinished')


def proposal(member,data,acquire=True):
    import vmi_sp500 as api
    from vmi_metrics import match
    decision=select(member,data);decision.update(source='VMI_MASTER_DOCUMENT.md:3234-3248',status='ACQUIRE',missing=[])
    method=decision['method'];c=data['current'];s=data['series'];inputs=[]
    if acquire and method in ('FCF','NORMALIZED','OCF','NI'):
        try: decision['discountEvidence']=company_discount(member['symbol'])
        except Exception as exc: decision['missing'].append(str(exc))
    def require(key,max_age=180):
        row=c.get(key)
        if not row or not finite(row.get('val')): raise ValueError('Missing '+key)
        age=(dt.date.today()-dt.date.fromisoformat(row['end'])).days
        if not 0<=age<=max_age: raise ValueError('Stale/future '+key+' period '+row['end'])
        inputs.append(row);return row
    try:
        if not method: raise ValueError(decision['reason'])
        if not acquire: raise ValueError('Valuation retrieval deferred until qualification; no valuation gate added')
        x=dict(method=method,fx=1,adr=1,basis=decision['reason'])
        if method in ('PB','PS'):
            if decision.get('distressed'): raise ValueError('Distressed tangible liquidation-book evidence required; ordinary equity cannot substitute')
            base=require('equity' if method=='PB' else 'revenue',180 if method=='PB' else 450)
            if method=='PB':
                bvps,row=quote_field(member['symbol'],'Book/sh')
                if bvps<=0: raise ValueError('Positive listing book/share required')
                x.update(base=bvps,shares=1)
                inputs.append(dict(source=row['source'],retrievedAt=row['retrievedAt'],val=bvps,unit='USD/share',basis='Provider listing book/share'))
            else:
                if data.get('shareClassCount')!=1: raise ValueError('Single-common-class share basis not verified; per-class allocation required')
                share=require('shares')
                if share['val']<=0: raise ValueError('Positive listing-compatible shares required')
                x.update(base=base['val'],shares=share['val'])
            history=api.cached(('valuation-multiple',member['symbol'],method),86400,lambda:historical_multiple(member['symbol'],method))
            observations=five(history)
            if not observations: raise ValueError('Five consecutive recent annual '+method+' ratios required; available '+str(len(history)))
            x['multiple']=sum(r['val'] for r in observations)/5;inputs+=observations
            if decision.get('yieldHurdle'):
                dividend,row=quote_field(member['symbol'],'Dividend/share')
                if dividend<=0: raise ValueError('Positive annual REIT distribution/share required')
                inputs.append(dict(source=row['source'],retrievedAt=row['retrievedAt'],val=dividend,unit='USD/share/year'))
                x['multiple']=min(1.2,x['multiple']);x['yieldPriceCap']=dividend/.05
            x['flowPeriod']=base['end']
        elif method=='PSG':
            growth,row=quote_field(member['symbol'],'Revenue Growth Forecast');ps,ps_row=quote_field(member['symbol'],'P/S')
            if growth<=0 or ps<=0 or row['fields'].get('Revenue Forecast Horizon')!='+1y': raise ValueError('Positive projected revenue growth with explicit horizon required; no EPS substitution')
            x.update(ratio=ps,revenueGrowth=growth,horizon='+1y',basis='PSG rough guide: next-fiscal-year revenue growth, not intrinsic value')
            inputs.extend([dict(source=row['source'],retrievedAt=row['retrievedAt'],val=growth,unit='percent',horizon='+1y'),dict(source=ps_row['source'],retrievedAt=ps_row['retrievedAt'],val=ps,unit='P/S')])
        else:
            flow=require('income' if method=='NI' else 'ocf',450)
            if method=='NI' and not rising(s.get('income',[])): raise ValueError('Consistently increasing five-year NI required')
            if data.get('shareClassCount')!=1: raise ValueError('Single-common-class share basis not verified; per-class allocation required')
            share=require('dilutedShares',450)
            if share['val']<=0: raise ValueError('Positive diluted weighted-average shares required')
            if c.get('cashInvestments'): cash=require('cashInvestments')
            else:
                cash=require('cash');investments=require('investments')
                if not match([investments],cash['end']): raise ValueError('Cash/investment dates mismatch')
                cash=dict(cash,val=cash['val']+investments['val'])
            debt=require('debt')
            if not debt.get('completeDebt',True): raise ValueError('Complete short- and long-term debt required')
            if cash['val']<0 or debt['val']<0 or not match([debt],cash['end']): raise ValueError('Cash/debt amounts or dates invalid')
            x.update(base=flow['val'],ocf=c.get('ocf',{}).get('val'),ni=c.get('income',{}).get('val'),shares=share['val'],cash=cash['val'],debt=debt['val'],flowPeriod=flow['end'])
            if method=='FCF':
                capex=require('capex',450)
                if capex['end']!=flow['end'] or capex['val']<0: raise ValueError('FCF capex/flow mismatch')
                x['base']-=capex['val']
            if method=='NORMALIZED':
                caps=five(s.get('capex',[]))
                if not caps or any(r['val']<0 for r in caps): raise ValueError('Five nonnegative annual capex observations required')
                inputs+=caps;x['capex']=[r['val'] for r in caps];x['base']=flow['val']-sum(x['capex'])/5
            if x['base']<=0: raise ValueError('Positive valuation flow required')
            forecast=v.number(data['market'].get('EPS next 5Y',data['market'].get('EPS Next 5Y')))
            if forecast is None or not -100<forecast<=100: raise ValueError('Long-term growth forecast required; no one-year substitution')
            x['forecast']=forecast
            rate=decision.get('discountEvidence')
            if not rate: raise ValueError('Company discount evidence unavailable')
            x.update(discount=rate['rate'],discountEvidence=rate)
        x['inputs']=inputs
        if decision['missing']: return None,decision
        decision['status']='READY'
        return x,decision
    except Exception as exc:
        decision['missing'].append(str(exc));decision['acquiredInputs']=inputs
        return None,decision
