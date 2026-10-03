# AgentFlow System

FastAPI control plane của AgentFlow đồng thời là orchestrator: lifecycle của backend chạy `runtime.orchestrator` để dispatch outbox vào Redis Stream. `runtime.worker` vẫn là process/container độc lập chịu trách nhiệm thực thi workflow.

Agent node mặc định dùng runtime `agent`: Compose build runtime được chọn bởi `AGENT_RUNTIME_NAME` (mặc định `src/agents/default`), sau đó worker tạo container từ `AGENT_RUNTIME_IMAGE`. Container nhận JSON qua stdin, trả JSON qua stdout và được cleanup sau mỗi attempt. Runtime `direct` vẫn tồn tại làm đường tương thích để gọi provider adapter ngay trong worker. Xem [quy ước Agent runtime](../agents/README.md), hướng dẫn local và lưu ý Docker socket trong [tài liệu hạ tầng](../../infra/README.md).

System cũng quản lý skill catalog. Built-in skill nằm trong `skills/<slug>/SKILL.md` cùng `metadata.json` và được đồng bộ vào PostgreSQL khi khởi động. Skill do người dùng tạo được lưu trực tiếp trong `system.skills`; mọi skill Active được chọn trực tiếp tại từng Agent node qua `config.skillIds` và được snapshot khi publish.

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
