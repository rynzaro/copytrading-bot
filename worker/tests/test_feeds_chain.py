import base64
import json
import os
import struct

import pytest

from copytrade_worker import chain, feeds


def frame(op, data, fin=True, mask=False):
    n = len(data)
    h = bytes([(0x80 if fin else 0) | op])
    mbit = 0x80 if mask else 0
    h += bytes([mbit | n]) if n < 126 else bytes([mbit | 126]) + struct.pack(">H", n)
    if mask:
        m = os.urandom(4)
        return h + m + bytes(b ^ m[i % 4] for i, b in enumerate(data))
    return h + data


class Sock:
    def __init__(self, chunks):
        self.chunks, self.sent = list(chunks), []

    def recv(self, n):
        return self.chunks.pop(0) if self.chunks else b""

    def sendall(self, b):
        self.sent.append(b)


def ws_with(chunks):
    w = feeds.WS.__new__(feeds.WS)
    w.s, w.buf, w.msg, w.last = Sock(chunks), b"", b"", 0
    return w


def test_ws_fragments_ping_and_split_frames():
    big = json.dumps({"rows": [[1] * 100]}).encode()
    data = frame(9, b"p") + frame(1, b'{"a":', fin=False) + frame(0, b"1}", mask=True) + frame(1, big)
    w = ws_with([data[:3], data[3:11], data[11:]])
    assert w.recv() == '{"a":1}'
    assert w.recv() == big.decode()
    assert w.s.sent and w.s.sent[0][0] == 0x8A  # pong
    with pytest.raises(ConnectionError):
        w.recv()


def test_health_stall_and_recovery():
    ev = []
    h = feeds.Health(("teardata", "intear_ws"), 30, lambda n, down, age: ev.append((n, down)))
    h.check(now=h.t["teardata"] + 31)
    h.check(now=h.t["teardata"] + 32)
    assert sorted(ev) == [("intear_ws", True), ("teardata", True)]
    h.beat("teardata")
    assert ev[-1] == ("teardata", False)
    snap = h.snapshot()
    assert snap["teardata"]["ok"] and not snap["intear_ws"]["ok"]


def rpc_fetch(replies):
    calls = []

    def f(url, body, timeout):
        calls.append((url, body))
        r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    f.calls = calls
    return f


def test_code_check_and_failover():
    code = base64.b64encode(b"\0asm...sniping_tax...").decode()
    f = rpc_fetch([OSError("down"), {"result": {"code_base64": code, "hash": "H"}}])
    c = chain.Chain(["https://a", "https://b"], fetch=f, min_gap_s=0)
    assert c.code_check("x.nearlytrade.near") == {"sniping_tax": True, "code_hash": "H"}
    assert [u for u, _ in f.calls] == ["https://a", "https://b"]
    f = rpc_fetch([{"result": {"code_base64": base64.b64encode(b"clean").decode(), "hash": 5}}])
    assert chain.Chain(["u"], fetch=f, min_gap_s=0).code_check("t") == {"sniping_tax": False, "code_hash": None}
    f = rpc_fetch([{"error": {"cause": "UNKNOWN_ACCOUNT"}}])
    assert chain.Chain(["u"], fetch=f, min_gap_s=0).code_check("t") is None


def test_view_and_active_orders():
    res = list(json.dumps([{"order_id": "1"}, "junk"]).encode())
    c = chain.Chain(["u"], fetch=rpc_fetch([{"result": {"result": res}}]), min_gap_s=0)
    assert c.active_orders("w.near") == [{"order_id": "1"}]
    c = chain.Chain(["u"], fetch=rpc_fetch([{"result": {"result": list(b"{bad")}}]), min_gap_s=0)
    assert c.view("a", "m") is None


def test_tokens_and_launches_shape():
    d = {"columns": ["account_id", "market_cap_usd"], "rows": [["a.near", 5], "junk", [7, 1]]}
    c = chain.Chain(["u"], fetch=rpc_fetch([d]), min_gap_s=0)
    assert c.tokens(["a.near"]) == {"a.near": {"account_id": "a.near", "market_cap_usd": 5}}
    d = {"columns": ["account_id"], "rows": [["ok.nearlytrade.near"], ["Bad Name"], [None]]}
    f = rpc_fetch([d])
    c = chain.Chain(["u"], fetch=f, min_gap_s=0)
    assert [r["account_id"] for r in c.launches(["nearlytrade.near"], 5)] == ["ok.nearlytrade.near"]
    assert f.calls[0][1]["where"] == {"parent": ["nearlytrade.near"], "created_after_millis": 5}
    assert chain.Chain(["u"], fetch=rpc_fetch([OSError()]), min_gap_s=0).tokens(["a"]) == {}


@pytest.mark.parametrize(
    "info,near_usd,want",
    [
        ({"market_cap_usd": 1e6, "decimals": 18, "created_at_millis": 5}, None, (1e6, 5, 18)),
        ({"market_cap_usd": None, "price_usd": "0.001", "total_supply_with_decimals": "1e9"}, None, (1e6, None, None)),
        ({"price_near": 0.0005, "total_supply_with_decimals": 1e9}, 2.0, (1e6, None, None)),
        ({"market_cap_usd": "nan", "decimals": True}, None, (None, None, None)),
        (None, 2.0, (None, None, None)),
    ],
)
def test_market(info, near_usd, want):
    assert chain.market(info, near_usd) == want
