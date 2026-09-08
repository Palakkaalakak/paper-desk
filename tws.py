"""
Trader Workstation API adapter — read only.

Talks to a running TWS or IB Gateway over its local socket, via ib_async. This gives
better data than the Client Portal Web API: real streaming ticks, IBKR's own greeks and
implied volatility per contract, complete option chains, and — importantly if your
gateway is on a paper login without subscriptions — a delayed-data mode that works
without any market data entitlement at all.

Read-only is enforced three ways, not one:
  1. the session connects with readonly=True, so ib_async itself refuses to transmit orders
  2. nothing here imports or constructs an order object; there is no code path to one
  3. every order-submitting method on the connected instance is replaced with a raiser,
     so even a bug cannot place, modify or cancel anything

Setup on the machine running TWS:
  TWS: Edit > Global Configuration > API > Settings > Enable ActiveX and Socket Clients.
       Leave "Read-Only API" ticked. IB Gateway accepts API connections by default.
  Ports: TWS live 7496, TWS paper 7497, Gateway live 4001, Gateway paper 4002.
  TWS or Gateway must stay running; there is no headless mode.
"""
import os, threading, math, asyncio, concurrent.futures, time, socket, logging, contextlib

# There is no budget on how long work may take. A chain of a thousand strikes is
# allowed to take as long as IBKR needs to send it. What is bounded is silence:
# if nothing at all arrives for this many seconds, the request has stalled and
# saying so beats waiting forever. Any byte of progress resets the clock.
TWS_IDLE = float(os.environ.get('PAPER_TWS_IDLE', '10'))
# Contract *definition* lookups are a different animal. IBKR paces them: the
# first answers at once, a similar repeat is deliberately held - their docs say
# about a minute, growing if you keep asking. During that hold TWS sends nothing
# at all, so a ten-second silence bound would kill a request that was always
# going to be answered. These get room for that hold.
TWS_DEF_IDLE = float(os.environ.get('PAPER_TWS_DEF_IDLE', '90'))
# We drive these requests ourselves and stop on silence, so ib_async's own
# wall-clock cap is switched off. It is a hard deadline on the whole call, which
# would cut off a big chain that is still arriving perfectly well.
TWS_REQUEST_TIMEOUT = 0
_TIMEOUT_ERRORS = (asyncio.TimeoutError, concurrent.futures.TimeoutError, TimeoutError)

# TWS reports failures through an event, and ib_async swallows them by default
# (RaiseRequestErrors is False), so a request that fails just returns nothing at
# all. Keep the recent ones so an empty result can say why it was empty.
_ERRORS = []
_ERRORS_MAX = 12
# Connection/data-farm chatter, not failures worth reporting.
_QUIET_CODES = {2104, 2106, 2107, 2108, 2119, 2158}

# TWS codes that mean "you must go and do something in the Gateway window".
# Nothing the adapter can retry will clear these, so say plainly what to do.
_BLOCKING = {
    10141: ('IB Gateway is refusing API connections until you accept its paper '
            'trading disclaimer. Switch to the IB Gateway/TWS window - the dialog '
            'may be behind it or minimised - and click "I understand and accept". '
            'It is asked once per install.'),
    10197: ('Another IBKR session is using your market data. Log out of TWS, the '
            'mobile app and Client Portal elsewhere, then retry - a live session '
            'and this one cannot hold the same data line at once.'),
    354: ('This login has no market data subscription for that instrument. '
          'Delayed data still works: set PAPER_TWS_DATA=delayed.'),
    502: ('Could not reach TWS/IB Gateway. Make sure it is running and logged in.'),
    504: ('Not connected to TWS/IB Gateway.'),
}
_BLOCKED = {'code': None, 'text': None}

# TWS keeps separate connections to IBKR's farms and reports them on this
# channel. Option chains come from the sec-def farm; while it is broken,
# reqSecDefOptParams answers with nothing at all and looks exactly like a symbol
# that has no options. Track the state so an empty chain can say which it was.
_FARM_DOWN = {
    1100: 'TWS has lost its connection to IBKR',
    2103: 'the market data farm connection is broken',
    2157: 'the sec-def farm connection is broken - this is where option chains come from',
    2105: 'the historical data farm connection is broken',
}
_FARM_UP = {1102, 2104, 2106, 2158, 2107, 2108}
_FARMS = {}


def farm_trouble():
    """What TWS currently says is disconnected, if anything."""
    return '; '.join(sorted(_FARMS.values())) or None


def blocking_problem():
    """The standing reason TWS will not serve this session, if there is one."""
    return _BLOCKED['text']


def _note_error(reqId, code, text, contract=None):
    if code in _FARM_DOWN:
        _FARMS[code] = _FARM_DOWN[code]
    elif code in _FARM_UP:
        _FARMS.clear()          # 1102 and the OK notices mean it is back
    if code in _QUIET_CODES:
        return
    if code in _BLOCKING:
        _BLOCKED['code'], _BLOCKED['text'] = code, _BLOCKING[code]
    what = ''
    if contract is not None:
        what = ' (%s)' % (getattr(contract, 'localSymbol', '')
                          or getattr(contract, 'symbol', '') or contract)
    _ERRORS.append('%s: %s%s' % (code, text, what))
    del _ERRORS[:-_ERRORS_MAX]


def last_errors(n=3):
    return list(_ERRORS[-n:])


def _why(fallback):
    """Explain an empty result using whatever TWS actually complained about."""
    errs = last_errors()
    return (fallback + ' TWS said - ' + '; '.join(errs)) if errs else fallback


