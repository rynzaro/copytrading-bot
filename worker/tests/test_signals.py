import gzip
import json
import pathlib

import pytest

from copytrade_worker import db
from copytrade_worker.signals import Pipeline

FX = pathlib.Path(__file__).with_name("fixtures")
IMEME = "imeme-2.v2-launchpad.justhoot.near"
BATMAN = "batman-4.nearlytrade.near"
SF = "seriousfarmer.near"
NOW = 1_800_000_000_000
GOOD = {"slippage_bps": 300}


def blocks(name):
    with gzip.open(FX / f"{name}.json.gz", "rt") as f:
        return json.load(f)


class FakeChain:
    def __init__(self, mcap=500_000.0, created=NOW - 60_000, orders=None):
        self.mcap, self.created, self.orders, self.calls = mcap, created, orders or {}, []

    def tokens(self, ids):
        self.calls.append(("tokens", tuple(sorted(ids))))
        out = {"wrap.near": {"account_id": "wrap.near", "decimals": 24, "price_usd": 2.0}}
        for t in ids:
            if t != "wrap.near":
                out[t] = {
                    "account_id": t,
                    "decimals": 18,
                    "market_cap_usd": self.mcap,
                    "created_at_millis": self.created,
                }
        return out

    def active_orders(self, a):
        self.calls.append(("orders", a))
        return self.orders.get(a, [])


def add_user(conn, owner, target=SF, enabled=1, bot_on=1, settings=GOOD):
    conn.execute(
        "insert into users(owner_id, vault_id, bot_on, settings, created_at) values (?,?,?,?,0)",
        (owner, "v." + owner, bot_on, json.dumps(settings)),
    )
    conn.execute("insert into copy_targets values (?,?,?)", (owner, target, enabled))


@pytest.fixture
def conn():
    return db.connect(":memory:")


def pipe(conn, chain=None):
    return Pipeline(conn, chain or FakeChain(), clock=lambda: NOW)


def drain(p):
    out = []
    while not p.plans.empty():
        out.append(p.plans.get_nowait())
    return out


def activity(conn):
    return [
        (r[0], r[1], json.loads(r[2])) for r in conn.execute("select owner_id, kind, payload from activity order by id")
    ]


def test_fill_plans_for_each_user_once(conn):
    add_user(conn, "a.near")
    add_user(conn, "b.near", settings={})  # no slippage -> logged skip
    add_user(conn, "c.near", enabled=0)  # quiet skip
    p = pipe(conn)
    ids = [i for b in blocks("sf_imeme") for i in p.on_block(b, "teardata")]
    assert len(ids) == 1
    plans = drain(p)
    assert [(x.owner_id, x.token_id, x.amount_in, x.venue) for x in plans] == [
        ("a.near", IMEME, str(10 * 10**24), "dcl")
    ]
    assert plans[0].min_out_inputs.fill_price == pytest.approx(140 / 57484696.659090299493698726)
    row = conn.execute("select target_id, token_id, kind, venue, near_in, source, seen_at from signals").fetchone()
    assert tuple(row) == (SF, IMEME, "swap", "dcl", str(140 * 10**24), "teardata", NOW)
    assert activity(conn) == [("b.near", "buy_skipped", {"token_id": IMEME, "reason": "no_slippage"})]
    status = conn.execute("select owner_id, status, amount_in from copy_buys").fetchall()
    assert [tuple(r) for r in status] == [("a.near", "planned", str(10 * 10**24))]
    # replay of the same blocks and the WS backup row for the same tx: no new signal
    assert [i for b in blocks("sf_imeme") for i in p.on_block(b, "teardata")] == []
    ws = dict(
        id=1,
        trader=SF,
        transaction_id="89khmbv7E5qxP8nKL9fW1ru3wYpThVL9egor5B4i62md",
        token_in="wrap.near",
        amount_in="1",
        token_out=IMEME,
        amount_out="2",
        pool_id="x",
    )
    assert p.on_row(ws, "intear_ws") is None
    assert drain(p) == []


def test_second_signal_same_token_is_already_bought(conn):
    add_user(conn, "a.near")
    p = pipe(conn)
    ws = dict(
        id=1, trader=SF, transaction_id="T1", token_in="wrap.near", amount_in="1", token_out=IMEME, amount_out="2"
    )
    assert p.on_row(ws, "intear_ws") is not None
    assert len(drain(p)) == 1
    assert p.on_row(dict(ws, transaction_id="T2"), "intear_ws") is not None
    assert drain(p) == []
    assert activity(conn)[-1] == ("a.near", "buy_skipped", {"token_id": IMEME, "reason": "already_bought"})


