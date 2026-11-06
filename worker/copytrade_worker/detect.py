"""Copy-target buy detection from block JSON (teardata format) and Intear trade rows. Pure: no network.

Ported from the Illia sniper (Beak tp/illia/detect.py). Two stages per buy:
  tx    the target's transaction in a chunk (block N): token, amount in, pools, or the order point.
  fill  receipt logs of the executed swap (N+2) or the placed order (`order_added`): exact amounts.
        Only successful receipts count.
Only swap fills become signals (`to_signal`). Limit buy orders become alerts, never buys: placed (`limit_order`)
and filled (`order_fill`, from claim/cancel events or list_active_orders records).
Token and account names here are untrusted text: compared, never executed.
"""

from __future__ import annotations

import base64
import json
import math
from collections.abc import Collection, Iterator
from typing import Any

from .types import Hop, Signal

DCL = "dclv2.ref-labs.near"
V1 = "v2.ref-finance.near"
WNEAR = "wrap.near"
LN = math.log(1.0001)
VENUE = {DCL: "dcl", V1: "ref_v1"}

Raw = dict[str, Any]


def _ok(status: Any) -> bool:
    return isinstance(status, dict) and ("SuccessValue" in status or "SuccessReceiptId" in status)


def _args(fc: dict[str, Any]) -> Any:
    try:
        return json.loads(base64.b64decode(fc.get("args") or ""))
    except Exception:
        return None


def parse_msg(msg: Any) -> Raw | None:
    """(kind, output token, own min out, pools, point) from a DCL or v1 ft_transfer_call msg, or None."""
    try:
        m = json.loads(msg) if isinstance(msg, str) else msg
    except Exception:
        return None
    if not isinstance(m, dict):
        return None
    if isinstance(m.get("Swap"), dict):
        s = m["Swap"]
        return dict(
            kind="swap",
            venue=DCL,
            token=s.get("output_token"),
            min_out=s.get("min_output_amount"),
            pools=s.get("pool_ids") or [],
        )
    for k in ("LimitOrderWithSwap", "LimitOrder"):
        if isinstance(m.get(k), dict):
            o = m[k]
            return dict(
                kind="order", venue=DCL, token=o.get("buy_token"), pools=[o.get("pool_id")], point=o.get("point")
            )
    acts = m.get("actions")
    if isinstance(acts, list) and acts and all(isinstance(a, dict) for a in acts):
        return dict(
            kind="swap",
            venue=V1,
            token=acts[-1].get("token_out"),
            min_out=acts[-1].get("min_amount_out"),
            pools=[a.get("pool_id") for a in acts],
            token_in=acts[0].get("token_in"),
        )
    return None


def tx_signals(tx: dict[str, Any], whales: Collection[str]) -> list[Raw]:
    """Signals from one signed transaction. ft_transfer_call carries the swap; near_deposit before it only wraps."""
    signer = tx.get("signer_id")
    if signer not in whales:
        return []
    out = []
    for a in tx.get("actions") or []:
        fc = a.get("FunctionCall") if isinstance(a, dict) else None
        if not fc or fc.get("method_name") != "ft_transfer_call":
            continue
        args = _args(fc)
        if not isinstance(args, dict) or args.get("receiver_id") not in (DCL, V1):
            continue
        p = parse_msg(args.get("msg"))
        if not p or not p.get("token"):
            continue
        out.append(
            dict(
                stage="tx",
                whale=signer,
                token=p["token"],
                token_in=tx.get("receiver_id"),
                amount_in=str(args.get("amount")),
                kind=p["kind"],
                venue=p["venue"],
                whale_min_out=p.get("min_out"),
                pools=p["pools"],
                point=p.get("point"),
                tx=tx.get("hash"),
            )
        )
    return out


def _events(logs: Any) -> Iterator[dict[str, Any]]:
    for line in logs or []:
        if isinstance(line, str) and line.startswith("EVENT_JSON:"):
            try:
                e = json.loads(line[11:])
            except Exception:
                continue
            if isinstance(e, dict):
                yield e