def _await(ib, coro, idle=None, what='that request'):
    """Drive one ib_async request and stop only when TWS goes quiet.

    ib_async's RequestTimeout is a hard deadline on the whole call, so a request
    that is still streaming in gets cut off for being big rather than for being
    stuck. This waits on *progress* instead: every message from TWS resets the
    clock, and only an unbroken stretch of silence ends it. Slow is fine; silent
    is not.
    """
    idle = TWS_IDLE if idle is None else idle
    m = _mod()
    start_errs = len(_ERRORS)
    try:
        loop = m.util.getLoop()
        task = asyncio.ensure_future(coro, loop=loop)
    except Exception:
        # no loop to drive by hand - fall back to the plain blocking call
        return m.util.run(coro)
    quiet_since = time.monotonic()
    while not task.done():
        if ib.waitOnUpdate(timeout=0.2):
            quiet_since = time.monotonic()          # TWS is still talking
        elif time.monotonic() - quiet_since > idle:
            task.cancel()
            try:
                m.util.run(task)
            except BaseException:
                # CancelledError is a BaseException, not an Exception - catching
                # only Exception lets it escape and replaces the explanation
                # below with a bare traceback.
                pass
            # TWS sometimes did say something on the error channel during the
            # wait (a farm outage, a permission problem) even though it never
            # answered this specific request - that is far more useful than
            # the generic pacing guess below, so lead with it when present.
            fresh = _ERRORS[start_errs:]
            trouble = farm_trouble()
            if fresh:
                raise TwsUnavailable(
                    'TWS sent nothing for %gs on %s. TWS said - %s'
                    % (idle, what, '; '.join(fresh)))
            if trouble:
                raise TwsUnavailable(
                    'TWS sent nothing for %gs on %s because %s. It usually '
                    'reconnects on its own within a minute - try again then.'
                    % (idle, what, trouble))
            raise TwsUnavailable(
                'TWS sent nothing for %gs on %s, and reported no error at all - '
                'it went completely silent. It is reachable, so this is not a '
                'dropped connection. Most likely causes: IB Gateway has a dialog '
                'open waiting for a click (a disclaimer, a data-sharing prompt), '
                'or IBKR is holding this exact request under its pacing rule '
                '(repeated identical contract lookups get held, sometimes for '
                'several minutes, not just one) - if you just hit this a few '
                'times in a row, stop asking and give it a few minutes rather '
                'than retrying, since each retry restarts the hold.' % (idle, what))
    return task.result()


def _guard(fn, *a, **kw):
    """Run one blocking ib_async call, reporting a stalled one clearly."""
    try:
        return fn(*a, **kw)
    except _TIMEOUT_ERRORS:
        raise TwsUnavailable(
            'TWS went silent on that request. Check that TWS/IB Gateway is still '
            'running and not showing a dialog.')

TWS_HOST = os.environ.get('PAPER_TWS_HOST', '127.0.0.1')
TWS_PORTS = [int(p) for p in os.environ.get('PAPER_TWS_PORTS', '7496,7497,4001,4002').split(',') if p.strip()]
TWS_CLIENT_ID = int(os.environ.get('PAPER_TWS_CLIENT_ID', '77'))
TWS_MODULE = os.environ.get('PAPER_TWS_MODULE', 'ib_async')
TWS_DATA = os.environ.get('PAPER_TWS_DATA', 'auto')   # auto | live | delayed

# every ib_async method that can change anything at the broker
_WRITE_METHODS = (
    'placeOrder', 'cancelOrder', 'reqGlobalCancel', 'exerciseOptions',
    'placeOrderAsync', 'cancelOrderAsync', 'reqGlobalCancelAsync',
)


class TwsUnavailable(Exception):
    pass


_LOCK = threading.Lock()
_IB = None
_MODE = {'delayed': False, 'frozen': False}
# Remember the port that answered, and don't re-scan every port on every request
# after a failure - that turns one bad connect into dozens of socket attempts.
_LAST_PORT = {'port': None}
_COOLDOWN = float(os.environ.get('PAPER_TWS_RETRY_AFTER', '20'))
_FAIL = {'at': 0.0, 'err': None}


def _mod():
    try:
        return __import__(TWS_MODULE, fromlist=['*'])
    except ImportError as e:
        raise TwsUnavailable(
            'the %s package is not installed. Run: pip install ib_async' % TWS_MODULE) from e


def _refuse(*a, **k):
    raise PermissionError('Paper Desk holds a read-only TWS session; it never sends orders.')


def _seal(ib):
    """Belt and braces on top of readonly=True."""
    for name in _WRITE_METHODS:
        if hasattr(ib, name):
            try:
                setattr(ib, name, _refuse)
            except Exception:
                pass
    return ib


def is_sealed(ib):
    """True when no order-submitting method can be reached on this session."""
    for name in _WRITE_METHODS:
        fn = getattr(ib, name, None)
        if fn is not None and getattr(fn, '__name__', '') != '_refuse':
            return False
    return True


def _port_open(host, port, timeout=0.4):
    """Is anything listening there? A refused connection is the normal answer for
    three of the four ports we know about, so ask cheaply and quietly rather than
    letting ib_async log 'API connection failed' for each one."""
    try:
        with contextlib.closing(socket.create_connection((host, port), timeout)):
            return True
    except OSError:
        return False


