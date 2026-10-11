# Bản đồ codebase AgentFlow

Tài liệu này chỉ ra luồng chạy hiện tại và nơi sở hữu từng trách nhiệm. Các thiết kế
mục tiêu chưa triển khai được mô tả riêng trong các tài liệu kiến trúc khác.

## Luồng chạy từ UI đến worker

```text
React UI
  -> Keycloak (đăng nhập, lấy access token)
  -> FastAPI router (xác thực và giới hạn dữ liệu theo owner)
  -> workflow draft -> validate -> immutable workflow version
  -> run + pending steps + dispatch outbox trong một transaction
  -> orchestrator chuyển outbox vào Redis Stream
  -> workflow worker claim message và run
  -> engine resolve DAG, input binding và branch
  -> builtin / Python giới hạn / LLM adapter / Agent container
  -> worker lưu step + run projection rồi ACK message
  -> UI poll run và step để hiển thị kết quả
```

Các ranh giới quan trọng:

- UI chỉ sửa draft; run luôn dùng snapshot `workflow_versions.graph` đã publish.
- API không thực thi graph trong request tạo run.
- PostgreSQL giữ trạng thái nghiệp vụ; Redis giữ trạng thái delivery.
- Agent node chọn runtime từ catalog allow-list. Graph không được truyền image tùy ý.
- Secret provider và MCP được giải mã sát thời điểm gọi, không lưu vào graph hay argv.

## Sở hữu mã nguồn

### `src/system`

| Thư mục | Trách nhiệm |
| --- | --- |
| `api/` | HTTP router theo resource; authorization và mapping request/response |
| `core/` | Kết nối database và xác minh token OIDC |
| `domain/` | SQLAlchemy model, Pydantic schema, node registry và validation graph |
| `providers/` | Adapter LLM và mã hóa secret provider |
| `services/` | Logic ứng dụng dùng lại, hiện gồm skill và MCP server |
| `runtime/` | Outbox dispatcher, Redis queue, worker, DAG engine và executor |
| `migrations/` | Lịch sử schema Alembic; không chứa logic runtime |
| `tests/` | Test contract và hành vi theo cùng ranh giới phía trên |

Điểm vào process:

- `main.py`: FastAPI application và lifecycle của orchestrator.
- `runtime/worker.py`: process consumer Redis độc lập.
- `runtime/engine.py`: điều phối node theo dependency và giới hạn song song.
- `runtime/agent_executor.py`: chọn đường gọi Agent container hoặc LLM trực tiếp.

### `src/web`

| Thư mục | Trách nhiệm |
| --- | --- |
| `src/app/` | App shell, điều hướng và phối hợp state cấp trang |
| `src/features/mcp/` | Màn hình MCP server |
| `src/features/models/` | Màn hình provider/model |
| `src/features/workflows/` | Template, nhãn tóm tắt và workflow editor |
| `src/features/workflows/editor/graph.ts` | Hàm thuần parse, layout, cycle, schema và tạo node |
| `src/features/workflows/editor/` components | Canvas node và schema builder |
| `src/shared/` | API client, Keycloak và TypeScript contracts dùng chung |
| `src/styles/` | CSS entry point |

### Agent runtime và MCP

- `src/agents/manage.py` quản lý manifest/build runtime; mỗi runtime độc lập nằm trong
  `src/agents/<runtime-id>`.
- `src/agents/default/src/agent` triển khai contract JSON qua stdin/stdout bằng Deep
  Agents/LangGraph.
- `src/mcp-filesystem` là MCP server demo với root filesystem bị giới hạn.

## Chọn nơi sửa theo loại thay đổi

| Thay đổi | Nơi bắt đầu |
| --- | --- |
| Thêm endpoint/resource | `src/system/api`, sau đó schema/service/domain tương ứng |
| Thêm loại node | `domain/node_registry.py`, `domain/validation.py`, `runtime/engine.py`, UI editor |
| Đổi semantics branch/input | `runtime/engine.py` và test execution/workflow |
| Thêm provider LLM | `providers/adapters.py`, API provider và shared web contracts |
| Đổi cách chạy Agent | `runtime/agent_runtime_manager.py`, `agent_container.py`, runtime manifest |
| Thêm workflow mẫu | `src/web/src/features/workflows/templates.ts` |
| Đổi thao tác graph/canvas | `editor/graph.ts` hoặc component cụ thể, không đặt vào app shell |
| Đổi database | model + migration Alembic mới; không sửa migration đã phát hành |

## Kiểm tra trước khi bàn giao

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
