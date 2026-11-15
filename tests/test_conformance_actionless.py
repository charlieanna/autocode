"""A headless client's actionless public outcome remains uncompleted evidence."""

import copy
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scenarios.harness.conformance import ConformanceDriver, ConformanceGap


class ActionlessConformanceTests(unittest.TestCase):
    def setUp(self):
        self.process = self.enterContext(
            mock.patch.object(subprocess, "Popen", side_effect=AssertionError("No process in client contract tests"))
        )
        self.network = self.enterContext(
            mock.patch.object(socket.socket, "connect", side_effect=AssertionError("No network in client tests"))
        )
        root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.client = ConformanceDriver(
            root / "project",
            root,
            [],
            {},
            autocode=["autocode"],
            max_steps=5,
            timeout_seconds=5,
            conformance={},
            scripted=True,
        )
        self.client.run_dir = root / "project/.autocode/runs/one"

    def tearDown(self):
        self.process.assert_not_called()
        self.network.assert_not_called()

    def assert_actionless(self, public):
        original = copy.deepcopy(public)
        self.client.person_paused = True
        with (
            mock.patch.object(self.client, "view", return_value=public),
            mock.patch.object(self.client, "take") as take,
            mock.patch.object(self.client, "call") as call,
            mock.patch.object(self.client, "decide") as decide,
            mock.patch.object(self.client, "person_command") as person,
            mock.patch.object(self.client, "note") as note,
        ):
            returned, error = None, None
            try:
                returned = self.client.until_stopped()
            except TypeError as failure:
                # Make the old nullable assumption an explicit public-contract
                # failure, not an unhandled setup/import error in the proof.
                error = type(failure).__name__
            for action in (take, call, decide, person, note):
                action.assert_not_called()
        self.assertEqual({"returned": public, "error": None}, {"returned": returned, "error": error})
        self.assertIs(returned, public, "return the observed view without fabricating completion or a new need")
        self.assertEqual(original, public, "the failure/usage evidence and completion flag stay unchanged")
        self.assertFalse(self.client.person_paused)

    def test_every_actionless_uncompleted_view_returns_without_a_status_rule(self):
        for status in ("PAUSED_PROCESS_CLEANUP", "PAUSED_INTERVENTION", "UNFAMILIAR_OUTCOME", "RUNNING"):
            with self.subTest(status=status):
                self.assert_actionless(
                    {
                        "status": status,
                        "done": False,
                        "needs": None,
                        "next_command": None,
                        "usage": {"accounting": {"attempts": [{"identity": "failed-one", "exit_code": 1}]}},
                    }
                )

    def test_a_stale_command_cannot_authorize_an_action_without_a_need(self):
        self.assert_actionless(
            {"status": "HELD", "done": False, "needs": None, "next_command": "autocode --resume-paused"}
        )

    def test_a_complete_view_remains_complete_without_taking_any_action(self):
        self.assert_actionless({"status": "TASK_COMPLETE", "done": True, "needs": None, "next_command": None})

    def test_a_genuine_plan_approval_keeps_its_named_command_and_token(self):
        offered = {
            "status": "WAITING_FOR_USER",
            "done": False,
            "needs": {"kind": "approve_plan", "token": "plan-current"},
            "next_command": "AUTOCODE_APPROVAL_TOKEN=plan-current autocode --approve-goal -",
        }
        complete = {"status": "TASK_COMPLETE", "done": True, "needs": None}

        def approve(*args, **kwargs):
            self.assertEqual("plan-current", self.client.env["AUTOCODE_APPROVAL_TOKEN"])
            return subprocess.CompletedProcess([], 0, "", "")

        with (
            mock.patch.object(self.client, "view", side_effect=[offered, complete]),
            mock.patch.object(self.client, "call", side_effect=approve) as call,
        ):
            self.assertIs(complete, self.client.until_stopped())
        call.assert_called_once_with("approve_plan", "--approve-goal", "-", action=True)
        self.assertEqual([{"gate": "approve_plan", "token": "plan-current"}], self.client.approvals)
        self.assertEqual("took", self.client.journal[0]["did"])

    def test_a_persons_own_pause_keeps_the_exact_named_resume(self):
        offered = {
            "status": "PAUSED_INTERVENTION",
            "done": False,
            "needs": {"kind": "resume"},
            "next_command": "autocode --resume-paused",
        }
        complete = {"status": "TASK_COMPLETE", "done": True, "needs": None}
        self.client.person_paused = True
        with (
            mock.patch.object(self.client, "view", side_effect=[offered, complete, complete]),
            mock.patch.object(self.client, "call", return_value=subprocess.CompletedProcess([], 0, "", "")) as call,
        ):
            self.assertIs(complete, self.client.until_stopped())
        call.assert_called_once_with("resume", "--resume-paused", action=False)
        self.assertEqual("--resume-paused", self.client.journal[0]["what"])

    def test_a_named_approval_checkpoint_still_stops_before_execution(self):
        offered = {"status": "WAITING", "done": False, "needs": {"kind": "approve_plan"}}
        with (
            mock.patch.object(self.client, "view", return_value=offered),
            mock.patch.object(self.client, "take") as take,
        ):
            self.assertIs(offered, self.client.until_stopped("needs:approve_plan"))
        take.assert_not_called()

    def test_a_missing_named_resume_still_refuses_instead_of_inventing_one(self):
        self.client.person_paused = True
        offered = {"status": "PAUSED_INTERVENTION", "done": False, "needs": {"kind": "resume"}}
        with (
            mock.patch.object(self.client, "view", return_value=offered),
            mock.patch.object(self.client, "take") as take,
            self.assertRaisesRegex(ConformanceGap, "names no next_command"),
        ):
            self.client.until_stopped()
        take.assert_not_called()
