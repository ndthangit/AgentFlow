"""LLM-backed Agent node execution shared by API-independent workers."""

import json
import re
from collections.abc import Iterable
from typing import Any

from domain.errors import WorkflowExecutionError
from domain.models import LlmProvider, McpServer
from providers.adapters import (
    OPENROUTER_BASE_URL,
    ChatCompletionRequest,
    ChatMessage,
    create_provider_adapter,
)
from providers.secrets import ProviderSecretStore
from runtime.agent_container import AgentContainerExecutor
from runtime.engine import AgentExecutor, LlmExecutor

PROMPT_INPUT_REFERENCE = re.compile(r"\{\{\s*input((?:\.[A-Za-z0-9_-]+)*)\s*\}\}")


def render_prompt(template: str, node_input: dict[str, Any]) -> str:
    """Resolve {{input.path}} references against this node's resolved input."""

    def replace(match: re.Match[str]) -> str:
        value: Any = node_input
        path = match.group(1)
        for field in path.removeprefix(".").split(".") if path else []:
            if isinstance(value, dict) and field in value:
                value = value[field]
            elif (
                isinstance(value, list) and field.isdigit() and int(field) < len(value)
            ):
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


def skills_for_node(
    graph: dict[str, Any], config: dict[str, Any]
) -> list[dict[str, Any]]:
    """Select this Agent's skills; old nodes without skillIds inherit all skills."""
    configured_skill_ids = config.get("skillIds")
    assigned_skill_ids = (
        {skill_id for skill_id in configured_skill_ids if isinstance(skill_id, str)}
        if isinstance(configured_skill_ids, list)
        else None
    )
    return [
        skill
        for skill in graph.get("skills", [])
        if isinstance(skill, dict)
        and (assigned_skill_ids is None or str(skill.get("id")) in assigned_skill_ids)
    ]


def skill_instructions_for_node(
    graph: dict[str, Any], config: dict[str, Any]
) -> list[str]:
    return [
        skill["instructions"]
        for skill in skills_for_node(graph, config)
        if isinstance(skill.get("instructions"), str) and skill["instructions"]
    ]


def mcp_servers_for_node(
    graph: dict[str, Any], config: dict[str, Any]
) -> list[dict[str, Any]]:
    configured_ids = config.get("mcpServerIds")
    if not isinstance(configured_ids, list):
        return []
    selected_ids = {
        server_id for server_id in configured_ids if isinstance(server_id, str)
    }
    return [
        server
        for server in graph.get("mcpServers", [])
        if isinstance(server, dict) and str(server.get("id")) in selected_ids
    ]


def _mcp_container_environment(
    graph: dict[str, Any],
    config: dict[str, Any],
    servers: Iterable[McpServer],
    secret_store: ProviderSecretStore,
) -> dict[str, str]:
    requested_ids = config.get("mcpServerIds")
    if not isinstance(requested_ids, list) or not requested_ids:
        return {}
    snapshots = {
        str(server.get("id")): server for server in mcp_servers_for_node(graph, config)
    }
    live_servers = {str(server.id): server for server in servers}
    connections: dict[str, dict[str, Any]] = {}
    for server_id in requested_ids:
        snapshot = snapshots.get(server_id)
        live = live_servers.get(server_id)
        if snapshot is None or live is None:
            raise WorkflowExecutionError(
                f"MCP server {server_id} is missing or disabled"
            )
        connection: dict[str, Any] = {
            "transport": snapshot.get("transport"),
            "url": snapshot.get("url"),
        }
        if live.headers_encrypted:
            try:
                headers = json.loads(secret_store.decrypt(live.headers_encrypted))
            except (TypeError, ValueError) as exc:
                raise WorkflowExecutionError(
                    f"MCP server {live.name} has invalid encrypted headers"
                ) from exc
            if not isinstance(headers, dict) or any(
                not isinstance(name, str) or not isinstance(value, str)
                for name, value in headers.items()
            ):
                raise WorkflowExecutionError(
                    f"MCP server {live.name} has invalid headers"
                )
            connection["headers"] = headers
        connections[live.slug] = connection
    return {
        "AGENT_MCP_SERVERS": json.dumps(
            connections, ensure_ascii=True, separators=(",", ":")
        )
    }


