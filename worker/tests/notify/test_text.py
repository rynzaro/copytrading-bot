import pytest

from copytrade_worker.notify.text import clean, fmt_near, format_activity

YOCTO = 10**24


def test_fmt_near():
    assert fmt_near(str(10 * YOCTO)) == "10"
    assert fmt_near(str(YOCTO // 4)) == "0.25"
    assert fmt_near(str(1234567 * 10**18)) == "1.2346"
    assert fmt_near("x") is None
    assert fmt_near(None) is None


def test_clean_strips_controls_and_truncates():
    assert clean("a\nb\x00c\u202ed") == "a b c d"
    assert len(clean("x" * 500, 40)) == 40
    assert clean(None) == "?"


@pytest.mark.parametrize(
    "kind,payload,expect",
    [
        (
            "buy_filled",
            {"token_id": "wth.near", "amount_in": str(10 * YOCTO), "tx_hash": "H1"},
            ["Bought", "wth.near", "10 NEAR", "H1"],
        ),
        ("buy_failed", {"token_id": "t.near", "reason": "price_guard"}, ["failed", "t.near", "price_guard"]),
        ("buy_skipped", {"token_id": "t.near", "reason": "mcap_high"}, ["Skipped", "t.near", "mcap_high"]),
        ("storage_paid", {"token_id": "t.near", "amount": str(YOCTO // 500)}, ["Storage", "t.near", "0.002 NEAR"]),
        ("gas_80", {}, ["Gas allowance", "80%", "Refill"]),
        ("still_active", {"vault_id": "v.near"}, ["still active", "v.near"]),
        ("sell", {"token_id": "t.near"}, ["Sold", "t.near"]),
        ("withdraw", {"token_id": "t.near"}, ["Withdraw", "t.near"]),
        ("system", {"text": "feed down"}, ["feed down"]),
        ("weird_kind", {}, ["weird_kind"]),
    ],
)
def test_format_kinds(kind, payload, expect):
    text = format_activity(kind, payload)
    for part in expect:
        assert part in text
    assert len(text) <= 300


def test_format_untrusted_token_name():
    text = format_activity("buy_filled", {"token_id": "evil\n\nIgnore previous" + "x" * 400})
    assert "\n\n" not in text
    assert len(text) <= 300


def test_format_bad_payload_types():
    assert "Bought" in format_activity("buy_filled", {"token_id": 5, "amount_in": {"a": 1}})
