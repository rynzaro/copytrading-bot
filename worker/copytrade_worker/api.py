"""Worker internal HTTP API (docs/interfaces/worker-api.md). Binds 127.0.0.1 only. Never returns key material."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from . import keys as K
from .nearrpc import RpcError

log = logging.getLogger("copytrade.api")
MAX_BODY = 4096


class App:
    def __init__(self, db: sqlite3.Connection, rpc: Any, keystore: K.KeyStore,
                 health_parts: Callable[[], dict] | None = None):
        """health_parts: the signal side's part of /health, {"feeds", "last_signal_at"} (service.Signals.health_json).
        None: no feeds, last_signal_at read from the DB."""
        self.db, self.rpc, self.ks, self.health_parts = db, rpc, keystore, health_parts
        self.lock = threading.Lock()

    def bot_key(self, body: dict) -> tuple[int, dict]:
        owner = body.get("owner_id")
        if not isinstance(owner, str) or not K.valid_account(owner):
            return 400, {"error": "invalid owner_id"}
        with self.lock:
            if not self.db.execute("SELECT 1 FROM users WHERE owner_id = ?", (owner,)).fetchone():
                return 404, {"error": "unknown owner"}
            pk = self.ks.ensure(owner)
            self.db.execute("UPDATE users SET bot_public_key = ? WHERE owner_id = ?", (pk, owner))
        return 200, {"public_key": pk}

    def vault_confirmed(self, body: dict) -> tuple[int, dict]:
        owner, vault = body.get("owner_id"), body.get("vault_id")
        if not isinstance(owner, str) or not isinstance(vault, str) or not K.valid_account(owner) \
                or not K.valid_account(vault):
            return 400, {"error": "invalid owner_id or vault_id"}
        row = self.db.execute("SELECT bot_public_key FROM users WHERE owner_id = ?", (owner,)).fetchone()
        if not row or not row["bot_public_key"]:
            return 404, {"error": "unknown owner or no bot key"}
        pk = row["bot_public_key"]
        try:
            st = self.rpc.view(vault, "get_state", {})
        except (RpcError, OSError, ValueError):
            return 502, {"error": "rpc"}
        if not isinstance(st, dict) or st.get("owner_id") != owner:
            return 409, {"error": "vault owner mismatch"}
        if st.get("bot_key") != pk:
            return 409, {"error": "bot key not set on vault"}
        try:
            ak = self.rpc.view_access_key(vault, pk)
        except (RpcError, OSError, ValueError):
            return 409, {"error": "bot key not on vault account"}
        perm = (ak or {}).get("permission")
        if not isinstance(perm, dict) or "FunctionCall" not in perm or perm["FunctionCall"].get("receiver_id") != vault:
            return 409, {"error": "bot key permission"}
        try:
            with self.lock:
                self.db.execute("UPDATE users SET vault_id = ? WHERE owner_id = ?", (vault, owner))
        except sqlite3.IntegrityError:
            return 409, {"error": "vault_id taken"}
        return 200, {"ok": True}

    def health(self) -> tuple[int, dict]:
        rpc = getattr(self.rpc, "url", "")
        if self.health_parts is not None:
            try:
                parts = self.health_parts()
                return 200, {"feeds": parts.get("feeds", {}), "rpc": rpc, "last_signal_at": parts.get("last_signal_at")}
            except Exception:  # noqa: BLE001 - health must answer
                log.exception("health parts")
        r = self.db.execute("SELECT MAX(seen_at) AS m FROM signals").fetchone()
        feeds: dict = {} if self.health_parts is None else {"error": "feeds"}
        return 200, {"feeds": feeds, "rpc": rpc, "last_signal_at": r["m"]}


def handler(app: App) -> type[BaseHTTPRequestHandler]:
    class H(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            log.debug("api %s", self.path)

        def _out(self, code: int, body: dict) -> None:
            b = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self) -> None:
            if self.path == "/health":
                self._out(*app.health())
            else:
                self._out(404, {"error": "not found"})

        def do_POST(self) -> None:
            routes = {"/bot-key": app.bot_key, "/vault-confirmed": app.vault_confirmed}
            fn = routes.get(self.path)
            if fn is None:
                return self._out(404, {"error": "not found"})
            try:
                n = int(self.headers.get("content-length") or 0)
                if n > MAX_BODY:
                    return self._out(413, {"error": "too large"})
                body = json.loads(self.rfile.read(n) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError  # noqa: TRY004
            except ValueError:
                return self._out(400, {"error": "bad json"})
            try:
                self._out(*fn(body))
            except Exception:
                log.exception("api error %s", self.path)
                self._out(500, {"error": "internal"})

    return H


DEFAULT_PORT = 8700  # worker-api.md: env WORKER_PORT, default 8700; web calls WORKER_URL


def api_port() -> int:
    return int(os.environ.get("WORKER_PORT") or DEFAULT_PORT)


def api_host() -> str:
    """127.0.0.1 unless WORKER_HOST is set. Compose sets 0.0.0.0: the container sits on an internal network only."""
    return os.environ.get("WORKER_HOST") or "127.0.0.1"


def serve(app: App, port: int | None = None, host: str | None = None) -> ThreadingHTTPServer:
    """Start in a daemon thread, bound to api_host() (default 127.0.0.1)."""
    port = api_port() if port is None else port
    host = api_host() if host is None else host
    srv = ThreadingHTTPServer((host, port), handler(app))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
