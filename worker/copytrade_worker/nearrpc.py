"""Small NEAR JSON-RPC client (stdlib). Treat every reply as untrusted data."""
from __future__ import annotations

import base64
import json
import os
import urllib.request
from typing import Any

DEFAULT_RPC = "https://rpc.mainnet.near.org"


class RpcError(Exception):
    def __init__(self, error: Any):
        self.error = error
        super().__init__(json.dumps(error)[:300] if not isinstance(error, str) else error[:300])

    @property
    def text(self) -> str:
        return json.dumps(self.error) if not isinstance(self.error, str) else self.error


class Rpc:
    def __init__(self, url: str | None = None, timeout: float = 10):
        self.url = url or os.environ.get("NEAR_RPC_URL", DEFAULT_RPC)
        self.timeout = timeout

    def call(self, method: str, params: Any) -> Any:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        req = urllib.request.Request(self.url, body, {"content-type": "application/json", "user-agent": "copytrade-worker"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            d = json.load(r)
        if "error" in d:
            raise RpcError(d["error"])
        res = d["result"]
        if isinstance(res, dict) and "error" in res and "result" not in res:
            raise RpcError(res["error"])
        return res

    def view(self, account: str, method: str, args: dict) -> Any:
        r = self.call("query", {"request_type": "call_function", "finality": "final", "account_id": account,
                                "method_name": method, "args_base64": base64.b64encode(json.dumps(args).encode()).decode()})
        return json.loads(bytes(r["result"]).decode())

    def view_access_key(self, account: str, public_key: str) -> dict:
        return self.call("query", {"request_type": "view_access_key", "finality": "final",
                                   "account_id": account, "public_key": public_key})

    def block_hash(self) -> str:
        return self.call("block", {"finality": "final"})["header"]["hash"]

    def send_tx(self, signed_b64: str) -> str:
        r = self.call("send_tx", {"signed_tx_base64": signed_b64, "wait_until": "NONE"})
        return r.get("transaction", {}).get("hash", "") if isinstance(r, dict) else ""

    def tx_status(self, tx_hash: str, sender: str) -> dict | None:
        try:
            return self.call("tx", {"tx_hash": tx_hash, "sender_account_id": sender, "wait_until": "EXECUTED_OPTIMISTIC"})
        except RpcError as e:
            if "UNKNOWN_TRANSACTION" in e.text or "TIMEOUT" in e.text.upper():
                return None
            raise
