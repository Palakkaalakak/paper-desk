"""Identity-checked actual issuer-filing recovery, retaining dates, units and contexts."""
import datetime as dt
from html.parser import HTMLParser
import io
import math
import re
import time
import urllib.error
import vmi_data as v

MIRRORS = {70858: 'https://investor.bankofamerica.com/regulatory-and-other-filings/select-sec-filings/content/'}
_SEC_UNTIL = 0


class InlineFacts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.contexts, self.units, self.identities = {}, {}, {}
        self.facts, self.stack, self.identity_facts = [], [], []
        self.context = self.unit = self.capture = None

    def handle_starttag(self, tag, attrs):
        a, local = dict(attrs), tag.split(':')[-1]
        if local == 'context': self.context = dict(id=a.get('id'), dimensions=False, members={}, unsupported=False)
        elif self.context is not None:
            if local in ('explicitmember','typedmember','segment','scenario'): self.context['dimensions'] = True
            if local in ('typedmember','scenario'): self.context['unsupported']=True
            if local == 'explicitmember':
                self.dimension=a.get('dimension','');self.capture=(local,[])
            if local in ('identifier','instant','startdate','enddate'): self.capture = (local, [])
        if local == 'unit': self.unit = dict(id=a.get('id'), measures=[])
        elif self.unit is not None and local == 'measure': self.capture = ('measure', [])
        if tag in ('ix:nonfraction','ix:nonnumeric'): self.stack.append(dict(attrs=a, text=[], kind=tag))

    def handle_data(self, text):
        if self.capture is not None: self.capture[1].append(text)
        for fact in self.stack: fact['text'].append(text)

    def handle_endtag(self, tag):
        local = tag.split(':')[-1]
        if self.capture is not None and self.capture[0] == local:
            value = ''.join(self.capture[1]).strip()
            if local == 'measure' and self.unit is not None: self.unit['measures'].append(value.split(':')[-1])
            elif self.context is not None:
                if local=='explicitmember':
                    if self.dimension in self.context['members']: self.context['unsupported']=True
                    self.context['members'][self.dimension]=value
                else: self.context[local] = value
            self.capture = None
        if local == 'context' and self.context is not None:
            self.contexts[self.context['id']] = self.context; self.context = None
        if local == 'unit' and self.unit is not None:
            self.units[self.unit['id']] = self.unit['measures']; self.unit = None
        if tag not in ('ix:nonfraction','ix:nonnumeric') or not self.stack: return
        fact = self.stack.pop()
        if fact['kind'] != tag: return
        a = fact['attrs']; text = ''.join(fact['text']).strip(); name = a.get('name','')
        if tag == 'ix:nonnumeric':
            if name.lower() == 'dei:documentperiodenddate':
                text = ' '.join(text.split())
                for fmt in ('%B %d, %Y','%b %d, %Y'):
                    try: text = dt.datetime.strptime(text, fmt).date().isoformat(); break
                    except ValueError: pass
            self.identities.setdefault(name.lower(),set()).add(text)
            self.identity_facts.append(dict(name=name.lower(), text=text, context=a.get('contextref')))
            return
        if a.get('xsi:nil') == 'true' or a.get('continuedat'): return
        fmt = a.get('format','').split(':')[-1]
        if fmt not in ('','num-dot-decimal','numdotdecimal','num-zero','zerodash'): return
        text = text.replace(',','').replace('\u00a0','').replace(' ','')
        if fmt in ('num-zero','zerodash') and text in ('-','—','–'): text = '0'
        if not re.fullmatch(r'[-+]?\d+(?:\.\d+)?',text): return
        try:
            scale = int(a.get('scale','0'))
            if not -12 <= scale <= 12: return
            value = float(text)*10**scale*(-1 if a.get('sign') == '-' else 1)
            if not math.isfinite(value): return
        except ValueError: return
        self.facts.append(dict(tag=name,val=value,context=a.get('contextref'),unitref=a.get('unitref'),decimals=a.get('decimals','0')))

    def validated(self, cik, report_date, source, filed, form):
        def scoped(name):
            rows = [r for r in self.identity_facts if r['name'] == name]
            contextual = [r for r in rows if r['context']]
            if contextual:
                values = set()
                for row in contextual:
                    context = self.contexts.get(row['context'], {})
                    identifier = context.get('identifier', '')
                    end = context.get('instant', context.get('enddate'))
                    if identifier.isdigit() and int(identifier) == cik and not context.get('dimensions') and end == report_date:
                        values.add(row['text'])
                return values
            # Legacy single-registrant documents only. Unscoped DEI cannot
            # establish which entity a combined-registrant document describes.
            ids = self.identities.get('dei:entitycentralindexkey', set())
            if not ids or any(not x.isdigit() or int(x) != cik for x in ids):
                return set()
            return {r['text'] for r in rows}
        ids = scoped('dei:entitycentralindexkey')
        if not ids or any(not x.isdigit() or int(x) != cik for x in ids):
            raise ValueError('Filing CIK identity mismatch')
        if scoped('dei:documentperiodenddate') != {report_date}:
            raise ValueError('Filing report date mismatch')
        grouped, result = {}, {}
        for f in self.facts:
            c = self.contexts.get(f['context'],{}); end = c.get('instant',c.get('enddate')); identifier = c.get('identifier',''); units = self.units.get(f['unitref'],[])
            if c.get('dimensions') or not identifier.isdigit() or int(identifier)!=cik or not end or end>report_date or len(units)!=1 or units[0] not in ('USD','pure','shares'): continue
            grouped.setdefault((f['tag'],end,c.get('startdate'),units[0]),[]).append(f)
        for (tag,end,start,unit), facts in grouped.items():
            def precision(f):
                try: return float(f['decimals'])
                except ValueError: return float('-inf')
            best = max(precision(f) for f in facts); chosen = [f for f in facts if precision(f)==best]
            if len({f['val'] for f in chosen})!=1: continue
            row = dict(end=end,val=chosen[0]['val'],source=source,filed=filed,form=form,tag=tag,unit=unit,basis='Consolidated issuer inline XBRL')
            if start: row['start']=start
            # Keep namespaces intact: a custom concept must not impersonate GAAP.
            result.setdefault(tag,{'units':{}})['units'].setdefault(unit,[]).append(row)
        return result


