"""Signal pipeline: feed data -> stored signal -> per-user plan or skip -> queue for the executor (0006).

Only executed swaps are signals. DCL limit buy orders (paid in wNEAR) by a copy target are never bought: they
write `limit_order` activity rows, stage 'placed' on `order_added` and 'filled' at the first bought amount
(order watch: list_active_orders poll and claim/cancel events). One row per (placing tx, token, stage, user), for
users with the target enabled, the bot on and `notify.limit_orders` not false. Tx-stage sightings are never copied.
Per user, `rules.plan` decides. A plan first claims the copy_buys row ('planned'); only then it is queued.
Skips go to `activity` (buy_skipped), except the quiet ones (bot off, target off for that user).
"""

from __future__ import annotations

import math
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
                self.on_order(raw)
                continue
            if raw.get("kind") == "order_fill":
                self.order_filled(raw)
                continue
            sid = self.handle(detect.to_signal(raw, source, self.clock()))
            if sid is not None:
                out.append(sid)
        return out

    def on_row(self, row: dict[str, Any], source: str, t_recv: float | None = None) -> int | None:
        raw = detect.ws_signal(row, self.targets())
        return self.handle(detect.to_signal(raw, source, self.clock())) if raw else None

    # ---------- limit orders ----------
    def on_order(self, raw: dict[str, Any]) -> list[int]:
        """A placed limit buy order by a copy target: watch it, alert stage 'placed'. Returns new activity ids."""
        o = detect.limit_order(raw)
        if o is None or not rules.is_meme(o["token_id"]) or o["target_id"] not in self.targets():
            return []
        self.watch(o["order_id"], o["target_id"], o["token_id"], o["pool_id"], o["tx_hash"])
        return self.alert("placed", o, o["amount_near"], self.point_price(o["token_id"], o["pool_id"], o["point"]))

    def watch(self, oid: str, target: str, token: str, pool: str, tx: str | None) -> None:
        with self.olock:
            if oid not in self.orders:
                self.orders[oid] = dict(target=target, token=token, pool=pool, tx=tx, t=time.time(), fired=False)
                self.log("order_seen", target=target, token=token, order_id=oid)

    def seed_orders(self) -> None:
        """At start: targets' open, unfilled wNEAR buy orders (placed before a restart) are watched again. Their
        placing tx comes from the stored 'placed' alert at fill time."""
        for target in self.targets():
            for r in self.chain.active_orders(target) or []:
                oid, tok, pool = r.get("order_id"), r.get("buy_token"), r.get("pool_id")
                if not (isinstance(oid, str) and isinstance(pool, str) and rules.is_meme(tok)):
                    continue
                if r.get("sell_token") == detect.WNEAR and not int(r.get("bought_amount") or 0):
                    self.watch(oid, target, tok, pool, None)

    def order_filled(self, fill: dict[str, Any]) -> list[int]:
        """First fill of a watched order -> stage 'filled' alerts. Unwatched or already fired: nothing."""
        with self.olock:
            v = self.orders.get(fill.get("order_id") or "")
            if not v or v["fired"] or v["target"] != fill.get("whale"):
                return []
            v["fired"] = True
        tx = v["tx"]
        if tx is None:
            with self.lock:
                tx = db.placed_order_tx(self.conn, v["target"], v["token"], v["pool"], self.clock() - WATCH_S * 1000)
        if tx is None:
            self.log("order_fill_untracked", target=v["target"], token=v["token"], order_id=fill.get("order_id"))
            return []
        sold, bought = detect._amount(fill.get("amount_in")), detect._amount(fill.get("amount_out"))
        if sold is None:
            return []
        o = dict(target_id=v["target"], token_id=v["token"], pool_id=v["pool"], tx_hash=tx)
        price = self.fill_price(v["token"], sold, bought) or self.point_price(v["token"], v["pool"], fill.get("point"))
        return self.alert("filled", o, sold, price)

    def alert(self, stage: str, o: dict[str, Any], amount_near: str, price: float | None) -> list[int]:
        payload: dict[str, Any] = dict(stage=stage, **{k: o[k] for k in ("target_id", "token_id", "pool_id")})
        payload.update(amount_near=amount_near, tx_hash=o["tx_hash"])
        if price is not None:
            payload["price"] = price
        with self.lock:
            ids = [
                i
                for owner in db.limit_order_owners(self.conn, o["target_id"])
                if (i := db.add_limit_order_alert(self.conn, owner, payload, self.clock())) is not None
            ]
        if ids:
            self.log("limit_order", stage=stage, target=o["target_id"], token=o["token_id"], users=len(ids))
        return ids

    def decimals(self, token: str) -> int | None:
        try:
            info = self.chain.tokens({token}) or {}
        except Exception as e:
            self.log("error", error=f"order decimals: {type(e).__name__}")
            return None
        dec = (info.get(token) or {}).get("decimals")
        return dec if isinstance(dec, int) and not isinstance(dec, bool) and 0 <= dec <= 40 else None

    def point_price(self, token: str, pool: str, point: Any) -> float | None:
        """NEAR per whole token at a DCL point, or None (no point, decimals unknown, chain down)."""
        if not isinstance(point, int) or isinstance(point, bool) or abs(point) > 800_000:
            return None
        dec = self.decimals(token)
        if dec is None:
            return None
        try:
            px = detect.point_price(point, pool, token, dec, 24)
        except (ValueError, OverflowError):
            return None
        return px if math.isfinite(px) and px > 0 else None

    def fill_price(self, token: str, sold: str, bought: str | None) -> float | None:
        """NEAR per whole token actually paid by the order so far, or None."""
        dec = self.decimals(token) if bought else None
        if dec is None or bought is None:
            return None
        return (int(sold) / 10**24) / (int(bought) / 10**dec)

    def poll_orders(self) -> list[int]:
        """One pass: list_active_orders per target; the first bought amount is the fill."""
        now = time.time()
        with self.olock:
            for oid in [k for k, v in self.orders.items() if now - v["t"] > WATCH_S or v["fired"]]:
                self.orders.pop(oid)
            by_target: dict[str, list[str]] = {}
            for oid, v in self.orders.items():
                by_target.setdefault(v["target"], []).append(oid)
        out = []
        for target, oids in by_target.items():
            rows = self.chain.active_orders(target)
            if rows is None:
                continue
            live = {r.get("order_id"): r for r in rows if isinstance(r, dict)}
            for oid in oids:
                f = detect.order_fill(live.get(oid), target)
                if f:
                    out += self.order_filled(f)
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
        self.log("signal", id=sid, target=s.target_id, token=s.token_id, signal_kind=s.kind, source=s.source)
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
