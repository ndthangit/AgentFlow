# Hạ tầng Docker cho AgentFlow

AgentFlow dùng một Compose gốc và hai file override. Cách này giữ định nghĩa service,
healthcheck, volume và dependency ở một nơi, nhưng vẫn tách rõ cách chạy của từng
môi trường.

| Môi trường | Compose | Env | Project mặc định |
| --- | --- | --- | --- |
| Local | `compose.yaml` | `.env` | `agentflow` |
| Server dev | `compose.yaml` + `compose.dev.yaml` | `.env.dev` | `agentflow-dev` |
| Production | `compose.yaml` + `compose.prod.yaml` | `.env.prod` | `agentflow-prod` |

Không chạy riêng `compose.dev.yaml` hoặc `compose.prod.yaml`; chúng là override của
`compose.yaml`. Tên project khác nhau làm container, network và named volume của ba
môi trường không đè lên nhau.

Tất cả lệnh bên dưới được chạy trong thư mục Docker:

```powershell
Set-Location docker
```

## 1. Local

Local build source hiện tại, chỉ bind service vào `127.0.0.1` và chạy Keycloak bằng
`start-dev`.

```powershell
Copy-Item .env.example .env
# Thay các password mẫu trước lần chạy đầu tiên.

docker compose config
docker compose up -d --build
docker compose ps
```

Các endpoint local:

| Service | URL/cổng host |
| --- | --- |
| Web | `http://localhost:3000` |
| System API | `http://localhost:8000` |
| Keycloak | `http://localhost:8080` |
| Keycloak management | `http://localhost:9000` |
| PostgreSQL | `127.0.0.1:5432` |
| Redis | `127.0.0.1:6379` |
| Filesystem MCP | `127.0.0.1:8002` |

Dừng local nhưng giữ dữ liệu:

```powershell
docker compose down
```

## 2. Server development

Server dev vẫn build trực tiếp từ source, nhưng dùng public URL của server, project
và network riêng, đồng thời giới hạn log Docker. PostgreSQL, Redis, MCP và cổng
management Keycloak mặc định chỉ bind loopback; web, API và Keycloak bind `0.0.0.0`
để có thể kiểm thử từ máy khác.

```powershell
Copy-Item .env.dev.example .env.dev
# Sửa password, email và ba biến WEB/SYSTEM/KEYCLOAK_PUBLIC_URL.

docker compose `
  --env-file .env.dev `
  -f compose.yaml `
  -f compose.dev.yaml `
  config

docker compose `
  --env-file .env.dev `
  -f compose.yaml `
  -f compose.dev.yaml `
  up -d --build
```

Xem trạng thái và log:

```powershell
docker compose --env-file .env.dev -f compose.yaml -f compose.dev.yaml ps
docker compose --env-file .env.dev -f compose.yaml -f compose.dev.yaml logs -f system workflow-worker
```

Dừng server dev:

```powershell
docker compose --env-file .env.dev -f compose.yaml -f compose.dev.yaml down
```

## 3. Production server

Production không build source trên server. `compose.prod.yaml` loại toàn bộ `build`
và chỉ pull các image bất biến được khai báo trong `.env.prod`. PostgreSQL, Redis và
Filesystem MCP không publish port. Web, API và Keycloak mặc định bind loopback để
reverse proxy trên host terminate TLS.

Chuẩn bị:

1. CI build và push bốn image `system`, `web`, `agent-runtime` và `filesystem-mcp`.
2. Image web phải được build với đúng `VITE_SYSTEM_API_URL` và
   `VITE_KEYCLOAK_URL` production vì các giá trị này được nhúng lúc build.
3. Reverse proxy ánh xạ các hostname HTTPS tới:
   - web: `127.0.0.1:3000`;
   - System API: `127.0.0.1:8000`;
   - Keycloak: `127.0.0.1:8080`.
4. Tạo backup volume/database trước mỗi lần nâng version.

Triển khai:

