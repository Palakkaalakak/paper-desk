"""VMI financial evidence, calculations and non-moat business gates.
Source thresholds: VMI_MASTER_DOCUMENT.md lines 2460-2503, 4241-4295, 4480-4559.
Qualitative trend tolerances and industry-peer selection are explicit app policies.
"""
import datetime as dt
import json
import math
import re
import time
import urllib.parse
import vmi_data as v

FLOW=dict(v.FLOW_TAGS,**{
 'revenue':['RevenueFromContractWithCustomerExcludingAssessedTax','Revenues','SalesRevenueNet','RevenueFromContractWithCustomerIncludingAssessedTax'],
 'capex':['PaymentsToAcquirePropertyPlantAndEquipment','PaymentsToAcquireProductiveAssets'],
 'gross':['GrossProfit'],'cost':['CostOfRevenue','CostOfGoodsAndServicesSold','CostOfGoodsSold'],
 'pretax':['IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest','IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments'],
 'interestIncome':['InterestIncomeExpenseNonoperatingNet','InterestAndDividendIncomeOperating'],
})
BAL={
 'cash':['CashAndCashEquivalentsAtCarryingValue'], 'cashInvestments':['CashCashEquivalentsAndShortTermInvestments'], 'investments':['ShortTermInvestments','MarketableSecuritiesCurrent'],
 'equity':['StockholdersEquity','StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest'],
 'retained':['RetainedEarningsAccumulatedDeficit'],
 'treasury':['TreasuryStockValue'], 'assets':['Assets'], 'ca':['AssetsCurrent'], 'cl':['LiabilitiesCurrent'],
 'ar':['AccountsReceivableNetCurrent','AccountsNotesAndOtherReceivablesNetCurrent','AccountsReceivableNet'],
 'inventory':['InventoryNet'], 'ap':['AccountsPayableCurrent','AccountsPayable'],
 'longDebt':['LongTermDebt','LongTermDebtCurrentAndNoncurrent','LongTermDebtAndFinanceLeaseObligationsIncludingCurrentMaturities','LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities'],
 'debtNoncurrent':['LongTermDebtNoncurrent','LongTermDebtAndCapitalLeaseObligations'],
 'debtCurrent':['DebtCurrent'], 'longCurrent':['LongTermDebtCurrent','LongTermDebtAndCapitalLeaseObligationsCurrent'],
 'shortDebt':['ShortTermBorrowings','CommercialPaper'],
 'deferred':['ContractWithCustomerLiabilityCurrent','DeferredRevenueCurrent'],
 'shares':['CommonStockSharesOutstanding'],
}
YAHOO={
 'revenue':'TotalRevenue','income':'NetIncome','operatingIncome':'OperatingIncome','ocf':'OperatingCashFlow',
 'capex':'CapitalExpenditure','gross':'GrossProfit','cost':'CostOfRevenue','tax':'TaxProvision','pretax':'PretaxIncome',
 'interest':'InterestExpense','da':'DepreciationAndAmortization','cash':'CashAndCashEquivalents','investments':'OtherShortTermInvestments',
 'equity':'StockholdersEquity','retained':'RetainedEarnings','treasury':'TreasuryStock','assets':'TotalAssets','ca':'CurrentAssets','cl':'CurrentLiabilities',
 'ar':'AccountsReceivable','inventory':'Inventory','ap':'AccountsPayable','debt':'TotalDebt',
 'ebitda':'EBITDA','deferred':'CurrentDeferredRevenue','shares':'OrdinarySharesNumber',
 'dilutedShares':'DilutedAverageShares','cashInvestments':'CashCashEquivalentsAndShortTermInvestments',
}
POLICY={
 'history':'Five consecutive fiscal years; conservative ten for revenue, earnings and CFO. Positive linear trend; latest exceeds first; at most two declining years.',
 'stability':'Margins: latest at least five-year mean minus 0.5 percentage point; fitted slope >= -0.1 pp/year. Returns: latest >= five-year mean; slope >= 0. These numerical interpretations are app policy, not quoted course thresholds.',
 'fcf':'Source allows 1-2 negative FCF years, not persistent deficits. Conservative: zero; moderate: one; optimistic: two, never consecutive and latest positive.',
 'returns':'Annual ROE = NI / year-end equity. Annual ROIC = EBIT x (1-effective tax rate) / (equity + debt - cash). TTM/quarter-end ratios use the same formulas; provider ROIC is not mixed into calculated history averages.',
 'ccc':'Physical-goods industries only. Latest <= five-year mean +10 days and trend <=2 days/year (explicit app tolerances). No absent balance is assumed zero.',
 'peers':'Three largest distinct S&P 500 issuers in the same GICS sub-industry. A transparent industry proxy, not a claim of identical business mix or a full direct-competitor map.',
 'dates':'USD; annual windows and latest quarter/TTM retained separately. SEC fiscal periods are preferred; Yahoo dates matched within seven days.',
 'moat':'Excluded from scanner qualification; remains available in the separate manual company review.',
 'buybacks':'Negative equity is not rejected on provider ROE alone. The source exception requires matched positive retained earnings and treasury stock sufficient to explain negative equity, plus increasing earnings and FCF. No numeric infinity is stored; ROIC and debt remain independent.',
 'marginSubstitution':'Operating margin may substitute for inconsistent net margin independently of the earnings substitution (source section 2.5). Both use the same five-year stability policy.',
}


