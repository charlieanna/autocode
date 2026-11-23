"""The fast lane (``--fast-lane``): a small, clear task skips the Plan Reviewer and gets one combined review.

Through the real CLI with the offline fixture provider: the run completes with four model stages instead of six,
the skipped stages are shown with their reason, and the evidence gates (regression proof, changed-code evidence,
the combined independent review) still run. ``decide`` is pinned for each reason a draft takes the ordinary path.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_fast_lane as fast_lane
import autocode_review_routing as routing
import autocode_stage_decisions as stage_decisions
import autocode_taskrun as taskrun

from tests.test_agent_step import fixture_workspace
from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS

REVIEWS = ("tester", "review_completion", "review_checkpoint")
PLAN_REVIEWS = ("review_plan", "revise_plan", "finalize_plan")


@unittest.skipUnless(sys.platform.startswith("linux"), "supervised provider requires supported Linux")
class FastLaneCliTests(unittest.TestCase):
    def setUp(self):
        self.workspace, self.env = fixture_workspace(self)
        # The fixture provider and AutoCode's child processes run `python3` by name.
        first, rest = self.env["PATH"].split(os.pathsep, 1)
        self.env["PATH"] = os.pathsep.join([first, str(Path(sys.executable).parent), rest])
        # Codex refuses an API key in its environment; the offline fixture needs none.
        self.base = {key: value for key, value in os.environ.items() if key not in ("OPENAI_API_KEY", "CODEX_API_KEY")}

    def cli(self, *args, env=None):
        return subprocess.run(
            [*taskrun.AUTOCODE, "--workspace", str(self.workspace), *args],
            env={**self.base, **self.env, **(env or {})},
            capture_output=True,
            text=True,
            timeout=240,
        )

    def view(self):
        result = self.cli("--run-dir", str(self.run_dir), "--status")
        self.assertEqual(0, result.returncode, result.stderr)
        return json.loads(result.stdout)["view"]

    def stages(self):
        return [row["stage"] for row in json.loads((self.run_dir / "state.json").read_text())["stages"]]

    def start(self, clarity):
        started = self.cli(
            "--brief", BRIEF, *FIXTURE_OPTIONS, "--in-place", "--fast-lane", env={"LIVE_FIXTURE_CLARITY": clarity}
        )
        self.assertEqual(2, started.returncode, started.stdout + started.stderr)
        self.run_dir = next((self.workspace / ".autocode/runs").iterdir())
        return self.view()

    def test_a_small_clear_task_goes_to_approval_without_a_plan_review_and_completes_with_one_review(self):
        shown = self.start("clear")
        self.assertEqual("approve_plan", shown["needs"]["kind"], shown["needs"])
        self.assertEqual(["requirements_gather", "draft_plan"], self.stages())
        decisions = {row["stage"]: row for row in shown["stage_decisions"]}
        for stage in PLAN_REVIEWS:
            self.assertEqual("skip", decisions[stage]["decision"], stage)
            self.assertEqual(fast_lane.SKIP_TEXT, decisions[stage]["reason"])
            self.assertEqual(["draft_plan", stage_decisions.APPROVAL], decisions[stage]["covered_by"])
        plan = json.loads((self.run_dir / "state.json").read_text())["goal_contract"]["body"]
        self.assertIn(routing.POLICY, plan["constraints"], "the combined review is disclosed in the plan")

        approved = self.cli(
            "--run-dir",
            str(self.run_dir),
            "--approve-goal",
            "-",
            env={"AUTOCODE_APPROVE_GOAL_TOKEN": shown["needs"]["token"]},
        )
        self.assertEqual(0, approved.returncode, approved.stdout + approved.stderr)
        done = self.cli("--run-dir", str(self.run_dir), "--no-chat")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        state = json.loads((self.run_dir / "state.json").read_text())
        self.assertEqual("TASK_COMPLETE", state["status"])
        stages = self.stages()
        self.assertEqual(["review_checkpoint"], [stage for stage in stages if stage in REVIEWS])
        self.assertFalse(set(PLAN_REVIEWS) & set(stages))
        self.assertIn("regression_proof", stages, "the regression proof still runs")
        self.assertEqual("PASS", state["validation"]["verdict"])

    def test_a_request_requirements_did_not_judge_clear_is_reviewed_as_usual(self):
        shown = self.start("vague")
        self.assertEqual("approve_plan", shown["needs"]["kind"], shown["needs"])
        self.assertIn("review_plan", self.stages())
        decision = json.loads((self.run_dir / "state.json").read_text())["planning"]["fast_lane"]
        self.assertEqual((False, ["not_clear"]), (decision["skip_plan_review"], decision["reasons"]))


def draft(paths=("greet.py",), milestones=1, **extra):
    return {
        "milestones": [{"id": f"M{n}", "affected_paths": list(paths), "depends_on": []} for n in range(milestones)],
        "initial_task": {"kind": "implement", "affected_paths": list(paths)},
        "acceptance_criteria": [{"id": "C1", "criterion": "greets"}],
        "open_blocking_questions": [],
        **extra,
    }


class DecideTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.state = {
            "task": "Rename page_count to count_pages.",
            "workspace": temp.name,
            "workflow": {"clarity": "clear"},
            "settings": {"fast_lane": True},
        }

    def reasons(self, body):
        return fast_lane.decide(self.state, body)["reasons"]

    def test_a_small_clear_approvable_draft_skips_the_plan_review(self):
        decision = fast_lane.decide(self.state, draft())
        self.assertTrue(decision["skip_plan_review"])
        self.assertEqual(([], fast_lane.SKIP_TEXT, "small"), (decision["reasons"], decision["text"], decision["size"]))

    def test_each_reason_sends_the_draft_to_the_plan_reviewer(self):
        self.assertEqual(["not_small"], self.reasons(draft(paths=[f"f{n}.py" for n in range(5)])))
        self.assertEqual(["not_small"], self.reasons(draft(milestones=2)))
        self.assertEqual(["not_approvable"], self.reasons(draft(open_blocking_questions=[{"id": "Q1"}])))
        self.assertEqual(["not_approvable"], self.reasons({**draft(), "initial_task": {"kind": "investigate"}}))
        self.state["settings"]["sensitive_paths"] = ["*auth*"]
        self.assertEqual(["sensitive_paths"], self.reasons(draft(paths=["src/auth.py"])))
        self.state["settings"]["design_manifest"] = {"path": "design.md"}
        self.assertIn("design", self.reasons(draft()))
        self.state["settings"].pop("design_manifest")
        self.state["workflow"]["clarity"] = "vague"
        self.assertEqual(["not_clear"], self.reasons(draft()))

    def test_a_draft_whose_citations_need_a_reviewer_s_judgment_is_reviewed(self):
        # A criterion cites the request but another does not: only a Plan Reviewer may excuse that, so the
        # strictest trace fails and the draft takes the ordinary path.
        body = draft()
        body["acceptance_criteria"] = [
            {"id": "C1", "criterion": "renamed", "sources": [{"id": "task:0", "quote": "Rename page_count"}]},
            {"id": "C2", "criterion": "uncited"},
        ]
        self.assertEqual(["needs_reviewer_observations"], self.reasons(body))

    def test_a_sealed_observation_record_in_the_draft_is_reviewed(self):
        self.assertEqual(["needs_reviewer_observations"], self.reasons(draft(source_trace={"version": 1})))

    def test_only_the_first_draft_of_a_fast_lane_run_is_considered(self):
        queued = []
        state = {**self.state, "next_stage": "review_plan", "planning": {"adaptive": {"challenges": 1}}}
        state["settings"] = {"fast_lane": True, "joint_planning": True, "adaptive_planning": True}
        self.assertIsNone(fast_lane.after_draft(state, "draft_plan", {}, queue=queued.append))
        self.assertIsNone(fast_lane.after_draft(state, "revise_plan", {}, queue=queued.append))
        state["settings"]["fast_lane"] = False
        self.assertIsNone(fast_lane.after_draft(state, "draft_plan", {}, queue=queued.append))
        self.assertEqual([], queued)


class ConfiguredTests(unittest.TestCase):
    def test_the_flag_has_three_states_and_implies_the_combined_review(self):
        parser = argparse.ArgumentParser()
        fast_lane.add_argument(parser)
        self.assertIsNone(parser.parse_args([]).fast_lane)
        self.assertTrue(parser.parse_args(["--fast-lane"]).fast_lane)
        self.assertFalse(parser.parse_args(["--no-fast-lane"]).fast_lane)
        self.assertEqual({"fast_lane": True, "review_routing": "combined"}, fast_lane.new_run_settings({}, True))
        self.assertEqual({}, fast_lane.new_run_settings({}, None))
        self.assertEqual({}, fast_lane.new_run_settings({}, False))

    def test_it_refuses_a_separate_review_checkpoints_and_a_change_on_a_saved_run(self):
        with self.assertRaisesRegex(ValueError, "--review-routing separate"):
            fast_lane.new_run_settings({}, True, review_requested="separate")
        with self.assertRaisesRegex(ValueError, "checkpoints"):
            fast_lane.new_run_settings({}, True, checkpoints=True)
        with self.assertRaisesRegex(ValueError, "new-run choice"):
            fast_lane.configured({}, True, started=True)
        with self.assertRaisesRegex(ValueError, "new-run choice"):
            fast_lane.configured({"fast_lane": True}, False, started=True)
        self.assertTrue(fast_lane.configured({"fast_lane": True}, None, started=True))
        self.assertFalse(fast_lane.configured({}, None, started=True))


class StageDecisionTests(unittest.TestCase):
    def test_a_fast_lane_draft_shows_the_three_planning_reviews_as_skipped_after_the_draft(self):
        state = {
            "stages": [{"stage": stage, "iteration": 1, "pid": 103} for stage in ("requirements_gather", "draft_plan")],
            "planning": {"adaptive": {"approved_at": fast_lane.APPROVED_AT, "challenges": 0}},
        }
        shown = [(row["stage"], row["decision"], row["reason"]) for row in stage_decisions.view(state)]
        self.assertEqual(
            [
                ("requirements_gather", "run", None),
                ("draft_plan", "run", None),
                *((stage, "skip", fast_lane.SKIP_TEXT) for stage in PLAN_REVIEWS),
            ],
            shown,
        )
        self.assertEqual(stage_decisions.FAST_LANE, fast_lane.APPROVED_AT)
        self.assertEqual(stage_decisions.FAST_LANE_SKIPPED, fast_lane.SKIPPED)


if __name__ == "__main__":
    unittest.main()
