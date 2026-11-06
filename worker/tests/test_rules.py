import pytest

from copytrade_worker import rules
from copytrade_worker.types import Hop, Plan, Signal, Skip, User

TOK = "imeme-2.v2-launchpad.justhoot.near"
POOL = TOK + "|wrap.near|10000"
GOOD = {"slippage_bps": 300, "price_guard_x": 1.5}


def sig(**kw):
    d = dict(
        target_id="root.near",
        token_id=TOK,
        kind="swap",
        tx_hash="T",
        source="teardata",
        seen_at=1,
        id=7,
        venue="dcl",
        fill_price=2e-6,
        mcap_usd=500_000.0,
        hops=(Hop(POOL, "wrap.near", TOK),),
    )
    d.update(kw)
    return Signal(**d)


def user(settings=None, **kw):
    d = dict(
        owner_id="o.near",
        vault_id="v.o.near",
        bot_on=True,
        settings=GOOD if settings is None else settings,
        target_enabled=True,
    )
    d.update(kw)
    return User(**d)


@pytest.mark.parametrize(
    "s,u,reason",
    [
        (sig(), user(bot_on=False, vault_id=None), "bot_off"),
        (sig(), user(vault_id=None), "no_vault"),
        (sig(), user({}), "no_slippage"),
        (sig(), user({"slippage_bps": -1}), "no_slippage"),
        (sig(), user({"slippage_bps": 1.5}), "no_slippage"),
        (sig(), user({"slippage_bps": True}), "no_slippage"),
        (sig(), user(target_enabled=False), "target_disabled"),
        (sig(), user({"slippage_bps": 1, "price_guard_x": 0}), "bad_settings"),
        (sig(), user({"slippage_bps": 1, "ladder": []}), "bad_settings"),
        (sig(), user({"slippage_bps": 1, "ladder": [{"max_mcap_usd": 1e6, "size_near": "abc"}]}), "bad_settings"),
        (sig(), user({"slippage_bps": 1, "ladder": [{"max_mcap_usd": 1e6, "size_near": "-1"}]}), "bad_settings"),
        (sig(), user({"slippage_bps": 1, "min_mcap_usd": "x"}), "bad_settings"),
        (sig(token_id="usdt.tether-token.near"), user(), "not_meme"),
        (sig(token_id="eth.omft.near"), user(), "not_meme"),
        (sig(token_id="Bad Token"), user(), "not_meme"),
        (sig(mcap_usd=None), user(), "mcap_unknown"),
        (sig(mcap_usd=0.0), user(), "mcap_unknown"),
        (sig(mcap_usd=float("nan")), user(), "mcap_unknown"),
        (sig(mcap_usd=4_999.0), user({"slippage_bps": 1, "min_mcap_usd": 5000}), "mcap_below_min"),
        (sig(mcap_usd=3_000_000.01), user(), "mcap_above_max"),
        (sig(), user(copy_buy_status="planned"), "already_bought"),
        (sig(), user(copy_buy_status="sent"), "already_bought"),
        (sig(), user(copy_buy_status="filled"), "already_bought"),
        (sig(sniping_tax=True), user(), "sniping_tax"),
    ],
)
def test_skips(s, u, reason):
    r = rules.plan(s, u)
    assert isinstance(r, Skip) and r.reason == reason


@pytest.mark.parametrize(
    "mcap,near",
    [(1.0, 10), (999_999.99, 10), (1_000_000, 5), (2_000_000, 5), (3_000_000, 5)],
)
def test_default_ladder_bands(mcap, near):
    r = rules.plan(sig(mcap_usd=mcap), user({"slippage_bps": "unlimited"}))
    assert isinstance(r, Plan) and r.amount_in == str(near * 10**24)


def test_custom_ladder_unsorted_and_fractional():
    st = {
        "slippage_bps": 0,
        "ladder": [{"max_mcap_usd": 2e6, "size_near": "0.5"}, {"max_mcap_usd": 5e5, "size_near": 2}],
    }
    assert rules.plan(sig(mcap_usd=1e5), user(st)).amount_in == str(2 * 10**24)
    assert rules.plan(sig(mcap_usd=1e6), user(st)).amount_in == str(5 * 10**23)


def test_plan_fields_and_failed_row_does_not_block():
    r = rules.plan(sig(), user(copy_buy_status="failed"))
    assert isinstance(r, Plan)
    assert (r.owner_id, r.vault_id, r.signal_id, r.token_id, r.venue) == ("o.near", "v.o.near", 7, TOK, "dcl")
    assert r.hops == (Hop(POOL, "wrap.near", TOK),)
    m = r.min_out_inputs
    assert (m.price_guard_x, m.slippage_bps, m.fill_price, m.pool_price) == (1.5, 300, 2e-6, None)


def test_unlimited_slippage_no_guard():
    m = rules.plan(sig(), user({"slippage_bps": "unlimited"})).min_out_inputs
    assert (m.price_guard_x, m.slippage_bps) == (None, "unlimited")


@pytest.mark.parametrize(
    "hops,venue,ok",
    [
        ((Hop("p1", "wrap.near", "token.rhealab.near"), Hop("p2", "token.rhealab.near", TOK)), "dcl", True),
        ((Hop("p1", "usdc.near", TOK),), "dcl", False),  # not paid in wNEAR
        ((Hop("p1", "wrap.near", "x.near"), Hop("p2", "x.near", TOK)), "dcl", False),  # intermediate not allowed
        ((Hop("p1", "wrap.near", TOK),), None, False),  # unknown venue
        ((), "ref_v1", False),
    ],
)
def test_route_hint(hops, venue, ok):
    r = rules.plan(sig(hops=hops, venue=venue), user())
    assert isinstance(r, Plan)
    assert (r.hops == hops and r.venue == venue) if ok else (r.hops == () and r.venue is None)


def test_meme_rule_names():
    for t, rule in (
        (TOK, "launchpad v2-launchpad.justhoot.near"),
        ("blackdragon.tkn.near", "launchpad tkn.near"),
        ("token.0xshitzu.near", "not on denylist"),
        ("wrap.near", "not a meme: wNEAR"),
        ("token.rhealab.near", "not a meme: RHEA"),
    ):
        assert rules.meme_rule(t)[1] == rule
    for t in (None, "", "UPPER.near", "a b.near", "x" * 65 + ".near"):
        assert not rules.is_meme(t)
