"""A stalled Tester continues its archived investigation without inheriting a verdict."""

import copy
import importlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_source_scope as source_scope
import autocode_stage_context as stage_context
import autocode_support as support

from . import test_partial_work


class PartialReviewTests(unittest.TestCase):
    setUp = test_partial_work.PartialWorkTests.setUp
    git = test_partial_work.PartialWorkTests.git
    admit = test_partial_work.PartialWorkTests.admit
    timeout = test_partial_work.PartialWorkTests.timeout
    intervening_review = test_partial_work.PartialWorkTests.intervening_review

    @property
    def partial(self):
        return importlib.import_module("autocode_partial_review")

    def stalled_tester(self):
        self.admit(stage="tester")
        self.record.update(timeout_kind="idle", criteria_revision=1)
        self.state["criteria_revision"] = 1
        self.state["sessions"] = {"tester": "stalled-session"}
        rows = [
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "id": f"check-{n}",
                    "command": f"python -m unittest tests.test_{n}",
                    "exit_code": 1 if n == 3 else 0,
                    "aggregated_output": "FAILED (failures=1)" if n == 3 else "OK",
                },
            }
            for n in range(20)
        ]
        rows.extend(
            [
                {
                    "type": "item.updated",
                    "item": {"type": "agent_message", "id": "notes", "text": "Found an edge case in check 3"},
                },
                {
                    "type": "item.started",
                    "item": {"type": "command_execution", "id": "unfinished", "command": "unfinished check"},
                },
            ]
        )
        Path(self.record["events"]).write_text("".join(json.dumps(row) + "\n" for row in rows))

    def packet(self):
        prompt, _ = stage_context.context_packet(self.state, "tester", self.run / "state.json")
        return json.loads(prompt.split("\nCURRENT HANDOFF DATA\n", 1)[1])

    def test_idle_retry_receives_completed_work_and_findings_without_a_verdict(self):
        self.stalled_tester()
        recovery = self.timeout()
        self.state = support.read(self.run / "state.json")
        handoff = self.packet()
        self.assertIn("partial_review", handoff["recovery_context"])
        retained = handoff["recovery_context"]["partial_review"]
        self.assertTrue(retained["available"], retained)
        self.assertEqual("unverified", retained["verification"])
        self.assertEqual(20, len(retained["commands"]))
        self.assertEqual("python -m unittest tests.test_3", retained["commands"][3]["command"])
        self.assertEqual(1, retained["commands"][3]["exit_code"])
        self.assertEqual("FAILED (failures=1)", retained["commands"][3]["output"])
        self.assertNotIn("unfinished check", json.dumps(retained))
        self.assertEqual("Found an edge case in check 3", retained["partial_text"])
        archived = Path(retained["artifacts"]["events"]["path"])
        self.assertTrue(archived.is_file())
        self.assertIn("archived-", str(archived))
        self.assertEqual(support.file_hash(archived), retained["artifacts"]["events"]["sha256"])
        self.assertEqual(retained, recovery["partial_review"])
        self.assertIn("Continue", recovery["instruction"])
        self.assertNotIn("Execute the required checks for this attempt", recovery["instruction"])
        self.assertNotIn("validation", self.state)
        self.assertNotIn("tester", self.state["sessions"])
        self.assertEqual(1, self.state["consecutive_timeout_recoveries"])
        self.assertEqual(1, len(self.state["automatic_timeout_recoveries"]))
        new_events = self.directory / "tester-retry.jsonl"
        new_events.write_text("")
        with self.assertRaisesRegex(ValueError, "executed Validator event"):
            support.verify_checks(
                [{"command": "python -m unittest tests.test_0", "exit_code": 0, "evidence_ref": "event:check-0"}],
                self.root,
                new_events,
            )

    def test_intervening_review_does_not_erase_the_tester_continuation(self):
        self.stalled_tester()
        original = self.timeout().get("partial_review")
        self.intervening_review()
        handoff = self.packet()
        self.assertIn("retained_tester_work", handoff)
        self.assertEqual(original, handoff["retained_tester_work"])

    def test_second_stall_keeps_the_first_attempt_retrievable_without_nesting_packets(self):
        self.stalled_tester()
        first = self.timeout().get("partial_review")
        first_attempt = self.record
        self.record = {
            **first_attempt,
            "iteration": 2,
            "output": str(self.directory / "tester-02.json"),
            "events": str(self.directory / "tester-02.jsonl"),
            "before_ref": str(self.directory / "tester-02.before.json"),
        }
        self.before = source_scope.snapshot(self.root, self.state)
        support.atomic_json(Path(self.record["before_ref"]), self.before)
        self.admit(stage="tester")
        Path(self.record["events"]).write_text('{"type":"thread.started","thread_id":"second-stall"}\n')
        second = self.timeout()
        self.assertIn("partial_review", second)
        retained = second["partial_review"]
        self.assertEqual([first["artifacts"]], retained["earlier_attempts"])
        self.assertNotIn("partial_review", json.dumps(retained["earlier_attempts"]))
        Path(first_attempt["events"]).write_text("changed first archive\n")
        self.assertFalse(self.partial.pending(self.state, self.before["revision"])["available"])
        self.assertEqual(2, self.state["consecutive_timeout_recoveries"])

    def test_changed_source_task_contract_criteria_or_later_tester_cannot_borrow_work(self):
        self.stalled_tester()
        self.timeout()
        revision = self.before["revision"]
        self.assertTrue(self.partial.pending(self.state, revision)["available"])
        mutations = [
            lambda state: state["current_task"].update(id="T2"),
            lambda state: state["goal_contract"].update(hash="different"),
            lambda state: state.update(criteria_revision=2),
            lambda state: next(row for row in state["stages"] if row.get("stage") == "tester").pop("output"),
            lambda state: state["stages"].append(
                {"stage": "tester", "iteration": 2, "output": str(self.directory / "tester-02.json")}
            ),
        ]
        for mutate in mutations:
            current = copy.deepcopy(self.state)
            mutate(current)
            with self.subTest(mutate=mutate):
                self.assertIsNone(self.partial.pending(current, revision))
        self.assertIsNone(self.partial.pending(self.state, "changed-source"))
        (self.root / "app.py").write_text("changed after recovery\n")
        self.assertFalse(self.packet()["recovery_context"]["partial_review"]["available"])

    def test_changed_or_missing_archived_artifact_is_an_explicit_gap(self):
        self.stalled_tester()
        self.timeout()
        archived = Path(self.record["events"])
        archived.write_text("changed archive\n")
        packet = self.partial.pending(self.state, self.before["revision"])
        self.assertFalse(packet["available"])
        self.assertIn("changed", packet["reason"])
        archived.unlink()
        packet = self.partial.pending(self.state, self.before["revision"])
        self.assertFalse(packet["available"])
        self.assertIn("missing", packet["reason"])

    def test_capture_preserves_opencode_notes_tools_and_optional_report_artifacts(self):
        self.stalled_tester()
        rows = [
            {"type": "step_start", "sessionID": "session", "part": {"type": "step-start"}},
            {
                "type": "tool_use",
                "sessionID": "session",
                "part": {
                    "type": "tool",
                    "id": "tool-1",
                    "tool": "bash",
                    "state": {
                        "status": "completed",
                        "input": {"command": "python -m unittest tests.test_app"},
                        "output": "OK",
                        "metadata": {"exit": 0},
                    },
                },
            },
            {
                "type": "text",
                "sessionID": "session",
                "part": {"id": "note", "text": "Already checked the normal case; inspect the edge case next"},
            },
        ]
        Path(self.record["events"]).write_text("".join(json.dumps(row) + "\n" for row in rows))
        Path(self.record["output"]).write_text('{"unfinished":')
        packet = self.partial.capture(self.record, self.before, self.before)
        self.assertTrue(packet["available"], packet)
        self.assertEqual("Already checked the normal case; inspect the edge case next", packet["partial_text"])
        self.assertEqual("python -m unittest tests.test_app", packet["commands"][0]["command"])
        self.assertEqual("tool-1", packet["commands"][0]["event_id"])
        self.assertEqual(support.file_hash(self.record["output"]), packet["artifacts"]["output"]["sha256"])

    def test_capture_is_bounded_but_keeps_full_log_pins_and_omission_counts(self):
        self.stalled_tester()
        rows = [
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "id": f"check-{n}",
                    "command": "large command " + "\u2603" * 5000,
                    "exit_code": 0,
                    "aggregated_output": "large output " + "\u2603" * 10000,
                },
            }
            for n in range(100)
        ]
        Path(self.record["events"]).write_text("".join(json.dumps(row) + "\n" for row in rows))
        packet = self.partial.capture(self.record, self.before, self.before)
        self.assertTrue(packet["available"], packet)
        self.assertLessEqual(len(json.dumps(packet).encode()), self.partial.MAX_PACKET_BYTES)
        self.assertGreater(packet["omitted_commands"], 0)
        self.assertEqual(100, packet["omitted_commands"] + len(packet["commands"]))
        self.assertTrue(packet["commands"][-1]["truncated"])
        self.assertEqual("check-99", packet["commands"][-1]["event_id"])
        self.assertEqual(support.file_hash(self.record["events"]), packet["artifacts"]["events"]["sha256"])

    def test_source_changes_or_missing_events_are_not_usable_continuations(self):
        self.stalled_tester()
        packet = self.partial.capture(self.record, self.before, {**self.before, "revision": "changed"})
        self.assertFalse(packet["available"])
        self.assertIn("source", packet["reason"])
        Path(self.record["events"]).unlink()
        packet = self.partial.capture(self.record, self.before, self.before)
        self.assertFalse(packet["available"])
        self.assertIn("missing", packet["reason"])

    def test_capture_and_retrieval_record_read_failures(self):
        self.stalled_tester()
        with patch.object(support, "file_hash", side_effect=OSError("unreadable evidence")):
            packet = self.partial.capture(self.record, self.before, self.before)
        self.assertFalse(packet["available"])
        self.assertIn("unreadable evidence", packet["reason"])
        self.timeout()
        with patch.object(support, "file_hash", side_effect=OSError("unreadable archive")):
            packet = self.partial.pending(self.state, self.before["revision"])
        self.assertFalse(packet["available"])
        self.assertIn("unreadable archive", packet["reason"])

    def test_an_unavailable_previous_attempt_remains_an_explicit_gap(self):
        self.stalled_tester()
        packet = self.partial.capture(
            self.record,
            self.before,
            self.before,
            previous={"available": False, "reason": "Previous log changed"},
        )
        self.assertTrue(packet["available"], packet)
        self.assertEqual("Previous log changed", packet.get("earlier_work_unavailable"))

    def test_no_history_or_other_stages_do_not_create_tester_work(self):
        self.assertIsNone(self.partial.pending(self.state, self.before["revision"]))
        self.admit()
        recovery = self.timeout()
        self.assertNotIn("partial_review", recovery)
        self.assertNotIn("retained_tester_work", self.packet())
