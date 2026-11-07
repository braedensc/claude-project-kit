#!/usr/bin/env python3
"""A WebSocket client with the standard library only (KIT-559, a slice of KIT-557).

    python3 scripts/pipeline_ws_client.py --selftest

The tech lead will hold Slack's Socket Mode connection so a DM is picked up within seconds
(KIT-557). Python's standard library has no WebSocket client and the kit stays free of Python
packages, so this is the small piece of RFC 6455 it needs and nothing more. THIS FILE OPENS NO
SLACK CONNECTION AND RUNS NO JOB: the job that uses it is the next slice.

    ws = connect("wss://wss-primary.slack.com/link/?ticket=...", (".slack.com",))
    ws.send_text('{"envelope_id": "..."}')
    got = ws.recv(timeout=30)    # ("text", str), ("binary", bytes), ("close", (code, reason)),
                                 # or None when nothing came in time: the connection is still good
    ws.close()

WHAT IT REFUSES, each with a plain reason and never a crash (WSError):
  * a host that does not end with one of the suffixes the caller gives, a plain ws:// connection
    (except to the loopback address, which only the selftest uses), and any answer but 101 (a
    redirect included: it follows none);
  * an answer whose Sec-WebSocket-Accept is not the key's;
  * a masked server frame, set reserved bits, a control frame that is fragmented or over 125
    bytes, a continuation with nothing to continue, a new message in the middle of one, text that
    is not UTF-8, and a message over the size cap (judged from the frame's header, before its
    payload is read).
  A protocol fault is answered with a close (1002) before the error is raised.

IT NEVER SAYS A URL. The Slack connection address carries a ticket in its query, so no error, and
nothing the object keeps, holds the query string: only the host.

A READ THAT TIMES OUT returns None and leaves the connection usable: a half-read frame stays in the
buffer. A connection that closed or broke says so (WSError). A ping is answered with a pong, and a
close with a close, as the message is read.
"""
import base64
import hashlib
import os
import socket
import ssl
import struct
import sys
import time
from urllib.parse import urlsplit

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_MESSAGE = 1 << 20           # bytes of one message: far above any Socket Mode envelope
MAX_HEADER = 16 * 1024          # bytes of the server's answer to the handshake
LOOPBACK = ("127.0.0.1", "localhost", "::1")
OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0, 1, 2, 8, 9, 10


class WSError(Exception):
    """Always fixed words: never a URL, a query string or a payload."""


def host_allowed(host, suffixes):
    """True when `host` is one of `suffixes` (".slack.com" allows slack.com and any subdomain, and
    nothing that merely contains it: not notslack.com, not slack.com.evil.io)."""
    host = (host or "").lower().rstrip(".")
    return any(host == s.lstrip(".").lower() or host.endswith("." + s.lstrip(".").lower())
               for s in suffixes)


def check_url(url, suffixes, loopback_ok=False):
    """(secure, host, port, target) for a URL this client may open, or WSError. `target` is the
    path and query for the request line only; it is never returned in a message."""
    try:
        parts = urlsplit(url)
        host, port = parts.hostname, parts.port
    except ValueError:
        raise WSError("the address is not a valid WebSocket address")
    if parts.scheme not in ("wss", "ws") or not host:
        raise WSError("the address is not a ws:// or wss:// address")
    loopback = host in LOOPBACK
    if parts.scheme == "ws" and not (loopback and loopback_ok):
        raise WSError("a plain ws:// connection is refused")
    if not loopback and not host_allowed(host, suffixes):
        raise WSError("%s is not a host this connection may open" % host[:80])
    target = (parts.path or "/") + ("?" + parts.query if parts.query else "")
    secure = parts.scheme == "wss"
    return secure, host, port or (443 if secure else 80), target


def encode_frame(opcode, payload=b"", mask=True, fin=True):
    """One frame, masked as a client must (or not, for a test server)."""
    head = bytes([(0x80 if fin else 0) | opcode])
    n = len(payload)
    flag = 0x80 if mask else 0
    if n < 126:
        head += bytes([flag | n])
    elif n < 1 << 16:
        head += bytes([flag | 126]) + struct.pack(">H", n)
    else:
        head += bytes([flag | 127]) + struct.pack(">Q", n)
    if mask:
        key = os.urandom(4)
        payload = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
        head += key
    return head + payload