def test_untargeted_and_non_meme_ignored(conn):
    add_user(conn, "a.near", bot_on=0)  # target not enabled by any active user
    p = pipe(conn)
    assert [i for b in blocks("sf_imeme") for i in p.on_block(b, "teardata")] == []
    add_user(conn, "b.near")
    p = pipe(conn)
    sell = dict(
        id=1, trader=SF, transaction_id="T", token_in=IMEME, amount_in="1", token_out="wrap.near", amount_out="2"
    )
    assert p.on_row(sell, "intear_ws") is None
    assert conn.execute("select count(*) from signals").fetchone()[0] == 0


def test_unknown_mcap_skips(conn):
    add_user(conn, "a.near")
    p = pipe(conn, FakeChain(mcap=None))
    p.on_row(
        dict(trader=SF, transaction_id="T", token_in="wrap.near", amount_in="1", token_out=IMEME, amount_out="2"),
        "intear_ws",
    )
    assert drain(p) == []
    assert activity(conn) == [("a.near", "buy_skipped", {"token_id": IMEME, "reason": "mcap_unknown"})]


@pytest.mark.parametrize(
    "age_min,cached,skipped", [(5, True, True), (11, True, False), (5, False, False), (5, None, False)]
)
def test_sniping_tax_cache(conn, age_min, cached, skipped):
    add_user(conn, "a.near")
    if cached is not None:
        db.put_launch_tax(conn, IMEME, "h", cached, NOW - 60_000)
    p = pipe(conn, FakeChain(created=NOW - age_min * 60_000))
    p.on_row(
        dict(trader=SF, transaction_id="T", token_in="wrap.near", amount_in="1", token_out=IMEME, amount_out="2"),
        "intear_ws",
    )
    assert (drain(p) == []) == skipped
    if skipped:
        assert activity(conn)[-1][2] == {"token_id": IMEME, "reason": "sniping_tax"}


def order_row(bought=0, remain=14 * 10**24):
    return dict(
        order_id=BATMAN + "|wrap.near|10000#12480",
        owner_id="simsey.near",
        sell_token="wrap.near",
        buy_token=BATMAN,
        point=55200,
        pool_id=BATMAN + "|wrap.near|10000",
        original_amount=str(14 * 10**24),
        remain_amount=str(remain),
        bought_amount=str(bought),
    )


def test_limit_order_copied_only_when_filled(conn):
    add_user(conn, "a.near", target="simsey.near")
    ch = FakeChain()
    p = pipe(conn, ch)
    assert [i for b in blocks("simsey_order") for i in p.on_block(b, "teardata")] == []  # placed: watched only
    assert list(p.orders) == [BATMAN + "|wrap.near|10000#12480"]
    ch.orders = {"simsey.near": [order_row()]}
    assert p.poll_orders() == []
    ch.orders = {"simsey.near": [order_row(bought=28048675444921538699338, remain=7 * 10**24)]}
    ids = p.poll_orders()
    assert len(ids) == 1
    (plan,) = drain(p)
    assert plan.token_id == BATMAN
    r = conn.execute("select kind, venue, near_in, source, tx_hash, fill_price from signals").fetchone()
    assert tuple(r)[:5] == (
        "limit_fill",
        "dcl",
        str(7 * 10**24),
        "order_watch",
        "7PZZMDLCwjXyPRTBpQ1ui85ko43tPbGeLUrMJcaUGqsx",
    )
    assert r[5] == pytest.approx(7 / 28048.675444921538699338)
    assert p.poll_orders() == []  # fires once
    assert p.orders == {}


def test_unwatched_order_completed_event_ignored(conn):
    add_user(conn, "a.near", target="simsey.near")
    p = pipe(conn)
    f = dict(
        stage="fill",
        kind="order_fill",
        whale="simsey.near",
        token=BATMAN,
        token_in="wrap.near",
        amount_in="1",
        amount_out="2",
        pools=[],
        venue="dclv2.ref-labs.near",
        order_id="nope",
        tx="T",
    )
    assert p.order_filled(f, "teardata") is None


def test_seed_orders_watches_open_unfilled(conn):
    add_user(conn, "a.near", target="simsey.near")
    ch = FakeChain(orders={"simsey.near": [order_row(), dict(order_row(bought=5), order_id="filled#1")]})
    p = pipe(conn, ch)
    p.seed_orders()
    assert list(p.orders) == [BATMAN + "|wrap.near|10000#12480"]
    ch.orders = {"simsey.near": [order_row(bought=10, remain=10**24)]}
    p.poll_orders()
    assert conn.execute("select tx_hash from signals").fetchone()[0] == "order:" + BATMAN + "|wrap.near|10000#12480"