```powershell
Copy-Item .env.prod.example .env.prod
# Thay toàn bộ secret, public URL và image trong .env.prod.

docker login ghcr.io

docker compose `
  --env-file .env.prod `
  -f compose.yaml `
  -f compose.prod.yaml `
  config

docker compose `
  --env-file .env.prod `
  -f compose.yaml `
  -f compose.prod.yaml `
  pull

docker compose `
  --env-file .env.prod `
  -f compose.yaml `
  -f compose.prod.yaml `
  up -d --remove-orphans
```

Kiểm tra sau triển khai:

```powershell
docker compose --env-file .env.prod -f compose.yaml -f compose.prod.yaml ps
docker compose --env-file .env.prod -f compose.yaml -f compose.prod.yaml logs --tail 200 system workflow-worker keycloak
```

Production Keycloak chạy lệnh `start` với proxy headers thay vì `start-dev`.
`agentflow-realm-prod.json` chỉ cho phép redirect tới `WEB_PUBLIC_URL`, không bật
Direct Access Grant của CLI local.

## 4. Service và dữ liệu

| Service | Trách nhiệm |
| --- | --- |
| `postgres` | Database chung; dữ liệu ứng dụng và Keycloak nằm ở schema riêng |
| `redis` | Redis Stream queue và pending job |
| `db-init` | Tạo idempotent schema `system` và `keycloak` |
| `keycloak` | OIDC, user và client web PKCE |
| `system` | FastAPI control plane, migration và outbox orchestrator |
| `workflow-worker` | Claim queue, thực thi graph và ghi run/step |
| `agent-runtime-image` | Đảm bảo image runtime tồn tại trước khi worker chạy |
| `filesystem-mcp` | MCP demo trên network runtime riêng |
| `web` | React UI được phục vụ bởi Nginx |

Named volume lưu PostgreSQL, Redis, khóa mã hóa provider và dữ liệu MCP. Lệnh
`down` giữ volume. Chỉ dùng `down --volumes` khi thực sự muốn xóa toàn bộ dữ liệu;
thao tác này không thể hoàn tác.

## 5. Worker và Agent runtime

Số worker được cấu hình bằng `WORKFLOW_WORKER_REPLICAS`. Các replica dùng chung
Redis consumer group nhưng có consumer name riêng, nên một message chỉ được một
worker claim tại một thời điểm.

Agent node tạo container dùng một lần từ `AGENTFLOW_AGENT_RUNTIME_IMAGE`. Provider,
model và API key được lấy từ workflow/provider đã đăng ký rồi inject tạm thời vào
container; chúng không nằm trong file env deploy.

Worker hiện cần Docker socket để tạo Agent container:

```text
/var/run/docker.sock:/var/run/docker.sock
```

Quyền này tương đương quyền quản trị Docker host. Compose production đã giảm bề mặt
public network nhưng chưa thay thế Docker socket bằng runner supervisor độc lập.
Trước khi coi hệ thống là hardened production, cần tách runner sang host/node riêng,
giới hạn image allow-list và dùng credential ngắn hạn.

## 6. Keycloak realm

- Local dùng `docker/keycloak/agentflow-realm.json`, gồm redirect localhost và CLI
  development.
- Server dev dùng `docker/keycloak/agentflow-realm-dev.json`, lấy redirect/origin từ
  env và cho phép HTTP để kiểm thử.
- Production dùng `docker/keycloak/agentflow-realm-prod.json`, lấy redirect/origin từ
  env và yêu cầu HTTPS cho request bên ngoài.
- Import dùng `--override false`: chỉ tạo realm nếu chưa tồn tại. Đổi file JSON không
  cập nhật realm trong volume cũ; hãy sửa bằng Admin Console hoặc tạo deployment mới.
- `KEYCLOAK_DEV_USER_ID` phải ổn định sau khi đã có workflow vì claim `sub` là khóa
  sở hữu dữ liệu.

## 7. Validate cấu hình trong CI

Có thể kiểm tra cả ba file mà không khởi động container:

```powershell
docker compose config --quiet
docker compose --env-file .env.dev.example -f compose.yaml -f compose.dev.yaml config --quiet
docker compose --env-file .env.prod.example -f compose.yaml -f compose.prod.yaml config --quiet
```