def match(rows,end):
    candidates=[r for r in rows if abs((dt.date.fromisoformat(r['end'])-dt.date.fromisoformat(end)).days)<=7]
    return min(candidates,key=lambda r:abs((dt.date.fromisoformat(r['end'])-dt.date.fromisoformat(end)).days)) if candidates else None


def merged(primary,other):
    result=list(primary)
    for r in other:
        if not match(result,r['end']): result.append(r)
    return sorted(result,key=lambda r:r['end'])[-12:]


def calc(end,val,method,inputs):
    return dict(end=end,val=val,method=method,inputs=inputs,source='Derived from dated financial statements')


def latest(rows): return max(rows,key=lambda r:r['end']) if rows else None


def ttm(facts,tags,annual):
    """Annual + current fiscal YTD - comparable prior YTD, never sum overlapping quarters."""
    if not annual: return None
    base=annual[-1];start=(dt.date.fromisoformat(base['end'])+dt.timedelta(days=1)).isoformat();candidates=[]
    for rank,tag in enumerate(tags):
        for row in facts.get(tag,{}).get('units',{}).get('USD',[]):
            if row.get('form') not in ('10-Q','10-Q/A') or not row.get('start') or not row.get('filed'): continue
            if row['end']>dt.date.today().isoformat() or row['filed']>dt.date.today().isoformat(): continue
            if type(row.get('val')) not in (int,float) or not math.isfinite(row['val']): continue
            candidates.append(dict(row,tag=tag,rank=rank,unit='USD',source=base.get('source')))
    current=[r for r in candidates if abs((dt.date.fromisoformat(r['start'])-dt.date.fromisoformat(start)).days)<=7 and r['end']>base['end']]
    if not current: return dict(base,basis='Latest fiscal year (also latest available trailing year)')
    current.sort(key=lambda r:(r['end'],r['filed'],-r['rank']));now=current[-1]
    days=(dt.date.fromisoformat(now['end'])-dt.date.fromisoformat(now['start'])).days
    prior=[r for r in candidates if 350<=(dt.date.fromisoformat(now['end'])-dt.date.fromisoformat(r['end'])).days<=380 and abs((dt.date.fromisoformat(r['end'])-dt.date.fromisoformat(r['start'])).days-days)<=7]
    if not prior: return None
    old=max(prior,key=lambda r:(r['filed'],-r['rank']))
    return calc(now['end'],base['val']+now['val']-old['val'],'TTM = latest annual + current YTD - prior comparable YTD',[base,now,old])


def yahoo(ticker):
    keys=['annual'+n for n in YAHOO.values()]+['quarterly'+YAHOO[k] for k in ('income','ocf','revenue','debt','cash','cashInvestments','investments','shares','dilutedShares','ca','cl','assets','equity','deferred')]
    url='https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/'+ticker.replace('.','-')+'?'+urllib.parse.urlencode(dict(type=','.join(keys),period1=1262304000,period2=int(time.time())))
    result={}
    for item in json.loads(v.fetch(url)).get('timeseries',{}).get('result') or []:
        if ticker.replace('.','-') not in item.get('meta',{}).get('symbol',[]): raise ValueError('Yahoo identity mismatch')
        for key in keys:
            for row in item.get(key,[]):
                val,end=row.get('reportedValue',{}).get('raw'),row.get('asOfDate')
                if type(val) not in (int,float) or not math.isfinite(val) or not end or end>dt.date.today().isoformat(): continue
                shares=key.endswith(('SharesNumber','AverageShares'))
                if not shares and row.get('currencyCode')!='USD': continue
                if key.endswith('CapitalExpenditure'): val=abs(val)
                result.setdefault(key,[]).append(dict(end=end,val=val,tag=key,source=url,unit='shares' if shares else 'USD',basis='Provider dated fiscal statement'))
    for rows in result.values(): rows.sort(key=lambda r:r['end'])
    return result


