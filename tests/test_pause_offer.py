"""The pause a client may request while a run works (#1598): pure offer rules and the status view."""

import json
import subprocess
import unittest

import autocode as runner
import autocode_pause_offer as pause_offer
import autocode_support as support

from tests import test_subprocess

CAPABLE = {"intervention_capability": {"supported": True, "version": 1}}
LIVE = {"done": False, "liveness": {"kind": "supervised"}, "controller": {"kind": "unknown"}}


class PauseOfferTests(unittest.TestCase):
    def test_a_live_stage_offers_the_exact_pause_request(self):
        offer = pause_offer.project(CAPABLE, LIVE, run_dir="/w/.autocode/runs/r", workspace="/w")
        self.assertEqual(
            [
                "intervention",
                "submit",
                "--workspace",
                "/w",
                "--run-dir",
                "/w/.autocode/runs/r",
                "--request-id",
                "REQUEST_ID",
                "--kind",
                "pause",
                "--json",
            ],
            offer["next_command"],
        )
        self.assertFalse(offer["requested"])
        self.assertEqual("Pause after this step", offer["label"])

    def test_a_live_controller_or_lock_holder_counts_as_working(self):
        running = {"done": False, "controller": {"kind": "running", "checked": True, "alive": True}}
        self.assertIsNotNone(pause_offer.project(CAPABLE, running))
        self.assertIsNotNone(pause_offer.project(CAPABLE, {"done": False, "controller": {"kind": "busy"}}))
        unchecked = {"done": False, "controller": {"kind": "running", "checked": False, "alive": None}}
        self.assertIsNone(pause_offer.project(CAPABLE, unchecked), "an unverified owner is not live work")

    def test_nothing_to_pause_when_the_run_is_not_working(self):
        for view in (
            {"done": False, "liveness": {"kind": "unknown"}, "controller": {"kind": "ended"}},
            {"done": False, "controller": {"kind": "boundary"}},
            {**LIVE, "done": True},
            {},
        ):
            self.assertIsNone(pause_offer.project(CAPABLE, view), view)

    def test_a_runner_that_cannot_consume_a_pause_or_a_stopped_run_offers_none(self):
        self.assertIsNone(pause_offer.project({}, LIVE))
        self.assertIsNone(pause_offer.project({"intervention_capability": {"supported": False}}, LIVE))
        self.assertIsNone(pause_offer.project({**CAPABLE, "stop_intent": {"id": "s"}}, LIVE))
        self.assertIsNone(pause_offer.project({**CAPABLE, "applied_interventions": [{"kind": "stop"}]}, LIVE))

    def test_a_queued_pause_is_reported_without_a_second_command(self):
        offer = pause_offer.project(
            CAPABLE, LIVE, pending=[{"id": "f", "kind": "feedback"}, {"id": "p", "kind": "pause"}]
        )
        self.assertTrue(offer["requested"])
        self.assertIsNone(offer["next_command"])
        self.assertFalse(pause_offer.project(CAPABLE, LIVE, pending=[{"id": "f", "kind": "feedback"}])["requested"])


class StopNowOfferTests(unittest.TestCase):
    STOPPABLE = {"intervention_capability": {"supported": True, "version": 1, "supports_stop_now": True}}

    def test_a_runner_that_can_end_a_step_offers_stop_now_beside_the_pause(self):
        # #3263: a client had no way to end a runaway step; the person paid for up to an hour.
        offer = pause_offer.project(self.STOPPABLE, LIVE, run_dir="/w/.autocode/runs/r", workspace="/w")
        stop_now = offer["stop_now"]
        self.assertEqual("Stop now", stop_now["label"])
        self.assertIn("ends now", stop_now["effect"])
        self.assertFalse(stop_now["requested"])
        self.assertEqual([*offer["next_command"][:-1], "--now", "--json"], stop_now["next_command"])

    def test_an_older_runner_offers_only_the_boundary_pause(self):
        self.assertNotIn("stop_now", pause_offer.project(CAPABLE, LIVE))

    def test_a_queued_boundary_pause_still_offers_stop_now_and_a_queued_stop_now_offers_neither(self):
        waiting = pause_offer.project(self.STOPPABLE, LIVE, pending=[{"id": "p", "kind": "pause"}])
        self.assertIsNone(waiting["next_command"])
        self.assertIsNotNone(waiting["stop_now"]["next_command"], "a boundary pause lets the step run on")
        stopping = pause_offer.project(self.STOPPABLE, LIVE, pending=[{"id": "n", "kind": "pause", "now": True}])
        self.assertTrue(stopping["stop_now"]["requested"])
        self.assertIsNone(stopping["stop_now"]["next_command"])
        self.assertIsNone(stopping["next_command"])


class PauseOfferStatusTests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ("--engine", "codex")

    def view(self, run):
        return json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)["view"]

    def test_status_names_pause_only_while_the_run_is_owned_and_working(self):
        self.launch(["Build greeting"], 2)
        run, state = self.saved()
        self.assertIsNone(self.view(run)["pause"], "a run waiting for a person has nothing to pause")
        state["status"] = "RUNNING"
        runner.write_json(run / "state.json", state)
        with support.workspace_lock(self.project):
            offer = self.view(run)["pause"]
        self.assertEqual(
            ["intervention", "submit", "--workspace", str(self.project), "--run-dir", str(run)],
            offer["next_command"][:6],
        )
        argv = [word.replace("REQUEST_ID", "pause-1") for word in offer["next_command"]]
        submitted = subprocess.run(
            [*self.entry, *argv], cwd=self.root, env=self.env, capture_output=True, text=True, timeout=60
        )
        self.assertEqual(0, submitted.returncode, submitted.stdout + submitted.stderr)
        self.assertEqual("pause", json.loads(submitted.stdout)["receipt"]["kind"])
        with support.workspace_lock(self.project):
            queued = self.view(run)["pause"]
        self.assertEqual((True, None), (queued["requested"], queued["next_command"]))
        # The runner records that it can end a step on request, so the view also offers Stop now (#3263).
        stop_now = queued["stop_now"]
        self.assertFalse(stop_now["requested"], "a boundary pause lets the step run on")
        argv = [word.replace("REQUEST_ID", "stop-1") for word in stop_now["next_command"]]
        self.assertEqual(["--kind", "pause", "--now", "--json"], argv[-4:])
        submitted = subprocess.run(
            [*self.entry, *argv], cwd=self.root, env=self.env, capture_output=True, text=True, timeout=60
        )
        self.assertEqual(0, submitted.returncode, submitted.stdout + submitted.stderr)
        self.assertIs(True, json.loads(submitted.stdout)["receipt"]["now"])
        with support.workspace_lock(self.project):
            stopping = self.view(run)["pause"]["stop_now"]
        self.assertEqual((True, None), (stopping["requested"], stopping["next_command"]))
