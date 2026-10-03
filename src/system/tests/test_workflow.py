import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pydantic import ValidationError

from api.workflows import _version_snapshot
from domain.errors import WorkflowExecutionError
from domain.examples import sum_workflow_draft
from domain.schemas import WorkflowCreate
from domain.validation import (
    agent_mcp_server_ids,
    agent_skill_ids,
    validate_agent_mcp_selections,
    validate_agent_model_selections,
    validate_agent_skill_selections,
    validate_graph,
)
from runtime.agent_executor import skill_instructions_for_node
from runtime.builtin import (
    execute_builtin_workflow,
    execute_builtin_workflow_detailed,
)
from services.skills import WorkflowSkillSelection, content_hash


class WorkflowValidationTests(unittest.TestCase):
    def test_agent_mcp_selections_are_scoped_and_validated(self):
        graph = {
            "nodes": [
                {
                    "id": "researcher",
                    "type": "agent",
                    "config": {
                        "runtime": "agent",
                        "inputSchema": {},
                        "outputSchema": {},
                        "mcpServerIds": ["docs", "shared"],
                    },
                },
                {
                    "id": "writer",
                    "type": "agent",
                    "config": {
                        "runtime": "agent",
                        "inputSchema": {},
                        "outputSchema": {},
                        "mcpServerIds": ["shared"],
                    },
                },
            ]
        }

        self.assertEqual(agent_mcp_server_ids(graph), ["docs", "shared"])
        servers = [SimpleNamespace(id="docs"), SimpleNamespace(id="shared")]
        self.assertEqual(validate_agent_mcp_selections(graph, servers), [])
        self.assertEqual(
            validate_agent_mcp_selections(graph, [SimpleNamespace(id="docs")]),
            ["agent node researcher references unavailable MCP servers: shared", "agent node writer references unavailable MCP servers: shared"],
        )

    def test_direct_agent_runtime_rejects_mcp_servers(self):
        graph = {
            "nodes": [
                {
                    "id": "writer",
                    "type": "agent",
                    "config": {
                        "runtime": "direct",
                        "inputSchema": {},
                        "outputSchema": {},
                        "mcpServerIds": ["docs"],
                    },
                }
            ],
            "edges": [],
        }

        self.assertIn(
            "agent node writer must use agent runtime when MCP servers are selected",
            validate_graph(graph),
        )

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

    def test_agent_skill_selections_come_directly_from_node_configs(self):
        graph = {
            "nodes": [
                {
                    "id": "researcher",
                    "type": "agent",
                    "config": {"skillIds": ["research", "shared"]},
                },
                {
                    "id": "writer",
                    "type": "agent",
                    "config": {"skillIds": ["shared", "writing"]},
                },
            ]
        }

        self.assertEqual(agent_skill_ids(graph), ["research", "shared", "writing"])
        available = [
            SimpleNamespace(id="research"),
            SimpleNamespace(id="shared"),
            SimpleNamespace(id="writing"),
        ]
        self.assertEqual(validate_agent_skill_selections(graph, available), [])

    def test_rejects_agent_skill_that_is_not_active_and_visible(self):
        graph = {
            "nodes": [
                {
                    "id": "writer",
                    "type": "agent",
                    "config": {"skillIds": ["missing-skill"]},
                }
            ]
        }

        self.assertEqual(
            validate_agent_skill_selections(graph, []),
            ["agent node writer references unavailable skills: missing-skill"],
        )

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
            "nodes": [
                {"id": "start", "type": "input.schema", "schema": {}},
                {"id": "done", "type": "output.schema", "schema": {}},
            ],
            "edges": [{"from": "start", "to": "done"}],
        }
        self.assertEqual(validate_graph(graph), [])

    def test_rejects_empty_unsupported_or_wrong_version_graphs(self):
        self.assertEqual(
            validate_graph({"nodes": [], "edges": []}),
            ["workflow must contain at least one node"],
        )
        errors = validate_graph(
            {
                "nodes": [
                    {"id": "future", "type": "http.request"},
                    {
                        "id": "agent",
                        "type": "agent",
                        "typeVersion": 2,
                        "config": {"inputSchema": {}, "outputSchema": {}},
                    },
                ],
                "edges": [],
            }
        )
        self.assertIn("node future has unsupported type: http.request", errors)
        self.assertIn("node agent typeVersion must be 1 for agent", errors)

    def test_rejects_invalid_unknown_and_non_upstream_bindings(self):
        graph = {
            "nodes": [
                {"id": "start", "type": "input.schema", "schema": {}},
                {
                    "id": "agent",
                    "type": "agent",
                    "inputs": {
                        "bad": {"from": "$input."},
                        "missing": {"from": "$nodes.missing.output.value"},
                        "future": {"from": "$nodes.end.output.value"},
                    },
                    "config": {"inputSchema": {}, "outputSchema": {}},
                },
                {"id": "end", "type": "output.schema", "schema": {}},
            ],
            "edges": [
                {"from": "start", "to": "agent"},
                {"from": "agent", "to": "end"},
            ],
        }

        errors = validate_graph(graph)

        self.assertIn("node agent input bad has invalid reference $input.", errors)
        self.assertIn(
            "node agent input missing references unknown node missing", errors
        )
        self.assertIn(
            "node agent input future must reference an upstream node", errors
        )

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
                {"id": "disabled", "type": "output.schema", "schema": {}},
                {"id": "left", "type": "output.schema", "schema": {}},
                {"id": "right", "type": "output.schema", "schema": {}},
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

    def test_agent_provider_and_model_must_be_selected_together(self):
        graph = {
            "nodes": [
                {
                    "id": "agent",
                    "type": "agent",
                    "config": {
                        "runtime": "direct",
                        "providerId": "provider-1",
                        "inputSchema": {},
                        "outputSchema": {},
                    },
                }
            ],
            "edges": [],
        }

        self.assertIn(
            "agent node agent must define a non-empty model", validate_graph(graph)
        )

    def test_agent_container_runtime_does_not_require_registered_provider(self):
        graph = {
            "nodes": [
                {
                    "id": "agent",
                    "type": "agent",
                    "config": {
                        "runtime": "agent",
                        "inputSchema": {},
                        "outputSchema": {},
                    },
                }
            ],
            "edges": [],
        }

        self.assertEqual(validate_graph(graph), [])
        self.assertEqual(validate_agent_model_selections(graph, []), [])

    def test_agent_container_accepts_node_provider_and_model(self):
        graph = {
            "nodes": [
                {
                    "id": "agent",
                    "type": "agent",
                    "config": {
                        "runtime": "agent",
                        "providerId": "provider-1",
                        "model": "model-1",
                        "inputSchema": {},
                        "outputSchema": {},
                    },
                }
            ],
            "edges": [],
        }

        provider = SimpleNamespace(
            id="provider-1",
            name="OpenRouter",
            settings={"selected_models": ["model-1"]},
        )

        self.assertEqual(validate_graph(graph), [])
        self.assertEqual(validate_agent_model_selections(graph, [provider]), [])
        self.assertIn(
            "agent node agent references an unavailable LLM provider",
            validate_agent_model_selections(graph, []),
        )

    def test_llm_call_requires_prompt_and_structured_schemas(self):
        graph = {
            "nodes": [
                {
                    "id": "summarize",
                    "type": "llm.call",
                    "config": {
                        "prompt": " ",
                        "skillIds": ["writer"],
                        "inputSchema": {},
                    },
                }
            ],
            "edges": [],
        }

        errors = validate_graph(graph)

        self.assertIn("llm.call node summarize must define a non-empty prompt", errors)
        self.assertIn("llm.call node summarize cannot define skillIds", errors)
        self.assertIn(
            "llm.call node summarize must define outputSchema as an object", errors
        )

    def test_llm_call_model_selection_uses_an_enabled_registered_model(self):
        provider_id = uuid.uuid4()
        graph = {
            "nodes": [
                {
                    "id": "summarize",
                    "type": "llm.call",
                    "config": {
                        "prompt": "Summarize",
                        "providerId": str(provider_id),
                        "model": "openai/gpt-test",
                        "inputSchema": {},
                        "outputSchema": {},
                    },
                }
            ],
            "edges": [],
        }
        provider = SimpleNamespace(
            id=provider_id,
            name="OpenRouter",
            settings={"selected_models": ["openai/gpt-test"]},
        )

        self.assertEqual(validate_agent_model_selections(graph, [provider]), [])
        self.assertIn(
            "llm.call node summarize references an unavailable LLM provider",
            validate_agent_model_selections(graph, []),
        )

    def test_agent_model_selection_uses_an_enabled_registered_model(self):
        provider_id = uuid.uuid4()
        graph = {
            "nodes": [
                {
                    "id": "writer",
                    "type": "agent",
                    "config": {
                        "runtime": "direct",
                        "providerId": str(provider_id),
                        "model": "openai/gpt-test",
                        "inputSchema": {},
                        "outputSchema": {},
                    },
                }
            ],
            "edges": [],
        }
        provider = SimpleNamespace(
            id=provider_id,
            name="OpenRouter",
            settings={"selected_models": ["openai/gpt-test"]},
        )

        self.assertEqual(validate_agent_model_selections(graph, [provider]), [])
        provider.settings = {"selected_models": ["anthropic/claude-test"]}
        self.assertIn(
            "agent node writer model openai/gpt-test is not enabled for provider OpenRouter",
            validate_agent_model_selections(graph, [provider]),
        )
        self.assertIn(
            "agent node writer references an unavailable LLM provider",
            validate_agent_model_selections(graph, []),
        )

    def test_new_workflow_has_editable_input_agent_and_output_nodes(self):
        draft = WorkflowCreate(name="Example").draft

        self.assertEqual(
            [node["type"] for node in draft["nodes"]],
            ["input.schema", "agent", "output.schema"],
        )
        self.assertEqual(
            [node["id"] for node in draft["nodes"]],
            ["start", "agent", "end"],
        )
        self.assertEqual(draft["nodes"][0]["name"], "Bắt đầu")
        self.assertEqual(draft["nodes"][-1]["name"], "Kết thúc")
        self.assertEqual(draft["nodes"][-1]["inputs"], {})
        self.assertEqual(draft["nodes"][1]["config"]["skillIds"], [])
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


class WorkflowSkillSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_contains_only_skills_selected_by_agent_nodes(self):
        selected_id = uuid.uuid4()
        unused_id = uuid.uuid4()
        selected = SimpleNamespace(
            id=selected_id,
            slug="writer",
            name="Writer",
            version=2,
            content_hash="a" * 64,
            instructions="Write clearly",
        )
        unused = SimpleNamespace(
            id=unused_id,
            slug="unused",
            name="Unused",
            version=1,
            content_hash="b" * 64,
            instructions="Not selected",
        )
        workflow = SimpleNamespace(
            owner_subject="user-1",
            draft={
                "nodes": [
                    {
                        "id": "agent",
                        "type": "agent",
                        "config": {"skillIds": [str(selected_id)]},
                    }
                ],
                "edges": [],
            },
        )
        session = AsyncMock()
        session.scalars.return_value = [unused, selected]

        graph, _content_hash = await _version_snapshot(workflow, session)

        self.assertEqual([skill["id"] for skill in graph["skills"]], [str(selected_id)])
        self.assertEqual(graph["nodes"][0]["typeVersion"], 1)


if __name__ == "__main__":
    unittest.main()
