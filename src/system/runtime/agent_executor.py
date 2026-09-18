"""LLM-backed Agent node execution shared by API-independent workers."""

import json
import re
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
from runtime.engine import AgentExecutor, LlmExecutor

PROMPT_INPUT_REFERENCE = re.compile(
    r"\{\{\s*input((?:\.[A-Za-z0-9_-]+)*)\s*\}\}"
)


def render_prompt(template: str, node_input: dict[str, Any]) -> str:
    """Resolve {{input.path}} references against this node's resolved input."""

    def replace(match: re.Match[str]) -> str:
        value: Any = node_input
        path = match.group(1)
        for field in path.removeprefix(".").split(".") if path else []:
            if isinstance(value, dict) and field in value:
                value = value[field]
            elif isinstance(value, list) and field.isdigit() and int(field) < len(value):
                value = value[int(field)]
            else:
                raise WorkflowExecutionError(
                    f"Prompt input reference is not available: {match.group(0)}"
                )
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False)

    rendered = PROMPT_INPUT_REFERENCE.sub(replace, template)
    if "{{input" in rendered or "{{ input" in rendered:
        raise WorkflowExecutionError(
            "Invalid prompt input reference; use {{input}} or {{input.field}}"
        )
    return rendered


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


def skill_instructions_for_node(
    graph: dict[str, Any], config: dict[str, Any]
) -> list[str]:
    """Select this Agent's skills; old nodes without skillIds inherit all skills."""
    configured_skill_ids = config.get("skillIds")
    assigned_skill_ids = (
        {skill_id for skill_id in configured_skill_ids if isinstance(skill_id, str)}
        if isinstance(configured_skill_ids, list)
        else None
    )
    return [
        skill["instructions"]
        for skill in graph.get("skills", [])
        if isinstance(skill, dict)
        and isinstance(skill.get("instructions"), str)
        and skill["instructions"]
        and (assigned_skill_ids is None or str(skill.get("id")) in assigned_skill_ids)
    ]


def _create_completion_executor(
    graph: dict[str, Any],
    providers: Iterable[LlmProvider],
    secret_store: ProviderSecretStore,
    *,
    node_label: str,
    prompt_field: str,
    include_skills: bool,
) -> AgentExecutor:
    available = list(providers)

    async def execute_completion(
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
                    f"The {node_label} node's LLM provider is missing or disabled"
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
        selected_models = provider.settings.get("selected_models") or []
        if selected_models and model not in selected_models:
            raise WorkflowExecutionError(
                f"Model {model} is not enabled for provider {provider.name}"
            )
        output_schema = config.get("outputSchema", {})
        skill_instructions = (
            skill_instructions_for_node(graph, config) if include_skills else []
        )
        configured_prompt = config.get(prompt_field)
        prompt_template = (
            configured_prompt
            if isinstance(configured_prompt, str) and configured_prompt.strip()
            else "Complete the requested transformation."
        )
        system_parts = [
            render_prompt(prompt_template, node_input),
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

    return execute_completion


def create_agent_executor(
    graph: dict[str, Any],
    providers: Iterable[LlmProvider],
    secret_store: ProviderSecretStore,
) -> AgentExecutor:
    return _create_completion_executor(
        graph,
        providers,
        secret_store,
        node_label="Agent",
        prompt_field="instructions",
        include_skills=True,
    )


def create_llm_executor(
    graph: dict[str, Any],
    providers: Iterable[LlmProvider],
    secret_store: ProviderSecretStore,
) -> LlmExecutor:
    """Create a direct, single-completion executor without Agent skills/tools."""
    return _create_completion_executor(
        graph,
        providers,
        secret_store,
        node_label="LLM Call",
        prompt_field="prompt",
        include_skills=False,
    )
