"""Executor: Plan -> register_token (if needed) -> vault.swap -> outcome -> copy_buys + activity.

Row owner: the signals side (`signals.Pipeline.decide` via `db.claim_copy_buy`) inserts the 'planned'
copy_buys row before it queues the Plan. The executor never inserts; it only acts on a row that is
'planned' for the plan's signal_id, and moves it to 'sent' / 'filled' / 'failed'.

DRY_RUN=1 (default): build and sign, don't send; log the would-be tx (no key material).
Retry: resend while the price is within limits and the outcome proves no input left the vault (classify,
NO_INPUT_LEFT); then mark failed. Any other outcome (timeout, "output balance unknown", a later receipt
failed) is unknown and never resent: the row stays 'sent' so the token stays blocked.
Reconciler (`reconcile`, run by `loop` at start and every `reconcile_s`): looks up each 'sent' row's tx by hash
and settles it 'filled' / 'failed' from the outcome. It never signs or sends. Same thread as the plans, so it
never races an in-flight wait.
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


def _tgas(name: str, default: int, lo: int, hi: int = 200) -> int:
    """Env Tgas clamped to [lo, hi]. hi 200: 300 Tgas needs more than the 1 NEAR allowance (NotEnoughAllowance)."""
    try:
        v = int(os.environ.get(name, default))
    except ValueError:
        v = default
    return min(max(v, lo), hi) * TGAS


# Sandbox (contracts/vault tests/sandbox.rs `gas_sizing`): every buy shape (DCL/Ref v1, 1-2 hops; 3 can't pass
# the route rules) needs >= 180 Tgas prepaid, burns ~27 Tgas; register_token >= 40, burns ~11.
SWAP_GAS = _tgas("SWAP_GAS_TGAS", 200, 180)
REGISTER_GAS = _tgas("REGISTER_GAS_TGAS", 50, 40)
GAS_ALLOWANCE = int(os.environ.get("GAS_ALLOWANCE_YOCTO", str(10 ** 24)))   # vault.md default: 1 NEAR
EVENT_STANDARD = "copytrade_vault"


def dry_run_default() -> bool:
    return os.environ.get("DRY_RUN", "1") != "0"


class Fail(Exception):
    def __init__(self, reason: str, retry: bool = False):
        self.reason, self.retry = reason, retry
        super().__init__(reason)


def events(outcome: dict, emitter: str) -> list[dict]:
    """NEP-297 events of our vault standard from receipts executed by `emitter` (the vault). Any contract can
    log our standard (a token's ft_balance_of could forge a swap_result), so other receipts don't count.
    Untrusted: parsed defensively."""
    out = []
    for r in outcome.get("receipts_outcome") or []:
        o = r.get("outcome") if isinstance(r, dict) else None
        if not isinstance(o, dict) or o.get("executor_id") != emitter:
            continue
        for line in o.get("logs") or []:
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


FINAL = {"SuccessValue", "SuccessReceiptId", "Failure"}


def is_final(res: dict | None) -> bool:
    st = res.get("status") if isinstance(res, dict) else None
    return isinstance(st, dict) and bool(st.keys() & FINAL)


# Vault `swap_result` errors that prove no input left the vault (contracts/vault/src/lib.rs on_swap_checked:
# before any transfer; on_swap_sent: the venue used 0 and the input came back). Error -> reason code.
# `{token}` = the bought token or wrap.near. Any other error, or none, does not prove it.
NO_INPUT_LEFT = {
    "paused": "paused",
    "vault not registered on {token}": "not_registered",
    "ft_balance_of failed on {token}": "balance_check_failed",
    "amount_in above balance": "balance_low",
    "swap failed, input refunded": "swap_refunded",
}
# Vault errors emitted after the venue consumed the input: outcome unknown, never resent.
INPUT_MAYBE_LEFT = {"output balance unknown"}
# Failed reasons worth a resend (price may have moved back). The rest are deterministic.
RETRY_REASONS = {"swap_refunded"}


def _receipt_failures(res: dict) -> list[tuple[str | None, str | None, str]]:
    """(receipt id, executor, failure json) for each failed receipt."""
    out = []
    for r in res.get("receipts_outcome") or []:
        o = r.get("outcome") if isinstance(r, dict) else None
        if not isinstance(o, dict):
            continue
        rs = o.get("status")
        if isinstance(rs, dict) and "Failure" in rs:
            out.append((r.get("id"), o.get("executor_id"), json.dumps(rs["Failure"])[:300]))
    return out


def _first_receipt(res: dict) -> str | None:
    """Id of the receipt the tx converted into (the vault call itself)."""
    o = (res.get("transaction_outcome") or {}).get("outcome") or {}
    ids = o.get("receipt_ids") if isinstance(o, dict) else None
    return ids[0] if isinstance(ids, list) and ids and isinstance(ids[0], str) else None


def _no_input_reason(error: Any, token_id: str) -> str | None:
    if not isinstance(error, str):
        return None
    for pattern, reason in NO_INPUT_LEFT.items():
        if "{token}" in pattern:
            if error in {pattern.format(token=t) for t in (token_id, R.WNEAR)}:
                return reason
        elif error == pattern:
            return reason
    return None


def classify(res: dict, vault_id: str, token_id: str) -> tuple[str, str | None]:
    """Swap outcome -> ('filled', amount_out) | ('failed', reason) | ('unknown', None). Untrusted data.

    'failed' only when the outcome proves no input left the vault (the row may then be resent or reused);
    everything else that isn't a clean fill is 'unknown' (row stays 'sent', never resent)."""
    ev = [e for e in events(res, vault_id) if e.get("event") == "swap_result"]
    fails = _receipt_failures(res)
    last = ev[-1] if ev else None
    if last is not None and "amount_out" in last and not last.get("error") and not fails:
        try:
            out = int(last["amount_out"])
        except (TypeError, ValueError):
            return "unknown", None
        return ("filled", str(out)) if out >= 0 else ("unknown", None)
    if last is not None and last.get("error"):
        reason = _no_input_reason(last["error"], token_id)
        if reason is None:
            return "unknown", None
        # "input refunded" is also what the vault says when ft_transfer_call's result was unreadable. If a
        # wrap.near receipt failed (e.g. ft_resolve_transfer), the refund isn't proven.
        if reason == "swap_refunded" and any(ex == R.WNEAR for _, ex, _ in fails):
            return "unknown", None
        return "failed", reason
    # No verdict from the vault. Proven only if the vault call itself panicked: its promises never ran.
    first = _first_receipt(res)
    for rid, ex, text in fails:
        if first is not None and rid == first and ex == vault_id:
            return "failed", "paused" if "paused" in text.lower() else "swap_rejected"
    return "unknown", None  # success without our event, or a later receipt failed: can't tell


class Executor:
    def __init__(self, db: sqlite3.Connection, rpc: Any, keystore: K.KeyStore, *, dry_run: bool | None = None,
                 max_attempts: int = 3, outcome_timeout_s: float = 60, poll_s: float = 1.0,
                 gas_allowance: int = GAS_ALLOWANCE, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, reconcile_s: float = 60):
        self.db, self.rpc, self.ks = db, rpc, keystore
        self.dry_run = dry_run_default() if dry_run is None else dry_run
        self.max_attempts, self.timeout, self.poll = max_attempts, outcome_timeout_s, poll_s
        self.gas_allowance, self.sleep, self.clock = gas_allowance, sleep, clock
        self.reconcile_s = reconcile_s
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
            if res and is_final(res):
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
        dep = next((e.get("deposit") for e in events(res, plan.vault_id) if e.get("event") == "token_registered"), None)
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
        kind, val = classify(res, plan.vault_id, plan.token_id)
        if kind == "filled":
            self._update(plan, status="filled", amount_out=val, reason=None)
            self._activity(plan.owner_id, "buy_filled", {"token_id": plan.token_id, "amount_in": str(amount_in),
                                                         "amount_out": val, "tx_hash": tx.hash})
            return "filled"
        if kind == "failed":
            raise Fail(val or "swap_failed", retry=val in RETRY_REASONS)
        # Input may have left the vault. Never resend: the row stays 'sent', the token stays blocked.
        log.warning("swap outcome without proof tx=%s owner=%s token=%s", tx.hash, plan.owner_id, plan.token_id)
        self._activity(plan.owner_id, "system", {"text": f"buy {plan.token_id}: outcome unknown, tx {tx.hash}"})
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

    # ---- reconciler ----
    def reconcile(self) -> int:
        """Settle 'sent' rows from their tx outcome (by hash, sender = vault). Never sends. Returns rows settled."""
        rows = self.db.execute(
            "SELECT b.owner_id, b.token_id, b.amount_in, b.tx_hash, u.vault_id FROM copy_buys b"
            " JOIN users u ON u.owner_id = b.owner_id"
            " WHERE b.status = 'sent' AND b.tx_hash IS NOT NULL AND u.vault_id IS NOT NULL ORDER BY b.updated_at"
        ).fetchall()
        n = 0
        for r in rows:
            try:
                res = self.rpc.tx_status(r["tx_hash"], r["vault_id"])
            except (RpcError, RpcDown) as e:
                log.warning("reconcile tx_status %s: %s", r["tx_hash"], e)
                continue
            if not res or not is_final(res):
                continue
            kind, val = classify(res, r["vault_id"], r["token_id"])
            if kind == "unknown":
                log.info("reconcile: outcome without verdict tx=%s owner=%s token=%s", r["tx_hash"], r["owner_id"],
                         r["token_id"])
                continue
            cols = ("filled", val, None) if kind == "filled" else ("failed", None, val)
            cur = self.db.execute(
                "UPDATE copy_buys SET status = ?, amount_out = coalesce(?, amount_out), reason = ?, updated_at = ?"
                " WHERE owner_id = ? AND token_id = ? AND status = 'sent' AND tx_hash = ?",
                (*cols, now_ms(), r["owner_id"], r["token_id"], r["tx_hash"]),
            )
            if cur.rowcount != 1:
                continue
            n += 1
            base = {"token_id": r["token_id"], "amount_in": r["amount_in"]}
            if kind == "filled":
                self._activity(r["owner_id"], "buy_filled", {**base, "amount_out": val, "tx_hash": r["tx_hash"]})
            else:
                self._activity(r["owner_id"], "buy_failed", {**base, "reason": val, "tx_hash": r["tx_hash"]})
            log.info("reconciled tx=%s owner=%s token=%s -> %s", r["tx_hash"], r["owner_id"], r["token_id"], kind)
        return n

    def loop(self, q: queue.Queue[Any]) -> None:
        """Consume plans one at a time (nonce safety). None stops the loop. Reconciles at start and every
        reconcile_s between plans."""
        next_rec = time.monotonic()
        while True:
            if time.monotonic() >= next_rec:
                try:
                    self.reconcile()
                except Exception:
                    log.exception("reconcile error")
                next_rec = time.monotonic() + self.reconcile_s
            try:
                plan = q.get(timeout=max(0.0, next_rec - time.monotonic()))
            except queue.Empty:
                continue
            if plan is None:
                return
            try:
                self.run(plan)
            except Exception:
                log.exception("executor error owner=%s token=%s", plan.owner_id, plan.token_id)
