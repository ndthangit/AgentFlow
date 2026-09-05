"""JSON stdin/stdout boundary for a flow worker to call as a subprocess."""

import argparse
import asyncio
import json
import sys

from pydantic import ValidationError

from agent.contracts import AgentRunError, RunRequest
from agent.runtime import run_agent


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the AgentFlow sample planner")
    parser.add_argument(
        "--task", help="Task to plan; omit to read a JSON request from stdin"
    )
    parser.add_argument(
        "--demo", action="store_true", help="Use a scripted offline model"
    )
    args = parser.parse_args()
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        request = (
            RunRequest(task=args.task)
            if args.task is not None
            else RunRequest.model_validate_json(sys.stdin.read())
        )
        result = asyncio.run(run_agent(request, demo=args.demo))
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
        print(json.dumps({"error": {"code": exc.code, "message": exc.message}}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
