import json
import logging

import pytest

from copytrade_worker import executor as X
from copytrade_worker import keys as K
from copytrade_worker import route as R
from copytrade_worker.db import claim_copy_buy, connect
from copytrade_worker.types import Hop, MinOutInputs, Plan

from .fakes import FakeRpc, event, outcome, ref_pool

E24, E18 = 10 ** 24, 10 ** 18
MEME, OWNER, VAULT = "meme.near", "alice.near", "alice-vault.near"


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
    rpc = FakeRpc()
    rpc.ref[7] = ref_pool(R.WNEAR, MEME, 1000 * E24, 10 ** 6 * E18, fee=30)   # ~0.001 NEAR/MEME
    rpc.storage[(MEME, VAULT)] = {"total": "1", "available": "0"}
    ex = X.Executor(db, rpc, ks, dry_run=False, sleep=lambda s: None, outcome_timeout_s=0)
    return db, rpc, ks, ex


def plan(signal_id=1, guard=None, slip="unlimited", fill=0.001, venue="ref_v1", hops=None):
    hops = (Hop("7", R.WNEAR, MEME),) if hops is None else hops
    return Plan(OWNER, VAULT, signal_id, MEME, venue, hops, str(10 * E24), MinOutInputs(guard, slip, fill, None))


def go(ex, p):
    """What the pipeline does: claim the row (db.claim_copy_buy), then the executor runs the plan."""
    claim_copy_buy(ex.db, p)
    return ex.run(p)


def row(db):
    return dict(db.execute("SELECT * FROM copy_buys WHERE owner_id=? AND token_id=?", (OWNER, MEME)).fetchone())


def acts(db, kind=None):
    q = "SELECT kind, payload FROM activity" + (" WHERE kind = ?" if kind else "") + " ORDER BY id"
    return [(k, json.loads(p)) for k, p in db.execute(q, (kind,) if kind else ())]


def swap_args(sent):
    raw = sent["raw"]
    i = raw.index(b"swap") + 4
    n = int.from_bytes(raw[i:i + 4], "little")
    return json.loads(raw[i + 4:i + 4 + n])


def filled(amount="9000"):
    return outcome([event("swap_result", {"token_out": MEME, "amount_out": amount})])


def test_fill_happy_path(env):
    db, rpc, ks, ex = env
    rpc.outcomes = [filled("9969999")]
    assert go(ex, plan()) == "filled"
    r = row(db)
    assert r["status"] == "filled" and r["amount_out"] == "9969999" and r["min_out"] == "1" and r["tx_hash"]
    a = swap_args(rpc.sent[0])["args"]
    assert a == {"venue": "ref_v1", "hops": [{"pool_id": "7", "token_in": R.WNEAR, "token_out": MEME}],
                 "amount_in": str(10 * E24), "min_out": "1"}
    assert acts(db) == [("buy_filled", {"token_id": MEME, "amount_in": str(10 * E24), "amount_out": "9969999",
                                        "tx_hash": r["tx_hash"]})]
    assert ks.next_nonce(ks.load(OWNER).public_key, 0) == 102   # chain nonce 100 -> used 101


def test_register_token_first_logs_storage_paid(env):
    db, rpc, ks, ex = env
    rpc.storage[(MEME, VAULT)] = None
    rpc.outcomes = [outcome([event("token_registered", {"token_id": MEME, "deposit": "1250000000000000000000"})]),
                    filled()]
    assert go(ex, plan()) == "filled"
    assert len(rpc.sent) == 2 and b"register_token" in rpc.sent[0]["raw"]
    assert acts(db)[0] == ("storage_paid", {"token_id": MEME, "amount": "1250000000000000000000"})


def test_register_failure_marks_failed(env):
    db, rpc, ks, ex = env
    rpc.storage[(MEME, VAULT)] = None
    rpc.outcomes = [outcome(failure={"ActionError": "x"})]
    assert go(ex, plan()) == "failed"
    assert row(db)["reason"] == "register_failed" and len(rpc.sent) == 1


def test_retry_then_fill(env):
    db, rpc, ks, ex = env
    rpc.outcomes = [outcome([event("swap_result", {"token_out": MEME, "error": "min_out"})]), filled()]
    assert go(ex, plan(slip=100)) == "filled"
    assert len(rpc.sent) == 2


def test_retry_exhausted_marks_failed(env):
    db, rpc, ks, ex = env
    rpc.outcomes = [outcome(failure={"x": 1})] * 3
    assert go(ex, plan(slip=100)) == "failed"
    assert len(rpc.sent) == 3
    r = row(db)
    assert r["status"] == "failed" and r["reason"] == "swap_failed"
    assert acts(db, "buy_failed")[0][1] == {"token_id": MEME, "amount_in": str(10 * E24), "reason": "swap_failed",
                                            "tx_hash": r["tx_hash"]}


