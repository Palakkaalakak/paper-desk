"""
Fallback market data providers for when the IBKR gateway is not running.

Every one of these is delayed on its free tier and most do not publish a bid/ask,
so quotes are returned with `synthetic_spread: true` when the page has to invent
one. serve.py exposes them at /data/*; the page normalises them into the same
shape the gateway adapter produces.

    quote:  /data/quote?provider=finnhub&symbol=AAPL&key=...
    search: /data/search?provider=finnhub&q=apple&key=...
    chain:  /data/chain?provider=yahoo&symbol=AAPL[&expiry=1766006400]
"""
import json, urllib.request, urllib.parse, urllib.error, ssl, csv, io

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')
# tests point this at a local stub; unset in normal use
YAHOO = __import__('os').environ.get('PAPER_YAHOO_BASE', 'https://query1.finance.yahoo.com')
CTX = ssl.create_default_context()

ALPACA_DATA = __import__('os').environ.get('PAPER_ALPACA_DATA', 'https://data.alpaca.markets')
ALPACA_API  = __import__('os').environ.get('PAPER_ALPACA_API',  'https://api.alpaca.markets')
TRADIER_BASE = __import__('os').environ.get('PAPER_TRADIER_BASE', 'https://sandbox.tradier.com')
NASDAQ = __import__('os').environ.get('PAPER_NASDAQ_BASE', 'https://api.nasdaq.com')
CNBC = __import__('os').environ.get('PAPER_CNBC_BASE', 'https://quote.cnbc.com')

PROVIDERS = {
    'tws':        {'name': 'TWS / IB Gateway (read-only)', 'key': False,
                   'delay': 'real time, or delayed with no subscription',
                   'quotes': True, 'search': True, 'chain': True, 'bidask': True,
                   'limits': 'your own TWS session; no third party involved',
                   'signup': 'run TWS or IB Gateway and enable socket clients; pip install ib_async',
                   'official': True, 'local': True},
    'alpaca':     {'name': 'Alpaca (Basic)', 'key': True, 'delay': 'real time (IEX)',
                   'quotes': True, 'search': True, 'chain': True, 'bidask': True,
                   'limits': '200 calls a minute',
                   'signup': 'alpaca.markets - free account, then Home > API Keys. Paste as KEY:SECRET',
                   'official': True},
    'tradier':    {'name': 'Tradier (sandbox)', 'key': True, 'delay': '15 minutes',
                   'quotes': True, 'search': True, 'chain': True, 'bidask': True,
                   'limits': '120 calls a minute',
                   'signup': 'developer.tradier.com - free sandbox account, then copy the access token',
                   'official': True},
    'finnhub':    {'name': 'Finnhub',     'key': True,  'delay': 'about 20 minutes',
                   'quotes': True, 'search': True, 'chain': False, 'bidask': False,
                   'limits': '60 calls a minute'},
    'twelvedata': {'name': 'Twelve Data', 'key': True,  'delay': 'up to 4 hours',
                   'quotes': True, 'search': True, 'chain': False, 'bidask': False,
                   'limits': '800 calls a day'},
    'stooq':      {'name': 'Stooq',       'key': False, 'delay': 'end of day',
                   'quotes': True, 'search': False, 'chain': False, 'bidask': False,
                   'limits': 'unpublished, be gentle'},
    'nasdaq':     {'name': 'Nasdaq.com (unofficial)', 'key': False, 'delay': 'about 15 minutes',
                   'quotes': True, 'search': True, 'chain': True, 'bidask': True,
                   'limits': 'undocumented; blocks aggressive callers',
                   'signup': 'nothing to sign up for; undocumented site endpoints',
                   'official': False},
    'cnbc':       {'name': 'CNBC (unofficial)', 'key': False, 'delay': 'about 15 minutes',
                   'quotes': True, 'search': False, 'chain': False, 'bidask': True,
                   'limits': 'undocumented',
                   'signup': 'nothing to sign up for; undocumented site endpoints',
                   'official': False},
    'yahoo':      {'name': 'Yahoo Finance (unofficial)', 'key': False, 'delay': 'about 15 minutes',
                   'quotes': True, 'search': True, 'chain': True, 'bidask': True,
                   'limits': 'undocumented; unofficial endpoints that can change or block',
                   'signup': 'nothing to sign up for, but these are undocumented endpoints',
                   'official': False},
}
for _k in ('finnhub', 'twelvedata', 'stooq'):
    PROVIDERS[_k].setdefault('official', True)
