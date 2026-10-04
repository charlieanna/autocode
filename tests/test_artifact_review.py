"""Public CLI regression for a technically validated artifact awaiting human acceptance."""
import copy
import json
import unittest

from . import test_subprocess, test_goals
import autocode_completion as completion
import autocode_goals as goals
import autocode_goal_lifecycle as lifecycle
from goal_fixtures import body


class ArtifactReviewCLITests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ("--engine", "codex")

    def review_then_complete(self, **fixture_flags):
        self.env["AUTOCODE_FIXTURE_MODE"] = "human-pending"
        self.env.update(fixture_flags)
        probe = self.root / "launches.jsonl"
        self.env["AUTOCODE_REGISTRY_LAUNCH_PROBE"] = str(probe)
        self.launch(["Build greeting", "--chat", "--max-iterations", "2"], 2, answers="CLI\nyes\n")
        run, _ = self.saved()
        status = json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)
        need = status["view"]["needs"]
        self.assertEqual("review", need["kind"], status["view"])
        self.assertEqual(["C1"], need["criteria"])
        self.assertTrue(need["token"])
        self.assertFalse(status["view"]["done"])
        # The public review token approves only this current artifact; resume then completes.
        self.launch(["--run-dir", str(run), "--approve-review", "C1", "--review-token", need["token"]], 0)
        self.launch(["--run-dir", str(run), "--no-chat"], 0)
        done = json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)
        self.assertTrue(done["view"]["done"])
        stages = [json.loads(line) for line in probe.read_text().splitlines()]
        self.assertEqual(1, sum(row["stage"] == "sol" for row in stages))
        self.assertEqual(1, sum(row["stage"] == "terra" for row in stages))
        repairs = [row["stage"] for row in stages if row["stage"].endswith("_report_repair")]
        self.assertEqual(["astra_review_report_repair"] if fixture_flags.get("AUTOCODE_FIXTURE_EMPTY_HUMAN_EVIDENCE")
                         else ["sol_report_repair"] if fixture_flags.get("AUTOCODE_FIXTURE_OMIT_CHECKS") else [], repairs)

    def test_unverified_human_result_presents_review_without_repeating_validation(self):
        self.review_then_complete()

    def test_empty_human_decision_evidence_is_repaired_before_review(self):
        self.review_then_complete(AUTOCODE_FIXTURE_EMPTY_HUMAN_EVIDENCE="1")

    def test_omitted_executed_checks_are_repaired_before_review(self):
        self.review_then_complete(AUTOCODE_FIXTURE_OMIT_CHECKS="1")

    def test_flow_awaiting_only_human_acceptance_presents_review_without_repeating_validation(self):
        # #195: the approved flow ends in the person's approval, so the Validator leaves it NOT_VERIFIED.
        self.review_then_complete(AUTOCODE_FIXTURE_FLOW_AWAITS_REVIEW="CLI flows executed; C1 human acceptance pending")

    def test_passing_verdict_with_only_human_acceptance_pending_presents_review(self):
        # The live GLM 5.3 Validator reported PASS, not BLOCKED, with the human criterion and flow pending.
        self.review_then_complete(AUTOCODE_FIXTURE_HUMAN_VERDICT="PASS",
                                  AUTOCODE_FIXTURE_FLOW_AWAITS_REVIEW="CLI flows executed; C1 human acceptance pending")

    def test_unexplained_flow_gap_is_not_offered_as_a_review(self):
        self.env.update(AUTOCODE_FIXTURE_MODE="human-pending",
                        AUTOCODE_FIXTURE_FLOW_AWAITS_REVIEW="One flow step was not executed")
        self.launch(["Build greeting", "--chat", "--max-iterations", "2"], 2, answers="CLI\nyes\n")
        run, _ = self.saved()
        status = json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)
        self.assertFalse(status["view"]["done"])
        self.assertNotEqual("review", status["view"]["needs"]["kind"])

    def test_repair_cannot_invent_an_executed_check_and_does_not_repeat_validation(self):
        self.env.update(AUTOCODE_FIXTURE_MODE="human-pending", AUTOCODE_FIXTURE_NO_CHECK_EVENT="1")
        probe = self.root / "launches.jsonl"
        self.env["AUTOCODE_REGISTRY_LAUNCH_PROBE"] = str(probe)
        self.launch(["Build greeting", "--chat", "--max-iterations", "2"], 2, answers="CLI\nyes\n")
        run, _ = self.saved()
        status = json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)
        self.assertFalse(status["view"]["done"])
        self.assertNotEqual("review", status["view"]["needs"]["kind"])
        stages = [json.loads(line)["stage"] for line in probe.read_text().splitlines()]
        self.assertEqual(1, stages.count("sol"))
        self.assertEqual(1, stages.count("terra"))
        self.assertEqual(2, stages.count("sol_report_repair"))
        # The actual report error is retained; exhausting iterations is not the cause.
        self.assertNotIn("iteration ceiling", status["view"]["stop_reason"].lower())


