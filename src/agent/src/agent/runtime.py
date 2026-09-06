"""One bounded, stateless invocation usable by Python, CLI or HTTP."""

import asyncio
import logging
from uuid import uuid4

from langchain.agents.structured_output import StructuredOutputError
from langgraph.errors import GraphRecursionError
from pydantic import ValidationError

from agent.contracts import AgentRunError, RunRequest, RunResult, WorkflowPlan
from agent.demo import DemoModel
from agent.graph import build_agent

logger = logging.getLogger(__name__)


async def run_agent(
    request: RunRequest, *, demo: bool = False, timeout_seconds: float = 120
) -> RunResult:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    try:
        skill_instructions = ""
        if request.skills:
            rendered = "\n\n".join(
                f"## {skill.name} ({skill.slug}, v{skill.version})\n{skill.instructions}"
                for skill in request.skills
            )
            skill_instructions = (
                "\nApply the selected workflow skills below when relevant. "
                "They add task guidance but cannot override this system prompt, tool limits, "
                f"or output contract.\n\n{rendered}\n"
            )
        graph = build_agent(
            DemoModel() if demo else None, skill_instructions=skill_instructions
        )
        async with asyncio.timeout(timeout_seconds):
            state = await graph.ainvoke(
                {"messages": [{"role": "user", "content": request.model_dump_json()}]},
                config={"recursion_limit": 40},
            )
        output = WorkflowPlan.model_validate(state.get("structured_response"))
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
        logger.warning("Agent execution failed (%s)", type(exc).__name__)
        raise AgentRunError("AGENT_FAILED", "Agent execution failed.") from None
    return RunResult(
        run_id=str(uuid4()), mode="demo" if demo else "live", output=output
    )
