"""Worker wiring. `start` runs the signal side (DB, feeds, order watch, launch-tax watcher); `run` adds the
executor (consumes `pipeline.plans`), the internal API (127.0.0.1, /health from `Signals.health_json`) and the
notifier loop.

copy_buys ownership: `Pipeline.decide` writes the 'planned' row (`db.claim_copy_buy`) before it queues a Plan;
the executor only advances that row.

Entry point (DRY_RUN=1 default: signs, never sends):
    DATA_DIR=/tmp/ctw python -m copytrade_worker
Env: DATA_DIR, DRY_RUN, NEAR_RPC_URL, WORKER_PORT (API, default 8700), NTFY_URL, TELEGRAM_BOT_TOKEN, HOST_NTFY_TOPIC,
HOST_TELEGRAM_CHAT_ID.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any

from . import db, feeds, launch_tax
from .api import App, serve
from .chain import Chain
from .executor import Executor
from .keys import KeyStore
from .nearrpc import Rpc
from .notify.config import Config as NotifyConfig
from .notify.loop import Notifier
from .signals import Pipeline


def log(kind: str, /, **kw: Any) -> None:
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


@dataclass
class Worker:
    signals: Signals
    executor: Executor
    api: Any  # ThreadingHTTPServer
    notifier: Notifier

    def stop(self) -> None:
        self.signals.stop.set()
        self.signals.pipeline.plans.put(None)  # type: ignore[arg-type]  # executor loop sentinel
        self.api.shutdown()


def data_dir() -> str:
    d = os.environ.get("DATA_DIR") or "./data"
    os.makedirs(d, exist_ok=True)
    return d


def run(
    path: str | None = None,
    chain: Chain | None = None,
    rpc: Any = None,
    api_port: int | None = None,
    notify_config: NotifyConfig | None = None,
    stop: threading.Event | None = None,
) -> Worker:
    """Start everything in daemon threads. Each part gets its own connection to the same DB file."""
    data = data_dir()
    path = path or os.path.join(data, "worker.db")
    sig = start(db.connect(path), chain=chain, stop=stop)
    rpc = rpc or Rpc()
    ks = KeyStore(data)
    ex = Executor(db.connect(path), rpc, ks)
    log("executor_start", dry_run=ex.dry_run)
    threading.Thread(target=ex.loop, args=(sig.pipeline.plans,), daemon=True, name="executor").start()
    srv = serve(App(db.connect(path), rpc, ks, health_parts=sig.health_json), port=api_port)
    log("api_start", port=srv.server_address[1])
    n = Notifier(db.connect(path), notify_config or NotifyConfig.from_env())
    threading.Thread(target=n.run_forever, args=(sig.stop,), daemon=True, name="notify").start()
    return Worker(sig, ex, srv, n)


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    w = run()
    try:
        while True:
            time.sleep(30)
            log("health", **w.signals.health_json(), queued=w.signals.pipeline.plans.qsize())
    except KeyboardInterrupt:
        w.stop()


if __name__ == "__main__":
    main()
