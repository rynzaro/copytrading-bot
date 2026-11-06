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


ORDER_TX = "7PZZMDLCwjXyPRTBpQ1ui85ko43tPbGeLUrMJcaUGqsx"
OID = BATMAN + "|wrap.near|10000#12480"
POOL = BATMAN + "|wrap.near|10000"
KEYS = {"stage", "order_id", "target_id", "token_id", "pool_id", "amount_near", "price", "tx_hash"}


def order_row(bought=0, remain=14 * 10**24, **kw):
    return dict(
        dict(
            order_id=OID,
            owner_id="simsey.near",
            sell_token="wrap.near",
            buy_token=BATMAN,
            point=55200,
            pool_id=POOL,
            original_amount=str(14 * 10**24),
            remain_amount=str(remain),
            bought_amount=str(bought),
        ),
        **kw,
    )


def place(p):
    return [i for b in blocks("simsey_order") for i in p.on_block(b, "teardata")]


def test_limit_order_placed_alerts_users_never_buys(conn):
    add_user(conn, "a.near", target="simsey.near")
    add_user(conn, "b.near", target="simsey.near", settings={})  # alert does not need buy settings
    add_user(conn, "c.near", target="simsey.near", enabled=0)  # target off: no alert
    add_user(conn, "d.near", target="simsey.near", bot_on=0)  # bot off: no alert
    add_user(conn, "e.near", target=SF)  # other target
    add_user(conn, "f.near", target="simsey.near", settings={"notify": {"limit_orders": False}})  # switched off
    add_user(conn, "g.near", target="simsey.near", settings={"notify": {"limit_orders": True}})
    ch = FakeChain()
    p = pipe(conn, ch)
    assert place(p) == []
    assert drain(p) == []
    assert conn.execute("select count(*) from signals").fetchone()[0] == 0
    assert conn.execute("select count(*) from copy_buys").fetchone()[0] == 0
    acts = activity(conn)
    assert [(o, k) for o, k, _ in acts] == [
        ("a.near", "limit_order"),
        ("b.near", "limit_order"),
        ("g.near", "limit_order"),
    ]
    pl = acts[0][2]
    assert set(pl) == KEYS
    assert (
        pl["stage"],
        pl["order_id"],
        pl["target_id"],
        pl["token_id"],
        pl["pool_id"],
        pl["amount_near"],
        pl["tx_hash"],
    ) == (
        "placed",
        OID,
        "simsey.near",
        BATMAN,
        POOL,
        str(14 * 10**24),
        ORDER_TX,
    )
    assert pl["price"] == pytest.approx(7 / 28048.675444921538699338, rel=1e-6)
    assert conn.execute("select created_at from activity").fetchone()[0] == NOW
    assert list(p.orders) == [OID]
    # replay (both feeds, restarts): one alert per (order, stage, user)
    assert place(p) == []
    place(pipe(conn, ch))
    assert len(activity(conn)) == 3


def test_limit_order_filled_alerts_once_never_buys(conn):
    add_user(conn, "a.near", target="simsey.near")
    add_user(conn, "f.near", target="simsey.near", settings={"notify": {"limit_orders": False}})
    ch = FakeChain()
    p = pipe(conn, ch)
    place(p)
    ch.orders = {"simsey.near": [order_row()]}
    assert p.poll_orders() == []
    ch.orders = {"simsey.near": [order_row(bought=28048675444921538699338, remain=7 * 10**24)]}
    assert len(p.poll_orders()) == 1
    assert drain(p) == []
    assert conn.execute("select count(*) from signals").fetchone()[0] == 0
    (_, (o, k, pl)) = activity(conn)
    assert (o, k, set(pl)) == ("a.near", "limit_order", KEYS)
    assert (pl["stage"], pl["amount_near"], pl["tx_hash"], pl["pool_id"]) == (
        "filled",
        str(7 * 10**24),
        ORDER_TX,
        POOL,
    )
    assert pl["price"] == pytest.approx(7 / 28048.675444921538699338)
    assert p.poll_orders() == []  # fires once
    assert p.orders == {}