def derive_debt(series):
    # Re-derive incomplete estimates when recovery supplies missing components.
    output=list(series.get('debt',[]))
    dates=sorted({r['end'] for k in ('longDebt','debtNoncurrent','debtCurrent') for r in series.get(k,[])})
    for end in dates:
        existing=match(output,end)
        if existing and existing.get('completeDebt',True): continue
        long=match(series.get('longDebt',[]),end);non=match(series.get('debtNoncurrent',[]),end);cur=match(series.get('debtCurrent',[]),end)
        short=match(series.get('shortDebt',[]),end);longcur=match(series.get('longCurrent',[]),end)
        if non and cur: inputs=[non,cur];method='Noncurrent debt + total current debt'
        elif long and short: inputs=[long,short];method='Long-term debt inclusive of current maturities + short-term borrowing'
        elif non and longcur and short: inputs=[non,longcur,short];method='Noncurrent + current long-term maturities + short-term borrowing'
        elif long:
            # LongTermDebt is an actual reported figure, not fabricated total debt.
            # Retain its limited basis; evaluator requires total-debt evidence for the debt gate.
            inputs=[long];method='Reported long-term debt; short-term component not separately established'
        else: continue
        row=calc(end,sum(r['val'] for r in inputs),method,inputs)
        row['completeDebt']=len(inputs)>1
        if existing: output.remove(existing)
        output.append(row)
    return sorted(output,key=lambda r:r['end'])


def reported_rejections(member,series,issuer,mode):
    """Stop only on complete, latest filed annual gates with no substitution.

    Missing/stale history, earnings (operating-income fallback), ratios and
    partial debt never cause this early stop. Full research acquisition remains
    available separately from this preset-specific scanner optimization.
    """
    from vmi_sp500 import sector,MODES
    if mode not in MODES: return []
    recent=issuer.get('filings',{}).get('recent',{});today=dt.date.today().isoformat()
    dates=[end for form,filed,end in zip(recent.get('form',[]),recent.get('filingDate',[]),recent.get('reportDate',[])) if form in ('10-K','10-K/A','20-F','20-F/A') and filed<=today and end and end<=today]
    if not dates: return []
    newest=max(dates);years=10 if mode=='conservative' else 5;failed=[]
    for key in ('revenue','ocf'):
        if key=='ocf' and sector(member)['bank']: continue
        rows=window(series.get(key,[]),years)
        if rows and rows[-1]['end']==newest and growth_test(rows,years) is False:
            failed.append(key)
    return failed


