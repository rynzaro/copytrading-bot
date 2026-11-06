"""Worker side of the sandbox e2e (contracts/vault/tests/e2e.rs drives it). Never for mainnet: it refuses any
RPC URL that is not localhost.

Runs the real API (bot key, /vault-confirmed with the pinned code hash, wNEAR registration hook) and the real
executor (DRY_RUN off) against a near-workspaces sandbox. JSON lines on stdout, commands on stdin:
    start -> {"public_key"}       (POST /bot-key over HTTP)
    confirm <vault>               -> {"status", "body"}   (POST /vault-confirmed over HTTP)
    buy <token> <yocto>           -> {"result", "row"}    (Executor.run on a planned copy buy, DCL token|wrap.near|2000)
    quit
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse

from copytrade_worker import api, db, keys, service
from copytrade_worker.chain import Chain
from copytrade_worker.executor import Executor
from copytrade_worker.types import Hop, MinOutInputs, Plan


def out(obj: object) -> None:
    print(json.dumps(obj), flush=True)


def post(base: str, path: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(base + path, json.dumps(body).encode(), {"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rpc", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--owner", required=True)
    ap.add_argument("--wasm", required=True, help="vault wasm: its sha256 is the pinned VAULT_CODE_HASH")
    a = ap.parse_args()
    if urlparse(a.rpc).hostname not in ("127.0.0.1", "localhost", "::1"):
        sys.exit("sandbox only: --rpc must be localhost")
    with open(a.wasm, "rb") as f:
        code_hash = keys.b58e(hashlib.sha256(f.read()).digest())
    path = f"{a.data}/copytrade.db"
    conn = db.connect(path)
    conn.execute("INSERT OR IGNORE INTO users (owner_id, created_at) VALUES (?, 0)", (a.owner,))
    rpc = Chain([a.rpc], min_gap_s=0)
    ks = keys.KeyStore(a.data)
    ex = Executor(db.connect(path), rpc, ks, dry_run=False, outcome_timeout_s=30, poll_s=0.5)
    app = api.App(db.connect(path), rpc, ks, code_hash=code_hash, on_confirmed=service.register_wrap(ex))
    srv = api.serve(app, port=0, host="127.0.0.1")
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    for line in sys.stdin:
        cmd = line.split()
        if not cmd:
            continue
        if cmd[0] == "start":
            out(post(base, "/bot-key", {"owner_id": a.owner})[1] | {"code_hash": code_hash})
        elif cmd[0] == "confirm":
            c, b = post(base, "/vault-confirmed", {"owner_id": a.owner, "vault_id": cmd[1]})
            out({"status": c, "body": b})
        elif cmd[0] == "buy":
            token, amount = cmd[1], cmd[2]
            vault = conn.execute("SELECT vault_id FROM users WHERE owner_id = ?", (a.owner,)).fetchone()[0]
            cur = conn.execute("INSERT INTO signals (target_id, token_id, kind, tx_hash, seen_at, source)"
                               " VALUES ('whale.test.near', ?, 'swap', 'e2e', ?, 'teardata')", (token, db.now_ms()))
            mi = MinOutInputs(price_guard_x=None, slippage_bps=100, fill_price=None, pool_price=None)
            hop = Hop(f"{token}|wrap.near|2000", "wrap.near", token)  # sandbox mock pool; route reads it via get_pool
            plan = Plan(a.owner, vault, int(cur.lastrowid or 0), token, "dcl", (hop,), amount, mi)
            assert db.claim_copy_buy(conn, plan)
            res = ex.run(plan)
            row = conn.execute("SELECT status, reason, amount_out, min_out, tx_hash FROM copy_buys"
                               " WHERE owner_id = ? AND token_id = ?", (a.owner, token)).fetchone()
            out({"result": res, "row": dict(row)})
        elif cmd[0] == "quit":
            break
    srv.shutdown()


if __name__ == "__main__":
    main()
