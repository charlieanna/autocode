"""Changing a started run's stage models between stages, through the real CLI and the status view.

A new task takes the saved stage choices; a started run keeps its own routes until the person changes
one here (autocode_run_stage_models, owner request 2026-11-10). Offline fake Codex; no model launches.
"""

import json
import subprocess
import unittest

from . import test_subprocess


class RunStageModelsCLITests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ("--engine", "codex")

    def command(self, args, expected):
        """``autocode ARGS`` exactly as a client runs a named argument array, with no added options."""
        result = subprocess.run(
            [*self.entry, *args], cwd=self.root, env=self.env, capture_output=True, text=True, timeout=240
        )
        self.assertEqual(expected, result.returncode, result.stdout + result.stderr)
        return result

    def status(self, run):
        return json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)["view"]

    def at_plan_approval(self):
        self.env["AUTOCODE_FIXTURE_MODE"] = "human-pending"
        self.launch(["Build a greeting tool"], 2)
        run, _ = self.saved()
        self.launch(["--run-dir", str(run), "--answer", "Q1=CLI"], 0)
        self.launch(["--run-dir", str(run)], 2)
        return run

    def test_a_started_run_changes_one_stage_between_stages_and_its_next_launch_runs_it(self):
        run = self.at_plan_approval()
        view = self.status(run)
        self.assertEqual("AWAITING_GOAL_APPROVAL", view["status"])
        offer = view["stage_models"]
        self.assertEqual({"changeable": True, "reason": None}, {key: offer[key] for key in ("changeable", "reason")})
        listed = json.loads(self.command(offer["next_command"], 0).stdout)
        stages = {stage["id"]: stage for stage in listed["stages"]}
        self.assertEqual("codex", listed["backend"])
        self.assertEqual({"model": "gpt-5.6-terra", "effort": "medium"}, stages["builder"]["chosen"])
        self.assertEqual(
            {role: route["model"] for role, route in view["routes"].items() if role in stages},
            {role: stage["chosen"]["model"] for role, stage in stages.items()},
            "the list shows the models this run runs, not the new-run choices",
        )
        options = {option["model"]: option["unavailable"] for option in stages["builder"]["options"]}
        self.assertRegex(options["gpt-5.6-sol"], "Tester and the Completion Reviewer check this stage's work")
        named = stages["builder"]["set"]["next_command"]
        fill = lambda model, effort: [model if w == "MODEL" else effort if w == "EFFORT" else w for w in named]  # noqa: E731
        state = run / "state.json"
        before = state.read_bytes()
        for model, effort, refused in (
            ("gpt-5.6-sol", "high", "Tester"),  # a checker would grade its own model's work
            ("gpt-5.6-terra", "medium", "already runs gpt-5.6-terra at medium"),
            ("gpt-6-astra", "ultra", "not a reasoning level"),
        ):
            with self.subTest(model=model, effort=effort):
                result = self.command(fill(model, effort), 2)
                self.assertIn(refused, result.stderr)
                self.assertEqual(before, state.read_bytes(), "a refused change saves nothing")
        other = [w if w != "builder" else "requirements" for w in fill("gpt-6-astra", "high")]
        self.assertIn("no requirements stage", self.command(other, 2).stderr)
        changed = self.command(fill("gpt-6-astra", "high"), 0)
        self.assertIn("The Builder now runs gpt-6-astra at high in this run (was gpt-5.6-terra)", changed.stdout)
        self.assertIn("no agent launched", changed.stdout)
        after = self.status(run)
        self.assertEqual("AWAITING_GOAL_APPROVAL", after["status"], "changing a model answers nothing")
        self.assertEqual(view["usage"]["accounting"]["attempt_count"], after["usage"]["accounting"]["attempt_count"])
        builder = after["routes"]["builder"]
        self.assertEqual(
            ("gpt-6-astra", "high", "user_explicit"), (builder["model"], builder["reasoning_effort"], builder["origin"])
        )
        [assignment] = after["route_assignments"]
        self.assertEqual(
            ("user_cli", "stage_models", "builder", "gpt-5.6-terra", "gpt-6-astra", "AWAITING_GOAL_APPROVAL"),
            tuple(assignment[k] for k in ("actor", "via", "role", "from", "to", "pause_status")),
        )
        self.assertEqual({"from": "medium", "to": "high", "named": True}, assignment["effort"])
        relisted = json.loads(self.command(offer["next_command"], 0).stdout)
        self.assertEqual(
            {"model": "gpt-6-astra", "effort": "high"},
            next(stage for stage in relisted["stages"] if stage["id"] == "builder")["chosen"],
        )
        self.assertFalse((self.root / "xdg-config" / "autocode" / "stages.toml").exists(), "new-run choices untouched")
        # The next Builder launch runs the changed route.
        self.env["AUTOCODE_APPROVE_GOAL_TOKEN"] = self.saved()[1]["displayed_goal"]
        self.launch(["--run-dir", str(run), "--approve-goal", "-"], 0)
        del self.env["AUTOCODE_APPROVE_GOAL_TOKEN"]
        self.launch(["--run-dir", str(run)], 2)
        attempts = self.status(run)["usage"]["accounting"]["attempts"]
        builders = [row for row in attempts if row.get("stage") == "builder"]
        self.assertTrue(builders, [row.get("stage") for row in attempts])
        self.assertEqual({"gpt-6-astra"}, {row["model"] for row in builders})


if __name__ == "__main__":
    unittest.main()