PROVIDERS['finnhub']['signup'] = 'finnhub.io - free account, copy the API key'
PROVIDERS['twelvedata']['signup'] = 'twelvedata.com - free account, copy the API key'
PROVIDERS['stooq']['signup'] = 'nothing to sign up for'


def _split_key(key):
    """Alpaca needs KEY:SECRET; everything else is a single token."""
    if not key:
        return None, None
    if ':' in key:
        a, b = key.split(':', 1)
        return a.strip(), b.strip()
    return key.strip(), None


def _get_h(url, headers, timeout=20):
    req = urllib.request.Request(url, headers=dict(headers, **{'User-Agent': UA}))
    with urllib.request.urlopen(req, context=CTX, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8', 'replace'))


def _alpaca_h(key):
    k, s = _split_key(key)
    if not k or not s:
        raise ValueError('Alpaca needs both parts of the key, entered as KEY:SECRET')
    return {'APCA-API-KEY-ID': k, 'APCA-API-SECRET-KEY': s, 'accept': 'application/json'}


def _tradier_h(key):
    k, _ = _split_key(key)
    if not k:
        raise ValueError('Tradier needs an access token')
    return {'Authorization': 'Bearer %s' % k, 'Accept': 'application/json'}


_ASSETS = {'at': 0, 'rows': []}


def _alpaca_assets(key):
    import time as _t
    if _ASSETS['rows'] and _t.time() - _ASSETS['at'] < 21600:
        return _ASSETS['rows']
    rows = _get_h(ALPACA_API + '/v2/assets?status=active&asset_class=us_equity', _alpaca_h(key))
    _ASSETS['rows'] = [r for r in rows if r.get('tradable')]
    _ASSETS['at'] = _t.time()
    return _ASSETS['rows']


def _money(v):
    """Nasdaq formats prices as '$312.10' and percentages as '0.90%'."""
    if v is None:
        return None
    s = str(v).replace('$', '').replace(',', '').replace('%', '').strip()
    if s in ('', 'N/A', '--', 'UNCH'):
        return None
    return _f(s)


_MON = {'Jan': '01', 'Feb': '02', 'Mar': '03', 'Apr': '04', 'May': '05', 'Jun': '06',
        'Jul': '07', 'Aug': '08', 'Sep': '09', 'Oct': '10', 'Nov': '11', 'Dec': '12'}


def _nasdaq_date(s):
    """Nasdaq writes expiries as 'Sep 18, 2026' or '2026-09-18'."""
    s = (s or '').strip()
    if not s:
        return None
    import re as _re
    m = _re.match(r'^(\d{4})-(\d{2})-(\d{2})$', s)
    if m:
        return ''.join(m.groups())
    m = _re.match(r'^([A-Za-z]{3})\w*\s+(\d{1,2}),?\s+(\d{4})$', s)
    if m and m.group(1).title() in _MON:
        return '%s%s%02d' % (m.group(3), _MON[m.group(1).title()], int(m.group(2)))
    return None


def occ_parse(sym):
    """AAPL260918C00310000 -> (root, YYYYMMDD, 'C', 310.0)"""
    import re as _re
    m = _re.match(r'^([A-Z]+)(\d{6})([CP])(\d{8})$', str(sym or ''))
    if not m:
        return None
    root, ymd, right, strike = m.groups()
    return root, '20' + ymd, right, int(strike) / 1000.0


_YCOOKIE = {'jar': None, 'crumb': None, 'at': 0}


def _yahoo_session():
    """Yahoo's finance endpoints now want a cookie and a matching crumb. Fetch both
    once and reuse them; they last a good while."""
    import time as _t
    if _YCOOKIE['jar'] is not None and _t.time() - _YCOOKIE['at'] < 1800:
        return _YCOOKIE['jar'], _YCOOKIE['crumb']
    import http.cookiejar
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar),
        urllib.request.HTTPSHandler(context=CTX))
    hdrs = {'User-Agent': UA, 'Accept': '*/*'}
    crumb = None
    for seed in ('https://fc.yahoo.com', YAHOO.replace('query1', 'query2')):
        try:
            opener.open(urllib.request.Request(seed, headers=hdrs), timeout=10).read()
        except Exception:
            pass
        if jar:
            break
    try:
        req = urllib.request.Request(
            YAHOO.replace('query1', 'query2') + '/v1/test/getcrumb', headers=hdrs)
        crumb = opener.open(req, timeout=10).read().decode('utf-8', 'replace').strip()
        if len(crumb) > 40 or '<' in crumb:
            crumb = None
    except Exception:
        crumb = None
    _YCOOKIE['jar'], _YCOOKIE['crumb'], _YCOOKIE['at'] = jar, crumb, _t.time()
    return jar, crumb


