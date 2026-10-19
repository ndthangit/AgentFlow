# Tích hợp OpenHands vào AgentFlow

Ngày kiểm chứng: **19/10/2026**. Runtime triển khai dùng `openhands-sdk==1.53.0`.

## Bề mặt tích hợp

AgentFlow dùng OpenHands Software Agent SDK trực tiếp trong image `agentflow-agent-openhands`, không chạy OpenHands CLI headless hoặc GUI server. SDK cung cấp `Agent`, `Conversation`, MCP và structured response qua `FinishTool`; đây là bề mặt phù hợp với contract one-shot JSON stdin/stdout của AgentFlow.

Nguồn chính thức:

- [OpenHands Software Agent SDK](https://github.com/OpenHands/software-agent-sdk)
- [Structured Output](https://github.com/OpenHands/docs/blob/main/sdk/guides/structured-output.mdx)
- [MCP guide](https://github.com/OpenHands/docs/blob/main/sdk/guides/mcp.mdx)
- [OpenHands CLI command reference](https://github.com/OpenHands/docs/blob/main/openhands/usage/cli/command-reference.mdx)

Không dùng CLI headless vì đó là bề mặt dành cho automation tổng quát và tự chạy theo approval policy của CLI. Adapter SDK cho phép AgentFlow chỉ cấp capability cần thiết cho đúng node.

## Contract và capability

Runtime đọc `task`, `context`, `skills`, `output_schema` từ stdin. Worker inject tạm thời `AGENT_PROVIDER`, `AGENT_MODEL`, `AGENT_PROVIDER_BASE_URL`, `AGENT_PROVIDER_API_KEY` và `AGENT_MCP_SERVERS`; runtime không đọc credential tĩnh trong `.env`.

Agent chỉ có:

- `FinishTool`, với schema `{ "result": <output_schema> }` để tránh xung đột các tên reserved của OpenHands;
- remote MCP Streamable HTTP đã được người dùng chọn cho node.

Không cấp TerminalTool, FileEditorTool, browser hoặc workspace source. Kết quả lấy từ lần gọi `finish`, bỏ lớp `result`, sau đó được validate lại bằng JSON Schema trước khi trả `RunResult`.

## Giới hạn thực thi

- `AGENT_RUNTIME_MAX_STEPS` mặc định 8, chỉ chấp nhận 1–32.
- Deadline nội bộ kết thúc trước `AGENT_RUNTIME_TIMEOUT_SECONDS` 20 giây để runtime còn thời gian cleanup và trả lỗi công khai.
- Mỗi provider request timeout tối đa 60 giây và chỉ retry một lần.
- Conversation, temporary workspace và credential bị hủy cùng disposable container.
- Lát cắt hiện tại chỉ ánh xạ OpenRouter; model được chuyển thành định danh LiteLLM `openrouter/<model-id>` và phải hỗ trợ tool calling.

## Build và vận hành

Compose build/pull service `agent-runtime-openhands-image`, đăng ký runtime id `openhands` trong `AGENT_RUNTIME_IMAGES`, rồi workflow worker resolve image từ allow-list. UI hiển thị OpenHands trong datalist runtime của Agent node.

```powershell
docker compose --env-file docker/.env -f docker/compose.yaml build agent-runtime-openhands-image
docker compose --env-file docker/.env -f docker/compose.yaml up -d --no-deps --force-recreate workflow-worker
```

Không restart worker khi còn run `pending` hoặc `running`; image mới chỉ có hiệu lực sau khi worker được recreate an toàn.
