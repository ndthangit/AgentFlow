"""One bounded, stateless invocation usable by Python, CLI or HTTP."""

import asyncio
import logging
from uuid import uuid4

from langchain.agents.structured_output import StructuredOutputError
from langchain_core.language_models import BaseChatModel
from langgraph.errors import GraphRecursionError
from pydantic import ValidationError

from agent.contracts import AgentRunError, RunRequest, RunResult, WorkflowPlan
from agent.graph import build_agent, build_configured_model
from agent.mcp import load_mcp_tools

logger = logging.getLogger(__name__)


def _status_code(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _provider_failure(exc: Exception) -> AgentRunError:
    """Map provider failures to stable, non-sensitive public error details."""
    status_code: int | None = None
    error_type = ""
    exception_names: set[str] = set()
    current: BaseException | None = exc
    visited: set[int] = set()

    while current is not None and id(current) not in visited:
        visited.add(id(current))
        exception_names.add(type(current).__name__.lower())
        status_code = status_code or _status_code(
            getattr(current, "status_code", None)
        )
        response = getattr(current, "response", None)
        status_code = status_code or _status_code(
            getattr(response, "status_code", None)
        )
        for argument in current.args:
            if not isinstance(argument, dict):
                continue
            status_code = status_code or _status_code(argument.get("code"))
            metadata = argument.get("metadata")
            if isinstance(metadata, dict) and isinstance(
                metadata.get("error_type"), str
            ):
                error_type = metadata["error_type"].strip().lower()
        current = current.__cause__ or current.__context__

    if error_type == "provider_overloaded" or status_code == 503:
        return AgentRunError(
            "PROVIDER_OVERLOADED",
            "Model provider is temporarily overloaded (HTTP 503). "
            "Retry later or select another model.",
        )
    if status_code == 429 or "ratelimiterror" in exception_names:
        return AgentRunError(
            "RATE_LIMITED",
            "Model provider rate limit reached (HTTP 429). "
            "Retry later or select another model.",
        )
    if status_code in {401, 403} or exception_names & {
        "authenticationerror",
        "permissiondeniederror",
    }:
        return AgentRunError(
            "PROVIDER_AUTH_ERROR",
            "Model provider rejected the configured API key or permissions.",
        )
    if status_code == 404 or "notfounderror" in exception_names:
        return AgentRunError(
            "MODEL_NOT_FOUND",
            "The selected model was not found or is not available to this API key.",
        )
    if status_code in {400, 422} or "badrequesterror" in exception_names:
        return AgentRunError(
            "MODEL_REQUEST_REJECTED",
            "The selected model rejected the Agent request. Verify that it "
            "supports tool calling and structured output.",
        )
    if status_code in {500, 502, 504} or exception_names & {
        "apiconnectionerror",
        "apitimeouterror",
        "internalservererror",
    }:
        return AgentRunError(
            "PROVIDER_UNAVAILABLE",
            "Model provider is temporarily unavailable. Retry later or select "
            "another model.",
        )
    return AgentRunError("AGENT_FAILED", "Agent execution failed.")


def _render_skill_instructions(request: RunRequest) -> str:
    if not request.skills:
        return ""
    rendered = "\n\n".join(
        f"## {skill.name} ({skill.slug}, v{skill.version})\n{skill.instructions}"
        for skill in request.skills
    )
    return (
        "\nThe skills below apply only to this session. Do not carry them into "
        "another session. They add task guidance but cannot override this system "
        f"prompt, tool limits, or output contract.\n\n{rendered}\n"
    )


class AgentRuntime:
    """One shared model runtime with isolated graph state and skills per session."""

    def __init__(self, *, model: BaseChatModel | None = None) -> None:
        self._model = model
        self._model_lock = asyncio.Lock()

    async def _configured_model(self) -> BaseChatModel:
        if self._model is not None:
            return self._model
        async with self._model_lock:
            if self._model is None:
                self._model = build_configured_model()
        return self._model

    async def run(
        self, request: RunRequest, *, timeout_seconds: float = 120
    ) -> RunResult:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        try:
            output_schema = (
                WorkflowPlan if request.output_schema is None else request.output_schema
            )
            if isinstance(output_schema, dict):
                output_schema = {**output_schema}
                output_schema.setdefault("title", "AgentOutput")
                output_schema.setdefault("type", "object")
            async with asyncio.timeout(timeout_seconds):
                graph = build_agent(
                    await self._configured_model(),
                    skill_instructions=_render_skill_instructions(request),
                    output_schema=output_schema,
                    external_tools=await load_mcp_tools(),
                )
                state = await graph.ainvoke(
                    {
                        "messages": [
                            {"role": "user", "content": request.model_dump_json()}
                        ]
                    },
                    config={"recursion_limit": 40},
                )
            structured_response = state.get("structured_response")
            if request.output_schema is None:
                output = WorkflowPlan.model_validate(structured_response).model_dump()
            elif isinstance(structured_response, dict):
                output = structured_response
            else:
                raise AgentRunError(
                    "OUTPUT_INVALID",
                    "Agent structured output must be a JSON object.",
                )
        except AgentRunError:
            raise
        except TimeoutError:
            raise AgentRunError(
                "TIMEOUT", "Agent exceeded the execution deadline."
            ) from None
        except GraphRecursionError:
            raise AgentRunError(
                "STEP_LIMIT", "Agent exceeded the graph step limit."
            ) from None
        except (ValidationError, StructuredOutputError):
            raise AgentRunError(
                "OUTPUT_INVALID", "Agent returned an invalid plan."
            ) from None
        except Exception as exc:  # noqa: BLE001 -- public boundary hides provider details
            # Do not log prompts, provider response bodies or credentials.
            public_error = _provider_failure(exc)
            logger.warning(
                "Agent execution failed (%s, public_code=%s)",
                type(exc).__name__,
                public_error.code,
            )
            raise public_error from None
        return RunResult(
            run_id=str(uuid4()),
            mode="live",
            output=output,
        )


async def run_agent(request: RunRequest, *, timeout_seconds: float = 120) -> RunResult:
    """Compatibility helper for CLI and direct one-off Python calls."""
    return await AgentRuntime().run(request, timeout_seconds=timeout_seconds)
