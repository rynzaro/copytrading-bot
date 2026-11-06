#!/bin/sh
# Sandbox e2e (ticket 0009): web owner txs + worker API + one bot buy. Needs rustup cargo, cargo-near, uv.
# Refresh the web fixture first if owner txs changed: (cd web && pnpm vitest run -u src/lib/owner-txs.test.ts)
set -eu
cd "$(dirname "$0")/.."
export PATH="$HOME/.cargo/bin:$PATH"
sh scripts/build.sh
CTB_E2E=1 cargo test --test e2e -- --nocapture
