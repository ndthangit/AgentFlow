# OpenHands runtime

This runtime uses `openhands-sdk==1.53.0` and maps the OpenHands Software Agent SDK
to AgentFlow's one-shot container contract.

## Scope

- Accepts one JSON request over `stdin` and returns only one result/error JSON object over `stdout`.
- Uses the provider, model, and API key injected by the workflow worker for the node; OpenRouter is currently supported.
- Uses `FinishTool` with a wrapped response schema to return structured output, then validates it again with JSON Schema.
- Exposes only `FinishTool` and selected Streamable HTTP MCP servers. It does not expose a built-in terminal, browser, or file editor.
- Limits agent turns with `AGENT_RUNTIME_MAX_STEPS` (8 by default, 32 maximum) and finishes before the container timeout to leave time for cleanup.
- Retains no conversation, workspace, or credentials after an invocation.

## Build and test

```powershell
docker build -t agentflow-agent-openhands:0.1.0 src/agents/openhands
docker run --rm --network none --entrypoint python agentflow-agent-openhands:0.1.0 -m unittest discover -s /opt/agentflow-openhands/test
```

When running through AgentFlow, select the `openhands` runtime in the Agent node and
choose a provider/model from Models. The model must support tool calling because the
structured result is returned through `FinishTool`.
