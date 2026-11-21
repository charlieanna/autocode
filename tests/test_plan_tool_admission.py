"""A plan whose commands the tool boundary refuses goes back to the Planner before approval (#4015).

A live Kilo run approved a plan that ran `uv run pytest`, then paused PAUSED_TOOL_CONTAINMENT at its
first contained stage ("Contained toolchain discovery does not support uv"), and every resume paused
again. The same admission now runs before the plan is shown for approval, for every program the
boundary refuses, not only uv.
"""

import copy
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_completion_result as completion_result
import autocode_goal_lifecycle as lifecycle
import autocode_plan_defects as plan_defects
import autocode_plan_tool_admission as admission
import autocode_regression as regression
import autocode_tool_admission as tool_admission
import autocode_toolchain as toolchain
from goal_fixtures import body

ABSENT = "autocode-absent-program-4015"
# Runners the boundary has no rule for: each is refused whether or not this host has it installed.
REFUSED = ("uv run pytest -q tests/", "make test", "bundle exec rspec", "yarn test")


def plan(*commands):
    value = body()
    rows = []
    for number, command in enumerate(commands, 1):
        rows.append(
            {
                "id": f"C{number}",
                "criterion": f"Check {number} holds",
                "verification_method": f"Run `{command}` and confirm it exits 0.",
                "human_review": False,
            }
        )
    value["acceptance_criteria"] = rows
    value["milestones"][0]["acceptance_criteria"] = [row["id"] for row in rows]
    return value


class WorkspaceCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.root)], check=False)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        # The contained stages' own interpreter: CI runs the suite without activating its venv.
        path = os.pathsep.join([str(Path(sys.executable).parent), os.environ.get("PATH", "")])
        self.environment = {**tool_admission.environment(), "PATH": path}
        patcher = mock.patch.object(tool_admission, "environment", return_value=self.environment)
        patcher.start()
        self.addCleanup(patcher.stop)

    def state(self, **settings):
        return {
            "version": 3,
            "task_id": "task-boundary",
            "task": "Add JSON round trip",
            "workspace": str(self.root),
            "status": "RUNNING",
            "next_stage": "draft_plan",
            "settings": {"engine": "opencode", "joint_planning": True, **settings},
            "answers": {},
            "user_events": [],
        }


class RefusalTests(WorkspaceCase):
    def test_each_refused_program_is_named_with_its_criterion_and_an_admitted_one_is_not(self):
        found = admission.refusals(self.root, plan("python3 -m pytest -q", *REFUSED))
        self.assertEqual(["C2", "C3", "C4", "C5"], [ident for ident, _command, _error in found])
        self.assertEqual(list(REFUSED), [command for _ident, command, _error in found])
        for _ident, command, error in found:
            with self.subTest(command=command):
                program = command.split()[0]
                self.assertIn(program, error)
        # The launch's own refusal names what the boundary admits, so the Planner can choose.
        unsupported = [error for _ident, command, error in found if command == "make test"]
        if unsupported and "does not support" in unsupported[0]:
            self.assertIn(", ".join(toolchain.ADMITTED), unsupported[0])

    def test_the_refusal_is_the_launch_s_own_discovery(self):
        with self.assertRaises(toolchain.Unadmitted) as caught:
            toolchain.discover(self.root, [f"{ABSENT} run"], self.environment)
        self.assertEqual((ABSENT, True), (caught.exception.program, caught.exception.missing))


