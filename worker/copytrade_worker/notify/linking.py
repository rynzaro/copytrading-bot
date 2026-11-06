"""Telegram linking: the user posts `/link <code>` in a chat where the bot is admin."""

import re

_CMD = re.compile(r"^/link(?:@[A-Za-z0-9_]{1,64})?\s+([A-Za-z0-9_-]{1,64})\s*$")


def parse_link_command(text) -> str | None:
    if not isinstance(text, str):
        return None
    m = _CMD.match(text.strip())
    return m.group(1) if m else None


def extract(update) -> tuple[int | None, int | None, str | None]:
    """-> (update_id, chat_id, code). Any part may be None for junk updates."""
    if not isinstance(update, dict):
        return None, None, None
    uid = update.get("update_id")
    uid = uid if isinstance(uid, int) and not isinstance(uid, bool) else None
    msg = update.get("channel_post") or update.get("message")
    if not isinstance(msg, dict):
        return uid, None, None
    chat = msg.get("chat")
    chat_id = chat.get("id") if isinstance(chat, dict) else None
    if not isinstance(chat_id, int) or isinstance(chat_id, bool):
        chat_id = None
    return uid, chat_id, parse_link_command(msg.get("text"))


def claim(conn, code: str, chat_id: int, now_ms: int) -> str | None:
    """Consume a valid code and store chat id in the owner's settings. Returns owner_id or None."""
    with conn:
        row = conn.execute("SELECT owner_id, expires_at FROM telegram_links WHERE code = ?", (code,)).fetchone()
        if row is None:
            return None
        owner_id, expires_at = row[0], row[1]
        if expires_at <= now_ms:
            conn.execute("DELETE FROM telegram_links WHERE code = ?", (code,))
            return None
        conn.execute("DELETE FROM telegram_links WHERE code = ?", (code,))
        # Merge in SQL so a concurrent web write to other settings keys isn't lost.
        cur = conn.execute(
            """UPDATE users SET settings = json_set(
                   CASE WHEN json_valid(settings) THEN settings ELSE '{}' END,
                   '$.notify',
                   json_patch(
                     CASE WHEN json_valid(settings) AND json_type(settings, '$.notify') = 'object'
                          THEN json_extract(settings, '$.notify') ELSE '{}' END,
                     json_object('telegram_chat_id', ?)))
               WHERE owner_id = ?""",
            (str(chat_id), owner_id),
        )
        return owner_id if cur.rowcount else None
