"""Executor: Plan -> register_token (if needed) -> vault.swap -> outcome -> copy_buys + activity.

Row owner: the signals side (`signals.Pipeline.decide` via `db.claim_copy_buy`) inserts the 'planned'
copy_buys row before it queues the Plan. The executor never inserts; it only acts on a row that is
'planned' for the plan's signal_id, and moves it to 'sent' / 'filled' / 'failed'.

DRY_RUN=1 (default): build and sign, don't send; log the would-be tx (no key material).
Retry: resend while the price is within limits and the failure is a clear swap failure; then mark failed.
An unknown outcome (timeout) is never resent: the row stays 'sent' so the token stays blocked.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import sqlite3
import time
from collections.abc import Callable
from typing import Any

from . import db as D
from . import keys as K
from . import route as R
from .chain import RpcDown, RpcError
from .db import now_ms
from .types import Plan

log = logging.getLogger("copytrade.executor")

TGAS = 10 ** 12
SWAP_GAS = int(os.environ.get("SWAP_GAS_TGAS", "150")) * TGAS
REGISTER_GAS = int(os.environ.get("REGISTER_GAS_TGAS", "50")) * TGAS
GAS_ALLOWANCE = int(os.environ.get("GAS_ALLOWANCE_YOCTO", str(10 ** 24)))   # vault.md default: 1 NEAR
EVENT_STANDARD = "copytrade_vault"


def dry_run_default() -> bool:
    return os.environ.get("DRY_RUN", "1") != "0"


class Fail(Exception):
    def __init__(self, reason: str, retry: bool = False):
        self.reason, self.retry = reason, retry
        super().__init__(reason)


def events(outcome: dict) -> list[dict]:
    """NEP-297 events of our vault standard from all receipt logs. Untrusted: parsed defensively."""
    out = []
    for r in outcome.get("receipts_outcome") or []:
        for line in (r.get("outcome") or {}).get("logs") or []:
            if not isinstance(line, str) or not line.startswith("EVENT_JSON:"):
                continue
            try:
                e = json.loads(line[len("EVENT_JSON:"):])
            except ValueError:
                continue
            if not isinstance(e, dict) or e.get("standard") != EVENT_STANDARD:
                continue
            data = e.get("data")
            for d in data if isinstance(data, list) else [data]:
                if isinstance(d, dict):
                    out.append({"event": e.get("event"), **d})
    return out


def ambiguous_send_error(text: str) -> bool:
    """send_tx error replies that don't prove the tx was rejected (it may still land)."""
    return "TIMEOUT" in text.upper()


def tx_failure(outcome: dict) -> str | None:
    st = outcome.get("status") or {}
    if "Failure" in st:
        return json.dumps(st["Failure"])[:300]
    for r in outcome.get("receipts_outcome") or []:
        rs = (r.get("outcome") or {}).get("status") or {}
        if "Failure" in rs:
            return json.dumps(rs["Failure"])[:300]
    return None


