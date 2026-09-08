"""Minimal RFC 6455 codec - server side toward the browser, client side toward the gateway.
Standard library only. Text frames, ping/pong and close; no extensions, no fragmentation
beyond what the peers actually send (continuation frames are reassembled)."""
import base64, hashlib, os, socket, struct, ssl, urllib.parse

GUID = b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
OP_CONT, OP_TEXT, OP_BIN, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1(key.encode() + GUID).digest()).decode()


def _recv_exact(sock, n):
    buf = b''
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError('peer closed')
        buf += chunk
    return buf


def recv_frame(sock):
    """Returns (opcode, payload_bytes). Reassembles continuation frames."""
    frames = []
    first_op = None
    while True:
        b1, b2 = _recv_exact(sock, 2)
        fin = b1 & 0x80
        op = b1 & 0x0F
        masked = b2 & 0x80
        ln = b2 & 0x7F
        if ln == 126:
            ln = struct.unpack('>H', _recv_exact(sock, 2))[0]
        elif ln == 127:
            ln = struct.unpack('>Q', _recv_exact(sock, 8))[0]
        mask = _recv_exact(sock, 4) if masked else None
        data = _recv_exact(sock, ln) if ln else b''
        if mask:
            data = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
        if first_op is None and op != OP_CONT:
            first_op = op
        frames.append(data)
        if fin:
            return first_op if first_op is not None else op, b''.join(frames)


def send_frame(sock, payload, op=OP_TEXT, mask=False):
    if isinstance(payload, str):
        payload = payload.encode('utf-8')
    hdr = bytearray([0x80 | op])
    ln = len(payload)
    m = 0x80 if mask else 0
    if ln < 126:
        hdr.append(m | ln)
    elif ln < (1 << 16):
        hdr.append(m | 126); hdr += struct.pack('>H', ln)
    else:
        hdr.append(m | 127); hdr += struct.pack('>Q', ln)
    if mask:
        key = os.urandom(4)
        hdr += key
        payload = bytes(c ^ key[i % 4] for i, c in enumerate(payload))
    sock.sendall(bytes(hdr) + payload)


def connect(url, cookies=None, origin=None, timeout=20):
    """Open a client WebSocket to url (ws:// or wss://). Returns a connected socket
    with the handshake already completed. Self-signed certificates are accepted -
    the gateway is on this machine."""
    u = urllib.parse.urlsplit(url)
    secure = u.scheme == 'wss'
    port = u.port or (443 if secure else 80)
    host = u.hostname or 'localhost'
    sock = socket.create_connection((host, port), timeout=timeout)
    if secure:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        sock = ctx.wrap_socket(sock, server_hostname=host)
    key = base64.b64encode(os.urandom(16)).decode()
    path = u.path or '/'
    if u.query:
        path += '?' + u.query
    lines = [
        'GET %s HTTP/1.1' % path,
        'Host: %s:%d' % (host, port),
        'Upgrade: websocket',
        'Connection: Upgrade',
        'Sec-WebSocket-Key: %s' % key,
        'Sec-WebSocket-Version: 13',
        'User-Agent: PaperDesk',
    ]
    if origin:
        lines.append('Origin: %s' % origin)
    if cookies:
        lines.append('Cookie: ' + '; '.join('%s=%s' % kv for kv in cookies.items()))
    sock.sendall(('\r\n'.join(lines) + '\r\n\r\n').encode())
    buf = b''
    while b'\r\n\r\n' not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError('gateway closed during handshake')
        buf += chunk
    head = buf.split(b'\r\n\r\n', 1)[0].decode('latin-1')
    if '101' not in head.split('\r\n')[0]:
        raise ConnectionError('gateway refused the upgrade: ' + head.split('\r\n')[0])
    return sock
