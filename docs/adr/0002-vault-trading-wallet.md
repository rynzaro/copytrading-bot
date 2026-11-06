# 0002: Per-user vault contract as the trading wallet

Status: accepted, 2026-11-06

## Decision

Each user's trading wallet is an account running a vault contract, deployed from a global contract pinned by code hash. The server holds a function-call key that can only call `vault.swap`. Only the owner wallet can withdraw, pause or change the vault code. The vault checks on chain: the DEX allowlist (Rhea DCL, Ref v1), the route, and a `min_out`. Bot power has no expiry; the user revokes it by signing pause or removing the key.

## Why

- A bare function-call key can't attach the 1 yocto that every DCL swap needs.
- A function-call key on a Ref v1 internal balance can be drained through a skewed pool that the host creates.
- TEE tooling (Shade Agents) is unmaintained since 2026-04 and unaudited.
- Server-side encryption fails against a malicious host (ADR 0001).

## Consequences

- New, unaudited contract with real funds. No balance or user caps (owner's choice).
- A malicious host can still waste funds by buying its own tokens within the routes the vault allows. Mcap and whale origin can't be checked on chain.
- Setup costs the user under 1 NEAR (storage, DCL registration).
- Pinned by hash: a code change needs each owner's signature.
