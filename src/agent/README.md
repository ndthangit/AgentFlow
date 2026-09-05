# Agent mẫu cho AgentFlow

Một agent Python nhỏ dùng **Deep Agents trên LangGraph** để đề xuất kế hoạch workflow từ `task/context`. Agent gọi tool `get_node_catalog`, có planning tool `write_todos`, scratchpad trong `StateBackend` và trả `WorkflowPlan` có schema. Agent chưa thực thi các node trong kế hoạch.

Mục đích là thử đường tích hợp **flow → agent → output JSON** trước khi xây adapter Codex/OpenCode. Mẫu chạy độc lập; engine và giao diện AgentFlow vẫn chưa triển khai.

## Chạy nhanh không cần API key

Yêu cầu Python 3.13 và uv. Từ thư mục gốc repository:

```powershell
cd src/agent
uv sync --locked
uv run agent --demo --task "Tạo flow nhận dữ liệu, chuẩn hóa và trả kết quả"
```

`--demo` dùng **model giả lập với kế hoạch cố định**, không suy luận theo task và không gọi provider. Model này vẫn chạy qua graph Deep Agents thật, gọi tool danh mục node và đi qua bộ kiểm tra structured output. Response luôn có `mode: "demo"` để phân biệt.

## Chạy LLM self-host qua OpenAI-compatible API

Mặc định agent dùng `ChatOpenAI` với `base_url` tùy chỉnh để gọi server triển khai OpenAI Chat Completions API. Có thể dùng vLLM, llama.cpp server, LocalAI, LM Studio hoặc endpoint tương thích của Ollama. Model phải hỗ trợ **tool calling** ổn định; Deep Agents cần gọi `get_node_catalog` và tool tạo structured output.

```powershell
Copy-Item .env.example .env
# Điền AGENT_MODEL và OPENAI_COMPATIBLE_BASE_URL trong .env.
uv run --env-file .env agent --task "Đề xuất flow gọi API tồn kho, chuẩn hóa dữ liệu và chờ người duyệt"
```

Ví dụ cấu hình:

```dotenv
AGENT_PROVIDER=openai-compatible
AGENT_MODEL=qwen3-coder
OPENAI_COMPATIBLE_BASE_URL=http://127.0.0.1:8000/v1
OPENAI_COMPATIBLE_API_KEY=
```

Base URL trỏ tới gốc API và thường kết thúc bằng `/v1`; không thêm `/chat/completions`. Khi server không yêu cầu xác thực, để key trống; client dùng placeholder `not-required`. Nếu server có xác thực, đặt key thật trong `.env`, không commit file này.

