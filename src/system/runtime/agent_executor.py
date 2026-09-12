"""LLM-backed Agent node execution shared by API-independent workers."""

import json
from collections.abc import Iterable
from typing import Any

from domain.errors import WorkflowExecutionError
from domain.models import LlmProvider
from providers.adapters import (
    ChatCompletionRequest,
    ChatMessage,
    create_provider_adapter,
)
from providers.secrets import ProviderSecretStore
from runtime.engine import AgentExecutor


def parse_agent_output(content: str) -> dict[str, Any]:
    candidate = content.strip()
    if candidate.startswith("```") and candidate.endswith("```"):
        lines = candidate.splitlines()
        candidate = "\n".join(lines[1:-1]).strip()
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        if start < 0:
            raise WorkflowExecutionError("Agent did not return a JSON object") from None
        try:
            value, _ = json.JSONDecoder().raw_decode(candidate[start:])
        except json.JSONDecodeError as exc:
            raise WorkflowExecutionError("Agent returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise WorkflowExecutionError("Agent must return a JSON object")
    return value


def create_agent_executor(
    graph: dict[str, Any],
    providers: Iterable[LlmProvider],
    secret_store: ProviderSecretStore,
) -> AgentExecutor:
    available = list(providers)

    async def execute_agent(
        node: dict[str, Any], node_input: dict[str, Any]
    ) -> dict[str, Any]:
        config = node.get("config", {})
        requested_provider_id = config.get("providerId")
        if requested_provider_id:
            provider = next(
                (
                    item
                    for item in available
                    if str(item.id) == str(requested_provider_id)
                ),
                None,
            )
            if provider is None:
                raise WorkflowExecutionError(
                    "The Agent node's LLM provider is missing or disabled"
                )
        else:
            provider = next(
                (item for item in available if item.settings.get("default_model")),
                available[0] if available else None,
            )
        if provider is None:
            raise WorkflowExecutionError(
                "No enabled LLM provider is configured for this workflow owner"
            )
        model = config.get("model") or provider.settings.get("default_model")
        if not isinstance(model, str) or not model.strip():
            raise WorkflowExecutionError(
                f"Provider {provider.name} has no default model; select one in Models"
            )
        output_schema = config.get("outputSchema", {})
        skill_instructions = [
            skill.get("instructions", "")
            for skill in graph.get("skills", [])
            if isinstance(skill, dict) and skill.get("instructions")
        ]
        system_parts = [
            config.get("instructions", "") or "Complete the requested transformation.",
            *skill_instructions,
            "Return only one valid JSON object. Do not use Markdown fences.",
            f"The JSON must match this schema: {json.dumps(output_schema, ensure_ascii=False)}",
        ]
        adapter = create_provider_adapter(
            provider.kind,
            provider.settings,
            secret_store.decrypt(provider.api_key_encrypted),
        )
        result = await adapter.complete(
            ChatCompletionRequest(
                model=model.strip(),
                temperature=0,
                messages=[
                    ChatMessage(role="system", content="\n\n".join(system_parts)),
                    ChatMessage(
                        role="user",
                        content=json.dumps(node_input, ensure_ascii=False),
                    ),
                ],
            )
        )
        return parse_agent_output(result.message.content)

    return execute_agent
