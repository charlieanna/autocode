"""An operator edits the source of a paused run (issue #302): no stale result is applied, and each
leftover record names or takes the step that gets the run validated on the current source.

Offline: Git fixture workspaces, the real CLI entry in-process, a fake provider. No model is called."""
import copy
import json
from pathlib import Path
import unittest

import autocode as runner
import autocode_completion as completion_gate
import autocode_goal_lifecycle as lifecycle
import autocode_resolver_human as resolver_human
import autocode_support as s
from goal_fixtures import body, envelope
from tests import test_goals


class PausedSourceEditTests(unittest.TestCase):
    # The goal tests' Git fixture and CLI driver; binding the class here would run its tests again.
    setUp, approve, draft, decision, validation, invoke = (getattr(test_goals.GoalTests, name) for name in (
        "setUp", "approve", "draft", "decision", "validation", "invoke"))

    def edit_test_file(self):
        before = s.snapshot(self.root)["revision"]
        path = self.root / "test_greeting.py"
        path.write_text(path.read_text() + "\n# Operator edit while the run is paused\n")
        return before, s.snapshot(self.root)["revision"]

    def provider(self, answers):
        """A fake provider: each call takes the next answer for the stage the runner dispatched."""
        calls = []

        def run_role(**kwargs):
            state = kwargs["state"]
            calls.append({"stage": state["next_stage"], "report_only": bool(kwargs.get("report_only"))})
            if not answers:
                raise s.Paused("PAUSED_TEST_LAUNCH", "Offline stage admission verified")
            return answers.pop(0)(state)
        return calls, run_role

    def completion_owner_requests_validation(self, state):
        criteria = [{**c, "status": "verified", "evidence": "event:check"} for c in state["acceptance_criteria"]]
        value = {**envelope(state), "status": "VALIDATE", "acceptance_criteria": criteria,
                 "next_objective": "Validate the current source", "next_task": {
                     "kind": "validate", "milestone_id": "M1", "requirements": ["Greet valid names; reject empty names"],
                     "acceptance_criteria": ["C1"], "validation_plan": ["Execute both CLI cases"]},
                 "agreed_limitations": [], "blocker": "", "evidence": ["event:check"],
                 "plan": ["Validate"], "affected_paths": ["greet.py"]}
        output = self.run / "completion-validate.json"
        s.atomic_json(output, value)
        return value, {"output": str(output), "duration_seconds": 1, "source_revision": s.snapshot(self.root)["revision"]}

    def validator(self, state):
        evidence = self.run / "fresh-sol.jsonl"
        evidence.write_text(json.dumps({"type": "item.completed", "item": {"id": "check", "type": "command_execution",
            "command": "python3 -m unittest", "exit_code": 0, "aggregated_output": "PASS"}}))
        value = {**envelope(state), "verdict": "PASS", "findings": [], "unverified_criteria": [],
                 "checks_run": ["python3 -m unittest"],
                 "checks": [{"command": "python3 -m unittest", "exit_code": 0, "evidence_ref": "event:check"}],
                 "criterion_results": [{"id": c["id"], "status": "PASS", "evidence_refs": ["event:check"]}
                                       for c in state["acceptance_criteria"]],
                 "end_to_end_result": {"status": "PASS", "summary": "Both CLI flows checked",
                                       "evidence_refs": ["event:check"]}}
        return value, {"events": str(evidence), "output": str(evidence), "duration_seconds": 1,
                       "source_revision": s.snapshot(self.root)["revision"]}

    def completed_validator_attempt(self):
        """A finished Validator response whose report the runner had not applied yet."""
        current = s.snapshot(self.root)
        base = self.run / "iterations/002/sol-01"
        base.parent.mkdir(parents=True)
        s.atomic_json(base.with_suffix(".before.json"), current)
        s.atomic_json(base.with_suffix(".after.json"), current)
        base.with_suffix(".jsonl").write_text("\n".join(json.dumps(event) for event in (
            {"type": "thread.started", "thread_id": "sol-session"},
            {"type": "item.completed", "item": {"id": "check", "type": "command_execution",
             "command": "python3 -m unittest", "exit_code": 0, "aggregated_output": "PASS"}},
            {"type": "turn.completed"})) + "\n")
        value, _ = self.validator(self.state)
        s.atomic_json(base.with_suffix(".json"), value)
        s.atomic_json(base.with_suffix(".schema.json"), {"type": "object"})
        return {"role": "sol", "stage": "sol", "iteration": 2, "exit_code": 0, "duration_seconds": 1,
                "output": str(base.with_suffix(".json")), "events": str(base.with_suffix(".jsonl")),
                "before_ref": str(base.with_suffix(".before.json")), "after_ref": str(base.with_suffix(".after.json")),
                "schema": str(base.with_suffix(".schema.json")), "source_revision": current["revision"]}

    def test_accept_completion_names_stale_validation_and_resume_revalidates_before_acceptance(self):
        self.approve()
        lifecycle.assign_task(self.state, self.decision(), s.snapshot(self.root))
        self.validation()
        self.state.update(status="PAUSED_INVALID_OUTPUT", phase="PAUSED_OR_BLOCKED", next_stage="astra_review",
                          stop_reason="Completion report could not be produced")
        old, new = self.edit_test_file()

        self.assertEqual(2, self.invoke("--accept-completion"))
        self.assertIn(f"Validation is stale: it checked source {old[:12]}, but the workspace is now at {new[:12]}",
                      self.stderr)
        self.assertIn("Resume with --resume-paused to re-validate", self.stderr)
        self.assertNotIn("requires current passing independent evidence", self.stderr)
        self.assertEqual("PAUSED_INVALID_OUTPUT", self.state["status"])

        calls, provider = self.provider([self.completion_owner_requests_validation, self.validator])
        self.assertEqual(2, self.invoke("--resume-paused", "--no-chat", role=provider))
        self.assertEqual(["astra_review", "sol", "astra_review"], [call["stage"] for call in calls])
        self.assertEqual(new, self.state["validation"]["source_revision"])

        self.assertEqual(0, self.invoke("--accept-completion"))
        self.assertEqual("TASK_COMPLETE", self.state["status"])
        self.assertEqual(new, self.state["validation"]["source_revision"])

    def test_accept_completion_names_a_validation_of_another_task(self):
        self.approve()
        lifecycle.assign_task(self.state, self.decision(), s.snapshot(self.root))
        self.validation()
        validated = self.state["current_task"]["id"]
        follow_up = copy.deepcopy(self.decision())
        follow_up["next_objective"] = "Record the release note"
        lifecycle.assign_task(self.state, follow_up, s.snapshot(self.root))
        current = self.state["current_task"]["id"]
        self.assertNotEqual(validated, current)
        with self.assertRaisesRegex(ValueError, f"Validation is stale: it belongs to task {validated}, "
                                                f"but the current task is {current}"):
            runner.accept_completion(self.state, self.root)
        self.assertNotEqual("TASK_COMPLETE", self.state["status"])

    def queue_validator_report_repair(self):
        self.state["settings"]["report_repair"] = {"max_attempts": 2}
        # run_role saves the provider session before the runner reads (and here rejects) the report.
        self.state.setdefault("sessions", {})["sol"] = "sol-session"
        with self.assertRaises(runner.ReportRepairQueued):
            runner.reject_completed_stage(self.state, self.run, self.completed_validator_attempt(),
                                          ValueError("Missing summary"))
        self.state["next_stage"] = "sol"
        return copy.deepcopy(self.state["pending_report_repair"])

    def test_resume_archives_a_report_repair_left_stale_by_a_paused_source_edit(self):
        self.approve()
        lifecycle.assign_task(self.state, self.decision(), s.snapshot(self.root))
        pending = self.queue_validator_report_repair()
        old, new = self.edit_test_file()

        calls, provider = self.provider([])
        self.assertEqual(2, self.invoke("--no-chat", role=provider))
        self.assertEqual([], calls)
        self.assertEqual("PAUSED_STALE_VALIDATION", self.state["status"])
        self.assertIn("--resume-paused to archive the repair", self.stderr)
        self.assertEqual(pending, self.state["pending_report_repair"])

        self.assertEqual(2, self.invoke("--resume-paused", "--no-chat", role=provider))
        self.assertIn(f"Discarded stale sol report repair from source {old[:12]}; the workspace is now at {new[:12]}",
                      self.stdout)
        self.assertEqual([("sol", False)], [(call["stage"], call["report_only"]) for call in calls])
        self.assertNotIn("pending_report_repair", self.state)
        self.assertEqual(pending, self.state["report_repair_archive"][-1]["repair"])
        self.assertTrue(all(Path(path).is_file() for path in pending["pins"]))
        # The fresh attempt does not resume the session that judged the old source.
        self.assertNotIn("sol", self.state["sessions"])
        self.assertEqual("sol-session", self.state["session_rotations"][-1]["old_session"])

    def exhausted_report_repair_published_after_a_source_edit(self):
        """The repair's attempts ran out at the same error (reject_completed_stage keeps the repair and pauses
        for repeated failure), the operator edited the source, and a plain invocation published AutoResolver's
        operational request."""
        pending = self.queue_validator_report_repair()
        pending["attempts"] = self.state["pending_report_repair"]["attempts"] = 2
        self.state.update(status="PAUSED_REPEATED_FAILURE", phase="PAUSED_OR_BLOCKED",
                          stop_reason="Completed sol output was rejected (Missing summary); attempt archived. "
                                      "Consecutive attempts at this source failed with the same error.")
        self.edit_test_file()
        self.assertEqual(2, self.invoke("--no-chat"))
        request = resolver_human.current(self.state)
        self.assertEqual(("WAITING_FOR_USER", "operational_exhaustion"), (self.state["status"], request["scope"]))
        return pending, request

    def test_resume_leaves_a_published_operational_request_and_its_stale_repair_alone(self):
        # Issue #302 review: an interactive resume archived the repair, withdrew AutoResolver's request as a side
        # effect and launched the Validator, although the same command with --no-chat (rightly) held.
        self.approve()
        lifecycle.assign_task(self.state, self.decision(), s.snapshot(self.root))
        pending, request = self.exhausted_report_repair_published_after_a_source_edit()
        calls, provider = self.provider([])

        self.assertEqual(2, self.invoke("--resume-paused", "--no-chat", role=provider))
        self.assertIn("AutoResolver retained the operational request", self.stdout)
        self.assertEqual(request, resolver_human.current(self.state))
        self.assertEqual(2, self.invoke("--resume-paused", "--chat", role=provider))

        self.assertEqual([], calls)
        escalation = self.state["resolver"]["human_escalations"][request["request_id"]]
        self.assertEqual("pending", escalation["status"])  # answered or withdrawn only by its own actions
        self.assertEqual(pending["original"], self.state["pending_report_repair"]["original"])
        self.assertNotIn("report_repair_archive", self.state)
        self.assertEqual("sol-session", self.state["sessions"]["sol"])

    def test_resume_of_an_interrupted_stale_report_repair_starts_the_stage_afresh(self):
        self.approve()
        lifecycle.assign_task(self.state, self.decision(), s.snapshot(self.root))
        pending = self.queue_validator_report_repair()
        self.assertEqual(("RUNNING", "REPORT_REPAIR"), (self.state["status"], self.state["phase"]))
        self.edit_test_file()

        calls, provider = self.provider([])
        self.assertEqual(2, self.invoke("--resume-paused", "--no-chat", role=provider))
        self.assertEqual([("sol", False)], [(call["stage"], call["report_only"]) for call in calls])
        self.assertEqual(pending, self.state["report_repair_archive"][-1]["repair"])

    def test_resume_keeps_a_report_repair_whose_pinned_evidence_changed(self):
        self.approve()
        lifecycle.assign_task(self.state, self.decision(), s.snapshot(self.root))
        pending = self.queue_validator_report_repair()
        self.edit_test_file()
        Path(pending["original"]["events"]).write_text("tampered\n")

        calls, provider = self.provider([])
        self.assertEqual(2, self.invoke("--resume-paused", "--no-chat", role=provider))
        self.assertEqual([], calls)
        self.assertEqual("PAUSED_STALE_VALIDATION", self.state["status"])
        self.assertIn("Saved report-repair inputs changed", self.stderr)
        self.assertEqual(pending, self.state["pending_report_repair"])
        self.assertNotIn("report_repair_archive", self.state)

    def test_only_a_moved_source_makes_a_repair_stale(self):
        self.approve()
        lifecycle.assign_task(self.state, self.decision(), s.snapshot(self.root))
        self.queue_validator_report_repair()
        self.assertIsNone(runner.stale_report_repair(self.state, self.root))  # same source: the repair still runs
        old, new = self.edit_test_file()
        self.assertEqual((old, new), runner.stale_report_repair(self.state, self.root))
        changes = {"an attempt is still active": {"active_stage": {"stage": "sol"}},
                   "uncertain artifacts await reconciliation": {"uncertain_artifacts": [{"path": "x"}]},
                   "the goal changed": {"goal_contract": {**self.state["goal_contract"], "hash": "another-goal"}},
                   "another stage is next": {"next_stage": "astra_review"},
                   "a person is asked": {"status": "WAITING_FOR_USER"},
                   "an AutoResolver request is published": {resolver_human.PUBLIC: {"scope": "operational_exhaustion"}},
                   "an AutoResolver request is queued": {resolver_human.PRIVATE: {"scope": "blocker"}}}
        for why, change in changes.items():
            with self.subTest(why):
                self.assertIsNone(runner.stale_report_repair({**copy.deepcopy(self.state), **change}, self.root))

    def test_read_only_result_left_stale_by_a_source_edit_names_the_abandon_step(self):
        self.approve()
        lifecycle.assign_task(self.state, self.decision(), s.snapshot(self.root))
        record = self.completed_validator_attempt()
        old, new = self.edit_test_file()
        self.state.update(status="PAUSED_INTERRUPTED", phase="PAUSED_OR_BLOCKED", next_stage="sol",
                          active_stage=record)

        calls, provider = self.provider([])
        self.assertEqual(2, self.invoke("--resume-paused", "--no-chat", role=provider))
        self.assertEqual([], calls)
        self.assertIn(f"sol ran on source {old[:12]}, the workspace is now at {new[:12]}; its result is not applied",
                      self.stderr)
        self.assertIn("--abandon-stage 002/sol-01", self.stderr)
        self.assertNotIn("validation", self.state)

        self.assertEqual(0, self.invoke("--abandon-stage", "002/sol-01"))
        self.assertEqual(2, self.invoke("--resume-paused", "--no-chat", role=provider))
        self.assertEqual([("sol", False)], [(call["stage"], call["report_only"]) for call in calls])

    def assert_recovered_result_set_aside(self, record, foreign_field):
        recovered = json.loads(Path(record["output"]).read_text())
        expected = envelope(self.state)
        self.assertNotEqual(expected[foreign_field], recovered[foreign_field])
        retained = {key: Path(record[key]).read_bytes()
                    for key in ("output", "events", "before_ref", "after_ref")}
        current = s.snapshot(self.root)["revision"]
        self.state.update(status="PAUSED_INTERRUPTED", phase="PAUSED_OR_BLOCKED", next_stage="sol", active_stage=record)
        calls, provider = self.provider([])
        self.assertEqual(2, self.invoke("--resume-paused", "--no-chat", role=provider))
        self.assertEqual([("sol", False)], [(call["stage"], call["report_only"]) for call in calls])

        # Explicit resume discards the foreign report and starts a fresh attempt. Intercept that
        # attempt before it can answer: the old PASS must not appear as current validation.
        self.assertEqual(0, self.invoke("--status", "--inspect-evidence"))
        view = json.loads(self.stdout)["view"]
        self.assertFalse(view["done"])
        self.assertEqual("not_recorded", view["verification"]["freshness"])
        self.assertIsNone(view["verification"]["report_token"])
        self.assertIsNone(view["evidence"]["validator_source_revision"])
        self.assertIsNone(view["evidence"]["check_replay"])
        self.assertTrue(all(row["state"] == "unchecked" for row in view["verification"]["coverage"]))
        attempts = [row for row in view["usage"]["accounting"]["attempts"]
                    if row["attempt_id"] == "002/sol-01" and row["rejected"]]
        self.assertEqual(1, len(attempts))
        archived = attempts[0]
        archive = Path(archived["output"]).parent
        self.assertNotEqual(Path(record["output"]).parent, archive)
        for key, original in retained.items():
            with self.subTest(artifact=key):
                self.assertEqual(original, (archive / Path(record[key]).name).read_bytes())
                self.assertFalse(Path(record[key]).exists())

        # A second authorized resume gets a real fixture answer. The runner must replay its
        # checks, bind the result to the current approved contract/task, and retain the archive.
        fresh_calls, provider = self.provider([self.validator])
        self.assertEqual(2, self.invoke("--resume-paused", "--no-chat", role=provider))
        self.assertEqual(["sol", "astra_review"], [call["stage"] for call in fresh_calls])
        self.assertEqual(0, self.invoke("--status", "--inspect-evidence"))
        view = json.loads(self.stdout)["view"]
        verification = view["verification"]
        self.assertEqual("current", verification["freshness"])
        self.assertIsNotNone(verification["report_token"])
        self.assertEqual(f"r{expected['contract_revision']}:{expected['contract_hash']}",
                         verification["contract_token"])
        self.assertEqual(expected["task_id"] or None, verification["task_id"])
        self.assertEqual(current, verification["source_revision"])
        self.assertEqual([("C1", "checked", "PASS")],
                         [(row["id"], row["state"], row["recorded_status"]) for row in verification["coverage"]])
        replay = view["evidence"]["check_replay"]
        self.assertEqual(("PASS", current), (replay["verdict"], replay["source_revision"]))
        self.assertEqual([("python3 -m unittest", 0)],
                         [(row["command"], row["exit_code"]) for row in replay["checks"]])
        self.assertIn(archived, view["usage"]["accounting"]["attempts"])
        for key, original in retained.items():
            self.assertEqual(original, (archive / Path(record[key]).name).read_bytes())

    def test_resume_never_applies_a_recovered_result_of_another_task(self):
        self.approve()
        lifecycle.assign_task(self.state, self.decision(), s.snapshot(self.root))
        record = self.completed_validator_attempt()
        follow_up = copy.deepcopy(self.decision())
        follow_up["next_objective"] = "Validate again"
        lifecycle.assign_task(self.state, follow_up, s.snapshot(self.root))
        self.assert_recovered_result_set_aside(record, "task_id")

    def test_resume_never_applies_a_recovered_result_of_another_goal_revision(self):
        self.approve()
        lifecycle.assign_task(self.state, self.decision(), s.snapshot(self.root))
        record = self.completed_validator_attempt()
        revised = body()
        revised["constraints"].append("Keep output ASCII")
        lifecycle.install_draft(self.state, revised, origin="test")
        lifecycle.human.evaluate(self.state)
        lifecycle.present(self.state)
        lifecycle.approve(self.state, runner.goals.token(self.state["goal_contract"]))
        self.assert_recovered_result_set_aside(record, "contract_hash")


class StaleValidationTests(unittest.TestCase):
    def test_names_the_source_goal_or_task_the_saved_validation_belongs_to(self):
        current = {"revision": "b" * 40}
        state = {"goal_contract": {"revision": 3, "hash": "goal-3"}, "current_task": {"id": "task-2"},
                 "validation": {"source_revision": "b" * 40, "contract_revision": 3, "contract_hash": "goal-3",
                                "task_id": "task-2"}}
        self.assertIsNone(completion_gate.stale_validation(state, current))
        self.assertIsNone(completion_gate.stale_validation({**state, "validation": {}}, current))
        cases = [({"source_revision": "a" * 40}, f"it checked source {'a' * 12}, but the workspace is now at {'b' * 12}"),
                 ({"contract_revision": 2, "contract_hash": "goal-2"},
                  "it belongs to goal revision 2, but the approved goal is now revision 3"),
                 ({"task_id": "task-1"}, "it belongs to task task-1, but the current task is task-2")]
        for change, expected in cases:
            with self.subTest(change=change):
                stale = {**state, "validation": {**state["validation"], **change}}
                self.assertEqual(expected, completion_gate.stale_validation(stale, current))


if __name__ == "__main__":
    unittest.main()