def parse_frame(buf, max_message):
    """((fin, opcode, payload), bytes used) for the first complete frame in `buf`, or None when
    it is not all there yet. Raises WSError for a frame no server may send."""
    if len(buf) < 2:
        return None
    b0, b1 = buf[0], buf[1]
    if b0 & 0x70:
        raise WSError("the server set reserved bits")
    if b1 & 0x80:
        raise WSError("the server sent a masked frame")
    fin, opcode, n, at = bool(b0 & 0x80), b0 & 0x0F, b1 & 0x7F, 2
    if opcode not in (OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG):
        raise WSError("the server sent a frame of an unknown kind")
    if n == 126:
        if len(buf) < 4:
            return None
        n, at = struct.unpack(">H", bytes(buf[2:4]))[0], 4
    elif n == 127:
        if len(buf) < 10:
            return None
        n, at = struct.unpack(">Q", bytes(buf[2:10]))[0], 10
    if opcode >= 8 and (not fin or n > 125):
        raise WSError("the server sent a control frame that is fragmented or too long")
    if n > max_message:
        raise WSError("the server sent a message over the size limit")
    if len(buf) < at + n:
        return None
    return (fin, opcode, bytes(buf[at:at + n])), at + n


class WebSocket(object):
    def __init__(self, sock, host, leftover=b"", max_message=MAX_MESSAGE):
        self.sock, self.host, self.max_message = sock, host, max_message
        self.buf = bytearray(leftover)
        self.closed = False
        self.sent_close = False
        self._open = None               # (opcode, chunks) of a message still arriving

    def __repr__(self):
        return "<WebSocket %s%s>" % (self.host, " closed" if self.closed else "")

    def _send(self, data):
        try:
            self.sock.sendall(data)
        except (OSError, ssl.SSLError):
            self.closed = True
            raise WSError("the connection broke while sending")

    def send_text(self, text):
        if self.closed or self.sent_close:
            raise WSError("the connection is closed")
        self._send(encode_frame(OP_TEXT, text.encode("utf-8")))

    def _fault(self, why):
        """A protocol fault: say close 1002 if we still can, then stop."""
        if not self.sent_close:
            try:
                self.sock.sendall(encode_frame(OP_CLOSE, struct.pack(">H", 1002)))
            except (OSError, ssl.SSLError):
                pass
            self.sent_close = True
        self.closed = True
        raise WSError(why)

    def _fill(self, deadline):
        """More bytes into the buffer, or False when the deadline passed."""
        left = None if deadline is None else deadline - time.monotonic()
        if left is not None and left <= 0:
            return False
        try:
            self.sock.settimeout(left)
            data = self.sock.recv(65536)
        except (socket.timeout, ssl.SSLWantReadError):
            return False
        except (OSError, ssl.SSLError):
            self.closed = True
            raise WSError("the connection broke")
        if not data:
            self.closed = True
            raise WSError("the connection closed")
        self.buf += data
        return True

    def recv(self, timeout=None):
        """The next message: ("text", str), ("binary", bytes) or ("close", (code, reason)); None
        when `timeout` seconds passed with no whole message (the connection is still good)."""
        if self.closed:
            raise WSError("the connection is closed")
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            try:
                got = parse_frame(self.buf, self.max_message)
            except WSError as exc:
                self._fault(str(exc))
            if got is None:
                if not self._fill(deadline):
                    return None
                continue
            (fin, opcode, payload), used = got
            del self.buf[:used]
            if opcode == OP_PING:
                self._send(encode_frame(OP_PONG, payload))
            elif opcode == OP_PONG:
                pass
            elif opcode == OP_CLOSE:
                if len(payload) == 1:
                    self._fault("the server sent a close frame with half a code")
                code = struct.unpack(">H", payload[:2])[0] if payload else 1005
                try:
                    reason = payload[2:].decode("utf-8")
                except UnicodeDecodeError:
                    reason = ""
                if not self.sent_close:
                    self.sent_close = True
                    try:
                        self.sock.sendall(encode_frame(OP_CLOSE, payload[:2]))
                    except (OSError, ssl.SSLError):
                        pass
                self.closed = True
                return "close", (code, reason[:120])
            else:
                if opcode == OP_CONT:
                    if self._open is None:
                        self._fault("the server continued a message that was not started")
                else:
                    if self._open is not None:
                        self._fault("the server began a message in the middle of another")
                    self._open = (opcode, [])
                self._open[1].append(payload)
                if sum(len(c) for c in self._open[1]) > self.max_message:
                    self._fault("the server sent a message over the size limit")
                if fin:
                    kind, chunks = self._open
                    self._open = None
                    data = b"".join(chunks)
                    if kind == OP_BINARY:
                        return "binary", data
                    try:
                        return "text", data.decode("utf-8")
                    except UnicodeDecodeError:
                        self._fault("the server sent text that is not UTF-8")

    def close(self, code=1000):
        """Say goodbye once and drop the socket. Never raises."""
        if not self.sent_close:
            self.sent_close = True
            try:
                self.sock.sendall(encode_frame(OP_CLOSE, struct.pack(">H", code)))
            except (OSError, ssl.SSLError):
                pass
        self.closed = True
        try:
            self.sock.close()
        except OSError:
            pass


