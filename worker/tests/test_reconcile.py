"""Reconciler: stuck 'sent' rows are settled from the tx outcome by hash. Never resends."""
import json
import queue

import pytest

from copytrade_worker import executor as X
from copytrade_worker import keys as K
from copytrade_worker.db import claim_copy_buy, connect
from copytrade_worker.types import Hop, MinOutInputs, Plan

from .fakes import FakeRpc, event, outcome, swap_error

E24 = 10 ** 24
MEME, OWNER, VAULT, TXH = "meme.near", "alice.near", "alice-vault.near", "TxHashSent1"


@pytest.fixture
def env(tmp_path):
    db = connect(tmp_path / "t.db")
    ks = K.KeyStore(tmp_path)
    pk = ks.ensure(OWNER)
    db.execute("INSERT INTO users (owner_id, vault_id, bot_public_key, bot_on, created_at) VALUES (?,?,?,1,0)",
               (OWNER, VAULT, pk))
    db.execute("INSERT INTO signals (id, target_id, token_id, kind, tx_hash, seen_at, source) "
               "VALUES (1,'root.near',?,'swap','t1',0,'teardata'), (2,'root.near',?,'swap','t2',0,'teardata')",
               (MEME, MEME))
    db.execute("INSERT INTO copy_buys (owner_id, token_id, signal_id, status, amount_in, min_out, tx_hash,"
               " created_at, updated_at) VALUES (?,?,1,'sent',?, '1', ?, 0, 0)", (OWNER, MEME, str(10 * E24), TXH))
    rpc = FakeRpc()
    ex = X.Executor(db, rpc, ks, dry_run=False, sleep=lambda s: None, outcome_timeout_s=0)
    return db, rpc, ex


def row(db):
    return dict(db.execute("SELECT * FROM copy_buys WHERE owner_id=? AND token_id=?", (OWNER, MEME)).fetchone())


def acts(db):
    return [(k, json.loads(p)) for k, p in db.execute("SELECT kind, payload FROM activity ORDER BY id")]


def test_sent_row_settled_filled_by_hash(env):
    db, rpc, ex = env
    rpc.outcomes = [outcome([event("swap_result", {"token_out": MEME, "amount_out": "777"})])]
    assert ex.reconcile() == 1
    r = row(db)
    assert r["status"] == "filled" and r["amount_out"] == "777" and r["tx_hash"] == TXH and r["updated_at"] > 0
    assert rpc.status_calls == [(TXH, VAULT)] and rpc.sent == []
    assert acts(db) == [("buy_filled", {"token_id": MEME, "amount_in": str(10 * E24), "amount_out": "777",
                                        "tx_hash": TXH})]
    assert ex.reconcile() == 0 and len(rpc.status_calls) == 1   # settled rows are not looked up again


@pytest.mark.parametrize("res,reason", [
    (outcome(failure={"ActionError": "Smart contract panicked: paused"}), "paused"),
    (outcome(failure={"ActionError": {"kind": {"FunctionCallError": "x"}}}), "swap_rejected"),
    (swap_error("paused"), "paused"),
    (swap_error("amount_in above balance"), "balance_low"),
    (swap_error(f"vault not registered on {MEME}"), "not_registered"),
    (swap_error("vault not registered on wrap.near"), "not_registered"),
    (swap_error(f"ft_balance_of failed on {MEME}"), "balance_check_failed"),
    (swap_error("ft_balance_of failed on wrap.near"), "balance_check_failed"),
    (swap_error("swap failed, input refunded"), "swap_refunded"),
])
def test_sent_row_settled_failed_and_reusable(env, res, reason):
    db, rpc, ex = env
    rpc.outcomes = [res]
    assert ex.reconcile() == 1
    assert row(db)["status"] == "failed" and row(db)["reason"] == reason and rpc.sent == []
    assert acts(db) == [("buy_failed", {"token_id": MEME, "amount_in": str(10 * E24), "reason": reason,
                                        "tx_hash": TXH})]
    p = Plan(OWNER, VAULT, 2, MEME, "ref_v1", (Hop("7", "wrap.near", MEME),), str(E24),
             MinOutInputs(None, "unlimited", 0.001, None))
    assert claim_copy_buy(db, p)   # a failed row is reused by a later signal


@pytest.mark.parametrize("res", [
    None, outcome(), outcome([event("swap_result", {"amount_out": "junk"})]), {"status": {}},
    swap_error("output balance unknown"), swap_error("min_out"), swap_error("paused", emitter=MEME),
    outcome(failure={"ActionError": "x"}, failed_at="later"),
    outcome(failure={"ActionError": "x"}, failed_at="later", failed_executor="wrap.near"),
])
def test_unknown_outcome_stays_sent(env, res):
    """Not found yet, success without our event, junk amount, input maybe spent (output balance unknown, later
    receipt failed, forged event): can't tell. Leave 'sent', token stays blocked, never resent."""
    db, rpc, ex = env
    rpc.outcomes = [res]
    assert ex.reconcile() == 0
    assert row(db)["status"] == "sent" and acts(db) == [] and rpc.sent == []


def test_rpc_down_stays_sent(env):
    db, rpc, ex = env
    rpc.down = {"tx_status"}
    assert ex.reconcile() == 0 and row(db)["status"] == "sent"


def test_row_without_vault_or_hash_skipped(env):
    db, rpc, ex = env
    db.execute("UPDATE copy_buys SET tx_hash = NULL")
    assert ex.reconcile() == 0 and rpc.status_calls == []


def test_loop_reconciles_at_start(env):
    db, rpc, ex = env
    rpc.outcomes = [outcome([event("swap_result", {"token_out": MEME, "amount_out": "5"})])]
    q: queue.Queue = queue.Queue()
    q.put(None)
    ex.loop(q)
    assert row(db)["status"] == "filled" and rpc.sent == []


def test_dry_run_rows_untouched(env):
    db, rpc, ex = env
    db.execute("UPDATE copy_buys SET status = 'failed', reason = 'dry_run'")
    assert ex.reconcile() == 0 and rpc.status_calls == []
