"""Read-only automatic VMI discovery/financials. No broker or account mutations."""
import datetime as dt
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
import providers

_LOCK = threading.Lock()
_LAST = {}


def symbol(value):
    value = str(value).strip().upper()
    if not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,11}', value):
        raise ValueError('Invalid listing symbol')
    return value


def fetch(url):
    host = urllib.parse.urlsplit(url).hostname
    sec = host.endswith('.sec.gov')
    headers = {'User-Agent': os.environ.get('PAPER_SEC_USER_AGENT', 'PaperDesk/2.0 personal investment research') if sec else 'Mozilla/5.0', 'Accept': 'application/json,text/html'}
    for attempt in range(3):
        with _LOCK:
            delay = .2 if sec else 1.0
            time.sleep(max(0, delay - (time.monotonic() - _LAST.get(host, 0))))
            _LAST[host] = time.monotonic()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as response:
                body = response.read(25_000_001)
            if len(body) > 25_000_000:
                raise ValueError('Source response exceeds 25 MB limit')
            return body.decode('utf-8', 'replace')
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
            time.sleep(2 ** (attempt + 1))


class Tables(HTMLParser):
    """Supports screener HTML with omitted closing tr/td tags."""
    def __init__(self, marker):
        super().__init__()
        self.marker, self.depth, self.cell, self.row, self.rows = marker, 0, None, [], []

    def finish_cell(self):
        if self.cell is not None:
            self.row.append(' '.join(''.join(self.cell).split()))
            self.cell = None

    def finish_row(self):
        self.finish_cell()
        if self.row:
            self.rows.append(self.row)
            self.row = []

    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            if self.depth: self.depth += 1
            elif self.marker in dict(attrs).get('class', ''): self.depth = 1
        if not self.depth: return
        if tag == 'tr': self.finish_row()
        if tag in ('td', 'th'):
            self.finish_cell()
            self.cell = []

    def handle_data(self, text):
        if self.depth and self.cell is not None: self.cell.append(text)

    def handle_endtag(self, tag):
        if not self.depth: return
        if tag in ('td', 'th'): self.finish_cell()
        if tag == 'tr' or (tag == 'table' and self.depth == 1): self.finish_row()
        if tag == 'table': self.depth -= 1


def number(value):
    match = re.fullmatch(r'([-+]?\d+(?:\.\d+)?)([TBMK]?)', str(value).strip().replace(',', '').replace('%', ''))
    if not match: return None
    result = float(match[1]) * {'': 1, 'K': 1e3, 'M': 1e6, 'B': 1e9, 'T': 1e12}[match[2]]
    return result if math.isfinite(result) else None


def scan(mode='moderate', page=0):
    if mode not in ('conservative', 'moderate', 'optimistic') or type(page) is not int or not 0 <= page < 50:
        raise ValueError('Invalid scan preset/page')
    filters = ('fa_roe_o15' if mode == 'conservative' else 'fa_roe_o10') + ',fa_sales5years_pos,fa_eps5years_pos'
    url = 'https://finviz.com/screener.ashx?' + urllib.parse.urlencode({'v': 161, 'f': filters, 'ft': 4, 'r': 1+20*page})
    parser = Tables('screener_table'); parser.feed(fetch(url))
    header = next((r for r in parser.rows if 'Ticker' in r), None)
    if not header: raise ValueError('Screener response has no financial table header')
    rows = []
    for row in parser.rows:
        if row == header or len(row) != len(header): continue
        fields = dict(zip(header, row))
        try: ticker = symbol(fields.get('Ticker', ''))
        except ValueError: continue
        rows.append({'symbol': ticker, 'fields': fields})
    return {'rows': rows, 'page': page, 'nextPage': page+1 if len(rows)==20 and page<49 else 0,
            'source': url, 'filters': filters, 'valuationFiltered': False, 'retrievedAt': int(time.time()*1000)}