def _get(url, timeout=15, yahoo=False):
    hdrs = {'User-Agent': UA, 'Accept': 'application/json,text/plain,*/*',
            'Accept-Language': 'en-US,en;q=0.9'}
    if yahoo:
        jar, crumb = _yahoo_session()
        if crumb:
            url += ('&' if '?' in url else '?') + 'crumb=' + urllib.parse.quote(crumb)
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar),
            urllib.request.HTTPSHandler(context=CTX))
        return opener.open(urllib.request.Request(url, headers=hdrs), timeout=timeout).read()
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, context=CTX, timeout=timeout) as r:
        return r.read()


def _json(url, timeout=15, yahoo=False):
    """Yahoo answers on two hosts and throttles both; try the other one and a fresh
    crumb before giving up."""
    if not yahoo:
        return json.loads(_get(url, timeout).decode('utf-8', 'replace'))
    hosts = [url]
    if 'query1' in url:
        hosts.append(url.replace('query1', 'query2'))
    elif 'query2' in url:
        hosts.append(url.replace('query2', 'query1'))
    last = None
    for attempt, u in enumerate(hosts):
        for retry in range(2):
            try:
                return json.loads(_get(u, timeout, True).decode('utf-8', 'replace'))
            except urllib.error.HTTPError as e:
                last = e
                if e.code in (401, 403):
                    _YCOOKIE['jar'] = None       # crumb went stale
                    continue
                if e.code == 429:
                    import time as _t
                    _t.sleep(0.6 * (retry + 1))
                    continue
                break
            except Exception as e:
                last = e
                break
    raise last if last else RuntimeError('yahoo request failed')


def _f(v):
    try:
        f = float(v)
        return f if f == f else None          # drop NaN
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- quotes
# ---------------------------------------------------------------- caching
_CACHE = {}


def _cached(key, ttl, fn):
    """Free tiers are rate limited; never ask twice for something this fresh."""
    import time as _t
    now = _t.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = fn()
    if not (isinstance(val, dict) and val.get('error')):
        _CACHE[key] = (now, val)
    return val


# ---------------------------------------------------------------- cascade
KEYLESS = ('yahoo', 'nasdaq', 'cnbc', 'stooq')


def _order(provider, key, need):
    """Providers to try, best first: the one asked for, then anything else that can
    actually run — keyless sources always, plus the one the key belongs to."""
    order = [provider]
    for p in ('tws', 'alpaca', 'tradier', 'yahoo', 'nasdaq', 'cnbc', 'finnhub', 'twelvedata', 'stooq'):
        if p in order:
            continue
        meta = PROVIDERS.get(p, {})
        if need == 'chain' and not meta.get('chain'):
            continue
        if meta.get('local') and p != provider:
            continue          # never dial TWS unless it was actually chosen
        if meta.get('key') and not (key and p == provider):
            continue          # no key for it, so it cannot be tried
        order.append(p)
    return order


