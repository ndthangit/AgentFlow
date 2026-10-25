# AgentFlow System

AgentFlow's FastAPI control plane also hosts the orchestrator and recovery controller:
the backend lifecycle runs `runtime.orchestrator` to dispatch the outbox to a Redis
Stream and `runtime.recovery` to reconcile expired/timed-out runs and remove orphaned
Agent containers.
`runtime.worker` remains an independent process/container responsible for workflow
execution.

Agent nodes use the `agent`/`default` runtime by default. Compose builds a catalog
containing `default`, `codex`, and `opencode`, and the worker resolves images from
`AGENT_RUNTIME_IMAGES`. A container receives JSON over stdin and temporary provider,
model, and API-key settings through its environment, returns JSON over stdout, and
is cleaned up after every attempt. The `direct` runtime remains as a compatibility
path that calls a provider adapter inside the worker. See the
[Agent runtime contract](../agents/README.md) and the local setup and Docker socket
notes in the [infrastructure guide](../../docker/README.md).

The System also manages the skill catalog. Built-in skills live in
`skills/<slug>/SKILL.md` with `metadata.json` and are synchronized to PostgreSQL at
startup. User-created skills are stored directly in `system.skills`; every active
skill can be selected on an Agent node through `config.skillIds` and is snapshotted
when the workflow is published.

```powershell
uv sync --locked
$env:DATABASE_URL = "postgresql+asyncpg://agentflow:password@localhost:5432/agentflow"
uv run alembic upgrade head
uv run uvicorn main:app --reload --port 8000
# In another terminal, with REDIS_URL configured:
uv run python -m runtime.worker
```

## Backend structure

- `main.py`: application factory, orchestrator, and recovery lifecycle.
- `api/`: small FastAPI routers grouped by resource.
- `core/`: authentication and database infrastructure.
- `domain/`: persistence models, schemas, examples, errors, and graph validation.
- `providers/`: LLM adapters and encrypted provider secrets.
- `runtime/`: Redis queue, orchestrator, worker, and workflow execution engine.
- `services/`: reusable application services such as the skill catalog.

OpenAPI: `http://localhost:8000/docs`. The `GET /ready` endpoint checks PostgreSQL
readiness. The orchestrator and recovery controller run inside the backend; Compose
adds Redis and the worker. The UI polls runs while they are `pending` or `running`.
