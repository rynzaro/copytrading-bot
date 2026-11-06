# copytrading-bot

Web app that copies meme-token buys of chosen whale wallets on NEAR mainnet. You log in with your own NEAR wallet, set up a trading wallet (a vault contract you control), pick copy targets, and the bot buys for you when they buy.

- The vault can only swap through allowed DEXes. Only your owner wallet can withdraw, pause or sell.
- The server holds one function-call key per user (the bot key). It can only ask the vault to swap.
- No automatic exits in v1: you sell by hand.

Terms: [CONTEXT.md](CONTEXT.md). Decisions: [docs/DECISIONS.md](docs/DECISIONS.md), [docs/adr/](docs/adr/).

## Disclaimer

Real funds on NEAR mainnet. Meme tokens can go to zero within minutes, and copied buys often fill worse than the whale's. The bot key can spend your vault's wNEAR on any meme token. The software comes as is, without warranty (MIT, see [LICENSE](LICENSE)). Use only money you can lose.

## Self-host

Needs a Linux host with Docker (Compose v2), a domain pointing at it (A/AAAA record), and ports 80 and 443 open.

```sh
git clone https://github.com/rynzaro/copytrading-bot.git
cd copytrading-bot
sudo install -d -o 10001 -g 10001 -m 2770 data   # SQLite and bot keys
cd deploy
cp .env.example .env
$EDITOR .env        # at least APP_HOST, SESSION_SECRET, VAULT_CODE_HASH
docker compose up -d
```

What runs:

| Service | What |
|---|---|
| `caddy` | TLS (Let's Encrypt) for `APP_HOST`, proxies to `web` |
| `web` | Next.js app (standalone build) |
| `worker` | `python -m copytrade_worker`: feeds, signals, executor, notifications. Its API is only on an internal network that web can reach |

`DRY_RUN=1` is the default: the worker signs transactions but never sends them. Set `DRY_RUN=0` only once you are ready to trade with real funds.

Check it:

```sh
docker compose ps                     # all up, web and worker (healthy)
docker compose exec worker python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8700/health').read().decode())"
docker compose logs -f worker
```

Update: `git pull && docker compose up -d --build`. After changing `VAULT_CODE_HASH`, rebuild web too (`docker compose build web`): the browser bundle bakes in the hash.

## Data and backups

Everything lives in `data/` at the repo root (change it with `DATA_DIR`):

- `data/worker.db`: SQLite, shared by web and worker.
- `data/keys/`: one bot private key per user (dir 0700, files 0600, owner uid 10001). The web container runs as another uid and cannot read them.

Back up `data/keys/` (and `worker.db`) to somewhere encrypted. If you lose the keys, nobody's funds are at risk: they stay in the vaults, and owners can still withdraw. Each user must then add a new bot key to their vault before the bot trades for them again. If someone steals the keys, the most they can do is swap vault funds into meme tokens: owners should pause their vaults and remove the old bot key.

SQLite backup without stopping:

```sh
docker compose exec worker python -c "import sqlite3; s=sqlite3.connect('/data/worker.db'); d=sqlite3.connect('/data/backup.db'); s.backup(d)"
```
