import json
import urllib.error
import urllib.request

import pytest

from copytrade_worker import api as A
from copytrade_worker import keys as K
from copytrade_worker.db import connect

from .fakes import FakeRpc

OWNER, VAULT = "alice.near", "copytrade.alice.near"
HASH = "8kPzWhcBXaQX3rUJzNJo2U8KcU9xUZoTT3qQKcVN3r3H"
NO_CODE = "11111111111111111111111111111111"


@pytest.fixture
def srv(tmp_path):
    db = connect(tmp_path / "t.db")
    db.execute("INSERT INTO users (owner_id, created_at) VALUES (?, 0)", (OWNER,))
    rpc = FakeRpc()
    rpc.url = "http://rpc.test"
    ks = K.KeyStore(tmp_path)
    def parts():
        r = db.execute("SELECT MAX(seen_at) FROM signals").fetchone()
        return {"feeds": {"teardata": "ok"}, "last_signal_at": r[0]}

    confirmed: list = []
    app = A.App(db, rpc, ks, health_parts=parts, code_hash=HASH, on_confirmed=lambda o, v: confirmed.append((o, v)))
    s = A.serve(app, port=0)
    rpc.confirmed = confirmed
    yield f"http://127.0.0.1:{s.server_address[1]}", db, rpc, ks, tmp_path
    s.shutdown()


