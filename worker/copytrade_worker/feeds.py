"""Feeds for copy-target buys. First source to fire wins; signals dedupe on (tx_hash, token_id).
  teardata   optimistic blocks, mainnet-teardata.intea.rs/v0/block_opt/{h}: sees fills ~2 blocks after the tx.
  intear_ws  Intear API trades.subscribe per target (wss://api.intea.rs/ws): decoded fills, ~2 s. Backup.
Ported from the Illia sniper (Beak tp/illia/feeds.py). Stdlib only. Each feed calls on_block/on_row and beats health."""

from __future__ import annotations

import base64
import json
import os
import socket
import ssl
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

TEARDATA = "https://mainnet-teardata.intea.rs/v0/"
WS_URL = "wss://api.intea.rs/ws"
WS_FIELDS = [
    "id",
    "height",
    "timestamp_millis",
    "transaction_id",
    "trader",
    "pool_id",
    "token_in",
    "amount_in",
    "token_out",
    "amount_out",
]
NAMES = ("teardata", "intear_ws")

Log = Callable[..., None]


class WS:
    """Minimal WebSocket client: text frames, ping/pong, fragmentation."""

    def __init__(self, url: str, timeout: float = 30):
        u = urllib.parse.urlparse(url)
        port = u.port or (443 if u.scheme == "wss" else 80)
        s: socket.socket = socket.create_connection((u.hostname or "", port), timeout=timeout)
        if u.scheme == "wss":
            s = ssl.create_default_context().wrap_socket(s, server_hostname=u.hostname)
        key = base64.b64encode(os.urandom(16)).decode()
        path = (u.path or "/") + (("?" + u.query) if u.query else "")
        s.sendall(
            (
                f"GET {path} HTTP/1.1\r\nHost: {u.hostname}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nUser-Agent: copytrade-worker\r\n\r\n"
            ).encode()
        )
        self.s, self.buf, self.msg, self.last = s, b"", b"", time.time()
        hdr = self._until(b"\r\n\r\n")
        if b" 101 " not in hdr.split(b"\r\n")[0]:
            raise ConnectionError("ws handshake failed")

    def _fill(self) -> None:
        d = self.s.recv(65536)
        if not d:
            raise ConnectionError("closed")
        self.buf += d

    def _until(self, sep: bytes) -> bytes:
        while sep not in self.buf:
            self._fill()
        i = self.buf.index(sep) + len(sep)
        out, self.buf = self.buf[:i], self.buf[i:]
        return out

    def _send(self, op: int, data: bytes) -> None:
        m = os.urandom(4)
        n = len(data)
        if n < 126:
            h = bytes([0x80 | op, 0x80 | n])
        elif n < 65536:
            h = bytes([0x80 | op, 0xFE]) + struct.pack(">H", n)
        else:
            h = bytes([0x80 | op, 0xFF]) + struct.pack(">Q", n)
        self.s.sendall(h + m + bytes(b ^ m[i % 4] for i, b in enumerate(data)))

    def send(self, text: str) -> None:
        self._send(1, text.encode())

    def recv(self) -> str:
        """Next text message. A socket timeout leaves partial frames in the buffer, so a later call resumes cleanly."""
        while True:
            f = self._frame()
            if f is None:
                self._fill()
                continue
            b0, op, data = f
            self.last = time.time()
            if op == 9:
                self._send(10, data)
                continue
            if op == 10:
                continue
            if op == 8:
                raise ConnectionError("server close")
            self.msg += data
            if b0 & 0x80:
                out, self.msg = self.msg, b""
                return out.decode()

    def _frame(self) -> tuple[int, int, bytes] | None:
        """(b0, opcode, payload) of one complete frame taken from the buffer, or None if it isn't complete yet."""
        b = self.buf
        if len(b) < 2:
            return None
        n = b[1] & 0x7F
        o = 2
        if n == 126:
            if len(b) < 4:
                return None
            n = struct.unpack(">H", b[2:4])[0]
            o = 4
        elif n == 127:
            if len(b) < 10:
                return None
            n = struct.unpack(">Q", b[2:10])[0]
            o = 10
        mask = None
        if b[1] & 0x80:
            if len(b) < o + 4:
                return None
            mask = b[o : o + 4]
            o += 4
        if len(b) < o + n:
            return None
        data = b[o : o + n]
        self.buf = b[o + n :]
        if mask:
            data = bytes(x ^ mask[i % 4] for i, x in enumerate(data))
        return b[0], b[0] & 0x0F, data

    def close(self) -> None:
        try:
            self.s.close()
        except Exception:
            pass


