FROM ghcr.io/astral-sh/uv:0.12.1 AS uv

FROM python:3.13.14-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

COPY --from=uv /uv /uvx /bin/
RUN groupadd --system --gid 10001 agentflow \
    && useradd --system --uid 10001 --gid agentflow --home-dir /app agentflow

WORKDIR /app
COPY pyproject.toml uv.lock README.md alembic.ini ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY migrations ./migrations
COPY docker-entrypoint.sh ./docker-entrypoint.sh
RUN uv sync --frozen --no-dev \
    && chmod +x /app/docker-entrypoint.sh \
    && chown -R agentflow:agentflow /app

USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=2)"]
CMD ["./docker-entrypoint.sh"]
