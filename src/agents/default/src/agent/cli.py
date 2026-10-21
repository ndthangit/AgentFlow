"""JSON stdin/stdout boundary for a flow worker to call as a subprocess."""

import argparse
import asyncio
import json
import sys

from pydantic import ValidationError

from agent.contracts import AgentRunError, RunRequest
from agent.telemetry import configure_agent_telemetry, shutdown_agent_telemetry


def main() -> None:
    telemetry = configure_agent_telemetry()
    parser = argparse.ArgumentParser(description="Run the AgentFlow sample planner")
    parser.add_argument(
        "--task", help="Task to plan; omit to read a JSON request from stdin"
    )
    args = parser.parse_args()
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        # Import after instrumentation is installed so LangChain callbacks are traced.
        from agent.runtime import run_agent

        request = (
            RunRequest(task=args.task)
            if args.task is not None
            else RunRequest.model_validate_json(sys.stdin.read())
        )
        result = asyncio.run(run_agent(request))
        print(result.model_dump_json())
    except ValidationError:
        print(
            json.dumps(
                {
                    "error": {
                        "code": "INVALID_INPUT",
                        "message": "Invalid task/context JSON.",
                    }
                }
            )
        )
        raise SystemExit(2) from None
    except AgentRunError as exc:
        error = {"code": exc.code, "message": exc.message}
        if exc.detail:
            error["detail"] = exc.detail
        print(json.dumps({"error": error}))
        raise SystemExit(1) from None
    finally:
        shutdown_agent_telemetry(telemetry)


if __name__ == "__main__":
    main()
