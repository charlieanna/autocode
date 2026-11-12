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
        self.assertFalse(any(row["stage"] in ("review_plan", "finalize_plan") for row in self.calls()))
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
        # The Plan Reviewer runs at its saved level on a small clear brief too (owner decision 2026-11-11):
        # an explicit Medium stays Medium, and the default level is dispatched unlowered.
        expected = unchanged["roles"]["plan_reviewer"]["reasoning_effort"]
        if explicit:
            self.assertEqual("medium", expected)
        for role in unchanged["roles"]:
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
        reviews = [row for row in self.calls() if row["stage"] in ("review_plan", "finalize_plan")]
        self.assertTrue(reviews, self.calls())
        for row in reviews:
            self.assertIn(f'model_reasoning_effort="{expected}"', row["argv"])
        self.assertNotIn("builder", {row["stage"] for row in self.calls()})
        self.assertNotIn("counts across revisions", run.show_goal(), "each cycle gets its own allowance")
        self.assertIn(f"{len(reviews)}/2 plan-review calls used", run.show_goal())
        self.assertFalse(view["done"])
        self.assertFalse((self.project / "greet.py").exists())
        return run, after

    def test_explicit_medium_survives_actual_small_clear_reviewer_dispatch(self):
        self.review()

    def test_legacy_saved_medium_survives_public_resume_without_new_provenance(self):
        self.review(legacy=True)

    def test_fresh_default_reviewer_dispatches_its_saved_level(self):
        self.review(explicit=False)

    def test_a_vague_requests_first_draft_runs_one_level_above_the_planners_level(self):
        # Owner decision 2026-11-09: a hard plan gets more reasoning on the same model. Requirements judged
        # the request vague, so the Planner's first draft runs one level above its saved level.
        self.env["SCENARIO_FAKE_CLARITY"] = "vague"
        run = taskrun.TaskRun.start(
            self.project,
            self.scenario.brief,
            options=self.options,
            start_options=("--pause-after-stage",),
            env=self.env,
            timeout=180,
            cwd=self.root,
        )
        settings = self.public(run)["settings"]
        planner = copy.deepcopy(settings["roles"]["planner"])
        self.assertEqual("runner_default", settings["effort_origins"]["planner"])
        self.assertFalse(any(row["stage"] == "draft_plan" for row in self.calls()))
        # Fixture setup only: the levels a real run reads from its tool's listing.
        checkpoint = run.run_dir / "state.json"
        owned = json.loads(checkpoint.read_text())
        owned["settings"]["declared_variants"] = {planner["model"]: ["low", "medium", "high", "xhigh"]}
        checkpoint.write_text(json.dumps(owned))
        view = run.resume_paused()
        if view["needs"]["kind"] == "continue":
            view = run.advance_until_input()
        self.assertEqual("approve_plan", view["needs"]["kind"], view["stop_reason"])
        higher = route_fit.SCALE[route_fit.SCALE.index(planner["reasoning_effort"]) + 1]
        drafts = [row for row in self.calls() if row["stage"] == "draft_plan"]
        self.assertTrue(drafts, self.calls())
        for row in drafts:
            self.assertIn(f'model_reasoning_effort="{higher}"', row["argv"])
        self.assertEqual(planner, self.public(run)["settings"]["roles"]["planner"], "the saved level is not rewritten")
        state = json.loads(checkpoint.read_text())
        record = next(row for row in state["stages"] if row.get("stage") == "draft_plan")
        self.assertEqual(
            (higher, True, "hard_plan", ["vague_request"]),
            tuple(record["effort_choice"].get(key) for key in ("level", "raised", "reason", "signals")),
        )