def cascade(kind, provider, key, **kw):
    """Try each usable source in turn. Returns the payload with `served_by`, or a
    combined error explaining what every source said."""
    tried = []
    for p in _order(provider, key, kind):
        try:
            if kind == 'quote':
                out = quote(p, kw['symbol'], key if p == provider else None)
            elif kind == 'search':
                out = {'results': search(p, kw['q'], key if p == provider else None)}
            else:
                out = chain(p, kw['symbol'], kw.get('expiry'), key if p == provider else None)
        except Exception as e:
            tried.append('%s: %s' % (p, _friendly(e, PROVIDERS.get(p, {}).get('name', p))))
            continue
        if isinstance(out, dict) and out.get('error'):
            tried.append('%s: %s' % (p, out['error']))
            continue
        if isinstance(out, dict) and kind == 'search' and not out.get('results'):
            tried.append('%s: no matches' % p)
            continue
        if isinstance(out, dict) and kind == 'chain' and not (out.get('expirations')
                                                             or out.get('calls')):
            # An empty chain is a failure, not an answer. Accepting it let one
            # source's silence be reported to the user as "this symbol has no
            # options" - for symbols with thousands of them.
            tried.append('%s: returned an empty chain' % p)
            continue
        out['served_by'] = p
        if p != provider:
            out['fell_back'] = True
            # Say why the source they actually picked did not serve this. Without
            # it a fallback looks like the chosen provider's own answer, and a
            # real failure upstream reads as "this symbol has no options".
            if tried:
                out['fell_back_from'] = provider
                out['why_fell_back'] = tried[0]
        return out
    # Nobody could serve it. Lead with what the source they actually chose said.
    return {'error': ' | '.join(tried) or 'no usable data source', 'tried': tried,
            'asked': provider}


def _friendly(e, who):
    import urllib.error as _ue
    if isinstance(e, _ue.HTTPError):
        if e.code in (401, 403):
            return '%s rejected the request (HTTP %s) - check the API key' % (who, e.code)
        if e.code == 429:
            return '%s rate-limited you (HTTP 429) - wait a moment or use a different provider' % who
        return '%s returned HTTP %s' % (who, e.code)
    if isinstance(e, _ue.URLError):
        return 'could not reach %s (%s)' % (who, getattr(e, 'reason', e))
    return '%s: %s' % (who, e)


