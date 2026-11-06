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
