"""A changed validator can inspect an incident's exact retained report without a new attempt."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_action_recovery as recovery
import autocode_operational_admission as admission
import autocode_operational_disposition as disposition
import autocode_recovery_view as recovery_view
import autocode_resolver_runtime as resolver
import autocode_run_actions as actions
import autocode_run_view as view
import autocode_status_observation as observation
import autocode_util as util

from . import test_autocode as base
from . import test_operational_disposition as operational_fixtures
from . import test_report_repair as report_fixtures

runner = report_fixtures.runner


class RetainedReportIncidentTests(unittest.TestCase):
    setUp = base.RetrofitTest.setUp
    stage_record = report_fixtures.RepairTests.stage_record
    queue = report_fixtures.RepairTests.queue

    def held_report(self, pause="PAUSED_REPEATED_FAILURE", report='{"summary":"saved"}', stage="builder"):
        self.state["next_stage"] = stage
        pending = self.queue(report=report, stage=stage, role=stage)
        pending["validation_runtime"] = "0" * 64
        pending["attempts"] = 2
        self.state["status"] = pause
        self.state.setdefault("user_events", [])
        incident = resolver._operational_receipt(
            self.state,
            self.run,
            "hold",
            "The report was rejected",
            {
                "status": self.state["status"],
                "stage": self.state["next_stage"],
                "budget": disposition.budget(self.state, self.state["status"]),
            },
        )
        runner.write_json(self.run / "state.json", self.state)
        return pending, incident

    def test_changed_validator_offers_the_saved_report_without_a_new_retry(self):
        pending, _ = self.held_report()
        before = copy.deepcopy(self.state)
        shown = view.action(self.state, run_dir=self.run, workspace=self.root)
        self.assertEqual("continue", (shown["needs"] or {}).get("kind"))
        self.assertIn("--resume-paused", shown["next_command"])
        self.assertEqual(2, pending["attempts"])
        public = view.view(self.state, run_dir=self.run, workspace=self.root)
        self.assertEqual("continue", (public["needs"] or {}).get("kind"))
        self.assertIs(public["needs"]["automatic_reconciliation"], True)
        self.assertIn("--resume-paused --expected-recovery-token -", public["next_command"])
        self.assertIn(public["recovery"]["token"], public["next_command"])
        self.assertNotEqual("blocked", (public.get("recovery") or {}).get("category"))
        self.assertEqual(before, self.state, "status observes without changing the held incident")

    def test_other_report_failure_holds_offer_the_same_model_free_check(self):
        for pause in ("PAUSED_INVALID_OUTPUT", "PAUSED_REPORT_REPAIR_LIMIT"):
            with self.subTest(pause=pause):
                self.setUp()
                pending, _ = self.held_report(pause)
                shown = view.action(self.state, run_dir=self.run, workspace=self.root)
                self.assertEqual("pending", shown.get("_retained_report_revalidation"))
                self.assertIn("--resume-paused", shown["next_command"])
                self.assertEqual(2, pending["attempts"])

    def test_accepted_report_and_checked_incident_commit_together_without_a_provider(self):
        pending, incident = self.held_report(stage="tester")
        decided = actions.Decided()
        args = operational_fixtures.Arguments(resume_paused=True)

        def apply(state, stage, value, record, workspace, run):
            state["stages"].append(record)
            state["history"].append(record)
            state["next_stage"] = "review_completion"

        with (
            patch.object(runner, "load_stage_report", wraps=runner.load_stage_report) as validate,
            patch.object(runner, "apply_result", side_effect=apply),
            patch.object(runner, "run_role") as launch,
        ):
            self.assertIsNone(
                admission.guard(runner, args, None, self.state, self.run / "state.json", self.run, self.root, decided)
            )
            self.assertTrue(decided.owner_lane)
            self.assertEqual(incident, decided.operational_incident)
            code = actions.apply_owned(
                runner, args, None, self.state, self.run / "state.json", self.run, self.root, decided
            )
        self.assertIsNone(code)
        self.assertTrue(decided.dispatch)
        launch.assert_not_called()
        validate.assert_called_once()
        self.assertNotIn("pending_report_repair", self.state)
        self.assertEqual(2, self.state["report_repair_history"][-1]["attempts"])
        self.assertIsNotNone(disposition.checked_recovery(self.state, disposition.held(self.state)))
        self.assertTrue(admission.ready(self.state, decided))
        self.assertIsNone(
            admission.work_guard(
                runner,
                SimpleNamespace(expected_goal_token=None),
                None,
                self.state,
                self.run / "state.json",
                self.run,
                self.root,
                decided,
                final=True,
            )
        )
        public = view.view(self.state, run_dir=self.run, workspace=self.root)
        self.assertEqual("continue", (public["needs"] or {}).get("kind"))
        self.assertIs(public["needs"]["automatic_reconciliation"], True)
        self.assertIn("--resume-paused --expected-recovery-token -", public["next_command"])
        restored = json.loads((self.run / "state.json").read_text())
        self.assertIsNotNone(disposition.checked_recovery(restored, disposition.held(restored)))
        reopened = view.action(restored, run_dir=self.run, workspace=self.root)
        self.assertEqual("accepted", reopened.get("_retained_report_revalidation"))
        for change in (
            lambda state: state["user_events"].append({"actor": "user_cli", "kind": "feedback"}),
            lambda state: state.update(pending_questions=[{"id": "new-question"}]),
            lambda state: state.update(active_stage={"stage": "tester"}),
        ):
            changed = copy.deepcopy(self.state)
            change(changed)
            self.assertIsNone(view.action(changed, run_dir=self.run, workspace=self.root)["next_command"])
            self.assertEqual(
                2,
                admission.work_guard(
                    runner,
                    SimpleNamespace(expected_goal_token=None),
                    None,
                    changed,
                    self.run / "state.json",
                    self.run,
                    self.root,
                    decided,
                    final=True,
                ),
            )
        output = Path(self.state["report_repair_history"][-1]["repair"]["output"])
        original_bytes = output.read_bytes()
        try:
            output.write_bytes(original_bytes + b" ")
            self.assertIsNone(view.action(self.state, run_dir=self.run, workspace=self.root)["next_command"])
            self.assertEqual(
                2,
                admission.work_guard(
                    runner,
                    SimpleNamespace(expected_goal_token=None),
                    None,
                    self.state,
                    self.run / "state.json",
                    self.run,
                    self.root,
                    decided,
                    final=True,
                ),
            )
        finally:
            output.write_bytes(original_bytes)
        with patch.object(runner, "run_role") as launch:
            self.assertIsNone(
                admission.dispatch(
                    None,
                    runner,
                    args,
                    None,
                    self.state,
                    self.run / "state.json",
                    self.run,
                    self.root,
                    decided,
                )
            )
        launch.assert_not_called()
        self.assertIsNone(
            disposition.held(self.state), "final admission retires the incident before normal continuation"
        )

    def test_a_new_decision_boundary_is_not_silently_advanced(self):
        pending, incident = self.held_report()
        decided = actions.Decided(operational_incident=incident, operational_selection="retained_report")

        def apply(state, stage, value, record, workspace, run):
            state["stages"].append(record)
            state["history"].append(record)
            state["status"] = "WAITING_FOR_USER"
            state["pending_questions"] = [{"id": "new-decision"}]

        with (
            patch.object(runner, "load_stage_report", wraps=runner.load_stage_report),
            patch.object(runner, "apply_result", side_effect=apply),
            patch.object(runner, "run_role") as launch,
            self.assertRaisesRegex(runner.support.Paused, "separate decision boundary"),
        ):
            recovery.retained_report_check(
                runner,
                SimpleNamespace(resume_paused=True),
                None,
                self.state,
                self.run / "state.json",
                self.run,
                self.root,
                decided,
            )
        launch.assert_not_called()
        self.assertEqual(pending, self.state["pending_report_repair"])
        self.assertIsNone(disposition.checked_recovery(self.state, disposition.held(self.state)))
        self.assertEqual("PAUSED_REPEATED_FAILURE", json.loads((self.run / "state.json").read_text())["status"])

    def test_failed_candidate_commit_never_grants_a_checked_incident(self):
        pending, incident = self.held_report()
        decided = actions.Decided(operational_incident=incident, operational_selection="retained_report")

        def apply(state, stage, value, record, workspace, run):
            state["stages"].append(record)
            state["history"].append(record)
            state["next_stage"] = "tester"

        with (
            patch.object(runner, "load_stage_report", wraps=runner.load_stage_report),
            patch.object(runner, "apply_result", side_effect=apply),
            patch.object(runner, "commit_boundary_candidate", side_effect=RuntimeError("commit failed")),
            patch.object(runner, "run_role") as launch,
            self.assertRaisesRegex(RuntimeError, "commit failed"),
        ):
            recovery.retained_report_check(
                runner,
                SimpleNamespace(resume_paused=True),
                None,
                self.state,
                self.run / "state.json",
                self.run,
                self.root,
                decided,
            )
        launch.assert_not_called()
        self.assertEqual(pending, self.state["pending_report_repair"])
        self.assertIsNone(disposition.checked_recovery(self.state, disposition.held(self.state)))
        saved = json.loads((self.run / "state.json").read_text())
        self.assertIsNone(disposition.checked_recovery(saved, disposition.held(saved)))

    def test_checked_report_restart_takes_one_bound_continuation_without_revalidation(self):
        self.held_report()
        decided = actions.Decided()
        args = operational_fixtures.Arguments(resume_paused=True)

        def apply(state, stage, value, record, workspace, run):
            state["stages"].append(record)
            state["history"].append(record)
            state["next_stage"] = "tester"

        with patch.object(runner, "apply_result", side_effect=apply):
            self.assertIsNone(
                admission.guard(runner, args, None, self.state, self.run / "state.json", self.run, self.root, decided)
            )
            self.assertIsNone(
                actions.apply_owned(
                    runner, args, None, self.state, self.run / "state.json", self.run, self.root, decided
                )
            )
        restored = json.loads((self.run / "state.json").read_text())
        public = view.view(restored, run_dir=self.run, workspace=self.root)
        token = public["recovery"]["token"]
        self.assertTrue(token)
        self.assertIs(public["needs"]["automatic_reconciliation"], True)
        self.assertIn("--resume-paused --expected-recovery-token -", public["next_command"])
        changed = copy.deepcopy(restored)
        changed["user_events"].append({"actor": "user_cli", "kind": "feedback"})
        with self.assertRaises(ValueError):
            recovery_view.require_token(changed, token)
        parser = runner.cli_args.build_parser(None, runner.DEFAULT_ROLE_MODELS)
        resume = parser.parse_args(["--resume-paused", "--no-chat", "--expected-recovery-token", token])
        mixed = parser.parse_args(
            ["--resume-paused", "--retry-failed-stage", "--expected-recovery-token", token, "--no-chat"]
        )
        self.assertFalse(any(owner.name == "observe" for owner in actions.owned_operations(mixed, restored)))
        stale = parser.parse_args(["--resume-paused", "--no-chat", "--expected-recovery-token", "stale"])
        with patch.object(runner, "load_stage_report") as validate, patch.object(runner, "run_role") as launch:
            self.assertEqual(
                2, actions.handle(runner, stale, parser, restored, self.run / "state.json", self.run, self.root)
            )
        validate.assert_not_called()
        launch.assert_not_called()
        self.assertIsNotNone(disposition.held(restored))
        recovery_view.require_token(restored, token)
        with patch.object(runner, "load_stage_report") as validate, patch.object(runner, "run_role") as launch:
            self.assertIsNone(
                actions.handle(runner, resume, parser, restored, self.run / "state.json", self.run, self.root)
            )
        validate.assert_not_called()
        launch.assert_not_called()
        self.assertIsNone(disposition.held(restored))
        self.assertEqual(2, restored["report_repair_history"][-1]["attempts"])

    def test_one_normal_resume_accepts_the_report_and_admits_continuation(self):
        pending, _ = self.held_report()
        parser = runner.cli_args.build_parser(None, runner.DEFAULT_ROLE_MODELS)
        args = parser.parse_args(["--resume-paused", "--no-chat"])

        def apply(state, stage, value, record, workspace, run):
            state["stages"].append(record)
            state["history"].append(record)
            state["next_stage"] = "tester"

        with (
            patch.object(runner, "load_stage_report", wraps=runner.load_stage_report) as validate,
            patch.object(runner, "apply_result", side_effect=apply),
            patch.object(runner, "run_role") as launch,
        ):
            result = actions.handle(runner, args, parser, self.state, self.run / "state.json", self.run, self.root)
        self.assertIsNone(result, "the normal controller may continue after its final guards")
        validate.assert_called_once()
        launch.assert_not_called()
        self.assertIsNone(disposition.held(self.state))
        self.assertNotIn("pending_report_repair", self.state)
        self.assertEqual(2, pending["attempts"])
        self.assertFalse(any(event.get("kind") == "report_repair_resume_epoch" for event in self.state["user_events"]))

    def test_unavailable_or_changed_frontier_never_offers_or_launches(self):
        pending, incident = self.held_report()
        original = copy.deepcopy(self.state)
        cases = {
            "unchanged runtime": lambda state: state["pending_report_repair"].update(
                validation_runtime=util.runtime_source_digest()
            ),
            "missing runtime": lambda state: state["pending_report_repair"].pop("validation_runtime"),
            "other stage": lambda state: state.update(next_stage="tester"),
            "active stage": lambda state: state.update(active_stage={"stage": "builder"}),
            "unsettled runner check": lambda state: state.update(active_runner_check={"stage": "builder"}),
            "unresolved source": lambda state: state.update(uncertain_artifacts=[{"path": "unknown"}]),
            "pending question": lambda state: state.update(pending_questions=[{"id": "Q"}]),
            "new failure": lambda state: state.setdefault("failure_history", {}).update(changed={}),
            "later human input": lambda state: state["user_events"].append({"actor": "user_cli", "kind": "feedback"}),
            "changed accounting": lambda state: state.update(automatic_recoveries_since_resume=1),
            "changed active time": lambda state: state.update(active_seconds=999),
            "changed iteration": lambda state: state.update(iteration=999),
        }
        for name, change in cases.items():
            with self.subTest(case=name):
                state = copy.deepcopy(original)
                change(state)
                shown = view.action(state, run_dir=self.run, workspace=self.root)
                self.assertIsNone(shown["next_command"])
                decided = actions.Decided(operational_incident=incident, operational_selection="retained_report")
                with patch.object(runner, "run_role") as launch:
                    self.assertEqual(
                        2,
                        recovery.retained_report_check(
                            runner,
                            SimpleNamespace(resume_paused=True),
                            None,
                            state,
                            self.run / "state.json",
                            self.run,
                            self.root,
                            decided,
                        ),
                    )
                launch.assert_not_called()
                self.assertEqual(2, state["pending_report_repair"]["attempts"])

    def test_changed_or_missing_pinned_report_never_offers_or_launches(self):
        pending, incident = self.held_report()
        output = Path(pending["original"]["output"])
        contents = output.read_bytes()
        for mode in ("changed", "missing"):
            with self.subTest(mode=mode):
                try:
                    if mode == "changed":
                        output.write_bytes(contents + b" ")
                    else:
                        output.unlink()
                    shown = view.action(self.state, run_dir=self.run, workspace=self.root)
                    self.assertIsNone(shown["next_command"])
                    decided = actions.Decided(operational_incident=incident, operational_selection="retained_report")
                    with patch.object(runner, "run_role") as launch:
                        self.assertEqual(
                            2,
                            recovery.retained_report_check(
                                runner,
                                SimpleNamespace(resume_paused=True),
                                None,
                                self.state,
                                self.run / "state.json",
                                self.run,
                                self.root,
                                decided,
                            ),
                        )
                    launch.assert_not_called()
                    self.assertEqual(2, pending["attempts"])
                finally:
                    output.write_bytes(contents)

    def test_malformed_or_unavailable_status_worker_never_advertises_a_resume(self):
        self.held_report()
        for answer in (None, False, True, {}, {"result": "pending"}, "unknown"):
            with self.subTest(answer=answer), patch.object(observation, "_observe", return_value=answer):
                self.assertIsNone(view.action(self.state, run_dir=self.run, workspace=self.root)["next_command"])

    def test_changed_source_never_offers_the_old_report(self):
        pending, incident = self.held_report()
        (self.root / "new_input.py").write_text("changed = True\n")
        self.assertIsNone(view.action(self.state, run_dir=self.run, workspace=self.root)["next_command"])
        decided = actions.Decided(operational_incident=incident, operational_selection="retained_report")
        with patch.object(runner, "run_role") as launch:
            self.assertEqual(
                2,
                recovery.retained_report_check(
                    runner,
                    SimpleNamespace(resume_paused=True),
                    None,
                    self.state,
                    self.run / "state.json",
                    self.run,
                    self.root,
                    decided,
                ),
            )
        launch.assert_not_called()
        self.assertEqual(2, pending["attempts"])

    def test_another_workspace_or_run_cannot_claim_the_incident(self):
        pending, incident = self.held_report()
        other = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        decided = actions.Decided(operational_incident=incident, operational_selection="retained_report")
        for workspace, run_dir in ((other, self.run), (self.root, other)):
            with self.subTest(workspace=workspace, run_dir=run_dir):
                self.assertIsNone(view.action(self.state, run_dir=run_dir, workspace=workspace)["next_command"])
                with patch.object(runner, "run_role") as launch:
                    self.assertEqual(
                        2,
                        recovery.retained_report_check(
                            runner,
                            SimpleNamespace(resume_paused=True),
                            None,
                            self.state,
                            self.run / "state.json",
                            run_dir,
                            workspace,
                            decided,
                        ),
                    )
                launch.assert_not_called()
                self.assertEqual(2, pending["attempts"])

    def test_still_rejected_report_records_the_recheck_without_renewing_or_launching(self):
        pending, incident = self.held_report(report="{")
        decided = actions.Decided()
        args = operational_fixtures.Arguments(resume_paused=True)
        with (
            patch.object(runner, "load_stage_report", wraps=runner.load_stage_report) as validate,
            patch.object(runner, "run_role") as launch,
        ):
            self.assertIsNone(
                admission.guard(runner, args, None, self.state, self.run / "state.json", self.run, self.root, decided)
            )
            self.assertEqual(incident, decided.operational_incident)
            code = actions.apply_owned(
                runner, args, None, self.state, self.run / "state.json", self.run, self.root, decided
            )
        self.assertEqual(2, code)
        launch.assert_not_called()
        validate.assert_called_once()
        self.assertEqual(2, pending["attempts"])
        self.assertEqual("rejected", pending["revalidations"][-1]["result"])
        self.assertTrue(pending["revalidations"][-1]["reason"])
        self.assertEqual("PAUSED_REPEATED_FAILURE", self.state["status"])
        self.assertIsNone(disposition.checked_recovery(self.state, disposition.held(self.state)))
        self.assertFalse(admission.ready(self.state, decided))

    def test_one_normal_resume_keeps_a_still_rejected_report_held(self):
        pending, _ = self.held_report(report="{")
        parser = runner.cli_args.build_parser(None, runner.DEFAULT_ROLE_MODELS)
        args = parser.parse_args(["--resume-paused", "--no-chat"])
        with (
            patch.object(runner, "load_stage_report", wraps=runner.load_stage_report) as validate,
            patch.object(runner, "run_role") as launch,
        ):
            result = actions.handle(runner, args, parser, self.state, self.run / "state.json", self.run, self.root)
        self.assertEqual(2, result)
        validate.assert_called_once()
        launch.assert_not_called()
        self.assertEqual(2, pending["attempts"])
        self.assertEqual("rejected", pending["revalidations"][-1]["result"])
        self.assertIsNotNone(disposition.held(self.state))
        self.assertIsNone(view.action(self.state, run_dir=self.run, workspace=self.root)["next_command"])
