#!/usr/bin/env python3
"""
Paper Desk - local runner.

Serves paper_local.html, proxies /api/* to the IBKR Client Portal Gateway on
https://localhost:5000/v1/api, and bridges /ws to the gateway's WebSocket so the
page gets streaming ticks instead of polling. Same-origin throughout, so CORS
never comes up, and the gateway's self-signed certificate is accepted because the
connection never leaves this machine.

    python3 serve.py            then open http://localhost:8765

Requires the IBKR Client Portal Gateway running and logged in:
    1. download the Client Portal API from interactivebrokers.com/en/trading/ib-api.php
    2. unzip, then run  bin/run.sh root/conf.yaml   (Windows: bin\\run.bat root\\conf.yaml)
    3. open https://localhost:5000 and log in
Standard library only - nothing to install.
"""
import http.server, socketserver, urllib.request, urllib.error, urllib.parse, ssl, os, sys, json
import threading, webbrowser, time, hashlib, hmac, re
from http.cookies import SimpleCookie, CookieError
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ws as wsproto
import providers
import base64, zlib, struct, socket, gzip
import market

PAGE_CACHE = {'mtime': None, 'raw': b'', 'gzip': b''}
PAGE_LOCK = threading.Lock()


def _make_icon():
    """A 512x512 PNG drawn in pure Python - a rising bar on a dark ground."""
    W = 512
    bg = (18, 21, 26)
    fg = (63, 191, 128)
    rows = []
    bars = [(60, 300), (150, 210), (240, 250), (330, 130), (420, 60)]
    for y in range(W):
        row = bytearray()
        for x in range(W):
            c = bg
            for bx, top in bars:
                if bx <= x < bx + 62 and y >= top:
                    c = fg
                    break
            row += bytes(c)
        rows.append(b'\x00' + bytes(row))
    raw = b''.join(rows)

    def chunk(tag, data):
        return (struct.pack('>I', len(data)) + tag + data
                + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff))
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', W, W, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw, 6))
            + chunk(b'IEND', b''))


ICON_PNG = _make_icon()

PORT = int(os.environ.get('PORT') or os.environ.get('PAPER_PORT') or '8765')
BIND = os.environ.get('PAPER_BIND', '127.0.0.1')
ACCESS = os.environ.get('PAPER_ACCESS_TOKEN', '')
GATEWAY = os.environ.get('PAPER_GATEWAY', 'https://localhost:5000/v1/api')
WS_URL = os.environ.get('PAPER_GATEWAY_WS') or (
    GATEWAY.replace('https://', 'wss://').replace('http://', 'ws://') + '/ws')
HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, 'paper_local.html')

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE          # the gateway ships a self-signed cert

COOKIES = {}                              # gateway session, shared by REST and WS
COOKIE_LOCK = threading.Lock()
MAX_PROXY_BODY = 1024 * 1024

# Only the data/session operations used by the UI may reach the broker.
# Paper orders stay in the browser; even an authenticated caller cannot submit,
# modify, confirm or cancel a real order through this proxy.
GATEWAY_GET_PATHS = frozenset({
    '/iserver/accounts', '/iserver/marketdata/snapshot',
    '/iserver/marketdata/history', '/iserver/secdef/strikes',
    '/iserver/secdef/info', '/trsrv/secdef', '/trsrv/futures',
})
GATEWAY_POST_PATHS = frozenset({
    '/iserver/auth/status', '/iserver/reauthenticate', '/iserver/secdef/search',
})


def gateway_allowed(method, path):
    if method == 'GET':
        return path in GATEWAY_GET_PATHS
    if method == 'POST':
        return (path in GATEWAY_POST_PATHS or
                re.fullmatch(r'/iserver/account/[A-Za-z0-9_-]+/orders/whatif', path) is not None)
    return False


