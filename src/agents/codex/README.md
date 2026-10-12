# Codex runtime

Runtime one-shot dùng OpenAI Codex CLI và tuân theo contract JSON stdin/stdout của
AgentFlow. Image đã pin `@openai/codex` để mỗi lần deploy có thể tái lập.
Image cài system CA bundle vì Codex native binary cần trust store của hệ điều hành
để kết nối HTTPS tới model provider.

## Credential và model

Không cấu hình API key trong Dockerfile, Compose hoặc `.env`. Khi workflow chạy,
System lấy provider/model của Agent node từ trang **Models**, giải mã key trong
worker rồi chỉ truyền vào container dùng một lần:

- `AGENT_MODEL`
- `AGENT_PROVIDER_BASE_URL`
- `AGENT_PROVIDER_API_KEY`

Adapter cấu hình một custom Codex model provider dùng Responses API. Phiên Codex
chạy `--ephemeral`, bỏ qua user config, dùng sandbox `read-only`, không xin quyền
và trả kết quả theo `output_schema` của node.

MCP được chọn cho node được ánh xạ từ `AGENT_MCP_SERVERS` sang cấu hình Codex
`mcp_servers.<slug>`. Runtime hỗ trợ Streamable HTTP; SSE bị từ chối vì Codex CLI
không hỗ trợ transport này. Header xác thực được đặt trong biến môi trường tạm và
Codex chỉ nhận ánh xạ `env_http_headers`, nên giá trị secret không xuất hiện trong argv.

Model được chọn phải hỗ trợ Responses API và tool semantics mà Codex CLI sử dụng.
Với OpenRouter, nên chọn model Codex/GPT tương thích Responses; adapter sẽ trả lỗi
public nếu provider từ chối model thay vì fallback sang model khác.

## Build và kiểm tra

```bash
docker build -t agentflow-agent-codex:0.1.0 src/agents/codex
docker run --rm --entrypoint codex agentflow-agent-codex:0.1.0 --version
cd src/agents/codex && npm test
```

Khi chạy qua AgentFlow, chọn runtime `codex` trong Agent node. Nếu dùng MCP, hãy
chọn server có transport Streamable HTTP trong cùng cấu hình node.
