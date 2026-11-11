"""JSON stdin/stdout boundary used inside disposable Python sandboxes."""

import json
import sys
from typing import Any

from runtime.restricted_python import execute_restricted_python


def execute_request(request: Any) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise TypeError("Python sandbox request must be an object")
    code = request.get("code")
    inputs = request.get("inputs")
    if not isinstance(code, str) or not code.strip():
        raise ValueError("Python sandbox code must be a non-empty string")
    if not isinstance(inputs, dict):
        raise TypeError("Python sandbox inputs must be an object")
    return execute_restricted_python(code, inputs)


def main() -> int:
    try:
        output = execute_request(json.load(sys.stdin))
        document = {"output": output}
        return_code = 0
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        document = {"error": {"message": str(exc)}}
        return_code = 1
    sys.stdout.write(json.dumps(document, ensure_ascii=False, separators=(",", ":")))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
