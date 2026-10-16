"""The money cap end to end (#721): it pauses at the cap for you and continues only when you raise it. A climbed
attempt is also checked against the active-time budget you set before it launches (#611)."""

import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_cost as cost
import autocode_taskrun as taskrun

from . import test_build_blackbox as bb

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


class ClimbCapBlackbox(unittest.TestCase):
    """A role's climb on its effort ladder is checked against the cost cap and the active-time budget before it
    launches (#611), through the public unit CLIs with the deterministic build fixture, which reports 10 input and
    10 output tokens per stage."""

    setUp = bb.BuildBlackbox.setUp
    command = bb.BuildBlackbox.command
    invoke = bb.BuildBlackbox.invoke
    seed = bb.BuildBlackbox.seed
    state = bb.BuildBlackbox.state
    events = bb.BuildBlackbox.events
    build = bb.BuildBlackbox.build

    def status(self):
        return json.loads(self.invoke("autocode", ["--run-dir", str(self.run), "--status"]).stdout)

    def climb(self, *resolver_options, code=0):
        """The product's own check passes but the Tester's fails: the Completion Reviewer sends the work back, the
        Resolver asks for it to be checked again, and the Tester climbs its ladder, GPT-6 Sol High to XHigh."""
        spec = bb.plan(
            [([], "Version prints 1", ["version.py"])],
            {"M1": {"version.py": "print(1)\n"}},
            {"M1": "import version"},
            "Print version",
        )
        spec.update(complete_product=True, criterion_checks={"C1": "raise SystemExit(1)"})
        self.seed(spec, checker="gpt-6-sol")
        self.build()
        self.invoke("autoreview", ["--run-dir", str(self.run), "--no-chat"])
        self.invoke("autoresolver", ["--run-dir", str(self.run), "--no-chat", *resolver_options], code)
        status = self.status()
        self.assertEqual("sol", status["next_stage"])
        self.assertEqual("GPT-6 Sol XHigh", status["reasoning_escalations"][-1]["selected"]["profile"])
        return status

    def test_a_testers_climb_past_the_cost_cap_pauses_before_it_launches(self):
        status = self.climb()
        # The climbed attempt is projected from the last Tester attempt's 10 input and 10 output tokens at
        # GPT-6 Sol's price, (10 x $2 + 10 x $10) / 1M = $0.00012. A cap halfway there admits only the spend so far.
        spent = status["view"]["cost"]["spent_usd"]
        cap = round(spent + 0.00006, 6)
        before = self.events(stage="sol")
        self.invoke("autoreview", ["--run-dir", str(self.run), "--no-chat", "--max-cost-usd", str(cap)], 2)
        self.assertEqual(before, self.events(stage="sol"), "the climbed attempt must not launch past the cap")
        view = self.status()["view"]
        self.assertEqual("PAUSED_COST_LIMIT", view["needs"].get("pause_origin"), view["needs"])
        for part in (
            "The Tester's climb to GPT-6 Sol XHigh (gpt-6-sol at xhigh effort) did not launch: projected at $0.00012",
            "the last Tester attempt's 10 input and 10 output tokens at gpt-6-sol's price",
            f"from {cost.usd(spent)} to {cost.usd(spent + 0.00012)}, past the {cost.usd(cap)} cost cap",
            "resume with --max-cost-usd 0.01 or more",
        ):
            self.assertIn(part, view["stop_reason"])
        # Raising the cap to what the stop names is the person's decision; the climbed attempt then launches.
        self.invoke(
            "autoreview", ["--run-dir", str(self.run), "--no-chat", "--resume-paused", "--max-cost-usd", "0.01"]
        )
        self.assertEqual(len(before) + 1, len(self.events(stage="sol")))
        launched = [row["launch_route"] for row in self.state()["stages"] if row["stage"] == "sol"]
        self.assertEqual(
            [("gpt-6-sol", "high"), ("gpt-6-sol", "xhigh")],
            [(row["model"], row["reasoning_effort"]) for row in launched],
        )

    def advance_clock(self, stage, seconds):
        """The fake clock. The fixture's stages take a fraction of a real second and --max-seconds counts whole
        seconds, so record that the last `stage` attempt ran `seconds` longer: in its duration and in the run's
        active time, the two numbers the runner's own clock writes when an attempt finishes. Only at a pause: a
        running checkpoint that changed after its controller's receipt reads as a controller that ended."""
        path = self.run / "state.json"
        saved = json.loads(path.read_text())
        last = [row for row in saved["stages"] if row["stage"] == stage][-1]
        last["duration_seconds"] += seconds
        saved["active_seconds"] += seconds
        path.write_text(json.dumps(saved, indent=2))
        return last["duration_seconds"], saved["active_seconds"]

    def test_a_testers_climb_past_the_time_budget_pauses_before_it_launches(self):
        self.climb("--pause-after-stage", code=2)
        # The fake clock: the High attempt took 600 s, so the climbed attempt is projected at that long. A
        # 1,000-second budget admits the active time so far (600 s and the other stages' fractions) but not both.
        took, used = self.advance_clock("sol", 600)
        before = self.events(stage="sol")
        self.invoke(
            "autoreview", ["--run-dir", str(self.run), "--no-chat", "--resume-paused", "--max-seconds", "1000"], 2
        )
        self.assertEqual(before, self.events(stage="sol"), "the climbed attempt must not launch past the budget")
        view = self.status()["view"]
        self.assertEqual("PAUSED_TIME_LIMIT", view["needs"].get("pause_origin"), view["needs"])
        needed = math.ceil(used + took)
        for part in (
            "The Tester's climb to GPT-6 Sol XHigh (gpt-6-sol at xhigh effort) did not launch: projected at",
            "of active time, the last Tester attempt's duration, it would take the run's active time from",
            "past the 1,000 s active-time budget you set",
            f"resume with --max-seconds {needed} or more (0 for no limit) to make this attempt",
        ):
            self.assertIn(part, view["stop_reason"])
        # A budget one second below the total the stop names is accepted and saved, but the run pauses again before
        # the same attempt: the new stop projects it against that budget, under a new request. A resume refused
        # when its arguments are read also exits 2, but leaves the stop and its request as they were.
        request = view["needs"].get("resolver_request_id")
        held = ["--run-dir", str(self.run), "--no-chat", "--resume-paused", "--max-seconds", str(needed - 1)]
        self.invoke("autoreview", held, 2)
        self.assertEqual(
            before, self.events(stage="sol"), "the climbed attempt must not launch past the smaller budget"
        )
        view = self.status()["view"]
        self.assertEqual("PAUSED_TIME_LIMIT", view["needs"].get("pause_origin"), view["needs"])
        for part in (f"past the {needed - 1:,} s active-time budget you set", f"resume with --max-seconds {needed} or"):
            self.assertIn(part, view["stop_reason"])
        self.assertNotEqual(request, view["needs"].get("resolver_request_id"), "the resume must reach the new stop")
        # The budget the stop names launches exactly that attempt, on the climbed rung.
        self.invoke(
            "autoreview", ["--run-dir", str(self.run), "--no-chat", "--resume-paused", "--max-seconds", str(needed)]
        )
        self.assertEqual(len(before) + 1, len(self.events(stage="sol")))
        launched = [row["launch_route"] for row in self.state()["stages"] if row["stage"] == "sol"]
        self.assertEqual(
            [("gpt-6-sol", "high"), ("gpt-6-sol", "xhigh")],
            [(row["model"], row["reasoning_effort"]) for row in launched],
        )


if __name__ == "__main__":
    unittest.main()
