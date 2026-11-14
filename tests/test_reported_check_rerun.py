"""A reviewer whose cited check does not reproduce runs again; a report repair cannot run a command."""

import copy
import unittest

import autocode_rejection_route as rejection_route
import autocode_reported_check_rerun as rerun
import autocode_verification_plan as verification_plan

# The live run's check (probe-polyglot, claude-tiers, 2026-11-14): it passed from packages/api only.
COMMAND = "cd ../web && npm test --silent"


def refusal(command=COMMAND, kind=verification_plan.ReportedCheckFailure):
    error = kind(f"Check `{command}` was reported as exit 0, but when the runner re-ran it ... it exited 1")
    error.command, error.tail = command, "sh: 1: cd: can't cd to ../web\n"
    return error


class RunAgainTests(unittest.TestCase):
    def state(self, **fields):
        return {
            "iteration": 1,
            "status": "RUNNING",
            "phase": "EXECUTING",
            "next_stage": "tester",
            "next_action": "Notes can be archived",
            "current_task": {"id": "T1"},
            "goal_contract": {"hash": "h", "revision": 2},
            **fields,
        }

    def record(self, **fields):
        return {"stage": "tester", "source_revision": "s1", "output": "/archived/validator-01.json", **fields}

    def test_the_stage_that_cited_the_check_is_next_again_and_is_told_why(self):
        state = self.state()
        self.assertTrue(rerun.run_again(state, self.record(), refusal()))
        self.assertEqual(
            ("RUNNING", "EXECUTING", "tester", 1),
            tuple(state[key] for key in ("status", "phase", "next_stage", "iteration")),
        )
        self.assertNotIn("pending_report_repair", state)
        self.assertIn(f"`{COMMAND}`", state["next_action"])
        self.assertIn("from the repository root", state["next_action"])
        self.assertIn("Runner replay: Check `cd ../web && npm test --silent` was reported", state["next_action"])
        self.assertEqual(
            [
                {
                    "task_id": "T1",
                    "contract_hash": "h",
                    "contract_revision": 2,
                    "stage": "tester",
                    "command": COMMAND,
                    "source_revision": "s1",
                    "iteration": 1,
                    "output": "/archived/validator-01.json",
                }
            ],
            [{key: value for key, value in row.items() if key != "at"} for row in state[rerun.KEY]],
        )

    def test_the_checkpoint_reviewer_doing_the_testers_job_runs_again_too(self):
        state = self.state(next_stage="review_checkpoint")
        self.assertTrue(rerun.run_again(state, self.record(stage="review_checkpoint"), refusal()))
        self.assertEqual("review_checkpoint", state["next_stage"])

    def test_a_second_refusal_at_the_same_source_keeps_the_existing_route(self):
        state = self.state()
        self.assertTrue(rerun.run_again(state, self.record(), refusal()))
        before = copy.deepcopy(state)
        self.assertFalse(rerun.run_again(state, self.record(), refusal("npm test")))
        self.assertEqual(before, state)

    def test_a_task_gets_two_runs_and_another_task_gets_its_own(self):
        state = self.state()
        self.assertTrue(rerun.run_again(state, self.record(), refusal()))
        self.assertTrue(rerun.run_again(state, self.record(source_revision="s2"), refusal()))
        self.assertFalse(rerun.run_again(state, self.record(source_revision="s3"), refusal()))
        state["current_task"] = {"id": "T2"}
        self.assertTrue(rerun.run_again(state, self.record(source_revision="s3"), refusal()))
        self.assertEqual(["T1", "T1", "T2"], [row["task_id"] for row in state[rerun.KEY]])

    def test_any_other_refusal_is_left_as_it_was(self):
        cases = {
            "a planned check has its own route": (
                self.state(),
                self.record(),
                refusal(kind=verification_plan.PlannedCheckFailure),
            ),
            "another report error": (self.state(), self.record(), ValueError("Check `x` was reported as exit 0")),
            "a refused report repair": (
                self.state(pending_report_repair={"attempts": 1}),
                self.record(stage="tester_report_repair", original_stage="tester", report_only=True),
                refusal(),
            ),
            "a repair is already pending": (
                self.state(pending_report_repair={"attempts": 0}),
                self.record(),
                refusal(),
            ),
            "the Completion Reviewer": (
                self.state(next_stage="review_completion"),
                self.record(stage="review_completion"),
                refusal(),
            ),
            "another stage is next": (self.state(next_stage="resolve"), self.record(), refusal()),
            "no source": (self.state(), self.record(source_revision=None), refusal()),
            "no task": (self.state(current_task={}), self.record(), refusal()),
            "no command": (self.state(), self.record(), refusal(command="")),
        }
        for name, (state, record, error) in cases.items():
            with self.subTest(name):
                before = copy.deepcopy(state)
                self.assertFalse(rerun.run_again(state, record, error))
                self.assertEqual(before, state)

    def test_the_rejection_route_offers_it_after_the_routes_that_change_the_plan_or_the_source(self):
        state = self.state()
        asked = []
        self.assertTrue(rejection_route.reroute(state, self.record(), refusal(), asked.append))
        self.assertEqual(([], "tester"), (asked, state["next_stage"]))
        self.assertEqual(1, len(state[rerun.KEY]))


if __name__ == "__main__":
    unittest.main()
