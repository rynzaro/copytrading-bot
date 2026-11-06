# 0018: Limit orders: alert, don't buy

Decision 2026-11-06: only executed swaps become signals. DCL limit buy orders (`order_added`) by a copy target produce a `limit_order` activity row for each user who has that target enabled, when the order is placed and again when it fills (`stage`). No copy buy. Per-user setting `notify.limit_orders` (default true) switches both off.

- Worker: remove `limit_fill` signals and planning; detect placed limit buy orders; write activity per `db.sql` payload; dedupe per (order tx, user).
- Notify: message text for `limit_order`.
- Web: Activity view renders `limit_order` (both stages); Settings toggle "Limit-order alerts".
- Tests on each side.