def evidence(member,mode=None):
    import vmi_sp500 as api
    ticker,cik=member['symbol'],member['cik'];s={};current={};attempts=[];sources=[];issuer={};facts={}
    url=f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json';sub=f'https://data.sec.gov/submissions/CIK{cik:010d}.json'
    try:
        # Do not retain large raw filing blobs for a universe-sized scan.
        raw=json.loads(v.fetch(url));issuer=api.cached(('issuer',cik),21600,lambda:json.loads(v.fetch(sub)))
        if int(raw['cik'])!=cik or ticker.replace('-','.') not in [t.replace('-','.') for t in issuer.get('tickers',[])]: raise ValueError('SEC issuer identity mismatch')
        facts=raw.get('facts',{}).get('us-gaap',{})
        flows=dict(FLOW)
        if api.sector(member)['bank']: flows['revenue']=['RevenuesNetOfInterestExpense','Revenues']
        for key,tags in flows.items():
            s[key]=v.observations(facts,tags,annual=True)
            for r in s[key]: r['source']=url
            obs=ttm(facts,tags,s[key])
            if obs: current[key]=obs
        for key,tags in BAL.items():
            unit='shares' if key=='shares' else 'USD'
            annual={tag:{'units':{unit:[r for r in facts.get(tag,{}).get('units',{}).get(unit,[]) if r.get('form') in ('10-K','10-K/A','20-F','20-F/A')]}} for tag in tags}
            s[key]=v.observations(annual,tags,unit=unit)
            obs=latest(v.observations(facts,tags,unit=unit))
            if obs: current[key]=dict(obs,source=url)
            for r in s[key]: r['source']=url
        diluted=v.observations(facts,['WeightedAverageNumberOfDilutedSharesOutstanding'],unit='shares',annual=True)
        if diluted:
            s['dilutedShares']=[dict(r,source=url) for r in diluted]
            current['dilutedShares']=dict(diluted[-1],source=url,basis='Latest annual diluted weighted-average shares, not period-end ordinary shares')
        sources += [url,sub]
    except Exception as exc: attempts.append(dict(source=url,error=str(exc),fallback='Yahoo statements'))
    early=reported_rejections(member,s,issuer,mode) if sources else []
    if early:
        import vmi_trace as trace
        trace.event('early_rejection',ticker+' / latest filed annual '+', '.join(early))
        return dict(symbol=ticker,cik=cik,shareClassCount=len(issuer.get('tickers',[])) or None,series=s,current=current,market={},marketFieldSources={},sources=sources,attempts=attempts,retrievedAt=int(time.time()*1000),policies=POLICY,acquisition=dict(stoppedAfter='SEC reported annual gates',preset=mode,failedKeys=early,deferred=['Yahoo statements','issuer filing recovery','market recovery']))
    # Independent HTML tables supply annual history when API coverage is short.
    s['debt']=derive_debt(s)
    required=('revenue','income','ocf','capex','equity','debt','cash','operatingIncome','tax','pretax','ar')
    if any(window([r for r in s.get(k,[]) if k!='debt' or r.get('completeDebt',True)],10) is None for k in required):
        try:
            from vmi_web import statements
            scraped=api.cached(('html-statements',ticker),21600,lambda:statements(ticker,issuer.get('exchanges',[])))
            for key,rows in scraped['series'].items():
                primary=s.get(key,[])
                if key=='debt': primary=[r for r in primary if r.get('completeDebt',True)]
                s[key]=merged(primary,rows)
                if key in BAL or key=='debt':
                    obs=latest(scraped['quarterly'].get(key,[])+rows)
                    if obs and (key not in current or obs['end']>current[key]['end']): current[key]=obs
            for key in ('income','ocf','revenue'): s['quarterly_'+key]=scraped['quarterly'].get(key,[])
            sources.append(scraped['source']);attempts+=scraped.get('attempts',[])
        except Exception as exc: attempts.append(dict(source='MarketBeat HTML statements',error=str(exc)))
    try:
        alt=api.cached(('yahoo',ticker),21600,lambda:yahoo(ticker))
        for key,name in YAHOO.items():
            rows=alt.get('annual'+name,[])
            s[key]=merged(rows,s.get(key,[])) if key=='debt' else merged(s.get(key,[]),rows)
            if key in BAL or key in ('debt','dilutedShares'):
                obs=latest(alt.get('quarterly'+name,[])+rows)
                if obs and (key not in current or obs['end']>current[key]['end']): current[key]=obs
        for key in ('income','ocf','revenue'): s['quarterly_'+key]=merged(s.get('quarterly_'+key,[]),alt.get('quarterly'+YAHOO[key],[]))
        sources+=sorted({r['source'] for rows in alt.values() for r in rows})
    except Exception as exc: attempts.append(dict(source='Yahoo financial timeseries',error=str(exc),fallback='SEC reported and derived facts'))
    # Existing dated components may already establish total debt; derive first.
    s['debt']=derive_debt(s)
    debt_history=[r for r in s['debt'] if r.get('completeDebt',True)]
    needs_filing=api.sector(member)['bank'] or window(debt_history) is None or any(window(s.get(k,[])) is None for k in ('interest','capex'))
    if needs_filing and issuer:
        try:
            from vmi_filings import recover
            filing=api.cached(('filing-evidence',cik),21600,lambda:recover(member,issuer))
            attempts+=filing['attempts']
            if filing['source']:
                sources.append(filing['source'])
                reported={tag.split(':',1)[1]:value for tag,value in filing['facts'].items() if tag.startswith('us-gaap:')}
                for key,tags in dict(FLOW,**BAL).items():
                    is_flow=key in FLOW;unit='shares' if key=='shares' else 'USD'
                    recovered=v.observations(reported,tags,unit=unit,annual=is_flow)
                    if is_flow:
                        s[key]=merged(s.get(key,[]),recovered)
                        combined={tag:{'units':{'USD':list(facts.get(tag,{}).get('units',{}).get('USD',[]))+list(reported.get(tag,{}).get('units',{}).get('USD',[]))}} for tag in tags}
                        obs=ttm(combined,tags,s[key])
                    else:
                        annual_dates={r['end'] for r in s.get('revenue',[])}
                        s[key]=merged(s.get(key,[]),[r for r in recovered if r['end'] in annual_dates])
                        obs=latest(recovered)
                    if obs and (key not in current or obs['end']>=current[key]['end']): current[key]=obs
                if filing.get('cet1'): current['cet1']=filing['cet1']
        except Exception as exc: attempts.append(dict(source='Issuer filing recovery',error=str(exc)))
    # BAC's actual report labels this GAAP ratio nonperforming loans / total loans.
    # Nonaccrual is not generalized to NPL for other banks with different definitions.
    if cik==70858:
        row=latest(v.observations(facts,['FinancingReceivableExcludingAccruedInterestNonaccrualPercentPastDue'],unit='pure'))
        if row: current['npl']=dict(row,val=row['val']*100,unit='percent',source=url,basis='BAC issuer-reported nonperforming loan ratio')
    for revenue in s.get('revenue',[]):
        end=revenue['end'];gross=match(s.get('gross',[]),end);cost=match(s.get('cost',[]),end)
        if cost and not gross: s.setdefault('gross',[]).append(calc(end,revenue['val']-cost['val'],'Revenue - cost of goods/services',[revenue,cost]))
        if gross and not cost: s.setdefault('cost',[]).append(calc(end,revenue['val']-gross['val'],'Revenue - gross profit',[revenue,gross]))
    s['debt']=derive_debt(s)
    if 'debt' not in current:
        r=latest(derive_debt({k:[r] for k,r in current.items()}))
        if r: current['debt']=r
    shot=api.market_fields(ticker);sources+=shot['sources'];attempts+=shot['attempts']
    for rows in s.values(): rows.sort(key=lambda r:r['end'])
    return dict(symbol=ticker,cik=cik,shareClassCount=len(issuer.get('tickers',[])) or None,series=s,current=current,market=shot['fields'],marketFieldSources=shot.get('fieldSources',{}),sources=sources,attempts=attempts,retrievedAt=int(time.time()*1000),policies=POLICY)