def test_retry_stops_when_price_leaves_guard(env):
    db, rpc, ks, ex = env

    def pump(r):  # after the first failed attempt the pool price jumps 3x
        r.ref[7] = ref_pool(R.WNEAR, MEME, 3000 * E24, 10 ** 6 * E18 // 1, fee=30)
        return outcome(failure={"x": 1})
    rpc.outcomes = [pump]
    assert go(ex, plan(guard=2.0)) == "failed"
    assert len(rpc.sent) == 1 and row(db)["reason"] == "price_guard"


def test_price_guard_blocks_before_send(env):
    db, rpc, ks, ex = env
    assert go(ex, plan(guard=1.0, fill=0.0005)) == "failed"
    assert rpc.sent == [] and row(db)["reason"] == "price_guard"


def test_unknown_outcome_not_resent_stays_sent(env):
    db, rpc, ks, ex = env
    rpc.outcomes = [None]
    assert go(ex, plan()) == "sent"
    assert len(rpc.sent) == 1 and row(db)["status"] == "sent"
    assert acts(db, "buy_failed") == []
    assert go(ex, plan(signal_id=2)) == "duplicate"   # in flight blocks


def test_one_buy_per_token_and_failed_row_reused(env):
    db, rpc, ks, ex = env
    rpc.outcomes = [outcome(failure={"x": 1})] * 3 + [filled()]
    assert go(ex, plan()) == "failed"
    assert go(ex, plan(signal_id=2)) == "filled"
    assert row(db)["signal_id"] == 2
    assert go(ex, plan(signal_id=1)) == "duplicate"
    assert len(rpc.sent) == 4


def test_send_rejected_allowance_no_retry(env):
    db, rpc, ks, ex = env
    err = {"InvalidTxError": {"InvalidAccessKeyError": {"NotEnoughAllowance": {}}}}
    rpc.send_errors = [{"data": {"TxExecutionError": err}}]
    assert go(ex, plan()) == "failed"
    assert row(db)["reason"] == "gas_allowance"


def test_send_rejected_retried(env):
    db, rpc, ks, ex = env
    rpc.send_errors = [{"name": "TIMEOUT"}]
    rpc.outcomes = [filled()]
    assert go(ex, plan()) == "filled"


def test_gas_80_written_once_and_rearmed(env):
    db, rpc, ks, ex = env
    rpc.access["permission"]["FunctionCall"]["allowance"] = str(E24 // 10)   # 90% used
    pk = ks.load(OWNER).public_key
    ex.check_gas(OWNER, VAULT, pk)
    ex.check_gas(OWNER, VAULT, pk)
    assert acts(db, "gas_80") == [("gas_80", {"vault_id": VAULT})]
    rpc.access["permission"]["FunctionCall"]["allowance"] = str(E24)       # refill seen
    ex.check_gas(OWNER, VAULT, pk)
    rpc.access["permission"]["FunctionCall"]["allowance"] = str(E24 // 10)
    ex.check_gas(OWNER, VAULT, pk)
    assert len(acts(db, "gas_80")) == 2


def test_dry_run_signs_not_sends_no_key_in_log(env, caplog):
    db, rpc, ks, _ = env
    rpc.storage[(MEME, VAULT)] = None
    ex = X.Executor(db, rpc, ks, sleep=lambda s: None)   # DRY_RUN default
    assert ex.dry_run is True
    caplog.set_level(logging.DEBUG)
    assert go(ex, plan()) == "failed"
    assert rpc.sent == []
    assert row(db)["reason"] == "dry_run"
    assert "would send" in caplog.text and "register_token" in caplog.text
    priv = json.loads((ks.dir / f"{OWNER}.json").read_text())["private_key"].split(":")[1]
    assert priv not in caplog.text
    assert acts(db, "buy_failed") == [] and acts(db, "system")


def test_bot_key_mismatch(env):
    db, rpc, ks, ex = env
    db.execute("UPDATE users SET bot_public_key = 'ed25519:other'")
    assert go(ex, plan()) == "failed" and row(db)["reason"] == "bot_key_mismatch" and rpc.sent == []


def test_events_ignores_foreign_standard_and_junk():
    o = {"receipts_outcome": [{"outcome": {"logs": [
        "EVENT_JSON:{bad", 'EVENT_JSON:{"standard":"other","event":"swap_result","data":[{"amount_out":"9"}]}',
        event("swap_result", {"amount_out": "5"}), 7]}}]}
    assert X.events(o) == [{"event": "swap_result", "amount_out": "5"}]


def test_loop_consumes_queue(env):
    import queue
    db, rpc, ks, ex = env
    rpc.outcomes = [filled()]
    q = queue.Queue()
    p = plan()
    claim_copy_buy(db, p)
    q.put(p)
    q.put(None)
    ex.loop(q)
    assert row(db)["status"] == "filled"


def test_success_without_event_not_resent(env):
    db, rpc, ks, ex = env
    rpc.outcomes = [outcome()]
    assert go(ex, plan()) == "sent" and len(rpc.sent) == 1


def test_unclaimed_plan_not_run_and_no_row_written(env):
    db, rpc, ks, ex = env
    assert ex.run(plan()) == "duplicate"
    assert db.execute("SELECT count(*) FROM copy_buys").fetchone()[0] == 0 and rpc.sent == []


def test_no_target_route_falls_back_to_dcl_pools(env):
    from .fakes import dcl_pool
    db, rpc, ks, ex = env
    pid = R.dcl_pool_id(MEME, R.WNEAR, 2000)
    rpc.dcl[pid] = dcl_pool(MEME, R.WNEAR, 2000, 0)
    rpc.outcomes = [filled()]
    assert go(ex, plan(venue=None, hops=())) == "filled"
    a = swap_args(rpc.sent[0])["args"]
    assert a["venue"] == "dcl" and a["hops"] == [{"pool_id": pid, "token_in": R.WNEAR, "token_out": MEME}]
