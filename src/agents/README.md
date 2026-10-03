# Agent runtimes

Mỗi thư mục con trực tiếp trong `src/agents` là một implementation Agent runtime độc lập. Runtime có sẵn của dự án nằm tại [`default`](default/README.md).

```text
src/agents/
├── default/          # Deep Agents/LangGraph runtime đi kèm dự án
├── my-runtime/       # Runtime tùy biến của người dùng
└── README.md         # Quy ước tích hợp
```

## Chọn runtime cho Docker Compose

Đặt tên thư mục trong `.env`, sau đó build lại image:

```dotenv
AGENT_RUNTIME_NAME=default
```

```powershell
docker compose build agent-runtime-image
docker compose up -d workflow-worker
```

Ví dụ để dùng `src/agents/my-runtime`, đặt `AGENT_RUNTIME_NAME=my-runtime`. Mỗi stack Compose hiện chọn một runtime image; workflow worker vẫn tạo một container dùng một lần cho mỗi Agent node attempt.

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
