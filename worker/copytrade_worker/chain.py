"""Network reads: NEAR RPC views, contract code, Intear token data. Read-only, no keys.
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


class Chain:
    def __init__(self, rpcs: Iterable[str] | None = None, fetch: Fetch = http_json, min_gap_s: float = 0.25):
        env = [u.strip() for u in (os.environ.get("RPC_URLS") or "").split(",") if u.strip()]
        self.rpcs = list(rpcs or env or DEFAULT_RPCS)
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

    def query(self, params: dict[str, Any], tries: int = 3) -> dict[str, Any] | None:
        """`query` RPC result, or None (error, missing method, all endpoints down)."""
        for i in range(tries):
            self._space()
            try:
                r = self.fetch(
                    self.rpcs[i % len(self.rpcs)], {"jsonrpc": "2.0", "id": 1, "method": "query", "params": params}, 8
                )
            except Exception:
                continue
            if not isinstance(r, dict):
                continue
            res = r.get("result")
            if "error" in r or not isinstance(res, dict) or "error" in res:
                return None
            return res
        return None

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
