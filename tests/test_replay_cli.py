"""Public replay orchestration refuses divergence and uses fresh decisions."""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import autocode_replay_cli as cli
import autocode_replay_view as replay_view
from autocode_replay_recording import Recording, RecordingError
from autocode_taskrun import TaskRun


def tape(*, dispatches=(), actions=(), verdict="TASK_COMPLETE"):
    recording = Mock(
        spec=Recording,
        sha256="a" * 64,
        document={
            "dispatches": [{"id": name} for name in dispatches],
            "actions": list(actions),
            "expected": {"verdict": verdict},
        },
    )
    # Drive mocks public runner decisions; bundle verification has its own
    # immutable-file tests. Model its required interface without bypassing it.
    recording.verified.return_value = SimpleNamespace(
        sha256=recording.sha256, document=copy.deepcopy(recording.document)
    )
    return recording


def view(recording, consumed, *, status="RUNNING", needs=None):
    rows = recording.document["dispatches"]
    return {
        "status": status,
        "done": status == "TASK_COMPLETE",
        "needs": needs or {"kind": "continue"},
        "replay_progress": {
            "manifest_sha256": recording.sha256,
            "checks": "fresh",
            "consumed": consumed,
            "next_dispatch": rows[consumed]["id"] if consumed < len(rows) else "end",
        },
    }


