# Agent mẫu cho AgentFlow

Một agent Python nhỏ dùng **Deep Agents trên LangGraph** để đề xuất kế hoạch workflow từ `task/context`. Agent gọi tool `get_node_catalog`, có planning tool `write_todos`, scratchpad trong `StateBackend` và trả `WorkflowPlan` có schema. Agent chưa thực thi các node trong kế hoạch.

Đây là implementation Agent chính cho đường tích hợp **flow → agent → output JSON** hiện tại.

Workflow worker chạy CLI này trong **một container mới cho mỗi attempt của agent node**, thu kết quả rồi xóa container. Chọn runtime `agent` trong editor hoặc đặt `config.runtime` thành `agent`; Compose build image `agentflow-agent-runtime`. Đây là runtime mặc định của Agent node và luôn gọi model thật đã chọn trong editor. Runtime không có fake model. FastAPI bên dưới chỉ phục vụ phát triển/kiểm thử contract, không phải runtime dùng chung cho workflow. Xem [thiết kế ephemeral agent runtime](../../docs/ephemeral-agent-runtime.md).

Có thể kiểm tra trực tiếp contract container bằng request mẫu [examples/container-request.json](examples/container-request.json):

```powershell
Get-Content -Raw examples/container-request.json | docker run --rm -i --read-only --cap-drop ALL --security-opt no-new-privileges --env AGENT_PROVIDER --env AGENT_MODEL --env OPENAI_COMPATIBLE_BASE_URL --env OPENAI_COMPATIBLE_API_KEY --env OPENROUTER_BASE_URL --env OPENROUTER_API_KEY --entrypoint agent agentflow-agent-runtime:0.1.0
```

## Chạy nhanh với LLM thật

Yêu cầu Python 3.13 và uv. Từ thư mục gốc repository:

```powershell
cd src/agent
uv sync --locked
uv run --env-file .env agent --task "Tạo flow nhận dữ liệu, chuẩn hóa và trả kết quả"
```

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

OpenRouter có thể dùng trực tiếp mà không cần đổi tên biến:

```dotenv
AGENT_PROVIDER=openrouter
AGENT_MODEL=nvidia/nemotron-3-super-120b-a12b:free
OPENROUTER_API_KEY=your-key
```

Khi chạy từ workflow, nếu Agent node không chọn riêng provider/model, worker tự lấy provider có `default_model` của người dùng và truyền model, base URL cùng credential đã giải mã vào container.

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

Chạy server với LLM thật từ `src/agent`:

```powershell
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

Khi `AUTH_ENABLED=true`, `POST /v1/runs` yêu cầu JWT Bearer token. Agent xác minh chữ ký RS256 bằng JWKS, đồng thời kiểm tra issuer, audience, thời hạn và các claim bắt buộc. Docker Compose đã cấu hình chế độ này với Keycloak; xem [hướng dẫn hạ tầng](../../infra/README.md). `GET /health` vẫn public cho liveness probe.

Client Node.js mẫu: [examples/invoke-agent.mjs](examples/invoke-agent.mjs). Khi server đang chạy:

```powershell
node examples/invoke-agent.mjs
```

Worker có thể gọi HTTP hoặc spawn CLI với argv và stdin JSON. Không ghép prompt thành shell command. Đặt timeout client lớn hơn deadline runtime 120 giây; đừng tự retry vô hạn vì mỗi request là lần gọi model mới.

## Gọi trực tiếp trong Python

```python
import asyncio
from agent.contracts import RunRequest
from agent.runtime import run_agent

result = asyncio.run(run_agent(RunRequest(task="Plan a small workflow")))
print(result.output)
```

## Phạm vi bản mẫu

- HTTP service dùng một `AgentRuntime` và một model dùng chung. Mỗi request tạo graph state riêng, nhận bộ skill riêng và không đọc file trên host; scratchpad và skill không truyền sang session khác.
- Chỉ có một agent; general-purpose subagent được tắt qua harness profile. Cấu hình profile là process-global, nên chạy module này như runtime riêng.
- Không có shell, web search, credential của connector hoặc hành động bên ngoài; mọi lần chạy đều gọi model thật.
- Giới hạn task/context, output tối đa 12 bước, graph tối đa 40 supersteps và deadline 120 giây. Đây không phải hard billing cap; request provider đã gửi có thể vẫn bị tính phí sau timeout.
- Agent là integration độc lập và không sở hữu database. FastAPI control plane, PostgreSQL và migration nằm trong [`../system`](../system/README.md). Agent chưa có streaming, resume hoặc cancel endpoint; deadline runtime vẫn áp dụng.
- `run_id` dùng để tương quan kết quả trả về, chưa dùng truy vấn job. Khi tích hợp engine thật, worker chịu trách nhiệm gắn kết quả vào node execution và quản lý retry.

## Kiểm thử

```powershell
uv run python -m unittest discover -s tests -v
uv run ruff check src tests
uv run ruff format --check src tests
```

Unit test mock biên model/network để kiểm tra output schema, dữ liệu riêng từng request, CLI, HTTP, lỗi cấu hình, deadline/cancel và che thông tin lỗi provider. Runtime ứng dụng không chứa hoặc cung cấp fake model.

Phiên bản đã khóa trong [uv.lock](uv.lock): Deep Agents 0.7.13, LangGraph 1.2.11. Nguồn API: [Deep Agents quickstart](https://docs.langchain.com/oss/python/deepagents/quickstart), [customization và structured output](https://docs.langchain.com/oss/python/deepagents/customization), [tắt subagents](https://docs.langchain.com/oss/python/deepagents/subagents#running-without-subagents).