@contextlib.contextmanager
def _quiet_client_log():
    """ib_async logs a failed handshake at ERROR straight to the console. We
    report failures ourselves, with more context, so its copy is just noise."""
    saved = []
    for name in ('ib_async.client', 'ib_async.Client', 'ib_insync.client'):
        lg = logging.getLogger(name)
        saved.append((lg, lg.level))
        lg.setLevel(logging.CRITICAL)
    try:
        yield
    finally:
        for lg, level in saved:
            lg.setLevel(level)


def connect():
    global _IB
    with _LOCK:
        if _IB is not None and getattr(_IB, 'isConnected', lambda: False)():
            return _IB
        # A failed connect stays failed for a moment. Without this, every quote on
        # the page re-scans all four ports and TWS sees a burst of dead sockets.
        if _FAIL['err'] and (time.monotonic() - _FAIL['at']) < _COOLDOWN:
            raise _FAIL['err']
        m = _mod()
        ib = m.IB()
        # Listen before dialling, not after: the failures that matter most - the
        # paper-trading disclaimer, a competing session - arrive during the
        # handshake, and TWS hangs up straight after sending them.
        try:
            ib.errorEvent += _note_error
        except Exception:
            pass
        last = None
        ports = list(TWS_PORTS)
        if _LAST_PORT['port'] in ports:            # the one that worked before, first
            ports.remove(_LAST_PORT['port'])
            ports.insert(0, _LAST_PORT['port'])
        listening = [p for p in ports if _port_open(TWS_HOST, p)]
        if not listening:
            err = TwsUnavailable(
                'Nothing is listening on %s port%s %s. Start TWS or IB Gateway, log '
                'in, and check its API socket port under Global Configuration > API '
                '> Settings.'
                % (TWS_HOST, '' if len(ports) == 1 else 's',
                   ', '.join(str(p) for p in ports)))
            _FAIL['at'], _FAIL['err'] = time.monotonic(), err
            raise err
        for port in listening:
            try:
                # Ask for nothing but market data. ib_async's default startup
                # pulls positions, account values and executions for every
                # account on the login - real balances this simulator has no use
                # for, since it keeps its own book. StartupFetchNONE skips all of
                # it, so a funded login never streams its holdings in here.
                kw = {}
                fetch_none = getattr(m, 'StartupFetchNONE', None)
                if fetch_none is not None:
                    kw['fetchFields'] = fetch_none
                with _quiet_client_log():
                    ib.connect(TWS_HOST, port, clientId=TWS_CLIENT_ID, timeout=6,
                               readonly=True, **kw)
                if ib.isConnected():
                    # ib_async waits forever by default (RequestTimeout=0) when
                    # TWS never sends a completion signal. This is not a budget on
                    # how long work may take - it is how long total silence is
                    # tolerated before the request is called stalled.
                    ib.RequestTimeout = TWS_REQUEST_TIMEOUT
                    # Warm tickers belong to the session that created them. A new
                    # socket means every one of them is dead, so forget them
                    # rather than serve prices from a connection that is gone.
                    _SUBS.clear()
                    _STK.clear()
                    # StartupFetchNONE is not enough on its own: ib_async issues
                    # reqPositions unconditionally and never consults its own
                    # POSITIONS flag, so a funded login streams every holding in
                    # regardless. Cancel that subscription the moment we are up,
                    # and drop whatever already arrived.
                    try:
                        ib.client.cancelPositions()
                    except Exception:
                        pass
                    try:
                        ib.reqAccountUpdates(False, '')
                    except Exception:
                        pass
                    for attr in ('positions', 'accountValues', 'portfolio'):
                        try:
                            store = getattr(getattr(ib, 'wrapper', None), attr, None)
                            if hasattr(store, 'clear'):
                                store.clear()
                        except Exception:
                            pass
                    _IB = _seal(ib)
                    _start_pump()
                    _LAST_PORT['port'] = port
                    _FAIL['err'] = None
                    _set_data_mode(_IB)
                    return _IB
            except Exception as e:
                last = e
                continue
        # If TWS told us why it hung up - the paper disclaimer, most often - that
        # is the answer, not "nothing answered on these ports".
        blocked = blocking_problem()
        err = TwsUnavailable(blocked) if blocked else TwsUnavailable(
            'Something is listening on %s (port%s %s) but the API handshake did not '
            'complete.%s'
            % (TWS_HOST, '' if len(listening) == 1 else 's',
               ', '.join(str(p) for p in listening),
               (' Last error: %s' % last) if last else ''))
        _FAIL['at'], _FAIL['err'] = time.monotonic(), err
        raise err


def _set_data_mode(ib, delayed=None):
    """1 live, 2 frozen, 3 delayed, 4 delayed-frozen. Delayed needs no subscription,
    which is what makes this work on a paper login."""
    if delayed is None:
        delayed = (TWS_DATA == 'delayed')
    try:
        ib.reqMarketDataType(3 if delayed else 1)
        _MODE['delayed'] = bool(delayed)
        _MODE['frozen'] = False
    except Exception:
        pass


def _set_frozen_mode(ib):
    """2 frozen (needs a subscription, gives the last session's real trades) or
    4 delayed-frozen (needs none). While the market is closed, live and delayed
    both go quiet - nothing is trading, so there is nothing to stream - and that
    correctly looks empty, not broken. Frozen is IBKR's actual answer for 'what
    did this last trade at': it is what a closed market looks like, not a
    fallback for a failure."""
    try:
        ib.reqMarketDataType(4 if _MODE.get('delayed') else 2)
        _MODE['frozen'] = True
    except Exception:
        pass