class ArtifactReviewGateTests(unittest.TestCase):
    setUp = test_goals.GoalTests.setUp
    decision = test_goals.GoalTests.decision
    validation = test_goals.GoalTests.validation

    def fixture(self):
        draft = body(human=True)
        draft["acceptance_criteria"].append({"id": "C2", "criterion": "Technical behavior",
            "verification_method": "Execute the CLI", "human_review": False})
        draft["milestones"][0]["acceptance_criteria"].append("C2")
        lifecycle.install_draft(self.state, draft, origin="test")
        lifecycle.human.evaluate(self.state)
        lifecycle.present(self.state)
        lifecycle.approve(self.state, self.state["displayed_goal"])
        current = self.validation()
        validation = self.state["validation"]
        validation.update(verdict="BLOCKED", unverified_criteria=["C1 human acceptance pending"])
        validation["criterion_results"][0]["status"] = "NOT_VERIFIED"
        decision = self.decision()
        decision["next_task"]["kind"] = "validate"
        decision["acceptance_criteria"][0]["status"] = "unverified"
        return self.state, decision, current

    def test_review_request_never_marks_human_acceptance_or_changes_model_reports(self):
        state, decision, current = self.fixture()
        before = copy.deepcopy((state, decision))
        request = completion.artifact_review_request(state, decision, current)
        self.assertEqual(["C1"], request["criteria"])
        self.assertEqual(before, (state, decision))
        self.assertFalse(completion.completion_ready(state, {**decision, "status": "TASK_COMPLETE"}, current))

    def test_empty_human_evidence_requires_report_repair_without_mutation_or_review(self):
        state, decision, current = self.fixture()
        decision["acceptance_criteria"][0]["evidence"] = ""
        before = copy.deepcopy((state, decision))
        with self.assertRaisesRegex(ValueError, "cite the existing Validator evidence"):
            completion.artifact_review_request(state, decision, current)
        self.assertEqual(before, (state, decision))
        self.assertFalse(completion.completion_ready(state, {**decision, "status": "TASK_COMPLETE"}, current))

    def test_review_does_not_hide_any_technical_gap_or_requested_correction(self):
        state, decision, current = self.fixture()
        cases = {
            "implementation requested": lambda s, d: d["next_task"].update(kind="implement"),
            "rework requested": lambda s, d: d.update(status="REWORK"),
            "unverified technical decision": lambda s, d: d["acceptance_criteria"][1].update(status="unverified"),
            "changed criterion": lambda s, d: d["acceptance_criteria"][0].update(criterion="Different"),
            "failed technical outcome": lambda s, d: s["validation"]["criterion_results"][1].update(status="FAIL"),
            "missing technical evidence": lambda s, d: s["validation"]["criterion_results"][1].update(evidence_refs=[]),
            "missing executed checks": lambda s, d: s["validation"].update(checks=[]),
            "failed check": lambda s, d: s["validation"]["checks"][0].update(exit_code=1),
            "failed replay": lambda s, d: s["validation"]["check_replay"].update(verdict="FAIL"),
            "missing replay": lambda s, d: s["validation"].pop("check_replay"),
            "stale replay": lambda s, d: s["validation"]["check_replay"].update(source_revision="old"),
            "stale validation": lambda s, d: s["validation"].update(source_revision="old"),
            "stale contract": lambda s, d: s["validation"].update(contract_hash="old"),
            "blocking decision": lambda s, d: d.update(findings=[{"blocking": True}]),
            "blocking validation": lambda s, d: s["validation"].update(findings=[{"blocking": True}]),
            "failed flow": lambda s, d: s["validation"]["end_to_end_result"].update(status="FAIL"),
        }
        for label, change in cases.items():
            for empty_human_evidence in (False, True):
                with self.subTest(label=label, empty_human_evidence=empty_human_evidence):
                    candidate, report = copy.deepcopy((state, decision))
                    if empty_human_evidence:
                        report["acceptance_criteria"][0]["evidence"] = ""
                    change(candidate, report)
                    self.assertIsNone(completion.artifact_review_request(candidate, report, current))
        evidence = next(iter(state["validation"]["evidence_hashes"]))
        from pathlib import Path
        Path(evidence).write_text("Changed evidence")
        self.assertIsNone(completion.artifact_review_request(state, decision, current))

    def awaiting_flow(self, summary="Both CLI flows checked; C1 human acceptance pending"):
        state, decision, current = self.fixture()
        state["validation"]["end_to_end_result"].update(status="NOT_VERIFIED", summary=summary)
        return state, decision, current

    def test_flow_awaiting_only_human_acceptance_is_presented_and_completes_only_after_approval(self):
        # #195: the approved flow ends in the person's approval, so the Validator leaves it NOT_VERIFIED.
        self.check_awaiting_flow_review("BLOCKED")

    def test_passing_verdict_awaiting_only_human_acceptance_is_presented_and_completes_only_after_approval(self):
        self.check_awaiting_flow_review("PASS")

    def check_awaiting_flow_review(self, verdict):
        state, decision, current = self.awaiting_flow()
        state["validation"]["verdict"] = verdict
        request = completion.artifact_review_request(state, decision, current)
        self.assertEqual(["C1"], request["criteria"])
        complete = {**decision, "status": "TASK_COMPLETE",
                    "acceptance_criteria": [{**row, "status": "verified"} for row in decision["acceptance_criteria"]]}
        self.assertFalse(completion.completion_ready(state, complete, current))
        lifecycle.wait_for_user(state, request)
        lifecycle.human.evaluate(state)
        lifecycle.present(state)
        goals.approve_review(state, "C1", goals.review_token(state), current)
        self.assertTrue(completion.completion_ready(state, complete, current))
        state["validation"]["end_to_end_result"]["evidence_refs"] = []
        self.assertFalse(completion.completion_ready(state, complete, current))

    def test_flow_gap_that_does_not_name_every_pending_human_criterion_is_not_a_review(self):
        for label, summary in {"unexplained": "One flow step was not executed",
                               "longer ID": "Awaiting C10 acceptance", "prefixed ID": "Awaiting XC1 acceptance",
                               "empty": ""}.items():
            with self.subTest(label=label):
                state, decision, current = self.awaiting_flow(summary)
                self.assertIsNone(completion.artifact_review_request(state, decision, current))
        state, decision, current = self.awaiting_flow()
        state["validation"]["end_to_end_result"]["evidence_refs"] = []
        self.assertIsNone(completion.artifact_review_request(state, decision, current))
        state, decision, current = self.awaiting_flow()
        state["validation"]["end_to_end_result"]["status"] = "FAIL"
        self.assertIsNone(completion.artifact_review_request(state, decision, current))
        state, decision, current = self.awaiting_flow()
        state["validation"]["verdict"] = "FAIL"
        self.assertIsNone(completion.artifact_review_request(state, decision, current))
