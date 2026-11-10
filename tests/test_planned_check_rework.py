"""A reviewer PASS refused because the approved plan's check cannot find what it names goes back to the Builder."""

import copy
import unittest

import autocode_planned_check_rework as planned
import autocode_protected_commands as protected_commands
import autocode_verification_plan as verification_plan

from . import test_autocode as base
from . import test_report_repair

runner = base.runner

COMMAND = (
    "python3 -m unittest -v tests.test_planner.DependencyAndCycleTests.test_ac2_dependency_and_cycle_errors "
    "tests.test_cli.CLITests.test_ac8_cli_success_and_exit_2"
)
# The live run's replay tail (ladder-24, claude-tiers, 2026-11-07): the tests existed under other classes.
TAIL = (
    "AttributeError: type object 'CLITests' has no attribute 'test_ac8_cli_success_and_exit_2'\n\n"
    "----------------------------------------------------------------------\nRan 2 tests in 0.001s\n\n"
    "FAILED (errors=2)\n"
)


def refusal(command=COMMAND, tail=TAIL, kind=verification_plan.PlannedCheckFailure):
    error = kind(f"Check `{command}` is required by the approved verification methods ... it exited 1")
    error.command, error.tail = command, tail
    return error


class SendBackTests(unittest.TestCase):
    def state(self):
        return {
            "iteration": 2,
            "status": "RUNNING",
            "phase": "REPORT_REPAIR",
            "next_stage": "tester",
            "current_task": {"id": "T1"},
            "goal_contract": {"hash": "h", "revision": 2},
            "pending_report_repair": {"attempts": 1},
        }

    def record(self, **fields):
        return {
            "stage": "tester_report_repair",
            "original_stage": "tester",
            "source_revision": "s1",
            "output": "/r.json",
            **fields,
        }

    def test_a_check_that_cannot_find_a_test_it_names_returns_the_task_to_the_builder(self):
        state = self.state()
        self.assertTrue(planned.send_back(state, self.record(), refusal()))
        self.assertEqual(
            ("RUNNING", "EXECUTING", "builder", 3),
            tuple(state[k] for k in ("status", "phase", "next_stage", "iteration")),
        )
        self.assertNotIn("pending_report_repair", state)
        self.assertIn(f"`{COMMAND}`", state["next_action"])
        self.assertIn("cannot find test_ac8_cli_success_and_exit_2", state["next_action"])
        self.assertIn("Do not change the plan's commands, criteria or scope", state["next_action"])
        self.assertEqual(
            {
                "task_id": "T1",
                "contract_hash": "h",
                "contract_revision": 2,
                "command": COMMAND,
                "source_revision": "s1",
                "iteration": 3,
            },
            {
                key: state[planned.KEY][0][key]
                for key in ("task_id", "contract_hash", "contract_revision", "command", "source_revision", "iteration")
            },
        )
        self.assertTrue(planned.owns_builder_round(state, "s1"))
        self.assertFalse(planned.owns_builder_round(state, "s2"))
        self.assertFalse(planned.owns_builder_round({**state, "iteration": 4}, "s1"))
        self.assertFalse(planned.owns_builder_round({**state, "current_task": {"id": "T2"}}, "s1"))
        self.assertFalse(planned.owns_builder_round({}, "s1"))

    def test_every_other_refusal_keeps_its_route(self):
        cases = {
            "an expected failure": (
                self.record(),
                refusal(command="python3 -m notes add", tail="Usage: notes <command>\n"),
            ),
            "a name the command does not give": (
                self.record(),
                refusal(tail="AttributeError: module 'x' has no attribute 'other'\n"),
            ),
            "an original gate": (self.record(), refusal(kind=protected_commands.OriginalGateFailure)),
            "a plain report error": (self.record(), ValueError("Missing summary")),
            "a Builder report": (self.record(stage="builder", original_stage=None), refusal()),
            "no source": (self.record(source_revision=None), refusal()),
            "no command": (self.record(), refusal(command=None)),
        }
        for name, (record, error) in cases.items():
            with self.subTest(name):
                state = self.state()
                before = copy.deepcopy(state)
                self.assertFalse(planned.send_back(state, record, error))
                self.assertEqual(before, state)

    def test_one_send_back_per_source_and_two_per_command_in_a_task(self):
        state = self.state()
        self.assertTrue(planned.send_back(state, self.record(), refusal()))
        self.assertFalse(planned.send_back(state, self.record(), refusal()))
        self.assertTrue(planned.send_back(state, self.record(source_revision="s2"), refusal()))
        self.assertFalse(planned.send_back(state, self.record(source_revision="s3"), refusal()))
        self.assertTrue(planned.send_back(state, self.record(source_revision="s3"), refusal(command=COMMAND + " -k x")))
        state["current_task"] = {"id": "T2"}
        self.assertTrue(planned.send_back(state, self.record(source_revision="s3"), refusal()))


class RejectionTests(unittest.TestCase):
    setUp = base.RetrofitTest.setUp
    tearDown = base.RetrofitTest.tearDown
    stage_record = test_report_repair.RepairTests.stage_record

    def test_a_refused_tester_pass_goes_to_the_builder_instead_of_pausing(self):
        self.state.update(current_task={"id": "T1"}, goal_contract={"hash": "h", "revision": 1}, next_stage="tester")
        self.state["settings"]["report_repair"] = {"max_attempts": 2}
        record = self.stage_record(stage="tester", role="tester")
        with self.assertRaises(runner.ReportRepairQueued):
            runner.reject_completed_stage(self.state, self.run, record, refusal())
        self.assertEqual(("RUNNING", "builder"), (self.state["status"], self.state["next_stage"]))
        self.assertEqual(record["source_revision"], self.state[planned.KEY][0]["source_revision"])


if __name__ == "__main__":
    unittest.main()
