"""Bot tx gas (ticket 0010 item 1). Sandbox: every buy shape needs >= 180 Tgas prepaid (the vault reserves
150 Tgas for its checked callback); register_token >= 40. The 1 NEAR allowance refuses 300 Tgas."""
import importlib

import pytest

from copytrade_worker import executor as E

TGAS = 10 ** 12


def reload(monkeypatch, **env):
    for k in ("SWAP_GAS_TGAS", "REGISTER_GAS_TGAS"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return importlib.reload(E)


def test_defaults_from_sandbox_measurements(monkeypatch):
    m = reload(monkeypatch)
    assert m.SWAP_GAS == 200 * TGAS
    assert m.REGISTER_GAS == 50 * TGAS


@pytest.mark.parametrize(("env", "want"), [("300", 200), ("100", 180), ("190", 190), ("junk", 200)])
def test_swap_gas_env_clamped(monkeypatch, env, want):
    assert reload(monkeypatch, SWAP_GAS_TGAS=env).SWAP_GAS == want * TGAS


@pytest.mark.parametrize(("env", "want"), [("300", 200), ("10", 40), ("60", 60)])
def test_register_gas_env_clamped(monkeypatch, env, want):
    assert reload(monkeypatch, REGISTER_GAS_TGAS=env).REGISTER_GAS == want * TGAS


def teardown_module():
    importlib.reload(E)
