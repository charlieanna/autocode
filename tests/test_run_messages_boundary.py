"""Conversation requests preserve saved work and every protected pause."""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_interventions as inbox
import autocode_stop as stop
import autocode_taskrun as taskrun
import autocode_util as util

from tests import ROOT
from tests.test_message_context import context
from tests.test_run_messages import receipt, working


class MessageBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        (self.workspace / ".git").mkdir()
        self.run = self.workspace / ".autocode" / "runs" / "test"
        self.run.mkdir(parents=True)
        self.state = working()
        self.state["intervention_capability"] = {"supported": True, "supports_messages": True}
        self.state["workspace"] = str(self.workspace)
        self.path = self.run / "state.json"
        self.save()

    def save(self):
        util.atomic_json(self.path, self.state)

    def test_an_older_worker_cannot_acknowledge_a_message_it_does_not_understand(self):
        self.state["intervention_capability"] = {"supported": True, "supports_stop": True}
        self.save()
        with self.assertRaisesRegex(inbox.InterventionError, "conversation"):
            self.submit()
        self.assertEqual([], inbox.inspect(self.workspace, self.run)["requests"])

    def test_the_runtime_writer_advertises_message_support_only_when_it_owns_a_checkpoint(self):
        writer = stop.state_writer(util.atomic_json, util.atomic_json)
        self.state.pop("intervention_capability", None)
        writer(self.path, self.state)
        self.assertTrue(json.loads(self.path.read_text())["intervention_capability"].get("supports_messages"))

    def test_task_context_requires_a_capable_writer_and_is_part_of_receipt_identity(self):
        args = dict(request_id="task-message", kind="message", text=receipt()["text"], context=context())
        with self.assertRaisesRegex(inbox.InterventionError, "context"):
            inbox.submit(self.workspace, self.run, **args)
        stop.state_writer(util.atomic_json, util.atomic_json)(self.path, self.state)
        before = self.path.read_bytes()
        first = inbox.submit(self.workspace, self.run, **args)
        self.assertEqual(context(), first["receipt"]["context"])
        self.assertEqual(before, self.path.read_bytes())
        for altered in (None, {**context(), "participants": context()["participants"][:1]}):
            with self.assertRaisesRegex(inbox.InterventionError, "different payload"):
                inbox.submit(self.workspace, self.run, **{**args, "context": altered})
        self.consume()
        retry = inbox.submit(self.workspace, self.run, **args)
        self.assertTrue(retry["idempotent"])
        self.assertEqual(context(), retry["receipt"]["context"])
        with self.assertRaisesRegex(inbox.InterventionError, "different payload"):
            inbox.submit(self.workspace, self.run, **{**args, "context": None})

    def test_context_cannot_accompany_feedback_pause_or_stop(self):
        for kind in ("feedback", "pause", "stop"):
            with self.subTest(kind=kind), self.assertRaisesRegex(inbox.InterventionError, "context"):
                inbox.submit(
                    self.workspace,
                    self.run,
                    request_id=kind,
                    kind=kind,
                    text="" if kind != "feedback" else "change",
                    context=context(),
                )
        self.assertEqual([], inbox.inspect(self.workspace, self.run)["requests"])

    def test_cli_context_submission_and_retry_preserve_original_text_and_readonly_context(self):
        stop.state_writer(util.atomic_json, util.atomic_json)(self.path, self.state)
        packet = self.workspace / "public-participants.json"
        packet.write_text(json.dumps(context()))
        command = [
            sys.executable,
            str(ROOT / "tools/autocode.py"),
            "intervention",
            "submit",
            "--workspace",
            str(self.workspace),
            "--run-dir",
            str(self.run),
            "--request-id",
            "with-context",
            "--kind",
            "message",
            "--text",
            receipt()["text"],
            "--context-file",
            str(packet),
            "--json",
        ]
        before = self.path.read_bytes()
        for expected in (False, True):
            reply = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(0, reply.returncode, reply.stdout + reply.stderr)
            value = json.loads(reply.stdout)
            self.assertEqual(expected, value["idempotent"])
            self.assertEqual(receipt()["text"], value["receipt"]["text"])
            self.assertEqual(context(), value["receipt"]["context"])
        self.assertEqual(before, self.path.read_bytes())
        packet.write_text("{ invalid")
        refused = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(2, refused.returncode, refused.stdout + refused.stderr)
        self.assertEqual("invalid_context", json.loads(refused.stdout)["error"]["code"])

    def submit(self, identifier="m1", kind="message"):
        return inbox.submit(
            self.workspace,
            self.run,
            request_id=identifier,
            kind=kind,
            text=receipt()["text"] if kind == "message" else "",
        )

    def consume(self):
        return stop.consume(self.state, self.run, self.workspace, write_json=util.atomic_json, now=lambda: "now")

    def test_public_submission_is_pending_only_and_an_identical_retry_is_one_message(self):
        before = self.path.read_bytes()
        command = [
            sys.executable,
            str(ROOT / "tools/autocode.py"),
            "intervention",
            "submit",
            "--workspace",
            str(self.workspace),
            "--run-dir",
            str(self.run),
            "--request-id",
            "m1",
            "--kind",
            "message",
            "--text",
            receipt()["text"],
            "--json",
        ]
        first = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(0, first.returncode, first.stderr)
        retry = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(0, retry.returncode, retry.stderr)
        self.assertEqual(json.loads(first.stdout)["receipt"], json.loads(retry.stdout)["receipt"])
        self.assertTrue(json.loads(retry.stdout)["idempotent"])
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(1, len(inbox.inspect(self.workspace, self.run)["requests"]))

    def test_a_saved_boundary_schedules_the_answer_without_invalidating_the_plan(self):
        before = copy.deepcopy(self.state)
        self.submit()
        self.assertTrue(self.consume())
        self.assertEqual("RUNNING", self.state["status"])
        self.assertEqual("requirements_gather", self.state["next_stage"])
        self.assertTrue(self.state["stop_reason"])  # the invocation can yield a receipted boundary
        self.assertEqual(before["goal_contract"], self.state["goal_contract"])
        self.assertEqual(before["source_log"], self.state["source_log"])
        self.assertEqual([], self.state["brief_feedback"])
        self.assertEqual([], inbox.inspect(self.workspace, self.run)["requests"])

    def test_cli_explains_queued_discussion_handoff_without_releasing_pause(self):
        self.state.update(next_stage="answer_question", iteration=1, sessions={})
        self.save()
        self.submit("fix-request")
        self.submit("pause-request", "pause")
        self.consume()
        before = self.path.read_bytes()
        run = taskrun.TaskRun(
            self.workspace,
            self.run,
            command=[sys.executable, str(ROOT / "tools/autocode.py")],
        )
        view = run.status()
        self.assertEqual("PAUSED_INTERVENTION", view["status"])
        self.assertIn("Requirements will handle your saved message", view["recovery"]["what_happened"])
        resume = next(row for row in view["recovery"]["actions"] if row["kind"] == "resume")
        self.assertEqual("Resume with Requirements", resume["label"])
        self.assertEqual(["--resume-paused"], resume["args"])
        self.assertEqual(before, self.path.read_bytes(), "Status must not release the saved pause")
        self.assertFalse((self.run / "iterations").exists(), "Reading the handoff must not launch a provider")
        self.assertFalse(self.consume(), "reopen cannot reapply or relaunch the same message")

    def test_pause_and_stop_take_priority_and_leave_the_question_saved(self):
        for kind in ("pause", "stop"):
            with self.subTest(kind=kind):
                self.state = working()
                self.state["intervention_capability"] = {"supported": True, "supports_messages": True}
                self.state["workspace"] = str(self.workspace)
                self.save()
                self.submit("message-" + kind)
                self.submit("hold-" + kind, kind)
                self.consume()
                self.assertEqual("PAUSED_INTERVENTION", self.state["status"])
                self.assertEqual("astra_finalize", self.state["next_stage"])
                self.assertEqual("run_message", self.state["user_events"][-1]["kind"])
                if kind == "stop":
                    self.assertIsNotNone(stop.applied_stop(self.state))

    def test_message_under_an_exhausted_budget_never_releases_the_budget(self):
        self.state.update(status="PAUSED_COST_LIMIT", phase="PAUSED_OR_BLOCKED", stop_reason="cost exhausted")
        self.save()
        self.submit()
        self.consume()
        self.assertEqual("PAUSED_COST_LIMIT", self.state["status"])
        self.assertEqual("cost exhausted", self.state["stop_reason"])
        self.assertEqual("astra_finalize", self.state["next_stage"])
        self.assertEqual("run_message", self.state["user_events"][-1]["kind"])

    def test_message_submitted_before_final_result_keeps_the_exact_completion_checkpoint(self):
        self.submit()
        self.state.update(
            status="TASK_COMPLETE",
            phase="COMPLETE",
            next_stage=None,
            completed_at="finished",
            final_decision={"status": "COMPLETE"},
        )
        self.save()
        self.assertTrue(self.consume())
        self.assertEqual("requirements_gather", self.state["next_stage"])
        event = self.state["user_events"][-1]
        self.assertIsNone(event["return_checkpoint"]["next_stage"])
        self.assertEqual("finished", event["return_checkpoint"]["completed_at"])

    def test_nonchat_and_terminal_runs_refuse_a_message_that_cannot_be_answered(self):
        for change in (
            {"settings": {"spec_first": False}},
            {"status": "TASK_COMPLETE"},
            {"applied_interventions": [{"id": "stopped", "kind": "stop"}]},
        ):
            with self.subTest(change=change):
                self.state = {**working(), **change, "workspace": str(self.workspace)}
                self.save()
                before = self.path.read_bytes()
                with self.assertRaises(inbox.InterventionError):
                    self.submit()
                self.assertEqual(before, self.path.read_bytes())
                self.assertEqual([], inbox.inspect(self.workspace, self.run)["requests"])

    def test_crash_after_commit_before_acknowledgement_does_not_duplicate_the_exchange(self):
        self.submit()
        original = util.atomic_json

        def crash(path, value):
            if path == self.run / inbox.INBOX_NAME:
                raise SystemExit("simulated receipt acknowledgement loss")
            original(path, value)

        with mock.patch.object(util, "atomic_json", side_effect=crash):
            with self.assertRaises(SystemExit):
                self.consume()
        self.state = json.loads(self.path.read_text())
        self.consume()
        self.assertEqual(1, len(self.state["user_events"]))
        self.assertEqual("requirements_gather", self.state["next_stage"])
        self.assertEqual([], inbox.inspect(self.workspace, self.run)["requests"])
        self.assertTrue(self.submit()["idempotent"])