def _num(v):
    """Any finite number. Greeks are legitimately negative - put delta, theta."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float('inf'), float('-inf')):
        return None
    return f


def _px(v):
    """A price. TWS uses -1 to mean 'no quote', so anything negative is not a price."""
    f = _num(v)
    return f if (f is not None and f > 0) else None


def _empty(t):
    return (_px(getattr(t, 'bid', None)) is None
            and _px(getattr(t, 'ask', None)) is None
            and _px(getattr(t, 'last', None)) is None)


# IBKR allows a limited number of simultaneous market data lines (100 by
# default). Subscribe in batches of that size and release each batch before the
# next, so a chain of any depth fits through.
TWS_LINES = int(os.environ.get('PAPER_TWS_LINES', '90'))
# Quotes are held open rather than re-subscribed per request. TWS streams ticks
# continuously; tearing the subscription down after every poll threw that away
# and paid a fresh round trip each time. A warm ticker is read straight out of
# memory, so a repeat quote costs nothing.
TWS_WARM = int(os.environ.get('PAPER_TWS_WARM', '90'))
TWS_WARM_TTL = float(os.environ.get('PAPER_TWS_WARM_TTL', '300'))
# How long a *cold* request may block before answering with what has arrived so
# far. This is not a limit on the work: the subscriptions stay open and keep
# filling, so the next poll a moment later has the rest. Nothing is discarded,
# nothing is re-requested - the screen just stops waiting on the slowest strike.
TWS_PAINT = float(os.environ.get('PAPER_TWS_PAINT', '2'))
# A chain may take longer to fill than a quote; it also keeps its subscriptions,
# so what has not landed by the time it answers is there on the next read.
TWS_CHAIN_PAINT = float(os.environ.get('PAPER_TWS_CHAIN_PAINT', '6'))

# ib_async drives one asyncio loop and is not thread-safe; serve.py answers each
# request on its own thread. Every call into TWS goes through here.
_CALL = threading.RLock()
_SUBS = {}          # key -> {'contract', 'ticker', 'used'}


def _pump(ib, seconds=0.0):
    """Let queued ticks land. Streaming data is already on its way; this just
    gives the event loop a moment to apply it."""
    try:
        ib.waitOnUpdate(timeout=0.01)
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            ib.waitOnUpdate(timeout=0.02)
    except Exception:
        pass


def _warm(ib, key, contract):
    """Return a live ticker for this contract, subscribing only if we do not
    already hold one. Second return value says whether it is brand new."""
    row = _SUBS.get(key)
    if row is not None:
        row['used'] = time.monotonic()
        return row['ticker'], False
    t = ib.reqMktData(contract, '', False, False)
    _SUBS[key] = {'contract': contract, 'ticker': t, 'used': time.monotonic()}
    return t, True


def _sweep(ib):
    """Drop subscriptions nobody has asked about lately, and keep the warm set
    inside its share of the account's market-data lines."""
    now = time.monotonic()
    for k, row in list(_SUBS.items()):
        if now - row['used'] > TWS_WARM_TTL:
            _drop(ib, k)
    while len(_SUBS) > max(1, TWS_WARM):
        oldest = min(_SUBS, key=lambda k: _SUBS[k]['used'])
        _drop(ib, oldest)


def _drop(ib, key):
    row = _SUBS.pop(key, None)
    if row is None:
        return
    try:
        ib.cancelMktData(row['contract'])
    except Exception:
        pass


_PUMP = {'thread': None, 'stop': False}


def _pump_loop():
    """Keep the socket drained between requests.

    ib_async only advances its event loop while someone is calling into it. With
    nothing running between polls, ticks would sit in the socket and a quote
    would be as fresh as the last request rather than as fresh as the market.
    This takes the same lock every other caller takes, so the session is still
    touched by one thread at a time."""
    while not _PUMP['stop']:
        ib = _IB
        if ib is not None and getattr(ib, 'isConnected', lambda: False)():
            if _CALL.acquire(blocking=False):
                try:
                    _pump(ib, 0.02)
                finally:
                    _CALL.release()
        time.sleep(0.08)


def _start_pump():
    if _PUMP['thread'] is None or not _PUMP['thread'].is_alive():
        th = threading.Thread(target=_pump_loop, name='tws-pump', daemon=True)
        _PUMP['thread'] = th
        th.start()


def forget_all(ib=None):
    for k in list(_SUBS):
        _drop(ib or _IB, k)


def _has_px(t):
    return not _empty(t)


def _progress(tickers, want_greeks):
    """How much of what we asked for has arrived. Any increase means TWS is
    still feeding us, so there is no reason to give up."""
    n = 0
    for t in tickers:
        if _has_px(t):
            n += 1
        if want_greeks and getattr(t, 'modelGreeks', None) is not None:
            n += 1
    return n


def _done(t, want_greeks):
    return _has_px(t) and (not want_greeks
                           or getattr(t, 'modelGreeks', None) is not None)


