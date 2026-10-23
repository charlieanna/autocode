"""Explicit recovery of a failed Builder classification through the public CLI."""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autocode_classification_recovery as recovery
import autocode_run_view as run_view
import autocode_stuck_job as stuck

from tests import test_failure_routing_cli as routing_fixture


class ClassificationRecoveryCLI(unittest.TestCase):
    # Borrow the hermetic fixture and public CLI helpers, never another test.
    command = routing_fixture.FailureRoutingCLI.command
    setUp = routing_fixture.FailureRoutingCLI.setUp
    call = routing_fixture.FailureRoutingCLI.call
    status = routing_fixture.FailureRoutingCLI.status
    trace = routing_fixture.FailureRoutingCLI.trace
    call_order = routing_fixture.FailureRoutingCLI.call_order
    seed = routing_fixture.FailureRoutingCLI.seed
    approve = routing_fixture.FailureRoutingCLI.approve
    drive = routing_fixture.FailureRoutingCLI.drive

    def test_failed_classification_retries_only_the_bound_read_only_investigator(self):
        self.seed()
        self.env.update(BUILD_AUDIT_FAULT="no_change", FAILURE_ROUTING_DIAGNOSIS="stale")
        self.drive(codes=(2,))
        held = self.status()
        self.assertEqual("PAUSED_BUILDER_CLASSIFICATION", held["status"])
        need = held["view"]["needs"]
        self.assertEqual("retry_job", need["kind"])
        self.assertEqual("investigate_stuck", need["stage"])
        token = need["job_retry_token"]
        self.assertTrue(token.startswith("bc:"))
        calls = self.call_order()
        for args in (
            ("--resume-paused",),
            ("--resume-paused", "--retry-failed-stage"),
            ("--resume-paused", "--retry-failed-stage", "--job-retry-token", "bc:forged"),
            ("--resume-paused", "--retry-failed-stage", "--job-retry-token", token, "--feedback", "change the plan"),
            ("--resume-paused", "--retry-failed-stage", "--job-retry-token", token, "--abandon-stage", "000/attempt"),
            (
                "--resume-paused",
                "--retry-failed-stage",
                "--job-retry-token",
                token,
                "--reject-assumption",
                "A1",
                "--review-token",
                "forged-review",
            ),
        ):
            with self.subTest(args=args):
                self.drive(*args, codes=(2,))
                current = self.status()
                self.assertEqual(calls, self.call_order())
                for key in ("current_task", "usage", "needs"):
                    self.assertEqual(held["view"][key], current["view"][key])
                self.assertEqual(self.approved_token, current["contract_token"])
        self.env["FAILURE_ROUTING_DIAGNOSIS"] = "unknown"
        self.drive("--resume-paused", "--retry-failed-stage", "--job-retry-token", token, codes=(2,))
        calls = self.trace("investigate_stuck")
        self.assertEqual(2, len(calls))
        for row in calls:
            self.assertEqual("read-only", row["argv"][row["argv"].index("--sandbox") + 1])
        packets = [row["handoff"]["builder_failure"] for row in calls]
        self.assertEqual(packets[0]["failure_id"], packets[1]["failure_id"])
        self.assertEqual(packets[0]["binding"], packets[1]["binding"])
        self.assertEqual(1, len(self.trace("terra")))
        self.assertFalse((self.project / "main.py").exists())
        self.assertEqual(self.approved_token, self.status()["contract_token"])
        before = self.call_order()
        self.drive("--resume-paused", "--retry-failed-stage", "--job-retry-token", token, codes=(2,))
        self.assertEqual(before, self.call_order())

    def test_fresh_execution_classification_completes_through_normal_builder_and_checks(self):
        self.seed()
        self.env.update(BUILD_AUDIT_FAULT="no_change", FAILURE_ROUTING_DIAGNOSIS="stale")
        self.drive(codes=(2,))
        held = self.status()
        self.assertEqual("PAUSED_BUILDER_CLASSIFICATION", held["status"])
        need = held["view"]["needs"]
        token = need["job_retry_token"]
        retained = {path: Path(path).read_bytes() for path in need["retained_evidence"]}
        self.assertTrue(retained)
        self.assertEqual(1, len(self.trace("terra")))
        self.assertFalse((self.project / "main.py").exists())

        self.env.pop("BUILD_AUDIT_FAULT")
        self.env["FAILURE_ROUTING_DIAGNOSIS"] = "execution"
        self.drive("--resume-paused", "--retry-failed-stage", "--job-retry-token", token, codes=(0,))
        completed = self.status()
        self.assertEqual("TASK_COMPLETE", completed["status"])
        self.assertTrue(completed["completion_current"])
        self.assertEqual(self.approved_token, completed["contract_token"])
        self.assertEqual(retained, {path: Path(path).read_bytes() for path in retained})

        investigators = self.trace("investigate_stuck")
        self.assertEqual(2, len(investigators))
        for row in investigators:
            self.assertEqual("read-only", row["argv"][row["argv"].index("--sandbox") + 1])
        first_packet, fresh_packet = [row["handoff"]["builder_failure"] for row in investigators]
        self.assertEqual(first_packet["failure_id"], fresh_packet["failure_id"])
        self.assertEqual(first_packet["binding"], fresh_packet["binding"])
        fresh_diagnosis = self.trace("investigate_stuck", "diagnosis")[-1]["report"]
        self.assertEqual(fresh_packet["failure_id"], fresh_diagnosis["failure_id"])
        self.assertEqual("execution", fresh_diagnosis["failure_class"])
        builders = self.trace("terra")
        self.assertEqual(2, len(builders))
        self.assertTrue(all(row["model"] == "gpt-6-luna" for row in builders))
        calls = self.trace()
        second_investigator = calls.index(investigators[1])
        second_builder = calls.index(builders[1])
        self.assertLess(second_investigator, second_builder)
        fresh_stages = [row["stage"] for row in calls[second_builder + 1 :]]
        self.assertIn("sol", fresh_stages)
        self.assertIn("astra_review", fresh_stages)
        oracle = subprocess.run([sys.executable, "main.py"], cwd=self.project, capture_output=True, text=True)
        self.assertEqual(0, oracle.returncode)
        self.assertEqual("hello\n", oracle.stdout)

    def test_a_spent_time_limit_holds_the_retry_under_its_raise_instead_of_reoffering_it(self):
        # #1949: with the active-time limit spent, the authorized retry stopped before the
        # Investigator launched, was recorded as a failed classification, and the same retry was
        # named again: a loop a client taking only the named action could not leave.
        self.seed(planner_extra=["--max-seconds", "1000"])
        self.env.update(BUILD_AUDIT_FAULT="no_change", FAILURE_ROUTING_DIAGNOSIS="stale")
        self.drive(codes=(2,))
        held = self.status()
        self.assertEqual("PAUSED_BUILDER_CLASSIFICATION", held["status"])
        token = held["view"]["needs"]["job_retry_token"]
        # The fake clock: record the run's active time past its 1,000 s budget at this pause.
        path = self.run / "state.json"
        saved = json.loads(path.read_text())
        saved["active_seconds"] = 1001
        path.write_text(json.dumps(saved, indent=2))

        self.env.pop("BUILD_AUDIT_FAULT")
        self.env["FAILURE_ROUTING_DIAGNOSIS"] = "execution"
        self.drive("--resume-paused", "--retry-failed-stage", "--job-retry-token", token, codes=(2,))
        limited = self.status()
        need = limited["view"]["needs"]
        # The time limit holds the run, under AutoResolver's request for that limit, not the retry.
        self.assertEqual("PAUSED_TIME_LIMIT", need.get("pause_origin"), need)
        self.assertNotEqual("retry_job", need.get("kind"))
        self.assertNotIn("job_retry_token", need)
        self.assertIn("--max-seconds", json.dumps(need))
        self.assertEqual(1, len(self.trace("investigate_stuck")))
        self.assertEqual(self.approved_token, limited["contract_token"])

        self.drive("--resume-paused", "--max-seconds", "5000", codes=(0,))
        completed = self.status()
        self.assertEqual("TASK_COMPLETE", completed["status"], completed["view"]["needs"])
        investigators = self.trace("investigate_stuck")
        self.assertEqual(2, len(investigators))
        self.assertEqual(
            investigators[0]["handoff"]["builder_failure"]["failure_id"],
            investigators[1]["handoff"]["builder_failure"]["failure_id"],
        )
        self.assertEqual(2, len(self.trace("terra")))

    def test_retry_preserves_the_existing_report_failure_limit(self):
        self.seed()
        self.env.update(BUILD_AUDIT_FAULT="no_change", FAILURE_ROUTING_DIAGNOSIS="stale")
        self.drive(codes=(2,))
        first = self.status()["view"]["needs"]
        token = first["job_retry_token"]
        self.drive("--resume-paused", "--retry-failed-stage", "--job-retry-token", token, codes=(2,))
        need = self.status()["view"]["needs"]
        self.assertEqual(
            {"used": 2, "limit": first["classification_attempts"]["limit"]}, need["classification_attempts"]
        )
        self.assertEqual(2, len(self.trace("investigate_stuck")))
        self.assertEqual("resume", need["kind"])
        self.assertTrue(need["edit_required"])
        self.assertNotIn("job_retry_token", need)
        self.assertIn("failure or repair limits", need["recovery_hint"])
        before = self.call_order()
        self.drive("--resume-paused", "--retry-failed-stage", "--job-retry-token", token, codes=(2,))
        self.drive("--resume-paused", codes=(2,))
        self.assertEqual(before, self.call_order())
        self.assertEqual(1, len(self.trace("terra")))
        self.assertEqual(self.approved_token, self.status()["contract_token"])

        self.assertEqual("--edit-goal FILE", need["action"])
        self.assertIsNone(need.get("feedback_action"))
        held = self.status()
        retained = {path: Path(path).read_bytes() for path in first["retained_evidence"]}
        body = json.loads((self.root / "plan.json").read_text())["contract"]
        body["intended_outcome"] = "Deliver the approved hello CLI after reviewing the failed classification"
        edited = self.root / "edited-goal.json"
        edited.write_text(json.dumps(body))
        self.drive("--edit-goal", str(edited), codes=(0,))
        corrected = self.status()
        self.assertNotEqual(self.approved_token, corrected["contract_token"])
        self.assertNotEqual("TASK_COMPLETE", corrected["status"])
        self.assertFalse(corrected["completion_current"])
        self.assertEqual(before, self.call_order())
        self.assertEqual(held["view"]["usage"], corrected["view"]["usage"])
        self.assertFalse((self.project / "main.py").exists())
        self.assertEqual(retained, {path: Path(path).read_bytes() for path in retained})
        self.call(
            "autoplanner",
            ["--run-dir", str(self.run), "--approve-goal", self.approved_token, "--no-chat"],
            codes=(2,),
        )
        self.assertEqual(before, self.call_order())

    def test_changed_source_refuses_the_classification_token_without_a_call(self):
        self.seed()
        self.env.update(BUILD_AUDIT_FAULT="no_change", FAILURE_ROUTING_DIAGNOSIS="stale")
        self.drive(codes=(2,))
        token = self.status()["view"]["needs"]["job_retry_token"]
        before = self.call_order()
        (self.project / "unreviewed.py").write_text("print('unexpected change')\n")
        result = self.drive("--resume-paused", "--retry-failed-stage", "--job-retry-token", token, codes=(2,))
        self.assertIn("identity or evidence changed", result.stderr)
        self.assertEqual(before, self.call_order())
        self.assertEqual("print('unexpected change')\n", (self.project / "unreviewed.py").read_text())
        self.assertEqual(self.approved_token, self.status()["contract_token"])

    def test_failed_classification_applies_the_same_retained_resolver_proposal_only_after_fresh_diagnosis(self):
        self.seed()
        self.env.update(
            BUILD_AUDIT_FAULT="retry_success",
            FAILURE_ROUTING_DIAGNOSIS="stale",
            FAILURE_ROUTING_RETAINED_REWORK="1",
        )
        self.drive(codes=(2,))
        held = self.status()
        self.assertEqual("PAUSED_BUILDER_CLASSIFICATION", held["status"])
        need = held["view"]["needs"]
        self.assertEqual("retry_job", need["kind"])
        self.assertTrue(need["resolver_proposal_pending"])
        token = need["job_retry_token"]
        self.assertTrue(token.startswith("bc:"))
        self.assertEqual(1, len(self.trace("terra")))
        self.assertEqual(1, len(self.trace("astra_resolve")))
        proposal = self.trace("astra_resolve", "retained_rework")[0]["report"]
        self.assertEqual("REWORK", proposal["status"])
        original = self.trace("terra")[0]["handoff"]["current_task"]
        self.assertEqual(original["id"], held["current_task"]["id"])
        self.assertNotEqual(proposal["next_objective"], held["current_task"]["objective"])
        self.assertFalse((self.project / "main.py").exists())
        self.assertEqual(self.approved_token, held["contract_token"])
        resolver_output = Path(need["resolver_proposal_output"])
        self.assertTrue(resolver_output.is_relative_to(self.run))
        self.assertEqual(proposal, json.loads(resolver_output.read_text()))
        resolver_argv = self.trace("astra_resolve")[0]["argv"]
        leaves = [
            resolver_output.with_suffix(suffix)
            for suffix in (".json", ".jsonl", ".before.json", ".after.json", ".diff")
        ]
        leaves.append(Path(resolver_argv[resolver_argv.index("--output-schema") + 1]))
        self.assertTrue(all(str(path) in need["retained_evidence"] for path in leaves))
        pins = {path: path.read_bytes() for path in leaves}
        before_calls = self.call_order()
        for extra in (
            ("--resume-paused",),
            ("--resume-paused", "--retry-failed-stage", "--job-retry-token", "bc:forged"),
        ):
            self.drive(*extra, codes=(2,))
            current = self.status()
            self.assertEqual(before_calls, self.call_order())
            self.assertEqual(held["current_task"], current["current_task"])
            self.assertEqual(held["view"]["usage"], current["view"]["usage"])
            self.assertEqual(need, current["view"]["needs"])
            self.assertEqual(pins, {path: path.read_bytes() for path in leaves})
        self.env["FAILURE_ROUTING_DIAGNOSIS"] = "execution"
        self.drive("--resume-paused", "--retry-failed-stage", "--job-retry-token", token, codes=(0,))
        finished = self.status()
        self.assertEqual("TASK_COMPLETE", finished["status"])
        self.assertTrue(finished["completion_current"])
        self.assertEqual(self.approved_token, finished["contract_token"])
        self.assertEqual(1, len(self.trace("astra_resolve")), "The saved proposal must not be reissued")
        self.assertEqual(pins, {path: path.read_bytes() for path in leaves})
        calls = self.trace("investigate_stuck")
        self.assertEqual(2, len(calls))
        packets = [row["handoff"]["builder_failure"] for row in calls]
        self.assertEqual(packets[0]["binding"], packets[1]["binding"])
        self.assertEqual(packets[0]["failure_id"], packets[1]["failure_id"])
        self.assertEqual("astra_review", packets[0]["record"]["stage"])
        diagnoses = self.trace("investigate_stuck", "diagnosis")
        self.assertNotEqual(packets[0]["failure_id"], diagnoses[0]["report"]["failure_id"])
        self.assertEqual(packets[1]["failure_id"], diagnoses[1]["report"]["failure_id"])
        for row in calls:
            self.assertEqual("read-only", row["argv"][row["argv"].index("--sandbox") + 1])
        builders = self.trace("terra")
        self.assertEqual(["gpt-6-luna", "gpt-6-luna"], [row["model"] for row in builders])
        assigned = builders[1]["handoff"]["current_task"]
        self.assertNotEqual(original["id"], assigned["id"])
        self.assertEqual(proposal["next_objective"], assigned["objective"])
        repair = builders[1]["handoff"]["repair_plan"]
        self.assertEqual(str(resolver_output), repair["output"])
        self.assertEqual(proposal["diagnosis"], repair["diagnosis"])
        self.assertTrue(self.trace("sol"), "Completion needs a fresh independent Tester")
        oracle = subprocess.run(
            [sys.executable, "main.py"], cwd=self.project, capture_output=True, text=True, check=False
        )
        self.assertEqual(0, oracle.returncode, oracle.stderr)
        self.assertEqual("hello\n", oracle.stdout)