def connect(url, suffixes=(".slack.com",), timeout=10, loopback_ok=False, max_message=MAX_MESSAGE,
            context=None):
    """An open WebSocket to `url`, or WSError in fixed words (never the URL)."""
    secure, host, port, target = check_url(url, suffixes, loopback_ok)
    try:
        raw = socket.create_connection((host, port), timeout=timeout)
    except (OSError, socket.timeout):
        raise WSError("could not reach %s" % host[:80])
    sock = raw
    try:
        if secure:
            sock = (context or ssl.create_default_context()).wrap_socket(raw, server_hostname=host)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        sock.settimeout(timeout)
        sock.sendall(("GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n"
                      % (target, host, key)).encode("ascii", "replace"))
        data = b""
        while b"\r\n\r\n" not in data:
            if len(data) > MAX_HEADER:
                raise WSError("%s answered with a header that is too long" % host[:80])
            chunk = sock.recv(4096)
            if not chunk:
                raise WSError("%s closed the connection during the handshake" % host[:80])
            data += chunk
        head, _sep, leftover = data.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        status = lines[0].split(" ", 2)
        if len(status) < 2 or status[1] != "101":
            raise WSError("%s did not switch to a WebSocket (it answered %s)"
                          % (host[:80], (status[1] if len(status) > 1 else "nothing")[:3]))
        heads = {}
        for line in lines[1:]:
            name, _colon, value = line.partition(":")
            heads[name.strip().lower()] = value.strip()
        want = base64.b64encode(hashlib.sha1((key + GUID).encode("ascii")).digest()).decode("ascii")
        if (heads.get("upgrade", "").lower() != "websocket"
                or "upgrade" not in heads.get("connection", "").lower()
                or heads.get("sec-websocket-accept") != want):
            raise WSError("%s did not accept the WebSocket handshake" % host[:80])
        return WebSocket(sock, host, leftover, max_message)
    except WSError:
        _quiet_close(sock)
        raise
    except (OSError, ssl.SSLError, socket.timeout):
        _quiet_close(sock)
        raise WSError("the handshake with %s failed" % host[:80])


def _quiet_close(sock):
    try:
        sock.close()
    except OSError:
        pass


