# Codex runtime

This one-shot runtime uses the OpenAI Codex CLI and follows AgentFlow's JSON
stdin/stdout contract. The image pins `@openai/codex` for reproducible deployments.
It installs the system CA bundle because the native Codex binary requires the
operating system trust store for HTTPS connections to model providers.

## Credentials and models

Do not configure API keys in the Dockerfile, Compose, or `.env`. When a workflow
runs, the System reads the Agent node's provider and model from **Models**, decrypts
the key in the worker, and passes it only to the single-use container:

- `AGENT_MODEL`
- `AGENT_PROVIDER_BASE_URL`
- `AGENT_PROVIDER_API_KEY`

The adapter configures a custom Codex model provider that uses the Responses API.
The Codex session runs with `--ephemeral`, ignores user configuration, uses the
`read-only` sandbox, never requests approval, and returns data matching the node's
`output_schema`.

MCP servers selected for the node are mapped from `AGENT_MCP_SERVERS` to Codex
`mcp_servers.<slug>` configuration. The runtime supports Streamable HTTP; SSE is
rejected because the Codex CLI does not support that transport. Authentication
headers are stored in temporary environment variables and Codex receives only the
`env_http_headers` mapping, so secret values never appear in argv.

The selected model must support the Responses API and the tool semantics used by
the Codex CLI. For OpenRouter, select a Responses-compatible Codex/GPT model. If the
provider rejects the model, the adapter returns a public error instead of falling
back to another model.

## Build and test

```bash
docker build -t agentflow-agent-codex:0.1.0 src/agents/codex
docker run --rm --entrypoint codex agentflow-agent-codex:0.1.0 --version
cd src/agents/codex && npm test
```

When running through AgentFlow, select the `codex` runtime in the Agent node. If you
use MCP, select a server with the Streamable HTTP transport in the same node
configuration.
