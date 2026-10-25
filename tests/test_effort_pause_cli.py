"""A named effort the model does not declare pauses before any launch and takes a typed level (#2057).

#2020: a --single-model GLM run stopped at the Plan Reviewer on a level GLM does not declare, and
the chat could offer only ``Continue? [y/N]`` (#2039). Through the real CLI with a supervised fake
provider: the run pauses as PAUSED_EFFORT_UNSUPPORTED before the next stage launches, the status
view names ``--answer effort-plan_reviewer=EFFORT``, the answer saves a declared level, and the
reviewer then runs at it. The only fixture edit is the model's declared levels, which a real run
reads from its tool's listing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_taskrun as taskrun

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"
if str(SCENARIOS) not in sys.path:
    sys.path.insert(0, str(SCENARIOS))
from harness import catalog
from harness.driver import fake_setup
from harness.project import materialize, without_maintenance

DECLARED = ["low", "high", "max"]


@unittest.skipUnless(sys.platform == "linux", "supervised fake providers require supported Linux")
class EffortPauseCLI(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="effort-pause-cli-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.scenario = catalog.load("greenfield-greeting-cli")
        self.project = without_maintenance(materialize(self.scenario.seed, self.root / "project"))
        flags, env = fake_setup(self.scenario, self.root, self.scenario.dir / "reference")
        self.options = (*flags, "--no-spec-first")
        self.env = {**os.environ, **env, "AUTOCODE_HOME": str(self.root / "registry"), "SCENARIO_FAKE_SIDE": "effort"}
        provider = self.root / "bin/codex"
        source = provider.read_text()
        old = '"side": os.environ["SCENARIO_FAKE_SIDE"],'
        self.assertIn(old, source)
        provider.write_text(source.replace(old, old + '\n                        "argv": sys.argv[1:],', 1))

    def calls(self):
        return [json.loads(line) for line in (self.root / "fake-calls.jsonl").read_text().splitlines()]

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

    def test_named_undeclared_reviewer_effort_pauses_then_runs_at_the_typed_level(self):
        run = taskrun.TaskRun.start(
            self.project,
            self.scenario.brief,
            options=self.options,
            start_options=("--plan-reviewer-reasoning-effort", "medium", "--pause-after-stage"),
            env=self.env,
            timeout=180,
            cwd=self.root,
        )
        reviewer = self.public(run)["settings"]["roles"]["plan_reviewer"]["model"]
        # Fixture setup only: the levels a real run reads from its tool's listing.
        checkpoint = run.run_dir / "state.json"
        owned = json.loads(checkpoint.read_text())
        owned["settings"]["declared_variants"] = {reviewer: DECLARED}
        checkpoint.write_text(json.dumps(owned))
        launched = len(self.calls())

        view = run.resume_paused()
        self.assertEqual("PAUSED_EFFORT_UNSUPPORTED", view["status"], view.get("stop_reason"))
        self.assertEqual(launched, len(self.calls()), "nothing launches at a level the model does not declare")
        need = view["needs"]
        self.assertEqual("answer", need["kind"])
        self.assertEqual(
            {"question_id": "effort-plan_reviewer", "requested": "medium", "declared": DECLARED, "nearest": "high"},
            {key: need["effort"][key] for key in ("question_id", "requested", "declared", "nearest")},
        )
        self.assertIn("--answer effort-plan_reviewer=EFFORT", view["next_command"])
        self.assertEqual("your_decision", view["pause_category"])

        with self.assertRaisesRegex(taskrun.TaskRunError, "declares low, high, max"):
            run.assign_effort("plan_reviewer", "medium")
        view = run.assign_effort("plan_reviewer", "high")
        self.assertEqual(launched, len(self.calls()), "the answer saves; it launches nothing")
        self.assertNotIn("effort", view["needs"])
        self.assertIn("--resume-paused", view["next_command"])
        settings = self.public(run)["settings"]
        self.assertEqual("high", settings["roles"]["plan_reviewer"]["reasoning_effort"])
        self.assertEqual("user_explicit", settings["effort_origins"]["plan_reviewer"])

        view = run.resume_paused()
        if view["needs"]["kind"] == "continue":
            view = run.advance_until_input()
        self.assertEqual("approve_plan", view["needs"]["kind"], view["stop_reason"])
        reviews = [row for row in self.calls() if row["stage"] in ("astra_challenge", "astra_finalize")]
        self.assertTrue(reviews, self.calls())
        for row in reviews:
            self.assertIn('model_reasoning_effort="high"', row["argv"])


if __name__ == "__main__":
    unittest.main()
