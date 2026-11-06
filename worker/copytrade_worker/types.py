"""Shared worker types. `Plan` is the seam from signals (0005) to the executor (0006)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Venue = Literal["dcl", "ref_v1"]
SignalKind = Literal["swap"]  # only executed swaps become signals


@dataclass(frozen=True)
class Hop:
    """One hop of a vault route (docs/interfaces/vault.md `Hop`)."""

    pool_id: str
    token_in: str
    token_out: str

    def to_json(self) -> dict[str, str]:
        return {"pool_id": self.pool_id, "token_in": self.token_in, "token_out": self.token_out}


@dataclass(frozen=True)
class MinOutInputs:
    """Inputs the executor needs for `min_out`. None means not set / not known."""

    price_guard_x: float | None  # most paid, as multiple of the target's fill price
    slippage_bps: int | Literal["unlimited"]  # most paid above pool price; required
    fill_price: float | None  # NEAR per whole token the target paid
    pool_price: float | None  # NEAR per whole token in the pool now; None: executor reads it


@dataclass(frozen=True)
class Plan:
    """One planned copy buy. A `copy_buys` row with status 'planned' exists for (owner_id, token_id).

    venue/hops are the target's route when it is one the vault allows (first token_in wrap.near,
    intermediates wrap.near or RHEA). Empty hops / venue None: the executor picks the route (route.py).
    """

    owner_id: str
    vault_id: str
    signal_id: int
    token_id: str
    venue: Venue | None
    hops: tuple[Hop, ...]
    amount_in: str  # yocto wNEAR, decimal string
    min_out_inputs: MinOutInputs


@dataclass(frozen=True)
class Skip:
    """Why a signal is not copied for one user. `reason` is a stable code (see rules.REASONS)."""

    reason: str
    detail: str | None = None


@dataclass
class Signal:
    """One meme buy by a copy target (a `signals` row). id is None until stored."""

    target_id: str
    token_id: str
    kind: SignalKind
    tx_hash: str
    source: str  # 'teardata' | 'intear_ws'
    seen_at: int  # unix ms
    venue: str | None = None  # 'dcl' | 'ref_v1' | other
    pool_id: str | None = None
    near_in: str | None = None  # yocto wNEAR spent by the target, when paid in wNEAR
    fill_price: float | None = None  # NEAR per whole token
    hops: tuple[Hop, ...] = ()
    token_in: str | None = None
    amount_in: str | None = None
    amount_out: str | None = None
    # Market data at signal time (not stored in `signals`):
    mcap_usd: float | None = None
    created_ms: int | None = None
    sniping_tax: bool = False  # launch_tax cache hit with tax on a token <= 10 min old
    id: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class User:
    """A user as rules see it, loaded for one (target, token)."""

    owner_id: str
    vault_id: str | None
    bot_on: bool
    settings: dict[str, Any]
    target_enabled: bool
    copy_buy_status: str | None = None  # status of copy_buys row for this token, if any
