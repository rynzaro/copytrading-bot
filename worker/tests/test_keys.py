import base64
import json
import logging
import stat
from pathlib import Path

import pytest

from copytrade_worker import keys

VEC = json.loads((Path(__file__).parent / "fixtures" / "borsh_vector.json").read_text())


def test_borsh_vector_matches_near_api_js():
    key = keys.BotKey(bytes.fromhex(VEC["seed_hex"]))
    assert key.public_key == VEC["public_key"]
    act = keys.function_call(VEC["method"], VEC["args_json"].encode(), int(VEC["gas"]), int(VEC["deposit"]))
    tx = keys.sign_tx(key, VEC["signer_id"], int(VEC["nonce"]), VEC["receiver_id"], VEC["block_hash"], [act])
    assert tx.hash == VEC["tx_hash"]
    assert tx.base64 == VEC["signed_tx_base64"]


def test_function_call_dict_args_compact_json():
    a = keys.function_call("swap", json.loads(VEC["args_json"]), 1, 0)
    assert VEC["args_json"].encode() in a


def test_b58_roundtrip_leading_zeros():
    for b in [b"", b"\0\0\x01", bytes(32), bytes(range(40))]:
        assert keys.b58d(keys.b58e(b)) == b


def test_keystore_create_idempotent_mode(tmp_path):
    ks = keys.KeyStore(tmp_path)
    pk = ks.ensure("alice.near")
    assert pk.startswith("ed25519:")
    assert ks.ensure("alice.near") == pk
    p = tmp_path / "keys" / "alice.near.json"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "keys").stat().st_mode) == 0o700
    assert ks.load("alice.near").public_key == pk
    assert ks.ensure("bob.near") != pk


@pytest.mark.parametrize("bad", ["../x", "a/b.near", "", "A.near", "x" * 65, ".near"])
def test_keystore_rejects_bad_owner(tmp_path, bad):
    with pytest.raises(ValueError):
        keys.KeyStore(tmp_path).ensure(bad)


def test_repr_has_no_private_key(tmp_path, caplog):
    ks = keys.KeyStore(tmp_path)
    ks.ensure("alice.near")
    k = ks.load("alice.near")
    priv = json.loads((tmp_path / "keys" / "alice.near.json").read_text())["private_key"]
    caplog.set_level(logging.DEBUG)
    logging.getLogger("t").info("key %s %r", k, k)
    assert priv.split(":")[1] not in caplog.text and priv.split(":")[1] not in repr(k)


def test_nonce_last_used_plus_one(tmp_path):
    ks = keys.KeyStore(tmp_path)
    assert ks.next_nonce("pk", 10) == 11
    assert ks.next_nonce("pk", 10) == 12   # local last used wins
    assert ks.next_nonce("pk", 20) == 21   # chain ahead wins


def test_signature_verifies(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    k = keys.BotKey(bytes(32))
    tx = keys.sign_tx(k, "v.near", 1, "v.near", keys.b58e(bytes(32)), [keys.function_call("swap", {}, 1)])
    raw = base64.b64decode(tx.base64)
    body, sig = raw[:-65], raw[-64:]
    assert raw[-65] == 0
    import hashlib
    Ed25519PublicKey.from_public_bytes(k.public_bytes).verify(sig, hashlib.sha256(body).digest())
