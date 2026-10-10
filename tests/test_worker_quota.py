"""Pure policy for restoring a member question and rejecting parent-only retry routes."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_dispatch as dispatch
import autocode_member_stop as member_stop
import autocode_resolver_runtime as resolver_runtime
import autocode_support as support
import autocode_worker_quota as quota


class MemberQuestionPolicyTests(unittest.TestCase):
    def fixture(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        worker = {
            "role": "terra",
            "milestone_id": "M1",
            "run_dir": directory.name,
            "workspace": "/work/first",
            "pause_status": "PAUSED_BUDGET",
            "model": "fixture/first",
            "events": "/events/first",
            "attempt_id": "001/builder-01",
        }
        row = {**worker, "status": "PAUSED_BUDGET"}
        Path(directory.name, "result.json").write_text(json.dumps({"status": "PAUSED_BUDGET", "quota_worker": worker}))
        state = {"next_stage": "orchestrator", "orchestration_batch": {"status": "BUILDING", "workers": [row]}}
        return state, {"pause_status": "PAUSED_BUDGET", "quota_worker": worker}

    def test_withdrawn_question_restores_only_its_current_member_payload(self):
        state, origin = self.fixture()
        error = support.Paused("PAUSED_BUDGET", "original quota stop")
        self.assertIs(error, quota.asked_again(state, error, origin))
        self.assertEqual(origin["quota_worker"], error.quota_worker)

    def test_stale_member_and_nonmember_stops_do_not_restore_a_route_question(self):
        controls = (
            (
                "completed member",
                lambda state, origin: state["orchestration_batch"]["workers"][0].update(status="BUILT"),
            ),
            ("different run", lambda state, origin: origin["quota_worker"].update(run_dir="/runs/other")),
            ("different workspace", lambda state, origin: origin["quota_worker"].update(workspace="/work/other")),
            ("different member", lambda state, origin: origin["quota_worker"].update(milestone_id="M2")),
            (
                "different pause",
                lambda state, origin: origin["quota_worker"].update(pause_status="PAUSED_CONTENT_FILTER"),
            ),
            ("finished batch", lambda state, origin: state["orchestration_batch"].update(status="BUILT")),
            ("review checkpoint", lambda state, origin: state.update(next_stage="sol")),
            ("generic stop", lambda state, origin: origin.pop("quota_worker")),
        )
        for name, change in controls:
            with self.subTest(name=name):
                state, origin = self.fixture()
                change(state, origin)
                error = support.Paused("PAUSED_BUDGET", "current cause")
                self.assertIs(error, quota.asked_again(state, error, origin))
                self.assertFalse(hasattr(error, "quota_worker"))
        state, origin = self.fixture()
        error = support.Paused("PAUSED_ORCHESTRATOR_WORKER", "different failure")
        quota.asked_again(state, error, origin)
        self.assertFalse(hasattr(error, "quota_worker"))

    def test_a_new_stop_retains_its_own_payload(self):
        state, origin = self.fixture()
        error = support.Paused("PAUSED_BUDGET", "newly observed stop")
        error.quota_worker = {"milestone_id": "M2"}
        quota.asked_again(state, error, origin)
        self.assertEqual({"milestone_id": "M2"}, error.quota_worker)

    def test_an_unrelated_budget_cause_does_not_recover_the_withdrawn_members_payload(self):
        state, origin = self.fixture()
        proposal = {
            "origin": origin,
            "request": {"discovered": "original quota stop", "decision_needed": "Provide corrective information"},
        }
        error = support.Paused("PAUSED_BUDGET", "Configured total budget exhausted")
        with patch.object(resolver_runtime.human, "withdrawn", return_value=proposal):
            self.assertIs(error, resolver_runtime._asked_again(state, error))
        self.assertFalse(hasattr(error, "quota_worker"))

    def test_same_member_locations_with_a_different_attempt_do_not_restore_the_old_question(self):
        for field, value in (
            ("attempt_id", "001/builder-02"),
            ("events", "/events/second"),
            ("model", "fixture/other"),
        ):
            with self.subTest(field=field):
                state, origin = self.fixture()
                path = Path(origin["quota_worker"]["run_dir"], "result.json")
                result = json.loads(path.read_text())
                result["quota_worker"][field] = value
                path.write_text(json.dumps(result))
                error = support.Paused("PAUSED_BUDGET", "original quota stop")
                quota.asked_again(state, error, origin)
                self.assertFalse(hasattr(error, "quota_worker"))


class MemberRetryRoutePolicyTests(unittest.TestCase):
    def test_unapproved_goal_is_refused_before_settings_or_retry_state_can_change(self):
        state = {"next_stage": "orchestrator", "orchestration_batch": {"status": "BUILDING", "workers": []}}
        before = copy.deepcopy(state)
        error = support.Paused("PAUSED_GOAL_UNAPPROVED", "Review and approve the current goal")
        with patch.object(dispatch.goals, "execution_guard", side_effect=error):
            self.assertIs(error, dispatch.member_retry_refusal(state, ["M1"]))
        self.assertEqual(before, state)

    def test_parent_builder_route_changes_are_refused_but_other_settings_are_allowed(self):
        before = {
            "roles": {
                "terra": {"engine": "opencode", "model": "fixture/first", "provider": None, "reasoning_effort": "high"},
                "sol": {"model": "fixture/tester"},
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "state.json").write_text(json.dumps({"settings": before}))
            state = {
                "orchestration_batch": {
                    "workers": [{"milestone_id": "M1", "run_dir": directory, "status": "PAUSED_BUDGET"}]
                }
            }
            for key, value in (
                ("model", "fixture/other"),
                ("provider", "other"),
                ("reasoning_effort", "low"),
                ("engine", "codex"),
            ):
                with self.subTest(key=key):
                    after = copy.deepcopy(before)
                    after["roles"]["terra"][key] = value
                    refusal = quota.retry_route_refusal(state, ["M1"], before, after, asked="M1")
                    self.assertIn("fixture/first", refusal)
                    self.assertIn("--answer route-terra=MODEL", refusal)
            after = copy.deepcopy(before)
            after["roles"]["sol"]["model"] = "fixture/other-tester"
            after["orchestration"] = {"max_parallel": 3}
            self.assertIsNone(quota.retry_route_refusal(state, ["M1"], before, after))
            state["orchestration_batch"]["workers"][0]["status"] = "FAILED"
            after = copy.deepcopy(before)
            after["roles"]["terra"]["model"] = "fixture/other"
            self.assertNotIn("route-terra", quota.retry_route_refusal(state, ["M1"], before, after))


class OutputLengthMemberPolicyTests(unittest.TestCase):
    def test_member_retry_requires_another_model_and_preserves_completed_siblings(self):
        with tempfile.TemporaryDirectory() as directory:
            row = {
                "milestone_id": "M1",
                "run_dir": directory,
                "workspace": "/owned/member",
                "status": "PAUSED_OUTPUT_CAP",
            }
            worker = {**row, "model": "fixture/first", "pause_status": "PAUSED_OUTPUT_CAP"}
            result = {"status": "PAUSED_OUTPUT_CAP", "quota_worker": worker}
            state = {
                "next_stage": "orchestrator",
                "orchestration_batch": {
                    "status": "BUILDING",
                    "workers": [row, {"milestone_id": "M2", "status": "BUILT"}],
                },
            }
            Path(directory, "state.json").write_text(
                json.dumps({"settings": {"roles": {"terra": {"model": "fixture/first"}}}})
            )
            before = copy.deepcopy(state)
            refusal = quota.refused_retry(state, row, result, asked="M1")
            self.assertIn("exhaust its output limit", str(refusal))
            self.assertNotIn("content filter", str(refusal))
            with patch.object(member_stop, "answered", return_value=(row, worker)):
                label, effect = member_stop.card(state, "M1")
            self.assertIn("model", label)
            self.assertIn("output limit", effect)
            self.assertNotIn("content filter", effect)
            self.assertIn("route-terra", str(refusal))
            self.assertEqual(before, state)
            Path(directory, "state.json").write_text(
                json.dumps({"settings": {"roles": {"terra": {"model": "fixture/other"}}}})
            )
            self.assertIsNone(quota.refused_retry(state, row, result, asked="M1"))
            self.assertEqual(before, state)


if __name__ == "__main__":
    unittest.main()
