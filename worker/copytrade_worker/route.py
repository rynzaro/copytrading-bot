"""Buy route and min_out.

Route: the token's main pool on Rhea DCL or Ref v1, quote wNEAR direct or via RHEA (2 hops, same venue:
the vault takes one venue per swap). Pool data from RPC views (DCL `get_pool`, Ref v1 `get_pool`).

Rates are raw out per raw in (smallest units), after the pool fee. Price = NEAR per whole token.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, getcontext
from fractions import Fraction
from typing import Any

from .nearrpc import RpcError
from .types import MinOutInputs

getcontext().prec = 60

WNEAR = "wrap.near"
RHEA = "token.rhealab.near"
QUOTES = (WNEAR, RHEA)
DCL = "dclv2.ref-labs.near"
REF_V1 = "v2.ref-finance.near"
VENUE_CONTRACT = {"dcl": DCL, "ref_v1": REF_V1}
DCL_FEES = (100, 400, 2000, 10000)
NEAR_DEC = 24


class NoRoute(Exception):
    pass


class LimitError(Exception):
    """Price outside the user's limits (or limits unset): don't send."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class Pool:
    venue: str
    pool_id: str
    token_a: str
    token_b: str
    reserves: tuple[int, int]     # raw amounts of token_a, token_b in the pool
    spot_b_per_a: Fraction        # raw b per raw a, no fee
    fee: Fraction                 # fraction of input

    def has(self, t: str) -> bool:
        return t in (self.token_a, self.token_b)

    def other(self, t: str) -> str:
        return self.token_b if t == self.token_a else self.token_a

    def rate(self, token_in: str, token_out: str) -> Fraction:
        assert {token_in, token_out} == {self.token_a, self.token_b}
        r = self.spot_b_per_a if token_in == self.token_a else 1 / self.spot_b_per_a
        return r * (1 - self.fee)

    def reserve(self, t: str) -> int:
        return self.reserves[0] if t == self.token_a else self.reserves[1]


@dataclass(frozen=True)
class Route:
    venue: str
    hops: list[dict]
    rate: Fraction                # raw token out per yocto wNEAR in, after fees
    decimals: int
    depth_near: Fraction          # quote-side reserve of the main pool, in NEAR

    @property
    def price_near(self) -> Fraction:
        """Effective NEAR per whole token (fees included)."""
        return Fraction(10 ** self.decimals, 10 ** NEAR_DEC) / self.rate


def _dcl_pool(d: dict, pool_id: str) -> Pool | None:
    if not isinstance(d, dict) or d.get("state") != "Running":
        return None
    x, y, fee = d["token_x"], d["token_y"], int(d["fee"])
    if f"{x}|{y}|{fee}" != pool_id:
        return None
    spot = Fraction(Decimal("1.0001") ** int(d["current_point"]))
    return Pool("dcl", pool_id, x, y, (int(d.get("total_x", 0)), int(d.get("total_y", 0))), spot, Fraction(fee, 10 ** 6))


def _ref_pool(d: dict, pool_id: str) -> Pool | None:
    if not isinstance(d, dict) or d.get("pool_kind") != "SIMPLE_POOL":
        return None
    toks, amts = d.get("token_account_ids") or [], d.get("amounts") or []
    if len(toks) != 2 or len(amts) != 2:
        return None
    a, b = int(amts[0]), int(amts[1])
    if a <= 0 or b <= 0:
        return None
    return Pool("ref_v1", pool_id, toks[0], toks[1], (a, b), Fraction(b, a), Fraction(int(d["total_fee"]), 10_000))


def read_pool(rpc: Any, venue: str, pool_id: str) -> Pool | None:
    try:
        if venue == "dcl":
            return _dcl_pool(rpc.view(DCL, "get_pool", {"pool_id": pool_id}), pool_id)
        if venue == "ref_v1":
            return _ref_pool(rpc.view(REF_V1, "get_pool", {"pool_id": int(pool_id)}), pool_id)
    except (KeyError, TypeError, ValueError, RpcError):
        return None
    return None


def dcl_pool_id(a: str, b: str, fee: int) -> str:
    x, y = sorted((a, b))
    return f"{x}|{y}|{fee}"


