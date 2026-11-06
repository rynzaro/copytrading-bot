# 0007: Web app

Dir: `web/` (Next.js app router, React, TypeScript, Tailwind v4, pnpm). Interfaces: `db.sql`, `vault.md` (owner txs), `worker-api.md`. Decisions: `docs/DECISIONS.md` (Product, Q34 view layout).

## Do
- Catalyst components: copy needed files from `/Users/efte/repos/catalyst-ui-kit.zip` (`catalyst-ui-kit/typescript/`) into `web/src/components/catalyst/`. Add `LICENSE-CATALYST.md` at repo root: those files are under the Tailwind Plus license, not MIT. Don't copy the demo app wholesale.
- Look: clean, focused. Inspired by Uniswap's swap UI and Beak design tokens (read `/Users/efte/repos/beak/tp/snipe/assets/tokens.css`, `docs/BEAK_UI.md`; read-only).
- Login: NEAR Wallet Selector v10, vendored/pinned (no CDN), modules Meteor, MyNearWallet, HOT, Intear. Session via NEP-413 `signMessage` (nonce from server, recipient = app host). Server verifies signature and that the key is a full-access key of the account (RPC `view_access_key`). Session cookie httpOnly, `sessions` table.
- Created wallet: generate seed in browser, show once with a warning, recommend external wallet first.
- Views: top bar (vault balance, bot on/off, deposit), main (copy targets with toggles from curated list root.near, seriousfarmer.near + free entry; positions with Sell / Withdraw token), hidden Settings (ladder, mcap range, price guard (encouraged, no suggested value), slippage required with "unlimited" option, notifications ntfy/Telegram link, withdraw NEAR, pause, bot key refill), hidden Activity log.
- Setup flow: request bot key from worker, build owner-signed txs per 0004 account-creation doc (if 0004 not landed, stub behind one module `src/lib/vault-setup.ts`), show cost before signing, confirm via worker.
- Disclaimer per DECISIONS (dismiss per browser, footer always).
- DB access: `better-sqlite3` in server code only. Users see only their own rows.
- Tests: vitest for NEP-413 verification, session, settings validation, authz (user A can't read B). Typecheck clean, `next build` passes.

## Out
No deploy. No real wallet signing in tests.
