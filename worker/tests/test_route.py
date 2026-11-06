from fractions import Fraction

import pytest

from copytrade_worker import route as R
from copytrade_worker.types import MinOutInputs

from .fakes import FakeRpc, dcl_pool, ref_pool

E24, E18 = 10 ** 24, 10 ** 18
MEME = "meme.near"


def rpc_with_pools():
    f = FakeRpc()
    # Ref v1: 1000 NEAR vs 1_000_000 MEME (18 dec): 0.001 NEAR/MEME, 0.3% fee
    f.ref[7] = ref_pool(R.WNEAR, MEME, 1000 * E24, 10 ** 6 * E18, fee=30)
    # DCL meme|wrap|2000 at point 0: 1 raw wrap per raw meme, tiny wNEAR depth
    f.dcl["meme.near|wrap.near|2000"] = dcl_pool(MEME, R.WNEAR, 2000, 0, ty=5 * E24)
    return f


def test_ref_v1_direct_rate_and_price():
    f = rpc_with_pools()
    r = R.pick_route(f, MEME, [("ref_v1", "7")])
    assert r.venue == "ref_v1" and r.hops == [{"pool_id": "7", "token_in": R.WNEAR, "token_out": MEME}]
    assert r.rate == Fraction(10 ** 6 * E18, 1000 * E24) * Fraction(9970, 10000)
    assert float(r.price_near) == pytest.approx(0.001 / 0.997)


def test_picks_deepest_pool():
    f = rpc_with_pools()
    r = R.pick_route(f, MEME, [("dcl", "meme.near|wrap.near|2000"), ("ref_v1", "7")])
    assert r.venue == "ref_v1"
    f.dcl["meme.near|wrap.near|2000"]["total_y"] = str(5000 * E24)
    assert R.pick_route(f, MEME, [("dcl", "meme.near|wrap.near|2000"), ("ref_v1", "7")]).venue == "dcl"


def test_dcl_direction_token_x_vs_y():
    f = FakeRpc()
    # wNEAR is token_y here; point 6932 ~= 2.0 raw wrap per raw meme
    f.dcl["meme.near|wrap.near|400"] = dcl_pool(MEME, R.WNEAR, 400, 6932)
    r = R.pick_route(f, MEME, [("dcl", "meme.near|wrap.near|400")])
    assert float(r.rate) == pytest.approx(1 / 1.0001 ** 6932 * (1 - 0.0004))
    # token with name sorting after wrap.near: wNEAR is token_x, sides flip
    f.dcl["wrap.near|zzz.near|400"] = dcl_pool(R.WNEAR, "zzz.near", 400, 6932)
    r2 = R.pick_route(f, "zzz.near", [("dcl", "wrap.near|zzz.near|400")])
    assert float(r2.rate) == pytest.approx(1.0001 ** 6932 * (1 - 0.0004))


def test_dcl_not_running_or_mismatched_id_skipped():
    f = FakeRpc()
    f.dcl["meme.near|wrap.near|400"] = dcl_pool(MEME, R.WNEAR, 400, 0, state="Paused")
    with pytest.raises(R.NoRoute):
        R.pick_route(f, MEME, [("dcl", "meme.near|wrap.near|400")])
    f.dcl["meme.near|wrap.near|400"] = dcl_pool("evil.near", R.WNEAR, 400, 0)
    with pytest.raises(R.NoRoute):
        R.pick_route(f, MEME, [("dcl", "meme.near|wrap.near|400")])


def test_via_rhea_dcl_uses_deepest_bridge():
    f = FakeRpc()
    f.dcl[R.dcl_pool_id(MEME, R.RHEA, 2000)] = dcl_pool(MEME, R.RHEA, 2000, 0)
    f.dcl[R.dcl_pool_id(R.RHEA, R.WNEAR, 2000)] = dcl_pool(R.RHEA, R.WNEAR, 2000, 0, tx=E18, ty=E24)
    f.dcl[R.dcl_pool_id(R.RHEA, R.WNEAR, 400)] = dcl_pool(R.RHEA, R.WNEAR, 400, 0, tx=E18, ty=50 * E24)
    r = R.pick_route(f, MEME, [("dcl", R.dcl_pool_id(MEME, R.RHEA, 2000))])
    assert r.hops == [{"pool_id": "token.rhealab.near|wrap.near|400", "token_in": R.WNEAR, "token_out": R.RHEA},
                      {"pool_id": "meme.near|token.rhealab.near|2000", "token_in": R.RHEA, "token_out": MEME}]
    assert r.rate == Fraction(9996, 10000) * Fraction(998, 1000)


