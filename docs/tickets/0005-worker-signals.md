# 0005: Worker: feeds, signals, rules, launch tax

Dir: `worker/` (Python 3.12+, package `copytrade_worker`). Interfaces: `docs/interfaces/db.sql`, `docs/interfaces/worker-api.md` (`/health` only here).

## Do
- `db.py`: open SQLite, apply `docs/interfaces/db.sql` (copy into package at build), helpers.
- Port from the Illia sniper (read-only source: `/Users/efte/repos/beak/.claude/worktrees/agent-ab193009e71f30657/tp/illia/` — copy, never edit there): `feeds.py` (teardata optimistic tail, Intear WS backup, health), `detect.py` (swap + DCL `order_added` fills), `rules.py` + `denylist.json` (meme filter), `chain.py` mcap (Intear `/tokens`).
- Signals: only copy targets enabled by any user. Kinds: `swap` and `limit_fill` (DCL limit order when it fills, not when placed). Write `signals` rows, dedupe on `(tx_hash, token_id)`.
- Rules per user → plan or skip with reason code: bot_on, vault set, slippage set, target enabled, meme, mcap known and in range, ladder size, one copy buy per token (see schema), sniping tax.
- Launch tax watcher: port the JustHoot/nearlytrade launch poll and code check from Beak `tp/snipe/launches.py`. Cache in `launch_tax`. At signal time: cache hit with tax → skip; miss or token > 10 min old → no check.
- Output of this ticket: a function `plan(signal, user) -> Plan | Skip` and a queue the executor (0006) consumes. Define `Plan` in `worker/copytrade_worker/types.py` (dataclass: owner_id, vault_id, signal_id, token_id, venue, hops, amount_in, min_out_inputs {price_guard_x, slippage_bps, fill_price, pool_price}).
- Tests (pytest): detect on recorded blocks/receipts fixtures (copy small fixtures from the Illia tests), rules table tests, dedupe, tax cache logic.

## Out
No signing, no transactions. Free RPCs rate-limit: tests must not hit network; live smoke run slow and opt-in.
