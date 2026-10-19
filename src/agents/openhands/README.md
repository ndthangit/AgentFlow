# OpenHands runtime

Runtime này dùng `openhands-sdk==1.53.0` và ánh xạ OpenHands Software Agent SDK vào contract container one-shot của AgentFlow.

## Phạm vi

- Nhận một request JSON qua `stdin` và chỉ trả một result/error JSON qua `stdout`.
- Dùng provider, model và API key do workflow worker inject cho đúng node; hiện hỗ trợ provider OpenRouter.
- Dùng `FinishTool` với response schema bọc để trả output có cấu trúc và validate lại bằng JSON Schema.
- Chỉ cấp `FinishTool` và MCP Streamable HTTP đã được chọn. Runtime không cấp terminal, browser hoặc file editor tích hợp sẵn.
- Giới hạn số vòng agent bằng `AGENT_RUNTIME_MAX_STEPS` (mặc định 8, tối đa 32) và kết thúc sớm hơn timeout container để còn thời gian cleanup.
- Không giữ conversation, workspace hoặc credential sau invocation.

## Build và test

```powershell
docker build -t agentflow-agent-openhands:0.1.0 src/agents/openhands
docker run --rm --network none --entrypoint python agentflow-agent-openhands:0.1.0 -m unittest discover -s /opt/agentflow-openhands/test
```

Khi chạy qua AgentFlow, chọn runtime `openhands` trong Agent node và chọn provider/model từ Models. Model phải hỗ trợ tool calling vì structured result được trả qua `FinishTool`.
