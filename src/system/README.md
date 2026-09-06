# AgentFlow System

FastAPI control plane của AgentFlow. Service này sở hữu API workflow, PostgreSQL schema `system`, migration Alembic và xác thực Keycloak. Agent runtime nằm riêng tại `../agent` và được xem như một integration.

System cũng quản lý skill catalog. Built-in skill nằm trong `skills/<slug>/SKILL.md` cùng `metadata.json` và được đồng bộ vào PostgreSQL khi khởi động. Skill do người dùng tạo được lưu trực tiếp trong `system.skills`; workflow chọn skill qua `PUT /v1/workflows/{id}/skills`.

```powershell
uv sync --locked
$env:DATABASE_URL = "postgresql+asyncpg://agentflow:password@localhost:5432/agentflow"
uv run alembic upgrade head
uv run uvicorn system_api.api:app --reload --port 8000
```

OpenAPI: `http://localhost:8000/docs`. Readiness kiểm tra PostgreSQL tại `GET /ready`.
