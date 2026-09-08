#!/usr/bin/env python3
"""Ask your own TWS/IB Gateway what it actually returns, and print it plainly.

    python check.py            # AAPL
    python check.py NVDA

Read-only, like the app: it never sends an order. Paste the output rather than
a whole TWS log - it says which step fails and why.
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# IBKR refuses a second connection under the same client id outright (error 326,
# "client id is already in use") rather than sharing it - and the running app
# (start.bat) is very likely already connected under tws.py's default id. Use a
# different one so this check can run side by side with the app instead of
# fighting it for the same slot.
os.environ.setdefault('PAPER_TWS_CLIENT_ID', '78')
import tws


def line(k, v):
    print('  %-22s %s' % (k, v))


def main():
    sym = (sys.argv[1] if len(sys.argv) > 1 else 'AAPL').upper()
    print('\nPaper Desk - TWS check for %s\n' % sym)

    print('connection')
    try:
        t0 = time.time()
        ib = tws.connect()
        line('connected', 'yes on port %s in %.1fs'
             % (tws._LAST_PORT['port'], time.time() - t0))
    except tws.TwsUnavailable as e:
        line('connected', 'NO')
        line('reason', e)
        print('\nNothing else can work until that is resolved.\n')
        return 1
    st = tws.status()
    line('accounts', ', '.join(st.get('accounts') or []) or 'none reported')
    line('login type', 'FUNDED - not paper' if st.get('live_login') else 'paper')
    line('read-only', 'yes, sealed' if st.get('sealed') else 'NOT SEALED')
    line('feed', 'delayed' if st.get('delayed') else 'live')
    line('farms', tws.farm_trouble() or 'all connected')

    print('\nquote')
    t0 = time.time()
    q = tws.quote(sym)
    dt = time.time() - t0
    if q.get('error'):
        line('result', 'FAILED after %.1fs' % dt)
        line('reason', q['error'])
    else:
        line('last', q.get('last'))
        line('bid / ask', '%s / %s' % (q.get('bid'), q.get('ask')))
        line('delayed', q.get('delayed'))
        line('took', '%.2fs cold' % dt)
        t0 = time.time(); tws.quote(sym); line('refresh', '%.3fs warm' % (time.time() - t0))

    print('\noption chain')
    m = tws._mod()
    und = m.Stock(sym, 'SMART', 'USD')
    try:
        tws._guard(ib.qualifyContracts, und)
    except Exception as e:
        line('resolve underlying', 'FAILED: %s' % e)
        return 1
    conid = getattr(und, 'conId', 0)
    line('underlying conId', conid or 'NOT RESOLVED')
    if not conid:
        return 1
    t0 = time.time()
    try:
        params = tws._guard(ib.reqSecDefOptParams, sym, '', 'STK', conid) or []
    except Exception as e:
        line('reqSecDefOptParams', 'FAILED: %s' % e)
        line('farms', tws.farm_trouble() or 'all connected')
        return 1
    line('reqSecDefOptParams', '%d rows in %.1fs' % (len(params), time.time() - t0))
    if not params:
        line('meaning', 'TWS returned no option parameters at all')
        line('farms', tws.farm_trouble() or 'all connected - so this is not an outage')
        line('recent TWS errors', '; '.join(tws.last_errors()) or 'none')
        return 1
    for p in params[:6]:
        line(getattr(p, 'exchange', '?'),
             '%d expiries, %d strikes, class %s'
             % (len(p.expirations or []), len(p.strikes or []),
                getattr(p, 'tradingClass', '')))
    if len(params) > 6:
        line('', '... and %d more exchanges' % (len(params) - 6))

    t0 = time.time()
    c = tws.chain(sym)
    dt = time.time() - t0
    print('')
    if c.get('error'):
        line('chain', 'FAILED after %.1fs' % dt)
        line('reason', c['error'])
        return 1
    line('chain', '%d calls, %d puts in %.1fs' % (len(c['calls']), len(c['puts']), dt))
    line('expiry used', c.get('expiry'))
    line('expiries listed', len(c.get('expirations') or []))
    line('still filling', c.get('partial') or 'no, complete')
    if c['calls']:
        k = c['calls'][len(c['calls']) // 2]
        line('sample strike', '%s  bid %s  ask %s  iv %s  delta %s'
             % (k['strike'], k['bid'], k['ask'], k['iv'], k['delta']))
    t0 = time.time(); tws.chain(sym); line('reload', '%.2fs warm' % (time.time() - t0))
    print('')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass
