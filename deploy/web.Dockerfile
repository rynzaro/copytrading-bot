# Web: Next standalone build. Build context: repo root.
FROM node:24-bookworm-slim AS build
RUN apt-get update && apt-get install -y --no-install-recommends python3 make g++ && rm -rf /var/lib/apt/lists/*
# Same pnpm major as the lockfile (web/pnpm-lock.yaml, made with 10.x).
ARG PNPM_VERSION=10.29.2
RUN corepack enable && corepack prepare pnpm@${PNPM_VERSION} --activate
WORKDIR /src/web
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
# Inlined into the browser bundle at build time: the pinned vault code hash.
ARG NEXT_PUBLIC_VAULT_CODE_HASH=""
ENV NEXT_PUBLIC_VAULT_CODE_HASH=$NEXT_PUBLIC_VAULT_CODE_HASH NEXT_TELEMETRY_DISABLED=1
RUN pnpm build

FROM node:24-bookworm-slim
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
WORKDIR /app
COPY --from=build /src/web/.next/standalone ./
COPY --from=build /src/web/.next/static ./.next/static
COPY docs/interfaces/db.sql ./db.sql
ENV DB_SCHEMA=/app/db.sql
# Same group as the worker (10001), other uid: shares the SQLite files, cannot read data/keys.
RUN groupadd -g 10001 app && useradd -u 10002 -g app -M -s /usr/sbin/nologin web
USER web:app

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:3000/').then(r=>process.exit(r.status<500?0:1),()=>process.exit(1))"

CMD ["sh", "-c", "umask 007 && exec node server.js"]
