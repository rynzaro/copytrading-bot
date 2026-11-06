# 0011: Deploy (Docker Compose, VPS)

Blocks go-live. Also the self-host path.

## Do
- `deploy/compose.yaml`: services `web` (Next standalone build), `worker` (`python -m copytrade_worker`), shared volume `data/` (SQLite, bot keys 0700). Worker API on an internal network only. Reverse proxy with TLS (Caddy) for web.
- `.env.example` with every variable (names only, no values): `APP_HOST`, `SESSION_SECRET`, `NEAR_RPC_URL(S)`, `VAULT_CODE_HASH`, `NEXT_PUBLIC_VAULT_CODE_HASH`, `DRY_RUN`, `NTFY_URL`, `HOST_NTFY_TOPIC`, `TELEGRAM_BOT_TOKEN`, `HOST_TELEGRAM_CHAT_ID`, `WORKER_PORT`, `DATA_DIR`.
- `README.md`: what it is, disclaimer, self-host steps (clone, fill `.env`, `docker compose up -d`), backups of `data/keys` (lose them = re-add bot key; funds stay safe in vault).
- Local `docker compose up` smoke: web serves, worker `/health` green, DRY_RUN.

## Needs from owner
VPS (not the Beak host) with SSH, domain/DNS.
