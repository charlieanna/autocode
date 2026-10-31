"""Public combined-review selection remains bound to the exact approved plan."""

import argparse
import copy
import sys
import unittest
from unittest import mock

import autocode_goal_lifecycle as lifecycle
import autocode_goals as goals
import autocode_resolver_human as human
import autocode_support as support
import autocode_workflow as workflow
from goal_fixtures import approve_fixture, body

from . import test_autocode, test_subprocess

try:
    import autocode_review_routing as routing
except ImportError:
    routing = None


class RoutingConfigTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(routing, "the public review-routing policy is missing")

    def test_argument_and_new_run_choices(self):
        parser = argparse.ArgumentParser()
        routing.add_argument(parser)
        self.assertIsNone(parser.parse_args([]).review_routing)
        self.assertEqual("combined", parser.parse_args(["--review-routing", "combined"]).review_routing)
        self.assertEqual("separate", routing.configured({}, None, started=False))
        self.assertEqual("combined", routing.configured({}, "combined", started=False))
        with self.assertRaisesRegex(ValueError, "milestone checkpoints"):
            routing.configured({"milestone_checkpoints": {"enabled": True}}, "combined", started=False)

    def test_saved_choices_cannot_change_and_missing_means_separate(self):
        self.assertEqual("separate", routing.configured({}, None, started=True))
        saved = {"review_routing": "combined"}
        for selected in (None, "combined"):
            self.assertEqual("combined", routing.configured(saved, selected, started=True))
        for previous, selected in (({}, "combined"), (saved, "separate")):
            with self.assertRaisesRegex(ValueError, "new-run policy"):
                routing.configured(previous, selected, started=True)


class PlanApprovalTests(unittest.TestCase):
    setUp = test_autocode.RetrofitTest.setUp

    def draft(self):
        lifecycle.migrate(self.state)
        lifecycle.install_draft(self.state, body(), origin="fixture")
        human.evaluate(self.state)
        lifecycle.present(self.state)
        return goals.token(self.state["goal_contract"])

    def test_selection_is_shown_hashed_and_activated_only_by_exact_plan_approval(self):
        self.assertIsNotNone(routing, "the public review-routing policy is missing")
        self.state["settings"]["review_routing"] = "combined"
        selected = self.draft()
        self.assertIn(routing.POLICY, self.state["goal_contract"]["body"]["constraints"])
        self.assertIn(routing.POLICY, lifecycle.render(self.state))
        self.assertIn(routing.POLICY, lifecycle.approval_screen(self.state))
        self.assertIn(routing.POLICY, "\n".join(lifecycle.approval_summary(self.state)))
        self.assertFalse(workflow.enabled(self.state))
        before = copy.deepcopy(self.state)
        with self.assertRaises(ValueError):
            lifecycle.approve(self.state, "stale-token")
        self.assertEqual(before, self.state)
        lifecycle.approve(self.state, selected, source="agent review under owner delegation 2026-10-29")
        provenance.stamp(before, self.state, {"command": "autocode --approve-goal", "claimed_as": "delegated agent"})
        self.assertEqual("astra_checkpoint", workflow.review_stage(self.state))
        self.assertEqual(before["settings"]["roles"], self.state["settings"]["roles"])
        event = self.state["goal_contract"]["approval_event"]
        self.assertEqual(event, self.state["settings"]["workflow"]["approval_event"])
        self.assertEqual("agent review under owner delegation 2026-10-29", event["source"])
        self.state["user_events"].remove(event)
        with self.assertRaises(support.Paused):
            workflow.guard(self.state)

    def test_reapproval_rebinds_the_policy_and_a_new_draft_cannot_reuse_old_approval(self):
        self.assertIsNotNone(routing, "the public review-routing policy is missing")
        self.state["settings"]["review_routing"] = "combined"
        first = self.draft()
        lifecycle.approve(self.state, first)
        second = self.draft()
        self.assertNotEqual(first, second)
        with self.assertRaises(support.Paused):
            workflow.guard(self.state)
        lifecycle.approve(self.state, second)
        self.assertEqual("astra_checkpoint", workflow.review_stage(self.state))
        self.state["settings"]["review_routing"] = "separate"
        with self.assertRaises(support.Paused):
            workflow.guard(self.state)

    def test_a_policy_absent_from_the_reviewed_contract_cannot_be_activated(self):
        self.assertIsNotNone(routing, "the public review-routing policy is missing")
        selected = self.draft()
        self.state["settings"]["review_routing"] = "combined"
        with self.assertRaisesRegex(ValueError, "displayed plan"):
            lifecycle.approve(self.state, selected)
        self.assertFalse(goals.approved(self.state))

    def test_separate_review_keeps_the_existing_plan_and_approval_behavior(self):
        self.assertIsNotNone(routing, "the public review-routing policy is missing")
        original = body()
        self.assertEqual(original, routing.contract_body(self.state, original))
        approve_fixture(self.state, goals)
        self.assertEqual("sol", workflow.review_stage(self.state))
        self.assertNotIn("workflow", self.state["settings"])

    def test_legacy_workflow_approvals_still_require_their_exact_policy(self):
        approve_fixture(self.state, goals)
        self.state["status"] = "PAUSED_REQUESTED"
        workflow.activate(self.state, approval_source="legacy explicit operator choice")
        self.assertEqual("astra_checkpoint", workflow.review_stage(self.state))
        with mock.patch.object(workflow, "APPROVAL_TEXT", "different policy"):
            with self.assertRaises(support.Paused):
                workflow.guard(self.state)


@unittest.skipUnless(sys.platform.startswith("linux"), "supervised fake-provider flow requires supported Linux")
class PublicCombinedRoutingTests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ("--engine", "codex")

    def test_cli_approves_and_completes_with_one_combined_review_without_private_state_edits(self):
        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"
        started = self.launch(["Build greeting", "--review-routing", "combined", "--chat"], 2, answers="CLI\nno\n")
        self.assertNotIn("unrecognized arguments", started.stderr)
        run, state = self.saved()
        self.assertEqual("AWAITING_GOAL_APPROVAL", state["status"])
        self.assertIn(routing.POLICY, state["goal_contract"]["body"]["constraints"])
        self.launch(["--run-dir", str(run), "--approve-goal", state["displayed_goal"], "--no-chat"], 0)
        self.launch(["--run-dir", str(run), "--no-chat"], 0)
        _, state = self.saved()
        self.assertEqual("TASK_COMPLETE", state["status"])
        reviews = [
            row["stage"] for row in state["stages"] if row["stage"] in ("sol", "astra_review", "astra_checkpoint")
        ]
        self.assertEqual(["astra_checkpoint"], reviews)
        self.assertEqual("PASS", state["validation"]["verdict"])


import autocode_approval_provenance as provenance
