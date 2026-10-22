# AgentFlow

AgentFlow is a platform for designing and running workflows through a drag-and-drop
interface. Workflows support data processing, branching, parallel execution, LLM
calls, Agent containers, and MCP.

## Running the application

Docker Desktop or Docker Engine with Docker Compose v2 is required. Run all commands
below from the `docker` directory:

```powershell
Set-Location docker
```

### Local

```powershell
# Copy this file only during initial setup.
Copy-Item .env.example .env
# Replace the sample passwords in .env.
docker compose up -d --build
```

| Component | Address |
| --- | --- |
| Web UI | http://localhost:3000 |
| System API | http://localhost:8000 |
| API docs | http://localhost:8000/docs |
| Keycloak | http://localhost:8080 |

Sign in with `KEYCLOAK_DEV_USER` and `KEYCLOAK_DEV_USER_PASSWORD` from
`docker/.env`.

### Server dev

```powershell
Copy-Item .env.dev.example .env.dev
# Update the secrets and WEB/SYSTEM/KEYCLOAK_PUBLIC_URL variables in .env.dev.
docker compose --env-file .env.dev -f compose.yaml -f compose.dev.yaml up -d --build
```

### Production

```powershell
Copy-Item .env.prod.example .env.prod
# Update the secrets, HTTPS URLs, and CI-built image names in .env.prod.
docker compose --env-file .env.prod -f compose.yaml -f compose.prod.yaml pull
docker compose --env-file .env.prod -f compose.yaml -f compose.prod.yaml up -d --remove-orphans
```

### Inspecting and stopping the local stack

```powershell
docker compose ps
docker compose logs -f system workflow-worker web
docker compose down
```

The `down` command preserves data. Use `down --volumes` only when you intend to
delete the entire database and all saved configuration.

## Usage

1. Configure a provider and its models under Models.
2. Create a workflow, add nodes, and connect them on the canvas.
3. Configure each node and select **Save changes**.
4. Validate, publish, and run the workflow, then inspect each step's result.

Saving a node automatically updates the draft. Publishing always uses the latest
draft, so there is no separate **Save draft** button.

## Project structure

- `src/web`: React/Vite web interface.
- `src/system`: FastAPI, the orchestrator, and workflow workers.
- `src/agents`: Agent runtimes that run in containers.
- `src/mcp-filesystem`: demo filesystem MCP server.
- `docker`: Compose files, environment templates, and Keycloak configuration.
- `docs`: design and operations documentation.

Docker supports three environments: local, server development, and production. See
the [Docker guide](docker/README.md) for commands, HTTPS, and image configuration.

## Documentation

- [Codebase map](docs/codebase-map.md)
- [Workflow execution](docs/workflow-execution.md)
- [Agent runtime management](docs/agent-runtime-management.md)
- [Worker concurrency and Agent runtime scaling](docs/worker-concurrency-and-agent-runtime-scaling.md)
- [Codex and OpenCode integration](docs/codex-opencode-runtime-integration.md)
- [LLM providers and models](docs/llm-providers.md)
- [Provider integration testing without real API keys](docs/provider-integration-testing.md)
- [System and workflow observability](docs/observability.md)

License: [MIT](LICENSE).
