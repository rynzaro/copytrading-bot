# Decisions (grilling session 2026-11-06)

## Product
- NEAR mainnet, real funds. Disclaimer before first use, dismissible per browser, always in footer.
- Copy meme buys only (non-meme denylist). Copy targets: curated list (root.near, seriousfarmer.near only) plus free entry.
- Signals: swaps and filled DCL limit orders. Feed: Intear teardata optimistic tail, Intear WS backup.
- Quote token wNEAR. Venues: Rhea DCL and Ref v1, multi-hop via RHEA allowed. No Intear (ticket 0002), no bonding curves.
- Size ladder by mcap: < $1M S1 (10 NEAR), $1–3M S2 (5 NEAR), > $3M skip, unknown skip. No other spend limits.
- One copy buy per token per user, ever. No ordering between users.
- Price guard optional (UI encourages it), slippage required (unlimited allowed). No suggested values.
- Transfer tax ignored. Sniping tax: checked when launches appear, cached; skip on hit; no check if not cached or token > 10 min old.
- Heartbeat: ticket 0003, design copied from the Illia sniper.
- No exit in v1 (ticket 0001). Sell button (owner signs `vault.sell`) and Withdraw token.
- Notifications: ntfy and/or Telegram per user, in hidden settings. Events: fill, skip/fail with reason, storage payment, gas allowance 80%, weekly still-active push.
- Login: Wallet Selector v10 (Meteor, MyNearWallet, HOT, Intear wallet), NEP-413 session. Created wallet allowed, with warning.

## Custody (ADR 0001, 0002)
- Per-user vault, global contract pinned by hash. Bot key calls `vault.swap` only. Owner-only withdraw, pause, sell, code change. No expiry, no caps.
- Gas allowance 1 NEAR, refill by owner.
- Setup costs (< 1 NEAR) paid by user, shown before signing.

## Build
- Next.js + React (Catalyst kit, Tailwind v4; look inspired by Uniswap and Beak tokens). Python worker for feed and signing (port from Illia sniper `tp/illia/`, `launches.py`). SQLite. Docker Compose (also the self-host path).
- Vault: Rust near-sdk, near-workspaces sandbox tests, then mainnet test wallets with normal deploy. Global contract publish (~21 NEAR for the 213 KB wasm, burned) only at launch, after approval.
- Separate VPS, not the Beak host. VPS and domain chosen at deploy.
- Repo: public `rynzaro/copytrading-bot`, MIT; Catalyst files under `src/components/catalyst/`, excluded via `LICENSE-CATALYST.md`.
