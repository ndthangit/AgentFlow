import unittest

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


if __name__ == "__main__":
    unittest.main()