def test_via_rhea_ref_needs_configured_bridge(monkeypatch):
    f = FakeRpc()
    f.ref[3] = ref_pool(R.RHEA, MEME, 100 * E18, 100 * E18)
    monkeypatch.delenv("REF_V1_RHEA_POOLS", raising=False)
    with pytest.raises(R.NoRoute):
        R.pick_route(f, MEME, [("ref_v1", "3")])
    f.ref[9] = ref_pool(R.WNEAR, R.RHEA, 10 * E24, 20 * E18, fee=20)
    monkeypatch.setenv("REF_V1_RHEA_POOLS", "9")
    r = R.pick_route(f, MEME, [("ref_v1", "3")])
    assert [h["pool_id"] for h in r.hops] == ["9", "3"]


def test_rejects_non_quote_pair_and_unknown_venue_and_stable_pool():
    f = FakeRpc()
    f.ref[1] = ref_pool("usdt.near", MEME, E24, E24)
    f.ref[2] = ref_pool(R.WNEAR, MEME, E24, E24, kind="STABLE_SWAP")
    with pytest.raises(R.NoRoute):
        R.pick_route(f, MEME, [("ref_v1", "1"), ("ref_v1", "2"), ("intear", "x"), ("ref_v1", "404")])


def route(rate=Fraction(1000), dec=18):
    return R.Route("ref_v1", [], rate, dec, Fraction(1))


def test_min_out_unlimited_no_guard_is_one():
    assert R.min_out(10 * E24, route(), MinOutInputs(None, "unlimited", 0.001, None)) == 1


def test_min_out_slippage_only():
    # 10 NEAR, rate 1000 raw/yocto, 1% slippage -> 10e27 / 1.01
    assert R.min_out(10 * E24, route(), MinOutInputs(None, 100, None, None)) == int(Fraction(10 * E24 * 1000) / Fraction(101, 100))


def test_min_out_guard_only_and_tightest_wins():
    # rate 1000 raw/yocto at 18 dec -> price 1e18/1e27 = 1e-9 NEAR/token... use rate giving 0.001 NEAR/token
    r = route(rate=Fraction(10 ** 18, 10 ** 21))   # 1000 tokens per NEAR -> 0.001 NEAR/token
    assert float(r.price_near) == pytest.approx(0.001)
    g = R.min_out(10 * E24, r, MinOutInputs(2.0, "unlimited", 0.001, None))   # max 0.002 -> 5000 tokens
    assert g == 5000 * E18
    s = R.min_out(10 * E24, r, MinOutInputs(2.0, 50, 0.001, None))           # slippage tighter
    assert s == int(10 * E24 * r.rate / Fraction(10050, 10000)) and s > g
    t = R.min_out(10 * E24, r, MinOutInputs(1.001, 5000, 0.001, None))        # guard tighter
    assert t == int(Fraction(10 * E24) * E18 / (Fraction("1.001") * Fraction("0.001") * E24))


def test_min_out_errors():
    r = route(rate=Fraction(10 ** 18, 10 ** 21))
    with pytest.raises(R.LimitError, match="slippage_unset"):
        R.min_out(E24, r, MinOutInputs(None, None, None, None))
    with pytest.raises(R.LimitError, match="price_guard"):
        R.min_out(E24, r, MinOutInputs(0.9, "unlimited", 0.001, None))
    with pytest.raises(R.LimitError, match="no_fill_price"):
        R.min_out(E24, r, MinOutInputs(2.0, "unlimited", None, None))
    with pytest.raises(R.LimitError, match="bad_amount"):
        R.min_out(0, r, MinOutInputs(None, "unlimited", None, None))
