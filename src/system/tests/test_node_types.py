import unittest

from domain.node_registry import NODE_TYPES, node_type_catalog
from domain.schemas import NodeTypeView
from main import create_app


class NodeTypeCatalogTests(unittest.TestCase):
    def test_catalog_exposes_every_executable_node_with_a_version(self):
        catalog = node_type_catalog()

        self.assertEqual({item["type"] for item in catalog}, set(NODE_TYPES))
        self.assertTrue(all(item["typeVersion"] == 1 for item in catalog))
        self.assertEqual(
            NodeTypeView.model_validate(catalog[0]).model_dump(by_alias=True)[
                "typeVersion"
            ],
            1,
        )

    def test_system_exposes_the_authenticated_node_catalog_route(self):
        operation = create_app().openapi()["paths"]["/v1/node-types"]["get"]

        self.assertIn("node-types", operation["tags"])
        self.assertIn("security", operation)