def _stream(ib, contracts, want_greeks=False):
    """Subscribe, wait while data keeps arriving, unsubscribe.

    Deliberately NOT ib.reqTickers(). That issues a *snapshot* request, and IBKR
    requires a live exchange market-data subscription for snapshots - see
    https://interactivebrokers.github.io/tws-api/md_request.html. On a login
    without one (any plain paper account) every snapshot comes back empty, or
    stalls waiting for a tickSnapshotEnd that never arrives. Streaming market
    data carries no such requirement: it is what reqMarketDataType(3) delayed
    mode feeds, and ib_async maps the delayed tick types (66/67/68 for bid/ask/
    last, 83 for model greeks) onto the ordinary Ticker fields.

    Subscriptions run as a sliding window rather than fixed batches. The window
    is only as wide as the account's market-data line allowance, but a filled
    contract is released the moment its data lands and the freed line is reused
    immediately - so one slow strike cannot hold up the rest, and the chain is
    paced by the broker rather than by our own bookkeeping. There is no cap on
    total contracts or total time; only a stretch of complete silence ends it.
    """
    pending = list(contracts)
    active, out = {}, []
    # Lines already held open for warm quotes count against the same allowance.
    width = max(1, TWS_LINES - len(_SUBS))
    idle_since = time.monotonic()

    def release(c):
        try:
            ib.cancelMktData(c)
        except Exception:
            pass

    try:
        while pending or active:
            while pending and len(active) < width:
                c = pending.pop(0)
                try:
                    active[id(c)] = (c, ib.reqMktData(c, '', False, False))
                except Exception:
                    pass
            if not active:
                break
            ib.waitOnUpdate(timeout=0.2)
            filled = [k for k, (c, t) in active.items() if _done(t, want_greeks)]
            for k in filled:
                c, t = active.pop(k)
                release(c)
                out.append(t)
            if filled:
                idle_since = time.monotonic()             # still arriving
            elif time.monotonic() - idle_since > TWS_IDLE:
                # gone quiet: keep whatever partial data arrived and stop
                for k, (c, t) in list(active.items()):
                    release(c)
                    out.append(t)
                active.clear()
                break
    finally:
        for k, (c, t) in list(active.items()):
            release(c)
            out.append(t)
    return out


def _tickers(ib, contracts, want_greeks=False):
    """Get quotes; if the live feed yields nothing, fall back to delayed and retry.
    An empty result on both usually means the login has no market data at all."""
    ts = _stream(ib, contracts, want_greeks)
    if ts and all(_empty(t) for t in ts) and TWS_DATA == 'auto' and not _MODE['delayed']:
        _set_data_mode(ib, True)
        ts = _stream(ib, contracts, want_greeks)
    if ts and all(_empty(t) for t in ts) and TWS_DATA == 'auto' and not _MODE.get('frozen'):
        _set_frozen_mode(ib)
        ts2 = _stream(ib, contracts, want_greeks)
        if any(not _empty(t) for t in ts2):
            ts = ts2
        else:
            _set_data_mode(ib)
    return ts


def _is_paper_login(ib):
    accts = list(getattr(ib, 'managedAccounts', lambda: [])() or [])
    return bool(accts) and all(str(a).upper().startswith(('DU', 'DF')) for a in accts)


def status():
    try:
        ib = connect()
    except TwsUnavailable as e:
        return {'connected': False, 'readonly': True, 'sealed': True,
                'delayed': _MODE['delayed'], 'frozen': _MODE.get('frozen', False),
                'accounts': [],
                'problem': blocking_problem() or str(e), 'errors': last_errors()}
    accts = list(getattr(ib, 'managedAccounts', lambda: [])() or [])
    # IBKR paper logins are DU/DF; anything else is a funded account. Nothing can
    # be traded through this session either way, but the user should be told
    # which login their gateway is actually on.
    live = [a for a in accts if not str(a).upper().startswith(('DU', 'DF'))]
    return {'connected': True, 'readonly': True, 'sealed': is_sealed(ib),
            'delayed': _MODE['delayed'], 'frozen': _MODE.get('frozen', False),
            'accounts': accts, 'live_login': bool(live),
            'problem': blocking_problem() or farm_trouble(),
            'farms': farm_trouble(), 'errors': last_errors()}


def _wait_first(ib, tickers, want_greeks=False, paint=None):
    """Wait on the first sight of these contracts, and no longer than one paint.

    Returns as soon as everything is in, or the paint budget is spent, or the
    feed falls silent. The subscriptions live on either way, so whatever has not
    arrived yet keeps arriving and is simply there on the next read."""
    if not isinstance(tickers, (list, tuple)):
        tickers = [tickers]
    deadline = time.monotonic() + (TWS_PAINT if paint is None else paint)
    idle, best = time.monotonic(), -1
    while True:
        n = sum(1 for t in tickers if _done(t, want_greeks))
        if n >= len(tickers):
            break
        if n > best:
            best, idle = n, time.monotonic()
        if time.monotonic() >= deadline:
            break
        if time.monotonic() - idle > TWS_IDLE:
            break
        ib.waitOnUpdate(timeout=0.05)
    return tickers


_STK = {}          # symbol -> a contract with its conId filled in
_DEFS = {}         # (symbol, expiry) -> every option contract for it, qualified
_DEF_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.contracts.json')
_DEF_DISK = None


def _disk():
    """conIds never change, and IBKR paces the request that fetches them. Keeping
    them on disk means restarting the app does not re-ask for something it has
    already been told - which is what turns a paced repeat into a stall."""
    global _DEF_DISK
    if _DEF_DISK is None:
        try:
            import json
            with open(_DEF_FILE) as fh:
                _DEF_DISK = json.load(fh)
        except Exception:
            _DEF_DISK = {}
    return _DEF_DISK


DEF_TTL = float(os.environ.get('PAPER_TWS_DEF_TTL', str(24 * 3600)))


def forget_definitions():
    """Drop remembered contract definitions, in memory and on disk."""
    global _DEF_DISK
    _DEFS.clear()
    _PARAMS.clear()
    _DEF_DISK = {}
    try:
        os.remove(_DEF_FILE)
    except Exception:
        pass


