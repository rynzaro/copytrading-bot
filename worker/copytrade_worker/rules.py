"""Pure rules: meme filter (non-meme denylist) and per-user plan or skip. No network, no DB."""

from __future__ import annotations

import json
import pathlib
import re
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from typing import Any, Literal

from .types import Hop, MinOutInputs, Plan, Signal, Skip, User

HERE = pathlib.Path(__file__).parent
ACCOUNT = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
WNEAR = "wrap.near"
RHEA = "token.rhealab.near"
YOCTO = 10**24

DEFAULT_LADDER = [{"max_mcap_usd": 1_000_000, "size_near": "10"}, {"max_mcap_usd": 3_000_000, "size_near": "5"}]

# Skip reason codes, in check order. QUIET ones are not logged as activity (user not active for this target).
REASONS = (
    "bot_off",
    "no_vault",
    "no_slippage",
    "target_disabled",
    "bad_settings",
    "not_meme",
    "mcap_unknown",
    "mcap_below_min",
    "mcap_above_max",
    "already_bought",
    "sniping_tax",
)
QUIET = frozenset({"bot_off", "target_disabled"})


@lru_cache(maxsize=1)
def load_denylist() -> dict[str, Any]:
    d = json.loads((HERE / "denylist.json").read_text())
    p = json.loads((HERE / "launchpads.json").read_text())
    return dict(exact=d.get("exact", {}), suffix=d.get("suffix", {}), pads=tuple(p.get("suffix", [])))


def meme_rule(token: Any, deny: dict[str, Any] | None = None) -> tuple[bool, str]:
    """(is meme, rule that matched). Launchpad suffix -> meme; else the denylist decides."""
    deny = deny or load_denylist()
    if not isinstance(token, str) or not ACCOUNT.fullmatch(token) or len(token) > 64:
        return False, "bad token id"
    for suf in deny["pads"]:
        if token.endswith(suf):
            return True, f"launchpad {suf[1:]}"
    if token in deny["exact"]:
        return False, f"not a meme: {deny['exact'][token]}"
    for suf, why in deny["suffix"].items():
        if token.endswith(suf):
            return False, f"not a meme: {why}"
    return True, "not on denylist"


def is_meme(token: Any) -> bool:
    return meme_rule(token)[0]


def _num(x: Any) -> float | None:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    f = float(x)
    return f if f == f and abs(f) != float("inf") else None


def parse_ladder(raw: Any) -> list[tuple[float, int]] | None:
    """[(max_mcap_usd, size yocto)] ascending, or None when malformed."""
    if raw is None:
        raw = DEFAULT_LADDER
    if not isinstance(raw, list) or not raw:
        return None
    out = []
    for b in raw:
        if not isinstance(b, dict):
            return None
        m = _num(b.get("max_mcap_usd"))
        size = b.get("size_near")
        if m is None or m <= 0 or not isinstance(size, (str, int)) or isinstance(size, bool):
            return None
        try:
            d = Decimal(str(size))
        except InvalidOperation:
            return None
        if not d.is_finite() or d <= 0:
            return None
        y = int(d * YOCTO)
        if y <= 0:
            return None
        out.append((m, y))
    return sorted(out)


def size_for(mcap: float, ladder: list[tuple[float, int]]) -> int | None:
    """Band whose max is above mcap; mcap equal to the last max still counts. None: above the ladder."""
    for i, (m, y) in enumerate(ladder):
        if mcap < m or (i == len(ladder) - 1 and mcap == m):
            return y
    return None


def parse_slippage(x: Any) -> int | Literal["unlimited"] | None:
    if x == "unlimited":
        return "unlimited"
    n = _num(x)
    if n is None or n < 0 or n != int(n):
        return None
    return int(n)


def route_hint(s: Signal) -> tuple[str | None, tuple[Hop, ...]]:
    """The target's route if the vault would allow it for a buy, else (venue or None, ())."""
    hops = s.hops
    if s.venue not in ("dcl", "ref_v1") or not hops or not 1 <= len(hops) <= 3:
        return None, ()
    if hops[0].token_in != WNEAR or hops[-1].token_out != s.token_id:
        return None, ()
    for a, b in zip(hops, hops[1:], strict=False):
        if a.token_out != b.token_in or a.token_out not in (WNEAR, RHEA):
            return None, ()
    toks = [hops[0].token_in] + [h.token_out for h in hops]
    if len(set(toks)) != len(toks):
        return None, ()
    return s.venue, hops


def plan(signal: Signal, user: User) -> Plan | Skip:
    """Plan a copy buy for one user, or say why not. Checks in the order of REASONS."""
    st = user.settings if isinstance(user.settings, dict) else {}
    if not user.bot_on:
        return Skip("bot_off")
    if not user.vault_id:
        return Skip("no_vault")
    slip = parse_slippage(st.get("slippage_bps"))
    if slip is None:
        return Skip("no_slippage")
    if not user.target_enabled:
        return Skip("target_disabled")
    guard = st.get("price_guard_x")
    if guard is not None:
        guard = _num(guard)
        if guard is None or guard <= 0:
            return Skip("bad_settings", "price_guard_x")
    ladder = parse_ladder(st.get("ladder"))
    if ladder is None:
        return Skip("bad_settings", "ladder")
    min_mcap = _num(st.get("min_mcap_usd", 0))
    if min_mcap is None or min_mcap < 0:
        return Skip("bad_settings", "min_mcap_usd")
    ok, why = meme_rule(signal.token_id)
    if not ok:
        return Skip("not_meme", why)
    mcap = _num(signal.mcap_usd)
    if mcap is None or mcap <= 0:
        return Skip("mcap_unknown")
    if mcap < min_mcap:
        return Skip("mcap_below_min", f"{mcap:.0f}")
    size = size_for(mcap, ladder)
    if size is None:
        return Skip("mcap_above_max", f"{mcap:.0f}")
    if user.copy_buy_status in ("planned", "sent", "filled"):
        return Skip("already_bought", user.copy_buy_status)
    if signal.sniping_tax:
        return Skip("sniping_tax")
    if signal.id is None:
        raise ValueError("signal not stored")
    venue, hops = route_hint(signal)
    return Plan(
        owner_id=user.owner_id,
        vault_id=user.vault_id,
        signal_id=signal.id,
        token_id=signal.token_id,
        venue=venue,  # type: ignore[arg-type]
        hops=hops,
        amount_in=str(size),
        min_out_inputs=MinOutInputs(
            price_guard_x=guard, slippage_bps=slip, fill_price=signal.fill_price, pool_price=None
        ),
    )
