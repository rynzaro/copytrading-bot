# 0018: Limit orders: alert, don't buy

Decision 2026-11-06: only executed swaps become signals. DCL limit buy orders (`order_added`) by a copy target produce a `limit_order` activity row for each user who has that target enabled, when the order is placed. No copy buy.

- Worker: remove `limit_fill` signals and planning; detect placed limit buy orders; write activity per `db.sql` payload; dedupe per (order tx, user).
- Notify: message text for `limit_order`.
- Web: Activity view renders `limit_order`.
- Tests on each side.
