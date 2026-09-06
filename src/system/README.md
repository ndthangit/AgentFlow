# AgentFlow System

FastAPI control plane của AgentFlow. Service này sở hữu API workflow, PostgreSQL schema `system`, migration Alembic và xác thực Keycloak. Agent runtime nằm riêng tại `../agent` và được xem như một integration.

```powershell
uv sync --locked
$env:DATABASE_URL = "postgresql+asyncpg://agentflow:password@localhost:5432/agentflow"
uv run alembic upgrade head
uv run uvicorn system_api.api:app --reload --port 8000
```

OpenAPI: `http://localhost:8000/docs`. Readiness kiểm tra PostgreSQL tại `GET /ready`.
