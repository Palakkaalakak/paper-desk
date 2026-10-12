"""Dated financial-table scraping; independent of SEC and Yahoo APIs."""
import datetime as dt
import json
import math
import re
import time
import urllib.error
import vmi_data as v
import vmi_trace as trace

LABELS={
 'Total Revenue':'revenue','Consolidated Net Income / (Loss)':'income',
 'Total Operating Income':'operatingIncome','Total Pre-Tax Income':'pretax',
 'Income Tax Expense':'tax','Total Gross Profit':'gross','Total Cost of Revenue':'cost',
 'Net Cash From Operating Activities':'ocf','Capital Expenditures (CapEx)':'capex',
 'Total Depreciation and Amortization (D&A)':'da',
 'Earnings before Interest, Taxes, Depreciation and Amortization (EBITDA)':'ebitda',
 'Cash & Equivalents':'cash','Short-Term Investments':'investments',
 'Total Common Equity':'equity','Total Assets':'assets','Total Current Assets':'ca',
 'Total Current Liabilities':'cl','Accounts Receivable':'ar','Inventories, net':'inventory',
 'Accounts Payable':'ap','Total Debt':'debt','Retained Earnings':'retained',
 'Current Deferred Revenue':'deferred','Weighted Average Diluted Shares Outstanding':'dilutedShares',
 'Interest Expense':'interest',
}


class StatementTable(v.Tables):
    def __init__(self):
        self.raw_rows=[];self.raw_row=[];self.raw_value=None
        super().__init__('financials-table')
    def handle_starttag(self,tag,attrs):
        super().handle_starttag(tag,attrs)
        if self.depth and tag in ('td','th'): self.raw_value=dict(attrs).get('data-value')
    def finish_cell(self):
        if self.cell is not None: self.raw_row.append(self.raw_value)
        super().finish_cell();self.raw_value=None
    def finish_row(self):
        super().finish_row()
        if len(self.rows)>len(self.raw_rows): self.raw_rows.append(self.raw_row)
        self.raw_row=[]


def decode(text,ticker,url):
    alias=v.symbol(ticker).replace('.','-');identities=[]
    for attrs,body in re.findall(r'<script([^>]*)>(.*?)</script>',text,re.S):
        if 'application/ld+json' not in attrs: continue
        try:
            obj=json.loads(body)
            for item in obj if isinstance(obj,list) else [obj]:
                if isinstance(item,dict) and item.get('tickerSymbol'): identities.append(item['tickerSymbol'])
        except ValueError: continue
    if not identities or any(str(x).upper().replace('.','-')!=alias for x in identities): raise ValueError('MarketBeat issuer identity mismatch')
    annual={};quarterly={};retrieved=int(time.time()*1000)
    for match in re.finditer(r'<table\b[^>]*class="financials-table[^>]*>.*?</table>',text,re.S):
        caption=text[max(0,match.start()-800):match.start()]
        units='All values are USD millions unless otherwise specified'
        if units not in caption and not ('calculated financial ratios and metrics' in caption and units in text): continue
        p=StatementTable();p.feed(match.group())
        header=next((r for r in p.rows if r and r[0]=='Metric'),None)
        dates=next((r for r in p.rows if r and r[0]=='Period end date'),None)
        if not header or not dates or len(header)!=len(dates): continue
        indexes=[i for i,x in enumerate(header) if re.fullmatch(r'\d{4}',x)]
        target=annual;basis='Annual financial statement'
        if not indexes:
            indexes=[i for i,x in enumerate(header) if re.fullmatch(r'Q[1-4] \d{4}',x)]
            target=quarterly;basis='Quarterly statement, not TTM or YTD'
        for row,raw in zip(p.rows,p.raw_rows):
            key=LABELS.get(row[0]) if row else None
            if not key: continue
            for i in indexes:
                if i>=len(row) or i>=len(raw) or raw[i] is None or row[i].strip() in ('','-','—','N/A'): continue
                try: end=dt.datetime.strptime(dates[i],'%m/%d/%Y').date();value=float(raw[i])
                except (ValueError,TypeError): continue
                if end>dt.date.today() or not math.isfinite(value): continue
                shown=v.number(row[i].replace('$','').strip());unit='shares' if key=='dilutedShares' else 'USD'
                expected=shown if unit=='shares' else shown*1e6 if shown is not None else None
                if expected is None or abs(expected-value)>max(abs(value)*.006,1 if unit=='shares' else 500001): continue
                if key=='capex': value=abs(value)
                obs=dict(end=end.isoformat(),val=value,unit=unit,source=url,tag=row[0],basis=basis,retrievedAt=retrieved)
                if key=='debt': obs['completeDebt']=True
                prior=next((r for r in target.get(key,[]) if r['end']==obs['end']),None)
                if prior and prior['val']!=value: raise ValueError('Conflicting scraped statement observations')
                if not prior: target.setdefault(key,[]).append(obs)
    if not annual: raise ValueError('MarketBeat dated USD financial table not established')
    for series in (annual,quarterly):
        for rows in series.values(): rows.sort(key=lambda r:r['end'])
    return dict(symbol=ticker,series=annual,quarterly=quarterly,source=url,retrievedAt=retrieved)


def statements(ticker,exchanges=()):
    alias=v.symbol(ticker).replace('.','-');attempts=[]
    preferred=[str(e).upper() for e in exchanges]
    venues=list(dict.fromkeys([e for e in preferred if e in ('NASDAQ','NYSE','NYSEAMERICAN')]+['NYSE','NASDAQ']))
    for venue in venues:
        url=f'https://www.marketbeat.com/stocks/{venue}/{alias}/financials/'
        try:
            trace.event('scrape',ticker+' / dated HTML statements',provider='www.marketbeat.com')
            row=decode(v.fetch(url,timeout=12,retries=0,retry_429=False),ticker,url)
            return dict(row,attempts=attempts)
        except Exception as exc:
            attempts.append(dict(source=url,error=str(exc)))
            if isinstance(exc,urllib.error.HTTPError) and exc.code in (403,429): break
    raise ValueError('MarketBeat statement recovery: '+json.dumps(attempts))
