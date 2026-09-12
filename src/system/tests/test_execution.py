import unittest

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


if __name__ == "__main__":
    unittest.main()
