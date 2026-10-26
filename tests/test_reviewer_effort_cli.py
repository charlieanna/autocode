"""Reviewer effort preservation through real supervised fake-provider CLI dispatch.

The legacy case removes only the new provenance map from this test's own paused
checkpoint to represent an older run. All subsequent behavior and oracles use
TaskRun, public status/show-goal, workspace files, and the fake provider's argv.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_route_fit as route_fit
import autocode_taskrun as taskrun

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"
if str(SCENARIOS) not in sys.path:
    sys.path.insert(0, str(SCENARIOS))
from harness import catalog
from harness.driver import fake_setup
from harness.project import materialize, without_maintenance


@unittest.skipUnless(sys.platform == "linux", "supervised fake providers require supported Linux")
class ReviewerEffortCLI(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="reviewer-effort-cli-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.scenario = catalog.load("greenfield-greeting-cli")
        self.project = without_maintenance(materialize(self.scenario.seed, self.root / "project"))
        flags, env = fake_setup(self.scenario, self.root, self.scenario.dir / "reference")
        self.options = (*flags, "--no-spec-first")
        self.env = {**os.environ, **env, "AUTOCODE_HOME": str(self.root / "registry"), "SCENARIO_FAKE_SIDE": "effort"}
        # Extend the existing test-owned fake witness with launch argv, never prompt text.
        provider = self.root / "bin/codex"
        source = provider.read_text()
        old = '"side": os.environ["SCENARIO_FAKE_SIDE"],'
        self.assertIn(old, source)
        provider.write_text(source.replace(old, old + '\n                        "argv": sys.argv[1:],', 1))

    def public(self, run):
        result = subprocess.run(
            [*run.command, "--status", "--workspace", str(self.project), "--run-dir", str(run.run_dir)],
            env=self.env,
            cwd=self.root,
            text=True,
            capture_output=True,
            timeout=30,
            check=True,
        )
        return json.loads(result.stdout)

    def calls(self):
        return [json.loads(line) for line in (self.root / "fake-calls.jsonl").read_text().splitlines()]

    def review(self, *, legacy=False, explicit=True):
        selected = ("--plan-reviewer-reasoning-effort", "medium") if explicit else ()
        run = taskrun.TaskRun.start(
            self.project,
            self.scenario.brief,
            options=self.options,
            start_options=(*selected, "--pause-after-stage"),
            env=self.env,
            timeout=180,
            cwd=self.root,
        )
        before = self.public(run)
        self.assertEqual("clear", before["view"]["planning_depth"]["clarity"])
        self.assertFalse(any(row["stage"] in ("astra_challenge", "astra_finalize") for row in self.calls()))
        if legacy:
            # Fixture setup only: represent a375-era checkpoint, which had no effort provenance.
            checkpoint = run.run_dir / "state.json"
            owned = json.loads(checkpoint.read_text())
            owned["settings"].pop("effort_origins", None)
            owned["settings"]["roles"]["investigator"] = {
                **owned["settings"]["roles"]["plan_reviewer"],
                "model": "gpt-6-sol",
                "reasoning_effort": "high",
            }
            checkpoint.write_text(json.dumps(owned))
            before = self.public(run)
            self.assertNotIn("effort_origins", before["settings"])
        unchanged = copy.deepcopy(before["settings"])
        view = run.resume_paused()
        if view["needs"]["kind"] == "continue":
            view = run.advance_until_input()
        self.assertEqual("approve_plan", view["needs"]["kind"], view["stop_reason"])
        after = self.public(run)
        expected = "medium"  # explicit Medium preserved; automatic High reduced one tier
        reviewer = after["settings"]["roles"]["plan_reviewer"]
        self.assertEqual(expected, reviewer["reasoning_effort"])
        self.assertEqual(unchanged["roles"]["plan_reviewer"]["model"], reviewer["model"])
        for role in unchanged["roles"]:
            if role != "plan_reviewer":
                self.assertEqual(unchanged["roles"][role], after["settings"]["roles"][role], role)
        self.assertEqual(unchanged["limits"], after["settings"]["limits"])
        if legacy:
            self.assertEqual(
                route_fit.identity(unchanged),
                route_fit.identity(after["settings"]),
                "legacy resume must not infer effort provenance",
            )
        else:
            self.assertEqual(
                "user_explicit" if explicit else "runner_default",
                after["settings"]["effort_origins"]["plan_reviewer"],
            )
        reviews = [row for row in self.calls() if row["stage"] in ("astra_challenge", "astra_finalize")]
        self.assertTrue(reviews, self.calls())
        for row in reviews:
            self.assertIn('model_reasoning_effort="medium"', row["argv"])
        self.assertNotIn("terra", {row["stage"] for row in self.calls()})
        self.assertIn("counts across revisions (small clear brief)", run.show_goal())
        self.assertIn(f"{len(reviews)}/2 plan-review calls used", run.show_goal())
        self.assertFalse(view["done"])
        self.assertFalse((self.project / "greet.py").exists())
        return run, after

    def test_explicit_medium_survives_actual_small_clear_reviewer_dispatch(self):
        self.review()

    def test_legacy_saved_medium_survives_public_resume_without_new_provenance(self):
        self.review(legacy=True)

    def test_fresh_default_reviewer_still_dispatches_reduced_effort(self):
        self.review(explicit=False)