class ClassificationOfferTests(unittest.TestCase):
    def state(self):
        task = {"id": "T1", "milestone_id": "M1", "objective": "Keep the approved task"}
        bound = {
            "task_id": "T1",
            "task_digest": recovery.util.digest(task),
            "contract_hash": "C1",
            "contract_revision": 1,
            "source_revision": "source-1",
            "output": "builder.json",
            "evidence_hashes": {"builder.json": "hash"},
        }
        request = {
            "identity": "builder-failure:incident",
            "mode": "builder_failure",
            "stage": "terra",
            "status": "PAUSED_BUILDER_CLASSIFICATION",
            "reason": "Classify the retained failure",
            "requested_at": "2026-10-10T00:00:00+00:00",
            "failure_evidence": {"failure_id": "incident", "binding": bound},
        }
        state = {
            "version": 3,
            "run_dir": "/owned/run",
            "status": "PAUSED_BUILDER_CLASSIFICATION",
            "stop_reason": "The Investigator could not finish",
            "current_task": task,
            "goal_contract": {"hash": "C1", "revision": 1},
            "settings": {"roles": {"terra": {"model": "builder"}}, "report_repair": {"max_attempts": 2}},
            "stages": [],
        }
        history = {
            "identity": request["identity"],
            "outcome": "investigation_failed",
            "finished_at": "2026-10-10T00:01:00+00:00",
            "trigger": "builder_failure",
            "milestone_key": recovery.policy.key(state),
        }
        state["stuck_investigations"] = [history]
        state["builder_failure_hold"] = {
            "status": state["status"],
            "failure_id": "incident",
            "binding": copy.deepcopy(bound),
            "classification_retry": {
                "request": request,
                "history_index": 0,
                "history": copy.deepcopy(history),
                "history_prefix": [],
                "attempts": [],
                "pins": {},
                "report_repair": None,
            },
        }
        return state

    def test_prelaunch_failure_uses_the_failed_request_without_inventing_an_attempt(self):
        state = self.state()
        before = copy.deepcopy(state)
        need = recovery.offer(state)
        self.assertEqual("retry_job", need["kind"])
        self.assertIsNone(need["attempt_id"])
        self.assertIsNone(need["archive"])
        self.assertEqual(before, state)

    def test_temporary_route_is_derived_but_a_real_route_change_expires_the_token(self):
        state = self.state()
        token = recovery.offer(state)["job_retry_token"]
        state["settings"]["roles"]["stuck_investigator"] = {"model": "derived"}
        self.assertEqual(token, recovery.offer(state)["job_retry_token"])
        state["settings"]["roles"]["terra"]["model"] = "changed builder"
        self.assertNotEqual(token, recovery.offer(state)["job_retry_token"])
        state["run_dir"] = "/another/run"
        self.assertNotEqual(token, recovery.offer(state)["job_retry_token"])

    def test_disabled_investigator_has_no_retry_token_and_refuses_prior_authorization(self):
        state = self.state()
        token = recovery.offer(state)["job_retry_token"]
        state["settings"]["stuck_investigation"] = {"max_calls_per_run": 0}
        before = copy.deepcopy(state)
        need = recovery.offer(state)
        self.assertTrue(need["edit_required"])
        self.assertNotIn("job_retry_token", need)
        self.assertIn("disabled", need["recovery_hint"])
        runtime = SimpleNamespace(write_json=Mock())
        with self.assertRaisesRegex(ValueError, "allowance is exhausted"):
            recovery.authorize(runtime, state, Path("/owned/run"), Path("/owned"), token, supersede_job=Mock())
        self.assertEqual(before, state)
        runtime.write_json.assert_not_called()

    def test_disabled_investigator_cannot_consume_an_unlaunched_grant(self):
        state = self.state()
        request = copy.deepcopy(state["builder_failure_hold"]["classification_retry"]["request"])
        request["classification_retry"] = {
            "token": "bc:already-authorized",
            "run_dir": state["run_dir"],
            "configuration": recovery.configuration(state),
            "consumed": False,
        }
        state["stuck_investigation"] = request
        state["stuck_investigations"][-1]["outcome"] = "investigating"
        state["settings"]["stuck_investigation"] = {"max_calls_per_run": 0}
        before = copy.deepcopy(state)
        record = {"stage": "investigate_stuck"}
        with patch.object(recovery.failure, "guard"), self.assertRaises(recovery.util.Paused):
            recovery.admit(state, record, Path("/owned"))
        self.assertEqual(before, state)
        self.assertNotIn("classification_retry_token", record)

    def test_unfinished_or_changed_ownership_has_no_retry_offer(self):
        for key, value in (
            ("active_stage", {"stage": "investigate_stuck"}),
            ("uncertain_artifacts", ["provider output"]),
            ("stuck_investigation", {"identity": "other"}),
            ("pending_builder_failure", {"identity": "other"}),
        ):
            with self.subTest(key=key):
                state = self.state()
                state[key] = value
                self.assertIsNone(recovery.offer(state))
        state = self.state()
        state["stuck_investigations"][0]["outcome"] = "investigating"
        self.assertIsNone(recovery.offer(state))
        state = self.state()
        state["current_task"]["objective"] = "A different task"
        self.assertIsNone(recovery.offer(state))

    def test_a_spent_classification_allowance_offers_a_plan_correction(self):
        state = self.state()
        limit = recovery.policy.classification_limit(state)
        for i in range(1, limit):
            state["stuck_investigations"].append(
                {
                    "identity": f"failed-{i}",
                    "trigger": "builder_failure",
                    "milestone_key": recovery.policy.key(state),
                    "outcome": "investigation_failed",
                }
            )
        before = copy.deepcopy(state)
        need = recovery.offer(state)
        self.assertEqual({"used": limit, "limit": limit}, need["classification_attempts"])
        self.assertTrue(need["edit_required"])
        self.assertNotIn("job_retry_token", need)
        self.assertEqual(before, state)

    def test_retained_resolver_proposal_is_displayed_without_assignment(self):
        state = self.state()
        request = state["builder_failure_hold"]["classification_retry"]["request"]
        request["resolver_continuation"] = {
            "value": {"next_objective": "Unassigned proposal"},
            "record": {"output": "resolver.json"},
        }
        before = copy.deepcopy(state)
        need = recovery.offer(state)
        self.assertTrue(need["resolver_proposal_pending"])
        self.assertEqual("resolver.json", need["resolver_proposal_output"])
        self.assertEqual(before, state)

    def test_changed_report_repair_response_refuses_before_authorization(self):
        state = self.state()
        with tempfile.TemporaryDirectory() as root:
            response = Path(root) / "response.txt"
            response.write_text("original")
            pending = {
                "original": {"stage": "investigate_stuck", "started_at": "2026-10-10T00:00:00+00:00"},
                "attempts": 2,
                "latest_rejected": {"rejected": True},
                "pins": {str(response): recovery.util.file_hash(response)},
            }
            state["pending_report_repair"] = pending
            state["builder_failure_hold"]["classification_retry"]["report_repair"] = copy.deepcopy(pending)
            token = recovery.offer(state)["job_retry_token"]
            response.write_text("changed")
            runtime = SimpleNamespace(assert_stage_stopped=Mock(), intervention_metadata=Mock(), write_json=Mock())
            supersede = Mock()
            before = copy.deepcopy(state)
            with (
                patch.object(recovery.failure, "guard"),
                self.assertRaisesRegex(ValueError, "report-repair inputs changed"),
            ):
                recovery.authorize(runtime, state, Path(root) / "run", Path(root), token, supersede_job=supersede)
            self.assertEqual(before, state)
            supersede.assert_not_called()
            runtime.write_json.assert_not_called()

    def authorized(self, root):
        state = self.state()
        state["run_dir"] = str(root / "run")
        output = root / "investigator.json"
        response = root / "repair-response.txt"
        output.write_text("retained failed report")
        response.write_text("retained repair response")
        attempt = {
            "stage": "investigate_stuck",
            "attempt_id": "failed-investigator",
            "started_at": "2026-10-10T00:00:01+00:00",
            "finished_at": "2026-10-10T00:00:02+00:00",
            "exit_code": 1,
            "output": str(output),
            "rejected": True,
        }
        context = state["builder_failure_hold"]["classification_retry"]
        leaves = {"investigator": output, "repair": response}
        resolver_record = {}
        for key in ("output", "events", "schema", "before_ref", "after_ref", "diff_ref"):
            leaf = root / ("resolver-" + key)
            leaf.write_text("retained Resolver " + key)
            resolver_record[key] = str(leaf)
            leaves["resolver_" + key] = leaf
        context["request"]["resolver_continuation"] = {
            "value": {"next_objective": "Retained proposal"},
            "record": resolver_record,
        }
        state["stages"] = [attempt]
        pending = {
            "original": copy.deepcopy(attempt),
            "attempts": 2,
            "latest_rejected": {"rejected": True},
            "pins": {str(response): recovery.util.file_hash(response)},
        }
        state["pending_report_repair"] = pending
        recovery.retain(state, context["request"])
        token = recovery.offer(state)["job_retry_token"]
        runtime = SimpleNamespace(
            assert_stage_stopped=Mock(),
            intervention_metadata=Mock(return_value={"pending_count": 0, "inbox_error": None}),
            write_json=Mock(),
        )
        with patch.object(recovery.failure, "guard"):
            recovery.authorize(runtime, state, root / "run", root, token, supersede_job=Mock())
        # Normal preparation derives this role; it does not invalidate the grant.
        state["settings"]["roles"]["stuck_investigator"] = {"model": "derived"}
        return state, leaves

    def test_late_retained_artifact_change_refuses_before_grant_consumption(self):
        for leaf in (
            "investigator",
            "repair",
            "resolver_output",
            "resolver_events",
            "resolver_schema",
            "resolver_before_ref",
            "resolver_after_ref",
            "resolver_diff_ref",
        ):
            with self.subTest(leaf=leaf), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                state, leaves = self.authorized(root)
                control = copy.deepcopy(state)
                control_record = {"stage": "investigate_stuck"}
                with patch.object(recovery.failure, "guard"):
                    recovery.admit(control, control_record, root)
                self.assertTrue(control["stuck_investigation"]["classification_retry"]["consumed"])
                self.assertIn("classification_retry_token", control_record)
                leaves[leaf].write_text("edited after authorization")
                before = copy.deepcopy(state)
                record = {"stage": "investigate_stuck"}
                with patch.object(recovery.failure, "guard"), self.assertRaises(recovery.util.Paused):
                    recovery.admit(state, record, root)
                self.assertEqual(before, state)
                self.assertNotIn("classification_retry_token", record)
                retained_pins = copy.deepcopy(state["stuck_investigation"]["classification_retry"]["retained_pins"])
                stuck.abandon(state, "Late admission refused")
                context = state["builder_failure_hold"]["classification_retry"]
                self.assertEqual([], context["attempts"])
                self.assertEqual(retained_pins, context["pins"])
                token = recovery.offer(state)["job_retry_token"]
                runtime = SimpleNamespace(write_json=Mock())
                before = copy.deepcopy(state)
                with (
                    patch.object(recovery.failure, "guard"),
                    self.assertRaisesRegex(ValueError, "Retained classification artifacts changed"),
                ):
                    recovery.authorize(runtime, state, root / "run", root, token, supersede_job=Mock())
                self.assertEqual(before, state)
                runtime.write_json.assert_not_called()

    def test_late_history_attempt_or_request_change_refuses_before_grant_consumption(self):
        for changed in (
            "history",
            "attempt",
            "request",
            "resolver_proposal",
            "retained_attempt_after_second_grant",
            "history_before_second_grant",
            "history_after_second_grant",
            "attempt_before_second_authorization",
        ):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                state, _ = self.authorized(root)
                runtime = SimpleNamespace(
                    assert_stage_stopped=Mock(),
                    intervention_metadata=Mock(return_value={"pending_count": 0, "inbox_error": None}),
                    write_json=Mock(),
                )
                if changed == "history_before_second_grant":
                    token = state["stuck_investigation"]["classification_retry"]["token"]
                    state["stuck_investigations"][0]["finished_at"] = "2026-10-10T00:01:01+00:00"
                    stuck.abandon(state, "Prelaunch failure without a new physical attempt")
                    before = copy.deepcopy(state)
                    self.assertIsNone(recovery.offer(state))
                    with (
                        patch.object(recovery.failure, "guard"),
                        self.assertRaisesRegex(ValueError, "allowance is exhausted"),
                    ):
                        recovery.authorize(runtime, state, root / "run", root, token, supersede_job=Mock())
                    self.assertEqual(before, state)
                    runtime.write_json.assert_not_called()
                    continue
                if changed in (
                    "retained_attempt_after_second_grant",
                    "history_after_second_grant",
                    "attempt_before_second_authorization",
                ):
                    stuck.abandon(state, "Prelaunch failure without a new physical attempt")
                    token = recovery.offer(state)["job_retry_token"]
                    if changed == "attempt_before_second_authorization":
                        state["stages"][0]["rejection_reason"] = "Changed earlier failed attempt"
                        before = copy.deepcopy(state)
                        with (
                            patch.object(recovery.failure, "guard"),
                            self.assertRaisesRegex(ValueError, "Retained failed Investigator attempts changed"),
                        ):
                            recovery.authorize(runtime, state, root / "run", root, token, supersede_job=Mock())
                        self.assertEqual(before, state)
                        runtime.write_json.assert_not_called()
                        continue
                    with patch.object(recovery.failure, "guard"):
                        recovery.authorize(runtime, state, root / "run", root, token, supersede_job=Mock())
                    control = copy.deepcopy(state)
                    with patch.object(recovery.failure, "guard"):
                        recovery.admit(control, {"stage": "investigate_stuck"}, root)
                    self.assertTrue(control["stuck_investigation"]["classification_retry"]["consumed"])
                if changed in ("history", "history_after_second_grant"):
                    state["stuck_investigations"][0]["finished_at"] = "2026-10-10T00:01:01+00:00"
                elif changed in ("attempt", "retained_attempt_after_second_grant"):
                    state["stages"][0]["rejection_reason"] = "Different failed attempt context"
                elif changed == "request":
                    state["stuck_investigation"]["reason"] = "Changed fresh request"
                else:
                    state["stuck_investigation"]["resolver_continuation"]["value"]["next_objective"] = (
                        "Changed proposal"
                    )
                before = copy.deepcopy(state)
                record = {"stage": "investigate_stuck"}
                with patch.object(recovery.failure, "guard"), self.assertRaises(recovery.util.Paused):
                    recovery.admit(state, record, root)
                self.assertEqual(before, state)
                self.assertNotIn("classification_retry_token", record)

    def drive_into(self, state, status):
        """Drive the authorized retry into a boundary stop raised before any stage launches."""

        def boundary(_state):
            raise recovery.util.Paused(status, "Saved active-time limit reached at stage boundary")

        dispatch = Mock()
        with self.assertRaises(recovery.util.Paused) as stopped:
            stuck.drive(
                state,
                dispatch,
                before=boundary,
                active=lambda value: value.get("status") == "RUNNING",
                skip=object(),
                paused=recovery.util.Paused,
                investigate=True,
            )
        dispatch.assert_not_called()
        return stopped.exception

    def test_a_boundary_limit_before_launch_keeps_the_classification_queued(self):
        # #1949: the saved active-time limit was spent, so each authorized retry stopped before
        # the Investigator launched, was recorded as a failed classification and offered again.
        for status in sorted(stuck.BOUNDARY_LIMITS):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temporary:
                state, _ = self.authorized(Path(temporary))
                state["settings"]["roles"][stuck.ROUTE] = {"model": "derived"}
                request = copy.deepcopy(state["stuck_investigation"])
                history = copy.deepcopy(state["stuck_investigations"])
                error = self.drive_into(state, status)
                self.assertEqual(status, error.status)
                self.assertNotIn("could not finish", str(error))
                self.assertEqual(request, state["stuck_investigation"])
                self.assertEqual(history, state["stuck_investigations"])
                self.assertEqual("investigating", state["stuck_investigations"][-1]["outcome"])
                self.assertNotIn("builder_failure_hold", state)
                self.assertNotIn(stuck.ROUTE, state["settings"]["roles"])
                self.assertEqual("investigate_stuck", state["next_stage"])
                # The grant is still unspent: raising the bound and resuming launches it.
                record = {"stage": "investigate_stuck"}
                with patch.object(recovery.failure, "guard"):
                    recovery.admit(state, record, Path(temporary))
                self.assertEqual(request["classification_retry"]["token"], record["classification_retry_token"])

    def test_any_other_stop_before_launch_still_restores_the_classification_hold(self):
        with tempfile.TemporaryDirectory() as temporary:
            state, _ = self.authorized(Path(temporary))
            error = self.drive_into(state, "PAUSED_STALE_VALIDATION")
            self.assertEqual("PAUSED_BUILDER_CLASSIFICATION", error.status)
            self.assertNotIn("stuck_investigation", state)
            self.assertEqual("investigation_failed", state["stuck_investigations"][-1]["outcome"])

    def test_older_boundary_stops_before_launch_do_not_spend_the_allowance(self):
        # #1949: runs saved before the fix recorded each boundary stop as a failed classification
        # until the allowance ran out, though no Investigator had started.
        state = self.state()
        limit = recovery.policy.classification_limit(state)
        stopped = [
            {
                "identity": f"stopped-{i}",
                "trigger": "builder_failure",
                "milestone_key": recovery.policy.key(state),
                "outcome": "investigation_failed",
                "error": "Saved active-time limit reached at stage boundary",
                "requested_at": f"2026-10-09T01:0{i}:00+00:00",
                "finished_at": f"2026-10-09T01:0{i}:01+00:00",
            }
            for i in range(limit)
        ]
        state["stuck_investigations"][:0] = stopped
        context = state["builder_failure_hold"]["classification_retry"]
        context.update(history_index=limit, history_prefix=copy.deepcopy(stopped))
        need = recovery.offer(state)
        self.assertEqual("retry_job", need["kind"])
        self.assertEqual({"used": 1, "limit": limit}, need["classification_attempts"])
        # One that started an Investigator, or stopped for any other reason, still counts.
        state["stages"].append({"stage": "investigate_stuck", "started_at": "2026-10-09T01:00:00.5+00:00"})
        state["stuck_investigations"][1]["error"] = "Classification retry admission changed; no provider launched"
        self.assertEqual(3, len(recovery.failure.classified(state)))

    def test_older_failed_hold_names_a_plan_correction_instead_of_plain_resume(self):
        state = self.state()
        state["builder_failure_hold"].pop("classification_retry")
        state["stop_reason"] = "Failure\n(The Investigator could not finish: provider setup failed)"
        need = run_view.needs(state)
        self.assertTrue(need["edit_required"])
        self.assertIn("--edit-goal", need["action"])
        self.assertNotIn("job_retry_token", need)


if __name__ == "__main__":
    unittest.main()
