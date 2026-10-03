# Hạ tầng Docker cho AgentFlow

`compose.yaml` cung cấp môi trường phát triển tối thiểu:

| Service | Cổng host | Vai trò |
| --- | --- | --- |
| `web` | `3000` | React/Vite workflow studio và đăng nhập Keycloak PKCE |
| `system` | `8000` | FastAPI control plane, API workflow và orchestrator dispatch outbox |
| `workflow-worker` | Nội bộ | Consumer thực thi workflow và cập nhật run/step |
| `agent-runtime-image` | Không chạy lâu dài | Build agent trong `src/agents` để worker tạo container dùng một lần |
| `redis` | `6379` | Redis Stream queue, consumer group và retry pending job |
| `postgres` | `5432` | Một database dùng chung, phân tách bằng schema |
| `keycloak` | `8080`, management `9000` | OIDC authentication và trang quản trị |

`db-init` tạo idempotent hai schema `system` và `keycloak` trước khi các service khởi động. Redis bật AOF `everysec` và lưu trong named volume `agentflow_redis`. Temporal và object storage chưa được đưa vào Compose.

Service `system` tự chạy migration Alembic, lưu workflow/run/outbox và khởi động orchestrator loop cùng lifecycle FastAPI để đẩy outbox vào Redis. Chỉ `workflow-worker` thực thi graph. Keycloak dùng cùng PostgreSQL/database trong môi trường phát triển nhưng tách ở schema `keycloak`.

## Khởi động

Từ thư mục gốc repository:

```powershell
Copy-Item .env.example .env
# Thay toàn bộ mật khẩu trong .env trước lần chạy đầu tiên.
docker compose config
docker compose build system agent-runtime-image
docker compose up -d
docker compose ps
```

Agent node mới mặc định dùng agent đã viết trong `src/agents`. Có thể chọn **Agent container (src/agents)** trong editor; graph được lưu với cấu hình:

```json
{
  "type": "agent",
  "config": {
    "runtime": "agent",
    "providerId": "<provider-id>",
    "model": "nvidia/nemotron-3-super-120b-a12b:free",
    "instructions": "Phân tích input và trả kết quả theo schema.",
    "inputSchema": { "type": "object" },
    "outputSchema": { "type": "object" }
  }
}
```

Khi worker gặp node này, nó chạy container `agentflow-agent-*` từ image build bằng `src/agents/Dockerfile`, truyền task, context và snapshot đầy đủ của skill qua stdin, inject tạm thời provider/model/API key đã đăng ký trong editor, đọc `RunResult.output` từ stdout rồi luôn dừng/xóa container. Node không chọn riêng model dùng provider/model mặc định đã đăng ký của chủ workflow. Workflow worker không đọc model hoặc credential từ `.env`, và runtime không có nhánh fake model.

Compose local mount `/var/run/docker.sock` và chạy `workflow-worker` dưới quyền root để supervisor tạo container. Quyền này tương đương quyền quản trị Docker host, chỉ phù hợp môi trường phát triển. Production phải tách supervisor thành runner được harden và dùng credential proxy/token ngắn hạn; không mount Docker socket vào API hoặc container agent.

Compose cũng chạy `filesystem-mcp` trên network cô lập `agentflow-runtime`. Để demo, đăng ký MCP Streamable HTTP với URL `http://filesystem-mcp:8002/mcp`, không cần header, rồi chọn MCP đó trong node Agent. Ba tool `write_file`, `read_file`, `list_files` chỉ truy cập volume `/data`; xem [README của MCP demo](../src/mcp-filesystem/README.md).

Mở `http://localhost:3000`, đăng nhập bằng user development trong `.env`, sau đó có thể tạo workflow, sửa draft JSON, validate, publish và tạo run. Giao diện chạy local bằng Vite tại `http://localhost:5173`; cả hai origin đã được cấu hình trong Keycloak và CORS của System API.

## Lấy token development

Mỗi lần container Keycloak khởi động, `infra/keycloak/entrypoint.sh` chạy lệnh import offline với `--override false` từ `infra/keycloak/agentflow-realm.json`, rồi mới chạy server. Import chỉ tạo realm khi chưa tồn tại; restart không xóa realm hoặc tạo lại user. `KEYCLOAK_DEV_USER_ID` giữ OIDC `sub` ổn định cho lần khởi tạo mới. Muốn cập nhật client/user của realm đã có, dùng Admin Console hoặc `kcadm` thay vì import ghi đè. Realm có:

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

System và Keycloak dùng chung database trong named volume `agentflow_postgres`; queue dùng `agentflow_redis`; khóa mã hóa provider nằm trong `agentflow_system_secrets`. `docker compose down` giữ các volume này. Realm import bị bỏ qua nếu realm đã tồn tại, nên sửa JSON không tự cập nhật database cũ.

Muốn xóa toàn bộ dữ liệu development và import lại realm:

```powershell
docker compose down --volumes
docker compose up -d
```

Lệnh trên xóa database local và khóa mã hóa API key trong hai volume; không thể hoàn tác.

## Phạm vi triển khai

Compose dùng Keycloak `start-dev`, HTTP và Direct Access Grant để thử tích hợp trên localhost. Không dùng cấu hình này trực tiếp cho production. Production cần TLS/reverse proxy, hostname thật, authorization flow phù hợp, secret manager, backup, Keycloak optimized build và PostgreSQL được vận hành độc lập.

Image đã khóa theo phiên bản để build lặp lại dễ hơn: PostgreSQL `16.15-alpine3.24`, Redis `8.2.7-alpine`, Keycloak `26.7.3`, Python `3.13.14-slim`, uv `0.12.1`. Khi nâng image, đọc migration guide và thử backup/restore trước.

Nguồn: [Keycloak container](https://www.keycloak.org/server/containers), [realm import và environment placeholders](https://www.keycloak.org/server/importExport), [Keycloak health](https://www.keycloak.org/observability/health), [PostgreSQL official image](https://hub.docker.com/_/postgres).
