"""Wiring for the signal side: DB, feeds, order watch, launch-tax watcher. The executor (0006) reads `pipeline.plans`.

Live smoke run (read-only, no executor, slow public reads):
    DATA_DIR=/tmp/ctw python -m copytrade_worker.service
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any

from . import db, feeds, launch_tax
from .chain import Chain
from .signals import Pipeline


def log(kind: str, **kw: Any) -> None:
    """One JSON line to stderr. Callers never pass key material or secrets."""
    print(json.dumps(dict(t=round(time.time(), 3), kind=kind, **kw), default=str), file=sys.stderr, flush=True)


@dataclass
class Signals:
    pipeline: Pipeline
    health: feeds.Health
    stop: threading.Event

    def health_json(self) -> dict[str, Any]:
        """Parts of GET /health this side owns: feeds and last_signal_at."""
        with self.pipeline.lock:
            last = db.last_signal_at(self.pipeline.conn)
        return {"feeds": self.health.snapshot(), "last_signal_at": last}


def start(conn: Any = None, chain: Chain | None = None, stop: threading.Event | None = None) -> Signals:
    data = os.environ.get("DATA_DIR") or "./data"
    if conn is None:
        os.makedirs(data, exist_ok=True)
        conn = db.connect(os.path.join(data, "worker.db"))
    chain = chain or Chain()
    stop = stop or threading.Event()
    n = db.fail_stale_planned(conn, before_ms=db.now_ms())
    if n:
        log("stale_planned_failed", n=n)
    p = Pipeline(conn, chain, log=log)
    try:
        p.seed_orders()
    except Exception as e:
        log("error", error=f"order seed: {type(e).__name__}")
    threading.Thread(target=p.order_loop, args=(stop,), daemon=True, name="orders").start()
    w = launch_tax.Watcher(conn, chain, lock=p.lock, log=log)
    threading.Thread(target=w.run, args=(stop,), daemon=True, name="launch-tax").start()

    def notify(feed: str, down: bool, age: float) -> None:
        log("feed_down" if down else "feed_up", feed=feed, age_s=round(age, 1))

    health = feeds.start(p.targets, p.on_block, p.on_row, notify, log, stop)
    return Signals(p, health, stop)


if __name__ == "__main__":
    s = start()
    try:
        while True:
            time.sleep(30)
            log("health", **s.health_json(), queued=s.pipeline.plans.qsize())
    except KeyboardInterrupt:
        s.stop.set()
