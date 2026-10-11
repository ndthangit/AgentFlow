FROM node:22.22.1-bookworm-slim

ARG CODEX_VERSION=0.130.0

ENV NODE_ENV=production \
    HOME=/tmp/agentflow-home \
    CODEX_HOME=/tmp/agentflow-codex

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --global --omit=dev "@openai/codex@${CODEX_VERSION}" \
    && npm cache clean --force \
    && groupadd --system --gid 10001 agentflow \
    && useradd --system --uid 10001 --gid agentflow --home-dir /tmp/agentflow-home agentflow \
    && mkdir -p /workspace \
    && chown agentflow:agentflow /workspace

WORKDIR /workspace
COPY --chmod=755 agent.mjs /usr/local/bin/agent

USER 10001:10001
ENTRYPOINT ["agent"]
