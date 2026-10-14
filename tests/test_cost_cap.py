"""The money cap end to end (#721): it pauses at the cap for you and continues only when you raise it."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_taskrun as taskrun

TOOLS = Path(__file__).resolve().parents[1] / "tools"
BRIEF = "Build greeting and goodbye"
# The scripted Codex fixture: every model stage reports 100 input and 50 output tokens on gpt-5.6-sol or
# gpt-5.6-terra, both priced; milestones mode asks one question, then builds two milestones.
OPTIONS = ("--evidence-provenance", "fake", "--engine", "codex")
CAP = "0.000001"  # below one priced stage: the first boundary after a model stage is the pause


class CostCapCliTests(unittest.TestCase):
    """End to end through the real CLI and TaskRun with the scripted Codex fixture."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="cost-cap-")
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.workspace = root / "project"
        self.workspace.mkdir()
        git = ["git", "-C", str(self.workspace), "-c", "user.name=T", "-c", "user.email=t@example.test"]
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "base"], check=True)
        bindir = root / "bin"
        bindir.mkdir()
        shutil.copy2(TOOLS / "fake_codex.py", bindir / "codex")
        shutil.copy2(TOOLS / "goal_fixtures.py", bindir / "goal_fixtures.py")
        (bindir / "codex").chmod(0o755)
        self.env = {
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "AUTOCODE_HOME": str(root / "registry"),
            "XDG_CONFIG_HOME": str(root / "config"),
            "CODEX_HOME": str(root / "codex-home"),
            "AUTOCODE_FIXTURE_MODE": "milestones",
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def task(self, run_dir, *options):
        return taskrun.TaskRun(self.workspace, run_dir, options=(*OPTIONS, *options), env=self.env, timeout=300)

    def cli(self, run_dir, *argv):
        environment = {**os.environ, **self.env}
        command = [
            sys.executable,
            str(TOOLS / "autocode.py"),
            "--workspace",
            str(self.workspace),
            "--run-dir",
            str(run_dir),
        ]
        return subprocess.run([*command, *argv], env=environment, capture_output=True, text=True, timeout=300)

    @staticmethod
    def model_stages(view):
        return sum(not row["runner_owned"] for row in view["usage"]["accounting"]["attempts"])

    def assert_cost_pause(self, view, cap=CAP):
        self.assertEqual("PAUSED_COST_LIMIT", view["needs"].get("pause_origin") or view["status"], view)
        self.assertEqual("your_decision", view["pause_category"])
        self.assertEqual(float(cap), view["cost"]["cap_usd"])
        self.assertGreaterEqual(view["cost"]["spent_usd"], float(cap))
        self.assertEqual(0, view["cost"]["unpriced_stages"])

    def test_the_run_pauses_at_the_cap_for_you_and_continues_only_when_you_raise_it(self):
        started = taskrun.TaskRun.start(
            self.workspace, BRIEF, options=OPTIONS, start_options=("--max-cost-usd", CAP), env=self.env, timeout=300
        )
        view = started.status()
        self.assert_cost_pause(view)
        self.assertEqual(1, self.model_stages(view), "nothing launches past the stage that reached the cap")
        status = self.cli(started.run_dir, "--status")
        self.assertIn("Cost: $", status.stderr)
        self.assertIn("cap $0.000001.", status.stderr)

        # A plain resume, and restating the exhausted cap, leave it paused and launch nothing.
        for options in ((), ("--max-cost-usd", CAP)):
            with self.subTest(options=options):
                held = self.task(started.run_dir, *options).resume_paused()
                self.assert_cost_pause(held)
                self.assertEqual(1, self.model_stages(held))

        # Raising the cap is the person's acknowledgement: the run continues and completes under it.
        raised = self.task(started.run_dir, "--max-cost-usd", "5")
        view = raised.resume_paused()
        for _ in range(4):
            need = view["needs"]
            if view["done"] or need["kind"] not in ("answer", "approve_plan", "continue"):
                break
            if need["kind"] == "answer":
                question = need["questions"][0]
                answer = question["proposed_default"] or question["options"][0]
                view = raised.answer(question["id"], answer, resolver_token=need.get("resolver_token"))
            elif need["kind"] == "approve_plan":
                view = raised.approve_plan(need["token"])
            view = raised.advance_until_input()
        self.assertEqual("TASK_COMPLETE", view["status"], view)
        self.assertEqual(5.0, view["cost"]["cap_usd"])
        self.assertGreater(self.model_stages(view), 1)
        self.assertTrue(view["cost"]["complete"])
        summary = self.cli(started.run_dir)
        self.assertIn("Cost: $", summary.stdout)
        self.assertIn("cap $5.00.", summary.stdout)

    def test_without_a_cap_the_spend_is_shown_and_never_pauses_the_run(self):
        started = taskrun.TaskRun.start(self.workspace, BRIEF, options=OPTIONS, env=self.env, timeout=300)
        view = started.status()
        self.assertEqual("answer", view["needs"]["kind"], view)
        self.assertIsNone(view["cost"]["cap_usd"])
        self.assertGreater(view["cost"]["spent_usd"], 0)


if __name__ == "__main__":
    unittest.main()
