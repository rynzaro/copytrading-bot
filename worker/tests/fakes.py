"""Fake NEAR RPC for tests: views from dicts, records sent txs, scripted outcomes."""
from __future__ import annotations

import base64
import json

from copytrade_worker.chain import RpcDown, RpcError
from copytrade_worker.route import DCL, REF_V1


def event(name, data):
    e = {"standard": "copytrade_vault", "version": "1.0.0", "event": name, "data": [data]}
    return "EVENT_JSON:" + json.dumps(e)


VAULT = "alice-vault.near"


def outcome(logs=(), failure=None, failed_at="first", emitter=VAULT, failed_executor=VAULT):
    """Tx outcome like the RPC's. Receipt r0 = the vault call itself (tx's first receipt); logs come from r1
    (a vault callback) when there is one. failed_at: 'first' = the vault call itself panicked; 'later' = a
    later receipt (executor failed_executor) failed after the call ran."""
    ok = {"SuccessValue": ""}
    fail = {"Failure": failure} if failure else None
    rs = [{"id": "r0", "outcome": {"executor_id": VAULT, "logs": [], "status": ok, "receipt_ids": ["r1"]}},
          {"id": "r1", "outcome": {"executor_id": emitter, "logs": list(logs), "status": ok, "receipt_ids": []}}]
    if fail and failed_at == "first":
        rs = [{"id": "r0", "outcome": {"executor_id": VAULT, "logs": list(logs), "status": fail, "receipt_ids": []}}]
    elif fail:
        rs.append({"id": "r2", "outcome": {"executor_id": failed_executor, "logs": [], "status": fail,
                                           "receipt_ids": []}})
    return {"status": fail or ok,
            "transaction_outcome": {"id": "t", "outcome": {"executor_id": VAULT, "logs": [], "receipt_ids": ["r0"],
                                                           "status": {"SuccessReceiptId": "r0"}}},
            "receipts_outcome": rs}


def swap_error(error, token_out="meme.near", **kw):
    """Outcome whose vault callback emitted swap_result {error}."""
    return outcome([event("swap_result", {"token_out": token_out, "error": error})], **kw)


class FakeRpc:
    def __init__(self):
        self.dcl: dict[str, dict] = {}
        self.ref: dict[int, dict] = {}
        self.meta: dict[str, int] = {}
        self.storage: dict[tuple[str, str], dict | None] = {}
        self.state: dict[str, dict] = {}
        self.accounts: dict[str, dict] = {}  # view_account replies
        self.access: dict = {"nonce": 100, "permission": {"FunctionCall": {"allowance": str(10 ** 24),
                                                                         "receiver_id": "v", "method_names": []}}}
        self.sent: list[dict] = []
        self.outcomes: list = []        # popped per tx_status call for a new hash; dict, None (pending), or callable
        self.send_errors: list = []     # popped per send_tx: None = ok, else error object
        self.views = 0
        self.status_calls: list[tuple[str, str]] = []
        self.down: set[str] = set()     # method names that raise RpcDown (no endpoint answered)
        self.url = "http://rpc.test"
        self._res: dict[str, object] = {}

    def _maybe_down(self, name):
        if name in self.down:
            raise RpcDown(name)

    def call_view(self, account, method, args):
        self._maybe_down("call_view")
        self.views += 1
        if account == DCL and method == "get_pool":
            return self.dcl.get(args["pool_id"])
        if account == REF_V1 and method == "get_pool":
            if args["pool_id"] not in self.ref:
                raise RpcError("no pool")
            return self.ref[args["pool_id"]]
        if method == "ft_metadata":
            return {"decimals": self.meta.get(account, 18)}
        if method == "storage_balance_of":
            return self.storage.get((account, args["account_id"]))
        if method == "get_state":
            return self.state.get(account)
        raise RpcError(f"unknown view {account}.{method}")

    def view_account(self, account):
        self._maybe_down("view_account")
        if account not in self.accounts:
            raise RpcError(f"unknown account {account}")
        return self.accounts[account]

    def view_access_key(self, account, public_key):
        self._maybe_down("view_access_key")
        return self.access

    def block_hash(self):
        self._maybe_down("block_hash")
        return "11111111111111111111111111111111"

    def send_tx(self, b64):
        err = self.send_errors.pop(0) if self.send_errors else None
        if isinstance(err, Exception):
            raise err
        if err is not None:
            raise RpcError(err)
        raw = base64.b64decode(b64)
        self.sent.append({"b64": b64, "raw": raw})
        return "h"

    def tx_status(self, tx_hash, sender):
        self._maybe_down("tx_status")
        self.status_calls.append((tx_hash, sender))
        if tx_hash not in self._res:
            o = self.outcomes.pop(0) if self.outcomes else outcome()
            self._res[tx_hash] = o
        o = self._res[tx_hash]
        return o(self) if callable(o) else o


def dcl_pool(x, y, fee, point, tx=10 ** 24, ty=10 ** 24, state="Running"):
    return {"pool_id": f"{x}|{y}|{fee}", "token_x": x, "token_y": y, "fee": fee, "current_point": point,
            "total_x": str(tx), "total_y": str(ty), "state": state}


def ref_pool(a, b, ra, rb, fee=30, kind="SIMPLE_POOL"):
    return {"pool_kind": kind, "token_account_ids": [a, b], "amounts": [str(ra), str(rb)], "total_fee": fee}