def observations(facts, tags, unit='USD', annual=False, cutoff=None):
    cutoff = cutoff or dt.date.today().isoformat()
    grouped = {}
    for rank, tag in enumerate(tags):
        for f in facts.get(tag, {}).get('units', {}).get(unit, []):
            if not f.get('end') or not f.get('filed') or f['end']>cutoff or f['filed']>cutoff: continue
            if f.get('form') not in ('10-K', '10-K/A', '10-Q', '10-Q/A', '20-F', '20-F/A'): continue
            if type(f.get('val')) not in (int, float) or not math.isfinite(f['val']): continue
            if annual:
                try: days = (dt.date.fromisoformat(f['end'])-dt.date.fromisoformat(f['start'])).days
                except (KeyError, ValueError): continue
                if not 330<=days<=380 or f['form'] not in ('10-K', '10-K/A', '20-F', '20-F/A'): continue
            elif f.get('start'): continue
            grouped.setdefault(f['end'], []).append(dict(f, tag=tag, rank=rank, unit=unit))
    out = []
    for end, rows in sorted(grouped.items()):
        filed = max(r['filed'] for r in rows)
        rows = [r for r in rows if r['filed']==filed]
        rank = min(r['rank'] for r in rows)
        rows = [r for r in rows if r['rank']==rank]
        if len({(r['val'], r.get('start')) for r in rows})!=1: continue
        out.append({k:v for k,v in rows[0].items() if k!='rank'})
    return out[-12:]


def secondary(ticker):
    keys = ['annualInterestExpense', 'annualEBITDA', 'annualTotalDebt', 'quarterlyTotalDebt',
            'annualOrdinarySharesNumber', 'quarterlyOrdinarySharesNumber', 'annualDepreciationAndAmortization',
            'annualCashCashEquivalentsAndShortTermInvestments', 'quarterlyCashCashEquivalentsAndShortTermInvestments']
    url = 'https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/'+ticker+'?'+urllib.parse.urlencode({'type': ','.join(keys), 'period1': 1451606400, 'period2': int(time.time())})
    result = {}
    for row in json.loads(fetch(url)).get('timeseries', {}).get('result') or []:
        if ticker not in row.get('meta', {}).get('symbol', []): raise ValueError('Statement ticker mismatch')
        for key in keys:
            for f in row.get(key, []):
                val, end = f.get('reportedValue', {}).get('raw'), f.get('asOfDate')
                if type(val) not in (int,float) or not math.isfinite(val) or not end or end>dt.date.today().isoformat(): continue
                if 'SharesNumber' not in key and f.get('currencyCode')!='USD': continue
                result.setdefault(key, []).append({'val':val, 'end':end, 'tag':key, 'source':url,
                    'unit':'shares' if 'SharesNumber' in key else 'USD', 'basis':'Provider statement; fiscal dates may be normalized to calendar month-end'})
    for rows in result.values(): rows.sort(key=lambda r:r['end'])
    return result, url


FLOW_TAGS = {
    'revenue':['RevenueFromContractWithCustomerExcludingAssessedTax','Revenues','SalesRevenueNet'],
    'income':['NetIncomeLoss','ProfitLoss'], 'ocf':['NetCashProvidedByUsedInOperatingActivities'],
    'capex':['PaymentsToAcquirePropertyPlantAndEquipment'],
    'interest':['InterestExpenseNonoperating','InterestExpenseNonOperating','InterestExpenseDebt','InterestAndDebtExpense','InterestExpense'],
    'tax':['IncomeTaxExpenseBenefit'], 'operatingIncome':['OperatingIncomeLoss'],
    'da':['DepreciationDepletionAndAmortization','DepreciationDepletionAndAmortizationPropertyPlantAndEquipment','DepreciationAmortizationAndAccretionNet']}


