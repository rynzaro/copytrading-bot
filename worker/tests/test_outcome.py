"""classify: a swap is 'failed' (row reusable, retry allowed) only when the outcome proves no input left the
vault. Anything else that isn't a clean fill is 'unknown': the row stays 'sent' and is never resent."""
import re
from pathlib import Path

import pytest

from copytrade_worker import executor as X

from .fakes import VAULT, event, outcome, swap_error

MEME = "meme.near"
DCL = "dclv2.ref-labs.near"
LIB = Path(__file__).resolve().parents[2] / "contracts" / "vault" / "src" / "lib.rs"


def cls(res):
    return X.classify(res, VAULT, MEME)


def test_every_vault_swap_error_is_classified_explicitly():
    """Drift guard: each swap_result error string in lib.rs is either proof of no input moved, or known unknown."""
    src = LIB.read_text()
    found = set(re.findall(r'swap_failed\(&\w+, (?:format!\()?"([^"]+)"', src))
    found |= set(re.findall(r'error: Some\("([^"]+)"', src))
    found = {re.sub(r"\{\w+\}", "{token}", s) for s in found}
    assert found == set(X.NO_INPUT_LEFT) | X.INPUT_MAYBE_LEFT


@pytest.mark.parametrize("error,reason", [
    ("paused", "paused"),
    ("amount_in above balance", "balance_low"),
    (f"vault not registered on {MEME}", "not_registered"),
    ("vault not registered on wrap.near", "not_registered"),
    (f"ft_balance_of failed on {MEME}", "balance_check_failed"),
    ("ft_balance_of failed on wrap.near", "balance_check_failed"),
    ("swap failed, input refunded", "swap_refunded"),
])
def test_no_input_left_errors_are_failed(error, reason):
    assert cls(swap_error(error)) == ("failed", reason)


def test_refund_after_venue_panic_is_failed():
    """Venue's ft_on_transfer panicked (e.g. min_out): wrap.near refunds, vault says so."""
    res = swap_error("swap failed, input refunded", failure={"ActionError": "x"}, failed_at="later",
                     failed_executor=DCL)
    assert cls(res) == ("failed", "swap_refunded")


@pytest.mark.parametrize("res", [
    swap_error("output balance unknown"),                       # venue took the input, output unread
    swap_error("min_out"),                                      # not a vault string
    swap_error("vault not registered on other.near"),           # not our token
    swap_error("paused", emitter=MEME),                         # forged by a token contract, not the vault
    swap_error("swap failed, input refunded", failure={"ActionError": "x"}, failed_at="later",
               failed_executor="wrap.near"),                    # wrap.near failed: refund (resolve) not proven
    outcome(failure={"ActionError": "x"}, failed_at="later"),   # later receipt failed, no verdict
    outcome(failure={"ActionError": "x"}, failed_at="later", failed_executor=DCL),
    outcome(),                                                  # success without our event
    outcome([event("swap_result", {"token_out": MEME, "amount_out": "junk"})]),
    outcome([event("swap_result", {"token_out": MEME, "amount_out": "5"})], failure={"x": 1}, failed_at="later"),
    {"status": {"Failure": {"x": 1}}},                          # no receipts to tell where
])
def test_outcome_unknown(res):
    assert cls(res) == ("unknown", None)


def test_first_receipt_panic_is_failed():
    """The vault call itself panicked: its promises never ran, nothing left the vault."""
    assert cls(outcome(failure={"ActionError": "Smart contract panicked: paused"})) == ("failed", "paused")
    assert cls(outcome(failure={"ActionError": "Smart contract panicked: hops"})) == ("failed", "swap_rejected")


def test_filled():
    assert cls(outcome([event("swap_result", {"token_out": MEME, "amount_out": "9"})])) == ("filled", "9")


def test_retry_only_on_refund():
    assert X.RETRY_REASONS == {"swap_refunded"}
