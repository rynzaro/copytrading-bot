-- Interface: SQLite schema shared by web (0007) and worker (0005, 0006, 0008).
-- Change only by editing this file first. Migrations: worker/copytrade_worker/db.py applies this file (CREATE IF NOT EXISTS).
-- Times: unix ms integers. Amounts: decimal strings (yocto). JSON columns: TEXT.

PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS users (
  owner_id        TEXT PRIMARY KEY,           -- NEAR account of owner wallet
  vault_id        TEXT UNIQUE,                -- null until setup tx confirmed
  bot_public_key  TEXT,                       -- ed25519:..., written by worker
  bot_on          INTEGER NOT NULL DEFAULT 0, -- user toggle
  settings        TEXT NOT NULL DEFAULT '{}', -- JSON, see below
  created_at      INTEGER NOT NULL
);
-- settings JSON:
-- { "ladder": [{"max_mcap_usd": 1000000, "size_near": "10"}, {"max_mcap_usd": 3000000, "size_near": "5"}],
--   "min_mcap_usd": 0,
--   "price_guard_x": null | number,      -- optional, multiple of target fill
--   "slippage_bps": null | number | "unlimited",  -- required before bot runs
--   "notify": {"ntfy_topic": null|string, "telegram_chat_id": null|string, "still_active_weekly": true} }

CREATE TABLE IF NOT EXISTS copy_targets (
  owner_id   TEXT NOT NULL REFERENCES users(owner_id),
  target_id  TEXT NOT NULL,
  enabled    INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (owner_id, target_id)
);

CREATE TABLE IF NOT EXISTS sessions (
  token_hash  TEXT PRIMARY KEY,               -- sha256 of session cookie
  owner_id    TEXT NOT NULL,
  expires_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS login_nonces (    -- used NEP-413 login nonces, web only: single use across restarts
  nonce       TEXT PRIMARY KEY,               -- hex of the 32-byte nonce
  expires_at  INTEGER NOT NULL                -- issue time + TTL; expired rows are deleted
);

CREATE TABLE IF NOT EXISTS signals (
  id          INTEGER PRIMARY KEY,
  target_id   TEXT NOT NULL,
  token_id    TEXT NOT NULL,
  kind        TEXT NOT NULL,                  -- 'swap' (only swaps become copy buys)
  venue       TEXT,                           -- 'dcl' | 'ref_v1' | other
  pool_id     TEXT,
  near_in     TEXT,                           -- quote spent by target, yocto wNEAR equiv
  fill_price  REAL,                           -- NEAR per whole token
  tx_hash     TEXT NOT NULL,
  seen_at     INTEGER NOT NULL,
  source      TEXT NOT NULL,                  -- 'teardata' | 'intear_ws'
  UNIQUE (tx_hash, token_id)
);

CREATE TABLE IF NOT EXISTS copy_buys (
  id          INTEGER PRIMARY KEY,
  owner_id    TEXT NOT NULL,
  token_id    TEXT NOT NULL,
  signal_id   INTEGER NOT NULL REFERENCES signals(id),
  status      TEXT NOT NULL,                  -- 'planned' | 'sent' | 'filled' | 'failed' | 'skipped'
  reason      TEXT,                           -- skip/fail reason code
  amount_in   TEXT,
  min_out     TEXT,
  amount_out  TEXT,
  tx_hash     TEXT,
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL,
  UNIQUE (owner_id, token_id)                 -- one copy buy per token per user, ever
);
-- Skips are logged in activity, not copy_buys, so a skip does not block a later signal.
-- 'filled' blocks the token for that user forever; 'planned'/'sent' block while in flight.
-- A 'failed' row (not filled, e.g. price guard) is reused by a later signal: the worker updates it back to 'planned'.

CREATE TABLE IF NOT EXISTS launch_tax (
  token_id     TEXT PRIMARY KEY,
  code_hash    TEXT,
  sniping_tax  INTEGER NOT NULL,              -- 1 = found
  checked_at   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS activity (
  id           INTEGER PRIMARY KEY,
  owner_id     TEXT,                          -- null = system
  kind         TEXT NOT NULL,                 -- 'buy_filled'|'buy_failed'|'buy_skipped'|'storage_paid'|'gas_80'|'still_active'|'sell'|'withdraw'|'limit_order'|'system'
  payload      TEXT NOT NULL DEFAULT '{}',    -- JSON
  created_at   INTEGER NOT NULL,
  notified_at  INTEGER                        -- set by notifier (0008)
);
-- activity.payload keys per kind (all optional unless noted; amounts yocto strings):
--   buy_filled:   token_id*, amount_in*, amount_out, tx_hash*
--   buy_failed:   token_id*, amount_in, reason*, tx_hash
--   buy_skipped:  token_id*, reason*  (reason codes from worker rules)
--   storage_paid: token_id*, amount*
--   gas_80:       vault_id*
--   still_active: vault_id*
--   sell:         token_id*, amount_in*, amount_out, tx_hash
--   withdraw:     token_id*, amount*, tx_hash
--   limit_order:  target_id*, token_id*, pool_id*, amount_near*, price (NEAR per whole token), tx_hash*
--   system:       text*
CREATE INDEX IF NOT EXISTS activity_unnotified ON activity(notified_at) WHERE notified_at IS NULL;

CREATE TABLE IF NOT EXISTS telegram_links (
  code        TEXT PRIMARY KEY,               -- one-time code shown in web
  owner_id    TEXT NOT NULL,
  expires_at  INTEGER NOT NULL
);