def _disk_save(key, rows):
    try:
        import json
        d = _disk()
        d['%s|%s' % key] = {'ts': time.time(), 'rows': rows}
        tmp = _DEF_FILE + '.tmp'
        with open(tmp, 'w') as fh:
            json.dump(d, fh)
        os.replace(tmp, _DEF_FILE)
    except Exception:
        pass


def _option_contracts(ib, m, sym, want, trading_class):
    """Every strike and right for one expiry, qualified, in a single request.

    A partially specified option - no strike, no right - matches them all, and
    reqContractDetails returns the lot at once. That matters twice over: it is
    one round trip instead of one per contract, and IBKR paces this request hard
    (a similar repeat is held for a minute), so the answer is kept. conIds do
    not change, which is what makes keeping it safe.
    """
    key = (sym, want)
    have = _DEFS.get(key)
    if have:
        return have
    cached = _disk().get('%s|%s' % key)
    if isinstance(cached, dict):
        # A chain gains strikes as the underlying moves, so a remembered list
        # goes stale even though the conIds in it never do. Re-ask once a day.
        if time.time() - float(cached.get('ts') or 0) > DEF_TTL:
            cached = None
        else:
            cached = cached.get('rows')
    if cached:
        out = []
        for row in cached:
            c = m.Option(sym, want, row['strike'], row['right'], 'SMART',
                         tradingClass=row.get('tradingClass') or '')
            c.conId = row['conId']
            try:
                c.currency = 'USD'
            except Exception:
                pass
            out.append(c)
        _DEFS[key] = out
        return out
    tmpl = m.Option(sym, want, 0, '', 'SMART')
    try:
        tmpl.currency = 'USD'
        if trading_class:
            tmpl.tradingClass = trading_class
    except Exception:
        pass
    out = []
    for d in _await(ib, ib.reqContractDetailsAsync(tmpl), TWS_DEF_IDLE,
                    'the %s %s contract list' % (sym, want)) or []:
        c = getattr(d, 'contract', None)
        if c is not None and getattr(c, 'conId', 0):
            out.append(c)
    if out:
        _DEFS[key] = out
        _disk_save(key, [{'conId': c.conId, 'strike': float(c.strike),
                          'right': c.right,
                          'tradingClass': getattr(c, 'tradingClass', '')}
                         for c in out])
    return out


def _stock(ib, m, sym):
    """A qualified stock contract. ib_async keys its ticker map by hashing the
    contract, and an unqualified one has no conId to hash - so this step is not
    optional. It is one request per symbol, ever, and then it is remembered."""
    c = _STK.get(sym)
    if c is not None:
        return c
    c = m.Stock(sym, 'SMART', 'USD')
    _await(ib, ib.qualifyContractsAsync(c), TWS_DEF_IDLE,
           'resolving %s' % sym)
    if not getattr(c, 'conId', 0):
        raise TwsUnavailable(_why('TWS could not resolve %s to a contract.' % sym))
    _STK[sym] = c
    return c


def _spot_ticker(ib, m, sym, budget):
    """The warm ticker for a symbol, waiting only if we have not seen it before.

    The budget covers the whole call - the live attempt and the delayed retry
    together - so a cold quote answers inside one budget rather than two."""
    until = time.monotonic() + budget
    key = ('STK', sym)
    t, fresh = _warm(ib, key, _stock(ib, m, sym))
    if not fresh:
        _pump(ib)              # already streaming; just let queued ticks land
        return t
    _wait_first(ib, [t], paint=max(0.0, (until - time.monotonic()) / 2))
    # nothing on the live feed and no entitlement? delayed needs none
    if _empty(t) and TWS_DATA == 'auto' and not _MODE['delayed']:
        _drop(ib, key)
        _set_data_mode(ib, True)
        t, _f = _warm(ib, key, _stock(ib, m, sym))
        _wait_first(ib, [t], paint=max(0.0, until - time.monotonic()))
    # still nothing? that is exactly what a closed market looks like on the
    # live/delayed feed - nobody is trading, so there is nothing to stream.
    # Frozen mode answers "what did this last trade at" instead of "what is
    # trading right now", which is the question that actually has an answer
    # after hours.
    if _empty(t) and TWS_DATA == 'auto' and not _MODE.get('frozen'):
        _drop(ib, key)
        _set_frozen_mode(ib)
        t, _f = _warm(ib, key, _stock(ib, m, sym))
        # same overall budget as the live/delayed attempts above, not a new one -
        # a warm re-poll a couple of seconds later (the UI's own retry, or the
        # next auto-refresh) will pick up anything that lands just after this
        _wait_first(ib, [t], paint=max(0.05, until - time.monotonic()))
        if _empty(t):
            # frozen still nothing - restore live/delayed for the next symbol
            # rather than leaving the session stuck in frozen mode
            _drop(ib, key)
            _set_data_mode(ib)
            t, _f = _warm(ib, key, _stock(ib, m, sym))
    return t


def quote(symbol):
    sym = symbol.upper()
    with _CALL:
        ib = connect()
        m = _mod()
        t = _spot_ticker(ib, m, sym, TWS_PAINT)
        _sweep(ib)
        return _quote_from(t, symbol)


