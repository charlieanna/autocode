"""Public follow-up receipts survive an older Resolver response in chat history."""

import json
import sys
import unittest

import autocode_coordinate as coordinate
import autocode_taskrun as taskrun

from .test_agent_step import fixture_workspace
from .test_taskrun import BRIEF, FIXTURE_OPTIONS


@unittest.skipUnless(sys.platform == "linux", "fake providers require supported Linux")
class CoordinateReceiptCliTests(unittest.TestCase):
    def test_completed_follow_up_and_reopen_keep_exact_receipt_without_another_attempt(self):
        workspace, environment = fixture_workspace(self)
        run = taskrun.TaskRun.start(workspace, BRIEF, options=FIXTURE_OPTIONS, env=environment, timeout=240)
        view = run.advance_until_input()
        self.assertEqual("approve_plan", view["needs"]["kind"])
        self.assertIn("greet.py", run.show_goal())
        run.approve_plan(view["needs"]["token"])
        completed = run.advance_until_input()
        self.assertTrue(completed["done"], completed["needs"])

        # Fault injection into this disposable fake run only: reproduce the historical
        # display row retained by the incident, without spending a second model run
        # manufacturing an unrelated operational exhaustion. All actions and assertions
        # below use the public interface; no live campaign data is read or modified.
        path = run.run_dir / "state.json"
        fixture = json.loads(path.read_text())
        fixture.setdefault("resolver", {}).setdefault("human_responses", []).append(
            {
                "actor": "user_cli",
                "request_id": "old-repair",
                "text": "The previous runtime was repaired",
                "action": "provide_information",
                "at": "2020-01-01T00:00:00Z",
            }
        )
        path.write_text(json.dumps(fixture))
        before = run.status()
        count = len(before["usage"]["accounting"]["attempts"])
        message = "Recheck the greeting behavior against the expanded repository set, retaining correct work."
        fresh = run.follow_up(message)
        self.assertEqual(before["turn"] + 1, fresh["turn"])
        self.assertEqual(count, len(fresh["usage"]["accounting"]["attempts"]))
        self.assertTrue(coordinate.follow_up_received(fresh, before["turn"], message))
        self.assertFalse(coordinate.follow_up_received(fresh, before["turn"], "The previous runtime was repaired"))
        self.assertFalse(coordinate.follow_up_received(fresh, before["turn"] - 1, message))
        reopened = taskrun.TaskRun(run.workspace, run.run_dir, env=environment, timeout=60).status()
        self.assertTrue(coordinate.follow_up_received(reopened, before["turn"], message))
        self.assertEqual(count, len(reopened["usage"]["accounting"]["attempts"]))
        self.assertEqual(1, sum(row.get("text") == message for row in reopened["chat"]["messages"]))