def test_filled_after_restart_uses_placed_tx_and_dedupes(conn):
    add_user(conn, "a.near", target="simsey.near")
    ch = FakeChain()
    place(pipe(conn, ch))
    ch.orders = {"simsey.near": [order_row(), dict(order_row(bought=5), order_id="filled#1")]}
    p = pipe(conn, ch)  # restart: watch rebuilt from open orders
    p.seed_orders()
    assert list(p.orders) == [OID]
    ch.orders = {"simsey.near": [order_row(bought=10**18, remain=10**24)]}
    assert len(p.poll_orders()) == 1
    assert [(x[2]["stage"], x[2]["tx_hash"]) for x in activity(conn)] == [("placed", ORDER_TX), ("filled", ORDER_TX)]
    p2 = pipe(conn, ch)  # second restart before the order leaves the list: no second fill alert
    ch.orders = {"simsey.near": [order_row()]}
    p2.seed_orders()
    ch.orders = {"simsey.near": [order_row(bought=10**18, remain=10**24)]}
    assert p2.poll_orders() == []
    assert len(activity(conn)) == 2


def test_seeded_order_without_known_placing_tx_alerts_without_tx(conn):
    add_user(conn, "a.near", target="simsey.near")
    ch = FakeChain(orders={"simsey.near": [order_row()]})
    p = pipe(conn, ch)
    p.seed_orders()
    ch.orders = {"simsey.near": [order_row(bought=10**18, remain=10**24)]}
    assert len(p.poll_orders()) == 1
    ((_, _, pl),) = activity(conn)
    assert (pl["stage"], pl["order_id"], "tx_hash" in pl) == ("filled", OID, False)
    assert set(pl) == KEYS - {"tx_hash"}


def test_two_orders_same_tx_and_token_each_alert(conn):
    add_user(conn, "a.near", target="simsey.near")
    p = pipe(conn)
    raw = dict(
        stage="fill",
        kind="order",
        whale="simsey.near",
        token=BATMAN,
        token_in="wrap.near",
        amount_in="5",
        point=55200,
        pools=[POOL],
        tx="LADDER",
        order_id="o1",
    )
    assert len(p.on_order(raw)) == 1
    assert len(p.on_order(dict(raw, order_id="o2"))) == 1
    assert p.on_order(raw) == []
    assert [x[2]["order_id"] for x in activity(conn)] == ["o1", "o2"]


def test_order_completed_event_fills_watched_order(conn):
    add_user(conn, "a.near", target="simsey.near")
    p = pipe(conn)
    f = dict(
        stage="fill",
        kind="order_fill",
        whale="simsey.near",
        token=BATMAN,
        token_in="wrap.near",
        amount_in=str(2 * 10**24),
        amount_out=str(10**21),
        pools=[POOL],
        venue="dclv2.ref-labs.near",
        order_id="nope",
        tx="CLAIM",
    )
    assert p.order_filled(f) == []  # unwatched
    place(p)
    ids = p.order_filled(dict(f, order_id=OID))
    assert len(ids) == 1
    pl = activity(conn)[-1][2]
    assert (pl["stage"], pl["tx_hash"], pl["amount_near"], pl["price"]) == (
        "filled",
        ORDER_TX,
        str(2 * 10**24),
        pytest.approx(0.002),
    )


def test_limit_order_price_unknown_without_decimals(conn):
    add_user(conn, "a.near", target="simsey.near")

    class NoDec(FakeChain):
        def tokens(self, ids):
            raise OSError("down")

    p = pipe(conn, NoDec())
    place(p)
    ((_, kind, pl),) = activity(conn)
    assert kind == "limit_order" and "price" not in pl and pl["amount_near"] == str(14 * 10**24)


def test_limit_order_non_meme_ignored(conn):
    add_user(conn, "a.near", target="simsey.near")
    p = pipe(conn)
    raw = dict(
        stage="fill",
        kind="order",
        whale="simsey.near",
        token="usdt.tether-token.near",
        token_in="wrap.near",
        amount_in="5",
        point=1,
        pools=["usdt.tether-token.near|wrap.near|100"],
        venue="dclv2.ref-labs.near",
        tx="T",
        order_id="o1",
    )
    assert p.on_order(raw) == []
    assert activity(conn) == [] and p.orders == {}
