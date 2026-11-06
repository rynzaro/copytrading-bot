# 0017: Vault contract change batch

Before any mainnet deploy. Interface: `docs/interfaces/vault.md` (updated).

- Storage budget: `register_token` total per 24 h window ≤ budget (default 0.1 NEAR); `set_storage_budget` owner-only; state in `get_state`. Sandbox test: bot can't exceed it; owner can change it.
- Distinct `swap_result` errors for "ft_transfer_call failed" vs "venue used 0" (see 0016). Update the worker's `NO_INPUT_LEFT`/`INPUT_MAYBE_LEFT` and its drift test.
- Worker: on a storage budget panic, write `system` activity once per day per user, skip the buy (reason `storage_budget`).