def capital_xbrl(parser, cik, report_date, source, filed, form):
    """Actual parent CET1; admit only explicit standardized/parent dimensions.

    Call after document identity validation. Ordinary financial extraction still
    rejects all dimensions; this narrow adapter does not relax that rule.
    """
    tags={'us-gaap:CommonEquityTierOneCapitalRatio'}
    if cik==19617: tags.add('jpm:CommonEquityTier1CapitaltoRiskWeightedAssets')
    allowed={
        'srt:ConsolidatedEntitiesAxis':{'srt:ParentCompanyMember'},
        'us-gaap:RiskWeightedAssetsCalculationMethodologyAxis':{'us-gaap:StandardizedApproachMember'},
    }
    if cik==19617: allowed['us-gaap:RiskWeightedAssetsCalculationMethodologyAxis'].add('jpm:BaselIIIStandardizedMember')
    rows=[]
    for fact in parser.facts:
        if fact['tag'] not in tags or parser.units.get(fact['unitref'])!=['pure']: continue
        context=parser.contexts.get(fact['context'],{});identifier=context.get('identifier','');members=context.get('members',{})
        if not identifier.isdigit() or int(identifier)!=cik or context.get('instant')!=report_date or context.get('unsupported'): continue
        if context.get('dimensions') and not members: continue
        if any(axis not in allowed or member not in allowed[axis] for axis,member in members.items()): continue
        if not 0<=fact['val']<=1: continue
        rows.append(dict(end=report_date,val=fact['val']*100,unit='percent',source=source,filed=filed,form=form,
                         tag=fact['tag'],context=fact['context'],dimensions=members,retrievedAt=int(time.time()*1000),
                         basis='Actual parent common-equity Tier 1 ratio; standardized approach where dimensioned'))
    return rows[0] if rows and len({r['val'] for r in rows})==1 else None


