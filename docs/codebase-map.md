# AgentFlow codebase map

This document describes the current execution flow and the ownership of each
responsibility. Unimplemented target designs are documented separately in the other
architecture documents.

## Execution flow from UI to worker

```text
React UI
  -> Keycloak (sign in and obtain an access token)
  -> FastAPI router (authenticate and scope data to the owner)
  -> workflow draft -> validate -> immutable workflow version
  -> run + pending steps + dispatch outbox in one transaction
  -> orchestrator moves outbox entries to a Redis Stream
  -> workflow worker claims the message and run
  -> engine resolves the DAG, input bindings, and branches
  -> built-in / restricted Python / LLM adapter / Agent container
  -> worker saves step and run projections, then ACKs the message
  -> UI polls the run and steps to display results
```

Important boundaries:

- The UI edits only drafts; runs always use a published `workflow_versions.graph` snapshot.
- The API does not execute the graph in the create-run request.
- PostgreSQL stores business state; Redis stores delivery state.
- Agent nodes select runtimes from an allow-listed catalog. Graphs cannot provide arbitrary images.
- Provider and MCP secrets are decrypted immediately before use and are never stored in graphs or argv.

## Source ownership

### `src/system`

| Directory | Responsibility |
| --- | --- |
| `api/` | Resource-oriented HTTP routers, authorization, and request/response mapping |
| `core/` | Database connections and OIDC token verification |
| `domain/` | SQLAlchemy models, Pydantic schemas, node registry, and graph validation |
| `providers/` | LLM adapters and provider-secret encryption |
| `services/` | Reusable application logic, currently skills and MCP servers |
| `runtime/` | Outbox dispatcher, Redis queue, worker, DAG engine, and executors |
| `migrations/` | Alembic schema history; no runtime logic |
| `tests/` | Contract and behavior tests that follow the same boundaries |

Process entry points:

- `main.py`: FastAPI application and orchestrator lifecycle.
- `runtime/worker.py`: independent Redis consumer process.
- `runtime/engine.py`: schedules nodes by dependency and concurrency limits.
- `runtime/agent_executor.py`: selects the Agent container or direct LLM path.

### `src/web`

| Directory | Responsibility |
| --- | --- |
| `src/app/` | App shell, navigation, and page-level state coordination |
| `src/features/mcp/` | MCP server screens |
| `src/features/models/` | Provider/model screens |
| `src/features/workflows/` | Templates, summary labels, and workflow editor |
| `src/features/workflows/editor/graph.ts` | Pure parsing, layout, cycle, schema, and node-creation functions |
| `src/features/workflows/editor/` components | Node canvas and schema builder |
| `src/shared/` | Shared API client, Keycloak integration, and TypeScript contracts |
| `src/styles/` | CSS entry point |

### Agent runtimes and MCP

- `src/agents/manage.py` manages runtime manifests/builds; each independent runtime lives under
  `src/agents/<runtime-id>`.
- `src/agents/default/src/agent` implements the JSON stdin/stdout contract with Deep Agents/LangGraph.
- `src/mcp-filesystem` is a demo MCP server with a restricted filesystem root.
- `src/provider-mock` is a local test double for OpenAI, Anthropic, and NVIDIA; it is enabled only through `docker/compose.provider-mock.yaml` in local/CI environments.

## Where to start for each change type

| Change | Starting point |
| --- | --- |
| Add an endpoint/resource | `src/system/api`, then the corresponding schema/service/domain |
| Add a node type | `domain/node_registry.py`, `domain/validation.py`, `runtime/engine.py`, and the UI editor |
| Change branch/input semantics | `runtime/engine.py` and execution/workflow tests |
| Add an LLM provider | `providers/adapters.py`, provider API, and shared web contracts |
| Change Agent execution | `runtime/agent_runtime_manager.py`, `agent_container.py`, and runtime manifest |
| Add a sample workflow | `src/web/src/features/workflows/templates.ts` |
| Change graph/canvas operations | `editor/graph.ts` or the specific component, not the app shell |
| Change the database | model plus a new Alembic migration; do not edit released migrations |

## Checks before handoff

```powershell
$env:UV_CACHE_DIR = "$PWD/.uv-cache"
uv run --project src/system ruff check src/system
uv run --project src/system ruff format --check src/system
uv run --project src/system python -m unittest discover -s src/system/tests

uv run --project src/agents/default ruff check src/agents/default
uv run --project src/agents/default python -m unittest discover -s src/agents/default/tests

uv run --project src/mcp-filesystem ruff check src/mcp-filesystem
uv run --project src/mcp-filesystem python -m unittest discover -s src/mcp-filesystem/tests

cd src/web
npm.cmd run build
```