def _agent_container_environment(
    config: dict[str, Any],
    providers: Iterable[LlmProvider],
    secret_store: ProviderSecretStore,
) -> dict[str, str]:
    """Resolve the node model, falling back to the owner's default provider."""
    available = list(providers)
    provider_id = config.get("providerId")
    requested_model = config.get("model")
    if provider_id:
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise WorkflowExecutionError("The Agent node must select an LLM provider")
        provider = next(
            (item for item in available if str(item.id) == provider_id),
            None,
        )
    elif isinstance(requested_model, str) and requested_model.strip():
        provider = next(
            (
                item
                for item in available
                if requested_model in (item.settings.get("selected_models") or [])
            ),
            None,
        )
    else:
        provider = next(
            (item for item in available if item.settings.get("default_model")),
            available[0] if available else None,
        )
    if provider is None:
        raise WorkflowExecutionError(
            "The Agent node's LLM provider is missing or disabled"
        )
    model = requested_model or provider.settings.get("default_model")
    if not isinstance(model, str) or not model.strip():
        raise WorkflowExecutionError(
            f"Provider {provider.name} has no default model; select one in Models"
        )
    selected_models = provider.settings.get("selected_models") or []
    if selected_models and model not in selected_models:
        raise WorkflowExecutionError(
            f"Model {model} is not enabled for provider {provider.name}"
        )
    if provider.kind != "openrouter":
        raise WorkflowExecutionError(
            f"Provider {provider.name} is not supported by the Agent container"
        )
    return {
        "AGENT_PROVIDER": "openrouter",
        "AGENT_MODEL": model.strip(),
        "OPENROUTER_BASE_URL": OPENROUTER_BASE_URL,
        "OPENROUTER_API_KEY": secret_store.decrypt(provider.api_key_encrypted),
    }


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
    mcp_servers: Iterable[McpServer] = (),
    *,
    container_executor: AgentContainerExecutor | None = None,
) -> AgentExecutor:
    available = list(providers)
    available_mcp_servers = list(mcp_servers)
    direct_executor = _create_completion_executor(
        graph,
        available,
        secret_store,
        node_label="Agent",
        prompt_field="instructions",
        include_skills=True,
    )
    bundled_agent_executor = container_executor or AgentContainerExecutor()

    async def execute_agent(
        node: dict[str, Any], node_input: dict[str, Any]
    ) -> dict[str, Any]:
        config = node.get("config", {})
        runtime = config.get("runtime", "agent")
        if runtime == "direct":
            if config.get("mcpServerIds"):
                raise WorkflowExecutionError(
                    "MCP servers require the Agent container runtime"
                )
            return await direct_executor(node, node_input)
        if runtime != "agent":
            raise WorkflowExecutionError(f"Unsupported agent runtime: {runtime}")

        configured_instructions = config.get("instructions")
        prompt_template = (
            configured_instructions
            if isinstance(configured_instructions, str)
            and configured_instructions.strip()
            else "Complete the requested task."
        )
        rendered_instructions = render_prompt(prompt_template, node_input)
        environment_overrides = _agent_container_environment(
            config, available, secret_store
        )
        environment_overrides.update(
            _mcp_container_environment(
                graph, config, available_mcp_servers, secret_store
            )
        )
        skill_snapshots = []
        for skill in skills_for_node(graph, config):
            skill_snapshots.append(
                {
                    field: skill.get(field)
                    for field in (
                        "id",
                        "slug",
                        "name",
                        "version",
                        "content_hash",
                        "instructions",
                    )
                }
            )
        request = {
            "task": rendered_instructions,
            "context": json.dumps(node_input, ensure_ascii=False),
            "skills": skill_snapshots,
            "output_schema": config.get("outputSchema", {}),
        }
        return await bundled_agent_executor.execute(
            request, environment_overrides=environment_overrides
        )

    return execute_agent


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
