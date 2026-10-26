"""Next earnings date for BPSX, from Finviz's public quote page (IB's own earnings feed, Wall Street Horizon,
needs a separate paid subscription). Finviz shows 'Mon DD' plus BMO/AMC without a year, so the date is the next
occurrence on or after today (a few days of grace for a report that just happened)."""
import datetime as dt
import re
import time

import vmi_sources as sources
from vmi_data import symbol as validate_symbol

_CACHE = {}
MONTHS = {m: i + 1 for i, m in enumerate(['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'])}


def parse(html, today=None):
    today = today or dt.date.today()
    m = re.search(r'>\s*Earnings\s*</td>\s*<td[^>]*>(?:\s*<[^>]+>)*\s*([A-Z][a-z]{2})\s+(\d{1,2})(?:\s*,?\s*(\d{4}))?\s*(BMO|AMC|DMH)?', html or '')
    if not m or m.group(1) not in MONTHS:
        return None
    month, day = MONTHS[m.group(1)], int(m.group(2))
    year = int(m.group(3)) if m.group(3) else today.year
    try:
        d = dt.date(year, month, day)
    except ValueError:
        return None
    if not m.group(3) and d < today - dt.timedelta(days=3):
        d = dt.date(year + 1, month, day)
    return dict(date=d.isoformat(), timing=m.group(4) or '')


def earnings(ticker):
    ticker = validate_symbol(ticker)
    hit = _CACHE.get(ticker)
    if hit and time.monotonic() - hit[0] < 6 * 3600:
        return hit[1]
    try:
        html = sources.finviz_fetch('https://finviz.com/quote.ashx?t=' + ticker, fast=True)
        found = parse(html)
        out = dict(symbol=ticker, status='found' if found else 'missing', source='Finviz (estimated; confirm with the company)', **(found or {}))
    except Exception as exc:
        out = dict(symbol=ticker, status='unavailable', reason=str(exc)[:160], source='Finviz')
    _CACHE[ticker] = (time.monotonic() if out['status'] == 'found' else time.monotonic() - 5 * 3600 - 3000, out)
    return out
