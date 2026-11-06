"""The one NEAR RPC client (with failover), plus Intear token data. Holds no keys.
Signal side: lenient reads (`query`, `view`: None on any error). Executor and API: strict calls (`call`, `call_view`,
`view_access_key`, `block_hash`, `send_tx`, `tx_status`) that raise `RpcError` on an error reply and `RpcDown` when
no endpoint answered.
All replies are untrusted: shapes are checked, numbers parsed defensively. Free RPCs rate-limit: calls are spaced."""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
import urllib.request
from collections.abc import Callable, Iterable
from typing import Any

INTEAR = "https://api.intea.rs"
DEFAULT_RPCS = ("https://free.rpc.fastnear.com", "https://near.lava.build")
UA = {"user-agent": "copytrade-worker", "content-type": "application/json"}
ACCOUNT = re.compile(r"[a-z0-9][a-z0-9._-]{0,62}[a-z0-9]|[a-z0-9]")
TOKEN_FIELDS = [
    "symbol",
    "decimals",
    "price_usd",
    "price_near",
    "market_cap_usd",
    "total_supply_with_decimals",
    "created_at_millis",
]

Fetch = Callable[[str, Any, float], Any]


class RpcError(Exception):
    """The RPC answered with an error (deterministic: not retried on another endpoint)."""

    def __init__(self, error: Any):
        self.error = error
        super().__init__(self.text[:300])

    @property
    def text(self) -> str:
        return self.error if isinstance(self.error, str) else json.dumps(self.error)


class RpcDown(OSError):
    """No endpoint gave a usable answer. For send_tx: the tx may or may not have reached the chain."""


def http_json(url: str, body: Any = None, timeout: float = 10) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    with urllib.request.urlopen(urllib.request.Request(url, data, UA), timeout=timeout) as r:
        return json.load(r)


def num(x: Any) -> float | None:
    if isinstance(x, bool):
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def rows_of(d: Any) -> list[dict[str, Any]]:
    if not isinstance(d, dict) or not isinstance(d.get("columns"), list):
        return []
    return [dict(zip(d["columns"], r, strict=False)) for r in d.get("rows") or [] if isinstance(r, list)]


def env_rpcs() -> list[str]:
    """RPC_URLS (comma list), else NEAR_RPC_URL."""
    raw = os.environ.get("RPC_URLS") or os.environ.get("NEAR_RPC_URL") or ""
    return [u.strip() for u in raw.split(",") if u.strip()]