# ══════════════════════════════════════════════════════════════════════════════════════
# Selftest: a small server this file starts on the loopback address, one connection each
# ══════════════════════════════════════════════════════════════════════════════════════
def _selftest():
    import threading

    checks, failures = [0], []

    def check(name, got, want):
        checks[0] += 1
        if got != want:
            failures.append("%s: got %r, want %r" % (name, got, want))

    def accept_for(key):
        return base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()

    def read_request(conn):
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                return None, b""
            data += chunk
        head, _s, rest = data.partition(b"\r\n\r\n")
        lines = head.decode().split("\r\n")
        heads = dict((l.split(":", 1)[0].strip().lower(), l.split(":", 1)[1].strip())
                     for l in lines[1:] if ":" in l)
        return (lines[0], heads), rest

    def read_client_frame(conn, pending=b""):
        """(fin, opcode, masked, payload) of one client frame, unmasked."""
        buf = bytearray(pending)

        def need(k):
            while len(buf) < k:
                chunk = conn.recv(4096)
                if not chunk:
                    raise EOFError
                buf.extend(chunk)
        need(2)
        n, masked, at = buf[1] & 0x7F, bool(buf[1] & 0x80), 2
        if n == 126:
            need(4)
            n, at = struct.unpack(">H", bytes(buf[2:4]))[0], 4
        elif n == 127:
            need(10)
            n, at = struct.unpack(">Q", bytes(buf[2:10]))[0], 10
        key = b""
        if masked:
            need(at + 4)
            key, at = bytes(buf[at:at + 4]), at + 4
        need(at + n)
        payload = bytes(buf[at:at + n])
        if masked:
            payload = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
        return bool(buf[0] & 0x80), buf[0] & 0x0F, masked, payload

    log = []

    class Server(object):
        """One connection, then done. `script(conn, request, rest)` is what the server does."""

        def __init__(self, script, handshake="ok"):
            self.listener = socket.socket()
            self.listener.bind(("127.0.0.1", 0))
            self.listener.listen(1)
            self.port = self.listener.getsockname()[1]
            self.script, self.handshake = script, handshake
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()

        def run(self):
            try:
                self.listener.settimeout(8)
                conn, _a = self.listener.accept()
            except OSError:
                return
            try:
                conn.settimeout(8)
                request, rest = read_request(conn)
                if request is None:
                    return
                key = request[1].get("sec-websocket-key", "")
                if self.handshake == "ok":
                    conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                                  "Connection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n"
                                  % accept_for(key)).encode())
                elif self.handshake == "bad-accept":
                    conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                                 b"Connection: Upgrade\r\nSec-WebSocket-Accept: nope\r\n\r\n")
                elif self.handshake == "redirect":
                    conn.sendall(b"HTTP/1.1 302 Found\r\nLocation: http://evil.example/\r\n\r\n")
                elif self.handshake == "ok-200":
                    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
                elif self.handshake == "huge":
                    conn.sendall(b"HTTP/1.1 101 x\r\n" + b"X: " + b"a" * (MAX_HEADER + 10))
                elif self.handshake == "hangup":
                    return
                self.script(conn, request, rest)
            except (OSError, EOFError):
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

        def done(self):
            self.thread.join(10)
            self.listener.close()
            return not self.thread.is_alive()

    def url(server, query=""):
        return "ws://127.0.0.1:%d/link/%s" % (server.port, query)

    def open_ws(server, query="", **kw):
        return connect(url(server, query), loopback_ok=True, timeout=5, **kw)

    def raised(fn):
        try:
            fn()
        except WSError as exc:
            return str(exc)
        return "no error"

    # -- the address rules, with no connection made ----------------------------------------
    good = (".slack.com",)
    check("host: slack.com and its subdomains pass",
          [host_allowed(h, good) for h in ("slack.com", "wss-primary.slack.com", "A.B.SLACK.COM.")],
          [True] * 3)
    check("host: lookalikes do not pass",
          [host_allowed(h, good) for h in ("notslack.com", "slack.com.evil.io", "evil.io",
                                           "slack.comx", "", None)], [False] * 6)
    refused = [raised(lambda u=u: check_url(u, good)) for u in (
        "ws://wss-primary.slack.com/x", "wss://evil.example.com/x", "https://slack.com/x",
        "wss:///x", "wss://wss-primary.slack.com:notaport/x", "ws://127.0.0.1:9/x")]
    check("address: plain ws, a foreign host, another scheme, no host, a bad port and loopback "
          "without leave are all refused, none by crashing",
          [r.startswith(("a plain", "evil.example.com is not", "the address is not")) for r in refused],
          [True] * 6)
    check("address: wss to slack passes; loopback passes only with leave",
          (check_url("wss://wss-primary.slack.com/link/?ticket=T", good),
           check_url("ws://127.0.0.1:9/x", good, loopback_ok=True)),
          ((True, "wss-primary.slack.com", 443, "/link/?ticket=T"), (False, "127.0.0.1", 9, "/x")))

    # -- an echo, masking, a ping, fragments, a close ----------------------------------------
    seen = {}

    def echo(conn, request, rest):
        fin, op, masked, payload = read_client_frame(conn, rest)
        seen["masked"], seen["payload"] = masked, payload
        conn.sendall(encode_frame(OP_TEXT, ("echo:" + payload.decode()).encode(), mask=False))
        conn.sendall(encode_frame(OP_PING, b"are you there", mask=False))
        _f, op2, _m, pong = read_client_frame(conn)
        seen["pong"] = (op2, pong)
        conn.sendall(encode_frame(OP_TEXT, b"hel", mask=False, fin=False))
        conn.sendall(encode_frame(OP_PING, b"mid", mask=False))
        conn.sendall(encode_frame(OP_CONT, b"lo ", mask=False, fin=False))
        conn.sendall(encode_frame(OP_CONT, "wörld".encode(), mask=False, fin=True))
        _f, op3, _m, pong2 = read_client_frame(conn)
        seen["pong2"] = (op3, pong2)
        conn.sendall(encode_frame(OP_BINARY, b"\x00\x01", mask=False))
        conn.sendall(encode_frame(OP_CLOSE, struct.pack(">H", 1001) + b"going away", mask=False))
        _f, op4, _m, closing = read_client_frame(conn)
        seen["close"] = (op4, struct.unpack(">H", closing[:2])[0])

    server = Server(echo)
    ws = open_ws(server)
    ws.send_text("hi")
    got = [ws.recv(timeout=5) for _ in range(4)]
    check("echo: a text message, a fragmented one with a ping in the middle, and a binary one",
          got, [("text", "echo:hi"), ("text", "hello wörld"), ("binary", b"\x00\x01"),
                ("close", (1001, "going away"))][:1] + [("text", "hello wörld"),
                                                       ("binary", b"\x00\x01"),
                                                       ("close", (1001, "going away"))])
    check("the client's frame was masked, and a ping was answered with a pong of its payload",
          (seen["masked"], seen["payload"], seen["pong"], seen["pong2"]),
          (True, b"hi", (OP_PONG, b"are you there"), (OP_PONG, b"mid")))
    ended = server.done()
    check("a close was answered with a close of the same code, and the object says it is closed",
          (seen["close"], ws.closed, raised(lambda: ws.recv(1)),
           raised(lambda: ws.send_text("x"))),
          ((OP_CLOSE, 1001), True, "the connection is closed", "the connection is closed"))
    check("the server's thread ended", ended, True)
    ws.close()

    # -- a read that times out leaves the connection good ----------------------------------------
    def late(conn, request, rest):
        time.sleep(0.6)
        part = encode_frame(OP_TEXT, b"slow words", mask=False)
        conn.sendall(part[:5])
        time.sleep(0.4)
        conn.sendall(part[5:])
        time.sleep(0.2)

    server = Server(late)
    ws = open_ws(server)
    t0 = time.monotonic()
    first = ws.recv(timeout=0.2)
    check("a read that times out returns None, in time", (first, time.monotonic() - t0 < 1.0),
          (None, True))
    check("…and a frame that arrives in two halves is read whole after another timeout",
          [ws.recv(timeout=0.2), ws.recv(timeout=0.2), ws.recv(timeout=5)][-1], ("text", "slow words"))
    ws.close()
    check("the server's thread ended (late)", server.done(), True)

    # -- the handshake's refusals ---------------------------------------------------------------
    for mode, want in (("bad-accept", "did not accept the WebSocket handshake"),
                       ("redirect", "did not switch to a WebSocket (it answered 302)"),
                       ("ok-200", "did not switch to a WebSocket (it answered 200)"),
                       ("huge", "answered with a header that is too long"),
                       ("hangup", "closed the connection during the handshake")):
        server = Server(lambda c, r, rest: None, handshake=mode)
        got = raised(lambda: open_ws(server))
        check("handshake: %s" % mode, got, "127.0.0.1 " + want)
        check("handshake %s: its thread ended" % mode, server.done(), True)
    check("handshake: nobody listening says it could not reach the host, without the address",
          raised(lambda: connect("ws://127.0.0.1:1/x?ticket=SECRET", loopback_ok=True,
                                 timeout=2)), "could not reach 127.0.0.1")

    # -- frames no server may send -----------------------------------------------------------------
    def sends(*frames):
        def script(conn, request, rest):
            for f in frames:
                conn.sendall(f)
            try:
                read_client_frame(conn)         # the client's close, if it sends one
            except (OSError, EOFError):
                pass
        return script
    cases = (
        ("a masked frame", [encode_frame(OP_TEXT, b"x", mask=True)],
         "the server sent a masked frame"),
        ("reserved bits", [bytes([0xC1, 0x01]) + b"x"], "the server set reserved bits"),
        ("an unknown kind", [bytes([0x83, 0x00])], "the server sent a frame of an unknown kind"),
        ("a fragmented ping", [encode_frame(OP_PING, b"x", mask=False, fin=False)],
         "the server sent a control frame that is fragmented or too long"),
        ("a long ping", [encode_frame(OP_PING, b"x" * 126, mask=False)],
         "the server sent a control frame that is fragmented or too long"),
        ("a stray continuation", [encode_frame(OP_CONT, b"x", mask=False)],
         "the server continued a message that was not started"),
        ("a message inside a message", [encode_frame(OP_TEXT, b"a", mask=False, fin=False),
                                        encode_frame(OP_TEXT, b"b", mask=False)],
         "the server began a message in the middle of another"),
        ("text that is not UTF-8", [encode_frame(OP_TEXT, b"\xff\xfe", mask=False)],
         "the server sent text that is not UTF-8"),
        ("a message over the cap", [encode_frame(OP_TEXT, b"y" * 200, mask=False)],
         "the server sent a message over the size limit"),
        ("a header that claims a huge length, judged before any payload is read",
         [bytes([0x81, 127]) + struct.pack(">Q", 1 << 40)],
         "the server sent a message over the size limit"),
        ("fragments over the cap", [encode_frame(OP_TEXT, b"y" * 80, mask=False, fin=False),
                                    encode_frame(OP_CONT, b"y" * 80, mask=False)],
         "the server sent a message over the size limit"),
        ("half a close code", [encode_frame(OP_CLOSE, b"\x03", mask=False)],
         "the server sent a close frame with half a code"))
    for name, frames, want in cases:
        server = Server(sends(*frames))
        ws = open_ws(server, "?ticket=SECRET", max_message=100)
        got = raised(lambda: ws.recv(timeout=5))
        check("fault: %s" % name, (got, ws.closed, "SECRET" in got), (want, True, False))
        check("fault %s: its thread ended" % name, server.done(), True)
        ws.close()
    # the client said close 1002 to a faulty server
    got_close = {}

    def watches(conn, request, rest):
        conn.sendall(encode_frame(OP_TEXT, b"\xff", mask=False))
        _f, op, _m, payload = read_client_frame(conn)
        got_close["v"] = (op, struct.unpack(">H", payload[:2])[0])
    server = Server(watches)
    ws = open_ws(server)
    raised(lambda: ws.recv(timeout=5))
    server.done()
    check("a protocol fault is answered with a close of 1002", got_close.get("v"), (OP_CLOSE, 1002))
    ws.close()

    # -- a broken connection says so, and no message holds the URL's query ---------------------------
    def cut(conn, request, rest):
        part = encode_frame(OP_TEXT, b"never whole", mask=False)
        conn.sendall(part[:4])

    server = Server(cut)
    ws = open_ws(server, "?ticket=SECRET")
    got = raised(lambda: ws.recv(timeout=5))
    check("a connection cut in the middle of a frame says it closed, without the query",
          (got, "SECRET" in got, "SECRET" in repr(ws)), ("the connection closed", False, False))
    check("…and then says it is closed", raised(lambda: ws.recv(1)), "the connection is closed")
    check("the server's thread ended (cut)", server.done(), True)
    ws.close()

    # -- frames, encoded and parsed ---------------------------------------------------------------------
    check("a frame of each length encodes and parses back",
          [parse_frame(encode_frame(OP_TEXT, b"z" * n, mask=False), 1 << 20)[0][2] == b"z" * n
           for n in (0, 125, 126, 65535, 65536)], [True] * 5)
    check("a client frame is masked and its mask is not all zeros",
          (encode_frame(OP_TEXT, b"a")[1] & 0x80, encode_frame(OP_TEXT, b"a")[2:6] != b"\0" * 4),
          (0x80, True))
    check("an incomplete frame is not an error", [parse_frame(b"", 100), parse_frame(b"\x81", 100),
                                                  parse_frame(b"\x81\x05ab", 100),
                                                  parse_frame(b"\x81\x7e\x00", 100)],
          [None] * 4)

    if failures:
        print("FAIL: pipeline_ws_client selftest (%d of %d cases failed)" % (len(failures),
                                                                              checks[0]))
        for line in failures:
            print("  - " + line[:600])
        return 1
    print("OK: pipeline_ws_client selftest (%d cases)" % checks[0])
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--selftest"]:
        return _selftest()
    print("usage: pipeline_ws_client.py --selftest (the Socket Mode job imports this module)")
    return 2


if __name__ == "__main__":
    sys.exit(main())
