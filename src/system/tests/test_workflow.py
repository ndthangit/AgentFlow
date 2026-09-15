import unittest

from pydantic import ValidationError

from domain.errors import WorkflowExecutionError
from domain.examples import sum_workflow_draft
from domain.schemas import WorkflowCreate
from domain.validation import validate_graph
from runtime.agent_executor import skill_instructions_for_node
from runtime.builtin import (
    execute_builtin_workflow,
    execute_builtin_workflow_detailed,
)
from services.skills import WorkflowSkillSelection, content_hash


class WorkflowValidationTests(unittest.TestCase):
    def test_agent_receives_only_its_assigned_skills(self):
        graph = {
            "skills": [
                {"id": "research", "instructions": "Research first"},
                {"id": "writer", "instructions": "Write clearly"},
            ]
        }

        self.assertEqual(
            skill_instructions_for_node(graph, {"skillIds": ["writer"]}),
            ["Write clearly"],
        )
        self.assertEqual(skill_instructions_for_node(graph, {"skillIds": []}), [])

    def test_legacy_agent_inherits_all_workflow_skills(self):
        graph = {
            "skills": [
                {"id": "research", "instructions": "Research first"},
                {"id": "writer", "instructions": "Write clearly"},
            ]
        }

        self.assertEqual(
            skill_instructions_for_node(graph, {}),
            ["Research first", "Write clearly"],
        )

    def test_accepts_dag(self):
        graph = {
            "nodes": [{"id": "start"}, {"id": "done"}],
            "edges": [{"from": "start", "to": "done"}],
        }
        self.assertEqual(validate_graph(graph), [])

    def test_rejects_cycle(self):
        graph = {
            "nodes": [{"id": "a"}, {"id": "b"}],
            "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}],
        }
        self.assertIn("workflow graph must be acyclic", validate_graph(graph))

    def test_accepts_if_else_and_parallel_branches(self):
        graph = {
            "settings": {"maxParallelNodes": 4},
            "nodes": [
                {
                    "id": "decision",
                    "type": "if",
                    "inputs": {"value": {"from": "$input.enabled"}},
                    "config": {"operator": "equals", "expected": True},
                },
                {"id": "split", "type": "parallel"},
                {"id": "disabled"},
                {"id": "left"},
                {"id": "right"},
            ],
            "edges": [
                {"from": "decision", "port": "true", "to": "split"},
                {"from": "decision", "port": "false", "to": "disabled"},
                {"from": "split", "port": "parallel", "to": "left"},
                {"from": "split", "port": "parallel", "to": "right"},
            ],
        }

        self.assertEqual(validate_graph(graph), [])

    def test_rejects_incomplete_branch_configuration(self):
        graph = {
            "settings": {"maxParallelNodes": 0},
            "nodes": [
                {"id": "decision", "type": "if", "config": {"operator": "equals"}},
                {"id": "split", "type": "parallel"},
                {"id": "done"},
            ],
            "edges": [
                {"from": "decision", "port": "success", "to": "split"},
                {"from": "split", "port": "parallel", "to": "done"},
            ],
        }

        errors = validate_graph(graph)

        self.assertIn("if node decision must bind value with from", errors)
        self.assertIn("if node decision must define expected", errors)
        self.assertIn("if node decision edges must use true or false ports", errors)
        self.assertIn("if node decision must have true and false branches", errors)
        self.assertIn("parallel node split must have at least two branches", errors)
        self.assertIn(
            "settings.maxParallelNodes must be an integer from 1 to 32", errors
        )

    def test_rejects_invalid_agent_skill_ids(self):
        graph = {
            "nodes": [
                {
                    "id": "agent",
                    "type": "agent",
                    "config": {
                        "inputSchema": {},
                        "outputSchema": {},
                        "skillIds": ["same", "same"],
                    },
                }
            ],
            "edges": [],
        }

        self.assertIn(
            "agent node agent skillIds must be unique non-empty strings",
            validate_graph(graph),
        )

    def test_new_workflow_has_editable_input_agent_and_output_nodes(self):
        draft = WorkflowCreate(name="Example").draft

        self.assertEqual(
            [node["type"] for node in draft["nodes"]],
            ["input.schema", "agent", "output.schema"],
        )
        self.assertEqual(validate_graph(draft), [])

    def test_python_code_node_config_is_validated(self):
        graph = {
            "nodes": [
                {
                    "id": "python",
                    "type": "code.python",
                    "config": {
                        "language": "javascript",
                        "code": "",
                        "inputSchema": {},
                    },
                }
            ],
            "edges": [],
        }

        errors = validate_graph(graph)

        self.assertIn("code.python node python language must be python", errors)
        self.assertIn("code.python node python must define non-empty code", errors)
        self.assertIn(
            "code.python node python must define outputSchema as an object", errors
        )

    def test_schema_nodes_and_agent_schemas_are_validated(self):
        graph = {
            "nodes": [
                {"id": "input", "type": "input.schema"},
                {"id": "agent", "type": "agent", "config": {}},
            ],
            "edges": [{"from": "input", "to": "agent"}],
        }

        errors = validate_graph(graph)

        self.assertIn("node input must define schema as an object", errors)
        self.assertIn("agent node agent must define inputSchema as an object", errors)
        self.assertIn("agent node agent must define outputSchema as an object", errors)

    def test_sum_workflow_executes(self):
        graph = sum_workflow_draft()

        self.assertEqual(validate_graph(graph), [])
        self.assertEqual(
            execute_builtin_workflow(graph, {"num1": 7, "num2": 5}),
            {"sum": 12},
        )

    def test_sum_workflow_captures_each_node_input_and_output(self):
        result = execute_builtin_workflow_detailed(
            sum_workflow_draft(), {"num1": 7, "num2": 5}
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(
            [step.node_id for step in result.steps], ["input", "sum", "output"]
        )
        self.assertEqual(result.steps[0].input, {"num1": 7, "num2": 5})
        self.assertEqual(result.steps[1].input, {"left": 7, "right": 5})
        self.assertEqual(result.steps[1].output, {"sum": 12})
        self.assertEqual(result.steps[2].output, {"sum": 12})

    def test_sum_workflow_rejects_invalid_input(self):
        with self.assertRaisesRegex(WorkflowExecutionError, "num2 is required"):
            execute_builtin_workflow(sum_workflow_draft(), {"num1": 7})


class SkillTests(unittest.TestCase):
    def test_content_hash_is_stable(self):
        self.assertEqual(content_hash("same"), content_hash("same"))
        self.assertNotEqual(content_hash("same"), content_hash("changed"))

    def test_selection_rejects_duplicate_ids(self):
        import uuid

        skill_id = uuid.uuid4()
        with self.assertRaises(ValidationError):
            WorkflowSkillSelection(skill_ids=[skill_id, skill_id])


if __name__ == "__main__":
    unittest.main()
