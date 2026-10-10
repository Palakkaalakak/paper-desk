"""Identity-checked actual issuer-filing recovery, retaining dates, units and contexts."""
import datetime as dt
from html.parser import HTMLParser
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
        self.facts, self.stack = [], []
        self.context = self.unit = self.capture = None

    def handle_starttag(self, tag, attrs):
        a, local = dict(attrs), tag.split(':')[-1]
        if local == 'context': self.context = dict(id=a.get('id'), dimensions=False)
        elif self.context is not None:
            if local in ('explicitmember','typedmember','segment','scenario'): self.context['dimensions'] = True
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
            elif self.context is not None: self.context[local] = value
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
        ids = self.identities.get('dei:entitycentralindexkey',set())
        if not ids or any(not x.isdigit() or int(x)!=cik for x in ids): raise ValueError('Filing CIK identity mismatch')
        if self.identities.get('dei:documentperiodenddate',set()) != {report_date}: raise ValueError('Filing report date mismatch')
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
            capital=capital_table(text,'Bank of America Corporation',period,url) if cik==70858 else None
            return dict(facts=facts,cet1=capital,source=url,attempts=attempts)
        except Exception as exc:
            if sec and isinstance(exc,urllib.error.HTTPError) and exc.code in (403,429): _SEC_UNTIL=time.monotonic()+900
            attempts.append(dict(source=url,error=str(exc)))
    return dict(facts={},cet1=None,source=None,attempts=attempts)
