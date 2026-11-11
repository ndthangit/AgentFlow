import asyncio
import json
import unittest

from domain.errors import WorkflowExecutionError
from runtime.container_process import ContainerResult
from runtime.python_sandbox import (
    PythonSandboxExecutor,
    PythonSandboxSettings,
    build_python_sandbox_command,
)
from runtime.python_sandbox_runner import execute_request


def settings() -> PythonSandboxSettings:
    return PythonSandboxSettings(
        image="agentflow-system:test",
        docker_binary="docker",
        timeout_seconds=5,
        cpus="0.5",
        memory="256m",
        pids_limit=32,
        max_input_bytes=1024 * 1024,
        max_output_bytes=1024 * 1024,
    )


class PythonSandboxCommandTests(unittest.TestCase):
    def test_command_is_read_only_bounded_and_networkless(self):
        command = build_python_sandbox_command(
            settings(), "agentflow-python-job", {"node-id": "python"}
        )

        self.assertEqual(command[:3], ["docker", "run", "--rm"])
        self.assertIn("--read-only", command)
        self.assertIn("ALL", command)
        self.assertIn("no-new-privileges", command)
        self.assertEqual(command[command.index("--network") + 1], "none")
        self.assertEqual(command[command.index("--memory") + 1], "256m")
        self.assertNotIn("--env", command)
        self.assertEqual(
            command[-3:],
            ["agentflow-system:test", "-m", "runtime.python_sandbox_runner"],
        )
        self.assertIn("io.agentflow.node-id=python", command)


class PythonSandboxRunnerTests(unittest.TestCase):
    def test_executes_the_restricted_python_contract(self):
        result = execute_request(
            {
                "code": "def main(inputs):\n    return {'value': inputs['value']}\n",
                "inputs": {"value": 7},
            }
        )

        self.assertEqual(result, {"value": 7})


class PythonSandboxExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def test_runs_one_container_and_returns_its_output(self):
        calls = []

        async def runner(command, name, payload, environment, timeout):
            calls.append((command, name, payload, environment, timeout))
            return ContainerResult(0, b'{"output":{"value":7}}', b"")

        executor = PythonSandboxExecutor(
            settings(), runner=runner, semaphore=asyncio.Semaphore(1)
        )
        result = await executor.execute(
            "def main(inputs):\n    return inputs\n", {"value": 7}
        )

        self.assertEqual(result, {"value": 7})
        self.assertEqual(len(calls), 1)
        command, name, payload, _environment, timeout = calls[0]
        self.assertTrue(name.startswith("agentflow-python-"))
        self.assertEqual(json.loads(payload)["inputs"], {"value": 7})
        self.assertEqual(
            command[-3:],
            ["agentflow-system:test", "-m", "runtime.python_sandbox_runner"],
        )
        self.assertEqual(timeout, 5)

    async def test_propagates_the_safe_runner_error(self):
        async def runner(*_args):
            return ContainerResult(
                1, b'{"error":{"message":"unsupported Python statement: Import"}}', b""
            )

        executor = PythonSandboxExecutor(
            settings(), runner=runner, semaphore=asyncio.Semaphore(1)
        )
        with self.assertRaisesRegex(
            WorkflowExecutionError, "unsupported Python statement: Import"
        ):
            await executor.execute("import os", {})


if __name__ == "__main__":
    unittest.main()