def _quote_from(t, symbol):
    last = _px(t.last) or _px(t.close)
    prev = _px(t.close)
    if last is None:
        return {'error': _why('TWS sent no price for %s%s.'
                              % (symbol, '' if _MODE['delayed'] else ' on the live feed'))}
    return {'last': last, 'bid': _px(t.bid), 'ask': _px(t.ask),
            'bidSize': _num(t.bidSize), 'askSize': _num(t.askSize),
            'open': _px(getattr(t, 'open', None)), 'high': _px(getattr(t, 'high', None)),
            'low': _px(getattr(t, 'low', None)), 'prevClose': prev,
            'change': (last - prev) if (last is not None and prev is not None) else None,
            'changePct': ((last - prev) / prev * 100) if (last and prev) else None,
            'volume': _num(t.volume), 'delayed': _MODE['delayed'], 'frozen': _MODE.get('frozen', False)}


def search(pattern):
    with _CALL:
        return _search(pattern)


def _search(pattern):
    ib = connect()
    out = []
    for d in _await(ib, ib.reqMatchingSymbolsAsync(pattern), TWS_DEF_IDLE,
                    'a symbol search for %r' % pattern) or []:
        c = getattr(d, 'contract', None)
        if not c or getattr(c, 'secType', '') not in ('STK', 'IND', 'ETF', ''):
            continue
        out.append({'symbol': getattr(c, 'symbol', ''),
                    'name': getattr(c, 'description', '') or getattr(c, 'symbol', ''),
                    'exchange': getattr(c, 'primaryExchange', '') or getattr(c, 'exchange', ''),
                    'type': 'STK'})
    return out[:20]


def _ymd(s):
    s = str(s or '')
    return s if len(s) == 8 and s.isdigit() else None


def chain(symbol, expiry=None, width=12):
    with _CALL:
        ib = connect()
        m = _mod()
        return _chain(ib, m, symbol.upper(), expiry, width)


_PARAMS = {}          # symbol -> (ts, params list) - reqSecDefOptParamsAsync is identical on
                      # every call for a given underlying, and re-issuing it on a quick retry
                      # (e.g. our own "chain is still filling, try again" loop) is exactly the
                      # kind of repeated identical request IBKR's pacing limiter holds for ~1min
PARAMS_TTL = float(os.environ.get('PAPER_TWS_PARAMS_TTL', '120'))


