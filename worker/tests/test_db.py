import json
import pathlib

import pytest

from copytrade_worker import db
from copytrade_worker.types import MinOutInputs, Plan, Signal

REPO_SCHEMA = pathlib.Path(__file__).resolve().parents[2] / "docs" / "interfaces" / "db.sql"


def sig(**kw):
    d = dict(
        target_id="root.near", token_id="a.meme-cooking.near", kind="swap", tx_hash="T1", source="teardata", seen_at=1
    )
    d.update(kw)
    return Signal(**d)


def plan(owner="o.near", token="a.meme-cooking.near", sid=1):
    return Plan(owner, "v." + owner, sid, token, None, (), "10", MinOutInputs(None, 100, None, None))


@pytest.fixture
def conn():
    return db.connect(":memory:")


def test_package_schema_is_interface_copy():
    if not REPO_SCHEMA.exists():
        pytest.skip("not in repo checkout")
    assert db.SCHEMA_PATH.read_text() == REPO_SCHEMA.read_text()


def test_connect_applies_schema_twice(tmp_path):
    p = tmp_path / "w.db"
    db.connect(str(p)).close()
    c = db.connect(str(p))
    names = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
    assert {"users", "copy_targets", "signals", "copy_buys", "launch_tax", "activity"} <= names
    assert c.execute("pragma journal_mode").fetchone()[0] == "wal"


def test_insert_signal_dedupes_on_tx_and_token(conn):
    a = db.insert_signal(conn, sig())
    assert a is not None
    assert db.insert_signal(conn, sig(source="intear_ws")) is None
    assert db.insert_signal(conn, sig(token_id="b.meme-cooking.near")) not in (None, a)
    assert conn.execute("select count(*) from signals").fetchone()[0] == 2
    assert db.last_signal_at(conn) == 1


def add_user(conn, owner, target="root.near", enabled=1, bot_on=1, vault=True, settings=None):
    conn.execute(
        "insert into users(owner_id, vault_id, bot_on, settings, created_at) values (?,?,?,?,0)",
        (owner, "v." + owner if vault else None, bot_on, json.dumps(settings or {})),
    )
    conn.execute("insert into copy_targets values (?,?,?)", (owner, target, enabled))


def test_enabled_targets_and_users_for_target(conn):
    add_user(conn, "a.near")
    add_user(conn, "b.near", enabled=0)
    add_user(conn, "c.near", target="other.near")
    assert db.enabled_targets(conn) == {"root.near", "other.near"}
    us = {u.owner_id: u for u in db.users_for_target(conn, "root.near", "t.near")}
    assert set(us) == {"a.near", "b.near"}
    assert us["a.near"].target_enabled and not us["b.near"].target_enabled
    assert us["a.near"].copy_buy_status is None


def test_enabled_targets_ignores_bot_off(conn):
    add_user(conn, "a.near", bot_on=0)
    assert db.enabled_targets(conn) == set()


def test_bad_settings_json_reads_empty(conn):
    add_user(conn, "a.near")
    conn.execute("update users set settings='not json'")
    assert db.users_for_target(conn, "root.near", "t")[0].settings == {}


def test_claim_once_per_token_reuses_failed(conn):
    sid = db.insert_signal(conn, sig())
    p = plan(sid=sid)
    assert db.claim_copy_buy(conn, p, now=5)
    assert not db.claim_copy_buy(conn, p, now=6)  # planned blocks
    conn.execute("update copy_buys set status='failed', reason='price_guard'")
    assert db.claim_copy_buy(conn, p, now=7)  # failed is reused
    row = conn.execute("select status, reason, amount_in, updated_at from copy_buys").fetchone()
    assert tuple(row) == ("planned", None, "10", 7)
    conn.execute("update copy_buys set status='filled'")
    assert not db.claim_copy_buy(conn, p, now=8)
    add_user(conn, "o.near")
    assert db.users_for_target(conn, "root.near", p.token_id)[0].copy_buy_status == "filled"


def test_fail_stale_planned(conn):
    sid = db.insert_signal(conn, sig())
    db.claim_copy_buy(conn, plan(sid=sid), now=5)
    db.claim_copy_buy(conn, plan(owner="p.near", sid=sid), now=50)
    assert db.fail_stale_planned(conn, before_ms=10, now=60) == 1
    rows = dict(conn.execute("select owner_id, status from copy_buys").fetchall())
    assert rows == {"o.near": "failed", "p.near": "planned"}


def test_activity_and_launch_tax(conn):
    db.add_activity(conn, "o.near", "buy_skipped", {"reason": "mcap_unknown"}, now=3)
    r = conn.execute("select owner_id, kind, payload, created_at, notified_at from activity").fetchone()
    assert (r[0], r[1], json.loads(r[2]), r[3], r[4]) == ("o.near", "buy_skipped", {"reason": "mcap_unknown"}, 3, None)
    assert db.get_launch_tax(conn, "t") is None
    db.put_launch_tax(conn, "t", "hash", True, now=9)
    assert db.get_launch_tax(conn, "t") == {"token_id": "t", "code_hash": "hash", "sniping_tax": True, "checked_at": 9}


def test_every_connection_wal_and_busy_timeout(tmp_path):
    p = str(tmp_path / "w.db")
    for _ in range(2):   # first open creates the file, second opens an existing one
        c = db.connect(p)
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert c.execute("PRAGMA busy_timeout").fetchone()[0] == db.BUSY_TIMEOUT_MS
        assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_wal_lets_reader_see_writer_without_lock(tmp_path):
    p = str(tmp_path / "w.db")
    w, r = db.connect(p), db.connect(p)
    w.execute("BEGIN")
    w.execute("INSERT INTO launch_tax(token_id, sniping_tax, checked_at) VALUES ('t', 0, 1)")
    assert r.execute("SELECT count(*) FROM launch_tax").fetchone()[0] == 0   # WAL: reader not blocked
    w.execute("COMMIT")
    assert r.execute("SELECT count(*) FROM launch_tax").fetchone()[0] == 1