class Chain:
    def __init__(self, rpcs: Iterable[str] | None = None, fetch: Fetch = http_json, min_gap_s: float = 0.25):
        self.rpcs = list(rpcs or env_rpcs() or DEFAULT_RPCS)
        self.url = self.rpcs[0]  # last endpoint that answered (GET /health)
        self.fetch = fetch
        self.gap = min_gap_s
        self._t = 0.0
        self._lock = threading.Lock()

    def _space(self) -> None:
        with self._lock:
            wait = self._t + self.gap - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._t = time.monotonic()

    def call(self, method: str, params: Any, tries: int = 3) -> Any:
        """JSON-RPC result. Next endpoint on network error or junk reply; RpcError on an error reply; RpcDown when
        no endpoint answered. send_tx: an error after a network failure raises RpcDown (the earlier endpoint may
        have taken the tx, so 'InvalidNonce' and the like are not a clean rejection)."""
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        lost = False
        for i in range(tries):
            url = self.rpcs[i % len(self.rpcs)]
            self._space()
            try:
                r = self.fetch(url, body, 8)
            except Exception:  # noqa: BLE001 - any transport failure: next endpoint
                lost = True
                continue
            if not isinstance(r, dict) or ("error" not in r and "result" not in r):
                lost = True
                continue
            self.url = url
            if "error" in r:
                if method == "send_tx" and lost:
                    raise RpcDown(f"send_tx after lost attempt: {str(r['error'])[:200]}")
                raise RpcError(r["error"])
            res = r["result"]
            if isinstance(res, dict) and "error" in res and "result" not in res:
                raise RpcError(res["error"])
            return res
        raise RpcDown(f"{method}: no endpoint answered")

    def query(self, params: dict[str, Any], tries: int = 3) -> dict[str, Any] | None:
        """`query` RPC result, or None (error, missing method, all endpoints down)."""
        try:
            res = self.call("query", params, tries)
        except (RpcError, RpcDown):
            return None
        return res if isinstance(res, dict) and "error" not in res else None

    # ---- strict calls (executor, API) ----
    def call_view(self, account: str, method: str, args: dict[str, Any], finality: str = "final") -> Any:
        """Decoded view result (may be None for a JSON null). Raises RpcError, RpcDown."""
        res = self.call(
            "query",
            dict(
                request_type="call_function",
                finality=finality,
                account_id=account,
                method_name=method,
                args_base64=base64.b64encode(json.dumps(args).encode()).decode(),
            ),
        )
        try:
            return json.loads(bytes(res["result"]).decode())
        except (KeyError, TypeError, ValueError):
            raise RpcError(f"bad view result {account}.{method}") from None

    def view_access_key(self, account: str, public_key: str) -> dict[str, Any]:
        res = self.call(
            "query",
            {"request_type": "view_access_key", "finality": "final", "account_id": account, "public_key": public_key},
        )
        if not isinstance(res, dict):
            raise RpcError("bad access key reply")
        return res

    def block_hash(self) -> str:
        res = self.call("block", {"finality": "final"})
        try:
            h = res["header"]["hash"]
        except (KeyError, TypeError):
            raise RpcError("bad block reply") from None
        if not isinstance(h, str):
            raise RpcError("bad block reply")
        return h

    def send_tx(self, signed_b64: str) -> str:
        """Broadcast (wait_until NONE). The same signed tx on failover, so at most one lands."""
        r = self.call("send_tx", {"signed_tx_base64": signed_b64, "wait_until": "NONE"})
        h = r.get("transaction", {}).get("hash", "") if isinstance(r, dict) else ""
        return h if isinstance(h, str) else ""

    def tx_status(self, tx_hash: str, sender: str) -> dict[str, Any] | None:
        """Outcome, or None while unknown (not seen yet, or still running)."""
        params = {"tx_hash": tx_hash, "sender_account_id": sender, "wait_until": "EXECUTED_OPTIMISTIC"}
        try:
            r = self.call("tx", params)
        except RpcError as e:
            if "UNKNOWN_TRANSACTION" in e.text or "TIMEOUT" in e.text.upper():
                return None
            raise
        return r if isinstance(r, dict) else None

    def view(self, account: str, method: str, args: dict[str, Any] | None = None) -> Any:
        res = self.query(
            dict(
                request_type="call_function",
                finality="optimistic",
                account_id=account,
                method_name=method,
                args_base64=base64.b64encode(json.dumps(args or {}).encode()).decode(),
            )
        )
        try:
            return json.loads(bytes(res["result"]).decode()) if res else None
        except (KeyError, TypeError, ValueError):
            return None

    def code_check(self, account: str) -> dict[str, Any] | None:
        """{sniping_tax, code_hash} from the account's wasm, or None when unreadable. Rule from Beak snipe:
        'sniping_tax' in the code (JustHoot launch code held it while transfer_fee_pips read 0)."""
        res = self.query({"request_type": "view_code", "finality": "optimistic", "account_id": account})
        if not res or not isinstance(res.get("code_base64"), str):
            return None
        try:
            code = base64.b64decode(res["code_base64"])
        except ValueError:
            return None
        h = res.get("hash")
        return {"sniping_tax": b"sniping_tax" in code, "code_hash": h if isinstance(h, str) else None}

    def active_orders(self, account: str) -> list[dict[str, Any]] | None:
        v = self.view("dclv2.ref-labs.near", "list_active_orders", {"account_id": account})
        return [r for r in v if isinstance(r, dict)] if isinstance(v, list) else None

    def tokens(self, ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        """{token: row} from Intear /tokens. Missing ones left out; {} on error."""
        try:
            d = self.fetch(INTEAR + "/tokens", {"fields": TOKEN_FIELDS, "ids": sorted(set(ids))}, 6)
        except Exception:
            return {}
        return {r["account_id"]: r for r in rows_of(d) if isinstance(r.get("account_id"), str)}

    def launches(self, parents: Iterable[str], since_ms: int, limit: int = 100) -> list[dict[str, Any]]:
        """Newest launches under the launchpad parents (Beak launches.py fetch_tokens). Raises on network error."""
        d = self.fetch(
            INTEAR + "/tokens",
            dict(
                fields=["created_at_millis"],
                where=dict(parent=list(parents), created_after_millis=since_ms),
                sort=dict(by="created_at_millis", desc=True),
                limit=limit,
            ),
            15,
        )
        return [r for r in rows_of(d) if ACCOUNT.fullmatch(str(r.get("account_id") or ""))]


def market(info: dict[str, Any] | None, near_usd: float | None) -> tuple[float | None, int | None, int | None]:
    """(mcap USD, created ms, decimals) from one Intear token row. mcap: market_cap_usd, else price x supply."""
    if not isinstance(info, dict):
        return None, None, None
    dec = info.get("decimals")
    dec = dec if isinstance(dec, int) and not isinstance(dec, bool) and 0 <= dec <= 40 else None
    ca = num(info.get("created_at_millis"))
    created = int(ca) if ca and ca > 0 else None
    m = num(info.get("market_cap_usd"))
    if not m or m <= 0:
        sup = num(info.get("total_supply_with_decimals"))
        px = num(info.get("price_usd"))
        if not px and near_usd:
            pn = num(info.get("price_near"))
            px = pn * near_usd if pn else None
        m = px * sup if px and sup else None
    return (m if m and m > 0 else None), created, dec
