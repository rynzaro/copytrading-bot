"""Fake NEAR RPC for tests: views from dicts, records sent txs, scripted outcomes."""
from __future__ import annotations

import base64
import json

from copytrade_worker.nearrpc import RpcError
from copytrade_worker.route import DCL, REF_V1


def event(name, data):
    return "EVENT_JSON:" + json.dumps({"standard": "copytrade_vault", "version": "1.0.0", "event": name, "data": [data]})


def outcome(logs=(), failure=None):
    st = {"Failure": failure} if failure else {"SuccessValue": ""}
    return {"status": st, "transaction_outcome": {"outcome": {"logs": [], "status": {"SuccessReceiptId": "x"}}},
            "receipts_outcome": [{"outcome": {"logs": list(logs), "status": st}}]}


class FakeRpc:
    def __init__(self):
        self.dcl: dict[str, dict] = {}
        self.ref: dict[int, dict] = {}
        self.meta: dict[str, int] = {}
        self.storage: dict[tuple[str, str], dict | None] = {}
        self.state: dict[str, dict] = {}
        self.access: dict = {"nonce": 100, "permission": {"FunctionCall": {"allowance": str(10 ** 24),
                                                                         "receiver_id": "v", "method_names": []}}}
        self.sent: list[dict] = []
        self.outcomes: list = []        # popped per tx_status call for a new hash; dict, None (pending), or callable
        self.send_errors: list = []     # popped per send_tx: None = ok, else error object
        self.views = 0
        self._res: dict[str, object] = {}

    def view(self, account, method, args):
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

    def view_access_key(self, account, public_key):
        return self.access

    def block_hash(self):
        return "11111111111111111111111111111111"

    def send_tx(self, b64):
        err = self.send_errors.pop(0) if self.send_errors else None
        if err is not None:
            raise RpcError(err)
        raw = base64.b64decode(b64)
        self.sent.append({"b64": b64, "raw": raw})
        return "h"

    def tx_status(self, tx_hash, sender):
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
