# 0006: Worker: executor, bot keys, internal API

Dir: `worker/copytrade_worker/` (files `executor.py`, `keys.py`, `api.py`, `route.py`). Interfaces: `vault.md`, `db.sql`, `worker-api.md`. Consumes `Plan` from 0005 (`types.py`; if 0005 hasn't landed, create `types.py` exactly as 0005 describes and keep it minimal).

## Do
- `keys.py`: create ed25519 bot key per owner, store in `$DATA_DIR/keys/<owner>.json` 0600, never log. Sign transactions (Borsh) for FunctionCall to the vault: either pure Python (`cryptography` ed25519 + hand Borsh) or port Beak `tp/snipe/signer.mjs` approach; prefer pure Python if small. Nonce = last used + 1.
- `route.py`: pick route for a buy: token's main pool on DCL or Ref v1, quote wNEAR or via RHEA (`token.rhealab.near`) multi-hop. Uses pool data from RPC views (DCL `get_pool`, Ref `get_pool`). Compute `min_out` = min over price guard (if set) and slippage (if not unlimited) vs pool price; with unlimited slippage and no guard, `min_out` = 1.
- `executor.py`: Plan → ensure `register_token` (log `storage_paid` activity) → `swap` → wait for outcome → update `copy_buys`, write `activity` (`buy_filled`/`buy_failed`). Retry rule: resend while price within limits, then mark failed. Track gas allowance from `view_access_key`; write `gas_80` once at 80% used.
- `api.py`: stdlib `http.server` on 127.0.0.1 per `worker-api.md`.
- `DRY_RUN=1` default: build and sign, don't send; log the would-be tx (no key material).
- Tests: Borsh tx bytes against a known vector (generate with near-api-js once, commit the vector), min_out math, route selection on fixture pools, executor state machine with a fake RPC, API routes.

## Out
No mainnet transactions. No real keys in fixtures (generate test keys in tests).
