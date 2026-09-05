"""A scripted chat model for offline demos, not a real language model.

It still runs through the real Deep Agents graph, tool execution and structured
output machinery. Decisions are fixed so tests do not need network or API keys.
"""

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class DemoModel(BaseChatModel):
    model_name: str = "demo"

    @property
    def _llm_type(self) -> str:
        return "agentflow-demo"

    def _get_ls_params(self, stop=None, **kwargs):
        return {"ls_provider": "agentflow-demo", "ls_model_name": self.model_name}

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(
        self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any
    ) -> ChatResult:
        catalog_read = any(
            isinstance(message, ToolMessage) and message.name == "get_node_catalog"
            for message in messages
        )
        if not catalog_read:
            call = {"name": "get_node_catalog", "args": {}, "id": "demo-catalog"}
        else:
            call = {
                "name": "WorkflowPlan",
                "id": "demo-plan",
                "args": {
                    "summary": "Demo cố định: nhận input, xử lý dữ liệu và trả kết quả.",
                    "steps": [
                        {
                            "node_type": node_type,
                            "label": label,
                            "instructions": instructions,
                        }
                        for node_type, label, instructions in [
                            (
                                "trigger.manual",
                                "Nhận input",
                                "Nhận dữ liệu từ người dùng.",
                            ),
                            (
                                "transform",
                                "Xử lý dữ liệu",
                                "Chuẩn hóa dữ liệu đầu vào.",
                            ),
                            ("end", "Trả kết quả", "Trả dữ liệu cho node tiếp theo."),
                        ]
                    ],
                    "notes": [
                        "Chế độ demo dùng model giả lập, không suy luận theo task."
                    ],
                },
            }
        return ChatResult(
            generations=[
                ChatGeneration(message=AIMessage(content="", tool_calls=[call]))
            ]
        )