def quote(provider, symbol, key=None):
    s = urllib.parse.quote(symbol)
    if provider == 'tws':
        import tws as _tws
        return _tws.quote(symbol)
    if provider == 'alpaca':
        d = _get_h('%s/v2/stocks/snapshots?symbols=%s&feed=iex' % (ALPACA_DATA, s), _alpaca_h(key))
        snap = (d.get(symbol.upper()) or d.get(symbol) or
                (list(d.values())[0] if d and isinstance(list(d.values())[0], dict) else None))
        if not snap:
            return {'error': 'no data for %s' % symbol}
        q = snap.get('latestQuote') or {}
        t = snap.get('latestTrade') or {}
        db = snap.get('dailyBar') or {}
        pdb = snap.get('prevDailyBar') or {}
        last = _f(t.get('p')) or _f(db.get('c'))
        prev = _f(pdb.get('c'))
        return {'last': last, 'open': _f(db.get('o')), 'high': _f(db.get('h')), 'low': _f(db.get('l')),
                'prevClose': prev, 'bid': _f(q.get('bp')), 'ask': _f(q.get('ap')),
                'bidSize': _f(q.get('bs')), 'askSize': _f(q.get('as')),
                'change': (last - prev) if (last is not None and prev is not None) else None,
                'changePct': ((last - prev) / prev * 100) if (last and prev) else None,
                'volume': _f(db.get('v'))}
    if provider == 'tradier':
        d = _get_h('%s/v1/markets/quotes?symbols=%s' % (TRADIER_BASE, s), _tradier_h(key))
        qq = ((d.get('quotes') or {}).get('quote')) or None
        if isinstance(qq, list):
            qq = qq[0] if qq else None
        if not qq:
            return {'error': 'no data for %s' % symbol}
        return {'last': _f(qq.get('last')), 'open': _f(qq.get('open')), 'high': _f(qq.get('high')),
                'low': _f(qq.get('low')), 'prevClose': _f(qq.get('prevclose')),
                'bid': _f(qq.get('bid')), 'ask': _f(qq.get('ask')),
                'bidSize': _f(qq.get('bidsize')), 'askSize': _f(qq.get('asksize')),
                'change': _f(qq.get('change')), 'changePct': _f(qq.get('change_percentage')),
                'volume': _f(qq.get('volume'))}
    if provider == 'finnhub':
        d = _json('https://finnhub.io/api/v1/quote?symbol=%s&token=%s' % (s, urllib.parse.quote(key or '')))
        if _f(d.get('c')) in (None, 0):
            return {'error': 'no data for %s' % symbol}
        return {'last': _f(d.get('c')), 'open': _f(d.get('o')), 'high': _f(d.get('h')),
                'low': _f(d.get('l')), 'prevClose': _f(d.get('pc')), 'change': _f(d.get('d')),
                'changePct': _f(d.get('dp')), 'bid': None, 'ask': None, 'volume': None}
    if provider == 'twelvedata':
        d = _json('https://api.twelvedata.com/quote?symbol=%s&apikey=%s' % (s, urllib.parse.quote(key or '')))
        if d.get('status') == 'error' or d.get('code'):
            return {'error': d.get('message') or 'twelvedata error'}
        return {'last': _f(d.get('close')), 'open': _f(d.get('open')), 'high': _f(d.get('high')),
                'low': _f(d.get('low')), 'prevClose': _f(d.get('previous_close')),
                'change': _f(d.get('change')), 'changePct': _f(d.get('percent_change')),
                'bid': None, 'ask': None, 'volume': _f(d.get('volume'))}
    if provider == 'nasdaq':
        d = _json('%s/api/quote/%s/info?assetclass=stocks' % (NASDAQ, s))
        pd = ((d.get('data') or {}).get('primaryData')) or {}
        kd = ((d.get('data') or {}).get('keyStats')) or {}
        last = _money(pd.get('lastSalePrice'))
        if last is None:
            return {'error': 'Nasdaq returned no price for %s' % symbol}
        return {'last': last, 'open': _money((kd.get('OpenPrice') or {}).get('value')),
                'high': None, 'low': None,
                'prevClose': _money((kd.get('PreviousClose') or {}).get('value')),
                'bid': _money(pd.get('bidPrice')), 'ask': _money(pd.get('askPrice')),
                'bidSize': _f(pd.get('bidSize')), 'askSize': _f(pd.get('askSize')),
                'change': _money(pd.get('netChange')),
                'changePct': _money(str(pd.get('percentageChange') or '').replace('%', '')),
                'volume': _f(str((kd.get('Volume') or {}).get('value') or '').replace(',', ''))}
    if provider == 'cnbc':
        d = _json('%s/quote-html-webservice/restQuote/symbolType/symbol'
                  '?symbols=%s&requestMethod=itv&noform=1&partnerId=2&fund=1'
                  '&exthrs=1&output=json&events=1' % (CNBC, s))
        rows = (((d.get('FormattedQuoteResult') or {}).get('FormattedQuote')) or [])
        if isinstance(rows, dict):
            rows = [rows]
        if not rows:
            return {'error': 'CNBC returned no quote for %s' % symbol}
        r = rows[0]
        return {'last': _f(r.get('last')), 'open': _f(r.get('open')), 'high': _f(r.get('high')),
                'low': _f(r.get('low')), 'prevClose': _f(r.get('previous_day_closing')),
                'bid': _f(r.get('bid')), 'ask': _f(r.get('ask')),
                'bidSize': _f(r.get('bidsize')), 'askSize': _f(r.get('asksize')),
                'change': _f(r.get('change')), 'changePct': _f(r.get('change_pct')),
                'volume': _f(str(r.get('volume') or '').replace(',', ''))}
    if provider == 'stooq':
        raw = _get('https://stooq.com/q/l/?s=%s.us&f=sd2t2ohlcv&h&e=csv' % s.lower()).decode('utf-8', 'replace')
        rows = list(csv.DictReader(io.StringIO(raw)))
        if not rows or rows[0].get('Close') in (None, 'N/D'):
            return {'error': 'no data for %s' % symbol}
        r = rows[0]
        return {'last': _f(r.get('Close')), 'open': _f(r.get('Open')), 'high': _f(r.get('High')),
                'low': _f(r.get('Low')), 'prevClose': None, 'change': None, 'changePct': None,
                'bid': None, 'ask': None, 'volume': _f(r.get('Volume'))}
    if provider == 'yahoo':
        d = _json('%s/v8/finance/chart/%s?range=1d&interval=1m' % (YAHOO, s), yahoo=True)
        res = ((d.get('chart') or {}).get('result') or [None])[0]
        if not res:
            return {'error': 'no data for %s' % symbol}
        m = res.get('meta') or {}
        last = _f(m.get('regularMarketPrice'))
        prev = _f(m.get('chartPreviousClose')) or _f(m.get('previousClose'))
        return {'last': last, 'open': _f(m.get('regularMarketOpen')), 'high': _f(m.get('regularMarketDayHigh')),
                'low': _f(m.get('regularMarketDayLow')), 'prevClose': prev,
                'change': (last - prev) if (last is not None and prev is not None) else None,
                'changePct': ((last - prev) / prev * 100) if (last and prev) else None,
                'bid': _f(m.get('bid')), 'ask': _f(m.get('ask')), 'volume': _f(m.get('regularMarketVolume'))}
    return {'error': 'unknown provider %s' % provider}


