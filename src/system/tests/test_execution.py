import asyncio
import unittest
from unittest.mock import AsyncMock

from runtime.engine import execute_workflow
from runtime.restricted_python import (
    RestrictedPythonError,
    execute_restricted_python,
)


class RestrictedPythonTests(unittest.TestCase):
    def test_executes_demo_data_transformation(self):
        result = execute_restricted_python(
            """def main(inputs):
    cleaned_text = ' '.join(inputs['draft'].split())
    return {'cleaned_text': cleaned_text, 'word_count': len(cleaned_text.split())}
""",
            {"draft": "Xin   chao\nAgentFlow"},
        )

        self.assertEqual(
            result, {"cleaned_text": "Xin chao AgentFlow", "word_count": 3}
        )

    def test_rejects_imports_instead_of_executing_them(self):
        with self.assertRaisesRegex(
            RestrictedPythonError, "unsupported Python statement"
        ):
            execute_restricted_python(
                """def main(inputs):
    import os
    return {'value': os.environ}
""",
                {},
            )


class WorkflowRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_llm_call_uses_direct_executor_exactly_once(self):
        node = {
            "id": "summarize",
            "type": "llm.call",
            "inputs": {"text": {"from": "$input.text"}},
            "config": {
                "prompt": "Summarize the text",
                "inputSchema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                "outputSchema": {
                    "type": "object",
                    "properties": {"summary": {"type": "string"}},
                    "required": ["summary"],
                },
            },
        }
        graph = {"nodes": [node], "edges": []}
        execute_agent = AsyncMock()
        execute_llm = AsyncMock(return_value={"summary": "Short"})

        result = await execute_workflow(
            graph,
            {"text": "A long text"},
            execute_agent,
            execute_llm=execute_llm,
        )

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.output, {"summary": "Short"})
        execute_llm.assert_awaited_once_with(node, {"text": "A long text"})
        execute_agent.assert_not_awaited()

    async def test_llm_call_receives_the_previous_node_output(self):
        graph = {
            "nodes": [
                {
                    "id": "draft",
                    "type": "agent",
                    "config": {"inputSchema": {}, "outputSchema": {}},
                },
                {
                    "id": "summarize",
                    "type": "llm.call",
                    "config": {
                        "prompt": "Summarize {{input.draft}}",
                        "inputSchema": {},
                        "outputSchema": {},
                    },
                },
            ],
            "edges": [{"from": "draft", "to": "summarize"}],
        }
        execute_agent = AsyncMock(return_value={"draft": "Previous output"})
        execute_llm = AsyncMock(return_value={"summary": "Short"})

        result = await execute_workflow(
            graph, {}, execute_agent, execute_llm=execute_llm
        )

        self.assertEqual(result.output, {"summary": "Short"})
        execute_llm.assert_awaited_once_with(
            graph["nodes"][1], {"draft": "Previous output"}
        )

    async def test_parallel_branches_run_concurrently_and_receive_split_output(self):
        graph = {
            "settings": {"maxParallelNodes": 2},
            "nodes": [
                {"id": "input", "type": "input.schema", "schema": {}},
                {"id": "split", "type": "parallel"},
                {
                    "id": "left",
                    "type": "agent",
                    "config": {"inputSchema": {}, "outputSchema": {}},
                },
                {
                    "id": "right",
                    "type": "agent",
                    "config": {"inputSchema": {}, "outputSchema": {}},
                },
            ],
            "edges": [
                {"from": "input", "to": "split"},
                {"from": "split", "port": "parallel", "to": "left"},
                {"from": "split", "port": "parallel", "to": "right"},
            ],
        }
        started: set[str] = set()
        both_started = asyncio.Event()

        async def parallel_agent(node, inputs):
            started.add(node["id"])
            if len(started) == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=0.5)
            return {"branch": node["id"], "shared": inputs["shared"]}

        result = await execute_workflow(
            graph, {"shared": "parent-output"}, parallel_agent
        )

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(started, {"left", "right"})
        self.assertEqual(
            result.output,
            {
                "branches": {
                    "left": {"branch": "left", "shared": "parent-output"},
                    "right": {"branch": "right", "shared": "parent-output"},
                }
            },
        )

    async def test_if_executes_only_selected_branch_and_skips_the_other(self):
        graph = {
            "nodes": [
                {"id": "input", "type": "input.schema", "schema": {}},
                {
                    "id": "decision",
                    "type": "if",
                    "inputs": {"value": {"from": "$input.approved"}},
                    "config": {"operator": "equals", "expected": True},
                },
                {
                    "id": "accepted",
                    "type": "agent",
                    "config": {"inputSchema": {}, "outputSchema": {}},
                },
                {
                    "id": "rejected",
                    "type": "agent",
                    "config": {"inputSchema": {}, "outputSchema": {}},
                },
            ],
            "edges": [
                {"from": "input", "to": "decision"},
                {"from": "decision", "port": "true", "to": "accepted"},
                {"from": "decision", "port": "false", "to": "rejected"},
            ],
        }
        calls: list[str] = []

        async def branch_agent(node, inputs):
            calls.append(node["id"])
            return {"selected": node["id"], "decision": inputs["condition"]}

        result = await execute_workflow(graph, {"approved": True}, branch_agent)

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(calls, ["accepted"])
        self.assertEqual(result.output, {"selected": "accepted", "decision": True})
        self.assertEqual(
            [(step.node_id, step.status) for step in result.steps],
            [
                ("input", "succeeded"),
                ("decision", "succeeded"),
                ("accepted", "succeeded"),
                ("rejected", "skipped"),
            ],
        )

    async def test_parallel_join_waits_for_and_can_read_both_branch_outputs(self):
        graph = {
            "nodes": [
                {"id": "split", "type": "parallel"},
                {
                    "id": "left",
                    "type": "agent",
                    "config": {"inputSchema": {}, "outputSchema": {}},
                },
                {
                    "id": "right",
                    "type": "agent",
                    "config": {"inputSchema": {}, "outputSchema": {}},
                },
                {
                    "id": "join",
                    "type": "output.schema",
                    "inputs": {
                        "left": {"from": "$nodes.left.output.value"},
                        "right": {"from": "$nodes.right.output.value"},
                    },
                    "schema": {},
                },
            ],
            "edges": [
                {"from": "split", "port": "parallel", "to": "left"},
                {"from": "split", "port": "parallel", "to": "right"},
                {"from": "left", "to": "join"},
                {"from": "right", "to": "join"},
            ],
        }

        async def branch_agent(node, inputs):
            return {"value": f"{node['id']}:{inputs['shared']}"}

        result = await execute_workflow(graph, {"shared": "same"}, branch_agent)

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.output, {"left": "left:same", "right": "right:same"})
        self.assertEqual(result.steps[-1].node_id, "join")
        self.assertTrue(
            result.steps[-1].started_at >= result.steps[1].completed_at
            and result.steps[-1].started_at >= result.steps[2].completed_at
        )

    async def test_runs_agent_python_agent_and_captures_every_step(self):
        graph = {
            "nodes": [
                {
                    "id": "draft",
                    "type": "agent",
                    "inputs": {"topic": {"from": "$input.topic"}},
                    "config": {
                        "inputSchema": {
                            "type": "object",
                            "properties": {"topic": {"type": "string"}},
                            "required": ["topic"],
                            "additionalProperties": False,
                        },
                        "outputSchema": {
                            "type": "object",
                            "properties": {"draft": {"type": "string"}},
                            "required": ["draft"],
                            "additionalProperties": False,
                        },
                    },
                },
                {
                    "id": "clean",
                    "type": "code.python",
                    "inputs": {"draft": {"from": "$nodes.draft.output.draft"}},
                    "config": {
                        "code": "def main(inputs):\n    return {'cleaned': ' '.join(inputs['draft'].split())}\n",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"draft": {"type": "string"}},
                            "required": ["draft"],
                            "additionalProperties": False,
                        },
                        "outputSchema": {
                            "type": "object",
                            "properties": {"cleaned": {"type": "string"}},
                            "required": ["cleaned"],
                            "additionalProperties": False,
                        },
                    },
                },
                {
                    "id": "review",
                    "type": "agent",
                    "inputs": {"content": {"from": "$nodes.clean.output.cleaned"}},
                    "config": {
                        "inputSchema": {
                            "type": "object",
                            "properties": {"content": {"type": "string"}},
                            "required": ["content"],
                            "additionalProperties": False,
                        },
                        "outputSchema": {
                            "type": "object",
                            "properties": {"final": {"type": "string"}},
                            "required": ["final"],
                            "additionalProperties": False,
                        },
                    },
                },
            ],
            "edges": [
                {"from": "draft", "to": "clean"},
                {"from": "clean", "to": "review"},
            ],
        }

        async def agent(node, inputs):
            if node["id"] == "draft":
                return {"draft": f"  Noi dung   {inputs['topic']}  "}
            return {"final": inputs["content"]}

        result = await execute_workflow(graph, {"topic": "AI"}, agent)

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.output, {"final": "Noi dung AI"})
        self.assertEqual(
            [(step.node_id, step.status) for step in result.steps],
            [
                ("draft", "succeeded"),
                ("clean", "succeeded"),
                ("review", "succeeded"),
            ],
        )
        self.assertEqual(result.steps[1].input, {"draft": "  Noi dung   AI  "})
        self.assertEqual(result.steps[1].output, {"cleaned": "Noi dung AI"})

    async def test_marks_failure_and_skips_downstream_steps(self):
        graph = {
            "nodes": [
                {
                    "id": "agent",
                    "type": "agent",
                    "config": {"inputSchema": {}, "outputSchema": {}},
                },
                {
                    "id": "python",
                    "type": "code.python",
                    "config": {
                        "code": "def main(inputs):\n    return inputs\n",
                        "inputSchema": {},
                        "outputSchema": {},
                    },
                },
            ],
            "edges": [{"from": "agent", "to": "python"}],
        }

        async def unavailable_agent(_node, _inputs):
            raise RuntimeError("provider unavailable")

        result = await execute_workflow(graph, {}, unavailable_agent)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.steps[0].status, "failed")
        assert result.steps[0].error is not None
        self.assertEqual(result.steps[0].error["message"], "provider unavailable")
        self.assertEqual(result.steps[1].status, "skipped")

    async def test_reports_each_step_while_the_workflow_is_running(self):
        graph = {
            "nodes": [
                {"id": "input", "type": "input.schema", "schema": {}},
                {"id": "output", "type": "output.schema", "schema": {}},
            ],
            "edges": [{"from": "input", "to": "output"}],
        }
        updates = []

        async def unused_agent(_node, _inputs):
            raise AssertionError("agent should not be called")

        async def capture_update(step):
            updates.append((step.node_id, step.status, step.output))

        await execute_workflow(
            graph, {"value": 1}, unused_agent, on_step_update=capture_update
        )

        self.assertEqual(
            updates,
            [
                ("input", "running", None),
                ("input", "succeeded", {"value": 1}),
                ("output", "running", None),
                ("output", "succeeded", {"value": 1}),
            ],
        )


if __name__ == "__main__":
    unittest.main()
