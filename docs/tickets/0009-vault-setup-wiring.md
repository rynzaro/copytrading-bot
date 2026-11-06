# 0009: Wire vault setup end to end

Blocks go-live. After 0004.

## Do
- Replace the stub `web/src/lib/vault-setup.ts` with the account-creation flow chosen in 0004 (`contracts/vault/README.md`). Exact actions, costs from 0004 measurements, shown before signing.
- Owner actions from the web call the real vault methods (`vault.md`): deposit, sell, withdraw NEAR/token, pause/unpause, `set_bot_key` (refill allowance), `remove_bot_key`.
- `/vault-confirmed` (worker): also check the account's code hash equals the pinned `VAULT_CODE_HASH`; reject otherwise. Same env feeds `NEXT_PUBLIC_VAULT_CODE_HASH`.
- Sandbox e2e: a script that runs the owner setup actions against near-workspaces, confirms via worker API, runs one bot `swap` through the mock DEX.

- One DB default in web and worker per `worker-api.md` "Shared DB file"; update Compose.
- Bot txs: attached gas ≤ 200 Tgas (300 Tgas fails against the 1 NEAR allowance, measured in 0004). Also do 0010 item 1 (gas sizing) with the 0004 sandbox.
- `contracts/vault/README.md`: note that sandbox tests need `scripts/build.sh` first.

## Done when
Sandbox e2e passes; web tests cover each owner action's tx shape.