# ---------------------------------------------------------------- search
def search(provider, q, key=None):
    qq = urllib.parse.quote(q)
    if provider == 'tws':
        import tws as _tws
        return _tws.search(q)
    if provider == 'alpaca':
        term = (q or '').upper()
        rows = _alpaca_assets(key)
        exact = [r for r in rows if r.get('symbol', '').upper() == term]
        starts = [r for r in rows if r.get('symbol', '').upper().startswith(term) and r not in exact]
        named = [r for r in rows if term in (r.get('name') or '').upper()
                 and r not in exact and r not in starts]
        return [{'symbol': r.get('symbol'), 'name': r.get('name'),
                 'exchange': r.get('exchange'), 'type': 'STK'}
                for r in (exact + starts + named)[:20]]
    if provider == 'tradier':
        d = _get_h('%s/v1/markets/lookup?q=%s' % (TRADIER_BASE, qq), _tradier_h(key))
        rows = ((d.get('securities') or {}).get('security')) or []
        if isinstance(rows, dict):
            rows = [rows]
        return [{'symbol': r.get('symbol'), 'name': r.get('description'),
                 'exchange': r.get('exchange'), 'type': (r.get('type') or 'STK').upper()}
                for r in rows[:20]]
    if provider == 'finnhub':
        d = _json('https://finnhub.io/api/v1/search?q=%s&token=%s' % (qq, urllib.parse.quote(key or '')))
        return [{'symbol': r.get('displaySymbol') or r.get('symbol'), 'name': r.get('description'),
                 'exchange': '', 'type': r.get('type') or 'STK'}
                for r in (d.get('result') or [])[:20]]
    if provider == 'twelvedata':
        d = _json('https://api.twelvedata.com/symbol_search?symbol=%s' % qq)
        return [{'symbol': r.get('symbol'), 'name': r.get('instrument_name'),
                 'exchange': r.get('exchange'), 'type': r.get('instrument_type') or 'STK'}
                for r in (d.get('data') or [])[:20]]
    if provider == 'yahoo':
        d = _json('%s/v1/finance/search?q=%s&quotesCount=20&newsCount=0' % (YAHOO, qq), yahoo=True)
        return [{'symbol': r.get('symbol'), 'name': r.get('shortname') or r.get('longname'),
                 'exchange': r.get('exchange'), 'type': (r.get('quoteType') or 'STK').upper()}
                for r in (d.get('quotes') or []) if r.get('symbol')][:20]
    if provider == 'nasdaq':
        d = _json('%s/api/autocomplete/slookup/10?search=%s' % (NASDAQ, qq))
        rows = d if isinstance(d, list) else (d.get('data') or [])
        out = []
        for r in rows:
            sym = (r.get('symbol') or '').strip()
            if sym:
                out.append({'symbol': sym, 'name': r.get('name') or sym,
                            'exchange': '', 'type': 'STK'})
        return out[:20]
    if provider == 'stooq':
        return [{'symbol': q.upper(), 'name': q.upper(), 'exchange': 'stooq', 'type': 'STK'}]
    return []


