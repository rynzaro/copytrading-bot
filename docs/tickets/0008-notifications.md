# 0008: Notifications

Dir: `worker/copytrade_worker/notify/`. Interface: `db.sql` (`activity`, `users.settings.notify`, `telegram_links`).

## Do
- Loop: read `activity` where `notified_at IS NULL`, send to the owner's channels (ntfy topic and/or Telegram chat), set `notified_at`. System rows (owner null) go to the host channel from config.
- ntfy: POST to `$NTFY_URL/<topic>`. Topic is a secret: never log it.
- Telegram: one bot, token from env `TELEGRAM_BOT_TOKEN` (never log). Linking: web creates `telegram_links` code; user adds bot as admin to their channel and posts `/link <code>`; worker polls `getUpdates`, matches code, stores chat id in settings.
- Weekly still-active push per user when `still_active_weekly` and bot key present.
- Message texts short; kinds per `activity.kind`.
- Tests: formatting, routing, linking, no secret in logs (assert on captured logs), retry/backoff on HTTP errors with fake server.

## Out
No real sends in tests.
