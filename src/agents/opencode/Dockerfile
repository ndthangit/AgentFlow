FROM node:22.22.1-bookworm-slim

ENV NODE_ENV=production \
    HOME=/tmp/agentflow-home \
    XDG_CONFIG_HOME=/tmp/agentflow-config \
    XDG_DATA_HOME=/tmp/agentflow-data \
    XDG_CACHE_HOME=/tmp/agentflow-cache \
    OPENCODE_DISABLE_PROJECT_CONFIG=1 \
    OPENCODE_DISABLE_DEFAULT_PLUGINS=1 \
    OPENCODE_DISABLE_EXTERNAL_SKILLS=1 \
    OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1 \
    OPENCODE_PURE=1 \
    PATH=/opt/agentflow-opencode/node_modules/.bin:$PATH

WORKDIR /opt/agentflow-opencode
COPY package.json package-lock.json ./
RUN npm ci --omit=dev \
    && npm cache clean --force \
    && groupadd --system --gid 10001 agentflow \
    && useradd --system --uid 10001 --gid agentflow --home-dir /tmp/agentflow-home agentflow \
    && mkdir -p /workspace \
    && chown agentflow:agentflow /workspace

COPY --chmod=755 agent.mjs ./agent.mjs
RUN ln -s /opt/agentflow-opencode/agent.mjs /usr/local/bin/agent

WORKDIR /workspace
USER 10001:10001
ENTRYPOINT ["agent"]