# ---------------------------------------------------------------- option chain
def chain(provider, symbol, expiry=None, key=None):
    if provider == 'tws':
        import tws as _tws
        return _tws.chain(symbol, expiry)
    if provider == 'alpaca':
        sym = symbol.upper()
        # expirations come from the contracts listing, which costs no market data
        cd = _get_h('%s/v2/options/contracts?underlying_symbols=%s&status=active&limit=10000'
                    % (ALPACA_API, urllib.parse.quote(sym)), _alpaca_h(key))
        cons = cd.get('option_contracts') or []
        exps = sorted({c.get('expiration_date') for c in cons if c.get('expiration_date')})
        if not exps:
            return {'error': 'no listed options for %s' % symbol}
        want = expiry or exps[0]
        url = ('%s/v1beta1/options/snapshots/%s?feed=indicative&limit=1000&expiration_date=%s'
               % (ALPACA_DATA, urllib.parse.quote(sym), urllib.parse.quote(str(want))))
        sd = _get_h(url, _alpaca_h(key))
        calls, puts = [], []
        for osym, s in (sd.get('snapshots') or {}).items():
            parsed = occ_parse(osym)
            if not parsed:
                continue
            _r, ymd, right, strike = parsed
            q = s.get('latestQuote') or {}
            t = s.get('latestTrade') or {}
            g = s.get('greeks') or {}
            leg = {'symbol': osym, 'strike': strike, 'bid': _f(q.get('bp')), 'ask': _f(q.get('ap')),
                   'last': _f(t.get('p')), 'volume': None, 'oi': None,
                   'iv': _f(s.get('impliedVolatility')), 'expiry': ymd,
                   'delta': _f(g.get('delta')), 'gamma': _f(g.get('gamma')),
                   'theta': _f(g.get('theta')), 'vega': _f(g.get('vega'))}
            (calls if right == 'C' else puts).append(leg)
        calls.sort(key=lambda x: x['strike']); puts.sort(key=lambda x: x['strike'])
        return {'expirations': exps, 'expiry': want, 'calls': calls, 'puts': puts, 'dateStyle': 'ymd'}
    if provider == 'tradier':
        sym = symbol.upper()
        ed = _get_h('%s/v1/markets/options/expirations?symbol=%s&includeAllRoots=true'
                    % (TRADIER_BASE, urllib.parse.quote(sym)), _tradier_h(key))
        exps = ((ed.get('expirations') or {}).get('date')) or []
        if isinstance(exps, str):
            exps = [exps]
        if not exps:
            return {'error': 'no listed options for %s' % symbol}
        want = expiry or exps[0]
        cd = _get_h('%s/v1/markets/options/chains?symbol=%s&expiration=%s&greeks=true'
                    % (TRADIER_BASE, urllib.parse.quote(sym), urllib.parse.quote(str(want))),
                    _tradier_h(key))
        rows = ((cd.get('options') or {}).get('option')) or []
        if isinstance(rows, dict):
            rows = [rows]
        calls, puts = [], []
        for o in rows:
            g = o.get('greeks') or {}
            leg = {'symbol': o.get('symbol'), 'strike': _f(o.get('strike')),
                   'bid': _f(o.get('bid')), 'ask': _f(o.get('ask')), 'last': _f(o.get('last')),
                   'volume': _f(o.get('volume')), 'oi': _f(o.get('open_interest')),
                   'iv': _f(g.get('mid_iv')), 'expiry': str(want).replace('-', ''),
                   'delta': _f(g.get('delta')), 'gamma': _f(g.get('gamma')),
                   'theta': _f(g.get('theta')), 'vega': _f(g.get('vega'))}
            (calls if (o.get('option_type') == 'call') else puts).append(leg)
        calls.sort(key=lambda x: x['strike'] or 0); puts.sort(key=lambda x: x['strike'] or 0)
        return {'expirations': exps, 'expiry': want, 'calls': calls, 'puts': puts, 'dateStyle': 'iso'}
    if provider == 'nasdaq':
        sym = symbol.upper()
        url = ('%s/api/quote/%s/option-chain?assetclass=stocks&limit=1000&fromdate=all'
               '&todate=undefined&excode=oprac&callput=callput&money=all&type=all'
               % (NASDAQ, urllib.parse.quote(sym)))
        d = _json(url)
        tbl = ((d.get('data') or {}).get('table')) or {}
        rows = tbl.get('rows') or []
        if not rows:
            return {'error': 'Nasdaq returned no option chain for %s' % symbol}
        exps, calls, puts = [], [], []
        for r in rows:
            ed = (r.get('expiryDate') or r.get('expiregroup') or '').strip()
            ymd = _nasdaq_date(ed)
            if not ymd:
                continue
            if ymd not in exps:
                exps.append(ymd)
            if expiry and ymd != str(expiry):
                continue
            k = _f(r.get('strike'))
            if k is None:
                continue
            if r.get('c_Bid') is not None or r.get('c_Ask') is not None:
                calls.append({'symbol': '%s|%s|C|%s' % (sym, ymd, k), 'strike': k,
                              'bid': _f(r.get('c_Bid')), 'ask': _f(r.get('c_Ask')),
                              'last': _f(r.get('c_Last')), 'volume': _f(r.get('c_Volume')),
                              'oi': _f(r.get('c_Openinterest')), 'iv': None, 'expiry': ymd})
            if r.get('p_Bid') is not None or r.get('p_Ask') is not None:
                puts.append({'symbol': '%s|%s|P|%s' % (sym, ymd, k), 'strike': k,
                             'bid': _f(r.get('p_Bid')), 'ask': _f(r.get('p_Ask')),
                             'last': _f(r.get('p_Last')), 'volume': _f(r.get('p_Volume')),
                             'oi': _f(r.get('p_Openinterest')), 'iv': None, 'expiry': ymd})
        exps.sort()
        calls.sort(key=lambda x: x['strike']); puts.sort(key=lambda x: x['strike'])
        return {'expirations': exps, 'expiry': expiry or (exps[0] if exps else None),
                'calls': calls, 'puts': puts}
    if provider != 'yahoo':
        return {'error': 'no free option chain from this provider'}
    s = urllib.parse.quote(symbol)
    url = '%s/v7/finance/options/%s' % (YAHOO, s)
    if expiry:
        url += '?date=%s' % urllib.parse.quote(str(expiry))
    try:
        d = _json(url, yahoo=True)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 429):
            _YCOOKIE['jar'] = None          # stale crumb: rebuild and try once more
            try:
                d = _json(url, yahoo=True)
            except urllib.error.HTTPError as e2:
                return {'error': ('Yahoo refused the option chain (HTTP %s). Its endpoints are '
                                  'unofficial and are throttled or blocked without warning. A free '
                                  'Tradier or Alpaca key gives you chains that keep working.' % e2.code)}
        else:
            return {'error': 'Yahoo returned HTTP %s for the option chain' % e.code}
    res = ((d.get('optionChain') or {}).get('result') or [None])[0]
    if not res:
        return {'error': 'no chain for %s' % symbol}
    exps = res.get('expirationDates') or []
    opts = (res.get('options') or [{}])[0]

    def leg(o):
        return {'symbol': o.get('contractSymbol'), 'strike': _f(o.get('strike')),
                'bid': _f(o.get('bid')), 'ask': _f(o.get('ask')), 'last': _f(o.get('lastPrice')),
                'volume': _f(o.get('volume')), 'oi': _f(o.get('openInterest')),
                'iv': _f(o.get('impliedVolatility')), 'expiry': o.get('expiration')}
    return {'expirations': exps, 'expiry': opts.get('expirationDate'),
            'calls': [leg(o) for o in (opts.get('calls') or [])],
            'puts': [leg(o) for o in (opts.get('puts') or [])]}


