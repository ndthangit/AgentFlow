import unittest

from sqlalchemy import CheckConstraint, UniqueConstraint

from domain.models import (
    Base,
    NodeAttemptStatus,
    RunCommandStatus,
    RunCommandType,
)


class ExecutionLedgerModelTests(unittest.TestCase):
    def test_node_attempt_has_idempotency_and_state_constraints(self):
        table = Base.metadata.tables["system.node_attempts"]
        unique_columns = {
            tuple(constraint.columns.keys())
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        check_names = {
            constraint.name
            for constraint in table.constraints
            if isinstance(constraint, CheckConstraint)
        }

        self.assertIn(("operation_key",), unique_columns)
        self.assertIn(("run_step_id", "attempt"), unique_columns)
        self.assertIn("ck_node_attempts_status", check_names)
        self.assertFalse(table.c.deadline_at.nullable)
        self.assertEqual(NodeAttemptStatus.UNKNOWN, "unknown")

    def test_run_commands_are_idempotent_within_owner_and_scope(self):
        table = Base.metadata.tables["system.run_commands"]
        unique_columns = {
            tuple(constraint.columns.keys())
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        check_names = {
            constraint.name
            for constraint in table.constraints
            if isinstance(constraint, CheckConstraint)
        }

        self.assertIn(
            ("owner_subject", "scope", "idempotency_key"), unique_columns
        )
        self.assertEqual(
            check_names, {"ck_run_commands_type", "ck_run_commands_status"}
        )
        self.assertEqual(RunCommandType.CANCEL, "cancel")
        self.assertEqual(RunCommandStatus.ACCEPTED, "accepted")

    def test_run_events_have_monotonic_sequence_per_run(self):
        table = Base.metadata.tables["system.run_events"]
        unique_columns = {
            tuple(constraint.columns.keys())
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }

        self.assertIn(("run_id", "sequence"), unique_columns)
