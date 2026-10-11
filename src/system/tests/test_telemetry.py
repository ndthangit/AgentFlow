"""Tests for Langfuse semantic attributes and payload safeguards."""

import json
import unittest
from unittest.mock import Mock, patch

from core.telemetry import annotate_langfuse_span, langfuse_trace_context


class LangfuseTelemetryTests(unittest.TestCase):
    def test_annotation_is_disabled_by_default(self) -> None:
        span = Mock()

        with patch.dict("os.environ", {}, clear=True):
            annotate_langfuse_span(span, "chain", input_data={"value": 1})

        span.set_attribute.assert_not_called()

    def test_annotation_redacts_credentials_and_inherits_context(self) -> None:
        span = Mock()

        with patch.dict(
            "os.environ",
            {"LANGFUSE_ENABLED": "true", "DEPLOYMENT_ENVIRONMENT": "test"},
            clear=True,
        ), langfuse_trace_context(run_id="run-1"):
            annotate_langfuse_span(
                span,
                "agent",
                input_data={"api_token": "secret", "nested": {"value": 2}},
                output_data={"password": "secret", "ok": True},
                metadata={"node_id": "agent-1"},
            )

        attributes = dict(call.args for call in span.set_attribute.call_args_list)
        self.assertTrue(attributes["agentflow.langfuse.export"])
        self.assertEqual(attributes["langfuse.observation.type"], "agent")
        self.assertEqual(attributes["langfuse.environment"], "test")
        self.assertEqual(
            json.loads(attributes["langfuse.observation.input"]),
            {"api_token": "[REDACTED]", "nested": {"value": 2}},
        )
        self.assertEqual(
            json.loads(attributes["langfuse.observation.output"]),
            {"password": "[REDACTED]", "ok": True},
        )
        self.assertEqual(
            attributes["langfuse.observation.metadata.run_id"], "run-1"
        )
        self.assertEqual(
            attributes["langfuse.observation.metadata.node_id"], "agent-1"
        )

    def test_oversized_payload_is_replaced_by_digest(self) -> None:
        span = Mock()

        with patch.dict(
            "os.environ",
            {"LANGFUSE_ENABLED": "true", "LANGFUSE_MAX_IO_BYTES": "1024"},
            clear=True,
        ):
            annotate_langfuse_span(span, "span", input_data={"text": "x" * 2000})

        attributes = dict(call.args for call in span.set_attribute.call_args_list)
        payload = json.loads(attributes["langfuse.observation.input"])
        self.assertTrue(payload["truncated"])
        self.assertGreater(payload["size_bytes"], 1024)
        self.assertEqual(len(payload["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
