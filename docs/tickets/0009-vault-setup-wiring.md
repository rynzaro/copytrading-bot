# 0009: Wire vault setup end to end

Blocks go-live. After 0004.

## Do
- Replace the stub `web/src/lib/vault-setup.ts` with the account-creation flow chosen in 0004 (`contracts/vault/README.md`). Exact actions, costs from 0004 measurements, shown before signing.
- Owner actions from the web call the real vault methods (`vault.md`): deposit, sell, withdraw NEAR/token, pause/unpause, `set_bot_key` (refill allowance), `remove_bot_key`.
- `/vault-confirmed` (worker): also check the account's code hash equals the pinned `VAULT_CODE_HASH`; reject otherwise. Same env feeds `NEXT_PUBLIC_VAULT_CODE_HASH`.
- Sandbox e2e: a script that runs the owner setup actions against near-workspaces, confirms via worker API, runs one bot `swap` through the mock DEX.

## Done when
Sandbox e2e passes; web tests cover each owner action's tx shape.