def remember_cookies(headers):
    raw = headers.get_all('Set-Cookie') if hasattr(headers, 'get_all') else None
    if not raw:
        one = headers.get('Set-Cookie')
        raw = [one] if one else []
    with COOKIE_LOCK:
        for c in raw:
            pair = c.split(';', 1)[0].strip()
            if '=' in pair:
                k, v = pair.split('=', 1)
                COOKIES[k.strip()] = v.strip()


def cookie_header():
    with COOKIE_LOCK:
        return '; '.join('%s=%s' % kv for kv in COOKIES.items())


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = 'PaperDesk/2.0'
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        # Provider credentials and one-time login tokens must never reach logs.
        line = re.sub(r'\?[^\s"]*', '?[redacted]', fmt % args)
        if '/api/' not in line and '/ws' not in line:
            sys.stderr.write('%s - %s\n' % (self.address_string(), line))

    def setup(self):
        super().setup()
        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.connection.settimeout(30)

    # ---------- static page ----------
    def _page(self):
        try:
            mtime = os.stat(PAGE).st_mtime_ns
            with PAGE_LOCK:
                if PAGE_CACHE['mtime'] != mtime:
                    with open(PAGE, 'rb') as page:
                        raw = page.read()
                    PAGE_CACHE.update(mtime=mtime, raw=raw, gzip=gzip.compress(raw, compresslevel=6))
                compressed = 'gzip' in self.headers.get('Accept-Encoding', '')
                data = PAGE_CACHE['gzip' if compressed else 'raw']
        except FileNotFoundError:
            self.send_error(500, 'paper_local.html is missing next to serve.py')
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Vary', 'Accept-Encoding')
        if compressed:
            self.send_header('Content-Encoding', 'gzip')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    # ---------- REST proxy ----------
    def _proxy(self, method):
        path = self.path[len('/api'):] or '/'
        if not gateway_allowed(method, urllib.parse.urlsplit(path).path):
            return self._reject(403, 'Only market data, session checks and what-if previews are allowed')
        lengths = self.headers.get_all('Content-Length') or []
        if self.headers.get('Transfer-Encoding') or len(lengths) > 1:
            return self._reject(400, 'Unsupported request body framing')
        raw_length = lengths[0] if lengths else '0'
        if not re.fullmatch(r'[0-9]+', raw_length) or len(raw_length) > 10:
            return self._reject(400, 'Invalid Content-Length')
        length = int(raw_length)
        if length > MAX_PROXY_BODY:
            return self._reject(413, 'Request body is too large')
        body = self.rfile.read(length) if length else None
        req = urllib.request.Request(GATEWAY + path, data=body, method=method)
        req.add_header('Content-Type', 'application/json')
        req.add_header('User-Agent', 'PaperDesk')
        ck = cookie_header()
        if ck:
            req.add_header('Cookie', ck)
        try:
            with urllib.request.urlopen(req, context=CTX, timeout=25) as r:
                payload, status = r.read(), r.status
                ctype = r.headers.get('Content-Type', 'application/json')
                remember_cookies(r.headers)
        except urllib.error.HTTPError as e:
            payload, status, ctype = e.read(), e.code, 'application/json'
            try:
                remember_cookies(e.headers)
            except Exception:
                pass
        except Exception as e:
            payload = json.dumps({'error': 'gateway unreachable', 'detail': str(e)}).encode()
            status, ctype = 502, 'application/json'
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    # ---------- WebSocket bridge ----------
    def _bridge(self):
        key = self.headers.get('Sec-WebSocket-Key')
        if not key:
            self.send_error(400, 'not a WebSocket request')
            return
        try:
            with COOKIE_LOCK:
                cookies = dict(COOKIES)
            up = wsproto.connect(WS_URL, cookies=cookies, origin='http://localhost:%d' % PORT)
        except Exception as e:
            self.send_response(502)
            msg = json.dumps({'error': 'gateway websocket unreachable', 'detail': str(e)}).encode()
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
            return
        down = self.connection
        self.wfile.write((
            'HTTP/1.1 101 Switching Protocols\r\n'
            'Upgrade: websocket\r\nConnection: Upgrade\r\n'
            'Sec-WebSocket-Accept: %s\r\n\r\n' % wsproto.accept_key(key)).encode())
        self.wfile.flush()
        alive = threading.Event(); alive.set()

        def pump(src, dst, mask):
            try:
                while alive.is_set():
                    op, data = wsproto.recv_frame(src)
                    if op == wsproto.OP_CLOSE:
                        break
                    if op == wsproto.OP_PING:
                        wsproto.send_frame(src, data, wsproto.OP_PONG, mask=not mask)
                        continue
                    if op == wsproto.OP_PONG:
                        continue
                    wsproto.send_frame(dst, data, wsproto.OP_TEXT, mask=mask)
            except Exception:
                pass
            finally:
                alive.clear()

        # browser -> gateway must be masked; gateway -> browser must not be
        t1 = threading.Thread(target=pump, args=(down, up, True), daemon=True)
        t2 = threading.Thread(target=pump, args=(up, down, False), daemon=True)
        t1.start(); t2.start()
        while alive.is_set():
            time.sleep(0.2)
        for s in (up,):
            try:
                s.close()
            except Exception:
                pass
        self.close_connection = True

    # ---------- free-provider fallback ----------
    def _data(self):
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query)
        one = lambda k, d=None: (q.get(k) or [d])[0]
        prov = one('provider', os.environ.get('PAPER_PROVIDER', 'alpaca'))
        key = one('key') or os.environ.get('PAPER_PROVIDER_KEY')
        # Different credentials can have different data entitlements. Never
        # reuse another key's response just because both keys are nonempty.
        credential_id = hashlib.sha256(key.encode('utf-8')).digest() if key else None
        kind = u.path.rsplit('/', 1)[-1]
        # A local streaming source is already live in memory - caching its
        # answers for seconds would throw away the very freshness it provides.
        # Networked providers still get a real cache to stay inside rate limits.
        local = bool(providers.PROVIDERS.get(prov, {}).get('local'))
        ttl_quote = 0.5 if local else 15
        ttl_chain = 2 if local else 90
        try:
            if kind == 'providers':
                out = {'providers': providers.PROVIDERS}
            elif kind == 'quote':
                out = providers._cached(
                    ('q', prov, one('symbol', ''), credential_id), ttl_quote,
                    lambda: providers.cascade('quote', prov, key, symbol=one('symbol', '')))
            elif kind == 'search' and prov == 'tws':
                out = {'results': market.ENGINE.call('search', one('q', '')), 'served_by': 'tws'}
            elif kind == 'chain' and prov == 'tws':
                out = market.ENGINE.call('chain', one('symbol', '').upper(), one('expiry'), timeout=100)
            elif kind == 'search':
                out = providers._cached(
                    ('s', prov, one('q', ''), credential_id), 300,
                    lambda: providers.cascade('search', prov, key, q=one('q', '')))
            elif kind == 'twsstatus':
                out = market.ENGINE.snapshot(one('client', ''))
            elif kind in ('guns_scan', 'guns_bars', 'guns_news'):
                args = () if kind == 'guns_scan' else (one('symbol', ''),)
                out = market.ENGINE.call(kind, *args, timeout=40)
            elif kind == 'guns_verify':
                out = market.ENGINE.call(kind, one('conid', ''), timeout=40)
            elif kind == 'guns_article':
                out = market.ENGINE.call(kind, one('newsProvider', ''), one('articleId', ''), timeout=20)
            elif kind == 'depth':
                out = market.ENGINE.call('depth', one('symbol', ''))
            elif kind == 'selftest':
                out = providers.selftest(prov, key, one('symbol', 'AAPL'))
            elif kind == 'chain':
                out = providers._cached(
                    ('c', prov, one('symbol', ''), one('expiry'), credential_id), ttl_chain,
                    lambda: providers.cascade('chain', prov, key,
                                              symbol=one('symbol', ''), expiry=one('expiry')))
            else:
                out = {'error': 'unknown data endpoint'}
        except Exception as e:
            out = {'error': providers._friendly(e, providers.PROVIDERS.get(prov, {}).get('name', prov))}
        self._send(json.dumps(out), 'application/json')

    def _subscriptions(self):
        if self.headers.get('Transfer-Encoding'):
            return self._reject(400, 'Chunked bodies are not supported')
        lengths = self.headers.get_all('Content-Length') or []
        if len(lengths) != 1 or not lengths[0].isdigit() or len(lengths[0]) > 10:
            return self._reject(400, 'Invalid Content-Length')
        length = int(lengths[0])
        if not 0 < length <= MAX_PROXY_BODY:
            return self._reject(413, 'Subscription payload is too large or empty')
        try:
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError('Expected an object')
            result = market.ENGINE.subscribe(payload.get('client'), payload.get('instruments'))
        except (ValueError, TypeError) as e:
            return self._reject(400, str(e))
        self._send(json.dumps(result, allow_nan=False, separators=(',', ':')), 'application/json')

    def _stream_quotes(self):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        client = (q.get('client') or [''])[0]
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', client):
            return self._reject(400, 'Invalid stream client ID')
        self.close_connection = True
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache, no-transform')
        self.send_header('X-Accel-Buffering', 'no')
        self.send_header('Connection', 'close')
        self.end_headers()
        seq = 0  # Every connection receives a complete snapshot, including reconnects.
        try:
            while True:
                payload = market.ENGINE.snapshot(client, seq) if seq == 0 else market.ENGINE.wait(client, seq)
                seq = payload['seq']
                data = json.dumps(payload, allow_nan=False, separators=(',', ':'))
                self.wfile.write(('event: quotes\ndata: ' + data + '\n\n').encode())
                self.wfile.flush()
                time.sleep(0.1)  # Coalesce bursts without dropping the newest quote.
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    # ---------- installable-app bits ----------
    def _send(self, body, ctype, code=200, cache='no-store'):
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', cache)
        self.end_headers()
        self.wfile.write(body)

    def _manifest(self):
        self._send(json.dumps({
            'name': 'Paper Desk', 'short_name': 'Paper Desk',
            'start_url': '/', 'scope': '/', 'display': 'standalone',
            'background_color': '#12151a', 'theme_color': '#12151a',
            'icons': [{'src': '/icon.png', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'any maskable'}]
        }), 'application/manifest+json', cache='max-age=3600')

    def _icon(self):
        self._send(ICON_PNG, 'image/png', cache='max-age=86400')

    def end_headers(self):
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Content-Type-Options', 'nosniff')
        super().end_headers()

    def _reject(self, code, message):
        # Rejected POST bodies remain unread: close rather than interpreting
        # their bytes as another request on an HTTP/1.1 connection.
        self.close_connection = True
        return self._send(json.dumps({'error': message}), 'application/json', code)

    def _token_matches(self, token):
        return bool(ACCESS) and hmac.compare_digest(token.encode('utf-8'), ACCESS.encode('utf-8'))

    def _authed(self):
        if not ACCESS:
            return True
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        if self._token_matches((q.get('t') or [''])[0]):
            return True
        try:
            cookies = SimpleCookie()
            cookies.load(self.headers.get('Cookie') or '')
            token = cookies.get('pd_token')
            return token is not None and self._token_matches(token.value)
        except CookieError:
            return False

    def _authorize(self):
        if not self._authed():
            self._reject(403, 'Add ?t=YOUR_TOKEN to the app address to sign in')
            return False
        # Browsers must not let an unrelated website drive a local brokerage
        # session, even when this local-only installation uses no access token.
        origin = self.headers.get('Origin')
        if origin:
            try:
                parsed = urllib.parse.urlsplit(origin)
                same_origin = (parsed.scheme in ('http', 'https') and
                               parsed.netloc.lower() == (self.headers.get('Host') or '').lower())
            except ValueError:
                same_origin = False
            if not same_origin:
                self._reject(403, 'Cross-origin requests are not allowed')
                return False
        if self.headers.get('Sec-Fetch-Site') == 'cross-site' and self.headers.get('Sec-Fetch-Mode') != 'navigate':
            self._reject(403, 'Cross-site requests are not allowed')
            return False
        return True

    def do_GET(self):
        if self.path.split('?')[0] == '/manifest.json':
            return self._manifest()
        if self.path.split('?')[0] in ('/icon.png', '/favicon.ico'):
            # browsers ask for /favicon.ico unprompted; answering beats a 404 in
            # the log on every single page load
            return self._icon()
        if not self._authorize():
            return
        assets = {'/assets/' + name: name for name in
                  ('guns.js', 'guns-execution.js', 'guns-workflow.js', 'guns-tutorial.js', 'guns-ui.js', 'guns.css')}
        asset = assets.get(self.path.split('?')[0])
        if asset:
            mime = 'text/css' if asset.endswith('.css') else 'text/javascript'
            with open(os.path.join(HERE, asset), encoding='utf-8') as source:
                return self._send(source.read(), mime + '; charset=utf-8')
        if self.path.split('?')[0] == '/data/stream':
            return self._stream_quotes()
        if self.path.startswith('/data/'):
            return self._data()
        if self.path.startswith('/api/'):
            return self._proxy('GET')
        if self.path.split('?')[0] == '/ws':
            return self._bridge()
        if self.path.split('?')[0] in ('/', '/index.html'):
            if ACCESS:
                u = urllib.parse.urlsplit(self.path)
                q = urllib.parse.parse_qs(u.query)
                if self._token_matches((q.get('t') or [''])[0]):
                    cookie = SimpleCookie()
                    cookie['pd_token'] = ACCESS
                    cookie['pd_token']['path'] = '/'
                    cookie['pd_token']['samesite'] = 'Lax'
                    cookie['pd_token']['httponly'] = True
                    cookie['pd_token']['max-age'] = 31536000
                    if self.headers.get('X-Forwarded-Proto', '').lower() == 'https':
                        cookie['pd_token']['secure'] = True
                    self.send_response(303)
                    self.send_header('Set-Cookie', cookie['pd_token'].OutputString())
                    self.send_header('Location', u.path)
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
            return self._page()
        self.send_error(404)

    def do_POST(self):
        if not self._authorize():
            return
        if self.path.split('?')[0] == '/data/subscriptions':
            return self._subscriptions()
        if self.path.startswith('/api/'):
            return self._proxy('POST')
        self._reject(404, 'Unknown endpoint')

    def do_DELETE(self):
        if not self._authorize():
            return
        if self.path.startswith('/api/'):
            return self._proxy('DELETE')
        self._reject(404, 'Unknown endpoint')


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == '__main__':
    url = 'http://localhost:%d' % PORT
    if not os.path.exists(PAGE):
        print('!! paper_local.html not found next to serve.py'); sys.exit(1)
    print('Paper Desk    -> %s' % url)
    print('REST proxy    -> %s' % GATEWAY)
    print('stream bridge -> %s' % WS_URL)
    print('fallback data -> /data/* (%s)' % os.environ.get('PAPER_PROVIDER', 'alpaca'))
    if ACCESS:
        print('access token set - add ?t=YOUR_TOKEN to the app address (token is not logged)')
    if BIND != '127.0.0.1':
        print('listening on %s (reachable from other devices)' % BIND)
    if os.environ.get('PAPER_NO_BROWSER') != '1' and BIND == '127.0.0.1':
        try:
            webbrowser.open(url + ('/?t=' + urllib.parse.quote(ACCESS, safe='') if ACCESS else ''))
        except Exception:
            pass
    with Server((BIND, PORT), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print('\nstopped')
