# OpenCode runtime

This one-shot runtime uses a local OpenCode SDK/server and follows AgentFlow's JSON
stdin/stdout contract. The image pins matching versions of `opencode-ai` and
`@opencode-ai/sdk` to prevent client/server protocol drift.

## Constraints and security

- The server binds only to `127.0.0.1` inside the single-use container.
- Only OpenRouter providers configured on the **Models** page are currently
  supported. The worker injects the model, base URL, and API key through
  `AGENT_PROVIDER_*` for the specific invocation.
- Configuration references the key only through `{env:AGENT_PROVIDER_API_KEY}`;
  the secret is absent from prompts, argv, logged configuration, and results.
- All built-in tools are denied. Only tools from MCP servers selected for the node
  are allowed; runtime v1 has no workspace, session resume, or interactive approval.
- Streamable HTTP MCP servers map to OpenCode remote MCP. Secret headers use
  environment references, automatic OAuth is disabled, and the run stops with
  `MCP_CONNECTION_FAILED` if a selected server cannot connect. SSE is unsupported.
- Provider error/retry events become public error codes after no more than two
  retries. The adapter aborts before the supervisor deadline so OpenCode cannot turn
  429/5xx responses into a container timeout through unbounded retries.
- The adapter revalidates OpenCode structured output with JSON Schema.
- Each `session.prompt()` creates an OpenTelemetry `generation` span under
  `agent.container.run`. The span always records success/error/timeout, and the
  runtime force-flushes OTLP before the single-use container exits.
- The runtime emits only structured diagnostics without prompts or credentials.
  The worker records these events as JSON logs with `run_id`, `node_id`, model, and
  elapsed time, so runs remain searchable in Loki after the container is removed.

## Build and test

```bash
cd src/agents/opencode && npm test
docker build -t agentflow-agent-opencode:0.1.0 src/agents/opencode
docker run --rm --entrypoint opencode agentflow-agent-opencode:0.1.0 --version
```

When running through AgentFlow, select the `opencode` runtime in the Agent node and
choose an OpenRouter model with suitable tool and structured-output support.
