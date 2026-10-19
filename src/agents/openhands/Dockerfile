FROM python:3.12.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp/agentflow-home \
    XDG_CONFIG_HOME=/tmp/agentflow-config \
    XDG_DATA_HOME=/tmp/agentflow-data \
    XDG_CACHE_HOME=/tmp/agentflow-cache \
    OPENHANDS_SUPPRESS_BANNER=1 \
    LITELLM_LOCAL_MODEL_COST_MAP=True

WORKDIR /opt/agentflow-openhands
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --requirement requirements.txt \
    && groupadd --system --gid 10001 agentflow \
    && useradd --system --uid 10001 --gid agentflow --home-dir /tmp/agentflow-home agentflow \
    && mkdir -p /workspace \
    && chown agentflow:agentflow /workspace

COPY --chmod=755 agent.py ./agent.py
COPY test ./test
RUN ln -s /opt/agentflow-openhands/agent.py /usr/local/bin/agent

WORKDIR /workspace
USER 10001:10001
ENTRYPOINT ["agent"]
