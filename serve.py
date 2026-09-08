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
import threading, webbrowser, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ws as wsproto
import providers
import base64, zlib, struct


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
        line = fmt % args
        if '/api/' not in line and '/ws' not in line:
            sys.stderr.write('%s - %s\n' % (self.address_string(), line))

    # ---------- static page ----------
    def _page(self):
        try:
            data = open(PAGE, 'rb').read()
        except FileNotFoundError:
            self.send_error(500, 'paper_local.html is missing next to serve.py')
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    # ---------- REST proxy ----------
    def _proxy(self, method):
        path = self.path[len('/api'):] or '/'
        length = int(self.headers.get('Content-Length') or 0)
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
            up = wsproto.connect(WS_URL, cookies=dict(COOKIES), origin='http://localhost:%d' % PORT)
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
                    ('q', prov, one('symbol', ''), bool(key)), ttl_quote,
                    lambda: providers.cascade('quote', prov, key, symbol=one('symbol', '')))
            elif kind == 'search':
                out = providers._cached(
                    ('s', prov, one('q', ''), bool(key)), 300,
                    lambda: providers.cascade('search', prov, key, q=one('q', '')))
            elif kind == 'twsstatus':
                import tws as _tws
                out = _tws.status()
            elif kind == 'depth':
                import tws as _tws
                out = _tws.depth(one('symbol', ''))
            elif kind == 'selftest':
                out = providers.selftest(prov, key, one('symbol', 'AAPL'))
            elif kind == 'chain':
                out = providers._cached(
                    ('c', prov, one('symbol', ''), one('expiry'), bool(key)), ttl_chain,
                    lambda: providers.cascade('chain', prov, key,
                                              symbol=one('symbol', ''), expiry=one('expiry')))
            else:
                out = {'error': 'unknown data endpoint'}
        except Exception as e:
            out = {'error': providers._friendly(e, providers.PROVIDERS.get(prov, {}).get('name', prov))}
        body = json.dumps(out).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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

    def _authed(self):
        if not ACCESS:
            return True
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query)
        if (q.get('t') or [''])[0] == ACCESS:
            return True
        ck = self.headers.get('Cookie') or ''
        return ('pd_token=' + ACCESS) in ck

    def do_GET(self):
        if self.path.split('?')[0] == '/manifest.json':
            return self._manifest()
        if self.path.split('?')[0] in ('/icon.png', '/favicon.ico'):
            # browsers ask for /favicon.ico unprompted; answering beats a 404 in
            # the log on every single page load
            return self._icon()
        if not self._authed():
            return self._send('<h2 style="font-family:system-ui;padding:2rem">'
                              'Add ?t=YOUR_TOKEN to the address to open this.</h2>',
                              'text/html; charset=utf-8', 403)
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
                if (q.get('t') or [''])[0] == ACCESS:
                    self.send_response(200)
                    self.send_header('Set-Cookie', 'pd_token=%s; Path=/; SameSite=Lax; Max-Age=31536000' % ACCESS)
                    try:
                        data = open(PAGE, 'rb').read()
                    except FileNotFoundError:
                        return self.send_error(500, 'paper_local.html is missing')
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    return self.wfile.write(data)
            return self._page()
        self.send_error(404)

    def do_POST(self):
        if self.path.startswith('/api/'):
            return self._proxy('POST')
        self.send_error(404)

    def do_DELETE(self):
        if self.path.startswith('/api/'):
            return self._proxy('DELETE')
        self.send_error(404)


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
        print('access token set - open %s/?t=%s' % (url, ACCESS))
    if BIND != '127.0.0.1':
        print('listening on %s (reachable from other devices)' % BIND)
    if os.environ.get('PAPER_NO_BROWSER') != '1' and BIND == '127.0.0.1':
        try:
            webbrowser.open(url)
        except Exception:
            pass
    with Server((BIND, PORT), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print('\nstopped')