class Health:
    """Last beat per feed; reports a stall once per stall and the recovery."""

    def __init__(self, names: tuple[str, ...], stall_s: float, notify: Callable[[str, bool, float], None]):
        self.t = {n: time.time() for n in names}
        self.stall_s, self.notify = stall_s, notify
        self.down: set[str] = set()
        self.lock = threading.Lock()

    def beat(self, name: str) -> None:
        with self.lock:
            self.t[name] = time.time()
            if name in self.down:
                self.down.discard(name)
                self.notify(name, False, 0)

    def check(self, now: float | None = None) -> None:
        now = now or time.time()
        with self.lock:
            for n, t in self.t.items():
                if now - t > self.stall_s and n not in self.down:
                    self.down.add(n)
                    self.notify(n, True, now - t)

    def snapshot(self) -> dict[str, Any]:
        """For GET /health: {"teardata": {"last_beat_ms", "ok"}, ...}."""
        with self.lock:
            return {n: {"last_beat_ms": int(t * 1000), "ok": n not in self.down} for n, t in self.t.items()}


def http(url: str, timeout: float = 20) -> bytes:
    req = urllib.request.Request(url, headers={"user-agent": "copytrade-worker"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data: bytes = r.read()
        return data


def tail_blocks(
    base: str, name: str, on_block: Callable[..., Any], health: Health, stop: threading.Event, log: Log
) -> None:
    """Optimistic block tail. The server long-polls at the tip; null = skipped height. Far behind: jump to the tip."""
    h = None
    errs = 0
    while not stop.is_set():
        try:
            if h is None:
                h = json.loads(http(base + "last_block/optimistic"))["block"]["header"]["height"] + 1
            body = http(base + f"block_opt/{h}")
            errs = 0
            if body.strip() != b"null":
                b = json.loads(body)
                health.beat(name)
                hdr = b["block"]["header"]
                ts = int(hdr.get("timestamp_nanosec") or hdr["timestamp"]) / 1e9
                on_block(b, name, time.time())
                if time.time() - ts > 30:
                    log("feed_gap", feed=name, height=h, lag_s=round(time.time() - ts, 1))
                    h = None
                    continue
            h += 1
        except urllib.error.HTTPError as e:
            errs += 1
            time.sleep(0.5 if e.code != 429 else 5)
            if errs in (3, 30):
                log("feed_error", feed=name, error=f"HTTP {e.code}")
        except Exception as e:
            errs += 1
            time.sleep(0.5)
            if errs in (3, 30):
                log("feed_error", feed=name, error=type(e).__name__)
            if errs > 10:
                h = None


def ws_trades(
    whales_fn: Callable[[], set[str]],
    on_row: Callable[..., Any],
    health: Health,
    stop: threading.Event,
    log: Log,
    url: str = WS_URL,
    name: str = "intear_ws",
) -> None:
    """trades.subscribe per target. Reconnects with after_id so no trade is missed across a drop,
    and when the target set changes."""
    last_id = None
    while not stop.is_set():
        ws = None
        try:
            whales = set(whales_fn())
            if not whales:
                health.beat(name)
                stop.wait(5)
                continue
            ws = WS(url)
            ws.s.settimeout(5)
            for i, w in enumerate(sorted(whales), 1):
                p: dict[str, Any] = dict(fields=WS_FIELDS, trader=w)
                if last_id:
                    p["after_id"] = last_id
                ws.send(json.dumps({"id": i, "method": "trades.subscribe", "params": p}))
            health.beat(name)
            pinged = checked = time.time()
            while not stop.is_set():
                if time.time() - pinged > 10:
                    ws._send(9, b"hb")
                    pinged = time.time()
                if time.time() - checked > 15:
                    checked = time.time()
                    if set(whales_fn()) != whales:
                        break  # resubscribe with the new set
                try:
                    m = json.loads(ws.recv())
                except TimeoutError:
                    m = None
                if time.time() - ws.last < 20:
                    health.beat(name)
                if m is None:
                    if time.time() - ws.last > 60:
                        raise ConnectionError("silent socket")
                    continue
                if isinstance(m, dict) and m.get("error"):
                    log("feed_error", feed=name, error=str(m["error"])[:120])
                    continue
                res = m.get("result", m) if isinstance(m, dict) else {}
                if not isinstance(res, dict):
                    continue
                cols = res.get("columns") or WS_FIELDS
                for row in res.get("rows") or []:
                    if not isinstance(row, list):
                        continue
                    r = dict(zip(cols, row, strict=False))
                    rid = r.get("id")
                    if isinstance(rid, int):
                        last_id = max(last_id or 0, rid)
                    on_row(r, name, time.time())
        except Exception as e:
            log("feed_error", feed=name, error=type(e).__name__)
            time.sleep(3)
        finally:
            if ws:
                ws.close()


def start(
    whales_fn: Callable[[], set[str]],
    on_block: Callable[..., Any],
    on_row: Callable[..., Any],
    notify: Callable[[str, bool, float], None],
    log: Log,
    stop: threading.Event,
    stall_s: float = 30,
) -> Health:
    health = Health(NAMES, stall_s, notify)
    ts = [
        threading.Thread(target=tail_blocks, args=(TEARDATA, "teardata", on_block, health, stop, log), daemon=True),
        threading.Thread(target=ws_trades, args=(whales_fn, on_row, health, stop, log), daemon=True),
    ]
    for t in ts:
        t.start()

    def watch() -> None:
        while not stop.is_set():
            health.check()
            stop.wait(2)

    threading.Thread(target=watch, daemon=True).start()
    return health
