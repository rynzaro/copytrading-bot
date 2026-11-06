"""Message texts. Payload data is untrusted (token ids come from chain): plain text only, cleaned, short."""

import math
from decimal import Decimal, InvalidOperation

MAX_LEN = 300
YOCTO = Decimal(10) ** 24


def clean(value, limit: int = 64) -> str:
    if value is None:
        return "?"
    s = str(value)
    # Drop control and bidi/format chars, collapse whitespace.
    out = "".join(" " if (c.isspace() or not c.isprintable()) else c for c in s)
    out = " ".join(out.split())
    return out[:limit] or "?"


def fmt_near(yocto) -> str | None:
    if not isinstance(yocto, (str, int)) or isinstance(yocto, bool):
        return None
    try:
        v = Decimal(str(yocto)) / YOCTO
    except InvalidOperation:
        return None
    if not v.is_finite() or v < 0:
        return None
    s = f"{v.quantize(Decimal('0.0001')):f}".rstrip("0").rstrip(".")
    return s or "0"


def _near(payload, key) -> str:
    n = fmt_near(payload.get(key))
    return f" for {n} NEAR" if n is not None else ""


def fmt_price(x) -> str | None:
    """NEAR per whole token, 3 significant digits after leading zeros. None unless a finite positive number."""
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or x <= 0 or x >= 1e12:
        return None
    s = f"{x:.12f}".rstrip("0").rstrip(".") if x < 1 else f"{x:,.4f}".rstrip("0").rstrip(".")
    if x < 1:
        head, frac = s.split(".") if "." in s else (s, "")
        z = len(frac) - len(frac.lstrip("0"))
        s = f"{head}.{frac[: z + 6]}".rstrip("0").rstrip(".")
    return s or None


def format_activity(kind: str, payload: dict) -> str:
    p = payload if isinstance(payload, dict) else {}
    tok = clean(p.get("token_id"))
    reason = clean(p.get("reason"), 48)
    tx = p.get("tx_hash")
    tx_s = f"\ntx {clean(tx, 64)}" if tx else ""
    if kind == "buy_filled":
        text = f"Bought {tok}{_near(p, 'amount_in')}{tx_s}"
    elif kind == "buy_failed":
        text = f"Copy buy failed: {tok} ({reason}){tx_s}"
    elif kind == "buy_skipped":
        text = f"Skipped {tok} ({reason})"
    elif kind == "storage_paid":
        n = fmt_near(p.get("amount"))
        text = f"Storage paid for {tok}" + (f": {n} NEAR" if n is not None else "")
    elif kind == "gas_80":
        text = "Gas allowance 80% used. Refill it in Settings, or the bot stops."
    elif kind == "still_active":
        text = f"Your bot key is still active on {clean(p.get('vault_id'))}. Pause in Settings if unwanted."
    elif kind == "sell":
        text = f"Sold {tok}{tx_s}"
    elif kind == "withdraw":
        text = f"Withdraw {tok}{tx_s}"
    elif kind == "limit_order":
        who = clean(p.get("target_id"))
        head = f"Limit buy by {who} filled" if p.get("stage") == "filled" else f"{who} placed a limit buy"
        px = fmt_price(p.get("price"))
        at = f" at {px} NEAR" if px is not None else ""
        text = f"{head}: {tok}{_near(p, 'amount_near')}{at}. No copy buy.{tx_s}"
    elif kind == "system":
        text = clean(p.get("text") or "System event", 200)
    else:
        text = f"Event: {clean(kind, 32)}"
    return text[:MAX_LEN]
