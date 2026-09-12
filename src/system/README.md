# AgentFlow System

FastAPI control plane của AgentFlow đồng thời là orchestrator: lifecycle của backend chạy `runtime.orchestrator` để dispatch outbox vào Redis Stream. `runtime.worker` vẫn là process/container độc lập chịu trách nhiệm thực thi workflow.

System cũng quản lý skill catalog. Built-in skill nằm trong `skills/<slug>/SKILL.md` cùng `metadata.json` và được đồng bộ vào PostgreSQL khi khởi động. Skill do người dùng tạo được lưu trực tiếp trong `system.skills`; workflow chọn skill qua `PUT /v1/workflows/{id}/skills`.

```powershell
uv sync --locked
$env:DATABASE_URL = "postgresql+asyncpg://agentflow:password@localhost:5432/agentflow"
uv run alembic upgrade head
uv run uvicorn main:app --reload --port 8000
# Ở terminal khác, với REDIS_URL đã cấu hình:
uv run python -m runtime.worker
```

## Backend structure

- `main.py`: application factory and orchestrator lifecycle.
- `api/`: small FastAPI routers grouped by resource.
- `core/`: authentication and database infrastructure.
- `domain/`: persistence models, schemas, examples, errors, and graph validation.
- `providers/`: LLM adapters and encrypted provider secrets.
- `runtime/`: Redis queue, orchestrator, worker, and workflow execution engine.
- `services/`: reusable application services such as the skill catalog.

OpenAPI: `http://localhost:8000/docs`. Readiness kiểm tra PostgreSQL tại `GET /ready`. Orchestrator tự chạy bên trong backend; Compose chỉ tạo thêm Redis và worker. UI poll run trong lúc `pending/running`.
