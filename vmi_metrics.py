"""VMI financial evidence, calculations and non-moat business gates.
Source thresholds: VMI_MASTER_DOCUMENT.md lines 2460-2503, 4241-4295, 4480-4559.
Qualitative trend tolerances and industry-peer selection are explicit app policies.
"""
import datetime as dt
import json
import math
import re
from statistics import median
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
 'operatingLease':['OperatingLeaseLiability'],
 'operatingLeaseCurrent':['OperatingLeaseLiabilityCurrent'],
 'operatingLeaseNoncurrent':['OperatingLeaseLiabilityNoncurrent'],
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
# Change whenever accounting or qualification semantics change. Browser records
# retain raw observations and original collection dates, not stale verdicts.
CALCULATION_REVISION = '2026-10-13-accounting-8'

POLICY={
 'history':'Five consecutive fiscal years; conservative ten for revenue, earnings and CFO. Generally upward trend using the median of all pairwise annual slopes (Theil-Sen), or a positive fitted slope with at most two declines. A temporary bad year or end-point dip alone is not a veto; required positive flows and complete dated history remain enforced.',
 'stability':'Full-window robust/fitted trends plus leave-one-year-out sensitivity with original year spacing. A negative fit dependent on one year is a review warning, not an automatic rejection. Persistent declines fail. All observations stay visible and means are context only. Margin tolerance -0.1 pp/year and statistical methods are app policies, not verbatim course thresholds.',
 'fcf':'Source allows 1-2 negative FCF years, not persistent deficits. Conservative: zero; moderate: one; optimistic: two, never consecutive and latest positive.',
 'returns':'Annual ROE = NI / year-end equity. Annual ROIC = EBIT x (1-effective tax rate) / (equity + debt - cash). TTM/quarter-end ratios use the same formulas; provider ROIC is not mixed into calculated history averages.',
 'debt':'Course permits 2/3 debt ratios (moderate and optimistic); conservative explicitly adds a 3/3 policy. All three inputs must be acquired; missing never counts as passing.',
 'ccc':'Physical-goods industries, including footwear, only. Latest <= five-year mean +10 days and trend <=2 days/year (explicit app tolerances). No absent balance is assumed zero.',
 'peers':'Three largest distinct S&P 500 issuers in the same GICS sub-industry. A transparent industry proxy, not a claim of identical business mix or a full direct-competitor map.',
 'receivables':'PP method: annual revenue and receivables on one shared dollar scale over five fiscal observations. Compare absolute changes; display percentage changes separately, never use relative percentage growth as a substitute veto. Source: course Microsoft/Amazon chart instructions and user-supplied PP screenshot.',
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


def annual_balances(facts, tags, fiscal_ends, unit='USD'):
    """Filter fiscal-year instants before the bounded observation selection."""
    annual = {tag: {'units': {unit: [
        r for r in facts.get(tag, {}).get('units', {}).get(unit, [])
        if r.get('form') in ('10-K', '10-K/A', '20-F', '20-F/A')
        and r.get('end') in fiscal_ends
    ]}} for tag in tags}
    return v.observations(annual, tags, unit=unit)


def issuer_trade_receivables(member, facts, fiscal_ends, source):
    """FIX's filed net billed + unbilled balances, not a provider subtotal.

    Allowances are already deducted; retainage is already in billed balances.
    Nontrade receivables and contract assets remain separate. This verified
    interpretation is issuer-specific, not a generic net/gross assumption.
    """
    if member['cik'] != 1035983:
        return None
    tags = ('BilledContractReceivables', 'UnbilledContractsReceivable')

    def combine(parts):
        rows = []
        for billed in parts[0]:
            unbilled = next((r for r in parts[1] if r['end'] == billed['end']), None)
            if unbilled is None or billed['val'] < 0 or unbilled['val'] < 0:
                continue
            inputs = [dict(r, source=source) for r in (billed, unbilled)]
            row = calc(billed['end'], sum(r['val'] for r in inputs),
                       'Reported billed + unbilled trade receivables; both net of their separately disclosed allowances', inputs)
            row.update(unit='USD', source=source, filed=max(r['filed'] for r in inputs),
                       scope='billed_and_unbilled_trade_receivables',
                       tag='BilledContractReceivables + UnbilledContractsReceivable')
            rows.append(row)
        return rows

    annual = combine([annual_balances(facts, [tag], fiscal_ends) for tag in tags])
    current = latest(combine([v.observations(facts, [tag]) for tag in tags]))
    return dict(annual=annual, current=current)


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


def statement_ttm(annual, quarterly):
    """Four standalone quarters, verified against a same-source annual total.

    Cumulative YTD, incomplete quarters and mixed sources cannot be summed.
    """
    annual = sorted(annual, key=lambda r: r['end'])
    quarterly = sorted(quarterly, key=lambda r: r['end'])
    if not annual: return None
    base = annual[-1]
    if not quarterly or quarterly[-1]['end'] <= base['end']:
        return dict(base, basis='Latest fiscal year (latest available trailing year)')
    def four(rows):
        rows = rows[-4:]
        if len(rows) != 4 or any(r.get('basis') != 'Quarterly statement, not TTM or YTD' or
                r.get('unit') != 'USD' or r.get('source') != base.get('source') or
                type(r.get('val')) not in (int, float) or not math.isfinite(r['val']) for r in rows): return None
        if any(not 70 <= (dt.date.fromisoformat(b['end'])-dt.date.fromisoformat(a['end'])).days <= 110
               for a,b in zip(rows, rows[1:])): return None
        return rows
    reconciliation = four([r for r in quarterly if r['end'] <= base['end']])
    trailing = four(quarterly)
    if not reconciliation or not trailing or reconciliation[-1]['end'] != base['end']: return None
    if abs(sum(r['val'] for r in reconciliation)-base['val']) > max(1, abs(base['val'])*.000001): return None
    result = calc(trailing[-1]['end'], sum(r['val'] for r in trailing),
                  'TTM = four consecutive standalone fiscal quarters; same-source annual reconciliation verified', trailing)
    result.update(unit='USD', periodType='TTM', annualReconciliation=dict(annual=base, quarters=reconciliation))
    return result


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
        elif non and longcur and short: inputs=[non,longcur,short];method='Noncurrent + current long-term carrying amounts + short-term borrowing'
        elif long and short: inputs=[long,short];method='Long-term debt inclusive of current maturities + short-term borrowing'
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


def reconcile_debt_scope(series):
    """Require independent dated debt + filed operating leases = provider total.

    No blanket lease subtraction or missing-as-zero. Preserve original totals
    and all reconciliation inputs. Separate finance-lease mismatches stay open.
    """
    result=[]
    components=derive_debt({k:r for k,r in series.items() if k!='debt'})
    for existing in series.get('debt',[]):
        end=existing['end']
        if existing.get('debtScope')=='reported_debt_excluding_operating_leases':
            result.append(existing);continue
        lease=match(series.get('operatingLease',[]),end)
        lc=match(series.get('operatingLeaseCurrent',[]),end)
        ln=match(series.get('operatingLeaseNoncurrent',[]),end)
        if lc and ln and lc['end']==ln['end'] and lc.get('source')==ln.get('source'):
            total=calc(lc['end'],lc['val']+ln['val'],'Operating lease current + noncurrent liabilities',[lc,ln])
            total.update(source=lc.get('source'),unit='USD')
            if lease and abs(lease['val']-total['val'])>1: lease=None
            elif not lease: lease=total
        providers=[r for key in ('debt','yahooDebt','scrapedDebt') if (r:=match(series.get(key,[]),end)) and r.get('completeDebt',True)]
        lower=[r for key in ('longDebt','debtNoncurrent','scrapedDebt','yahooDebt') if (r:=match(series.get(key,[]),end))]
        component=match(components,end)
        if component and component.get('completeDebt'): lower.append(component)
        reconciled=[]
        if lease and lease['val']>0:
            for high in providers:
                for low in lower:
                    if low['val']<0 or not high.get('source') or not low.get('source') or high['source']==low['source']: continue
                    if abs(high['val']-low['val']-lease['val'])>max(1,abs(high['val'])*1e-6): continue
                    row=calc(end,low['val'],'Provider total reconciled to independently reported debt + filed operating lease liabilities; operating leases excluded',[high,low,lease])
                    row.update(unit='USD',completeDebt=True,debtScope='reported_debt_excluding_operating_leases',
                               reconciliation=dict(totalIncludingOperatingLeases=high,reportedDebt=low,operatingLeases=lease))
                    reconciled.append(row)
        result.append(reconciled[0] if reconciled and len({r['val'] for r in reconciled})==1 else existing)
    return result


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


def normalize_evidence(member, data):
    """Quarantine known invalid legacy observations without losing the audit.

    Runs before supplemental acquisition/merge so an old date/filing rank cannot
    resurrect a provider subtotal or quarterly instant as an annual balance.
    """
    import copy
    result = copy.deepcopy(data)
    series = result.setdefault('series', {})
    current = result.setdefault('current', {})
    alternatives = result.setdefault('observationAlternatives', [])
    fiscal_rows = [r for key in FLOW for r in series.get(key, [])]
    def excluded(key, row, reason):
        alternatives.append(dict(metric=key, preferred=None, alternative=row, reason=reason))
    for key in BAL:
        kept = []
        for row in series.get(key, []):
            if match(fiscal_rows, row['end']): kept.append(row)
            else: excluded(key, row, 'Not a verified fiscal-year-end balance; excluded from annual history')
        if key in series: series[key] = kept
    if member['cik'] == 1035983:
        scope = 'billed_and_unbilled_trade_receivables'
        def valid(row):
            inputs = row.get('inputs', [])
            return (row.get('scope') == scope and len(inputs) == 2
                    and {r.get('tag') for r in inputs} == {'BilledContractReceivables', 'UnbilledContractsReceivable'}
                    and all(r.get('end') == row['end'] and type(r.get('val')) in (int,float)
                            and math.isfinite(r['val']) and r['val'] >= 0 for r in inputs)
                    and abs(sum(r['val'] for r in inputs)-row['val']) <= 1)
        kept = []
        for row in series.get('ar', []):
            if valid(row): kept.append(row)
            else: excluded('ar', row, 'FIX full billed + unbilled trade-receivable scope not established')
        series['ar'] = kept
        if 'ar' in current and not valid(current['ar']):
            excluded('ar', current.pop('ar'), 'FIX current trade-receivable scope not established')
    if series.get('debt'): series['debt'] = reconcile_debt_scope(series)
    if current.get('debt'): current['debt'] = reconcile_debt_scope({k:[r] for k,r in current.items()})[0]
    result['calculationRevision'] = CALCULATION_REVISION
    return result


def merge_evidence(prior, incoming):
    """Preserve observations on partial failure; re-evaluate combined inputs later."""
    import copy
    result = copy.deepcopy(incoming)
    alternatives = list(prior.get('observationAlternatives', []))
    def choose(old, new, key):
        if not old: return new
        if not new: return old
        old_rank = (old.get('end',''), old.get('filed',''), old.get('retrievedAt',0))
        new_rank = (new.get('end',''), new.get('filed',''), new.get('retrievedAt',0))
        chosen = new if new_rank >= old_rank else old
        if key == 'debt' and old.get('completeDebt', True) and not new.get('completeDebt', True): chosen = old
        if old != new:
            alternatives.append(dict(metric=key, preferred=chosen, alternative=old if chosen is new else new,
                                     reason='Supplemental acquisition; prior observation retained'))
        return chosen
    result['series'] = {}
    for key in set(prior.get('series', {})) | set(incoming.get('series', {})):
        rows = list(prior.get('series', {}).get(key, []))
        for row in incoming.get('series', {}).get(key, []):
            old = match(rows, row['end'])
            selected = choose(old, row, key)
            if old: rows.remove(old)
            rows.append(selected)
        result['series'][key] = sorted(rows, key=lambda r:r['end'])
    result['current'] = {key:choose(prior.get('current', {}).get(key), incoming.get('current', {}).get(key), key)
                         for key in set(prior.get('current', {})) | set(incoming.get('current', {}))}
    result['market'] = dict(prior.get('market', {}))
    result['marketFieldSources'] = dict(prior.get('marketFieldSources', {}))
    for key,value in incoming.get('market', {}).items():
        if value is not None and str(value).strip() not in ('','-','—','N/A'):
            result['market'][key] = value
            if key in incoming.get('marketFieldSources', {}): result['marketFieldSources'][key] = incoming['marketFieldSources'][key]
    result['sources'] = list(dict.fromkeys(prior.get('sources', []) + incoming.get('sources', [])))
    result['attempts'] = prior.get('attempts', []) + incoming.get('attempts', [])
    result['observationAlternatives'] = alternatives
    result['retrievedAt'] = min(prior['retrievedAt'], incoming['retrievedAt'])
    result['supplementedAt'] = incoming['retrievedAt']
    if not result.get('shareClassCount'): result['shareClassCount'] = prior.get('shareClassCount')
    result['completedSources'] = list(dict.fromkeys(prior.get('completedSources', []) + incoming.get('completedSources', [])))
    result['completedDebtSources'] = list(dict.fromkeys(prior.get('completedDebtSources', []) + incoming.get('completedDebtSources', [])))
    result['series']['debt'] = derive_debt(result['series'])
    return result


def evidence(member,mode=None,prior=None):
    import vmi_sp500 as api
    ticker,cik=member['symbol'],member['cik'];s={};current={};attempts=[];sources=[];issuer={};facts={}
    completed=[];known=set((prior or {}).get('completedSources',[]))
    # Supplement newly needed debt inputs once, then reuse successful sources.
    known.intersection_update((prior or {}).get('completedDebtSources',[]))
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
        fiscal_ends={r['end'] for key in flows for r in s.get(key,[])}
        for key,tags in BAL.items():
            unit='shares' if key=='shares' else 'USD'
            s[key]=annual_balances(facts,tags,fiscal_ends,unit=unit)
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
    if 'html' not in known and any(window([r for r in s.get(k,[]) if k!='debt' or r.get('completeDebt',True)],10) is None for k in required):
        try:
            from vmi_web import statements
            scraped=api.cached(('html-statements',ticker),21600,lambda:statements(ticker,issuer.get('exchanges',[])))
            s['scrapedDebt']=scraped['series'].get('debt',[])
            raw_debt=latest(scraped['quarterly'].get('debt',[])+s['scrapedDebt'])
            if raw_debt: current['scrapedDebt']=raw_debt
            for key,rows in scraped['series'].items():
                primary=s.get(key,[])
                if key=='debt': primary=[r for r in primary if r.get('completeDebt',True)]
                s[key]=merged(primary,rows)
                if key in BAL or key=='debt':
                    obs=latest(scraped['quarterly'].get(key,[])+rows)
                    if obs and (key not in current or obs['end']>current[key]['end']): current[key]=obs
            for key in FLOW:
                quarters = scraped['quarterly'].get(key, [])
                s['quarterly_'+key] = quarters
                obs = statement_ttm(scraped['series'].get(key, []), quarters)
                if obs and (key not in current or obs['end'] > current[key]['end']): current[key] = obs
            sources.append(scraped['source']);attempts+=scraped.get('attempts',[])
            completed.append('html')
            for alternative in scraped.get('alternatives',[]):
                attempts.append(dict(source=scraped['source'],observationAlternative=alternative))
        except Exception as exc: attempts.append(dict(source='MarketBeat HTML statements',error=str(exc)))
    try:
        alt={} if 'yahoo' in known else api.cached(('yahoo',ticker),21600,lambda:yahoo(ticker))
        if alt: completed.append('yahoo')
        s['yahooDebt']=alt.get('annualTotalDebt',[])
        raw_debt=latest(alt.get('quarterlyTotalDebt',[])+s['yahooDebt'])
        if raw_debt: current['yahooDebt']=raw_debt
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
                if filing.get('cet1'):
                    current['cet1']=filing['cet1']
                    if filing['cet1']['source'] not in sources: sources.append(filing['cet1']['source'])
                if filing.get('npl'):
                    current['npl']=filing['npl']
                    if filing['npl']['source'] not in sources: sources.append(filing['npl']['source'])
        except Exception as exc: attempts.append(dict(source='Issuer filing recovery',error=str(exc)))
    # BAC's actual report labels this GAAP ratio nonperforming loans / total loans.
    # Nonaccrual is not generalized to NPL for other banks with different definitions.
    if cik==70858:
        row=latest(v.observations(facts,['FinancingReceivableExcludingAccruedInterestNonaccrualPercentPastDue'],unit='pure'))
        if row: current['npl']=dict(row,val=row['val']*100,unit='percent',source=url,basis='BAC issuer-reported nonperforming loan ratio')
    scoped=issuer_trade_receivables(member,facts,{r['end'] for r in s.get('revenue',[])},url)
    if scoped is not None:
        for old in s.get('ar',[]):
            preferred=match(scoped['annual'],old['end'])
            if preferred is None or old['val']!=preferred['val']:
                attempts.append(dict(source=old.get('source'),observationAlternative=dict(
                    metric='ar',preferred=preferred,alternative=old,
                    reason='Issuer-filed full trade-receivable scope required; provider subtotal excluded')))
        # Never fill an incomplete full-scope history with the known ambiguous subtotal.
        s['ar']=scoped['annual']
        current.pop('ar',None)
        if scoped['current']: current['ar']=scoped['current']
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
    return normalize_evidence(member,dict(symbol=ticker,cik=cik,shareClassCount=len(issuer.get('tickers',[])) or None,series=s,current=current,market=shot['fields'],marketFieldSources=shot.get('fieldSources',{}),sources=sources,attempts=attempts,retrievedAt=int(time.time()*1000),policies=POLICY,completedSources=completed,completedDebtSources=completed))


def window(rows,n=5):
    rows=sorted(rows,key=lambda r:r['end'])[-n:]
    if len(rows)<n: return None
    if any(not 330<=(dt.date.fromisoformat(b['end'])-dt.date.fromisoformat(a['end'])).days<=380 for a,b in zip(rows,rows[1:])): return None
    if (dt.date.today()-dt.date.fromisoformat(rows[-1]['end'])).days>550: return None
    return rows


def slope(values):
    n=len(values);x=(n-1)/2;y=sum(values)/n
    return sum((i-x)*(v-y) for i,v in enumerate(values))/sum((i-x)**2 for i in range(n)) if n>1 else 0


def trend_summary(values, positions=None):
    """Keep the full history and preserve elapsed years in sensitivity fits."""
    positions=list(range(len(values))) if positions is None else positions
    pairs=[(b-a)/(positions[j]-positions[i]) for i,a in enumerate(values) for j,b in enumerate(values) if j>i]
    x=sum(positions)/len(positions);y=sum(values)/len(values)
    denominator=sum((p-x)**2 for p in positions)
    fitted=sum((p-x)*(v-y) for p,v in zip(positions,values))/denominator if denominator else 0
    return dict(linear=fitted,robust=median(pairs) if pairs else 0,
                declines=sum(b<a for a,b in zip(values,values[1:])))


def general_trend(values, floor=0, strict=False):
    stats=trend_summary(values)
    meets=lambda value:value>floor if strict else value>=floor
    # A positive outlier must not rescue a mostly deteriorating business.
    return meets(stats['robust']) or (stats['declines']<=2 and meets(stats['linear']))


def stability_summary(values, floor=0):
    """A one-year-sensitive fit is a warning, not persistent deterioration.

    All years remain in evidence/full-window statistics. Both fitted and robust
    slopes must stabilize in the sensitivity check, with original time spacing.
    Current profitability thresholds are still enforced separately.
    """
    full=trend_summary(values);sensitive=[]
    for excluded in range(len(values)):
        positions=[i for i in range(len(values)) if i!=excluded]
        stats=trend_summary([values[i] for i in positions],positions)
        if stats['linear']>=floor and stats['robust']>=floor: sensitive.append(excluded)
    rising=general_trend(values,floor=floor)
    return dict(**full,passes=rising or bool(sensitive),
                basis='Stable/rising full-window trend' if rising else 'Trend sensitive to a single year; review warning, not automatic rejection' if sensitive else 'Persistent declining trend across one-year sensitivity tests',
                sensitivityYears=sensitive)


def growth_test(rows,n):
    rows=window(rows,n)
    if not rows: return None
    values=[r['val'] for r in rows]
    return all(v>0 for v in values) and general_trend(values,strict=True)


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
            if capital>0 and pretax['val']>0:
                # A filed tax benefit is a real negative effective rate, not
                # missing acquisition. Retain the unadjusted formula and inputs.
                s.setdefault('roic',[]).append(calc(end,op['val']*(1-tax['val']/pretax['val'])/capital*100,'EBIT x (1-reported effective tax rate, including tax benefits) / (equity + debt - cash) x 100',[op,tax,pretax,equity,debt,cash]))
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
    return stability_summary(values,floor=-.1)['passes']


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
        ok=margin_test(rows) if stable else growth_test(rows,n)
        stats=trend_summary(values)
        add(key,label,ok,f'Latest {values[-1]:.3f}; mean {mean:.3f} (context, not veto); fitted slope {stats["linear"]:.3f}; robust slope {stats["robust"]:.3f}; declining years {stats["declines"]}',rows)
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
    else: trend('grossMargin','Gross margin general trend',stable=True)
    net_ok=margin_test(s.get('netMargin',[]));operating_ok=margin_test(s.get('operatingMargin',[]))
    margin_key='netMargin' if net_ok is True else 'operatingMargin' if operating_ok is True else 'netMargin'
    margin_ok=True if net_ok is True or operating_ok is True else False if net_ok is False and operating_ok is False else None
    add('profitMargin','Profit margin general trend',margin_ok,'Operating-margin substitute' if margin_key=='operatingMargin' else 'Net margin with independent operating-margin fallback',s.get(margin_key,[])[-5:])
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
            threshold=(now>cutoff if key=='roe' and mode=='conservative' else now>=cutoff)
            stats=stability_summary(vals);direction=stats['passes']
            ok=threshold and direction
            add(key,key.upper()+' threshold and general trend',ok,f'Latest {now:.2f}%; annual mean {mean:.2f}% (comparison only, not a qualification cutoff); cutoff {cutoff}%; threshold {"PASS" if threshold else "FAIL"}; general trend {"PASS" if direction else "FAIL"}; fitted slope {stats["linear"]:.3f}; robust slope {stats["robust"]:.3f}; {stats["basis"]}; '+('TTM / latest quarter' if current else 'latest annual basis'),rows+([current] if current else []))
        else: add(key,key.upper()+' threshold and general trend',None,'Five matched years required; negative equity is not treated as zero',s.get(key,[]))
    if flags['bank'] or flags['insurance']: add('receivables','Revenue vs receivables growth',True,'Financial-sector trade receivables not comparable',na=True)
    else:
        revenues=window(s.get('revenue',[]));ars=[match(s.get('ar',[]),r['end']) for r in revenues] if revenues else []
        if revenues and len(ars)==5 and all(ars) and all(r['val']>=0 for r in revenues+ars):
            revenue_change=revenues[-1]['val']-revenues[0]['val']
            ar_change=ars[-1]['val']-ars[0]['val']
            rg=(revenues[-1]['val']/revenues[0]['val']-1)*100 if revenues[0]['val']>0 else None
            ag=(ars[-1]['val']/ars[0]['val']-1)*100 if ars[0]['val']>0 else None
            add('receivables','Revenue vs receivables on shared dollar scale',revenue_change>ar_change,
                f'{revenues[0]["end"]} to {revenues[-1]["end"]}: revenue change USD {revenue_change:,.0f}; receivables change USD {ar_change:,.0f}. '
                'PP shared-scale comparison, not relative percentage growth. Source: VMI_MASTER_DOCUMENT.md 2181, 5228-5234, 5486.',revenues+ars)
            checks[-1]['comparison']=dict(basis='Shared USD scale',revenueChange=revenue_change,receivablesChange=ar_change,
                revenuePercentChange=rg,receivablesPercentChange=ag,percentagesAreDiagnosticOnly=True,
                source='VMI_MASTER_DOCUMENT.md: Microsoft and Amazon shared-scale chart instructions',
                referenceImage='https://www.genspark.ai/api/files/s/QBxRrn0D')
        else: add('receivables','Revenue vs receivables on shared dollar scale',None,'Five aligned nonnegative annual revenue and receivable balances required; missing is not zero',s.get('ar',[]))
    physical=any(x in member['industry'].lower() for x in ('retail','food','beverage','apparel','footwear','household','hardware','semiconductor','automobile','manufactur','equipment','pharmaceutical','tobacco','distributor','product','machinery','chemical'))
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
        required=3 if mode=='conservative' else 2
        ok=None if None in outcomes else sum(outcomes)>=required
        # A failed known ratio can reject a 3/3 policy without fetching an irrelevant missing ratio.
        if sum(x is False for x in outcomes)>3-required: ok=False
        add('debt','Conservative debt structure',ok,f'{required}/3 required ({"stricter app preset" if mode=="conservative" else "course allowance"}; all inputs required): current {cr["val"] if cr else None}; debt/EBITDA {debt["val"] if debt else None}; interest/CFO % {service["val"] if service else None}'+('; documented deferred-revenue adjusted liquidity exception' if upfront else ''),[r for r in (cr,debt,service) if r])
    if include_peers: add('peers','Margins vs comparable businesses',None,'Last-stage closest-industry peer comparison')
    failures=[c['label'] for c in checks if c['status']=='FAIL'];pending=[c['key'] for c in checks if c['status']=='ACQUIRE']
    return dict(symbol=member['symbol'],preset=mode,status='REJECTED' if failures else 'ACQUIRING' if pending else 'QUALIFIED',checks=checks,failures=failures,pending=pending,moatGate=False,valuationGate=False,policies=POLICY)
