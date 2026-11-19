"""A model-free setup check that only ran out of time waits for the host and runs again.

Live VS Code test, 2026-11-18: on a host at a load average of about 165 Kilo's setup check spent
both of its own attempts, and the run paused with PAUSED_TOOL_CONTAINMENT for the person to resume.
The same check answered once the run was resumed by hand.
"""

import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import autocode as runner
import autocode_setup_check_retry as retry
import autocode_support as s
import autocode_tool_containment as containment
from providers import kilocode_probe

from . import test_assignment_scenarios, test_goals


def timeout_pause(cause=None, status="PAUSED_TOOL_CONTAINMENT"):
    """The pause a launch's preparation raises when Kilo's setup check only ran out of time."""
    cause = cause or kilocode_probe.KiloProbeTimeout(
        "Kilo's debug agent code check exhausted its 120-second setup budget after 2 attempt(s); "
        "no model request was launched"
    )
    try:
        raise s.Paused(status, "Native tool boundary was not established: " + str(cause)) from cause
    except s.Paused as error:
        return error


class TimedOutTests(unittest.TestCase):
    def test_every_model_free_timeout_is_recognized_through_the_cause_chain(self):
        for cause in (
            kilocode_probe.KiloProbeTimeout("timed out"),
            kilocode_probe.KiloProbeIncomplete("cut off"),
            containment.SetupCheckTimeout("did not answer"),
        ):
            with self.subTest(cause=type(cause).__name__):
                self.assertTrue(retry.timed_out(timeout_pause(cause)))
        # Through a wrapper too: an unreadable variant listing is a transport refusal caused by the timeout.
        wrapper = importlib.import_module("providers.kilocode").KiloEffortError("listing timed out")
        wrapper.__cause__ = kilocode_probe.KiloProbeTimeout("timed out")
        self.assertTrue(retry.timed_out(timeout_pause(wrapper, "PAUSED_TRANSPORT_UNVERIFIED")))
        # The other import path's class is the same check.
        other = importlib.import_module("tools.providers.kilocode_probe").KiloProbeTimeout("timed out")
        self.assertTrue(retry.timed_out(timeout_pause(other)))

    def test_an_answer_is_not_a_timeout(self):
        self.assertFalse(retry.timed_out(s.Paused("PAUSED_TOOL_CONTAINMENT", "Kilo denied bash")))
        self.assertFalse(retry.timed_out(timeout_pause(PermissionError("no namespaces"))))


class StageLaunchTests(unittest.TestCase):
    setUp = test_goals.GoalTests.setUp
    tearDown = test_assignment_scenarios.AssignmentScenarios.tearDown
    seed = test_assignment_scenarios.AssignmentScenarios.seed
    approve = test_assignment_scenarios.AssignmentScenarios.approve
    greeting_contract = test_assignment_scenarios.AssignmentScenarios.greeting_contract

    def ready(self):
        self.seed()
        self.greeting_contract()
        self.state["settings"]["orchestration"]["enabled"] = False
        self.state.update(status="RUNNING", phase="EXECUTING", next_stage="builder")

    def dispatch(self, error):
        with patch.object(runner, "run_role", side_effect=error), patch.object(retry, "wait") as slept:
            result = runner.autopilot.dispatch_unit(runner, self.state, "builder", self.root, self.run)
        return result, slept

    def test_a_launch_whose_setup_check_ran_out_of_time_waits_and_runs_again_a_bounded_number_of_times(self):
        self.ready()
        for number, seconds in enumerate(retry.WAITS_SECONDS, 1):
            result, slept = self.dispatch(timeout_pause())
            self.assertEqual(runner.orchestrator.SKIP, result)
            slept.assert_called_once_with(seconds)
            self.assertEqual("RUNNING", self.state["status"])
            row = self.state["setup_check_retries"][-1]
            self.assertEqual(
                ("builder", "stage_launch", number, seconds, "PAUSED_TOOL_CONTAINMENT"),
                (row["stage"], row["where"], row["retry_number"], row["wait_seconds"], row["status"]),
            )
            self.assertIn("no model request was launched", row["reason"])
        # The allowance is spent: the pause stands, and nothing waits again.
        with self.assertRaises(s.Paused) as raised:
            self.dispatch(timeout_pause())
        self.assertEqual("PAUSED_TOOL_CONTAINMENT", raised.exception.status)
        self.assertEqual(retry.MAX_RETRIES, len(self.state["setup_check_retries"]))
        # A stage that ran since gives the next stage its whole allowance again.
        self.state.setdefault("stages", []).append({"stage": "builder", "role": "builder", "iteration": 1})
        result, slept = self.dispatch(timeout_pause())
        self.assertEqual(runner.orchestrator.SKIP, result)
        slept.assert_called_once_with(retry.WAITS_SECONDS[0])

    def test_other_pauses_launched_work_and_a_requested_pause_are_never_retried(self):
        self.ready()
        with self.assertRaises(s.Paused):
            self.dispatch(s.Paused("PAUSED_TOOL_CONTAINMENT", "Kilo denied the shell its kernel boundary"))
        self.state["active_stage"] = {"stage": "builder", "role": "builder"}
        self.assertFalse(retry.recover(runner, self.state, self.run, self.root, timeout_pause()))
        self.state.pop("active_stage")
        (self.run / "pause-requested").write_text("")
        self.assertFalse(retry.recover(runner, self.state, self.run, self.root, timeout_pause()))
        self.assertNotIn("setup_check_retries", self.state)


class BoundaryCheckTests(unittest.TestCase):
    def test_a_boundary_check_waits_until_it_answers_and_records_each_wait(self):
        state = {"next_stage": "plan_review", "stages": []}
        check = Mock(side_effect=[timeout_pause(), timeout_pause(), "ready"])
        persist = Mock()
        with tempfile.TemporaryDirectory() as run_dir, patch.object(retry, "wait") as slept:
            answer = retry.patiently(check, state, Path(run_dir), persist=persist, where="tool_admission")
        self.assertEqual("ready", answer)
        self.assertEqual(list(retry.WAITS_SECONDS[:2]), [call.args[0] for call in slept.call_args_list])
        self.assertEqual(2, persist.call_count)
        self.assertEqual(["tool_admission"] * 2, [row["where"] for row in state["setup_check_retries"]])

    def test_a_boundary_check_stops_waiting_when_the_allowance_is_spent_or_the_answer_is_real(self):
        state = {"next_stage": "builder", "stages": []}
        with tempfile.TemporaryDirectory() as run_dir, patch.object(retry, "wait"):
            check = Mock(side_effect=timeout_pause())
            with self.assertRaises(s.Paused):
                retry.patiently(check, state, Path(run_dir), persist=Mock(), where="tool_admission")
            self.assertEqual(retry.MAX_RETRIES + 1, check.call_count)
            refused = Mock(side_effect=s.Paused("PAUSED_TOOL_CONTAINMENT", "Kilo denied bash"))
            with self.assertRaises(s.Paused):
                retry.patiently(refused, {"next_stage": "tester"}, Path(run_dir), persist=Mock(), where="x")
            self.assertEqual(1, refused.call_count)


if __name__ == "__main__":
    unittest.main()