class Executor:
    def __init__(self, db: sqlite3.Connection, rpc: Any, keystore: K.KeyStore, *, dry_run: bool | None = None,
                 max_attempts: int = 3, outcome_timeout_s: float = 60, poll_s: float = 1.0,
                 gas_allowance: int = GAS_ALLOWANCE, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self.db, self.rpc, self.ks = db, rpc, keystore
        self.dry_run = dry_run_default() if dry_run is None else dry_run
        self.max_attempts, self.timeout, self.poll = max_attempts, outcome_timeout_s, poll_s
        self.gas_allowance, self.sleep, self.clock = gas_allowance, sleep, clock
        self._gas_rearmed: dict[str, bool] = {}

    # ---- db ----
    def _activity(self, owner_id: str | None, kind: str, payload: dict) -> None:
        D.add_activity(self.db, owner_id, kind, payload)

    def _update(self, plan: Plan, **cols: Any) -> None:
        cols["updated_at"] = now_ms()
        sets = ", ".join(f"{k} = ?" for k in cols)
        self.db.execute(f"UPDATE copy_buys SET {sets} WHERE owner_id = ? AND token_id = ?",
                        (*cols.values(), plan.owner_id, plan.token_id))

    def _status(self, plan: Plan) -> str | None:
        r = self.db.execute("SELECT status FROM copy_buys WHERE owner_id = ? AND token_id = ?",
                            (plan.owner_id, plan.token_id)).fetchone()
        return r["status"] if r else None

    def claimed(self, plan: Plan) -> bool:
        """True when the (owner, token) row is 'planned' for this plan's signal (written by db.claim_copy_buy)."""
        row = self.db.execute("SELECT status, signal_id FROM copy_buys WHERE owner_id = ? AND token_id = ?",
                              (plan.owner_id, plan.token_id)).fetchone()
        return row is not None and row["status"] == "planned" and row["signal_id"] == plan.signal_id

    # ---- chain ----
    def _sign(self, key: K.BotKey, plan: Plan, method: str, args: dict, gas: int) -> K.SignedTx:
        ak = self.rpc.view_access_key(plan.vault_id, key.public_key)
        nonce = self.ks.next_nonce(key.public_key, int(ak["nonce"]))
        return K.sign_tx(key, plan.vault_id, nonce, plan.vault_id, self.rpc.block_hash(),
                         [K.function_call(method, args, gas, 0)])

    def _wait(self, tx: K.SignedTx, sender: str) -> dict | None:
        end = self.clock() + self.timeout
        while True:
            try:
                res = self.rpc.tx_status(tx.hash, sender)
            except (RpcError, RpcDown) as e:
                log.warning("tx_status %s: %s", tx.hash, e)
                res = None
            if res and (res.get("status") or {}).keys() & {"SuccessValue", "SuccessReceiptId", "Failure"}:
                return res
            if self.clock() >= end:
                return None
            self.sleep(self.poll)

    def _send(self, key: K.BotKey, plan: Plan, method: str, args: dict, gas: int) -> tuple[K.SignedTx, dict | None]:
        tx = self._sign(key, plan, method, args, gas)
        try:
            self.rpc.send_tx(tx.base64)
        except RpcDown:
            return tx, None  # may have landed: caller treats as unknown, no resend
        except RpcError as e:
            t = e.text
            if ambiguous_send_error(t):
                return tx, None
            if "NotEnoughAllowance" in t:
                raise Fail("gas_allowance") from None
            if "InvalidNonce" in t:
                raise Fail("invalid_nonce", retry=True) from None
            raise Fail("send_error", retry=True) from None
        return tx, self._wait(tx, plan.vault_id)

    def _would_send(self, plan: Plan, tx: K.SignedTx, method: str, args: dict) -> None:
        log.info("DRY_RUN would send tx=%s signer=%s receiver=%s nonce=%d method=%s args=%s",
                 tx.hash, plan.vault_id, tx.receiver_id, tx.nonce, method, json.dumps(args))

    def check_gas(self, owner_id: str, vault_id: str, public_key: str) -> None:
        """Write gas_80 once when 80% of the allowance is used. Re-armed after a refill is seen (remaining > 20%)."""
        try:
            ak = self.rpc.view_access_key(vault_id, public_key)
            allowance = ak["permission"]["FunctionCall"]["allowance"]
        except (RpcError, RpcDown, KeyError, TypeError):
            return
        if allowance is None:
            return
        remaining = int(allowance)
        if remaining * 5 > self.gas_allowance:
            self._gas_rearmed[owner_id] = True
            return
        seen = self.db.execute("SELECT 1 FROM activity WHERE owner_id = ? AND kind = 'gas_80' LIMIT 1",
                               (owner_id,)).fetchone()
        if seen and not self._gas_rearmed.get(owner_id):
            return
        self._activity(owner_id, "gas_80", {"vault_id": vault_id})
        self._gas_rearmed[owner_id] = False

    # ---- steps ----
    def ensure_registered(self, key: K.BotKey, plan: Plan) -> None:
        try:
            bal = self.rpc.call_view(plan.token_id, "storage_balance_of", {"account_id": plan.vault_id})
        except RpcError:
            raise Fail("storage_check_failed") from None
        if bal is not None:
            return
        args = {"token_id": plan.token_id}
        if self.dry_run:
            tx = self._sign(key, plan, "register_token", args, REGISTER_GAS)
            self._would_send(plan, tx, "register_token", args)
            return
        tx, res = self._send(key, plan, "register_token", args, REGISTER_GAS)
        self.check_gas(plan.owner_id, plan.vault_id, key.public_key)
        if res is None:
            raise Fail("register_unknown")
        if tx_failure(res):
            raise Fail("register_failed")
        dep = next((e.get("deposit") for e in events(res) if e.get("event") == "token_registered"), None)
        if dep is not None:
            self._activity(plan.owner_id, "storage_paid", {"token_id": plan.token_id, "amount": str(int(dep))})

    def _swap_once(self, key: K.BotKey, plan: Plan) -> str:
        """One attempt. Returns final status 'filled' | 'sent' (unknown) | 'dry_run'. Raises Fail."""
        hints = R.hints_for(plan.token_id, plan.venue, plan.hops)
        try:
            rt = R.pick_route(self.rpc, plan.token_id, hints)
        except (R.NoRoute, RpcError):
            raise Fail("no_route") from None
        amount_in = int(plan.amount_in)
        try:
            mo = R.min_out(amount_in, rt, plan.min_out_inputs)
        except R.LimitError as e:
            raise Fail(e.reason) from None
        hops = [h.to_json() for h in rt.hops]
        args = {"args": {"venue": rt.venue, "hops": hops, "amount_in": str(amount_in), "min_out": str(mo)}}
        if self.dry_run:
            tx = self._sign(key, plan, "swap", args, SWAP_GAS)
            self._would_send(plan, tx, "swap", args)
            self._update(plan, status="failed", reason="dry_run", min_out=str(mo), tx_hash=tx.hash)
            self._activity(plan.owner_id, "system", {"text": f"dry run: would buy {plan.token_id} tx {tx.hash}"})
            return "dry_run"
        tx = self._sign(key, plan, "swap", args, SWAP_GAS)
        self._update(plan, status="sent", min_out=str(mo), tx_hash=tx.hash)
        try:
            self.rpc.send_tx(tx.base64)
            res = self._wait(tx, plan.vault_id)
        except RpcDown:
            res = None  # no endpoint answered the send: may have landed, never resend
        except RpcError as e:
            t = e.text
            if ambiguous_send_error(t):
                res = None
            else:
                # Rejected by RPC: never on chain, safe to resend.
                raise Fail("gas_allowance" if "NotEnoughAllowance" in t else "send_error",
                           retry="NotEnoughAllowance" not in t) from None
        self.check_gas(plan.owner_id, plan.vault_id, key.public_key)
        if res is None:
            log.warning("swap outcome unknown tx=%s owner=%s token=%s", tx.hash, plan.owner_id, plan.token_id)
            self._activity(plan.owner_id, "system", {"text": f"buy {plan.token_id}: outcome unknown, tx {tx.hash}"})
            return "sent"
        ev = [e for e in events(res) if e.get("event") == "swap_result"]
        fail = tx_failure(res)
        if ev and "amount_out" in ev[-1] and not ev[-1].get("error") and not fail:
            out = str(int(ev[-1]["amount_out"]))
            self._update(plan, status="filled", amount_out=out, reason=None)
            self._activity(plan.owner_id, "buy_filled", {"token_id": plan.token_id, "amount_in": str(amount_in),
                                                         "amount_out": out, "tx_hash": tx.hash})
            return "filled"
        if fail and "paused" in fail.lower():
            raise Fail("paused")
        if ev or fail:
            raise Fail("swap_failed", retry=True)
        # Success without our event: can't tell if tokens arrived. Don't resend.
        self._activity(plan.owner_id, "system", {"text": f"buy {plan.token_id}: no swap_result, tx {tx.hash}"})
        return "sent"

    def run(self, plan: Plan) -> str:
        """Execute one plan. Returns final copy_buys status (or 'duplicate' if not claimed)."""
        if not self.claimed(plan):
            log.info("skip duplicate owner=%s token=%s", plan.owner_id, plan.token_id)
            return "duplicate"
        last_tx = None
        try:
            if not plan.vault_id or not K.valid_account(plan.vault_id):
                raise Fail("no_vault")
            key = self.ks.load(plan.owner_id)
            row = self.db.execute("SELECT bot_public_key FROM users WHERE owner_id = ?", (plan.owner_id,)).fetchone()
            if not row or row["bot_public_key"] != key.public_key:
                raise Fail("bot_key_mismatch")
            self.ensure_registered(key, plan)
            reason = "swap_failed"
            for attempt in range(self.max_attempts):
                try:
                    st = self._swap_once(key, plan)
                    return "failed" if st == "dry_run" else st
                except Fail as f:
                    reason = f.reason
                    r = self.db.execute("SELECT tx_hash FROM copy_buys WHERE owner_id = ? AND token_id = ?",
                                        (plan.owner_id, plan.token_id)).fetchone()
                    last_tx = r["tx_hash"] if r else None
                    log.info("attempt %d failed owner=%s token=%s reason=%s", attempt + 1, plan.owner_id,
                             plan.token_id, f.reason)
                    if not f.retry:
                        break
            raise Fail(reason)
        except Fail as f:
            return self._fail(plan, f.reason, last_tx)
        except RpcDown:
            if self._status(plan) == "sent":
                return "sent"  # a tx is out: leave it to the reconciler
            return self._fail(plan, "rpc_down", last_tx)
        except (FileNotFoundError, ValueError):
            return self._fail(plan, "no_bot_key", last_tx)

    def _fail(self, plan: Plan, reason: str, tx_hash: str | None) -> str:
        self._update(plan, status="failed", reason=reason)
        p = {"token_id": plan.token_id, "amount_in": plan.amount_in, "reason": reason}
        if tx_hash:
            p["tx_hash"] = tx_hash
        self._activity(plan.owner_id, "buy_failed", p)
        return "failed"

    def loop(self, q: queue.Queue[Any]) -> None:
        """Consume plans one at a time (nonce safety). None stops the loop."""
        while True:
            plan = q.get()
            if plan is None:
                return
            try:
                self.run(plan)
            except Exception:
                log.exception("executor error owner=%s token=%s", plan.owner_id, plan.token_id)
