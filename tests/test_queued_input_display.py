"""Accepted queued input can wait at a resolver frontier without a stop reason."""

import copy
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autocode_action_authority as authority
import autocode_action_prepare as preparation


class QueuedInputDisplayTests(unittest.TestCase):
    def test_prepare_reports_saved_input_without_crashing_or_changing_the_held_checkpoint(self):
        for reason in (None, "An operational hold remains"):
            state = {"version": 3, "status": "RESOLVER_PENDING"}
            if reason:
                state["stop_reason"] = reason
            before = copy.deepcopy(state)
            runner = Mock(consume_interventions=Mock(return_value=True))
            args = SimpleNamespace(
                workflow=None, run_dir=Path("/run"), milestone_checkpoints=False, retry_builder=False
            )
            output = io.StringIO()
            with (
                patch.object(preparation.milestones, "apply_queued_activation", return_value=False),
                redirect_stdout(output),
            ):
                code = preparation.prepare(
                    runner,
                    args,
                    Mock(),
                    state,
                    Path("/run/state.json"),
                    Path("/run"),
                    Path("/project"),
                    SimpleNamespace(released=False, issued=None),
                )
            self.assertEqual(2, code)
            self.assertIn(reason or "Queued input saved", output.getvalue())
            self.assertEqual(before, state)
            runner.write_json.assert_not_called()

    def test_authority_reports_saved_input_without_releasing_an_unexplained_hold(self):
        for reason in (None, "An operational hold remains"):
            state = {"status": "RESOLVER_PENDING"}
            if reason:
                state["stop_reason"] = reason
            before = copy.deepcopy(state)
            output = io.StringIO()
            runner = Mock(consume_interventions=Mock(return_value=True))
            with redirect_stdout(output):
                code = authority.apply_queued_input(runner, state, Path("/run"), Path("/project"))
            self.assertEqual(2, code)
            self.assertIn(reason or "Queued input saved", output.getvalue())
            self.assertEqual(before, state)
