FROM ghcr.io/astral-sh/uv:0.12.1 AS uv

FROM python:3.13.14-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    MCP_FILE_ROOT=/data \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8002

COPY --from=uv /uv /uvx /bin/

RUN groupadd --system --gid 10002 mcp \
    && useradd --system --uid 10002 --gid mcp --home-dir /app mcp \
    && install -d -o mcp -g mcp /app /data

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev \
    && chown -R mcp:mcp /app /data

USER 10002:10002
EXPOSE 8002

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD ["python", "-c", "import os,socket; s=socket.create_connection(('127.0.0.1', int(os.getenv('MCP_PORT', '8002'))), 2); s.close()"]

CMD ["filesystem-mcp"]

