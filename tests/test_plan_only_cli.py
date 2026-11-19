"""A plan-only run (`--plan-only`, the "Plan only" run mode) plans and reviews, then ends at the accepted plan.

End to end through the real CLI with the offline fixture provider, as tests.test_taskrun drives it.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode
import autocode_jobs as jobs
import autocode_plan_only as plan_only
import autocode_run_modes as run_modes
import autocode_taskrun as taskrun
import autocode_util as util

from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS, HERE

PLANNING_STAGES = {"requirements_gather", "draft_plan", "review_plan", "revise_plan", "finalize_plan"}


class PlanOnlyCliTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="plan-only-")
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.workspace = root / "project"
        self.workspace.mkdir()
        git = ["git", "-C", str(self.workspace), "-c", "user.name=T", "-c", "user.email=t@example.test"]
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "base"], check=True)
        bindir = root / "bin"
        bindir.mkdir()
        shutil.copy2(HERE / "live_fixture_provider.py", bindir / "codex")
        (bindir / "codex").chmod(0o755)
        self.env = {
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "AUTOCODE_HOME": str(root / "registry"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def start(self, *extra):
        return taskrun.TaskRun.start(
            self.workspace, BRIEF, options=(*FIXTURE_OPTIONS, *extra), env=self.env, timeout=300
        )

    def test_accepting_the_reviewed_plan_ends_the_run_without_building(self):
        run = self.start("--plan-only")
        view = run.status()
        need = view["needs"] or {}
        self.assertEqual("approve_plan", need.get("kind"), view["stop_reason"])
        # The same approval, bound to the same displayed token, named for what it does here.
        self.assertTrue(need.get("plan_only"), need)
        self.assertEqual(plan_only.ACCEPT_LABEL, need.get("label"))
        self.assertIn("Nothing is built", need.get("effect", ""))

        accepted = run.approve_plan(need["token"])
        self.assertTrue(accepted["done"], {key: accepted.get(key) for key in ("status", "needs", "stop_reason")})
        self.assertEqual("TASK_COMPLETE", accepted["status"])
        self.assertIsNone(accepted["needs"])
        self.assertEqual(need["token"], accepted["approved_contract"]["token"])
        self.assertEqual(plan_only.ACCEPTED_HEADLINE, accepted["progress"]["headline"], accepted["progress"])

        # Launching the saved run again launches nothing and keeps the accepted plan as its result:
        # no Builder, Tester or Completion Reviewer ever ran, so no built-code evidence can go stale.
        run._invoke("relaunch", "--no-chat", *FIXTURE_OPTIONS, advancing=True)
        again = run.status()
        self.assertTrue(again["done"], {key: again.get(key) for key in ("status", "needs", "stop_reason")})
        self.assertEqual("TASK_COMPLETE", again["status"])
        state = util.read(Path(again["run_dir"]) / "state.json")
        stages = {row["stage"] for row in state["stages"]}
        self.assertTrue(stages, state["stages"])
        self.assertLessEqual(stages, PLANNING_STAGES, stages)
        # The status view's one record of what ran says why the build stages did not.
        skipped = [row for row in again["stage_decisions"] if row["decision"] == "skip"]
        self.assertEqual(
            ["builder", "tester", "review_completion"],
            [row["stage"] for row in skipped if row["stage"] in plan_only.NOT_BUILT],
        )
        self.assertTrue(all("plan-only" in row["reason"] for row in skipped if row["stage"] in plan_only.NOT_BUILT))
        # Nothing the plan names was written.
        for name in ("greet.py", "test_greet.py", "README.md"):
            self.assertFalse((Path(again["workspace"]) / name).exists(), name)

    def test_a_full_run_still_builds_after_approval(self):
        run = self.start()
        need = run.status()["needs"]
        self.assertEqual("approve_plan", need["kind"])
        self.assertNotIn("plan_only", need)
        self.assertNotIn("label", need)
        view = run.approve_plan(need["token"])
        self.assertFalse(view["done"])

    def test_the_conversation_continues_at_the_reviewed_plan_and_after_it_is_accepted(self):
        mode = next(mode for mode in run_modes.view()["modes"] if mode["id"] == "plan")
        run = self.start(*mode["start_args"])
        view = run.advance_until_input()
        run.answer(view["needs"]["questions"][-1]["id"], "plan")
        view = run.advance_until_input()
        token = view["needs"]["token"]
        # A question at the reviewed plan is answered there; the plan and its approval stay as they were.
        self.assertEqual("Send message", view["chat"]["input"]["label"])
        view = run.feedback("Why does the plan use sys.argv rather than argparse?")
        view = run.advance_until_input()
        self.assertEqual(
            ("approve_plan", token, True), (view["needs"]["kind"], view["needs"]["token"], view["needs"]["plan_only"])
        )
        state = util.read(Path(view["run_dir"]) / "state.json")
        self.assertEqual("question", state["user_events"][-1]["intent"])
        run.approve_plan(token)
        # After the plan is accepted the chat goes on: the next message is a new turn that starts at
        # Requirements, and its plan is again reviewed and accepted, never built.
        done = run.status()
        self.assertEqual("follow_up", done["chat"]["input"]["kind"])
        view = run.follow_up("Also print the greeting in upper case with --shout.")
        view = run.advance_until_input()
        self.assertEqual("answer", view["needs"]["kind"], view["stop_reason"])
        run.answer(view["needs"]["questions"][-1]["id"], "plan")
        view = run.advance_until_input()
        self.assertEqual(("approve_plan", True), (view["needs"]["kind"], view["needs"].get("plan_only")))
        view = run.approve_plan(view["needs"]["token"])
        self.assertEqual(("TASK_COMPLETE", plan_only.ACCEPTED_HEADLINE), (view["status"], view["progress"]["headline"]))
        stages = {row["stage"] for row in util.read(Path(view["run_dir"]) / "state.json")["stages"]}
        self.assertLessEqual(stages, PLANNING_STAGES, stages)

    def test_plan_only_is_chosen_when_the_run_starts(self):
        run = self.start()
        with self.assertRaisesRegex(taskrun.TaskRunError, "--plan-only"):
            run._invoke("plan only on resume", "--plan-only", "--no-chat", *FIXTURE_OPTIONS, advancing=True)

    def test_plan_only_refuses_a_start_that_approves_the_plan_itself(self):
        with self.assertRaisesRegex(taskrun.TaskRunError, "--plan-only"):
            self.start("--plan-only", "--skip-to-build")


class FinishedWithoutBuildingTests(unittest.TestCase):
    """A run that built nothing keeps its completion when it is launched again (autocode.recheck_completion)."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="built-nothing-")
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name)
        git = ["git", "-C", str(self.workspace), "-c", "user.name=T", "-c", "user.email=t@example.test"]
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "base"], check=True)

    def finished(self, **fields):
        return {"version": 3, "status": "TASK_COMPLETE", "phase": "COMPLETE", "settings": {}, **fields}

    def test_an_answered_discussion_and_an_accepted_plan_stay_complete(self):
        cases = {
            "discussion": self.finished(
                workflow={"kind": "discuss"}, answer={"answer": "Because ...", "answered_by": "requirements_gather"}
            ),
            "accepted plan": self.finished(
                settings={plan_only.SETTING: True}, goal_contract={"approval_status": "approved", "body": {}}
            ),
        }
        for name, state in cases.items():
            with self.subTest(name):
                self.assertTrue(jobs.built_nothing(state))
                autocode.recheck_completion(state, self.workspace)
                self.assertEqual(("TASK_COMPLETE", None), (state["status"], state.get("stop_reason")))

    def test_a_built_run_is_still_rechecked(self):
        state = self.finished(goal_contract={"approval_status": "approved", "body": {}})
        self.assertFalse(jobs.built_nothing(state))
        autocode.recheck_completion(state, self.workspace)
        self.assertEqual("PAUSED_STALE_VALIDATION", state["status"])


class PlanOnlyModeTests(unittest.TestCase):
    def test_the_mode_list_offers_plan_only_with_the_full_planning_flow(self):
        modes = {mode["id"]: mode for mode in run_modes.view()["modes"]}
        self.assertEqual("full", run_modes.view()["default"])
        mode = modes["plan"]
        self.assertEqual("Plan only", mode["label"])
        self.assertEqual([*modes["full"]["start_args"], "--plan-only"], mode["start_args"])
        self.assertEqual("planning", mode["spend_confirmation"])


if __name__ == "__main__":
    unittest.main()
