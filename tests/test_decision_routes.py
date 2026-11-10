"""The decision routes Autopilot's _apply_result hands off (#2753)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import autocode_decision_routes as decision_routes


def runtime(*, final_only=False):
    return SimpleNamespace(
        support=SimpleNamespace(snapshot=MagicMock(name="snapshot")),
        goals=MagicMock(name="goals"),
        workflow=SimpleNamespace(final_only=lambda state: final_only),
    )


def checkpoint_value(**overrides):
    value = {
        "status": "CONTINUE",
        "next_task": {"kind": "none"},
        "acceptance_criteria": [{"id": "AC-1", "status": "verified"}],
    }
    value.update(overrides)
    return value


class ProgressiveCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.enabled = patch.object(decision_routes.progressive_state, "enabled", return_value=True).start()
        self.checkpoint = patch.object(decision_routes.progressive_state, "checkpoint").start()
        self.snapshot = patch.object(decision_routes.source_scope, "snapshot", return_value={"revision": "r1"}).start()
        patch.object(decision_routes.findings_ledger, "blocking_entries", return_value=["F-1"]).start()
        self.addCleanup(patch.stopall)

    def test_a_checkpoint_needs_continue_in_a_progressive_run(self):
        for status, enabled in (("REWORK", True), ("CONTINUE", False)):
            with self.subTest(status=status, enabled=enabled):
                self.enabled.return_value = enabled
                with self.assertRaisesRegex(ValueError, "requires explicit CONTINUE"):
                    decision_routes.progressive_checkpoint(runtime(), {}, checkpoint_value(status=status), {}, "/w")
        self.checkpoint.assert_not_called()

    def test_a_checkpoint_cannot_assign_the_next_task(self):
        value = checkpoint_value(next_task={"kind": "implement"})
        with self.assertRaisesRegex(ValueError, "cannot manufacture a next assignment"):
            decision_routes.progressive_checkpoint(runtime(), {}, value, {}, "/w")
        self.checkpoint.assert_not_called()

    def test_a_verified_criterion_needs_the_validators_pass(self):
        state = {"validation": {"criterion_results": [{"id": "AC-1", "status": "FAIL"}]}}
        with self.assertRaisesRegex(ValueError, "without the Validator's current product proof"):
            decision_routes.progressive_checkpoint(runtime(), state, checkpoint_value(), {}, "/w")
        self.checkpoint.assert_not_called()

    def test_a_proven_checkpoint_is_recorded_with_the_open_findings(self):
        state = {"validation": {"criterion_results": [{"id": "AC-1", "status": "PASS"}]}}
        record = {"output": "review.json"}
        decision_routes.progressive_checkpoint(runtime(), state, checkpoint_value(), record, "/w")
        self.checkpoint.assert_called_once_with(state, {"revision": "r1"}, record, product_findings=["F-1"])


class UserRequestTests(unittest.TestCase):
    def route(self, stage, value, *, final_only=False, record=None):
        self.queue = MagicMock(name="queue_resolution")
        self.within = MagicMock(name="assert_within_assignment")
        state = {}
        handled = decision_routes.user_request(
            runtime(final_only=final_only),
            state,
            stage,
            value,
            record or {"output": "out.json", "source_revision": "r1"},
            "/w",
            queue_resolution=self.queue,
            assert_within_assignment=self.within,
        )
        return handled, state

    def test_a_reviewer_asking_the_user_must_choose_blocked(self):
        value = {"status": "CONTINUE", "user_request": {"kind": "decision"}}
        with self.assertRaisesRegex(ValueError, "must choose BLOCKED"):
            self.route("review_completion", value)

    def test_a_final_only_builder_question_goes_to_the_resolver(self):
        value = {"status": "DONE", "user_request": {"kind": "blocker"}}
        handled, _ = self.route("builder", value, final_only=True)
        self.assertTrue(handled)
        self.within.assert_called_once()
        self.queue.assert_called_once()
        self.assertEqual({"source_stage": "builder", "source_report": True}, self.queue.call_args.kwargs)

    def test_a_builder_question_keeps_its_report_and_goes_to_review(self):
        value = {"status": "DONE", "user_request": {"kind": "decision"}}
        record = {"source_revision": "r2", "changed_files": ["a.py"], "after_ref": "s2", "diff_ref": "d2"}
        handled, state = self.route("builder", value, record=record)
        self.assertTrue(handled)
        self.assertEqual({"role": "builder", "request": {"kind": "decision"}}, state["agent_request"])
        self.assertEqual("review_completion", state["next_stage"])
        self.assertEqual(["a.py"], state["changed_files"])
        self.assertEqual(
            ("r2", "/w"), (state["implementation"]["source_revision"], state["implementation"]["workspace"])
        )

    def test_another_stage_records_the_request_and_continues(self):
        value = {"status": "PASS", "user_request": {"kind": "decision"}}
        handled, state = self.route("tester", value)
        self.assertFalse(handled)
        self.assertEqual({"role": "tester", "request": {"kind": "decision"}}, state["agent_request"])
        self.assertNotIn("next_stage", state)


if __name__ == "__main__":
    unittest.main()
