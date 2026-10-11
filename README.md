# AgentFlow

AgentFlow là nền tảng thiết kế và chạy workflow bằng giao diện kéo thả. Workflow hỗ
trợ xử lý dữ liệu, rẽ nhánh, chạy song song, gọi LLM, Agent container và MCP.

## Cách chạy

Yêu cầu Docker Desktop hoặc Docker Engine có Docker Compose v2. Các lệnh dưới đây
đều chạy từ thư mục `docker`:

```powershell
Set-Location docker
```

### Local

```powershell
# Chỉ cần sao chép ở lần cấu hình đầu tiên.
Copy-Item .env.example .env
# Đổi các mật khẩu mẫu trong .env.
docker compose up -d --build
```

| Thành phần | Địa chỉ |
| --- | --- |
| Giao diện | http://localhost:3000 |
| System API | http://localhost:8000 |
| API docs | http://localhost:8000/docs |
| Keycloak | http://localhost:8080 |

Đăng nhập bằng `KEYCLOAK_DEV_USER` và `KEYCLOAK_DEV_USER_PASSWORD` trong
`docker/.env`.

### Server dev

```powershell
Copy-Item .env.dev.example .env.dev
# Sửa secret và các biến WEB/SYSTEM/KEYCLOAK_PUBLIC_URL trong .env.dev.
docker compose --env-file .env.dev -f compose.yaml -f compose.dev.yaml up -d --build
```

### Production

```powershell
Copy-Item .env.prod.example .env.prod
# Sửa secret, URL HTTPS và tên image đã được CI build trong .env.prod.
docker compose --env-file .env.prod -f compose.yaml -f compose.prod.yaml pull
docker compose --env-file .env.prod -f compose.yaml -f compose.prod.yaml up -d --remove-orphans
```

### Kiểm tra và dừng local

```powershell
docker compose ps
docker compose logs -f system workflow-worker web
docker compose down
```

Lệnh `down` giữ nguyên dữ liệu. Chỉ dùng `down --volumes` khi muốn xóa toàn bộ
database và cấu hình đã lưu.

## Sử dụng

1. Khai báo provider và model trong mục Models.
2. Tạo workflow, thêm node và nối các node trên canvas.
3. Cấu hình từng node rồi nhấn **Lưu thay đổi**.
4. Validate, publish, chạy workflow và xem kết quả từng step.

Lưu node sẽ tự cập nhật draft. Publish luôn dùng draft mới nhất nên không có nút
**Lưu draft** riêng.

## Cấu trúc

- `src/web`: giao diện React/Vite.
- `src/system`: FastAPI, orchestrator và workflow worker.
- `src/agents`: các Agent runtime chạy trong container.
- `src/mcp-filesystem`: MCP filesystem dùng để demo.
- `docker`: Compose, env mẫu và cấu hình Keycloak.
- `docs`: tài liệu thiết kế và vận hành.

Docker được tách thành ba môi trường: local, server dev và production. Xem lệnh chạy,
HTTPS và cấu hình image trong [hướng dẫn Docker](docker/README.md).

## Tài liệu

- [Bản đồ codebase](docs/codebase-map.md)
- [Workflow và cơ chế thực thi](docs/workflow-execution.md)
- [Quản lý Agent runtime](docs/agent-runtime-management.md)
- [Provider và model LLM](docs/llm-providers.md)

Giấy phép: [MIT](LICENSE).
