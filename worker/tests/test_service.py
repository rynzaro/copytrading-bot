"""Wiring: one signal flows pipeline -> executor (DRY_RUN) -> activity; /health answers. No network."""

import json
import threading
import time
import urllib.request

import pytest

from copytrade_worker import db, feeds, launch_tax, service
from copytrade_worker import route as R
from copytrade_worker.notify.config import Config
from copytrade_worker.types import Hop, MinOutInputs, Plan

from .fakes import FakeRpc, ref_pool
from .test_signals import GOOD, IMEME, SF, FakeChain, blocks

E24, E18 = 10**24, 10**18


@pytest.fixture
def worker(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.setattr(feeds, "start", lambda *a: feeds.Health(feeds.NAMES, 30, lambda *b: None))
    monkeypatch.setattr(launch_tax.Watcher, "run", lambda self, stop: None)
    rpc = FakeRpc()
    rpc.url = "http://rpc.test"
    w = service.run(chain=FakeChain(), rpc=rpc, api_port=0, notify_config=Config(interval_s=0.05))
    yield w, rpc, tmp_path
    w.stop()


def wait(cond, s=5.0):
    end = time.time() + s
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_signal_to_dry_run_executor(worker):
    w, rpc, tmp = worker
    assert w.executor.dry_run is True
    conn = db.connect(str(tmp / "worker.db"))
    pk = w.executor.ks.ensure("a.near")
    conn.execute(
        "insert into users(owner_id, vault_id, bot_public_key, bot_on, settings, created_at) values (?,?,?,1,?,0)",
        ("a.near", "v.a.near", pk, json.dumps(GOOD)),
    )
    conn.execute("insert into copy_targets values (?,?,1)", ("a.near", SF))
    # No pool in the fake RPC for the target's hop: executor fails with no_route, after the planned row.
    p = w.signals.pipeline
    p._targets = (0.0, set())  # drop the 5 s targets cache primed at start
    ids = [i for b in blocks("sf_imeme") for i in p.on_block(b, "teardata")]
    assert len(ids) == 1

    def row():
        r = conn.execute("select status, reason from copy_buys where token_id = ?", (IMEME,)).fetchone()
        return tuple(r) if r else None

    assert wait(lambda: row() == ("failed", "no_route"))
    kinds = [r[0] for r in conn.execute("select kind from activity where owner_id = 'a.near'")]
    assert kinds == ["buy_failed"] and rpc.sent == []


def test_dry_run_builds_swap_and_never_sends(worker):
    w, rpc, tmp = worker
    meme = "meme.near"
    rpc.ref[7] = ref_pool(R.WNEAR, meme, 1000 * E24, 10**6 * E18)
    rpc.storage[(meme, "v.a.near")] = {"total": "1", "available": "0"}
    conn = db.connect(str(tmp / "worker.db"))
    pk = w.executor.ks.ensure("a.near")
    conn.execute(
        "insert into users(owner_id, vault_id, bot_public_key, bot_on, created_at) values ('a.near','v.a.near',?,1,0)",
        (pk,),
    )
    conn.execute(
        "insert into signals(id, target_id, token_id, kind, tx_hash, seen_at, source)"
        " values (1, 't.near', ?, 'swap', 'h', 0, 'teardata')",
        (meme,),
    )
    plan = Plan("a.near", "v.a.near", 1, meme, "ref_v1", (Hop("7", R.WNEAR, meme),), str(E24),
                MinOutInputs(None, "unlimited", None, None))
    assert db.claim_copy_buy(conn, plan)
    w.signals.pipeline.plans.put(plan)
    assert wait(lambda: conn.execute("select reason from copy_buys").fetchone()[0] == "dry_run")
    assert rpc.sent == []


def test_health_and_notifier_running(worker):
    w, rpc, tmp = worker
    port = w.api.server_address[1]
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
        h = json.load(r)
    assert h["rpc"] == "http://rpc.test" and set(h["feeds"]) == set(feeds.NAMES) and h["last_signal_at"] is None
    alive = {t.name for t in threading.enumerate() if t.is_alive()}
    assert {"notify", "executor"} <= alive


LIVE_OK = {"DRY_RUN": "0", "VAULT_CODE_HASH": "8kPzWhcBXaQX3rUJzNJo2U8KcU9xUZoTT3qQKcVN3r3H",
           "RPC_URLS": "https://rpc.a,https://rpc.b", "DATA_DIR": "/data"}


def test_startup_checks_only_when_live():
    assert service.startup_errors({}) == []                       # DRY_RUN unset: dry run
    assert service.startup_errors({"DRY_RUN": "1"}) == []
    assert service.startup_errors(LIVE_OK) == []
    assert service.startup_errors({**LIVE_OK, "RPC_URLS": "", "NEAR_RPC_URL": "https://one"}) == []


@pytest.mark.parametrize("change,name", [
    ({"VAULT_CODE_HASH": ""}, "VAULT_CODE_HASH"),
    ({"VAULT_CODE_HASH": "not a hash!"}, "VAULT_CODE_HASH"),
    ({"RPC_URLS": " , "}, "RPC_URLS"),
    ({"RPC_URLS": "ftp://x"}, "RPC_URLS"),
    ({"DATA_DIR": ""}, "DATA_DIR"),
])
def test_startup_checks_name_what_is_missing(change, name):
    env = {**LIVE_OK, **change}
    errs = service.startup_errors(env)
    assert len(errs) == 1 and name in errs[0]
    for v in change.values():
        if v.strip():
            assert v not in errs[0]   # names only, never values


def test_run_refuses_live_without_config(tmp_path, monkeypatch):
    for k in ("VAULT_CODE_HASH", "RPC_URLS", "NEAR_RPC_URL", "DATA_DIR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DRY_RUN", "0")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as e:
        service.run(chain=FakeChain(), rpc=FakeRpc(), api_port=0)
    msg = str(e.value)
    assert "DRY_RUN=0" in msg and "VAULT_CODE_HASH" in msg and "RPC_URLS" in msg and "DATA_DIR" in msg
    assert not (tmp_path / "data").exists()   # refused before touching disk