def window(rows,n=5):
    rows=sorted(rows,key=lambda r:r['end'])[-n:]
    if len(rows)<n: return None
    if any(not 330<=(dt.date.fromisoformat(b['end'])-dt.date.fromisoformat(a['end'])).days<=380 for a,b in zip(rows,rows[1:])): return None
    if (dt.date.today()-dt.date.fromisoformat(rows[-1]['end'])).days>550: return None
    return rows


def slope(values):
    n=len(values);x=(n-1)/2;y=sum(values)/n
    return sum((i-x)*(v-y) for i,v in enumerate(values))/sum((i-x)**2 for i in range(n)) if n>1 else 0


def growth_test(rows,n):
    rows=window(rows,n)
    if not rows: return None
    values=[r['val'] for r in rows]
    return all(v>0 for v in values) and values[-1]>values[0] and slope(values)>0 and sum(b<a for a,b in zip(values,values[1:]))<=2


def ratio(series,a,b,method,scale=100):
    rows=[]
    for r in series.get(a,[]):
        if a == 'debt' and not r.get('completeDebt', True):
            continue
        other=match(series.get(b,[]),r['end'])
        if other and other['val']>0: rows.append(calc(r['end'],r['val']/other['val']*scale,method,[r,other]))
    return rows


def calculate(series):
    s={k:list(r) for k,r in series.items()}
    for key,a,b,scale in [('grossMargin','gross','revenue',100),('netMargin','income','revenue',100),('operatingMargin','operatingIncome','revenue',100),('roe','income','equity',100),('currentRatio','ca','cl',1),('gearing','debt','assets',100),('service','interest','ocf',100)]:
        s[key]=ratio(s,a,b,f'{a} / {b} x {scale}',scale)
    for r in s.get('ocf',[]):
        capex=match(s.get('capex',[]),r['end'])
        if capex: s.setdefault('fcf',[]).append(calc(r['end'],r['val']-capex['val'],'CFO - capital expenditure',[r,capex]))
    for ni in s.get('income',[]):
        end=ni['end'];op=match(s.get('operatingIncome',[]),end);tax=match(s.get('tax',[]),end);pretax=match(s.get('pretax',[]),end)
        if tax and not pretax: pretax=calc(end,ni['val']+tax['val'],'Net income + tax',[ni,tax])
        equity=match(s.get('equity',[]),end);debt=match(s.get('debt',[]),end);cash=match(s.get('cash',[]),end)
        if op and tax and pretax and equity and debt and cash and debt.get('completeDebt', True):
            capital=equity['val']+debt['val']-cash['val']
            if capital>0 and pretax['val']>0 and 0<=tax['val']/pretax['val']<=1:
                s.setdefault('roic',[]).append(calc(end,op['val']*(1-tax['val']/pretax['val'])/capital*100,'EBIT x (1-effective tax rate) / (equity + debt - cash) x 100',[op,tax,pretax,equity,debt,cash]))
        da=match(s.get('da',[]),end)
        if op and da and not match(s.get('ebitda',[]),end): s.setdefault('ebitda',[]).append(calc(end,op['val']+da['val'],'Operating EBIT + depreciation and amortization',[op,da]))
    s['debtEbitda']=ratio(s,'debt','ebitda','Debt / EBITDA',1)
    for revenue in s.get('revenue',[]):
        end=revenue['end'];previous=(dt.date.fromisoformat(end)-dt.timedelta(days=365)).isoformat();cost=match(s.get('cost',[]),end)
        balances=[];inputs=[revenue]+([cost] if cost else [])
        for k in ('ar','inventory','ap'):
            a=match(s.get(k,[]),end);b=match(s.get(k,[]),previous)
            if a and b: balances.append((a['val']+b['val'])/2);inputs += [a,b]
        if cost and cost['val']>0 and revenue['val']>0 and len(balances)==3:
            ar,inv,ap=balances
            s.setdefault('ccc',[]).append(calc(end,365*(ar/revenue['val']+(inv-ap)/cost['val']),'365 x (average AR / sales + (average inventory - average AP) / cost of sales)',inputs))
    for rows in s.values(): rows.sort(key=lambda r:r['end'])
    return s