def receipt_signals(ro: dict[str, Any], whales: Collection[str], tx_hash: str | None = None) -> list[Raw]:
    """Fill signals from one receipt execution outcome. Failed receipts keep their logs: those are ignored."""
    o = (ro.get("execution_outcome") or {}).get("outcome") or {}
    if not _ok(o.get("status")):
        return []
    r = ro.get("receipt") or {}
    act = (r.get("receipt") or {}).get("Action") or {}
    signer = act.get("signer_id")
    exe = o.get("executor_id") or r.get("receiver_id")
    if signer not in whales:
        return []
    txh = tx_hash or ro.get("tx_hash")
    out: list[Raw] = []
    if exe == DCL:
        swaps = []
        for e in _events(o.get("logs")):
            if e.get("standard") != "dcl.ref":
                continue
            for d in e.get("data") or []:
                if not isinstance(d, dict):
                    continue
                if e.get("event") == "swap" and d.get("swapper") == signer and int(d.get("amount_in") or 0) > 0:
                    swaps.append(d)
                if e.get("event") == "order_added" and d.get("owner_id") == signer:
                    out.append(
                        dict(
                            stage="fill",
                            kind="order",
                            whale=signer,
                            token=d.get("buy_token"),
                            token_in=d.get("sell_token"),
                            amount_in=d.get("original_amount"),
                            point=d.get("point"),
                            pools=[d.get("pool_id")],
                            venue=DCL,
                            tx=txh,
                            order_id=d.get("order_id"),
                        )
                    )
                if e.get("event") in ("order_completed", "order_cancelled") and d.get("owner_id") == signer:
                    f = order_fill(d, str(signer))  # emitted on claim or cancel: fills that happened earlier
                    if f:
                        out.append(dict(f, tx=txh, event=e.get("event")))
        if swaps:  # a multi-hop swap logs one event per pool: first in, last out
            out.append(
                dict(
                    stage="fill",
                    kind="swap",
                    whale=signer,
                    token=swaps[-1].get("token_out"),
                    token_in=swaps[0].get("token_in"),
                    amount_in=swaps[0].get("amount_in"),
                    amount_out=swaps[-1].get("amount_out"),
                    pools=[s.get("pool_id") for s in swaps],
                    hops=[(s.get("pool_id"), s.get("token_in"), s.get("token_out")) for s in swaps],
                    venue=DCL,
                    tx=txh,
                )
            )
    elif exe == V1:
        hops = []
        for line in o.get("logs") or []:
            p = line.split() if isinstance(line, str) else []
            if len(p) == 6 and p[0] == "Swapped" and p[3] == "for":
                hops.append((p[2], p[1], p[5], p[4]))
        if hops:
            out.append(
                dict(
                    stage="fill",
                    kind="swap",
                    whale=signer,
                    token=hops[-1][2],
                    token_in=hops[0][0],
                    amount_in=hops[0][1],
                    amount_out=hops[-1][3],
                    venue=V1,
                    tx=txh,
                    pools=[],
                )
            )
    return out


def block_signals(block: dict[str, Any], whales: Collection[str]) -> list[Raw]:
    """All target signals in one block: transactions first, then receipt fills."""
    h = (block.get("block") or {}).get("header") or {}
    meta = dict(height=h.get("height"), block_ts=int(h.get("timestamp_nanosec") or h.get("timestamp") or 0) / 1e9)
    out = []
    for sh in block.get("shards") or []:
        for t in ((sh.get("chunk") or {}).get("transactions")) or []:
            out += [dict(s, **meta) for s in tx_signals(t.get("transaction") or {}, whales)]
    for sh in block.get("shards") or []:
        for ro in sh.get("receipt_execution_outcomes") or []:
            out += [dict(s, **meta) for s in receipt_signals(ro, whales)]
    return out


def order_fill(o: dict[str, Any] | None, whale: str) -> Raw | None:
    """Fill from a DCL order record (list_active_orders entry or order_completed/cancelled event data), or None
    while nothing is bought. The maker gets the exact point price."""
    if not isinstance(o, dict):
        return None
    try:
        bought = int(o.get("bought_amount") or 0)
        orig = int(o.get("original_amount") or 0)
        remain = o.get("remain_amount")
        cancel = o.get("actual_cancel_amount") or o.get("cancel_amount")
        sold = orig - int(remain) if remain is not None else orig - int(cancel or 0)
    except (TypeError, ValueError):
        return None
    if bought <= 0 or sold <= 0:
        return None
    return dict(
        stage="fill",
        kind="order_fill",
        whale=whale,
        token=o.get("buy_token"),
        token_in=o.get("sell_token"),
        amount_in=str(sold),
        amount_out=str(bought),
        point=o.get("point"),
        pools=[o.get("pool_id")],
        venue=DCL,
        order_id=o.get("order_id"),
    )


