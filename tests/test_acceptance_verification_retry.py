"""A completed acceptance writer is not paid again for a local proof failure."""

import copy
import json
import subprocess
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autocode_acceptance_tests as acceptance
import autocode_acceptance_tests_job as job
import autocode_acceptance_verification_retry as retry
import autocode_command_receipt as receipts
import autocode_contract_identity as identity
import autocode_progress_view as progress_view
import autocode_run_view as run_view
import autocode_source_scope as source_scope
import autocode_stuck_job as stuck
import autocode_util as util

from tests.test_stuck_job import report, state_for

# The #3927 incident's Investigator result for the Tester's rejected answer, and its advice.
IDENTITY = "write_acceptance_tests:PAUSED_INVALID_OUTPUT"
GUIDANCE = "Keep the public import and move it into the test body"
EXHAUSTED_SENTENCE = "AutoCode re-checked the saved tests 1 of 1 allowed times without a new Tester attempt."


class AcceptanceVerificationRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.workspace, self.run = root / "project", root / "run"
        self.workspace.mkdir()
        self.run.mkdir()
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.workspace),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.test",
                "commit",
                "--allow-empty",
                "-qm",
                "base",
            ],
            check=True,
        )
        (self.workspace / "tests").mkdir()
        (self.workspace / "tests/test_feature.py").write_text("# independently written test\n")
        body = {
            "open_blocking_questions": [],
            "acceptance_criteria": [{"id": "AC1", "criterion": "answer", "verification_method": "test: test_answer"}],
        }
        contract = {"task_id": "T1", "revision": 1, "body": body}
        contract["hash"] = util.digest(contract)
        approval = {"kind": "goal_approval", "actor": "user_cli", "token": identity.token(contract)}
        contract.update(approval_status="approved", approval_event=approval)
        self.state = {
            "workspace": str(self.workspace),
            "status": "RUNNING",
            "settings": {"spec_first": True},
            "goal_contract": contract,
            "user_events": [approval],
            "current_task": {"id": "T1"},
            "next_stage": "builder",
            "stages": [],
            "history": [],
        }
        acceptance.route(self.state)
        self.value = {
            "summary": "Test the answer",
            "tests": [{"case_id": "AC1", "file": "tests/test_feature.py", "test_name": "test_answer"}],
            "already_passing": [],
            "cannot_write": [],
            "tests_run": [],
        }
        self.record = {
            "stage": acceptance.STAGE,
            "contract_hash": contract["hash"],
            "task_id": "T1",
            "accounted": True,
            "exit_code": 0,
            "source_revision": source_scope.snapshot(self.workspace, self.state)["revision"],
            "changed_files": ["tests/test_feature.py"],
            "engine": "opencode",
            "output_mode": "opencode_events",
        }
        for key in retry.ARTIFACTS:
            path = self.run / (key + ".json")
            util.atomic_json(path, self.value if key == "output" else job.SCHEMA if key == "schema" else {})
            self.record[key] = str(path)
        event = lambda kind, **part: {
            "type": kind,
            "sessionID": "fixture",
            "part": {"id": kind, "messageID": "reply", **part},
        }
        Path(self.record["events"]).write_text(
            "\n".join(
                json.dumps(row)
                for row in [
                    event("text", text=json.dumps(self.value)),
                    event(
                        "step_finish", reason="stop", tokens={"input": 1, "output": 1, "cache": {"read": 0, "write": 0}}
                    ),
                ]
            )
        )
        self.record["metrics"] = {
            "request_context": {
                "accounting": {
                    "event_file": {
                        "path": self.record["events"],
                        "sha256": util.file_hash(self.record["events"]),
                    }
                }
            }
        }
        self.state["stages"] = [self.record]
        self.state["history"] = [self.record]
        self.result = {
            "exit_code": 0,
            "results": {
                "passed": ["tests.test_feature.Feature.test_answer"],
                "failed": [],
                "skipped": [],
                "total": 1,
                "complete": True,
            },
        }
        self.reference = Mock(
            side_effect=[
                {"error": "Cannot classify passing tests against the original reference: execution missing"},
                {"verdicts": {"AC1": "fails"}},
            ]
        )
        self.candidate = Mock(return_value=self.result)
        self.runtime = SimpleNamespace(
            load_stage_report=Mock(return_value=self.value),
            write_json=util.atomic_json,
            commit_boundary_candidate=self.commit,
            run_role=Mock(side_effect=AssertionError("model replay")),
        )

    def commit(self, state, candidate, run_dir, workspace):
        state.clear()
        state.update(candidate)
        util.atomic_json(run_dir / "state.json", state)

    def apply(self, stage, state, value, record, workspace, **kwargs):
        job.apply(
            state,
            value,
            record,
            workspace,
            run_tests=self.candidate,
            run_reference=self.reference,
            evidence_dir=self.run / "checks",
        )

    def start_retry(self):
        self.apply(acceptance.STAGE, self.state, self.value, self.record, self.workspace)
        self.assertEqual("RUNNING", self.state["status"])
        self.assertIn(retry.KEY, self.state[acceptance.KEY])
        self.assertFalse(acceptance.receipts(self.state))

    def dispatch(self):
        return retry.dispatch(self.runtime, self.state, acceptance.STAGE, self.workspace, self.run, apply=self.apply)

    def test_retry_status_rechecks_source_once_only_at_an_idle_boundary(self):
        self.start_retry()
        self.state["status"] = "PAUSED_INVALID_OUTPUT"
        with patch.object(source_scope, "snapshot", wraps=source_scope.snapshot) as snapshots:
            view = run_view.view(self.state, workspace=self.workspace, run_dir=self.run)
            self.assertEqual("recheck", view["acceptance_verification_retry"]["next"])
            self.assertEqual(1, snapshots.call_count)
        self.state["active_runner_check"] = {"stage": "verification", "summary": "still running"}
        with patch.object(source_scope, "snapshot", side_effect=AssertionError("active check must not scan source")):
            view = run_view.view(self.state, workspace=self.workspace, run_dir=self.run)
        self.assertEqual("blocked", view["acceptance_verification_retry"]["next"])

    def test_changed_retry_inputs_withhold_the_resume_that_dispatch_refuses(self):
        self.start_retry()
        self.state["status"] = "PAUSED_INVALID_OUTPUT"
        before = copy.deepcopy(self.state)
        paths = {key: Path(self.record[key]).read_bytes() for key in retry.ARTIFACTS}
        for change in (
            "source",
            "task",
            "contract",
            "pin",
            "uncertain",
            "malformed",
            "malformed_investigations",
            "malformed_provenance",
            "active_check",
            "active_stage",
        ):
            with self.subTest(change=change):
                self.state = copy.deepcopy(before)
                (self.workspace / "tests/test_feature.py").write_text("# independently written test\n")
                for key, data in paths.items():
                    Path(self.record[key]).write_bytes(data)
                if change == "source":
                    (self.workspace / "tests/test_feature.py").write_text("# changed source\n")
                elif change == "task":
                    self.state["current_task"]["id"] = "other"
                elif change == "contract":
                    self.state["goal_contract"]["hash"] = "different"
                elif change == "pin":
                    Path(self.record["events"]).write_text("changed evidence\n")
                elif change == "uncertain":
                    self.state["uncertain_artifacts"] = [{"reason": "unknown outcome"}]
                elif change == "malformed":
                    self.state[acceptance.KEY][retry.KEY] = ["invalid"]
                elif change == "malformed_investigations":
                    self.state[acceptance.KEY][retry.KEY]["investigations"] = 42
                elif change == "malformed_provenance":
                    self.state[acceptance.KEY][retry.KEY]["provenance_retry"] = "unknown"
                elif change == "active_check":
                    self.state["active_runner_check"] = {"stage": "verification", "summary": "still running"}
                else:
                    self.state["active_stage"] = {"stage": "write_acceptance_tests"}
                saved = copy.deepcopy(self.state)
                view = run_view.view(self.state, workspace=self.workspace, run_dir=self.run)
                self.assertIsNone(view["next_command"])
                self.assertEqual("blocked", view["recovery"]["category"])
                progress = progress_view.progress(
                    self.state,
                    accepted=(),
                    stage="Tester",
                    needs=view["needs"],
                    retained=view["acceptance_verification_retry"],
                )
                self.assertEqual("Task blocked", progress["headline"])
                self.assertEqual("nothing needed from you", progress["needs_you"])
                self.assertEqual(saved, self.state, "Status must not change retained evidence")
                with self.assertRaises(util.Paused):
                    self.dispatch()
                self.assertEqual(0, self.runtime.run_role.call_count)

    def test_retained_retry_does_not_claim_a_different_next_stage(self):
        self.start_retry()
        self.state["next_stage"] = "builder"
        shown = run_view.view(self.state, workspace=self.workspace, run_dir=self.run)
        self.assertEqual("blocked", shown["acceptance_verification_retry"]["next"])
        with self.assertRaises(util.Paused):
            self.dispatch()
        self.runtime.run_role.assert_not_called()

    def test_blocked_retry_does_not_hide_a_real_requirements_question(self):
        self.start_retry()
        self.state.update(
            status="PAUSED_INVALID_OUTPUT",
            pending_questions=[{"id": "q1", "question": "Which output format do you need?"}],
        )
        view = run_view.view(self.state, workspace=self.workspace, run_dir=self.run)
        self.assertEqual("answer", view["needs"]["kind"])
        self.assertIsNotNone(view["next_command"])
        self.assertNotEqual("blocked", view["recovery"]["category"])

    def test_blocked_retry_does_not_hide_a_retained_attempt_action(self):
        self.start_retry()
        self.state.update(
            status="PAUSED_INVALID_OUTPUT",
            active_stage={"stage": acceptance.STAGE, "iteration": 2, "output": str(self.run / "unfinished.json")},
        )
        view = run_view.view(self.state, workspace=self.workspace, run_dir=self.run)
        self.assertIn("--abandon-stage", view["next_command"])

    def test_local_retry_reaches_builder_without_second_writer_or_accounting(self):
        self.start_retry()
        self.dispatch()
        self.assertEqual("builder", self.state["next_stage"])
        self.assertEqual(1, len(self.state["stages"]))
        self.assertEqual(1, len(self.state["history"]))
        self.runtime.run_role.assert_not_called()
        self.assertEqual(2, self.candidate.call_count)
        self.assertEqual(2, self.reference.call_count)
        self.assertTrue(acceptance.receipts(self.state))
        self.assertNotIn(retry.KEY, self.state[acceptance.KEY])
        archived = self.state[acceptance.KEY]["verification_retries"][0]
        self.assertEqual(1, archived["attempts"])
        self.assertEqual(1, acceptance.verification_attempts(self.state))
        self.assertEqual(1, len(archived["failures"]))

    def test_repeated_failure_exhausts_without_model_replay_or_allowance_reset(self):
        self.reference.side_effect = None
        self.reference.return_value = {"error": "original-reference execution missing"}
        self.start_retry()
        for _ in range(2):
            with self.assertRaises(util.Paused) as stopped:
                self.dispatch()
            self.assertEqual("PAUSED_INVALID_OUTPUT", stopped.exception.status)
        self.assertEqual(2, self.reference.call_count)
        self.assertEqual(1, self.state[acceptance.KEY][retry.KEY]["attempts"])
        self.assertEqual(2, len(self.state[acceptance.KEY][retry.KEY]["failures"]))
        self.assertFalse(acceptance.receipts(self.state))
        self.runtime.run_role.assert_not_called()

    def test_newly_authenticated_inherited_tests_allow_one_local_recheck(self):
        self.value["already_passing"] = [{"case_id": "AC1", "evidence": "Already passes"}]
        util.atomic_json(self.record["output"], self.value)
        self.reference.side_effect = None
        self.reference.return_value = {"error": "original-reference execution missing"}
        self.start_retry()
        with self.assertRaises(util.Paused):
            self.dispatch()
        pending = self.state[acceptance.KEY][retry.KEY]
        self.assertEqual(1, pending["attempts"])
        with patch.object(retry.provenance, "inherited_cases", return_value={"AC1"}):
            self.dispatch()
        archived = self.state[acceptance.KEY]["verification_retries"][-1]
        self.assertEqual(2, archived["attempts"])
        self.assertTrue(archived["provenance_retry"])
        self.assertEqual("builder", self.state["next_stage"])
        self.assertEqual(2, self.reference.call_count)
        self.runtime.run_role.assert_not_called()

    def test_authenticated_recheck_is_spent_even_if_verification_fails(self):
        self.value["already_passing"] = [{"case_id": "AC1", "evidence": "Already passes"}]
        util.atomic_json(self.record["output"], self.value)
        self.reference.side_effect = None
        self.reference.return_value = {"error": "original-reference execution missing"}
        self.start_retry()
        with self.assertRaises(util.Paused):
            self.dispatch()
        pending = self.state[acceptance.KEY][retry.KEY]
        with patch.object(retry.provenance, "inherited_cases", return_value={"AC1"}):
            with self.assertRaises(util.Paused):
                retry.dispatch(
                    self.runtime,
                    self.state,
                    acceptance.STAGE,
                    self.workspace,
                    self.run,
                    apply=Mock(side_effect=ValueError("still broken")),
                )
            self.assertEqual(2, pending["attempts"])
            self.assertTrue(pending["provenance_retry"])
            with self.assertRaises(util.Paused):
                self.dispatch()
        self.runtime.run_role.assert_not_called()

    def test_resumed_local_failure_reaches_investigator_without_another_model_record(self):
        self.state.update(
            version=3, settings={**state_for(workspace=str(self.workspace))["settings"], "spec_first": True}
        )
        self.reference.side_effect = None
        self.reference.return_value = {"error": "original-reference execution missing"}
        self.start_retry()
        dispatched = []

        class ReachedInvestigator(Exception):
            pass

        def dispatch(state, stage):
            dispatched.append(stage)
            if stage == stuck.STAGE:
                raise ReachedInvestigator
            return self.dispatch()

        with self.assertRaises(ReachedInvestigator):
            try:
                stuck.drive(
                    self.state,
                    dispatch,
                    active=lambda state: state["status"] == "RUNNING",
                    skip=object(),
                    paused=util.Paused,
                    investigate=True,
                )
            except util.Paused as error:
                self.fail(f"A newly attempted local check paused without investigation: {error}")
        self.assertEqual([acceptance.STAGE, stuck.STAGE], dispatched)
        self.assertEqual(1, len(self.state["stages"]))
        self.assertEqual(1, self.state[acceptance.KEY][retry.KEY]["attempts"])

        # Merely resuming the exhausted check is still an old pause, not a fresh attempt.
        self.state["next_stage"] = acceptance.STAGE
        dispatched.clear()
        with self.assertRaises(util.Paused):
            stuck.drive(
                self.state,
                dispatch,
                active=lambda state: state["status"] == "RUNNING",
                skip=object(),
                paused=util.Paused,
                investigate=True,
            )
        self.assertEqual([acceptance.STAGE], dispatched)

    def test_stale_source_report_plan_and_task_never_execute(self):
        self.start_retry()
        self.reference.side_effect = None
        self.reference.return_value = {"verdicts": {"AC1": "fails"}}
        original = copy.deepcopy(self.state)
        for fault in ("source", "report", "events", "plan", "task", "uncertain", "pins"):
            with self.subTest(fault=fault):
                self.state = copy.deepcopy(original)
                target = None
                if fault == "source":
                    target = self.workspace / "extra.py"
                elif fault in ("report", "events"):
                    target = Path(self.record["output" if fault == "report" else "events"])
                elif fault == "plan":
                    self.state["goal_contract"]["approval_status"] = "draft"
                elif fault == "task":
                    self.state["current_task"]["id"] = "different"
                elif fault == "pins":
                    self.state[acceptance.KEY][retry.KEY]["pins"] = {}
                else:
                    self.state["uncertain_artifacts"] = ["uncollected"]
                previous = target.read_bytes() if target and target.exists() else None
                if target:
                    target.write_text("changed")
                try:
                    with self.assertRaises(util.Paused):
                        self.dispatch()
                finally:
                    if target:
                        target.write_bytes(previous) if previous is not None else target.unlink()
        self.assertEqual(1, self.reference.call_count)

    def test_new_approved_plan_supersedes_only_the_old_retained_report(self):
        self.start_retry()
        old = copy.deepcopy(self.state["goal_contract"])
        pending = copy.deepcopy(self.state[acceptance.KEY][retry.KEY])
        revised = {"task_id": old["task_id"], "revision": old["revision"] + 1, "body": copy.deepcopy(old["body"])}
        revised["body"]["acceptance_criteria"][0]["criterion"] = "corrected answer"
        revised["hash"] = util.digest(revised)
        approval = {"kind": "goal_approval", "actor": "user_cli", "token": identity.token(revised)}
        revised.update(approval_status="approved", approval_event=approval)
        self.state["contract_history"] = [old]
        self.state["goal_contract"] = revised
        self.state["user_events"].append(approval)
        self.state["current_task"] = {"id": "T2"}
        self.state["next_stage"] = acceptance.STAGE

        paused = copy.deepcopy(self.state)
        paused.update(status="PAUSED_INVALID_OUTPUT", stop_reason="Old acceptance report retained")
        shown = run_view.view(paused, workspace=self.workspace, run_dir=self.run)
        self.assertEqual("tester", shown["acceptance_verification_retry"]["next"])
        self.assertEqual("resume", shown["needs"]["kind"])
        self.assertIn("--resume-paused", shown["next_command"])

        before = copy.deepcopy(self.state)
        self.assertIsNone(self.dispatch(), "the normal dispatch must run a fresh writer")
        self.assertNotIn(retry.KEY, self.state[acceptance.KEY])
        archived = self.state[acceptance.KEY]["verification_retries"][-1]
        for key, value in pending.items():
            self.assertEqual(value, archived[key])
        self.assertEqual(revised["hash"], archived["superseded_by"]["contract_hash"])
        self.assertEqual(before["stages"], self.state["stages"])
        self.assertEqual(before["history"], self.state["history"])
        self.runtime.run_role.assert_not_called()

    def test_superseded_report_restarts_build_loop_before_stage_boundary(self):
        import autocode_build_loop as loop
        import autopilot

        class WriterReached(Exception):
            pass

        # The retained report is retired, but no stage has completed. The
        # next pass must reach a fresh writer before the after-stage hook.
        self.state[acceptance.KEY][retry.KEY] = {"record": {}}
        self.state["next_stage"] = acceptance.STAGE
        self.state["settings"]["roles"] = {}
        runner = SimpleNamespace(
            native_calls=Mock(),
            orchestrator=SimpleNamespace(SKIP=autopilot.SKIP, LoopExit=autopilot.LoopExit),
            write_json=Mock(),
            SCHEMA_DIR=self.run / "schemas",
        )
        args = SimpleNamespace(unit=None, chat=False, pause_after_stage=True)
        after = Mock()

        def drive(current, dispatch, **callbacks):
            return autopilot.drive(current, dispatch, after=after)

        runner.orchestrator.drive = drive

        def supersede(current_runner, current, stage, workspace, run_dir, *, apply):
            current[acceptance.KEY].pop(retry.KEY)
            return None

        with ExitStack() as stack:
            for module, name in (
                (loop.model_preflight, "check"),
                (loop.progressive, "guard_dispatch"),
                (loop.autopilot.builder_failure, "dispatch_guard"),
                (loop.workflow, "dispatch_guard"),
                (loop.autopilot, "admit_validation"),
                (loop.completion_result, "before_review"),
                (loop.launch_inputs, "guard"),
            ):
                stack.enter_context(patch.object(module, name))
            stack.enter_context(patch.object(loop.storage_budget, "conversation_only", return_value=True))
            stack.enter_context(patch.object(loop.planning, "is_planning", return_value=False))
            stack.enter_context(patch.object(loop.autopilot, "revalidate_resolver", return_value=False))
            stack.enter_context(patch.object(loop.acceptance_retry, "dispatch", side_effect=supersede))
            stack.enter_context(patch.object(loop.autopilot, "prepare_request", side_effect=WriterReached))
            with self.assertRaises(WriterReached):
                loop.run(
                    runner, args, self.state, self.run / "state.json", self.run, self.workspace, checkout_owned=True
                )
        after.assert_not_called()

    def test_replacement_cannot_archive_a_retained_report_with_changed_pins(self):
        self.start_retry()
        old = copy.deepcopy(self.state["goal_contract"])
        revised = {"task_id": old["task_id"], "revision": old["revision"] + 1, "body": copy.deepcopy(old["body"])}
        revised["body"]["acceptance_criteria"][0]["criterion"] = "corrected answer"
        revised["hash"] = util.digest(revised)
        approval = {"kind": "goal_approval", "actor": "user_cli", "token": identity.token(revised)}
        revised.update(approval_status="approved", approval_event=approval)
        self.state["contract_history"] = [old]
        self.state["goal_contract"] = revised
        self.state["user_events"].append(approval)
        pending = copy.deepcopy(self.state[acceptance.KEY][retry.KEY])
        Path(self.record["events"]).write_text("changed pinned evidence")
        with self.assertRaises(util.Paused):
            self.dispatch()
        self.assertEqual(pending, self.state[acceptance.KEY][retry.KEY])
        self.runtime.run_role.assert_not_called()

    def test_a_forged_or_unapproved_replacement_cannot_discard_a_retained_report(self):
        self.start_retry()
        old = copy.deepcopy(self.state["goal_contract"])
        revised = {"task_id": old["task_id"], "revision": old["revision"] + 1, "body": copy.deepcopy(old["body"])}
        revised["body"]["acceptance_criteria"][0]["criterion"] = "corrected answer"
        revised["hash"] = util.digest(revised)
        approval = {"kind": "goal_approval", "actor": "user_cli", "token": identity.token(revised)}
        revised.update(approval_status="approved", approval_event=approval)
        self.state["contract_history"] = [old]
        self.state["goal_contract"] = revised
        self.state["user_events"].append(approval)
        original = copy.deepcopy(self.state)
        original_events = Path(self.record["events"]).read_bytes()
        for fault in (
            "draft",
            "old_approval_missing",
            "new_approval_missing",
            "unchanged",
            "uncertain",
            "duplicate_history",
            "approval_order",
            "different_task",
            "active_stage",
            "artifact_changed",
            "pin_changed",
            "value_changed",
        ):
            with self.subTest(fault=fault):
                Path(self.record["events"]).write_bytes(original_events)
                self.state = copy.deepcopy(original)
                if fault == "draft":
                    self.state["goal_contract"]["approval_status"] = "draft"
                elif fault == "old_approval_missing":
                    self.state["user_events"].pop(0)
                elif fault == "new_approval_missing":
                    self.state["user_events"].pop()
                elif fault == "unchanged":
                    same = {
                        "task_id": old["task_id"],
                        "revision": old["revision"] + 1,
                        "body": copy.deepcopy(old["body"]),
                    }
                    same["hash"] = util.digest(same)
                    same_approval = {"kind": "goal_approval", "actor": "user_cli", "token": identity.token(same)}
                    same.update(approval_status="approved", approval_event=same_approval)
                    self.state["goal_contract"] = same
                    self.state["user_events"][-1] = same_approval
                elif fault == "uncertain":
                    self.state["uncertain_artifacts"] = ["pending"]
                elif fault == "duplicate_history":
                    self.state["contract_history"].append(copy.deepcopy(old))
                elif fault == "approval_order":
                    self.state["user_events"].reverse()
                elif fault == "different_task":
                    different = copy.deepcopy(revised)
                    different["task_id"] = "unrelated"
                    different["hash"] = util.digest({key: different[key] for key in ("task_id", "revision", "body")})
                    different_approval = {
                        "kind": "goal_approval",
                        "actor": "user_cli",
                        "token": identity.token(different),
                    }
                    different["approval_event"] = different_approval
                    self.state["goal_contract"] = different
                    self.state["user_events"][-1] = different_approval
                elif fault == "artifact_changed":
                    Path(self.record["events"]).write_text("altered evidence")
                elif fault == "pin_changed":
                    self.state[acceptance.KEY][retry.KEY]["pins"][self.record["events"]] = "0" * 64
                elif fault == "value_changed":
                    self.state[acceptance.KEY][retry.KEY]["value"] = {"changed": True}
                else:
                    self.state["active_stage"] = {"stage": acceptance.STAGE}
                pending = copy.deepcopy(self.state[acceptance.KEY][retry.KEY])
                if fault in ("artifact_changed", "pin_changed", "value_changed"):
                    paused = copy.deepcopy(self.state)
                    paused.update(status="PAUSED_INVALID_OUTPUT", stop_reason="Retained report needs attention")
                    shown = run_view.view(paused, workspace=self.workspace, run_dir=self.run)
                    self.assertEqual("blocked", shown["acceptance_verification_retry"]["next"])
                    self.assertIsNone(shown["next_command"])
                with self.assertRaises(util.Paused):
                    self.dispatch()
                self.assertEqual(pending, self.state[acceptance.KEY][retry.KEY])
        Path(self.record["events"]).write_bytes(original_events)

    def test_uncertain_cleanup_propagates_and_spent_retry_survives(self):
        self.start_retry()
        self.reference.side_effect = receipts.OwnershipUncertain("uncertain command")
        with self.assertRaises(receipts.OwnershipUncertain):
            self.dispatch()
        self.assertEqual(1, self.state[acceptance.KEY][retry.KEY]["attempts"])
        self.assertFalse(acceptance.receipts(self.state))

    def test_bound_investigator_grant_allows_one_check_retry_and_is_never_replenished(self):
        self.reference.side_effect = None
        self.reference.return_value = {"error": "original-reference execution missing"}
        self.start_retry()
        with self.assertRaises(util.Paused):
            self.dispatch()
        self.state["stuck_investigation"] = {
            "identity": "diagnosed-once",
            "in_force": True,
            "stage": acceptance.STAGE,
            "status": "PAUSED_INVALID_OUTPUT",
            **retry.recovery_binding.binding(self.state, acceptance.STAGE),
        }
        for _ in range(2):
            with self.assertRaises(util.Paused):
                self.dispatch()
        pending = self.state[acceptance.KEY][retry.KEY]
        self.assertEqual(2, pending["attempts"])
        self.assertEqual(["diagnosed-once"], pending["investigations"])
        self.assertEqual(3, self.reference.call_count)
        self.runtime.run_role.assert_not_called()

    def test_stale_investigator_advice_cannot_renew_retry(self):
        self.start_retry()
        self.state[acceptance.KEY][retry.KEY]["attempts"] = 1
        self.state["stuck_investigation"] = {
            "identity": "old-diagnosis",
            "in_force": True,
            "stage": acceptance.STAGE,
            "status": "PAUSED_INVALID_OUTPUT",
            "execution_context": "old-inputs",
        }
        with self.assertRaises(util.Paused):
            self.dispatch()
        self.assertEqual(1, self.reference.call_count)

    def test_invalid_saved_attempt_count_never_runs_checks(self):
        self.start_retry()
        for count in (None, "1", True, -1):
            with self.subTest(count=count):
                self.state[acceptance.KEY][retry.KEY]["attempts"] = count
                with self.assertRaises(util.Paused):
                    self.dispatch()
        self.assertEqual(1, self.reference.call_count)

    def test_legacy_resume_uses_pinned_terminal_report_and_keeps_bound(self):
        self.state.update(
            status="PAUSED_ACCEPTANCE_TESTS",
            stop_reason="The runner could not establish the approved pre-build proof: Cannot classify passing tests against the original reference: missing execution",
        )
        self.assertTrue(retry.retain_legacy(self.state, self.workspace))
        self.assertFalse(retry.retain_legacy(self.state, self.workspace))
        self.assertEqual(0, self.state[acceptance.KEY][retry.KEY]["attempts"])
        self.reference.side_effect = None
        self.reference.return_value = {"verdicts": {"AC1": "fails"}}
        self.dispatch()
        self.assertEqual("builder", self.state["next_stage"])

    def test_legacy_modified_events_or_behavioral_pause_are_not_recovered(self):
        self.state.update(status="PAUSED_ACCEPTANCE_TESTS", stop_reason="AC1 already works")
        self.assertFalse(retry.retain_legacy(self.state, self.workspace))
        self.state["stop_reason"] = (
            "The runner could not establish the approved pre-build proof: Cannot classify passing tests against the original reference: missing execution"
        )
        Path(self.record["events"]).write_text("tampered")
        self.assertFalse(retry.retain_legacy(self.state, self.workspace))

    def not_started(self):
        return {
            "command": "python3 -m pytest tests/test_feature.py",
            "exit_code": 1,
            "timed_out": False,
            "results": None,
            "results_expected": True,
            "results_file_written": False,
            "tail": "/usr/bin/python3: No module named pytest",
        }

    def first_runner_not_started_pause(self):
        """The writer's finished report meets a test command that never started; returns the test file bytes."""
        self.candidate.side_effect = [
            self.not_started(),
            self.not_started(),
            {
                "exit_code": 1,
                "timed_out": False,
                "results_expected": True,
                "results": {
                    "passed": [],
                    "failed": ["tests.test_feature.Feature.test_answer"],
                    "skipped": [],
                    "total": 1,
                    "complete": True,
                },
                "tail": "AssertionError",
            },
        ]
        written = (self.workspace / "tests/test_feature.py").read_bytes()
        self.apply(acceptance.STAGE, self.state, self.value, self.record, self.workspace)
        return written

    def assert_runner_not_started_pause(self, written):
        self.assertEqual("PAUSED_ACCEPTANCE_TESTS", self.state["status"])
        self.assertEqual(acceptance.STAGE, self.state["next_stage"])
        reason = self.state["stop_reason"]
        self.assertIn("No module named pytest", reason)
        self.assertNotIn("correct the tests and execute a fresh proof, or use --feedback to revise the plan", reason)
        self.assertNotIn("--feedback", reason)
        pending = (self.state.get(acceptance.KEY) or {}).get(retry.KEY)
        self.assertIsNotNone(pending, "the finished writer's report is kept for a runner-only retry")
        self.assertEqual(self.value, pending["value"])
        latest = pending["failures"][-1]
        self.assertEqual(1, latest["exit_code"])
        self.assertIn("No module named pytest", latest["tail"])
        self.assertEqual(written, (self.workspace / "tests/test_feature.py").read_bytes())
        self.assertFalse(acceptance.receipts(self.state))
        self.runtime.run_role.assert_not_called()

    def test_ac11_runner_that_never_started_keeps_writer_and_retries_without_model(self):
        written = self.first_runner_not_started_pause()
        self.assert_runner_not_started_pause(written)
        # Resumed while the runner still cannot start: the same pause again, with no model call.
        self.state["status"] = "RUNNING"
        self.dispatch()
        self.assert_runner_not_started_pause(written)
        self.assertEqual(2, len(self.state[acceptance.KEY][retry.KEY]["failures"]))
        # Resumed once the environment is corrected: the normal per-case proof decides.
        self.state["status"] = "RUNNING"
        self.dispatch()
        receipts = list(acceptance.receipts(self.state).values())
        self.assertEqual(1, len(receipts))
        self.assertEqual({"AC1": "fails"}, receipts[0]["results"])
        self.assertEqual("builder", self.state["next_stage"])
        self.assertEqual(3, self.candidate.call_count)
        self.assertEqual(0, self.reference.call_count)
        self.runtime.run_role.assert_not_called()
        self.assertEqual(1, len(self.state["stages"]))
        self.assertNotIn(retry.KEY, self.state[acceptance.KEY])

    def test_ac12_started_runner_reporting_skipped_test_goes_back_to_writer(self):
        self.candidate.return_value = {
            "exit_code": 0,
            "timed_out": False,
            "results_expected": True,
            "results": {
                "passed": [],
                "failed": [],
                "skipped": ["tests.test_feature.Feature.test_answer"],
                "total": 1,
                "complete": True,
            },
            "tail": "skipped",
        }
        self.apply(acceptance.STAGE, self.state, self.value, self.record, self.workspace)
        self.assertEqual("RUNNING", self.state["status"])
        self.assertEqual(acceptance.STAGE, self.state["next_stage"])
        self.assertEqual(1, len(self.state[acceptance.KEY]["rewrites"]))
        self.assertNotIn(retry.KEY, self.state[acceptance.KEY])

    def test_ac19_runner_not_started_pause_texts_name_the_execution_problem(self):
        import autocode_recovery_view as recovery_view
        import autocode_stop_explanations as stop_explanations

        self.first_runner_not_started_pause()
        happened = recovery_view.project(self.state)["what_happened"]
        self.assertIn("test command could not start", happened)
        self.assertNotIn("did not behave on today's code", happened)
        explained = stop_explanations.explain("PAUSED_ACCEPTANCE_TESTS", stop_reason=self.state["stop_reason"])
        self.assertIn("could not start", explained["what_it_means"])
        other = copy.deepcopy(self.state)
        (other.get(acceptance.KEY) or {}).pop(retry.KEY, None)
        other["stop_reason"] = "The runner could not establish the approved pre-build proof: AC1 already passes"
        self.assertIn(
            "the Tester's tests did not behave on today's code", recovery_view.project(other)["what_happened"]
        )

    def test_runner_that_cannot_start_on_an_ordinary_retry_is_retried_on_each_resume(self):
        import autocode_recovery_view as recovery_view

        # An ordinary retained retry (the original reference was missing) whose re-run meets a test command
        # that cannot start: each resume re-runs the checks without a model call until the runner starts.
        self.start_retry()
        self.candidate.side_effect = [self.not_started(), self.not_started(), self.result]
        self.reference.side_effect = None
        self.reference.return_value = {"verdicts": {"AC1": "fails"}}
        written = (self.workspace / "tests/test_feature.py").read_bytes()
        for attempt in (1, 2):
            with self.subTest(attempt=attempt):
                self.state["status"] = "RUNNING"
                try:
                    self.dispatch()
                except util.Paused as error:
                    self.fail(f"A runner that could not start was not retried: {error.status}: {error}")
                self.assert_runner_not_started_pause(written)
                pending = self.state[acceptance.KEY][retry.KEY]
                self.assertEqual("runner_not_started", pending["kind"])
                self.assertEqual(attempt + 1, len(pending["failures"]))
                happened = recovery_view.project(self.state)["what_happened"]
                self.assertIn("test command could not start", happened)
                self.assertNotIn("did not behave on today's code", happened)
        # Resumed once the environment is corrected: the normal per-case proof decides.
        self.state["status"] = "RUNNING"
        self.dispatch()
        receipts = list(acceptance.receipts(self.state).values())
        self.assertEqual(1, len(receipts))
        self.assertEqual({"AC1": "passes candidate; fails original reference"}, receipts[0]["results"])
        self.assertEqual("builder", self.state["next_stage"])
        self.assertNotIn(retry.KEY, self.state[acceptance.KEY])
        self.assertEqual(4, self.candidate.call_count)
        self.assertEqual(2, self.reference.call_count)
        self.assertEqual(1, len(self.state["stages"]))
        self.runtime.run_role.assert_not_called()

    def test_runner_that_started_after_not_starting_keeps_the_retry_bound(self):
        # Once the runner starts, a repeated ordinary verification failure ends in the existing bounded path
        # after at most one further runner-only re-check, with no model call.
        self.start_retry()
        self.candidate.side_effect = [self.not_started(), self.result, self.result, self.result]
        self.reference.side_effect = None
        self.reference.return_value = {"error": "original-reference execution missing"}
        self.state["status"] = "RUNNING"
        self.dispatch()
        self.assertEqual("PAUSED_ACCEPTANCE_TESTS", self.state["status"])
        # The runner starts: the runner-not-started retry is archived and one ordinary re-check is queued.
        self.state["status"] = "RUNNING"
        self.dispatch()
        self.assertEqual("RUNNING", self.state["status"])
        self.assertNotIn("kind", self.state[acceptance.KEY][retry.KEY])
        for _ in range(2):
            with self.assertRaises(util.Paused) as stopped:
                self.dispatch()
            self.assertEqual("PAUSED_INVALID_OUTPUT", stopped.exception.status)
        self.assertEqual(4, self.candidate.call_count)
        self.assertFalse(acceptance.receipts(self.state))
        self.runtime.run_role.assert_not_called()

    def test_runner_that_starts_on_resume_and_reports_a_skipped_test_goes_back_to_writer(self):
        log = str(self.run / "pytest.log")
        skipped = {
            "exit_code": 0,
            "timed_out": False,
            "results_expected": True,
            "results": {
                "passed": [],
                "failed": [],
                "skipped": ["tests.test_feature.Feature.test_answer"],
                "total": 1,
                "complete": True,
            },
            "tail": "skipped",
        }
        self.candidate.side_effect = [{**self.not_started(), "output": log}, skipped]
        self.apply(acceptance.STAGE, self.state, self.value, self.record, self.workspace)
        self.assertEqual(
            "The runner could not establish the approved pre-build proof: The acceptance test command could not "
            "start its test runner (exit 1): /usr/bin/python3: No module named pytest. The test command did not "
            "start on this host, so the written tests were kept and not judged. Correct the environment; "
            "--resume-paused re-runs the checks without another Tester or Planner call.",
            self.state["stop_reason"],
        )
        pending = self.state[acceptance.KEY][retry.KEY]
        self.assertEqual(retry.RUNNER_NOT_STARTED, pending["kind"])
        latest = pending["failures"][-1]
        self.assertEqual(retry.RUNNER_NOT_STARTED, latest["kind"])
        self.assertEqual("python3 -m pytest tests/test_feature.py", latest["command"])
        self.assertEqual(log, latest["output"])
        # Resumed: the runner now starts and reports the written test skipped, a defect in the tests, so the
        # retained retry is archived and the existing test-correction path sends the tests back to the writer.
        self.state["status"] = "RUNNING"
        self.dispatch()
        self.assertEqual("RUNNING", self.state["status"])
        self.assertEqual(acceptance.STAGE, self.state["next_stage"])
        self.assertEqual(1, len(self.state[acceptance.KEY]["rewrites"]))
        self.assertNotIn(retry.KEY, self.state[acceptance.KEY])
        self.assertEqual(2, self.candidate.call_count)
        self.runtime.run_role.assert_not_called()

    # ---- #3927: what the next resume of a retained retry does, after the Investigator ----

    def spend_recheck(self):
        """The writer's report is retained and its one local re-check fails: attempts 1, 2 failures."""
        self.reference.side_effect = None
        self.reference.return_value = {"error": "original-reference execution missing"}
        self.start_retry()
        with self.assertRaises(util.Paused):
            self.dispatch()
        pending = self.state[acceptance.KEY][retry.KEY]
        self.assertEqual((1, 2), (pending["attempts"], len(pending["failures"])))

    def advise(self, requested_at, cause):
        """An Investigator result as autocode_stuck_job records it: the history entry carries the cause and
        requested_at; the in-force record has exactly the keys apply writes, with no requested_at."""
        binding = retry.recovery_binding.binding(self.state, acceptance.STAGE)
        self.state.setdefault("stuck_investigations", []).append(
            {
                "identity": IDENTITY,
                "stage": acceptance.STAGE,
                "status": "PAUSED_INVALID_OUTPUT",
                "reason": "Acceptance verification retry could not finish: original-reference execution missing",
                "requested_at": requested_at,
                "outcome": "retried",
                "trigger": "rejected_output",
                "cause": cause,
                "diagnosis": "The saved test imports the public module at collection time.",
                "guidance": GUIDANCE,
                "finished_at": requested_at.replace(":26:26", ":32:35").replace(":40:00", ":45:00"),
                **binding,
            }
        )
        self.state["stuck_investigation"] = {
            "identity": IDENTITY,
            "stage": acceptance.STAGE,
            "status": "PAUSED_INVALID_OUTPUT",
            **binding,
            "guidance": GUIDANCE,
            "diagnosis": "The saved test imports the public module at collection time.",
            "example": "Given the saved test; when it is collected on the original; then the import fails",
            "in_force": True,
        }

    def pause_at_tester(self):
        self.state.update(
            status="PAUSED_INVALID_OUTPUT",
            next_stage=acceptance.STAGE,
            stop_reason="Acceptance verification retry could not finish: original-reference execution missing",
        )

    def project(self):
        return run_view.view(self.state, workspace=self.workspace, run_dir=self.run)

    def exhausted(self):
        """The #3927 state: saved at PAUSED_INVALID_OUTPUT after its one re-check failed, no in-force advice."""
        self.spend_recheck()
        self.pause_at_tester()
        return self.project()

    def test_ac1_test_cause_advice_retires_retained_retry(self):
        self.spend_recheck()
        self.advise("2026-11-20T02:26:26+00:00", "stage_output")
        self.assertIsNone(self.dispatch())
        self.assertEqual(2, self.reference.call_count, "re-reading the pinned report cannot edit the saved tests")
        self.runtime.run_role.assert_not_called()
        self.assertNotIn(retry.KEY, self.state[acceptance.KEY])
        archived = self.state[acceptance.KEY]["verification_retries"][-1]
        self.assertEqual(1, archived["attempts"])
        self.assertEqual(2, len(archived["failures"]))
        self.assertEqual({"identity": IDENTITY, "requested_at": "2026-11-20T02:26:26+00:00"}, archived["retired_by"])

        # A newly retained retry under that same Investigator result spends its own re-check, then is exhausted.
        self.start_retry()
        with self.assertRaises(util.Paused):
            self.dispatch()
        self.assertEqual(1, self.state[acceptance.KEY][retry.KEY]["attempts"])
        checks = self.reference.call_count
        with self.assertRaises(util.Paused) as stopped:
            self.assertIsNotNone(self.dispatch())
        self.assertEqual("PAUSED_INVALID_OUTPUT", stopped.exception.status)
        self.assertIn("The bounded acceptance verification retry is exhausted", str(stopped.exception))
        self.assertEqual(checks, self.reference.call_count)

        # A later Investigator result with the same identity string retires its own retained retry.
        self.advise("2026-11-20T02:40:00+00:00", "stage_output")
        self.assertIsNone(self.dispatch())
        self.assertEqual(checks, self.reference.call_count)
        self.assertNotIn(retry.KEY, self.state[acceptance.KEY])
        archived = self.state[acceptance.KEY]["verification_retries"][-1]
        self.assertEqual(1, archived["attempts"])
        self.assertEqual("2026-11-20T02:40:00+00:00", archived["retired_by"]["requested_at"])
        self.runtime.run_role.assert_not_called()

    def test_ac2_environment_cause_keeps_single_local_recheck(self):
        self.spend_recheck()
        self.advise("2026-11-20T02:26:26+00:00", "environment")
        with self.assertRaises(util.Paused) as stopped:
            self.dispatch()
        self.assertEqual("PAUSED_INVALID_OUTPUT", stopped.exception.status)
        self.assertEqual(3, self.reference.call_count)
        pending = self.state[acceptance.KEY][retry.KEY]
        self.assertEqual(2, pending["attempts"])
        self.assertEqual([IDENTITY], pending["investigations"])
        self.runtime.run_role.assert_not_called()

    def test_ac24_changed_report_with_test_cause_advice_pauses_without_retiring(self):
        self.spend_recheck()
        self.advise("2026-11-20T02:26:26+00:00", "stage_output")
        output = Path(self.record["output"])
        saved = output.read_bytes()
        output.write_text("changed")
        try:
            with self.assertRaises(util.Paused) as stopped:
                self.dispatch()
        finally:
            output.write_bytes(saved)
        self.assertEqual("PAUSED_INVALID_OUTPUT", stopped.exception.status)
        self.assertIn("Retained acceptance report, approved task or source changed", str(stopped.exception))
        pending = self.state[acceptance.KEY][retry.KEY]
        self.assertEqual(1, pending["attempts"])
        self.assertFalse(self.state[acceptance.KEY].get("verification_retries"))
        self.assertEqual(2, self.reference.call_count)
        self.assertFalse(acceptance.receipts(self.state))
        self.runtime.run_role.assert_not_called()

    def test_ac5_exhausted_recheck_offers_no_resume(self):
        shown = self.exhausted()
        self.assertIsNone(shown["needs"])
        self.assertIsNone(shown["next_command"])
        card = shown["recovery"]
        self.assertNotIn("resume", [row["kind"] for row in card["actions"]])
        self.assertIn(EXHAUSTED_SENTENCE, card["what_happened"])
        self.assertEqual({"next": "exhausted", "rechecks": 1, "allowed": 1}, shown.get("acceptance_verification_retry"))
        self.assertNotIn("Try the Tester step again", json.dumps(shown))

    def test_ac6_exhausted_recheck_reports_failure_without_requesting_machinery_feedback(self):
        shown = self.exhausted()
        self.assertIsNone(shown["chat"]["input"])
        feedback = [row for row in shown["recovery"]["actions"] if row["kind"] == "feedback"]
        self.assertEqual([], feedback)

    def test_ac21_exhausted_recheck_reports_a_blocked_outcome(self):
        shown = self.exhausted()
        self.assertIsNone(shown["needs"])
        self.assertIsNone(shown["next_command"])
        card = shown["recovery"]
        self.assertEqual("blocked", card["category"])
        self.assertEqual("No re-check of the saved tests is left", card["title"])
        self.assertIn("1 of 1 allowed times", card["what_happened"])
        self.assertEqual(card["what_happened"], shown["chat"]["notice"])
        self.assertIsNone(shown["chat"]["input"])

    def test_ac7_pending_recheck_is_labelled_as_recheck(self):
        self.reference.side_effect = None
        self.reference.return_value = {"error": "original-reference execution missing"}
        self.start_retry()
        self.pause_at_tester()
        shown = self.project()
        resume = next(row for row in shown["recovery"]["actions"] if row["kind"] == "resume")
        self.assertEqual("Re-check the saved tests", resume["label"])
        self.assertIn("without a new Tester attempt", resume["effect"])
        self.assertNotIn("checks its new answer", resume["effect"])
        self.assertNotIn("Try the Tester step again", [row["label"] for row in shown["recovery"]["actions"]])
        self.assertIn("--resume-paused", shown["next_command"])
        self.assertEqual({"next": "recheck", "rechecks": 0, "allowed": 1}, shown.get("acceptance_verification_retry"))

    def test_ac8_test_cause_advice_offers_fresh_tester_attempt(self):
        self.spend_recheck()
        self.advise("2026-11-20T02:26:26+00:00", "stage_output")
        self.pause_at_tester()
        shown = self.project()
        self.assertEqual({"next": "tester", "rechecks": 1, "allowed": 1}, shown.get("acceptance_verification_retry"))
        resume = next(row for row in shown["recovery"]["actions"] if row["kind"] == "resume")
        self.assertEqual("Try the Tester step again", resume["label"])
        self.assertIn("--resume-paused", shown["next_command"])
        # Dispatch reads the same answer: the resume retires the retained report for a fresh Tester attempt.
        self.assertIsNone(self.dispatch())
        self.assertEqual(2, self.reference.call_count)
        self.runtime.run_role.assert_not_called()

    def test_ac26_in_loop_test_cause_advice_reaches_writer_launch(self):
        self.state.update(
            version=3, settings={**state_for(workspace=str(self.workspace))["settings"], "spec_first": True}
        )
        self.reference.side_effect = None
        self.reference.return_value = {"error": "original-reference execution missing"}
        self.start_retry()
        dispatched, launches, checked = [], [], []

        class ReachedWriterLaunch(Exception):
            pass

        def dispatch(state, stage):
            dispatched.append(stage)
            if stage == stuck.STAGE:
                checked.append(self.reference.call_count)
                # The real Investigator result: autocode_stuck_job.apply records it from a scripted report.
                output = self.run / "investigator.json"
                stuck.apply(
                    state,
                    report(cause="stage_output", guidance=GUIDANCE, evidence_refs=["tests/test_feature.py"]),
                    {"output": str(output), "changed_files": []},
                    self.workspace,
                )
                return {"stage": stage, "output": str(output)}
            retained = self.dispatch()
            if retained is None:  # the ordinary test-writing step: a model launch, with the guidance
                launches.append(stuck.guidance(state, stage))
                raise ReachedWriterLaunch
            return retained

        with self.assertRaises(ReachedWriterLaunch):
            stuck.drive(
                self.state,
                dispatch,
                active=lambda state: state["status"] == "RUNNING",
                skip=object(),
                paused=util.Paused,
                investigate=True,
            )
        self.assertEqual([acceptance.STAGE, stuck.STAGE, acceptance.STAGE], dispatched)
        self.assertEqual(1, len(launches))
        self.assertIn(GUIDANCE, launches[0])
        entry = self.state["stuck_investigations"][-1]
        self.assertEqual((IDENTITY, "retried"), (entry["identity"], entry["outcome"]))
        self.assertEqual(acceptance.STAGE, self.state["next_stage"])
        self.assertNotIn(retry.KEY, self.state[acceptance.KEY])
        archived = self.state[acceptance.KEY]["verification_retries"][-1]
        self.assertEqual(1, archived["attempts"])
        self.assertEqual(entry["requested_at"], archived["retired_by"]["requested_at"])
        self.assertEqual(checked, [self.reference.call_count], "no original-reference check after the Investigator")
        self.runtime.run_role.assert_not_called()

    def test_saved_pause_names_resume_and_explains_retained_verification(self):
        self.state.update(status="PAUSED_ACCEPTANCE_TESTS", stop_reason="original-reference execution missing")
        need = run_view.needs(self.state)
        self.assertEqual("--resume-paused", need["action"])
        self.assertIn("retained report", need["guidance"])
