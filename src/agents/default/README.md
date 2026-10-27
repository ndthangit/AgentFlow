# Default Agent runtime for AgentFlow

A small Python agent that uses **Deep Agents on LangGraph** to propose a workflow
plan from `task/context`. It calls the `get_node_catalog` tool, uses the
`write_todos` planning tool and a `StateBackend` scratchpad, and returns a typed
`WorkflowPlan`. The agent does not execute nodes from the plan.

This is the primary Agent implementation for the current
**flow → agent → JSON output** integration path.

The workflow worker runs this CLI in **a new container for every Agent node
attempt**, collects the result, and removes the container. Select the `agent`
runtime in the editor or set `config.runtime` to `agent`; Compose builds the
`agentflow-agent-runtime` image. This is the default Agent node runtime and always
calls the real model selected in the editor. It has no fake model. The FastAPI app
below exists only for contract development and testing; it is not a shared workflow
runtime. See the [runtime integration contract](../README.md) and the architecture
overview in the [project README](../../../README.md#architecture).

You can test the container contract directly with the sample request in
[examples/container-request.json](examples/container-request.json):

```powershell
Get-Content -Raw examples/container-request.json | docker run --rm -i --read-only --cap-drop ALL --security-opt no-new-privileges --env AGENT_PROVIDER --env AGENT_MODEL --env OPENAI_COMPATIBLE_BASE_URL --env OPENAI_COMPATIBLE_API_KEY --env OPENROUTER_BASE_URL --env OPENROUTER_API_KEY --entrypoint agent agentflow-agent-runtime:0.1.0
```

## Quick start with a real LLM

Python 3.13 and uv are required. From the repository root:

```powershell
cd src/agents/default
uv sync --locked
uv run --env-file .env agent --task "Create a flow that receives, normalizes, and returns data"
```

## Run a self-hosted LLM through an OpenAI-compatible API

By default, the agent uses `ChatOpenAI` with a custom `base_url` to call a server
that implements the OpenAI Chat Completions API. Compatible options include vLLM,
llama.cpp server, LocalAI, LM Studio, and Ollama endpoints. The model must support
reliable **tool calling** because Deep Agents calls `get_node_catalog` and the
structured-output tool.

```powershell
Copy-Item .env.example .env
# Set AGENT_MODEL and OPENAI_COMPATIBLE_BASE_URL in .env.
uv run --env-file .env agent --task "Propose a flow that calls an inventory API, normalizes data, and waits for approval"
```

Example configuration:

```dotenv
AGENT_PROVIDER=openai-compatible
AGENT_MODEL=qwen3-coder
OPENAI_COMPATIBLE_BASE_URL=http://127.0.0.1:8000/v1
OPENAI_COMPATIBLE_API_KEY=
```

The base URL points to the API root and usually ends in `/v1`; do not append
`/chat/completions`. Leave the key blank when the server does not require
authentication; the client uses the `not-required` placeholder. If authentication
is required, put the real key in `.env` and do not commit that file.

OpenRouter can be used directly without renaming variables:

```dotenv
AGENT_PROVIDER=openrouter
AGENT_MODEL=nvidia/nemotron-3-super-120b-a12b:free
OPENROUTER_API_KEY=your-key
```

When invoked by a workflow, if the Agent node has no explicit provider/model, the
worker selects the user's provider with a `default_model` and passes the model,
base URL, and decrypted credential to the container.

`ChatOpenAI` uses only the standard part of the OpenAI API. Provider-specific fields
such as reasoning metadata may not be preserved. A server that supports only text
completion and not OpenAI tool calls cannot run this agent. Source:
[LangChain OpenAI-compatible endpoints](https://docs.langchain.com/oss/python/concepts/providers-and-models#openai-compatible-endpoints).

Anthropic remains available: set `AGENT_PROVIDER=anthropic`, `AGENT_MODEL`, and
`ANTHROPIC_API_KEY`. Code does not load `.env` automatically; `uv --env-file` loads
it explicitly. `.env` is gitignored.

## Input/output

The CLI accepts `--task` or a JSON object over stdin:

```json
{
  "task": "Propose an order-processing flow",
  "context": "Internal processing only; require approval before sending data outside."
}
```

The output has this shape:

```json
{
  "run_id": "runtime-generated-id",
  "status": "succeeded",
  "mode": "live",
  "output": {
    "summary": "Order-processing plan",
    "steps": [
      {
        "node_type": "trigger.manual",
        "label": "Receive order",
        "instructions": "Receive order data from the user."
      }
    ],
    "notes": []
  }
}
```

The sample output illustrates only the schema. `output` is a sequential proposal,
not an executable workflow definition. The integrated worker uses `output` as the
Agent node result; it does not execute model-proposed steps.

The CLI writes one JSON object to stdout and diagnostics to stderr. Exit codes are
`0` for success, `1` for runtime/configuration failures, and `2` for invalid input
or arguments. Runtime error JSON includes `error.code` and `error.message`; argparse
errors use standard CLI messages.

## HTTP invocation from a flow

Run the server with a real LLM from `src/agents/default`:

```powershell
uv run --env-file .env uvicorn agent.api:app --host 127.0.0.1 --port 8001
```

Call it from PowerShell:

```powershell
$body = @{ task = "Plan an order processing workflow"; context = "Require approval" } | ConvertTo-Json
Invoke-RestMethod -Uri http://127.0.0.1:8001/v1/runs -Method Post -ContentType 'application/json' -Body $body
```

- `GET /health`: liveness and mode; it does not verify credential/provider readiness.
- `POST /v1/runs`: waits for the result in the same request and returns **200** on completion; it does not return 202 or persist a background job.
- `GET /docs`: the OpenAPI UI provided by FastAPI.
- Invalid input returns 422, missing configuration returns 503, a deadline returns 504, and output/provider/step-limit errors return 502.

When `AUTH_ENABLED=true`, `POST /v1/runs` requires a JWT bearer token. The Agent
verifies the RS256 signature with JWKS and checks the issuer, audience, expiration,
and required claims. Docker Compose configures this mode with Keycloak; see the
[project README](../../../README.md#run-agentflow). `GET /health` remains public
for liveness probes.

Sample Node.js client: [examples/invoke-agent.mjs](examples/invoke-agent.mjs). With
the server running:

```powershell
node examples/invoke-agent.mjs
```

The worker can call HTTP or spawn the CLI with argv and JSON stdin. Never interpolate
a prompt into a shell command. Set the client timeout above the 120-second runtime
deadline; do not retry indefinitely because each request creates a new model call.

## Call directly from Python

```python
import asyncio
from agent.contracts import RunRequest
from agent.runtime import run_agent

result = asyncio.run(run_agent(RunRequest(task="Plan a small workflow")))
print(result.output)
```

## Prototype scope

- The HTTP service shares one `AgentRuntime` and model. Every request gets separate graph state and skills and cannot read host files; scratchpads and skills do not cross sessions.
- There is one agent; the general-purpose subagent is disabled through the harness profile. Profile configuration is process-global, so run this module as a separate runtime.
- There is no shell, web search, connector credential, or external action; every run calls a real model.
- Task/context size is limited, output is capped at 12 steps, the graph at 40 supersteps, and the deadline at 120 seconds. This is not a hard billing cap; provider requests already sent may still be charged after a timeout.
- The Agent is an independent integration and does not own a database. The FastAPI control plane, PostgreSQL, and migrations live in [`src/system`](../../system/README.md). The Agent has no streaming, resume, or cancel endpoint; its runtime deadline still applies.
- `run_id` correlates returned results; it is not a job lookup key. In the full engine integration, the worker attaches results to node execution and manages retries.

## Testing

```powershell
uv run python -m unittest discover -s tests -v
uv run ruff check src tests
uv run ruff format --check src tests
```

Unit tests mock model/network boundaries to verify the output schema, per-request
isolation, CLI and HTTP behavior, configuration errors, deadlines/cancellation, and
provider error redaction. The application runtime contains and exposes no fake model.

Versions locked in [uv.lock](uv.lock): Deep Agents 0.7.13 and LangGraph 1.2.11. API
sources: [Deep Agents quickstart](https://docs.langchain.com/oss/python/deepagents/quickstart),
[customization and structured output](https://docs.langchain.com/oss/python/deepagents/customization),
and [disabling subagents](https://docs.langchain.com/oss/python/deepagents/subagents#running-without-subagents).
