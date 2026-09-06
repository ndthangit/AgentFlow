# Hạ tầng Docker cho AgentFlow

`compose.yaml` cung cấp môi trường phát triển tối thiểu:

| Service | Cổng host | Vai trò |
| --- | --- | --- |
| `web` | `3000` | React/Vite workflow studio và đăng nhập Keycloak PKCE |
| `system` | `8000` | FastAPI control plane và API workflow |
| `agent` | `8001` | Deep Agents API, build từ `src/agent/Dockerfile` |
| `postgres` | `5432` | Một database dùng chung, phân tách bằng schema |
| `keycloak` | `8080`, management `9000` | OIDC authentication và trang quản trị |

`db-init` tạo idempotent hai schema `system` và `keycloak` trước khi các service khởi động. Temporal, object storage và UI chưa được đưa vào Compose vì chưa có service sử dụng chúng.

Service `system` tự chạy migration Alembic và dùng schema PostgreSQL `system` để lưu workflow draft, version bất biến và run projection. Keycloak dùng cùng PostgreSQL/database trong môi trường phát triển nhưng tách ở schema `keycloak`. Agent là integration độc lập và không kết nối database.

## Khởi động

Từ thư mục gốc repository:

```powershell
Copy-Item .env.example .env
# Thay toàn bộ mật khẩu trong .env trước lần chạy đầu tiên.
docker compose config
docker compose build system agent
docker compose up -d
docker compose ps
```

Mở `http://localhost:3000`, đăng nhập bằng user development trong `.env`, sau đó có thể tạo workflow, sửa draft JSON, validate, publish và tạo run. Giao diện chạy local bằng Vite tại `http://localhost:5173`; cả hai origin đã được cấu hình trong Keycloak và CORS của System API.

Mặc định `AGENT_DEMO=true`, nên không cần LLM để kiểm tra hạ tầng. Để gọi LLM self-host, đặt `AGENT_DEMO=false`, cấu hình model/base URL trong `.env`, rồi chạy:

```powershell
docker compose up -d --build agent
```

Nếu LLM chạy trên host, container gọi qua `host.docker.internal`; Compose thêm ánh xạ `host-gateway` cho Linux. LLM phải lắng nghe trên interface mà Docker truy cập được, không chỉ loopback trong một container khác. Nếu LLM cũng nằm trong Compose, dùng tên service, ví dụ `http://vllm:8000/v1`.

## Lấy token development

Mỗi lần container Keycloak khởi động, `infra/keycloak/entrypoint.sh` chạy lệnh import offline với `--override true` từ `infra/keycloak/agentflow-realm.json`, rồi mới chạy server. Vì vậy thay đổi client/user trong JSON được áp dụng cả khi PostgreSQL volume đã tồn tại. Realm có:

- resource server `agentflow-api`;
- public client `agentflow-web` dùng Authorization Code + PKCE, cho phép cả `localhost` và `127.0.0.1` trên cổng 3000/5173;
- public client `agentflow-cli`, bật Direct Access Grant chỉ để thử local;
- user lấy từ `KEYCLOAK_DEV_USER` và `KEYCLOAK_DEV_USER_PASSWORD`.

Lấy access token:

```powershell
$tokenResponse = Invoke-RestMethod `
  -Uri http://localhost:8080/realms/agentflow/protocol/openid-connect/token `
  -Method Post `
  -ContentType 'application/x-www-form-urlencoded' `
  -Body @{
    grant_type = 'password'
    client_id = 'agentflow-cli'
    username = $env:KEYCLOAK_DEV_USER
    password = $env:KEYCLOAK_DEV_USER_PASSWORD
  }

$headers = @{ Authorization = "Bearer $($tokenResponse.access_token)" }
$body = @{ task = 'Tạo flow xử lý đơn hàng' } | ConvertTo-Json
Invoke-RestMethod http://localhost:8001/v1/runs -Method Post -Headers $headers -ContentType 'application/json' -Body $body
```

PowerShell không tự nạp `.env` thành biến môi trường. Có thể nhập đúng giá trị trong file `.env` vào hai biến `$env:KEYCLOAK_DEV_USER` và `$env:KEYCLOAK_DEV_USER_PASSWORD`, hoặc thay trực tiếp trong lệnh local. Không đưa access token vào source/log.

`GET /health` không yêu cầu token để Docker kiểm tra liveness. `POST /v1/runs` yêu cầu Bearer token có issuer `http://localhost:8080/realms/agentflow` và audience `agentflow-api`. Agent tải public keys qua mạng nội bộ Docker tại `keycloak:8080`, do đó không cần public endpoint để lấy JWKS.

Admin Console: `http://localhost:8080/admin`, dùng `KEYCLOAK_ADMIN` và `KEYCLOAK_ADMIN_PASSWORD`.

## Dữ liệu và reset development

Database nằm trong named volumes `agentflow_postgres` và `keycloak_postgres`. `docker compose down` giữ dữ liệu. Realm import bị bỏ qua nếu realm đã tồn tại, nên sửa JSON không tự cập nhật database cũ.

Muốn xóa toàn bộ dữ liệu development và import lại realm:

```powershell
docker compose down --volumes
docker compose up -d
```

Lệnh trên xóa cả hai database local và không thể hoàn tác.

## Phạm vi triển khai

Compose dùng Keycloak `start-dev`, HTTP và Direct Access Grant để thử tích hợp trên localhost. Không dùng cấu hình này trực tiếp cho production. Production cần TLS/reverse proxy, hostname thật, authorization flow phù hợp, secret manager, backup, Keycloak optimized build và PostgreSQL được vận hành độc lập.

Image đã khóa theo phiên bản để build lặp lại dễ hơn: PostgreSQL `16.15-alpine3.24`, Keycloak `26.7.3`, Python `3.13.14-slim`, uv `0.12.1`. Khi nâng image, đọc migration guide và thử backup/restore trước.

Nguồn: [Keycloak container](https://www.keycloak.org/server/containers), [realm import và environment placeholders](https://www.keycloak.org/server/importExport), [Keycloak health](https://www.keycloak.org/observability/health), [PostgreSQL official image](https://hub.docker.com/_/postgres).
