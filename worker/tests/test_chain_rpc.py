"""Chain as the one RPC client for executor and API: strict calls, failover, safe send_tx."""
import json

import pytest

from copytrade_worker import chain
from copytrade_worker.chain import RpcDown, RpcError


def fetcher(replies):
    calls = []

    def f(url, body, timeout):
        calls.append((url, body))
        r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    f.calls = calls
    return f


def mk(replies, rpcs=("https://a", "https://b")):
    f = fetcher(list(replies))
    return chain.Chain(list(rpcs), fetch=f, min_gap_s=0), f


def view_reply(obj):
    return {"result": {"result": list(json.dumps(obj).encode()), "logs": []}}


def test_call_view_fails_over_on_network_error_and_tracks_url():
    c, f = mk([OSError("down"), view_reply({"decimals": 18})])
    assert c.call_view("t.near", "ft_metadata", {}) == {"decimals": 18}
    assert [u for u, _ in f.calls] == ["https://a", "https://b"]
    assert f.calls[0][1]["params"]["finality"] == "final"
    assert c.url == "https://b"


def test_call_view_raises_on_error_reply_without_failover():
    c, f = mk([{"result": {"error": "wasm execution failed", "logs": []}}])
    with pytest.raises(RpcError):
        c.call_view("t.near", "get_pool", {"pool_id": 1})
    assert len(f.calls) == 1
    c, _ = mk([{"error": {"name": "HANDLER_ERROR", "cause": {"name": "UNKNOWN_ACCOUNT"}}}])
    with pytest.raises(RpcError) as e:
        c.call_view("t.near", "m", {})
    assert "UNKNOWN_ACCOUNT" in e.value.text


def test_call_view_junk_bytes_is_rpc_error():
    c, _ = mk([{"result": {"result": list(b"{bad")}}])
    with pytest.raises(RpcError):
        c.call_view("t.near", "m", {})


def test_null_view_result_is_none_not_error():
    c, _ = mk([view_reply(None)])
    assert c.call_view("t.near", "storage_balance_of", {"account_id": "v.near"}) is None


def test_all_down_raises_rpc_down():
    c, f = mk([OSError(), OSError(), OSError()])
    with pytest.raises(RpcDown):
        c.block_hash()
    assert len(f.calls) == 3
    assert isinstance(RpcDown(), OSError) and not isinstance(RpcDown(), RpcError)


def test_access_key_and_block_hash():
    c, f = mk([{"result": {"nonce": 5, "permission": "FullAccess"}}, {"result": {"header": {"hash": "H"}}}])
    assert c.view_access_key("v.near", "ed25519:x")["nonce"] == 5
    assert c.block_hash() == "H"
    assert f.calls[0][1]["params"]["request_type"] == "view_access_key"
    assert f.calls[1][1] == {"jsonrpc": "2.0", "id": 1, "method": "block", "params": {"finality": "final"}}


def test_send_tx_returns_hash_and_raises_rejection():
    c, f = mk([{"result": {"transaction": {"hash": "TX"}}}])
    assert c.send_tx("b64") == "TX"
    assert f.calls[0][1]["params"] == {"signed_tx_base64": "b64", "wait_until": "NONE"}
    c, f = mk([{"error": {"data": {"TxExecutionError": {"InvalidTxError": "InvalidNonce"}}}}])
    with pytest.raises(RpcError) as e:
        c.send_tx("b64")
    assert "InvalidNonce" in e.value.text and len(f.calls) == 1


def test_send_tx_error_after_network_failure_is_ambiguous():
    """The first endpoint may have taken the tx: a later 'InvalidNonce' must not look like a clean rejection."""
    c, f = mk([OSError("timeout"), {"error": {"data": {"TxExecutionError": {"InvalidTxError": "InvalidNonce"}}}}])
    with pytest.raises(RpcDown):
        c.send_tx("b64")
    assert len(f.calls) == 2


def test_send_tx_same_signed_tx_on_failover():
    c, f = mk([OSError("timeout"), {"result": {"transaction": {"hash": "TX"}}}])
    assert c.send_tx("b64") == "TX"
    assert f.calls[0][1] == f.calls[1][1]


def test_tx_status_unknown_and_timeout_are_none():
    c, f = mk([{"result": {"status": {"SuccessValue": ""}}}])
    assert c.tx_status("TX", "v.near") == {"status": {"SuccessValue": ""}}
    assert f.calls[0][1]["method"] == "tx"
    assert f.calls[0][1]["params"] == {"tx_hash": "TX", "sender_account_id": "v.near",
                                       "wait_until": "EXECUTED_OPTIMISTIC"}
    for err in ({"name": "HANDLER_ERROR", "cause": {"name": "UNKNOWN_TRANSACTION"}}, {"name": "TIMEOUT_ERROR"}):
        c, _ = mk([{"error": err}])
        assert c.tx_status("TX", "v.near") is None
    c, _ = mk([{"error": {"name": "HANDLER_ERROR", "cause": {"name": "INVALID_TRANSACTION"}}}])
    with pytest.raises(RpcError):
        c.tx_status("TX", "v.near")


def test_env_urls(monkeypatch):
    monkeypatch.delenv("RPC_URLS", raising=False)
    monkeypatch.setenv("NEAR_RPC_URL", "https://one")
    assert chain.Chain().rpcs == ["https://one"]
    monkeypatch.setenv("RPC_URLS", "https://x, https://y")
    assert chain.Chain().rpcs == ["https://x", "https://y"]


def test_view_account():
    acct = {"amount": "1", "code_hash": "1" * 32,
            "global_contract_hash": "8kPzWhcBXaQX3rUJzNJo2U8KcU9xUZoTT3qQKcVN3r3H"}
    c, f = mk([{"result": acct}])
    assert c.view_account("v.near") == acct
    p = f.calls[0][1]["params"]
    assert p == {"request_type": "view_account", "finality": "final", "account_id": "v.near"}
    c, _ = mk([{"result": None}])
    with pytest.raises(RpcError):
        c.view_account("v.near")
