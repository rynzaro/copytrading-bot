# Worker: python -m copytrade_worker. Build context: repo root.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=0 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv
# Locked deps first (cache layer), then the package itself.
COPY worker/pyproject.toml worker/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY worker/copytrade_worker ./copytrade_worker
RUN uv sync --frozen --no-dev --no-editable && rm -rf /root/.cache
ENV PATH=/app/.venv/bin:$PATH

# uid 10001 owns data/ and data/keys (0700). The web container runs as 10002 in the same group:
# it can use the SQLite files (umask 007) but not the keys.
RUN groupadd -g 10001 app && useradd -u 10001 -g app -M -s /usr/sbin/nologin worker \
    && mkdir -p /data && chown worker:app /data && chmod 2770 /data
USER worker:app
ENV DATA_DIR=/data

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % (os.environ.get('WORKER_PORT') or 8700), timeout=4)"

COPY --chmod=0755 deploy/worker-entrypoint.sh /usr/local/bin/worker-entrypoint
CMD ["worker-entrypoint"]