def citi_capital_table(text, report_date, source, filed, form):
    """Citigroup's actual standardized column, not required or Citibank ratios.

    Used only after CIK/DEI validation. Reconcile the displayed ratio to the
    same dated table's CET1 capital and standardized risk-weighted assets.
    """
    from html import unescape
    def plain(value):
        return ' '.join(unescape(re.sub(r'<[^>]*>', ' ', value)).split())
    found = []
    for table in re.finditer(r'<table\b.*?</table>', text, re.S | re.I):
        heading = plain(text[max(0, table.start()-2500):table.start()])
        if not re.search(r'The following tables present Citigroup[’\']s capital components and ratios as of [^.]+:\s*$', heading):
            continue
        parser = v.Tables(''); parser.feed(table[0])
        date = None; columns = None; capital = assets = None
        for raw in parser.rows:
            row = [cell.strip() for cell in raw if cell.strip()]
            if not row: continue
            dates = re.findall(r'\b[A-Z][a-z]+ \d{1,2}, \d{4}\b', ' '.join(row))
            if dates:
                date = dt.datetime.strptime(dates[0], '%B %d, %Y').date().isoformat()
                columns = None; capital = assets = None
                continue
            if row[0] == 'In millions of dollars, except ratios':
                columns = row[1:]
                continue
            if date != report_date or columns != ['Required Ratios', 'Standardized Approach', 'Required Ratios', 'Advanced Approaches']:
                continue
            values = [v.number(x) for x in row[1:] if x not in ('$', '%')]
            if row[0] == 'Common Equity Tier 1 Capital' and len(values) == 2:
                capital = values[0]
            elif row[0] == 'Total Risk-Weighted Assets' and len(values) == 2:
                assets = values[0]
            elif re.fullmatch(r'CET1 Capital ratio(?:\(\d+\))?', row[0]):
                if len(values) != 4 or row[2::2] != ['%']*4 or any(x is None or not 0 <= x <= 100 for x in values): continue
                actual = values[1]
                if capital is None or assets is None or capital <= 0 or assets <= 0 or abs(capital/assets*100-actual) > .0051: continue
                found.append(dict(end=date, val=actual, unit='percent', source=source,
                    filed=filed, form=form, retrievedAt=int(time.time()*1000), tag=row[0],
                    tableRow=row, column='Standardized Approach', columnHeader=columns,
                    issuerHeading=heading, reconciliation=dict(capitalMillions=capital, riskWeightedAssetsMillions=assets),
                    basis='Actual Citigroup parent standardized CET1; capital / risk-weighted assets reconciled'))
    return found[0] if found and len({r['val'] for r in found}) == 1 else None


def capital_table(text, issuer_name, report_date, source):
    """Strict dated parent-company table adapter; rejects subsidiary/minimum ratios."""
    wanted = re.sub(r'\W','',issuer_name).lower(); result=[]; seen=set()
    for occurrence in re.finditer(r'Common equity tier 1 capital ratio',text,re.I):
        begin=text.rfind('<table',0,occurrence.start()); end=text.find('</table>',occurrence.end())
        if begin<0 or end<0 or begin in seen: continue
        seen.add(begin); parser=v.Tables(''); parser.feed(text[begin:end+8])
        rows=[[c.strip() for c in row if c.strip()] for row in parser.rows]
        heading=' '.join(' '.join(row) for row in rows[:6])
        if wanted not in re.sub(r'\W','',heading).lower(): continue
        date=None; standard=False
        for row in rows:
            joined=' '.join(row)
            if 'StandardizedApproach' in joined or 'Standardized Approach' in joined: standard=True
            dates=re.findall(r'\b([A-Z][a-z]+ \d{1,2}, \d{4})\b',joined)
            if dates:
                try: date=dt.datetime.strptime(dates[0],'%B %d, %Y').date().isoformat()
                except ValueError: date=None
            if not row or row[0].lower()!='common equity tier 1 capital ratio': continue
            if date!=report_date or not standard or len(row)<3 or row[2]!='%': continue
            value=v.number(row[1])
            if value is not None and 0<=value<=100: result.append(dict(end=date,val=value,unit='percent',source=source,basis='Parent issuer Basel III standardized actual CET1 ratio',tag=row[0],tableRow=row))
    return result[0] if len({r['val'] for r in result})==1 else None


