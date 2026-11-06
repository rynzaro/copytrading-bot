"""Notifier loop: deliver pending activity, poll Telegram links, create weekly still-active rows."""

import json
import logging
import time

from .channels import Ntfy, Telegram
from .config import Config
from .httppost import PermanentError, SendError
from .linking import claim, extract
from .text import format_activity

log = logging.getLogger("copytrade_worker.notify")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _notify_settings(raw) -> dict:
    try:
        s = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    n = s.get("notify") if isinstance(s, dict) else None
    return n if isinstance(n, dict) else {}


def _str_or_none(v) -> str | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (str, int)):
        s = str(v).strip()
        return s or None
    return None


class Notifier:
    def __init__(self, conn, config: Config, *, sleep=time.sleep, now=_now_ms):
        self.conn = conn
        self.cfg = config
        self.sleep = sleep
        self.now = now
        self.ntfy = Ntfy(config.ntfy_url, sleep)
        self.tg = Telegram(config.telegram_token, config.telegram_api, sleep) if config.telegram_token else None
        self.tg_offset: int | None = None
        # Optional on-chain check (vault_id, public_key) -> bool. Default: bot key present in DB.
        self.key_check = None

    # ---- routing ----
    def _channels(self, owner_id):
        """-> list of (name, send_fn). Only names are safe to log."""
        if owner_id is None:
            topic, chat = self.cfg.host_ntfy_topic, self.cfg.host_telegram_chat_id
        else:
            row = self.conn.execute("SELECT settings FROM users WHERE owner_id = ?", (owner_id,)).fetchone()
            if row is None:
                return []
            n = _notify_settings(row[0])
            topic, chat = _str_or_none(n.get("ntfy_topic")), _str_or_none(n.get("telegram_chat_id"))
        out = []
        if topic:
            out.append(("ntfy", lambda text, t=topic: self.ntfy.send(t, text)))
        if chat and self.tg is not None:
            out.append(("telegram", lambda text, c=chat: self.tg.send(c, text)))
        return out

    def _mark(self, aid: int) -> None:
        with self.conn:
            self.conn.execute("UPDATE activity SET notified_at = ? WHERE id = ? AND notified_at IS NULL",
                              (self.now(), aid))

    def deliver_pending(self) -> int:
        rows = self.conn.execute(
            "SELECT id, owner_id, kind, payload, created_at FROM activity WHERE notified_at IS NULL "
            "ORDER BY id LIMIT ?", (self.cfg.batch,)).fetchall()
        done = 0
        for aid, owner_id, kind, payload, created_at in rows:
            try:
                p = json.loads(payload) if payload else {}
            except (TypeError, ValueError):
                p = {}
            text = format_activity(kind, p)
            chans = self._channels(owner_id)
            ok = permanent = 0
            for name, send in chans:
                try:
                    send(text)
                    ok += 1
                except PermanentError as e:
                    permanent += 1
                    log.warning("notify %s rejected activity=%s status=%s", name, aid, e.status)
                except SendError as e:
                    log.warning("notify %s failed activity=%s: %s", name, aid, e)
            expired = self.now() - (created_at or 0) > self.cfg.give_up_ms
            if ok or not chans or permanent == len(chans) or expired:
                if chans and not ok:
                    log.warning("notify giving up activity=%s", aid)
                self._mark(aid)
                done += 1
        return done

    # ---- telegram linking ----
    def poll_telegram(self) -> None:
        if self.tg is None:
            return
        try:
            updates = self.tg.get_updates(self.tg_offset)
        except SendError as e:
            log.warning("notify telegram getUpdates failed: %s", e)
            return
        for u in updates:
            uid, chat_id, code = extract(u)
            if uid is not None:
                self.tg_offset = max(self.tg_offset or 0, uid + 1)
            if code is None or chat_id is None:
                continue
            owner = claim(self.conn, code, chat_id, self.now())
            if owner is None:
                log.info("notify telegram link code rejected")
                continue
            log.info("notify telegram linked owner=%s", owner)
            try:
                self.tg.send(chat_id, "Linked to copytrade notifications.")
            except SendError as e:
                log.warning("notify telegram link reply failed: %s", e)

    # ---- weekly still-active ----
    def weekly_still_active(self) -> int:
        now = self.now()
        users = self.conn.execute(
            "SELECT owner_id, vault_id, bot_public_key, settings FROM users "
            "WHERE vault_id IS NOT NULL AND bot_public_key IS NOT NULL").fetchall()
        made = 0
        for owner_id, vault_id, pk, settings in users:
            n = _notify_settings(settings)
            if n.get("still_active_weekly", True) is not True:
                continue
            if not self._channels(owner_id):
                continue
            last = self.conn.execute(
                "SELECT max(created_at) FROM activity WHERE owner_id = ? AND kind = 'still_active'",
                (owner_id,)).fetchone()[0]
            if last is not None and now - last < self.cfg.weekly_ms:
                continue
            if self.key_check is not None:
                try:
                    if not self.key_check(vault_id, pk):
                        continue
                except Exception as e:  # noqa: BLE001  chain check failure: try next round
                    log.warning("notify key check failed owner=%s: %s", owner_id, type(e).__name__)
                    continue
            with self.conn:
                self.conn.execute(
                    "INSERT INTO activity(owner_id, kind, payload, created_at) VALUES (?, 'still_active', ?, ?)",
                    (owner_id, json.dumps({"vault_id": vault_id}), now))
            made += 1
        return made

    def run_once(self) -> None:
        self.poll_telegram()
        self.weekly_still_active()
        self.deliver_pending()

    def run_forever(self) -> None:
        log.info("notify loop start %s", self.cfg)
        while True:
            try:
                self.run_once()
            except Exception as e:  # keep the loop alive; no secrets in type name
                log.exception("notify loop error: %s", type(e).__name__)
            self.sleep(self.cfg.interval_s)