def call(base, path, body=None):
    req = urllib.request.Request(base + path, json.dumps(body).encode() if body is not None else None,
                                 {"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def test_binds_localhost(srv):
    assert srv[0].startswith("http://127.0.0.1:")


def test_bot_key_idempotent_no_private(srv):
    base, db, rpc, ks, tmp = srv
    c, r = call(base, "/bot-key", {"owner_id": OWNER})
    assert c == 200 and r["public_key"].startswith("ed25519:") and set(r) == {"public_key"}
    assert call(base, "/bot-key", {"owner_id": OWNER})[1] == r
    assert db.execute("SELECT bot_public_key FROM users").fetchone()[0] == r["public_key"]
    priv = json.loads((tmp / "keys" / f"{OWNER}.json").read_text())["private_key"]
    assert priv not in json.dumps(list(db.execute("SELECT * FROM users").fetchone()))


def test_bot_key_errors(srv):
    base = srv[0]
    assert call(base, "/bot-key", {"owner_id": "../etc"})[0] == 400
    assert call(base, "/bot-key", {"owner_id": "bob.near"})[0] == 404
    assert call(base, "/nope", {})[0] == 404
    req = urllib.request.Request(base + "/bot-key", b"{bad", {"content-type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 400


def confirmed_setup(srv):
    base, db, rpc, ks, _ = srv
    pk = call(base, "/bot-key", {"owner_id": OWNER})[1]["public_key"]
    rpc.state[VAULT] = {"owner_id": OWNER, "paused": False, "bot_key": pk, "version": "1"}
    rpc.access = {"nonce": 1, "permission": {"FunctionCall": {"allowance": str(10 ** 24), "receiver_id": VAULT,
                                                               "method_names": ["swap", "register_token"]}}}
    # Mainnet: UseGlobalContract by hash -> code_hash all-ones, global_contract_hash set.
    rpc.accounts[VAULT] = {"amount": "1", "code_hash": NO_CODE, "global_contract_hash": HASH}
    return pk


def test_vault_confirmed_ok(srv):
    base, db = srv[0], srv[1]
    confirmed_setup(srv)
    assert call(base, "/vault-confirmed", {"owner_id": OWNER, "vault_id": VAULT}) == (200, {"ok": True})
    assert db.execute("SELECT vault_id FROM users").fetchone()[0] == VAULT
    assert srv[2].confirmed == [(OWNER, VAULT)]


def test_vault_confirmed_accepts_deployed_code_with_pinned_hash(srv):
    # Test wallets: DeployContract of the same wasm -> code_hash is the pinned hash.
    base, rpc = srv[0], srv[2]
    confirmed_setup(srv)
    rpc.accounts[VAULT] = {"amount": "1", "code_hash": HASH}
    assert call(base, "/vault-confirmed", {"owner_id": OWNER, "vault_id": VAULT}) == (200, {"ok": True})


@pytest.mark.parametrize("acct", [
    {"code_hash": NO_CODE, "global_contract_hash": "9" * 44},
    {"code_hash": "9" * 44},
    {"code_hash": NO_CODE},
    {"code_hash": NO_CODE, "global_contract_account_id": "publisher.near"},  # by account: re-pointable
])
def test_vault_confirmed_rejects_other_code(srv, acct):
    base, db, rpc = srv[0], srv[1], srv[2]
    confirmed_setup(srv)
    rpc.accounts[VAULT] = acct
    c, r = call(base, "/vault-confirmed", {"owner_id": OWNER, "vault_id": VAULT})
    assert (c, r) == (409, {"error": "vault code hash mismatch"})
    assert db.execute("SELECT vault_id FROM users").fetchone()[0] is None
    assert rpc.confirmed == []


@pytest.mark.parametrize("methods", [["swap"], ["swap", "register_token", "sell"], [], ["swap", "swap"],
                                     ["swap", "register_token", "register_token"]])
def test_vault_confirmed_rejects_other_methods(srv, methods):
    base, rpc = srv[0], srv[2]
    confirmed_setup(srv)
    rpc.access["permission"]["FunctionCall"]["method_names"] = methods
    body = {"owner_id": OWNER, "vault_id": VAULT}
    assert call(base, "/vault-confirmed", body) == (409, {"error": "bot key permission"})


def test_vault_confirmed_without_pinned_hash_rejects(tmp_path):
    db = connect(tmp_path / "t.db")
    db.execute("INSERT INTO users (owner_id, created_at) VALUES (?, 0)", (OWNER,))
    app = A.App(db, FakeRpc(), K.KeyStore(tmp_path), code_hash="")
    app.bot_key({"owner_id": OWNER})
    assert app.vault_confirmed({"owner_id": OWNER, "vault_id": VAULT}) == (503, {"error": "VAULT_CODE_HASH not set"})


def test_vault_confirmed_hook_failure_still_confirms(srv, tmp_path):
    base, db, rpc, ks, _ = srv
    pk = confirmed_setup(srv)
    def boom(o, v):
        raise RuntimeError("rpc down")
    app = A.App(db, rpc, ks, code_hash=HASH, on_confirmed=boom)
    assert pk and app.vault_confirmed({"owner_id": OWNER, "vault_id": VAULT}) == (200, {"ok": True})


def test_vault_confirmed_rejects(srv):
    base, db, rpc = srv[0], srv[1], srv[2]
    confirmed_setup(srv)
    body = {"owner_id": OWNER, "vault_id": VAULT}
    rpc.state[VAULT]["owner_id"] = "mallory.near"
    assert call(base, "/vault-confirmed", body)[0] == 409
    rpc.state[VAULT]["owner_id"] = OWNER
    rpc.state[VAULT]["bot_key"] = None
    assert call(base, "/vault-confirmed", body)[0] == 409
    rpc.state[VAULT]["bot_key"] = db.execute("SELECT bot_public_key FROM users").fetchone()[0]
    rpc.access = {"nonce": 1, "permission": "FullAccess"}
    assert call(base, "/vault-confirmed", body)[0] == 409
    assert call(base, "/vault-confirmed", {"owner_id": OWNER, "vault_id": "unknown.near"})[0] == 409
    assert db.execute("SELECT vault_id FROM users").fetchone()[0] is None


def test_vault_confirmed_rpc_down_is_502(srv):
    base, db, rpc = srv[0], srv[1], srv[2]
    confirmed_setup(srv)
    rpc.down = {"call_view"}
    assert call(base, "/vault-confirmed", {"owner_id": OWNER, "vault_id": VAULT}) == (502, {"error": "rpc"})
    assert db.execute("SELECT vault_id FROM users").fetchone()[0] is None


def test_health(srv):
    base, db = srv[0], srv[1]
    db.execute("INSERT INTO signals (target_id, token_id, kind, tx_hash, seen_at, source)"
               " VALUES ('a','b','swap','t',42,'teardata')")
    assert call(base, "/health") == (200, {"feeds": {"teardata": "ok"}, "rpc": "http://rpc.test", "last_signal_at": 42})


def test_health_without_signal_side(tmp_path):
    db = connect(tmp_path / "t.db")
    rpc = FakeRpc()
    rpc.url = "http://rpc.test"
    app = A.App(db, rpc, K.KeyStore(tmp_path))
    assert app.health() == (200, {"feeds": {}, "rpc": "http://rpc.test", "last_signal_at": None})


def test_port_from_worker_port_env(monkeypatch):
    monkeypatch.delenv("WORKER_PORT", raising=False)
    assert A.api_port() == 8700
    monkeypatch.setenv("WORKER_PORT", "9123")
    assert A.api_port() == 9123


def test_host_from_worker_host_env(monkeypatch):
    # Default stays loopback; Compose sets WORKER_HOST=0.0.0.0 on its internal network (deploy/compose.yaml).
    monkeypatch.delenv("WORKER_HOST", raising=False)
    assert A.api_host() == "127.0.0.1"
    monkeypatch.setenv("WORKER_HOST", "0.0.0.0")
    assert A.api_host() == "0.0.0.0"


def test_serve_binds_api_host(monkeypatch, tmp_path):
    monkeypatch.delenv("WORKER_HOST", raising=False)
    s = A.serve(A.App(connect(tmp_path / "t.db"), FakeRpc(), K.KeyStore(tmp_path)), port=0)
    try:
        assert s.server_address[0] == "127.0.0.1"
    finally:
        s.shutdown()