class ReplayCliContainmentTest(unittest.TestCase):
    def command(self, *args):
        return subprocess.run(
            [sys.executable, str(Path(cli.__file__).with_name("autocode.py")), "replay", *map(str, args), "--json"],
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_aliased_empty_destination_is_refused_by_the_cli_before_foreign_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            foreign = root / "foreign"
            foreign.mkdir()
            alias = root / "alias"
            alias.symlink_to(foreign, target_is_directory=True)
            result = self.command(root / "unavailable-recording", "--into", alias / ".autocode/runs/reproduction")
            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertEqual(json.loads(result.stderr)["code"], "REPLAY_REFUSED")
            self.assertIn("aliased", json.loads(result.stderr)["message"])
            self.assertEqual(list(foreign.iterdir()), [])

    def test_aliased_source_manifest_is_refused_without_creating_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            manifest = root / "recording.json"
            manifest.write_text("{}")
            alias = root / "alias.json"
            alias.symlink_to(manifest)
            destination = root / "fresh/.autocode/runs/reproduction"
            result = self.command(alias, "--into", destination)
            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertIn("aliased", json.loads(result.stderr)["message"])
            self.assertFalse(destination.parent.parent.parent.exists())
            self.assertEqual(manifest.read_text(), "{}")


class ReplayDriverTest(unittest.TestCase):
    def test_consumes_public_dispatches_and_returns_the_current_verdict(self):
        recording = tape(dispatches=("requirements-1", "builder-1"))
        task = Mock(spec=TaskRun)
        task.status.return_value = view(recording, 0)
        completed = view(recording, 2, status="TASK_COMPLETE")
        task.advance.side_effect = [view(recording, 1), completed]
        self.assertEqual(cli.drive(recording, task), completed)
        self.assertEqual(task.advance.call_count, 2)
        recording.verified.assert_called_once_with()

    def test_a_recorded_terminal_pause_can_be_reproduced_without_inventing_an_answer(self):
        recording = tape(dispatches=("requirements-1",), verdict="WAITING_FOR_USER")
        paused = view(recording, 1, status="WAITING_FOR_USER", needs={"kind": "answer"})
        task = Mock(spec=TaskRun)
        task.status.return_value = paused
        self.assertEqual(cli.drive(recording, task), paused)
        recording.verified.assert_called_once_with()
        task.answer.assert_not_called()
        task.advance.assert_not_called()

    def test_missing_foreign_or_inconsistent_public_authority_refuses_before_advance(self):
        recording = tape(dispatches=("builder-1",))
        valid = view(recording, 0)
        variants = []
        for changes in (
            {"manifest_sha256": "b" * 64},
            {"checks": "recorded"},
            {"consumed": True},
            {"consumed": -1},
            {"consumed": 2},
            {"next_dispatch": "foreign-1"},
        ):
            invalid = copy.deepcopy(valid)
            invalid["replay_progress"].update(changes)
            variants.append(invalid)
        variants.append({"status": "RUNNING", "needs": {"kind": "continue"}})
        for invalid in variants:
            with self.subTest(view=invalid):
                task = Mock(spec=TaskRun)
                task.status.return_value = invalid
                with self.assertRaises(RecordingError):
                    cli.drive(recording, task)
                task.advance.assert_not_called()

    def test_recorded_checks_cannot_return_a_current_completion(self):
        recording = tape(dispatches=("builder-1",))
        current = view(recording, 1, status="TASK_COMPLETE")
        current["replay_progress"]["checks"] = "recorded"
        task = Mock(spec=TaskRun)
        task.status.return_value = current
        with self.assertRaisesRegex(RecordingError, "cannot establish current completion"):
            cli.drive(recording, task, checks="recorded")
        task.advance.assert_not_called()

    def test_fresh_replay_cannot_silently_substitute_a_simulated_verdict(self):
        recording = tape(dispatches=("builder-1",))
        simulated = view(recording, 1, status="REPLAY_COMPLETE")
        simulated["replay_result"] = {"simulated": True, "recorded_verdict": "TASK_COMPLETE"}
        task = Mock(spec=TaskRun)
        task.status.return_value = simulated
        with self.assertRaisesRegex(RecordingError, "simulated verdict"):
            cli.drive(recording, task)
        task.advance.assert_not_called()

    def test_unrecorded_operator_request_stops_without_answering(self):
        recording = tape(dispatches=("builder-1",))
        task = Mock(spec=TaskRun)
        task.status.return_value = view(recording, 0, status="WAITING_FOR_USER", needs={"kind": "answer"})
        with self.assertRaisesRegex(RecordingError, "no matching recorded answer"):
            cli.drive(recording, task)
        task.answer.assert_not_called()
        task.advance.assert_not_called()

    def test_public_no_progress_stops_instead_of_looping(self):
        recording = tape(dispatches=("builder-1",))
        initial = view(recording, 0)
        task = Mock(spec=TaskRun)
        task.status.return_value = initial
        task.advance.return_value = copy.deepcopy(initial)
        with self.assertRaisesRegex(RecordingError, "no public progress"):
            cli.drive(recording, task)
        task.advance.assert_called_once()

    def test_exhausted_dispatches_with_an_unused_operator_action_refuse(self):
        recording = tape(
            dispatches=("builder-1",),
            actions=({"before_dispatch": "builder-1", "kind": "feedback", "match": {}, "payload": {"text": "revise"}},),
        )
        task = Mock(spec=TaskRun)
        task.status.return_value = view(recording, 1, status="TASK_COMPLETE")
        with self.assertRaises(RecordingError):
            cli.drive(recording, task)
        recording.verified.assert_called_once_with()
        task.feedback.assert_not_called()

    def test_terminal_result_cannot_bypass_failed_bundle_verification(self):
        recording = tape(dispatches=("builder-1",))
        recording.verified.side_effect = RecordingError("Recording manifest changed")
        task = Mock(spec=TaskRun)
        task.status.return_value = view(recording, 1, status="TASK_COMPLETE")
        with self.assertRaisesRegex(RecordingError, "Recording manifest changed"):
            cli.drive(recording, task)
        recording.verified.assert_called_once_with()
        task.advance.assert_not_called()


class ReplayApprovalTest(unittest.TestCase):
    def pending(self, token, criteria=None):
        return {
            "needs": {"kind": "approve_plan", "token": token},
            "displayed_plan": {
                "acceptance_criteria": criteria or ["print greeting"],
                "constraints": [],
                "permission_boundaries": [],
            },
        }

    def action(self):
        return {
            "kind": "approve_plan",
            "match": {
                "kind": "approve_plan",
                "plan": {"acceptance_criteria": ["print greeting"], "constraints": [], "permission_boundaries": []},
            },
            "payload": {},
        }

    def test_showing_plan_refreshes_current_token_before_approval(self):
        task = Mock(spec=TaskRun)
        task.status.return_value = self.pending("current-after-display")
        task.approve_plan.return_value = {"status": "READY"}
        self.assertEqual(
            cli._action(task, self.action(), self.pending("old-before-display"), tape()), {"status": "READY"}
        )
        task.show_goal.assert_called_once()
        task.status.assert_called_once()
        task.approve_plan.assert_called_once_with("current-after-display")

    def test_changed_plan_after_display_refuses_current_and_old_tokens(self):
        task = Mock(spec=TaskRun)
        task.status.return_value = self.pending("current-after-display", ["delete data"])
        with self.assertRaisesRegex(RecordingError, "operator request differs"):
            cli._action(task, self.action(), self.pending("old-before-display"), tape())
        task.approve_plan.assert_not_called()

    def test_exported_approval_retains_scope_exclusions_and_commit_delivery(self):
        event = {"kind": "goal_approval", "token": "recorded-token", "at": "2026-10-26"}
        terms = {
            "scope_exclusions": ["Not covered: changes in billing-api: accept the new payload"],
            "git_delivery": {"kind": "atomic_commit", "message": "Fix client payload"},
        }
        body = {**self.action()["match"]["plan"], **terms}
        projection = replay_view.project(
            {"user_events": [event], "goal_contract": {"approval_event": event, "body": body}}
        )
        action = projection["actions"][0]
        self.assertEqual(body, action["match"]["plan"])
        current = self.pending("fresh-token")
        current["displayed_plan"].update(terms)
        task = Mock(spec=TaskRun)
        task.status.return_value = current
        cli._action(task, action, current, tape())
        task.approve_plan.assert_called_once_with("fresh-token")

    def test_scope_or_delivery_changes_refuse_replayed_approval(self):
        for field, earlier, later in (
            ("scope_exclusions", [], ["Not covered: changes in billing-api: accept the new payload"]),
            ("scope_exclusions", ["Not covered: changes in billing-api: accept the new payload"], []),
            ("git_delivery", None, {"kind": "atomic_commit", "message": "Create a new commit"}),
            (
                "git_delivery",
                {"kind": "atomic_commit", "message": "Earlier terms"},
                {"kind": "atomic_commit", "message": "Changed terms"},
            ),
        ):
            with self.subTest(field=field, earlier=earlier, later=later):
                action = self.action()
                action["match"]["plan"][field] = earlier
                current = self.pending("fresh-token")
                current["displayed_plan"][field] = later
                task = Mock(spec=TaskRun)
                task.status.return_value = current
                with self.assertRaisesRegex(RecordingError, "operator request differs"):
                    cli._action(task, action, current, tape())
                task.approve_plan.assert_not_called()

    def test_legacy_recording_does_not_invent_authority_for_new_scope_or_delivery_terms(self):
        for field, value in (
            ("scope_exclusions", ["Not covered: changes in billing-api: accept the new payload"]),
            ("git_delivery", {"kind": "atomic_commit", "message": "Create a new commit"}),
        ):
            with self.subTest(field=field):
                current = self.pending("fresh-token")
                current["displayed_plan"][field] = value
                task = Mock(spec=TaskRun)
                task.status.return_value = current
                with self.assertRaisesRegex(RecordingError, "operator request differs"):
                    cli._action(task, self.action(), current, tape())
                task.approve_plan.assert_not_called()


if __name__ == "__main__":
    unittest.main()