def selftest(provider, key=None, symbol='AAPL'):
    """Try each capability and report what actually works, with reasons."""
    out = {'provider': provider, 'name': PROVIDERS.get(provider, {}).get('name', provider)}
    try:
        q = quote(provider, symbol, key)
        if q.get('error'):
            out['quote'] = {'ok': False, 'why': q['error']}
        else:
            out['quote'] = {'ok': q.get('last') is not None,
                            'last': q.get('last'),
                            'bidask': q.get('bid') is not None and q.get('ask') is not None,
                            'why': '' if q.get('last') is not None else 'no price came back'}
    except Exception as e:
        out['quote'] = {'ok': False, 'why': _friendly(e, out['name'])}
    try:
        r = search(provider, symbol, key)
        out['search'] = {'ok': bool(r), 'n': len(r), 'why': '' if r else 'no matches'}
    except Exception as e:
        out['search'] = {'ok': False, 'why': _friendly(e, out['name'])}
    if PROVIDERS.get(provider, {}).get('chain'):
        try:
            c = chain(provider, symbol, None, key)
            if c.get('error'):
                out['chain'] = {'ok': False, 'why': c['error']}
            else:
                out['chain'] = {'ok': bool(c.get('calls')), 'expiries': len(c.get('expirations') or []),
                                'strikes': len(c.get('calls') or []),
                                'greeks': bool((c.get('calls') or [{}])[0].get('delta') is not None),
                                'why': '' if c.get('calls') else 'chain came back empty'}
        except Exception as e:
            out['chain'] = {'ok': False, 'why': _friendly(e, out['name'])}
    else:
        out['chain'] = {'ok': False, 'why': 'this provider has no option chain'}
    return out
