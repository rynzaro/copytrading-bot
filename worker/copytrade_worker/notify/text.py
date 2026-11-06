"""Message texts. Payload data is untrusted (token ids come from chain): plain text only, cleaned, short."""

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
    elif kind == "system":
        text = clean(p.get("text") or "System event", 200)
    else:
        text = f"Event: {clean(kind, 32)}"
    return text[:MAX_LEN]