def margin_test(rows):
    rows=window(rows)
    if not rows: return None
    values=[r['val'] for r in rows]
    return values[-1]>=sum(values)/len(values)-.5 and slope(values)>=-.1


def buyback_exception(series,current,years=5):
    """Source-backed exception, not an invented infinite numerical ROE."""
    equity=current.get('equity') or latest(series.get('equity',[]))
    if not equity or equity['val']>0: return None,[]
    end=equity['end'];inputs=[equity]
    if (dt.date.today()-dt.date.fromisoformat(end)).days>550: return None,inputs
    for key in ('treasury','retained'):
        obs=current.get(key)
        if not obs or not match([obs],end): obs=match(series.get(key,[]),end)
        if not obs: return None,inputs
        inputs.append(obs)
    equity,treasury,retained=inputs
    earnings=growth_test(series.get('income',[]),years);cash=growth_test(series.get('fcf',[]),years)
    if earnings is None or cash is None: return None,inputs
    income=current.get('income') or latest(series.get('income',[]))
    ok=treasury['val']>0 and equity['val']+treasury['val']>0 and retained['val']>0 and earnings and cash and income is not None and income['val']>0
    return ok,inputs+series.get('income',[])[-years:]+series.get('fcf',[])[-years:]


def evaluate(member,data,mode='moderate',include_peers=True):
    from vmi_sp500 import sector,MODES
    if mode not in MODES: raise ValueError('Invalid preset')
    s=calculate(data['series']);cur=calculate({k:[r] for k,r in data.get('current',{}).items()});flags=sector(member)
    checks=[];years=10 if mode=='conservative' else 5;cutoff=15 if mode=='conservative' else 12
    def add(key,label,ok,detail='',rows=None,na=False):
        checks.append(dict(key=key,label=label,status='N/A' if na else 'ACQUIRE' if ok is None else 'PASS' if ok else 'FAIL',detail=detail,observations=rows or []))
    def trend(key,label,n=5,stable=False):
        rows=window(s.get(key,[]),n)
        if not rows: add(key,label,None,f'Need {n} consecutive recent annual observations',s.get(key,[]));return None
        values=[r['val'] for r in rows];mean=sum(values)/n
        ok=(values[-1]>=mean-.5 and slope(values)>=-.1) if stable else growth_test(rows,n)
        add(key,label,ok,f'Latest {values[-1]:.3f}; mean {mean:.3f}; slope {slope(values):.3f}',rows)
        return ok
    add('industry','Industry suitability',not flags['excluded'],member['industry'])
    trend('revenue','Consistent revenue growth',years)
    ni=growth_test(s.get('income',[]),years);op=growth_test(s.get('operatingIncome',[]),years);substitute=ni is not True and op is True
    add('earnings','Consistent earnings / operating-income substitute',True if ni is True or op is True else False if ni is False and op is False else None,'Operating income substituted' if substitute else 'Net income with operating-income fallback',s.get('operatingIncome' if substitute else 'income',[])[-years:])
    if flags['bank']:
        add('ocf','CFO history',True,'Bank cash-flow exception',na=True);add('fcf','Free cash flow history',True,'Bank cash-flow exception',na=True)
    else:
        trend('ocf','Consistent operating cash flow',years)
        rows=window(s.get('fcf',[]));allow={'conservative':0,'moderate':1,'optimistic':2}[mode]
        vals=[r['val'] for r in rows] if rows else []
        ok=vals[-1]>0 and sum(v<=0 for v in vals)<=allow and not any(a<=0 and b<=0 for a,b in zip(vals,vals[1:])) if vals else None
        add('fcf','Positive free cash flow history',ok,f'At most {allow} isolated deficit years; latest positive',rows or s.get('fcf',[]))
    if flags['bank'] or flags['insurance']: add('grossMargin','Gross margin',True,'Not a comparable banking/insurance subtotal',na=True)
    else: trend('grossMargin','Gross margin stability and average',stable=True)
    net_ok=margin_test(s.get('netMargin',[]));operating_ok=margin_test(s.get('operatingMargin',[]))
    margin_key='netMargin' if net_ok is True else 'operatingMargin' if operating_ok is True else 'netMargin'
    margin_ok=True if net_ok is True or operating_ok is True else False if net_ok is False and operating_ok is False else None
    add('profitMargin','Profit margin stability and average',margin_ok,'Operating-margin substitute' if margin_key=='operatingMargin' else 'Net margin with independent operating-margin fallback',s.get(margin_key,[])[-5:])
    projected=v.number(data['market'].get('EPS next 5Y',data['market'].get('EPS Next 5Y')))
    add('growth','Positive projected earnings growth',projected>0 if projected is not None else None,'Next five-year analyst estimate: '+str(projected),[dict(source=u) for u in data.get('sources',[]) if 'finviz' in u])
    for key in ('roe','roic'):
        if key=='roe':
            equity=data.get('current',{}).get('equity') or latest(s.get('equity',[]))
            if equity and equity['val']<=0:
                allowed,inputs=buyback_exception(s,data.get('current',{}),years)
                add('roe','ROE / documented buyback exception',allowed,'Require positive retained earnings, treasury cost sufficient to restore positive equity, and increasing profits and FCF. Numeric ROE remains undefined; ROIC and debt still apply.',inputs)
                continue
        if key=='roic' and any(flags[k] for k in ('bank','insurance','utility')): add(key,'ROIC',True,'Course bank/insurance/utility exception; ROE still tested',na=True);continue
        rows=window(s.get(key,[]));current=latest(cur.get(key,[]))
        if rows:
            vals=[r['val'] for r in rows];now=current['val'] if current else vals[-1];mean=sum(vals)/5
            ok=(now>cutoff if key=='roe' and mode=='conservative' else now>=cutoff) and now>=mean and slope(vals)>=0
            add(key,key.upper()+' threshold, trend and five-year average',ok,f'Latest {now:.2f}%; annual mean {mean:.2f}%; cutoff {cutoff}%; '+('TTM / latest quarter' if current else 'latest annual basis'),rows+([current] if current else []))
        else: add(key,key.upper()+' threshold, trend and five-year average',None,'Five matched years required; negative equity is not treated as zero',s.get(key,[]))
    if flags['bank'] or flags['insurance']: add('receivables','Revenue vs receivables growth',True,'Financial-sector trade receivables not comparable',na=True)
    else:
        revenues=window(s.get('revenue',[]));ars=[match(s.get('ar',[]),r['end']) for r in revenues] if revenues else []
        if revenues and len(ars)==5 and all(ars) and revenues[0]['val']>0 and ars[0]['val']>0:
            rg=revenues[-1]['val']/revenues[0]['val']-1;ag=ars[-1]['val']/ars[0]['val']-1
            add('receivables','Revenue grows faster than receivables',rg>ag,f'{revenues[0]["end"]} to {revenues[-1]["end"]}: revenue {rg*100:.2f}%, receivables {ag*100:.2f}%',revenues+ars)
        else: add('receivables','Revenue grows faster than receivables',None,'Aligned positive opening receivable balance required',s.get('ar',[]))
    physical=any(x in member['industry'].lower() for x in ('retail','food','beverage','apparel','household','hardware','semiconductor','automobile','manufactur','equipment','pharmaceutical','tobacco','distributor','product','machinery','chemical'))
    if not physical: add('ccc','Cash conversion cycle',True,'Industry is not classified as a physical-goods business (explicit policy)',na=True)
    else:
        rows=window(s.get('ccc',[]));vals=[r['val'] for r in rows] if rows else []
        add('ccc','Cash conversion cycle consistent / declining',vals[-1]<=sum(vals)/5+10 and slope(vals)<=2 if vals else None,'App tolerances: mean +10 days, fitted slope <=2 days/year',rows or s.get('ccc',[]))
    def current(key):
        r=latest(cur.get(key,[])) or latest(s.get(key,[]))
        return r if r and (dt.date.today()-dt.date.fromisoformat(r['end'])).days<=550 else None
    if flags['bank']:
        r=current('cet1');add('debt','Bank CET1 >10%',r['val']>10 if r else None,'Common-equity Tier 1, never total Tier 1 substituted',[r] if r else [])
        r=current('npl');add('npl','Bank non-performing loans <5%',r['val']<5 if r else None,'Non-performing loans / total loans',[r] if r else [])
    elif flags['reit']:
        r=current('gearing');cut=40 if mode=='conservative' else 45
        add('debt','REIT gearing',r['val']<=cut if r and mode=='optimistic' else r['val']<cut if r else None,f'Total debt / assets; cutoff {cut}%', [r] if r else [])
    elif flags['insurance']: add('debt','Industrial debt ratios',True,'Insurance sector exemption from standard debt tests',na=True)
    else:
        cr=current('currentRatio');debt=current('debtEbitda');service=current('service')
        ca=data.get('current',{}).get('ca');cl=data.get('current',{}).get('cl');deferred=data.get('current',{}).get('deferred')
        upfront=bool(ca and cl and deferred and ca['end']==cl['end']==deferred['end'] and deferred['val']>0 and cl['val']>deferred['val'] and ca['val']/(cl['val']-deferred['val'])>1)
        outcomes=[True if upfront else (cr['val']>=1 if mode=='optimistic' else cr['val']>1) if cr else None,(debt['val']<3 if mode=='conservative' else debt['val']<=3) if debt else None,(0<=service['val']<30) if service else None]
        required=2 if mode=='optimistic' else 3
        ok=None if None in outcomes else sum(outcomes)>=required
        # A failed known ratio can reject a 3/3 policy without fetching an irrelevant missing ratio.
        if sum(x is False for x in outcomes)>3-required: ok=False
        add('debt','Conservative debt structure',ok,f'{required}/3 required: current {cr["val"] if cr else None}; debt/EBITDA {debt["val"] if debt else None}; interest/CFO % {service["val"] if service else None}'+('; documented deferred-revenue adjusted liquidity exception' if upfront else ''),[r for r in (cr,debt,service) if r])
    if include_peers: add('peers','Margins vs comparable businesses',None,'Last-stage closest-industry peer comparison')
    failures=[c['label'] for c in checks if c['status']=='FAIL'];pending=[c['key'] for c in checks if c['status']=='ACQUIRE']
    return dict(symbol=member['symbol'],preset=mode,status='REJECTED' if failures else 'ACQUIRING' if pending else 'QUALIFIED',checks=checks,failures=failures,pending=pending,moatGate=False,valuationGate=False,policies=POLICY)