def jpm_pdf_capital(pages, report_date, source, filed):
    """Read the parent's filed quarterly highlights, not an earnings estimate.

    Only the observed JPM 10-Q layout is supported. Column labels, cover identity
    and reporting period are required; numbers are never inferred from prose.
    """
    period = dt.date.fromisoformat(report_date)
    if period.month not in (3, 6, 9) or period > dt.date.today():
        raise ValueError('Unsupported JPM quarterly report period')
    normalized = ['\n'.join(' '.join(line.split()) for line in text.splitlines()) for text in pages]
    cover = normalized[0] if normalized else ''
    date_label = f'{period:%B} {period.day}, {period.year}'
    if not re.search(r'FORM\s+10-Q\b', cover) or not re.search(r'JPMorgan Chase & Co\s*\.\s*\n\(Exact name of registrant', cover):
        raise ValueError('JPM filed report identity mismatch')
    if not re.search(r'For the quarterly period ended[^\n]*\n' + re.escape(date_label) + r'\b', cover):
        raise ValueError('JPM filed report date mismatch')
    if not re.search(r'Common stock\s+JPM\b', cover):
        raise ValueError('JPM report ticker mismatch')
    quarter = (period.month + 2) // 3
    expected = []
    for back in range(5):
        n = period.year * 4 + quarter - 1 - back
        expected.append(f'{n % 4 + 1}Q{n // 4 % 100:02d}')
    found = []
    for page_number, text in enumerate(normalized[1:], 2):
        if not text.startswith('JPMorgan Chase & Co.\nConsolidated financial highlights (unaudited)\n'):
            continue
        lines = text.splitlines()
        header = next((line for line in lines[:8] if re.findall(r'\b[1-4]Q\d{2}\b', line)), '')
        columns = re.findall(r'\b[1-4]Q\d{2}\b', header)
        if columns != expected:
            continue
        for line in lines:
            match = re.fullmatch(r'Common equity Tier 1 \([“\"]CET1[”\"]\) capital ratio [–-] Standardized\s*\([a-z]\)\s+(.+)', line)
            if not match or not re.fullmatch(r'\d+(?:\.\d+)?(?:\s+\d+(?:\.\d+)?){6}', match[1]):
                continue
            values = [float(value) for value in match[1].split()]
            if not all(0 <= value <= 100 for value in values):
                continue
            found.append(dict(end=report_date, val=values[0], unit='percent', source=source,
                filed=filed, form='10-Q', page=page_number, tableRow=line, column=columns[0],
                columnHeader=header, retrievedAt=int(time.time()*1000),
                tag='Common equity Tier 1 (CET1) capital ratio - Standardized',
                basis='Parent issuer filed consolidated financial highlights; actual standardized CET1 ratio'))
    if not found or len({row['val'] for row in found}) != 1:
        raise ValueError('No unambiguous dated parent JPM CET1 column')
    return found[0]


def issuer_pdf(member, period, filed, form):
    """Independent official issuer source, with metadata-driven URL and validation."""
    if member['cik'] != 19617 or form != '10-Q':
        return None
    date = dt.date.fromisoformat(period)
    quarter = (date.month + 2) // 3
    ordinal = {1: '1st', 2: '2nd', 3: '3rd'}.get(quarter)
    if not ordinal:
        return None
    url = ('https://www.jpmorganchase.com/content/dam/jpmc/jpmorgan-chase-and-co/'
           f'investor-relations/documents/quarterly-earnings/{date.year}/{ordinal}-quarter/corp-q{quarter}-{date.year}.pdf')
    from pypdf import PdfReader
    body = v.fetch(url, binary=True, timeout=12, retries=0)
    if not body.startswith(b'%PDF-'):
        raise ValueError('Issuer response is not a PDF')
    reader = PdfReader(io.BytesIO(body))
    # Highlights are at the front; avoid parsing hundreds of irrelevant pages.
    pages = [page.extract_text(extraction_mode='layout') for page in reader.pages[:5]]
    capital = jpm_pdf_capital(pages, period, url, filed)
    return dict(facts={}, cet1=capital, source=url, attempts=[])