def fundamentals(ticker):
    ticker = symbol(ticker)
    sources, attempts, market, html = [], [], {}, ''
    market_url = 'https://finviz.com/quote.ashx?t='+ticker
    try:
        html = fetch(market_url)
        parser = Tables('snapshot-table2'); parser.feed(html)
        for row in parser.rows: market.update(zip(row[::2], row[1::2]))
        if not market: raise ValueError('Financial snapshot table absent')
        sources.append(market_url)
    except Exception as exc: attempts.append({'source':market_url,'error':str(exc),'fallback':'SEC statements'})
    cik = None
    try:
        mapping = providers._cached(('vmi-sec-map',),86400,lambda:json.loads(fetch('https://www.sec.gov/files/company_tickers.json')))
        cik = next((int(r['cik_str']) for r in mapping.values() if r['ticker'].replace('-','.')==ticker.replace('-','.')),None)
    except Exception as exc: attempts.append({'source':'SEC ticker map','error':str(exc),'fallback':'Issuer links / SEC entity search'})
    if not cik:
        ciks = set(re.findall(r'(?:CIK|cik)[=:/\s\"\x27]+(\d{1,10})',html))
        if len(ciks)==1: cik = int(ciks.pop())
    if not cik:
        url = 'https://efts.sec.gov/LATEST/search-index?'+urllib.parse.urlencode({'entityName':ticker})
        search = providers._cached(('vmi-sec-search',ticker),86400,lambda:json.loads(fetch(url)))
        names = [name for hit in search.get('hits',{}).get('hits',[]) for name in hit.get('_source',{}).get('display_names',[])]
        pattern = r'\('+re.escape(ticker)+r'\)\s+\(CIK (\d+)\)'
        ciks = {int(m[1]) for name in names if (m:=re.search(pattern,name))}
        if len(ciks)==1: cik = ciks.pop(); sources.append(url)
    if not cik: raise ValueError('No unique issuer in SEC ticker map, issuer links or SEC entity search')
    facts_url = 'https://data.sec.gov/api/xbrl/companyfacts/CIK%010d.json'%cik
    sub_url = 'https://data.sec.gov/submissions/CIK%010d.json'%cik
    raw = providers._cached(('vmi-sec-facts',cik),21600,lambda:json.loads(fetch(facts_url)))
    issuer = providers._cached(('vmi-sec-issuer',cik),21600,lambda:json.loads(fetch(sub_url)))
    if int(raw['cik'])!=cik or ticker.replace('-','.') not in [s.replace('-','.') for s in issuer.get('tickers',[])]: raise ValueError('SEC issuer/listing identity mismatch')
    sources.extend([facts_url,sub_url])
    facts = raw.get('facts',{}).get('us-gaap',{})
    flows = {k:observations(facts,tags,annual=True) for k,tags in FLOW_TAGS.items()}
    balances = {}
    for k,tags in {'cash':['CashCashEquivalentsAndShortTermInvestments','CashAndCashEquivalentsAtCarryingValue'], 'equity':['StockholdersEquity'], 'currentAssets':['AssetsCurrent'], 'currentLiabilities':['LiabilitiesCurrent'], 'shares':['CommonStockSharesOutstanding']}.items():
        rows = observations(facts,tags,unit='shares' if k=='shares' else 'USD')
        if rows: balances[k] = rows[-1]
    metrics, provenance = {}, {}
    for k,label in [('roe','ROE'),('roic','ROIC'),('current','Current Ratio'),('growth','EPS next 5Y')]:
        val = number(market.get(label))
        if val is not None: metrics[k]=val; provenance[k]={'source':market_url,'label':label,'basis':'Provider snapshot definition','retrievedAt':int(time.time()*1000)}
    ca,cl = balances.get('currentAssets'),balances.get('currentLiabilities')
    if 'current' not in metrics and ca and cl and ca['end']==cl['end'] and cl['val']>0:
        metrics['current']=ca['val']/cl['val']; provenance['current']={'inputs':[ca,cl],'method':'Current assets / current liabilities','period':ca['end']}
    alt = {}
    try:
        alt,url = providers._cached(('vmi-secondary',ticker),21600,lambda:secondary(ticker)); sources.append(url)
    except Exception as exc: attempts.append({'source':'Yahoo statements','error':str(exc)})
    def newest(keys):
        rows = [r for k in keys for r in alt.get(k,[])]
        return max(rows,key=lambda r:r['end']) if rows else None
    def matched(rows,end):
        return next((r for r in reversed(rows) if abs((dt.date.fromisoformat(r['end'])-dt.date.fromisoformat(end)).days)<=7),None)
    debt = newest(['annualTotalDebt','quarterlyTotalDebt'])
    if debt: balances['debt']=debt
    if not balances.get('shares'):
        shares = newest(['annualOrdinarySharesNumber','quarterlyOrdinarySharesNumber'])
        if shares: balances['shares']=shares
    cash = newest(['annualCashCashEquivalentsAndShortTermInvestments','quarterlyCashCashEquivalentsAndShortTermInvestments'])
    if cash and (not balances.get('cash') or (balances['cash']['tag']=='CashAndCashEquivalentsAtCarryingValue' and matched([cash],balances['cash']['end']))): balances['cash']=cash
    interest = flows['interest'] or alt.get('annualInterestExpense',[])
    if interest:
        intr=interest[-1]; ocf=matched(flows['ocf'],intr['end'])
        if ocf and ocf['val']>0 and intr['val']>=0:
            metrics['service']=intr['val']/ocf['val']*100; provenance['service']={'inputs':[intr,ocf],'period':intr['end'],'method':'Annual interest / same-period OCF × 100'}
    eb=newest(['annualEBITDA'])
    if debt and eb and eb['val']>0:
        metrics['debtEbitda']=debt['val']/eb['val']; provenance['debtEbitda']={'inputs':[debt,eb],'period':eb['end'],'method':'Latest total debt / annual provider EBITDA, NOT TTM'}
    if flows['income'] and 'roe' not in metrics:
        ni=flows['income'][-1]; equities=observations(facts,['StockholdersEquity']); end=matched(equities,ni['end']); start=matched(equities,ni['start'])
        if end and start and end['val']+start['val']>0:
            metrics['roe']=ni['val']/((end['val']+start['val'])/2)*100; provenance['roe']={'inputs':[ni,start,end],'period':ni['end'],'method':'Annual NI / average equity × 100'}
    ends=sorted(set.intersection(*(set(r['end'] for r in flows[k]) for k in ('revenue','income','ocf'))))
    metrics['years']=len(ends)
    stale=[k for k,p in provenance.items() if p.get('period') and (dt.date.today()-dt.date.fromisoformat(p['period'])).days>550]
    valuation=None
    if flows['ocf']:
        ocf=flows['ocf'][-1]; capex=matched(flows['capex'],ocf['end']); cash=balances.get('cash'); shares=balances.get('shares')
        domestic='10-K' in issuer.get('filings',{}).get('recent',{}).get('form',[])
        if domestic and capex and cash and debt and shares and shares['val']>0 and ocf['val']>capex['val']:
            valuation={'method':'FCF','base':ocf['val']-capex['val'],'cash':cash['val'],'debt':debt['val'],'shares':shares['val'],'fx':1,'adr':1,'forecast':metrics.get('growth'),'flowPeriod':ocf['end'],'inputs':[ocf,capex,cash,debt,shares],'basis':'Annual FCF, not TTM; latest available balances. Verify listing/share-class suitability.'}
    return {'symbol':ticker,'name':raw.get('entityName'),'cik':cik,'currency':'USD','metrics':metrics,'provenance':provenance,'flows':flows,'balances':balances,'secondary':alt,'market':market,'sources':sources,'attempts':attempts,'staleMetrics':stale,'valuationInputs':valuation,'retrievedAt':int(time.time()*1000),'qualitativeMoatVerified':False}