def ws_signal(row: Any, whales: Collection[str]) -> Raw | None:
    """Intear trades.subscribe row (dict by columns) -> fill signal, or None."""
    if (
        not isinstance(row, dict)
        or row.get("trader") not in whales
        or not row.get("token_out")
        or not row.get("amount_out")
    ):
        return None
    return dict(
        stage="fill",
        kind="swap",
        whale=row["trader"],
        token=row["token_out"],
        token_in=row.get("token_in"),
        amount_in=row.get("amount_in"),
        amount_out=row.get("amount_out"),
        pools=[row.get("pool_id")],
        tx=row.get("transaction_id"),
        height=row.get("height"),
        block_ts=(row.get("timestamp_millis") or 0) / 1000,
        venue=None,
    )


def point_price(point: int, pool_id: str, token: str, dec_token: int, dec_other: int) -> float:
    """Other-token per whole `token` at a DCL point. Pool id is 'x|y|fee'; price y per x (raw) = 1.0001^point."""
    x, y = pool_id.split("|")[:2]
    raw = math.exp(point * LN)
    if token == x:
        return raw * 10 ** (dec_token - dec_other)
    if token == y:
        return 1 / raw * 10 ** (dec_token - dec_other)
    raise ValueError("token not in pool")


def other_of(pool_id: str, token: str) -> str:
    x, y = pool_id.split("|")[:2]
    return y if token == x else x


def _str(x: Any) -> str | None:
    return x if isinstance(x, str) and x else None


def _amount(x: Any) -> str | None:
    """Decimal integer string > 0, or None. Chain data is untrusted."""
    s = str(x) if isinstance(x, (int, str)) else ""
    return s if s.isdigit() and int(s) > 0 else None


def limit_order(raw: Raw) -> Raw | None:
    """A placed DCL limit buy order paid in wNEAR (`order_added`) -> alert fields, or None.
    Keys: target_id, token_id, pool_id, amount_near (yocto), point (int or None), tx_hash, order_id. No meme check."""
    if raw.get("stage") != "fill" or raw.get("kind") != "order" or raw.get("token_in") != WNEAR:
        return None
    target, token, txh = _str(raw.get("whale")), _str(raw.get("token")), _str(raw.get("tx"))
    pools = raw.get("pools") or []
    pool = _str(pools[0]) if len(pools) == 1 else None
    amount, oid = _amount(raw.get("amount_in")), _str(raw.get("order_id"))
    if not (target and token and txh and pool and amount and oid) or token == WNEAR:
        return None
    if set(pool.split("|")[:2]) != {token, WNEAR}:
        return None
    pt = raw.get("point")
    point = pt if isinstance(pt, int) and not isinstance(pt, bool) else None
    return dict(
        target_id=target, token_id=token, pool_id=pool, amount_near=amount, point=point, tx_hash=txh, order_id=oid
    )


def to_signal(raw: Raw, source: str, seen_at: int) -> Signal | None:
    """A swap fill -> Signal, or None. Placed orders and tx-stage rows are not signals."""
    if raw.get("stage") != "fill" or raw.get("kind") != "swap":
        return None
    target, token, txh = _str(raw.get("whale")), _str(raw.get("token")), _str(raw.get("tx"))
    if not (target and token and txh):
        return None
    pools = [p for p in raw.get("pools") or [] if isinstance(p, str) and p]
    hops: tuple[Hop, ...] = ()
    if raw.get("hops"):
        try:
            hops = tuple(Hop(str(p), str(a), str(b)) for p, a, b in raw["hops"])
        except (TypeError, ValueError):
            hops = ()
    token_in, amount_in = _str(raw.get("token_in")), _amount(raw.get("amount_in"))
    return Signal(
        target_id=target,
        token_id=token,
        kind="swap",
        tx_hash=txh,
        source=source,
        seen_at=seen_at,
        venue=VENUE.get(raw.get("venue") or "", None),
        pool_id=pools[-1] if pools else None,
        near_in=amount_in if token_in == WNEAR else None,
        hops=hops,
        token_in=token_in,
        amount_in=amount_in,
        amount_out=_amount(raw.get("amount_out")),
        extra={k: raw[k] for k in ("height",) if raw.get(k) is not None},
    )


def fill_price(s: Signal, decimals: int | None) -> float | None:
    """NEAR per whole token the target paid. Only when paid in wNEAR and decimals are known."""
    if s.token_in != WNEAR or decimals is None or not s.amount_in or not s.amount_out:
        return None
    if not (0 <= decimals <= 40):
        return None
    return (int(s.amount_in) / 10**24) / (int(s.amount_out) / 10**decimals)
