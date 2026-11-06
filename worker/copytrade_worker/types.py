"""Plan: output of rules (0005), input of executor (0006).

Minimal version written by ticket 0006 to the ticket 0005 definition. Ticket 0005 owns this file.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MinOutInputs:
    price_guard_x: float | None   # optional: most the bot pays, as multiple of fill_price
    slippage_bps: int | str | None  # int bps, "unlimited", or None (unset: bot must not run)
    fill_price: float | None      # copy target's fill, NEAR per whole token
    pool_price: float | None      # pool price at plan time, NEAR per whole token


@dataclass(frozen=True)
class Plan:
    owner_id: str
    vault_id: str
    signal_id: int
    token_id: str
    venue: str                    # "dcl" | "ref_v1"
    hops: list[dict] = field(default_factory=list)  # [{"pool_id","token_in","token_out"}], vault.md Hop
    amount_in: str = "0"          # yocto wNEAR, decimal string
    min_out_inputs: MinOutInputs = field(default_factory=lambda: MinOutInputs(None, None, None, None))