class InstallTests(WorkspaceCase):
    def corrections(self, current):
        return [e for e in current["user_events"] if e.get("settled_as") == plan_defects.KIND]

    def test_a_plan_running_a_refused_program_goes_back_to_the_planner_before_approval(self):
        for command in REFUSED:
            with self.subTest(command=command):
                current = self.state()
                lifecycle.install_draft(current, plan("python3 -m pytest -q", command), origin="planner_draft")
                self.assertEqual(("RUNNING", "draft_plan"), (current["status"], current["next_stage"]))
                self.assertEqual("none", lifecycle.human.evaluate(current))
                [event] = self.corrections(current)
                self.assertEqual((admission.QUESTION_ID, ["C2"]), (event["question_id"], event["plan_parts"]))
                self.assertIn(f"`{command}`", event["text"])
                self.assertIn(command.split()[0], event["question"]["why"])
                self.assertEqual([], current["goal_contract"]["body"]["open_blocking_questions"])

    def test_a_second_refusal_in_the_same_round_is_the_person_s_test_environment_decision(self):
        current = self.state()
        lifecycle.install_draft(current, plan("uv run pytest -q tests/"), origin="planner_draft")
        lifecycle.install_draft(current, plan("uv run pytest -q tests/"), origin="revise_plan")
        lifecycle.human.evaluate(current)
        self.assertEqual("WAITING_FOR_USER", current["status"])
        [asked] = current["pending_questions"]
        self.assertEqual((admission.QUESTION_ID, "decision"), (asked["id"], asked["kind"]))
        self.assertIn("already asked the Planner", asked["why"])
        self.assertEqual(1, len(self.corrections(current)))

    def test_an_admitted_plan_and_an_uncontained_run_are_not_checked(self):
        cases = {
            "admitted": (self.state(), plan("python3 -m pytest -q")),
            "codex": (self.state(engine="codex"), plan("uv run pytest -q tests/")),
            "accepted uncontained": (self.state(allow_uncontained_tools=True), plan("uv run pytest -q tests/")),
        }
        for name, (current, draft) in cases.items():
            with self.subTest(name):
                lifecycle.install_draft(current, copy.deepcopy(draft), origin="planner_draft")
                self.assertEqual([], self.corrections(current))
                self.assertNotEqual("WAITING_FOR_USER", current["status"])


class ReopenTests(WorkspaceCase):
    """A plan approved before the check (or whose admission changed since) goes back to the Planner (#4015)."""

    def approved(self, command, **settings):
        current = self.state(**settings)
        with mock.patch.object(admission, "defect", return_value=None):  # approved before the check existed
            lifecycle.install_draft(current, plan("python3 -m pytest -q", command), origin="planner_draft")
        current["goal_contract"].update(approval_status="approved", approval_event={"id": "approval-1"})
        current.update(status="RUNNING", phase="EXECUTING", next_stage="builder")
        return current

    def before_launch(self, current):
        # No proof is spent on a launch the boundary would refuse.
        with mock.patch.object(regression, "before_review", side_effect=AssertionError("proof ran")):
            return completion_result.before_review(current, "builder", self.root, self.root / "run")

    def test_an_approved_plan_the_boundary_refuses_returns_to_the_planner_before_any_launch(self):
        current = self.approved("uv run pytest -q tests/")
        self.assertIs(True, self.before_launch(current))
        self.assertEqual(
            ("RUNNING", "DISCOVERING", "draft_plan"), tuple(current[k] for k in ("status", "phase", "next_stage"))
        )
        self.assertEqual(
            ("draft", None), (current["goal_contract"]["approval_status"], current["goal_contract"]["approval_event"])
        )
        [event] = [e for e in current["user_events"] if e.get("settled_as") == plan_defects.KIND]
        self.assertEqual((admission.QUESTION_ID, ["C2"]), (event["question_id"], event["plan_parts"]))
        self.assertIn("`uv run pytest -q tests/`", event["text"])

    def test_an_admitted_or_unapproved_plan_and_a_second_refusal_in_the_round_launch_as_before(self):
        admitted = self.approved("python3 -m pytest tests/")
        unapproved = self.approved("uv run pytest -q tests/")
        unapproved["goal_contract"]["approval_status"] = "draft"
        spent = self.approved("uv run pytest -q tests/")
        spent["user_events"].append(
            {"kind": "technical_decision", "actor": "autocode", "settled_as": plan_defects.KIND}
        )
        for name, current in (("admitted", admitted), ("unapproved", unapproved), ("spent", spent)):
            with self.subTest(name):
                before = copy.deepcopy(current)
                self.assertIs(False, admission.reopen(current))
                self.assertEqual(before, current)


class PauseTextTests(unittest.TestCase):
    def test_the_pause_names_the_plan_s_program_not_kilo(self):
        unsupported = toolchain.Unadmitted("Contained toolchain discovery does not support uv", "uv")
        paused = tool_admission.failure(unsupported, kilo_readonly=True)
        self.assertEqual("PAUSED_TOOL_CONTAINMENT", paused.status)
        self.assertIn("The approved plan runs `uv`", str(paused))
        self.assertIn("resuming this plan pauses here again", str(paused))
        self.assertNotIn("Restore a qualified Kilo", str(paused))
        missing = toolchain.Unadmitted("Approved tool is unavailable before launch: tox", "tox", missing=True)
        self.assertIn("not installed on this host; install it and resume", str(tool_admission.failure(missing)))


if __name__ == "__main__":
    unittest.main()