`ChatOpenAI` chỉ dùng phần chuẩn của OpenAI API. Các field riêng như reasoning metadata có thể không được giữ lại. Một server chỉ hỗ trợ text completion mà không hỗ trợ OpenAI tool calls sẽ không chạy được agent này. Nguồn: [LangChain OpenAI-compatible endpoints](https://docs.langchain.com/oss/python/concepts/providers-and-models#openai-compatible-endpoints).

Anthropic vẫn là tùy chọn: đặt `AGENT_PROVIDER=anthropic`, `AGENT_MODEL` và `ANTHROPIC_API_KEY`. Không tự đọc `.env` trong code; `uv --env-file` nạp file rõ ràng. File `.env` đã được gitignore.

## Input/output

CLI nhận `--task` hoặc một JSON object qua stdin:

```json
{
  "task": "Đề xuất flow xử lý đơn hàng",
  "context": "Chỉ xử lý nội bộ; phải duyệt trước khi gửi dữ liệu ra ngoài."
}
```

Output có dạng:

```json
{
  "run_id": "id-do-runtime-tao",
  "status": "succeeded",
  "mode": "live",
  "output": {
    "summary": "Kế hoạch xử lý đơn hàng",
    "steps": [
      {
        "node_type": "trigger.manual",
        "label": "Nhận đơn hàng",
        "instructions": "Nhận dữ liệu đơn hàng từ người dùng."
      }
    ],
    "notes": []
  }
}
```

Ví dụ output chỉ minh họa schema. `output` là đề xuất tuần tự, **không phải DSL thực thi** trong `docs/workflow-spec.md`. Worker tích hợp lấy `output` làm kết quả agent node; không tự chạy các bước do model đề xuất.

CLI ghi một JSON object vào stdout; lỗi chẩn đoán ghi stderr. Exit code: `0` thành công, `1` lỗi runtime/cấu hình, `2` input hoặc tham số sai. JSON lỗi runtime có `error.code` và `error.message`; lỗi argparse dùng thông báo CLI tiêu chuẩn.

## HTTP để flow gọi

Chạy server demo từ `src/agent`:

```powershell
$env:AGENT_DEMO = "true"
uv run uvicorn agent.api:app --host 127.0.0.1 --port 8001
```

Chạy server với LLM self-host trong terminal khác, hoặc sau khi dừng demo:

```powershell
$env:AGENT_DEMO = "false"
uv run --env-file .env uvicorn agent.api:app --host 127.0.0.1 --port 8001
```

Gọi từ PowerShell:

```powershell
$body = @{ task = "Plan an order processing workflow"; context = "Require approval" } | ConvertTo-Json
Invoke-RestMethod -Uri http://127.0.0.1:8001/v1/runs -Method Post -ContentType 'application/json' -Body $body
```

- `GET /health`: liveness và mode; không xác minh credential/provider readiness.
- `POST /v1/runs`: chờ kết quả trong cùng request, trả **200** khi xong; không trả 202 hoặc lưu background job.
- `GET /docs`: OpenAPI UI do FastAPI cung cấp.
- Input sai trả 422; thiếu cấu hình trả 503; deadline 504; lỗi output/provider/step limit trả 502.

Client Node.js mẫu: [examples/invoke-agent.mjs](examples/invoke-agent.mjs). Khi server demo đang chạy:

```powershell
node examples/invoke-agent.mjs
```

Worker có thể gọi HTTP hoặc spawn CLI với argv và stdin JSON. Không ghép prompt thành shell command. Đặt timeout client lớn hơn deadline runtime 120 giây; đừng tự retry vô hạn vì mỗi request là lần gọi model mới.

## Gọi trực tiếp trong Python

```python
import asyncio
from agent.contracts import RunRequest
from agent.runtime import run_agent

result = asyncio.run(run_agent(RunRequest(task="Plan a small workflow"), demo=True))
print(result.output.model_dump())
```

## Phạm vi bản mẫu

- Mỗi request có graph state mới, không lưu session/checkpoint và không đọc file trên host. Scratchpad chỉ nằm trong state của lần chạy.
- Một agent; general-purpose subagent được tắt qua harness profile. Cấu hình profile là process-global, nên chạy module này như runtime riêng.
- Không có shell, web search, credential của connector hoặc hành động bên ngoài; chỉ có lời gọi model khi chạy live.
- Giới hạn task/context, output tối đa 12 bước, graph tối đa 40 supersteps và deadline 120 giây. Đây không phải hard billing cap; request provider đã gửi có thể vẫn bị tính phí sau timeout.
- Chưa có streaming, resume, cancel endpoint, idempotency ledger, auth, queue hoặc persistence. Client ngắt HTTP không đồng nghĩa với hủy tác vụ; deadline runtime vẫn áp dụng. Server mẫu dành cho localhost.
- `run_id` dùng để tương quan kết quả trả về, chưa dùng truy vấn job. Khi tích hợp engine thật, worker chịu trách nhiệm gắn kết quả vào node execution và quản lý retry.

## Kiểm thử

```powershell
uv run python -m unittest discover -s tests -v
uv run ruff check src tests
uv run ruff format --check src tests
```

Test dùng graph thật với model giả lập: tool call, output schema, dữ liệu riêng từng request, CLI, HTTP, lỗi cấu hình, deadline/cancel và che thông tin lỗi provider. Chưa kiểm chứng chất lượng kế hoạch hoặc kết nối model bằng API key thật trong phiên triển khai này.

Phiên bản đã khóa trong [uv.lock](uv.lock): Deep Agents 0.7.13, LangGraph 1.2.11. Nguồn API: [Deep Agents quickstart](https://docs.langchain.com/oss/python/deepagents/quickstart), [customization và structured output](https://docs.langchain.com/oss/python/deepagents/customization), [tắt subagents](https://docs.langchain.com/oss/python/deepagents/subagents#running-without-subagents).
