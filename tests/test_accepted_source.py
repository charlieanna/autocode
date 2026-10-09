"""An operator's edit while a repair is paused becomes the source the next diagnosis reads.

Offline: a captured recovery packet, and the goal fixture's CLI with a fake provider.
No model is called. The approved contract, task, budget, proof and evidence stay.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode as runner
import autocode_accepted_source as accepted
import autocode_args as cli_args
import autocode_resolver_recovery as recovery
import autocode_run_view as run_view
import autocode_support as support
import autocode_util as util

from tests import test_goals


class AcceptReviewedSourceTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.run = self.root / ".autocode" / "runs" / "owned"
        self.run.mkdir(parents=True)
        self.source = self.root / "app.py"
        self.source.write_text("def answer():\n    return 2\n")
        self.events = self.run / "validator.jsonl"
        self.events.write_text(json.dumps({"type": "item.completed", "item": {
            "type": "command_execution", "id": "check", "command": "python -m unittest test_app", "exit_code": 1,
            "aggregated_output": "AssertionError: 2 != 3\nRan 1 test in 0.002s\nFAILED"}}) + "\n")
        self.output = self.run / "review.json"
        self.output.write_text('{"status":"REWORK"}')
        self.validator = self.run / "validator.json"
        self.validator.write_text('{"verdict":"FAIL"}')
        self.state = {"workspace": str(self.root), "run_dir": str(self.run),
            "settings": {"roles": {"terra": {"model_pinned": True}}},
            "goal_contract": {"task_id": "approved-job", "hash": "h", "revision": 1, "body": {
                "acceptance_criteria": [{"id": "C1", "criterion": "Return 3"}]}},
            "current_task": {"id": "T1", "kind": "implement", "milestone_id": "M1",
                             "acceptance_criteria": ["C1"], "affected_paths": ["app.py"]},
            "stages": [{"stage": "sol", "output": str(self.validator), "events": str(self.events)}],
            "validation": {"task_id": "T1", "output": str(self.validator), "verdict": "FAIL", "checks": [{
                "command": "python -m unittest test_app", "exit_code": 1, "evidence_ref": "event:check"}]}}
        self.record = {"stage": "astra_review", "output": str(self.output), "source_revision": "s1"}
        self.request = {"contract_hash": "h", "task_id": "T1", "source_revision": "s1",
                        "source_output": str(self.output),
                        "evidence_hashes": {str(self.output): util.file_hash(self.output),
                                            str(self.events): util.file_hash(self.events)}}
        self.state["resolution_request"] = self.request
        self.snapshot = patch.object(util, "snapshot", return_value={"revision": "s1", "files": {"app.py": "hash"}, "head": "h"})
        self.snapshot.start()
        self.addCleanup(self.snapshot.stop)
        recovery.prepare_resolution(self.state, {"summary": "Wrong answer"}, self.record)
        self.state["status"] = "PAUSED_STALE_HANDOFF"
        self.state["stop_reason"] = "Repair diagnosis needs the current reviewed source and task"
        self.state["regression_proof"] = {"verdict": "PASS", "source_revision": "s1"}
        self.pointer = copy.deepcopy(self.request["recovery_packet"])

    def accept(self, revision="s2"):
        with patch.object(accepted.source_scope, "snapshot", return_value={"revision": revision, "files": {}, "head": "h"}):
            return accepted.accept_reviewed_source(self.state, self.root)

    def test_the_edit_becomes_the_diagnosis_source_and_the_packet_stays_on_disk(self):
        contract = copy.deepcopy(self.state["goal_contract"])
        settings = copy.deepcopy(self.state["settings"])
        proof = copy.deepcopy(self.state["regression_proof"])
        event = self.accept()
        self.assertEqual("s2", self.request["source_revision"])
        self.assertNotIn("recovery_packet", self.request)
        self.assertNotIn("novelty_hold", self.request)
        self.assertTrue(Path(self.pointer["path"]).is_file())
        self.assertEqual(self.pointer, event["recovery_packet"])
        self.assertEqual("s1", event["previous_source_revision"])
        self.assertEqual("source_accepted", event["kind"])
        self.assertEqual("user_cli", event["actor"])
        self.assertEqual(contract, self.state["goal_contract"])
        self.assertEqual(settings, self.state["settings"])
        self.assertEqual(proof, self.state["regression_proof"])
        self.assertEqual(COMMAND_VIEW, run_view.needs({
            **self.state, "status": "PAUSED_STALE_HANDOFF",
            "stop_reason": "Repair diagnosis needs the current reviewed source and task",
            "resolution_request": {"source_revision": "s1"}})["action"])

    def test_a_settings_change_is_refused_and_the_request_stays_bound(self):
        before = copy.deepcopy(self.state)
        self.state["settings"]["roles"]["terra"]["model"] = "other"
        with self.assertRaisesRegex(ValueError, "settings_hash"):
            self.accept()
        self.assertEqual(before["resolution_request"], self.state["resolution_request"])
        self.assertNotIn("user_events", self.state)

    def test_changed_evidence_is_refused(self):
        before = copy.deepcopy(self.request)
        self.output.write_text('{"status":"CHANGED"}')
        with self.assertRaisesRegex(ValueError, "evidence"):
            self.accept()
        self.assertEqual(before, self.request)

    def test_source_the_resolver_wrote_is_refused(self):
        self.state["stop_reason"] = "Resolver must leave the reviewed source unchanged"
        with self.assertRaisesRegex(ValueError, "Resolver wrote"):
            self.accept()
        self.assertEqual("s1", self.request["source_revision"])
        self.assertEqual(self.pointer, self.request["recovery_packet"])

    def test_a_pause_that_is_not_a_stale_repair_is_refused(self):
        self.state["status"] = "PAUSED_NO_PROGRESS"
        with self.assertRaisesRegex(ValueError, "paused because its reviewed source changed"):
            self.accept()
        self.assertIn("recovery_packet", self.request)

    def test_an_unchanged_source_is_refused(self):
        with self.assertRaisesRegex(ValueError, "already matches"):
            self.accept("s1")
        self.assertEqual(self.pointer, self.request["recovery_packet"])

    def test_status_names_the_command_only_for_a_source_mismatch(self):
        self.state["stop_reason"] = "Repair evidence changed; review again before resolving"
        self.assertIsNone(accepted.resume_action(self.state))
        self.assertNotIn("action", run_view.needs(self.state))


COMMAND_VIEW = "--resume-paused --accept-source-edit"


class AcceptSourceEditCliTests(unittest.TestCase):
    setUp, draft, approve, decision, invoke = (getattr(test_goals.GoalTests, name) for name in (
        "setUp", "draft", "approve", "decision", "invoke"))

    def queue_repair(self):
        self.approve()
        # Bind the packet to the settings a resume will see. A resume fills the
        # same defaults a new run already has; capturing before that looks like
        # a settings change.
        support.atomic_json(self.run / "state.json", self.state)
        args, _ = cli_args.parse(None, ["--workspace", str(self.root), "--run-dir", str(self.run), "--no-chat"],
                                 runner.opencode.DEFAULT_MODELS)
        self.state["settings"] = runner.configure(args, self.state)
        runner.apply_result(self.state, "astra_plan", self.decision(), {"output": "plan"}, self.root, self.run)
        value = self.decision("REWORK")
        output = self.run / "review.json"
        output.write_text(json.dumps(value))
        record = {"output": str(output), "source_revision": support.snapshot(self.root)["revision"]}
        runner.apply_result(self.state, "astra_review", value, record, self.root, self.run)
        return support.snapshot(self.root)["revision"]

    def test_the_flag_continues_the_resolver_on_the_edited_source(self):
        previous = self.queue_repair()
        contract = copy.deepcopy(self.state["goal_contract"])
        criteria = copy.deepcopy(self.state["acceptance_criteria"])
        proof = copy.deepcopy(self.state.get("regression_proof"))
        packet = copy.deepcopy(self.state["resolution_request"].get("recovery_packet"))
        path = self.root / "test_greeting.py"
        path.write_text(path.read_text() + "\n# Operator edit while the run is paused\n")
        current = support.snapshot(self.root)["revision"]
        self.assertNotEqual(previous, current)

        def refuse(**kwargs):
            raise AssertionError("a plain resume must not launch a writer")

        code = self.invoke("--resume-paused", role=refuse)
        self.assertEqual(2, code)
        self.assertEqual("PAUSED_STALE_HANDOFF", self.state["status"])
        self.assertEqual(previous, self.state["resolution_request"]["source_revision"])
        self.assertEqual(COMMAND_VIEW, run_view.needs(self.state)["action"])
        settings = copy.deepcopy(self.state["settings"])

        seen = {}

        def admit(**kwargs):
            state = kwargs["state"]
            seen["stage"] = state["next_stage"]
            seen["revision"] = state["resolution_request"]["source_revision"]
            seen["packet"] = "recovery_packet" in state["resolution_request"]
            raise support.Paused("PAUSED_TEST_LAUNCH", "admitted")

        code = self.invoke("--resume-paused", "--accept-source-edit", role=admit)
        self.assertEqual(2, code)
        self.assertEqual({"stage": "astra_resolve", "revision": current, "packet": False}, seen)
        self.assertEqual(current, self.state["resolution_request"]["source_revision"])
        self.assertNotIn("recovery_packet", self.state["resolution_request"])
        self.assertEqual(contract, self.state["goal_contract"])
        launched = copy.deepcopy(self.state["settings"])
        launched["roles"].pop("resolver", None)
        expected = copy.deepcopy(settings)
        expected["roles"].pop("resolver", None)
        self.assertEqual(expected, launched)
        self.assertEqual(criteria, self.state["acceptance_criteria"])
        self.assertEqual(proof, self.state.get("regression_proof"))
        if packet:
            self.assertTrue(Path(packet["path"]).is_file())
        event = self.state["user_events"][-1]
        self.assertEqual("source_accepted", event["kind"])
        self.assertEqual(previous, event["previous_source_revision"])
        self.assertEqual(current, event["source_revision"])

    def test_changed_evidence_is_rejected_and_the_saved_run_stays(self):
        self.queue_repair()
        path = self.root / "greet.py"
        path.write_text(path.read_text() + "\n# edited\n")
        def refuse(**kwargs):
            raise AssertionError("must not launch")

        self.invoke("--resume-paused", role=refuse)
        evidence = next(iter(self.state["resolution_request"]["evidence_hashes"]))
        Path(evidence).write_text("changed evidence")
        before = copy.deepcopy(self.state["resolution_request"])
        code = self.invoke("--resume-paused", "--accept-source-edit")
        self.assertEqual(2, code)
        self.assertIn("Input rejected", self.stderr)
        self.assertEqual(before, self.state["resolution_request"])


if __name__ == "__main__":
    unittest.main()