def jpm_credit_ratio(text, report_date, source, filed, form):
    """Issuer-defined loan-only NPA components, after JPM CIK/DEI validation.

    Excludes derivatives, foreclosed assets and commitments. Credit cards stay
    in total loans; use reported aggregate nonaccrual totals, not missing=zero.
    """
    from html import unescape
    plain=' '.join(unescape(re.sub(r'<[^>]*>', ' ', text)).split())
    definition='Nonperforming assets include nonaccrual loans, nonperforming derivatives and certain assets acquired in loan satisfactions'
    if definition not in plain: return None
    portfolios={}
    for match in re.finditer(r'<table\b.*?</table>',text,re.S | re.I):
        if 'credit portfolio' not in match[0]: continue
        table=v.Tables('');table.feed(match[0])
        rows=[[c.strip() for c in row if c.strip()] for row in table.rows]
        rows=[r for r in rows if r]
        if not rows or rows[0] not in (['Consumer credit portfolio'],['Wholesale credit portfolio']): continue
        kind=rows[0][0];heading=' '.join(' '.join(r) for r in rows[:4])
        parsed=[]
        for value in re.findall(r'\b([A-Z][a-z]+ \d{1,2},\s*\d{4})\b',heading):
            value=re.sub(r',\s*',', ',value)
            for fmt in ('%B %d, %Y','%b %d, %Y'):
                try: parsed.append(dt.datetime.strptime(value,fmt).date().isoformat());break
                except ValueError: pass
        if '(in millions)' not in heading or len(parsed)!=4 or parsed[0]!=report_date or parsed[2]!=report_date or parsed[1]!=parsed[3] or parsed[1]>=report_date: continue
        def values(label):
            selected=[r for r in rows if r[0]==label]
            if len(selected)!=1: return None
            raw=[x for x in selected[0][1:] if x!='$']
            return [v.number(x) for x in raw] if len(raw)==4 else None
        if kind=='Consumer credit portfolio':
            if 'Credit exposure Nonaccrual loans' not in heading: continue
            consumer=values('Total consumer, excluding credit card loans')
            card=values('Total credit card loans');total=values('Total consumer credit portfolio')
            if not consumer or not card or not total or any(x is None for x in (consumer[0],card[0],total[2],consumer[2])): continue
            if total[2]!=consumer[2] or consumer[0]<0 or card[0]<0: continue
            loans=consumer[0]+card[0];nonperforming=total[2]
        else:
            if 'Credit exposure Nonperforming' not in heading: continue
            loan=values('Loans')
            if not loan or loan[0] is None or loan[2] is None: continue
            loans,nonperforming=loan[0],loan[2]
        if not 0<=nonperforming<=loans or loans<=0: continue
        observation=dict(loansMillions=loans,nonperformingLoansMillions=nonperforming,table=rows)
        if kind in portfolios and portfolios[kind]!=observation: return None
        portfolios[kind]=observation
    if len(portfolios)!=2: return None
    loans=sum(x['loansMillions'] for x in portfolios.values())
    npl=sum(x['nonperformingLoansMillions'] for x in portfolios.values())
    return dict(end=report_date,val=npl/loans*100,unit='percent',source=source,filed=filed,form=form,
        retrievedAt=int(time.time()*1000),tag='JPM filed consumer and wholesale loan-only credit-risk totals',
        method='100 x (consumer nonaccrual loans + wholesale nonperforming loans) / (consumer loans including credit cards + wholesale loans)',
        basis='Issuer-defined loan component of nonperforming assets; excludes derivatives, foreclosed assets and commitments',
        definition=definition,portfolios=portfolios)


def recover(member, issuer):
    global _SEC_UNTIL
    recent=issuer.get('filings',{}).get('recent',{}); today=dt.date.today().isoformat()
    filings=[i for i,form in enumerate(recent.get('form',[])) if form in ('10-K','10-Q') and recent['filingDate'][i]<=today]
    if not filings: raise ValueError('No recent issuer 10-K/10-Q metadata')
    i=filings[0]; cik=member['cik']; accession=recent['accessionNumber'][i]; document=recent['primaryDocument'][i]
    if not re.fullmatch(r'\d{10}-\d{2}-\d{6}',accession) or not re.fullmatch(r'[\w.-]+\.html?',document): raise ValueError('Invalid filing locator')
    urls=[MIRRORS[cik]+accession+'/'+document] if cik in MIRRORS else []
    urls.append(f'https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace("-", "")}/{document}')
    attempts=[]
    for url in urls:
        sec=url.startswith('https://www.sec.gov/')
        if sec and time.monotonic()<_SEC_UNTIL:
            attempts.append(dict(source=url,error='SEC archive cooldown; issuer mirrors remain available')); continue
        try:
            text=v.fetch(url); parser=InlineFacts(); parser.feed(text); period=recent['reportDate'][i]
            facts=parser.validated(cik,period,url,recent['filingDate'][i],recent['form'][i])
            capital=capital_xbrl(parser,cik,period,url,recent['filingDate'][i],recent['form'][i])
            if not capital and cik==70858: capital=capital_table(text,'Bank of America Corporation',period,url)
            if not capital and cik==831001: capital=citi_capital_table(text,period,url,recent['filingDate'][i],recent['form'][i])
            if not capital:
                try:
                    pdf = issuer_pdf(member, period, recent['filingDate'][i], recent['form'][i])
                    if pdf: capital = pdf['cet1']
                except Exception as exc:
                    attempts.append(dict(source='Official issuer filed PDF', error=str(exc)))
            npl=jpm_credit_ratio(text,period,url,recent['filingDate'][i],recent['form'][i]) if cik==19617 else None
            return dict(facts=facts,cet1=capital,npl=npl,source=url,attempts=attempts)
        except Exception as exc:
            if sec and isinstance(exc,urllib.error.HTTPError) and exc.code in (403,429): _SEC_UNTIL=time.monotonic()+900
            attempts.append(dict(source=url,error=str(exc)))
    try:
        pdf = issuer_pdf(member, recent['reportDate'][i], recent['filingDate'][i], recent['form'][i])
        if pdf:
            pdf['attempts'] = attempts + pdf['attempts']
            return pdf
    except Exception as exc:
        attempts.append(dict(source='Official issuer filed PDF', error=str(exc)))
    return dict(facts={},cet1=None,source=None,attempts=attempts)
