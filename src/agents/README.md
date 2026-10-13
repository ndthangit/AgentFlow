# Agent runtimes

Mỗi thư mục con trực tiếp trong `src/agents` là một implementation Agent runtime độc lập. Dự án có runtime [`default`](default/README.md), [`codex`](codex/README.md) và [`opencode`](opencode/README.md).

Thiết kế và lộ trình adapter cụ thể cho Codex/OpenCode nằm tại [tài liệu tích hợp runtime](../../docs/codex-opencode-runtime-integration.md).

```text
src/agents/
├── default/          # Deep Agents/LangGraph runtime đi kèm dự án
├── codex/            # OpenAI Codex CLI one-shot runtime
├── opencode/         # OpenCode SDK/server one-shot runtime
├── my-runtime/       # Runtime tùy biến của người dùng
└── README.md         # Quy ước tích hợp
```

## Build catalog runtime cho Docker Compose

Compose build sẵn các runtime `default`, `codex` và `opencode`, rồi đăng ký ba image trong
`AGENT_RUNTIME_IMAGES` của workflow worker:

```powershell
docker compose --env-file docker/.env -f docker/compose.yaml build agent-runtime-default-image agent-runtime-codex-image agent-runtime-opencode-image
docker compose --env-file docker/.env -f docker/compose.yaml up -d workflow-worker
```

Workflow chọn runtime bằng `config.runtime`; `agent` là alias của
`AGENT_RUNTIME_DEFAULT`. Muốn thêm `my-runtime`, hãy thêm service build/pull image
và entry tương ứng vào `AGENT_RUNTIME_IMAGES`. Worker vẫn tạo một container dùng
một lần cho mỗi Agent node attempt.

## Contract tối thiểu cho runtime mới

Một thư mục runtime mới phải:

1. Có `Dockerfile` build được từ chính thư mục đó.
2. Cung cấp executable `agent` trên `PATH` của image.
3. Đọc đúng một JSON request UTF-8 từ `stdin` khi chạy `agent` không có tham số.
4. Ghi đúng một JSON result vào `stdout`; log chẩn đoán phải ghi vào `stderr`.
5. Không ghi credential, provider response thô hoặc secret vào output/log.
6. Chạy được với filesystem chỉ đọc, user không đặc quyền, giới hạn CPU/RAM/PID và network do supervisor áp đặt.

Request hiện tại có các field `task`, `context`, `skills` và `output_schema`. Khi thành công, runtime trả:

```json
{
  "run_id": "runtime-generated-id",
  "status": "succeeded",
  "mode": "live",
  "output": {}
}
```

Khi thất bại, runtime nên trả JSON `{ "error": { "code": "...", "message": "..." } }` và exit code khác `0`. Có thể sao chép [`default`](default/README.md) làm điểm khởi đầu, nhưng không nên commit `.env`, `.venv`, cache hoặc credential vào runtime mới.
