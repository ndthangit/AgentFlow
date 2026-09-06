import unittest

from pydantic import ValidationError

from system_api.skills import WorkflowSkillSelection, content_hash
from system_api.workflow import validate_graph


class WorkflowValidationTests(unittest.TestCase):
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
