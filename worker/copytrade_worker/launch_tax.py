"""Sniping-tax watcher. Polls new JustHoot and Nearly launches (Beak tp/snipe/launches.py), reads each new token's
code once ('sniping_tax' in the wasm) and caches the answer in `launch_tax`.
At signal time (`hit`): cached tax on a token at most 10 min old -> skip. Not cached, or older -> no check."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from typing import Any

from . import db

PARENTS = ("launchpad.justhoot.near", "v2-launchpad.justhoot.near", "nearlytrade.near")
WINDOW_MS = 10 * 60_000
POLL_S = 10
CHECKS_PER_CYCLE = 6  # free RPCs: keep it slow
MAX_TRIES = 3


def hit(row: dict[str, Any] | None, created_ms: int | None, now: int) -> bool:
    """True when the cached check found sniping_tax and the token is at most 10 min old.
    Age from created_ms; unknown -> from checked_at (checked right after launch), so a stale row expires too."""
    if not row or not row.get("sniping_tax"):
        return False
    born = created_ms if created_ms else row.get("checked_at")
    return isinstance(born, int) and now - born <= WINDOW_MS


class Watcher:
    def __init__(
        self,
        conn: sqlite3.Connection,
        chain: Any,
        lock: threading.Lock | None = None,
        log: Callable[..., None] = lambda *a, **k: None,
    ):
        self.conn, self.chain, self.log = conn, chain, log
        self.lock = lock or threading.Lock()
        self.tries: dict[str, int] = {}

    def cycle(self, now: int | None = None) -> int:
        """One poll: list launches of the last 10 min, check uncached ones. Returns checks written."""
        now = db.now_ms() if now is None else now
        rows = self.chain.launches(PARENTS, now - WINDOW_MS)
        todo = []
        for r in rows:
            t = r["account_id"]
            with self.lock:
                cached = db.get_launch_tax(self.conn, t)
            if cached is None and self.tries.get(t, 0) < MAX_TRIES:
                todo.append(t)
        n = 0
        for t in todo[:CHECKS_PER_CYCLE]:
            c = self.chain.code_check(t)
            if c is None:
                self.tries[t] = self.tries.get(t, 0) + 1
                continue
            with self.lock:
                db.put_launch_tax(self.conn, t, c.get("code_hash"), bool(c.get("sniping_tax")), now)
            self.tries.pop(t, None)
            n += 1
            if c.get("sniping_tax"):
                self.log("sniping_tax", token=t)
        live = {r["account_id"] for r in rows}
        self.tries = {k: v for k, v in self.tries.items() if k in live}
        return n

    def run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.cycle()
            except Exception as e:  # network or shape: try again next cycle
                self.log("launch_tax_error", error=type(e).__name__)
            stop.wait(POLL_S)
