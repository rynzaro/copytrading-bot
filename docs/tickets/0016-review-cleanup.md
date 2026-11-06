# 0016: Cleanup from code review (2026-11-06)

Not blocking. Judgement calls from the Standards review.

- Glossary: UI says "vault" (`settings.tsx:219`, `setup.tsx:63`); use "trading wallet" everywhere users read.
- Denylist `_note` differs between `contracts/vault/denylist.json` and the worker copy, mentions a missing `config.json` and says "whale". Align notes; add a CI diff check.
- One worker module for contract ids (`wrap.near`, `token.rhealab.near`, DCL, Ref v1); today in route.py, detect.py, rules.py, chain.py.
- Ladder defaults and validation exist in web (`settings.ts`) and worker (`rules.py`): one source (JSON in `docs/interfaces/`) or a shared test vector.
- Skip reason codes: one shared list (`docs/interfaces/`), used by worker and web.
- Web RPC client (`web/src/server/rpc.ts`) has no failover: add RPC failover, or record in DECISIONS why web keeps its own client.
- Launchpad-suffix precedence over the denylist (`rules.py:47`) isn't known to the vault: document the rule in `vault.md`.
- Spec review "not asked for": feeds follow only targets of users with bot on (spec: enabled by any user) — fine, record in DECISIONS; dry-run buys notify via `system` rows — mute in DRY_RUN.

## Vault change batch (next contract version, with Q40 storage budget if approved)
- Distinct `swap_result` errors for "ft_transfer_call failed" vs "venue used 0", so the worker needn't infer refunds from wrap.near receipts.
- Vault state versioning: moved to 0019.
- Worker accepts a list of allowed vault code hashes (`VAULT_CODE_HASHES`), so vaults stay confirmed across upgrades. Web: Upgrade button that uses `upgradeTx`.
- Bump vault crate version when the state layout changes (0019 kept 2.0.0).
