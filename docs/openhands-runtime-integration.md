# Integrating OpenHands with AgentFlow

Verified: **October 19, 2026**. The implemented runtime uses `openhands-sdk==1.53.0`.

## Integration surface

AgentFlow uses the OpenHands Software Agent SDK directly in the
`agentflow-agent-openhands` image; it does not run the headless OpenHands CLI or GUI
server. The SDK provides `Agent`, `Conversation`, MCP, and structured responses
through `FinishTool`, which fits AgentFlow's one-shot JSON stdin/stdout contract.

Official sources:

- [OpenHands Software Agent SDK](https://github.com/OpenHands/software-agent-sdk)
- [Structured Output](https://github.com/OpenHands/docs/blob/main/sdk/guides/structured-output.mdx)
- [MCP guide](https://github.com/OpenHands/docs/blob/main/sdk/guides/mcp.mdx)
- [OpenHands CLI command reference](https://github.com/OpenHands/docs/blob/main/openhands/usage/cli/command-reference.mdx)

The headless CLI is not used because it targets general automation and follows its
own approval policy. The SDK adapter lets AgentFlow expose only the capabilities
required by the current node.

## Contract and capabilities

The runtime reads `task`, `context`, `skills`, and `output_schema` from stdin. The
worker temporarily injects `AGENT_PROVIDER`, `AGENT_MODEL`,
`AGENT_PROVIDER_BASE_URL`, `AGENT_PROVIDER_API_KEY`, and `AGENT_MCP_SERVERS`; the
runtime reads no static credential from `.env`.

The Agent has only:

- `FinishTool`, with `{ "result": <output_schema> }` to avoid OpenHands reserved-name conflicts.
- Remote Streamable HTTP MCP servers selected for the node.

No `TerminalTool`, `FileEditorTool`, browser, or source workspace is exposed. The
result is taken from the `finish` call, unwrapped from `result`, and revalidated with
JSON Schema before returning `RunResult`.

## Execution limits

- `AGENT_RUNTIME_MAX_STEPS` defaults to 8 and accepts only 1–32.
- The internal deadline ends 20 seconds before `AGENT_RUNTIME_TIMEOUT_SECONDS`, leaving time for cleanup and a public error.
- Each provider request has a maximum 60-second timeout and one retry.
- Conversations, temporary workspaces, and credentials are destroyed with the disposable container.
- The current slice maps only OpenRouter; models become LiteLLM identifiers in the form `openrouter/<model-id>` and must support tool calling.

## Build and operations

Compose builds or pulls `agent-runtime-openhands-image`, registers the `openhands`
runtime ID in `AGENT_RUNTIME_IMAGES`, and the workflow worker resolves the image
from that allow-list. The UI shows OpenHands in the Agent node runtime list.

```powershell
docker compose --env-file docker/.env -f docker/compose.yaml build agent-runtime-openhands-image
docker compose --env-file docker/.env -f docker/compose.yaml up -d --no-deps --force-recreate workflow-worker
```

Do not restart a worker while runs are `pending` or `running`; the new image takes
effect only after the worker is safely recreated.