def ref_bridge_ids() -> list[str]:
    """Ref v1 RHEA/wNEAR pool ids, from env REF_V1_RHEA_POOLS (comma list). No default: unverified ids."""
    return [s.strip() for s in os.environ.get("REF_V1_RHEA_POOLS", "").split(",") if s.strip()]


def bridge_pool(rpc: Any, venue: str) -> Pool | None:
    """Deepest RHEA/wNEAR pool on the venue."""
    ids = [dcl_pool_id(RHEA, WNEAR, f) for f in DCL_FEES] if venue == "dcl" else ref_bridge_ids()
    best = None
    for pid in ids:
        p = read_pool(rpc, venue, pid)
        ok = p and p.has(RHEA) and p.has(WNEAR) and p.reserve(WNEAR) > 0 and p.reserve(RHEA) > 0
        if p and ok and (best is None or p.reserve(WNEAR) > best.reserve(WNEAR)):
            best = p
    return best


def token_decimals(rpc: Any, token_id: str) -> int:
    d = rpc.view(token_id, "ft_metadata", {})
    dec = int(d["decimals"])
    if not 0 <= dec <= 32:
        raise NoRoute("bad decimals")
    return dec


def pick_route(rpc: Any, token_id: str, hints: list[tuple[str, str]], decimals: int | None = None) -> Route:
    """Pick the deepest valid main pool among hints [(venue, pool_id)], quote wNEAR or RHEA (then via RHEA/wNEAR)."""
    if token_id in QUOTES:
        raise NoRoute("quote token")
    bridges: dict[str, Pool | None] = {}
    best: Route | None = None
    dec = decimals
    for venue, pid in dict.fromkeys(hints):
        if venue not in VENUE_CONTRACT:
            continue
        p = read_pool(rpc, venue, str(pid))
        if not p or not p.has(token_id) or p.other(token_id) not in QUOTES or p.reserve(token_id) <= 0:
            continue
        q = p.other(token_id)
        if p.reserve(q) <= 0:
            continue
        main_hop = {"pool_id": p.pool_id, "token_in": q, "token_out": token_id}
        if q == WNEAR:
            hops, rate, depth = [main_hop], p.rate(WNEAR, token_id), Fraction(p.reserve(WNEAR), 10 ** NEAR_DEC)
        else:
            if venue not in bridges:
                bridges[venue] = bridge_pool(rpc, venue)
            b = bridges[venue]
            if b is None:
                continue
            hops = [{"pool_id": b.pool_id, "token_in": WNEAR, "token_out": RHEA}, main_hop]
            rate = b.rate(WNEAR, RHEA) * p.rate(RHEA, token_id)
            depth = Fraction(p.reserve(RHEA)) / b.rate(WNEAR, RHEA) / 10 ** NEAR_DEC
        if rate <= 0:
            continue
        if dec is None:
            dec = token_decimals(rpc, token_id)
        r = Route(venue, hops, rate, dec, depth)
        if best is None or r.depth_near > best.depth_near:
            best = r
    if best is None:
        raise NoRoute("no_route")
    return best


def _frac(x: float | str) -> Fraction:
    return Fraction(str(x))


def min_out(amount_in: int, route: Route, inputs: MinOutInputs) -> int:
    """Tightest floor of price guard (if set) and slippage (unless unlimited). Neither: 1.

    Raises LimitError("slippage_unset") if slippage is unset, LimitError("price_guard") if the pool
    price is already above the guard.
    """
    if amount_in <= 0:
        raise LimitError("bad_amount")
    s = inputs.slippage_bps
    floors = [1]
    if s is None:
        raise LimitError("slippage_unset")
    if s != "unlimited":
        bps = int(s)
        if bps < 0:
            raise LimitError("slippage_unset")
        floors.append(int(amount_in * route.rate / (1 + Fraction(bps, 10_000))))
    g, fill = inputs.price_guard_x, inputs.fill_price
    if g is not None:
        if not fill or fill <= 0:
            raise LimitError("no_fill_price")
        max_price = _frac(g) * _frac(fill)          # NEAR per whole token
        if max_price <= 0 or route.price_near > max_price:
            raise LimitError("price_guard")
        floors.append(int(Fraction(amount_in) * 10 ** route.decimals / (max_price * 10 ** NEAR_DEC)))
    return max(floors)
