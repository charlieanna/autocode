"""A coordinator's observer may request pauses but never launch another worker."""

import importlib
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

from tests.test_coordination_conversation import document


def watch_module():
    return importlib.import_module("autocode_coordination_message_watch")


class MessageWatchTests(unittest.TestCase):
    def setUp(self):
        self.record = document()
        self.messages = [{"id": "m1", "text": "What about conversion?"}]
        self.record["members"] = self.record["members"][:1]

    def setup_reads(self, module, view):
        stack = self.enterContext(ExitStack())
        stack.enter_context(patch.object(module.store, "read", return_value=self.record))
        stack.enter_context(patch.object(module.store, "cancelled", return_value=False))
        stack.enter_context(patch.object(module.inbox, "read", return_value=self.messages))
        task = Mock(workspace=Path("/api"), run_dir=Path("/api/run"), command=("autocode",), env={})
        task.status.return_value = view
        task.intervention_status.return_value = {"view": view, "interventions": {}}
        factory = stack.enter_context(patch.object(module.tasks, "TaskRun", return_value=task))
        submission = stack.enter_context(patch.object(module.runtime, "submit", return_value={"accepted": True}))
        return task, factory, submission

    def active_view(self):
        return {
            "workspace": "/api",
            "run_dir": "/api/run",
            "liveness": {"kind": "supervised"},
            "pause": {
                "next_command": [
                    "intervention",
                    "submit",
                    "--workspace",
                    "/api",
                    "--run-dir",
                    "/api/run",
                    "--request-id",
                    "REQUEST_ID",
                    "--kind",
                    "pause",
                    "--json",
                ]
            },
        }

    def test_only_named_pause_is_sent_with_stable_identity(self):
        module = watch_module()
        task, factory, submission = self.setup_reads(module, self.active_view())
        module.pause_pending(self.record["anchor"])
        task.intervention_status.assert_called_once()
        self.assertEqual(5, task.timeout)
        expected = module.policy.request_id(self.record, self.messages[0], "api", "pause")
        self.assertEqual({"id": expected, "kind": "pause", "text": ""}, submission.call_args.args[2])
        self.assertNotIn("--resume-paused", submission.call_args.args[1])
        task.advance.assert_not_called()
        task.resume_paused.assert_not_called()

    def test_already_requested_pause_or_settled_participant_is_left_alone(self):
        module = watch_module()
        view = self.active_view()
        task, _, submission = self.setup_reads(module, view)
        view["pause"]["requested"] = True
        module.pause_pending(self.record["anchor"])
        submission.assert_not_called()
        view["pause"].pop("requested")
        view.pop("liveness")
        module.pause_pending(self.record["anchor"])
        submission.assert_not_called()

    def test_completed_answer_does_not_repause_work_and_queued_second_message_does(self):
        module = watch_module()
        _, _, submission = self.setup_reads(module, self.active_view())
        self.record["conversation"] = {"active": {"id": "m1", "reply": {"text": "Answered"}}}
        module.pause_pending(self.record["anchor"])
        submission.assert_not_called()
        self.record["conversation"] = {"active": None, "results": [{"request_id": "m1"}]}
        module.pause_pending(self.record["anchor"])
        submission.assert_not_called()
        self.messages.append({"id": "m2", "text": "Another question"})
        module.pause_pending(self.record["anchor"])
        self.assertEqual(
            module.policy.request_id(self.record, self.messages[1], "api", "pause"), submission.call_args.args[2]["id"]
        )

    def test_admitted_conversation_needs_no_background_participant_reads(self):
        module = watch_module()
        _, factory, submission = self.setup_reads(module, self.active_view())
        self.record["conversation"] = {"active": {"id": "m1"}}
        # Admission already saved every participant at a boundary. The owner
        # observes them before each step; its pause watcher has no work here,
        # including when another message arrives before this reply finishes.
        for queued in (False, True):
            with self.subTest(second_message_queued=queued):
                if queued:
                    self.messages.append({"id": "m2", "text": "Another question"})
                self.assertEqual([], module.pause_pending(self.record["anchor"]))
                factory.assert_not_called()
                submission.assert_not_called()

    def test_newly_starting_member_is_observed_even_before_start_receipt(self):
        module = watch_module()
        task, _, submission = self.setup_reads(module, self.active_view())
        self.record["members"][0]["started"] = False
        self.record["pending"] = {"operation": "start", "target": "api"}
        module.pause_pending(self.record["anchor"])
        submission.assert_called_once()

    def test_cancelled_or_stopping_observer_never_submits(self):
        module = watch_module()
        _, _, submission = self.setup_reads(module, self.active_view())
        with patch.object(module.store, "cancelled", return_value=True):
            module.pause_pending(self.record["anchor"])
        submission.assert_not_called()
        module.pause_pending(self.record["anchor"], stopped=Mock(is_set=Mock(return_value=True)))
        submission.assert_not_called()

    def test_owned_observer_is_stopped_and_joined_even_when_action_fails(self):
        module = watch_module()
        thread, stop = Mock(), Mock()
        thread.is_alive.return_value = False
        with (
            patch.object(module.threading, "Thread", return_value=thread),
            patch.object(module.threading, "Event", return_value=stop),
        ):
            with self.assertRaisesRegex(RuntimeError, "action failure"):
                with module.watch("/anchor"):
                    raise RuntimeError("action failure")
        thread.start.assert_called_once()
        stop.set.assert_called_once()
        thread.join.assert_called_once_with(timeout=40)

    def test_polling_failure_is_retained_and_next_observation_may_recover(self):
        module = watch_module()
        stop = Mock(wait=Mock(side_effect=[False, False, True]))
        with (
            patch.object(module, "pause_pending", side_effect=[RuntimeError("public read unavailable"), []]) as pause,
            patch.object(module.util, "atomic_json") as save,
        ):
            module.poll("/anchor", ("autocode",), stop)
        self.assertEqual(2, pause.call_count)
        self.assertEqual("RuntimeError", save.call_args.args[1]["error_type"])


if __name__ == "__main__":
    unittest.main()
