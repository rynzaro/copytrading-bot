import base64
import gzip
import json
import pathlib

from copytrade_worker import detect

FX = pathlib.Path(__file__).with_name("fixtures")
IMEME = "imeme-2.v2-launchpad.justhoot.near"
BATMAN = "batman-4.nearlytrade.near"
SF, ROOT = "seriousfarmer.near", "root.near"
WHALES = {SF, ROOT}


def blocks(name):
    with gzip.open(FX / f"{name}.json.gz", "rt") as f:
        return json.load(f)


def raws(name, whales=WHALES):
    return [s for b in blocks(name) for s in detect.block_signals(b, whales)]


def sigs(name, whales=WHALES):
    return [x for x in (detect.to_signal(r, "teardata", 1) for r in raws(name, whales)) if x]


def test_seriousfarmer_imeme_tx_two_blocks_before_fill():
    s = raws("sf_imeme")
    tx = [x for x in s if x["stage"] == "tx"]
    fill = [x for x in s if x["stage"] == "fill"]
    assert len(tx) == 1 and len(fill) == 1
    assert (tx[0]["whale"], tx[0]["token"], tx[0]["token_in"], tx[0]["amount_in"]) == (
        SF,
        IMEME,
        "wrap.near",
        str(140 * 10**24),
    )
    assert tx[0]["tx"] == "89khmbv7E5qxP8nKL9fW1ru3wYpThVL9egor5B4i62md"
    assert fill[0]["height"] - tx[0]["height"] == 2
    assert fill[0]["amount_out"] == "57484696659090299493698726"


def test_swap_fill_becomes_one_signal_with_hops():
    out = sigs("sf_imeme")
    assert len(out) == 1
    s = out[0]
    assert (s.kind, s.target_id, s.token_id, s.venue) == ("swap", SF, IMEME, "dcl")
    assert s.tx_hash == "89khmbv7E5qxP8nKL9fW1ru3wYpThVL9egor5B4i62md"
    assert s.near_in == str(140 * 10**24) and s.amount_out == "57484696659090299493698726"
    assert s.pool_id == IMEME + "|wrap.near|10000"
    assert [(h.pool_id, h.token_in, h.token_out) for h in s.hops] == [(IMEME + "|wrap.near|10000", "wrap.near", IMEME)]
    px = detect.fill_price(s, 18)
    assert abs(px - 140 / 57484696.659090299493698726) < 1e-12


def test_root_imeme_and_batman():
    assert [(x["stage"], x["token"]) for x in raws("root_imeme")] == [("tx", IMEME), ("fill", IMEME)]
    s = [x for x in raws("root_batman")]
    batman = [x for x in s if x["token"] == BATMAN]
    assert [x["stage"] for x in batman] == ["tx", "fill"]
    assert batman[1]["amount_out"] == "8536860483499347437179321"
    assert [x.token_id for x in sigs("root_batman")] == [BATMAN]


def test_only_listed_targets():
    assert raws("sf_imeme", {ROOT}) == []
    assert raws("root_imeme", {SF}) == []


def test_limit_order_placed_is_not_a_signal():
    s = raws("simsey_order", {"simsey.near"})
    assert [(x["stage"], x["kind"], x["token"], x["point"]) for x in s] == [
        ("tx", "order", BATMAN, 55200),
        ("fill", "order", BATMAN, 55200),
    ]
    assert s[1]["order_id"]
    assert sigs("simsey_order", {"simsey.near"}) == []


def test_failed_receipt_ignored():
    bs = blocks("sf_imeme")
    for b in bs:
        for sh in b["shards"]:
            for ro in sh["receipt_execution_outcomes"]:
                ro["execution_outcome"]["outcome"]["status"] = {"Failure": {}}
    assert [x["stage"] for b in bs for x in detect.block_signals(b, WHALES)] == ["tx"]


def v1_receipt(logs, signer=ROOT):
    return {
        "tx_hash": "TV1",
        "receipt": {"receiver_id": detect.V1, "receipt": {"Action": {"signer_id": signer}}},
        "execution_outcome": {"outcome": {"status": {"SuccessValue": ""}, "executor_id": detect.V1, "logs": logs}},
    }


def test_v1_multi_hop_token_is_last_token_out():
    logs = ["Swapped 10 wrap.near for 5 token.rhealab.near", "Swapped 5 token.rhealab.near for 99 a.tkn.near"]
    r = detect.receipt_signals(v1_receipt(logs), WHALES)
    assert [(x["token"], x["token_in"], x["amount_in"], x["amount_out"]) for x in r] == [
        ("a.tkn.near", "wrap.near", "10", "99")
    ]
    s = detect.to_signal(r[0], "teardata", 1)
    assert s and s.venue == "ref_v1" and s.near_in == "10" and s.hops == ()


def test_order_fill_from_record():
    row = dict(
        order_id=BATMAN + "|wrap.near|10000#12480",
        owner_id="simsey.near",
        sell_token="wrap.near",
        buy_token=BATMAN,
        point=55200,
        pool_id=BATMAN + "|wrap.near|10000",
        original_amount=str(14 * 10**24),
        remain_amount=str(14 * 10**24),
        bought_amount="0",
    )
    assert detect.order_fill(row, "simsey.near") is None
    f = detect.order_fill(dict(row, remain_amount="0", bought_amount="56097350889843077398677"), "simsey.near")
    assert f and (f["kind"], f["amount_in"], f["amount_out"]) == (
        "order_fill",
        str(14 * 10**24),
        "56097350889843077398677",
    )
    s = detect.to_signal(f, "order_watch", 1, tx_hash="PLACE")
    assert s and (s.kind, s.tx_hash, s.near_in, s.venue) == ("limit_fill", "PLACE", str(14 * 10**24), "dcl")
    px = detect.point_price(55200, BATMAN + "|wrap.near|10000", BATMAN, 18, 24)
    assert abs(detect.fill_price(s, 18) - px) / px < 1e-6


def test_ws_row():
    row = dict(
        id=1,
        height=5,
        timestamp_millis=1000,
        transaction_id="t",
        trader=ROOT,
        pool_id="REF-1",
        token_in="wrap.near",
        amount_in="10",
        token_out=IMEME,
        amount_out="20",
    )
    s = detect.to_signal(detect.ws_signal(row, WHALES), "intear_ws", 1)
    assert s and (s.kind, s.token_id, s.amount_out, s.venue, s.near_in) == ("swap", IMEME, "20", None, "10")
    assert detect.ws_signal(dict(row, trader="x.near"), WHALES) is None


def test_untrusted_amounts_and_msgs():
    assert detect.parse_msg("not json") is None
    assert detect.parse_msg(json.dumps([1])) is None
    bad = dict(
        stage="fill",
        kind="swap",
        whale=ROOT,
        token=IMEME,
        tx="t",
        token_in="wrap.near",
        amount_in="-5",
        amount_out="1e9",
    )
    s = detect.to_signal(bad, "teardata", 1)
    assert s and s.near_in is None and s.amount_out is None and detect.fill_price(s, 18) is None
    tx = {
        "signer_id": ROOT,
        "hash": "h",
        "receiver_id": "wrap.near",
        "actions": [{"FunctionCall": {"method_name": "ft_transfer_call", "args": base64.b64encode(b"{bad").decode()}}],
    }
    assert detect.tx_signals(tx, WHALES) == []