def _chain(ib, m, sym, expiry, width):
    symbol = sym
    try:
        und = _stock(ib, m, sym)
    except TwsUnavailable as e:
        return {'error': str(e)}
    conid = getattr(und, 'conId', 0)

    cached = _PARAMS.get(sym)
    if cached and time.time() - cached[0] <= PARAMS_TTL:
        params = cached[1]
    else:
        # A known farm outage means this call is going to sit silent for the
        # full 90s and then blame the farm anyway - say so up front instead of
        # making every request pay that wait.
        trouble = farm_trouble()
        if trouble:
            return {'error': 'TWS could not look up options for %s because %s. '
                             'It usually reconnects on its own within a minute - '
                             'try again then.' % (symbol, trouble)}
        params = _await(ib, ib.reqSecDefOptParamsAsync(sym, '', 'STK', conid),
                        TWS_DEF_IDLE, 'the %s option parameters' % sym) or []
        if params:
            _PARAMS[sym] = (time.time(), params)
    smart = [p for p in params if getattr(p, 'exchange', '') == 'SMART'] or params
    if not smart:
        trouble = farm_trouble()
        if trouble:
            return {'error': 'TWS could not look up options for %s because %s. '
                             'It usually reconnects on its own within a minute - '
                             'try again then.' % (symbol, trouble)}
        return {'error': _why('TWS lists no options for %s.' % symbol)}
    p0 = smart[0]
    exps = sorted(_ymd(e) for e in (p0.expirations or []) if _ymd(e))
    strikes = sorted(float(s) for s in (p0.strikes or []))
    if not exps or not strikes:
        return {'error': _why('TWS returned an empty option chain for %s.' % symbol)}

    want = _ymd(expiry) or exps[0]
    st = _spot_ticker(ib, m, sym, TWS_PAINT)
    spot = _px(st.last) or _px(st.close)
    if spot:
        strikes = sorted(strikes, key=lambda k: abs(k - spot))[:max(2, width) * 2 + 1]
        strikes.sort()

    every = _option_contracts(ib, m, sym, want, getattr(p0, 'tradingClass', ''))
    if not every:
        trouble = farm_trouble()
        return {'error': ('TWS returned no contracts for %s expiring %s because %s.'
                          % (sym, want, trouble)) if trouble else
                _why('TWS returned no contracts for %s expiring %s.' % (sym, want))}
    keep = set(strikes)
    contracts = [c for c in every if float(getattr(c, 'strike', 0)) in keep] or every
    contracts.sort(key=lambda c: (float(getattr(c, 'strike', 0)),
                                  getattr(c, 'right', '')))
    if not contracts:
        return {'error': _why('TWS listed no strikes for %s expiring %s.' % (sym, want))}
    # Hold the chain's subscriptions open, exactly like quotes. A chain that
    # fits the line budget is then live: the first load paints inside one budget
    # with whatever has arrived, and every later read is free and complete.
    if len(contracts) <= max(1, TWS_WARM):
        tick = []
        cold = False
        for c in contracts:
            t, fresh = _warm(ib, ('OPT', getattr(c, 'conId', 0)), c)
            cold = cold or fresh
            tick.append(t)
        if cold:
            _wait_first(ib, tick, want_greeks=True, paint=TWS_CHAIN_PAINT)
        else:
            _pump(ib)
        if all(_empty(t) for t in tick) and TWS_DATA == 'auto' and not _MODE['delayed']:
            for c in contracts:
                _drop(ib, ('OPT', getattr(c, 'conId', 0)))
            _set_data_mode(ib, True)
            tick = []
            for c in contracts:
                t, _f = _warm(ib, ('OPT', getattr(c, 'conId', 0)), c)
                tick.append(t)
            _wait_first(ib, tick, want_greeks=True, paint=TWS_CHAIN_PAINT)
        # closed market: nothing traded on live or delayed because nothing is
        # trading, not because anything is broken. Frozen answers with each
        # contract's last real trade instead of the empty "nothing right now".
        if all(_empty(t) for t in tick) and TWS_DATA == 'auto' and not _MODE.get('frozen'):
            for c in contracts:
                _drop(ib, ('OPT', getattr(c, 'conId', 0)))
            _set_frozen_mode(ib)
            tick = []
            for c in contracts:
                t, _f = _warm(ib, ('OPT', getattr(c, 'conId', 0)), c)
                tick.append(t)
            _wait_first(ib, tick, want_greeks=True, paint=TWS_CHAIN_PAINT)
            if all(_empty(t) for t in tick):
                for c in contracts:
                    _drop(ib, ('OPT', getattr(c, 'conId', 0)))
                _set_data_mode(ib)
                tick = []
                for c in contracts:
                    t, _f = _warm(ib, ('OPT', getattr(c, 'conId', 0)), c)
                    tick.append(t)
    else:
        # Deeper than the account's lines allow: stream it through transiently.
        tick = _tickers(ib, contracts, want_greeks=True)

    if tick and all(_empty(x) and getattr(x, 'modelGreeks', None) is None for x in tick):
        # Every option leg came back completely blank on live, delayed and
        # frozen alike - not slow, not partial, nothing at all - while the
        # stock itself and the parameter lookup both worked. That is not a
        # symbol problem (we already have its strikes and expiries from IBKR);
        # it is that this login has no option market data of any kind. On a
        # paper login IBKR does not grant that automatically - it has to be
        # explicitly shared from the linked funded account.
        if _is_paper_login(ib):
            return {'error':
                'TWS resolved %s and listed its option strikes, but returned no '
                'market data at all for any of them - not live, not delayed, not '
                'even frozen last-trade prices. This paper login (%s) most likely '
                'is not sharing market data from your funded account: in Client '
                'Portal go to Settings > Account Configuration > Paper Trading '
                'Account, and turn on "Share real-time market data" for this '
                'paper user. The funded account also needs the OPRA subscription '
                'itself for that sharing to produce option quotes.'
                % (symbol, ', '.join(getattr(ib, 'managedAccounts', lambda: [])() or []) or '?')}
        return {'error': _why(
            'TWS resolved %s and listed its option strikes, but returned no market '
            'data at all for any of them - not live, not delayed, not even frozen '
            'last-trade prices. That points at a market data permissions problem '
            'on this account rather than anything wrong with %s specifically.'
            % (symbol, symbol))}

    calls, puts = [], []
    pending = 0
    for t in tick:
        if _empty(t) and getattr(t, 'modelGreeks', None) is None:
            pending += 1
            continue
        c = t.contract
        g = getattr(t, 'modelGreeks', None)
        leg = {'symbol': '%s %s %s %s' % (sym, want, getattr(c, 'strike', ''), getattr(c, 'right', '')),
               'strike': _px(getattr(c, 'strike', None)),
               'bid': _px(t.bid), 'ask': _px(t.ask), 'last': _px(t.last),
               'volume': _num(t.volume),
               'oi': _num(getattr(t, 'callOpenInterest', None) if getattr(c, 'right', '') == 'C'
                          else getattr(t, 'putOpenInterest', None)),
               'iv': _num(getattr(g, 'impliedVol', None)) if g else None,
               'delta': _num(getattr(g, 'delta', None)) if g else None,
               'gamma': _num(getattr(g, 'gamma', None)) if g else None,
               'theta': getattr(g, 'theta', None) if g else None,
               'vega': _num(getattr(g, 'vega', None)) if g else None,
               'expiry': want}
        if leg['strike'] is None:
            continue
        (calls if getattr(c, 'right', '') == 'C' else puts).append(leg)
    calls.sort(key=lambda x: x['strike'])
    puts.sort(key=lambda x: x['strike'])
    out = {'expirations': exps, 'expiry': want, 'calls': calls, 'puts': puts,
           'delayed': _MODE['delayed'], 'frozen': _MODE.get('frozen', False)}
    if pending:
        # Still filling. Say so rather than pretending this is the whole chain.
        out['partial'] = pending
    _sweep(ib)
    return out


def depth(symbol, rows=5):
    """Level 2. Present so the fill engine can walk a real book later."""
    with _CALL:
        return _depth(symbol, rows)


def _depth(symbol, rows=5):
    ib = connect()
    m = _mod()
    c = _stock(ib, m, symbol.upper())
    t = ib.reqMktDepth(c, numRows=rows)
    ib.sleep(1.0) if hasattr(ib, 'sleep') else None
    bids = [{'price': _px(l.price), 'size': _num(l.size)} for l in (getattr(t, 'domBids', []) or [])]
    asks = [{'price': _px(l.price), 'size': _num(l.size)} for l in (getattr(t, 'domAsks', []) or [])]
    try:
        ib.cancelMktDepth(c)
    except Exception:
        pass
    return {'bids': bids, 'asks': asks}
