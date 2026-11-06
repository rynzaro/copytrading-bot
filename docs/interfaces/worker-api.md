# Interface: worker internal HTTP

Bound to 127.0.0.1 only (Compose: internal network). Web (0007) calls it. No auth beyond network isolation in v1.

| Route | Body | Reply |
|---|---|---|
| `POST /bot-key` | `{"owner_id"}` | `{"public_key": "ed25519:..."}`. Creates the key once per owner, stores private key in `$DATA_DIR/keys/` (0600), writes `users.bot_public_key`. Idempotent. |
| `POST /vault-confirmed` | `{"owner_id","vault_id"}` | `{"ok": true}` after worker checks on chain: `get_state().owner_id == owner_id` and bot key present. Writes `users.vault_id`. |
| `GET /health` | – | `{"feeds": {...}, "rpc": "...", "last_signal_at": ms}` |

Private bot keys never leave the worker: not in DB, logs, or replies.
