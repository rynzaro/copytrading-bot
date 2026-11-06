# 0010: Worker hardening

Blocks go-live (items 1–3); 4–5 strongly wanted.

1. Gas: measure `swap` (DCL, Ref v1, 1–3 hops) and `register_token` in sandbox; set attached gas from measurements + margin; document gas per swap vs 1 NEAR allowance.
2. SQLite WAL: `db.connect` must apply `PRAGMA journal_mode=WAL` (and `busy_timeout`) on every connection; test it.
3. Stuck `sent` rows: reconciler looks up tx status by hash and settles `filled`/`failed`; never resends. Test with fake RPC.
4. One RPC client: move executor and API onto 0005's `Chain` (failover); delete `nearrpc.py`.
5. Startup checks: refuse to start with DRY_RUN=0 unless `VAULT_CODE_HASH`, RPC URLs and data dir are set.

## Status (2026-11-06)
Items 2–5 done. Item 1 waits for 0004.

## Follow-ups
- `sent` row with tx never found: after the tx validity window (~24 h), settle by the vault's balance of that token (> 0 → `filled`, else `failed` with `tx_lost`), using an archival RPC if available.
- Cap reconcile work per pass (e.g. 20 rows) so buys aren't delayed.
