# OpenCode runtime

Runtime one-shot dùng OpenCode SDK/server cục bộ và tuân theo contract JSON
stdin/stdout của AgentFlow. Image khóa cùng phiên bản cho `opencode-ai` và
`@opencode-ai/sdk` để client/server không lệch giao thức.

## Giới hạn và bảo mật

- Server chỉ bind `127.0.0.1` bên trong container dùng một lần.
- Chỉ provider OpenRouter hiện có trong trang **Models** được hỗ trợ. Model, base
  URL và API key được worker inject bằng `AGENT_PROVIDER_*` cho đúng invocation.
- Config chỉ tham chiếu key qua `{env:AGENT_PROVIDER_API_KEY}`; secret không nằm
  trong prompt, argv, config được log hoặc result.
- Mọi tool tích hợp sẵn bị deny. Chỉ tool thuộc các MCP đã chọn cho node được allow;
  runtime v1 vẫn không có workspace, session resume hay interactive approval.
- MCP Streamable HTTP được ánh xạ sang remote MCP của OpenCode. Header bí mật dùng
  tham chiếu environment, OAuth tự động bị tắt, và run dừng với
  `MCP_CONNECTION_FAILED` nếu một server đã chọn không kết nối được. SSE chưa được hỗ trợ.
- Event lỗi/retry của provider được chuyển thành mã lỗi công khai sau tối đa hai lần
  retry. Adapter cũng tự abort trước deadline của supervisor để OpenCode không biến lỗi
  429/5xx thành container timeout do retry vô hạn.
- Structured output do OpenCode tạo được adapter validate lại bằng JSON Schema.
- Mỗi `session.prompt()` tạo một OpenTelemetry `generation` span con của
  `agent.container.run`. Span luôn ghi success/error/timeout và runtime force-flush
  OTLP trước khi container dùng một lần kết thúc.
- Runtime chỉ phát structured diagnostic không chứa prompt hay credential. Worker
  ghi lại các event này vào JSON log với `run_id`, `node_id`, model và elapsed time,
  vì vậy có thể truy vấn theo run trong Loki kể cả khi container đã bị `--rm`.

## Build và kiểm tra

```bash
cd src/agents/opencode && npm test
docker build -t agentflow-agent-opencode:0.1.0 src/agents/opencode
docker run --rm --entrypoint opencode agentflow-agent-opencode:0.1.0 --version
```

Khi chạy qua AgentFlow, chọn runtime `opencode` trong Agent node và chọn một model
OpenRouter có khả năng tool/structured output phù hợp.
