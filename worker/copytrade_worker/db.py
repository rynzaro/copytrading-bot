"""SQLite access. Applies the interface schema (docs/interfaces/db.sql, copied to schema.sql) on every open."""

from __future__ import annotations

import json
import pathlib
import sqlite3
import time
from typing import Any

from .types import Plan, Signal, User

SCHEMA_PATH = pathlib.Path(__file__).with_name("schema.sql")
IN_FLIGHT = ("planned", "sent")
BUSY_TIMEOUT_MS = 10_000


def now_ms() -> int:
    return int(time.time() * 1000)


def connect(path: str) -> sqlite3.Connection:
    """Open the DB, apply the schema (idempotent). Autocommit; one connection may be shared under a lock.
    Every connection sets WAL (web and worker parts write the same file) and a busy timeout."""
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode = WAL")  # ':memory:' stays 'memory'
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text())
    return conn


def _json(text: str | None) -> dict[str, Any]:
    try:
        d = json.loads(text or "{}")
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def insert_signal(conn: sqlite3.Connection, s: Signal) -> int | None:
    """Store a signal. Returns its id, or None when (tx_hash, token_id) is already stored."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO signals(target_id, token_id, kind, venue, pool_id, near_in, fill_price, tx_hash,"
        " seen_at, source)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (s.target_id, s.token_id, s.kind, s.venue, s.pool_id, s.near_in, s.fill_price, s.tx_hash, s.seen_at, s.source),
    )
    return cur.lastrowid if cur.rowcount == 1 else None


def last_signal_at(conn: sqlite3.Connection) -> int | None:
    r = conn.execute("SELECT max(seen_at) FROM signals").fetchone()
    return r[0]


def enabled_targets(conn: sqlite3.Connection) -> set[str]:
    """Copy targets enabled by at least one user whose bot is on."""
    rows = conn.execute(
        "SELECT DISTINCT t.target_id FROM copy_targets t JOIN users u ON u.owner_id = t.owner_id"
        " WHERE t.enabled = 1 AND u.bot_on = 1"
    )
    return {r[0] for r in rows}


def users_for_target(conn: sqlite3.Connection, target_id: str, token_id: str) -> list[User]:
    """Users that list this target (enabled or not), with their copy_buys status for the token."""
    rows = conn.execute(
        "SELECT u.owner_id, u.vault_id, u.bot_on, u.settings, t.enabled, b.status FROM copy_targets t"
        " JOIN users u ON u.owner_id = t.owner_id"
        " LEFT JOIN copy_buys b ON b.owner_id = u.owner_id AND b.token_id = ?"
        " WHERE t.target_id = ? ORDER BY u.owner_id",
        (token_id, target_id),
    )
    return [
        User(
            owner_id=r[0],
            vault_id=r[1],
            bot_on=bool(r[2]),
            settings=_json(r[3]),
            target_enabled=bool(r[4]),
            copy_buy_status=r[5],
        )
        for r in rows
    ]


def claim_copy_buy(conn: sqlite3.Connection, p: Plan, now: int | None = None) -> bool:
    """Write the 'planned' copy_buys row. False when the token is filled or in flight for this owner.
    A 'failed' row is reused (set back to 'planned')."""
    now = now_ms() if now is None else now
    cur = conn.execute(
        "INSERT INTO copy_buys(owner_id, token_id, signal_id, status, amount_in, created_at, updated_at)"
        " VALUES (?,?,?,'planned',?,?,?)"
        " ON CONFLICT(owner_id, token_id) DO UPDATE SET status='planned', signal_id=excluded.signal_id, reason=NULL,"
        " amount_in=excluded.amount_in, min_out=NULL, amount_out=NULL, tx_hash=NULL, updated_at=excluded.updated_at"
        " WHERE copy_buys.status = 'failed'",
        (p.owner_id, p.token_id, p.signal_id, p.amount_in, now, now),
    )
    return cur.rowcount == 1


def fail_stale_planned(conn: sqlite3.Connection, before_ms: int, now: int | None = None) -> int:
    """At start: 'planned' rows from before this process were never sent. Mark them failed so a later signal
    can reuse them. Returns the count."""
    now = now_ms() if now is None else now
    cur = conn.execute(
        "UPDATE copy_buys SET status='failed', reason='worker_restart', updated_at=?"
        " WHERE status='planned' AND updated_at < ?",
        (now, before_ms),
    )
    return cur.rowcount


def add_activity(
    conn: sqlite3.Connection, owner_id: str | None, kind: str, payload: dict[str, Any], now: int | None = None
) -> int | None:
    cur = conn.execute(
        "INSERT INTO activity(owner_id, kind, payload, created_at) VALUES (?,?,?,?)",
        (owner_id, kind, json.dumps(payload, separators=(",", ":")), now_ms() if now is None else now),
    )
    return cur.lastrowid


def limit_order_owners(conn: sqlite3.Connection, target_id: str) -> list[str]:
    """Owners alerted on a target's limit orders: target enabled, bot on (the feed watches only those), and
    settings.notify.limit_orders not false (default on)."""
    rows = conn.execute(
        "SELECT u.owner_id, u.settings FROM copy_targets t JOIN users u ON u.owner_id = t.owner_id"
        " WHERE t.target_id = ? AND t.enabled = 1 AND u.bot_on = 1 ORDER BY u.owner_id",
        (target_id,),
    )
    out = []
    for owner, settings in rows:
        n = _json(settings).get("notify")
        if not (isinstance(n, dict) and n.get("limit_orders") is False):
            out.append(owner)
    return out


def add_limit_order_alert(
    conn: sqlite3.Connection, owner_id: str, payload: dict[str, Any], now: int | None = None
) -> int | None:
    """Insert a `limit_order` activity row unless one exists for (owner, stage, order_id).
    Returns the new id, or None for a duplicate. Callers serialize writes (one lock)."""
    cur = conn.execute(
        "INSERT INTO activity(owner_id, kind, payload, created_at) SELECT ?, 'limit_order', ?, ?"
        " WHERE NOT EXISTS (SELECT 1 FROM activity WHERE owner_id = ? AND kind = 'limit_order'"
        " AND json_extract(payload, '$.stage') = ? AND json_extract(payload, '$.order_id') = ?)",
        (
            owner_id,
            json.dumps(payload, separators=(",", ":")),
            now_ms() if now is None else now,
            owner_id,
            payload["stage"],
            payload["order_id"],
        ),
    )
    return cur.lastrowid if cur.rowcount == 1 else None


def placed_order_tx(conn: sqlite3.Connection, order_id: str) -> str | None:
    """Placing tx from a stored 'placed' alert for this DCL order, or None."""
    r = conn.execute(
        "SELECT json_extract(payload, '$.tx_hash') FROM activity WHERE kind = 'limit_order'"
        " AND json_extract(payload, '$.stage') = 'placed' AND json_extract(payload, '$.order_id') = ?"
        " ORDER BY id DESC LIMIT 1",
        (order_id,),
    ).fetchone()
    return r[0] if r and isinstance(r[0], str) else None


def get_launch_tax(conn: sqlite3.Connection, token_id: str) -> dict[str, Any] | None:
    r = conn.execute(
        "SELECT token_id, code_hash, sniping_tax, checked_at FROM launch_tax WHERE token_id = ?", (token_id,)
    ).fetchone()
    if r is None:
        return None
    return {"token_id": r[0], "code_hash": r[1], "sniping_tax": bool(r[2]), "checked_at": r[3]}


def put_launch_tax(
    conn: sqlite3.Connection, token_id: str, code_hash: str | None, sniping_tax: bool, now: int | None = None
) -> None:
    conn.execute(
        "INSERT INTO launch_tax(token_id, code_hash, sniping_tax, checked_at) VALUES (?,?,?,?)"
        " ON CONFLICT(token_id) DO UPDATE SET code_hash=excluded.code_hash, sniping_tax=excluded.sniping_tax,"
        " checked_at=excluded.checked_at",
        (token_id, code_hash, int(bool(sniping_tax)), now_ms() if now is None else now),
    )
