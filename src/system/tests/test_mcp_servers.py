import unittest
import uuid
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from api.mcp_servers import create_mcp_server, delete_mcp_server
from services.mcp_servers import McpServerCreate


class McpServerTests(unittest.IsolatedAsyncioTestCase):
    def test_contract_rejects_url_credentials_and_unsafe_headers(self):
        for url, headers in (
            ("https://user:secret@example.com/mcp", {}),
            ("https://example.com/mcp", {"Host": "internal"}),
            ("https://example.com/mcp", {"Authorization": "bad\r\nvalue"}),
        ):
            with self.subTest(url=url, headers=headers), self.assertRaises(
                ValidationError
            ):
                McpServerCreate(
                    slug="docs",
                    name="Docs",
                    url=url,
                    headers=headers,
                )

    async def test_create_encrypts_headers_before_persistence(self):
        class FakeSession:
            added = None

            def add(self, server):
                self.added = server

            async def commit(self):
                return None

            async def refresh(self, _server):
                return None

        session = FakeSession()
        request = McpServerCreate(
            slug="docs",
            name="Docs MCP",
            url="https://example.com/mcp",
            headers={"Authorization": "Bearer test-secret"},
        )
        with patch(
            "api.mcp_servers._encrypted_headers", return_value="ciphertext"
        ) as encrypt:
            server = await create_mcp_server(
                request,
                {"sub": "test-user"},
                session,
            )

        self.assertIs(server, session.added)
        self.assertEqual(server.owner_subject, "test-user")
        self.assertEqual(server.headers_encrypted, "ciphertext")
        self.assertTrue(server.has_headers)
        encrypt.assert_called_once_with(
            {"Authorization": "Bearer test-secret"}
        )

    async def test_delete_is_scoped_to_the_authenticated_owner(self):
        server_id = uuid.uuid4()

        class DeleteResult:
            def scalar_one_or_none(self):
                return server_id

        session = AsyncMock()
        session.execute.return_value = DeleteResult()

        response = await delete_mcp_server(
            server_id,
            {"sub": "test-user"},
            session,
        )

        self.assertEqual(response.status_code, 204)
        session.commit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
