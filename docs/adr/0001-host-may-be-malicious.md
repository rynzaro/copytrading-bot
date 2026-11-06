# 0001: Treat the host as possibly malicious for trading power

Status: accepted, 2026-11-06

## Decision

The design must keep user funds safe even if the host changes server code or the JS it serves. The bot's power is limited on chain, not by server promises. No full-access user key ever reaches the server.

## Why

Users must trust that the host (the person running the server) can't take their funds. Server-side encryption or a key in the browser fails once the host serves changed code. Only limits that the chain enforces bind a host that is not honest.

## Exception

A created wallet (seed made in the browser) is only as safe as the page served. The UI warns about this and recommends an external wallet first.

## Consequences

- The bot needs a key type that can't withdraw, and an on-chain place to enforce limits (see custody decision).
- Features that need trust in the server (for example a server-side pause) don't count as protection.
