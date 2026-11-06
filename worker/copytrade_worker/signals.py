"""Signal pipeline: feed data -> stored signal -> per-user plan or skip -> queue for the executor (0006).

Only fills are signals: a swap fill, or a DCL limit order when it fills (watched after `order_added`,
polled with list_active_orders). Placed orders and tx-stage sightings are never copied.
Per user, `rules.plan` decides. A plan first claims the copy_buys row ('planned'); only then it is queued.
Skips go to `activity` (buy_skipped), except the quiet ones (bot off, target off for that user).
"""

from __future__ import annotations

import queue
import sqlite3
import threading
import time
from collections.abc import Callable
from typing import Any

from . import db, detect, launch_tax, rules
from .chain import market, num
from .types import Plan, Signal, Skip

WATCH_S = 86_400  # watch a placed limit order this long
ORDER_POLL_S = 3


class Pipeline:
    def __init__(
        self,
        conn: sqlite3.Connection,
        chain: Any,
        plans: queue.Queue[Plan] | None = None,
        lock: threading.Lock | None = None,
        log: Callable[..., None] = lambda *a, **k: None,
        clock: Callable[[], int] = db.now_ms,
    ):
        self.conn, self.chain, self.log, self.clock = conn, chain, log, clock
        self.plans: queue.Queue[Plan] = plans if plans is not None else queue.Queue()
        self.lock = lock or threading.Lock()  # guards conn
        self.olock = threading.Lock()
        self.orders: dict[str, dict[str, Any]] = {}  # order_id -> watched target order
        self._targets: tuple[float, set[str]] = (0.0, set())

    # ---------- targets ----------
    def targets(self) -> set[str]:
        """Copy targets enabled by any user with the bot on. Cached 5 s."""
        t, s = self._targets
        if time.monotonic() - t > 5:
            with self.lock:
                s = db.enabled_targets(self.conn)
            self._targets = (time.monotonic(), s)
        return s

    # ---------- feed callbacks ----------
    def on_block(self, block: dict[str, Any], source: str, t_recv: float | None = None) -> list[int]:
        out = []
        for raw in detect.block_signals(block, self.targets()):
            if raw.get("kind") == "order":
                if raw.get("stage") == "fill":
                    self.watch_order(raw)
                continue
            if raw.get("kind") == "order_fill":  # claim/cancel event: only a watched, unfired order counts
                sid = self.order_filled(raw, "teardata")
            else:
                sid = self.handle(detect.to_signal(raw, source, self.clock()))
            if sid is not None:
                out.append(sid)
        return out

    def on_row(self, row: dict[str, Any], source: str, t_recv: float | None = None) -> int | None:
        raw = detect.ws_signal(row, self.targets())
        return self.handle(detect.to_signal(raw, source, self.clock())) if raw else None

    # ---------- limit orders ----------
    def watch_order(self, raw: dict[str, Any], placed_tx: str | None = None) -> None:
        oid, tok = raw.get("order_id"), raw.get("token")
        if not isinstance(oid, str) or not rules.is_meme(tok):
            return
        with self.olock:
            if oid not in self.orders:
                self.orders[oid] = dict(
                    order_id=oid,
                    target=raw["whale"],
                    token=tok,
                    tx=placed_tx or raw.get("tx"),
                    t=time.time(),
                    fired=False,
                )
                self.log("order_seen", target=raw["whale"], token=tok, order_id=oid)

    def seed_orders(self) -> None:
        """Targets' open, unfilled orders at start are watched too (placed while the worker was down)."""
        for target in self.targets():
            for r in self.chain.active_orders(target) or []:
                if r.get("order_id") and not int(r.get("bought_amount") or 0):
                    self.watch_order(dict(order_id=r["order_id"], whale=target, token=r.get("buy_token")))

    def order_filled(self, fill: dict[str, Any], source: str) -> int | None:
        with self.olock:
            v = self.orders.get(fill.get("order_id") or "")
            if not v or v["fired"] or v["target"] != fill.get("whale"):
                return None
            v["fired"] = True
        tx = v["tx"] or f"order:{v['order_id']}"
        return self.handle(detect.to_signal(fill, source, self.clock(), tx_hash=tx))

    def poll_orders(self) -> list[int]:
        """One pass: list_active_orders per target; the first bought amount is the fill."""
        now = time.time()
        with self.olock:
            for oid in [k for k, v in self.orders.items() if now - v["t"] > WATCH_S or v["fired"]]:
                self.orders.pop(oid)
            by_target: dict[str, list[dict[str, Any]]] = {}
            for v in self.orders.values():
                by_target.setdefault(v["target"], []).append(v)
        out = []
        for target, ws in by_target.items():
            rows = self.chain.active_orders(target)
            if rows is None:
                continue
            live = {r.get("order_id"): r for r in rows}
            for v in ws:
                f = detect.order_fill(live.get(v["order_id"]), target)
                if f:
                    sid = self.order_filled(f, "order_watch")
                    if sid is not None:
                        out.append(sid)
        return out

    def order_loop(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                if self.orders:
                    self.poll_orders()
            except Exception as e:
                self.log("error", error=f"order poll: {type(e).__name__}")
            stop.wait(ORDER_POLL_S)

    # ---------- per signal ----------
    def enrich(self, s: Signal) -> None:
        """Market data and the sniping-tax cache at signal time. Unknown stays None (rules skip on unknown mcap)."""
        info = self.chain.tokens({s.token_id, detect.WNEAR}) or {}
        near_usd = num((info.get(detect.WNEAR) or {}).get("price_usd"))
        s.mcap_usd, s.created_ms, dec = market(info.get(s.token_id), near_usd)
        s.fill_price = detect.fill_price(s, dec)
        with self.lock:
            cached = db.get_launch_tax(self.conn, s.token_id)
        s.sniping_tax = launch_tax.hit(cached, s.created_ms, self.clock())

    def handle(self, s: Signal | None) -> int | None:
        """Store a new meme-buy signal and plan it for every user listing the target. Returns the signal id,
        or None (not a meme buy, duplicate)."""
        if s is None or not rules.is_meme(s.token_id) or s.target_id not in self.targets():
            return None
        with self.lock:  # cheap dedupe before network
            dup = self.conn.execute(
                "SELECT 1 FROM signals WHERE tx_hash=? AND token_id=?", (s.tx_hash, s.token_id)
            ).fetchone()
        if dup:
            return None
        try:
            self.enrich(s)
        except Exception as e:
            self.log("error", error=f"enrich: {type(e).__name__}")
        with self.lock:
            sid = db.insert_signal(self.conn, s)
            if sid is None:
                return None
            s.id = sid
            users = db.users_for_target(self.conn, s.target_id, s.token_id)
        self.log("signal", id=sid, target=s.target_id, token=s.token_id, kind=s.kind, source=s.source)
        for u in users:
            self.decide(s, u)
        return sid

    def decide(self, s: Signal, u: Any) -> Plan | Skip:
        r = rules.plan(s, u)
        if isinstance(r, Plan):
            with self.lock:
                claimed = db.claim_copy_buy(self.conn, r, self.clock())
            if claimed:
                self.plans.put(r)
                return r
            r = Skip("already_bought", "claimed")
        if r.reason not in rules.QUIET:
            with self.lock:
                db.add_activity(
                    self.conn, u.owner_id, "buy_skipped", {"token_id": s.token_id, "reason": r.reason}, self.clock()
                )
        return r
