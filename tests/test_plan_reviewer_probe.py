"""The Plan Reviewer probe: its planted flaws, its grader and its runs through the task-run interface (#3630)."""

import copy
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_measured_routes as measured_routes
import autocode_model_probe as model_probe
import autocode_plan_file as plan_file
import autocode_plan_reviewer_probe as reviewer_probe
import autocode_route_selector as route_selector
import autocode_taskrun as taskrun

from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS, HERE

SOURCES = [{"source_id": "task:0", "quote": "make average([]) return 0.0"}]


def contract():
    return {
        "technical_approach": ["Return 0.0 from average when the list is empty."],
        "acceptance_criteria": [
            {
                "id": "AC1",
                "criterion": "average([]) is 0.0",
                "verification_method": "test: test_empty",
                "sources": SOURCES,
            },
            {
                "id": "AC2",
                "criterion": "the rest pass",
                "verification_method": "guard: test_average",
                "sources": SOURCES,
            },
        ],
        "milestones": [{"id": "M1", "objective": "fix", "affected_paths": ["totals.py", "test_empty.py"]}],
        "initial_task": {"milestone_id": "M1", "affected_paths": ["totals.py", "test_empty.py"]},
        "brief_acceptance": {"runner": "record"},
        "risk_acceptance": {"runner": "record"},
        "source_trace": {"runner": "record"},
    }


def review(verdict, *, blocking=(), concerns=None, earlier=False):
    return {
        "stage": "review_plan",
        "verdict": verdict,
        "blocking": list(blocking),
        "concerns": concerns
        if concerns is not None
        else [
            {"id": c, "concern": "edits test_totals.py", "blocking": True, "requested_change": "x"} for c in blocking
        ],
        "from_earlier_plan": earlier,
    }


APPROVED = review("approved")
BLOCKED = review("changes_requested", blocking=["C1"])


class PlantTests(unittest.TestCase):
    def test_a_forbidden_file_is_added_everywhere_and_the_quotes_are_untouched(self):
        original = contract()
        planted = reviewer_probe.plant(original, "P5")
        self.assertEqual(contract(), original, "the source plan is not changed")
        for holder in (planted["milestones"][0], planted["initial_task"]):
            self.assertEqual(["totals.py", "test_empty.py", "test_totals.py"], holder["affected_paths"])
        self.assertEqual(
            ["Return 0.0 from average when the list is empty.", reviewer_probe.BY_REQUEST["P5"].approach[0]],
            planted["technical_approach"],
        )
        self.assertEqual(
            [row["sources"] for row in original["acceptance_criteria"]],
            [row["sources"] for row in planted["acceptance_criteria"]],
        )
        for key in reviewer_probe.RUNNER_RECORDS:
            self.assertNotIn(key, planted)

    def test_a_replaced_approach_moves_the_fix_into_the_test_file(self):
        planted = reviewer_probe.plant(contract(), "P1")
        self.assertEqual(list(reviewer_probe.BY_REQUEST["P1"].approach), planted["technical_approach"])
        self.assertEqual(["test_totals.py"], planted["milestones"][0]["affected_paths"])
        self.assertEqual(["test_totals.py"], planted["initial_task"]["affected_paths"])

    def test_an_untested_criterion_is_verified_by_reading_the_code(self):
        planted = reviewer_probe.plant(contract(), "P2")
        self.assertEqual(reviewer_probe.UNTESTED, planted["acceptance_criteria"][0]["verification_method"])
        self.assertEqual("guard: test_average", planted["acceptance_criteria"][1]["verification_method"])
        with self.assertRaises(ValueError):
            reviewer_probe.plant({**contract(), "acceptance_criteria": []}, "P2")

    def test_the_sound_plan_only_loses_the_runner_records(self):
        expected = copy.deepcopy(contract())
        for key in reviewer_probe.RUNNER_RECORDS:
            expected.pop(key)
        self.assertEqual(expected, reviewer_probe.sound(contract()))

    def test_every_request_has_one_flaw(self):
        self.assertEqual(["P1", "P2", "P3", "P4", "P5", "P6", "P7"], [r.id for r in reviewer_probe.REQUESTS])
        for flaw in reviewer_probe.FLAWS:
            with self.subTest(flaw=flaw.kind):
                self.assertNotEqual(reviewer_probe.sound(contract()), reviewer_probe.plant(contract(), flaw))


class GradeTests(unittest.TestCase):
    def test_approving_the_sound_plan_and_blocking_the_flawed_one_passes(self):
        graded = reviewer_probe.grade("P5", APPROVED, BLOCKED, seconds=(80.0, 100.0), cost_usd=0.2)
        self.assertTrue(graded["passed"], graded)
        self.assertEqual(
            (90.0, 0.2, [], True), (graded["seconds"], graded["cost_usd"], graded["false_block"], graded["names_flaw"])
        )

    def test_a_false_block_fails_and_names_the_concerns(self):
        graded = reviewer_probe.grade("P4", review("changes_requested", blocking=["C2"]), BLOCKED, seconds=(1, 1))
        self.assertEqual(["approves_sound"], graded["failed"])
        self.assertEqual(["C2"], graded["false_block"])

    def test_approving_the_flawed_plan_fails(self):
        self.assertEqual(["blocks_flawed"], reviewer_probe.grade("P5", APPROVED, APPROVED, seconds=(1, 1))["failed"])

    def test_an_unresolved_final_review_counts_as_a_block(self):
        self.assertTrue(
            reviewer_probe.grade("P5", APPROVED, review("unresolved", blocking=["C1"]), seconds=(1, 1))["passed"]
        )

    def test_a_missing_or_carried_over_verdict_is_no_review(self):
        for flawed in (None, review("changes_requested", blocking=["C1"], earlier=True)):
            with self.subTest(flawed=flawed):
                graded = reviewer_probe.grade("P5", APPROVED, flawed, seconds=(1, 1))
                self.assertEqual(["reviews_accepted", "blocks_flawed"], graded["failed"])

    def test_a_slow_review_fails_on_time(self):
        slow = reviewer_probe.TIME_LIMIT_SECONDS + 1
        self.assertEqual(["time"], reviewer_probe.grade("P5", APPROVED, BLOCKED, seconds=(1, slow))["failed"])
        self.assertEqual(["time"], reviewer_probe.grade("P5", APPROVED, BLOCKED, seconds=(1,))["failed"])

    def test_a_block_on_something_else_does_not_name_the_flaw(self):
        other = review(
            "changes_requested", blocking=["C1"], concerns=[{"id": "C1", "concern": "vague", "blocking": True}]
        )
        graded = reviewer_probe.grade("P5", APPROVED, other, seconds=(1, 1))
        self.assertTrue(graded["passed"])
        self.assertFalse(graded["names_flaw"], "recorded, but not graded")


def scripted(passing, *, unreached=()):
    """A runner standing in for run_reviewer_request: candidates in ``passing`` pass, the others false-block."""
    calls = []

    def run(request, candidates, **kwargs):
        calls.append((request.id, list(candidates), kwargs["planner"]))
        if request.id in unreached:
            return {
                "request": request.id,
                "source": {"reached_approval": False, "run_status": "WAITING_FOR_USER"},
                "grades": {},
                "spent_usd": 1.0,
            }
        grades = {}
        for model, level in candidates:
            sound = APPROVED if model in passing else review("changes_requested", blocking=["C9"])
            grades[(model, level)] = reviewer_probe.grade(request, sound, BLOCKED, seconds=(60, 60), cost_usd=0.25)
        return {"request": request.id, "source": {"reached_approval": True}, "grades": grades, "spent_usd": 2.0}

    return run, calls


class RunReviewerProbeTests(unittest.TestCase):
    def setUp(self):
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        patcher = patch.dict("os.environ", {"XDG_CONFIG_HOME": home.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_each_plan_is_written_once_and_reviewed_by_every_candidate_of_another_family(self):
        run, calls = scripted({"zai-coding-plan/glm-5.3", "claude-opus-5-5"})
        outcome = model_probe.run_reviewer_probe(
            [("zai-coding-plan/glm-5.3", "low"), ("claude-opus-5-5", "medium"), ("gpt-5.6-terra", "high")],
            cap_usd=50,
            planner=("gpt-6.1-sol", "high"),
            runner=run,
        )
        self.assertEqual(["P4", "P5", "P1", "P2", "P3", "P6", "P7"], [request for request, *_ in calls])
        self.assertEqual([("zai-coding-plan/glm-5.3", "low"), ("claude-opus-5-5", "medium")], calls[0][1])
        self.assertEqual(
            [
                {
                    "model": "gpt-5.6-terra",
                    "effort": "high",
                    "reason": "the same family as the Planner whose plans it would review",
                }
            ],
            outcome["unmeasured"],
        )
        self.assertEqual(14.0, outcome["spent_usd"], "each request's whole run counts against the cap")
        self.assertTrue(all(row["qualified"] for row in outcome["ranking"]))
        self.assertEqual(
            ["zai-coding-plan/glm-5.3", "claude-opus-5-5"],
            [entry["model"] for entry in route_selector.qualified("plan_reviewer")],
            "the measured Plan Reviewers are the ones selection and the quota fallback choose from",
        )

    def test_a_false_block_in_the_quick_pass_ends_that_candidate(self):
        run, calls = scripted({"claude-opus-5-5"})
        outcome = model_probe.run_reviewer_probe(
            [("zai-coding-plan/glm-5.3", "low"), ("claude-opus-5-5", "medium")],
            cap_usd=50,
            planner=("gpt-6.1-sol", "high"),
            runner=run,
        )
        self.assertEqual(
            [("claude-opus-5-5", "medium")], calls[2][1], "after the quick pass only the passing one reviews"
        )
        ranking = {row["model"]: row["qualified"] for row in outcome["ranking"]}
        self.assertEqual({"claude-opus-5-5": True, "zai-coding-plan/glm-5.3": False}, ranking)

    def test_a_plan_that_never_reached_approval_is_skipped_and_no_one_qualifies(self):
        run, _calls = scripted({"claude-opus-5-5"}, unreached={"P3"})
        outcome = model_probe.run_reviewer_probe(
            [("claude-opus-5-5", "medium")], cap_usd=50, planner=("gpt-6.1-sol", "high"), runner=run
        )
        self.assertEqual(["P3"], [row["request"] for row in outcome["skipped"]])
        self.assertFalse(outcome["ranking"][0]["qualified"], "six of seven requests is not the full set")
        self.assertFalse(measured_routes.qualifies(outcome["results"][0], "plan_reviewer"))

    def test_the_cap_stops_the_probe(self):
        run, calls = scripted({"claude-opus-5-5"})
        outcome = model_probe.run_reviewer_probe(
            [("claude-opus-5-5", "medium")], cap_usd=3, planner=("gpt-6.1-sol", "high"), runner=run
        )
        self.assertEqual(["P4", "P5"], [request for request, *_ in calls])
        self.assertEqual("the probe reached its $3.00 cap", outcome["stopped"])
        self.assertEqual([], outcome["results"], "a full pass cut off is not recorded")


class ExportedPlanFormatTests(unittest.TestCase):
    def test_probe_reads_and_rewrites_both_public_plan_formats(self):
        original = {"intended_outcome": "Keep literal tests/__init__.py", "technical_approach": ["One function"]}
        for version in sorted({1, 2, plan_file.FORMAT}):
            with self.subTest(version=version), tempfile.TemporaryDirectory(prefix="reviewer-plan-format-") as parent:
                front = {
                    "autocode_plan": version,
                    "goal_hash": plan_file.goal_hash(original),
                    "revision": 3,
                    "approval_status": "not approved",
                    "contract": original,
                }
                exported = plan_file.document(front)
                self.assertEqual(front, model_probe._front_matter(exported))
                changed = {**original, "technical_approach": ["A deliberately different approach"]}
                path = model_probe._plan_file(
                    Path(parent), front, changed, (sys.executable, str(HERE / "autocode.py")), {}
                )
                parsed = plan_file.read(path.read_text())
                self.assertEqual(version, parsed.format)
                self.assertEqual(changed, parsed.body)
                self.assertEqual(front["goal_hash"], parsed.exported_hash)
                self.assertIsNone(parsed.stale_line)

    def test_unreadable_export_is_reported_as_a_taskrun_error(self):
        with self.assertRaisesRegex(taskrun.TaskRunError, "exported plan file"):
            model_probe._front_matter("# No canonical contract")


class ThroughTheCliTests(unittest.TestCase):
    """Reviews through the real `autocode` CLI on the offline fixture provider.

    The fixture provider's Requirements never names an outcome on a spec-first run, so the plan here
    comes from an ordinary fixture run brought to approval (``source``). Everything after it is the
    probe's own path: export, plant, render, load, the reviewer switch, one review under the planning
    call cap, and the verdict read from the status view. The fixture Plan Reviewer blocks a plan that
    holds the planted file name.
    """

    def setUp(self):
        root = Path(tempfile.mkdtemp(prefix="reviewer-probe-cli-"))
        self.addCleanup(shutil.rmtree, root, True)
        bindir = root / "bin"
        bindir.mkdir()
        shutil.copy2(HERE / "live_fixture_provider.py", bindir / "codex")
        (bindir / "codex").chmod(0o755)
        self.env = {
            "PATH": f"{bindir}{os.pathsep}{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
            "AUTOCODE_HOME": str(root / "registry"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "LIVE_FIXTURE_REVIEW_BLOCKS": "test_totals.py",
        }

    def source(self, workspace, _request):
        run = taskrun.TaskRun.start(workspace, BRIEF, options=FIXTURE_OPTIONS, env=self.env, timeout=300)
        return run, run.status()

    def test_each_candidate_reviews_the_plan_as_written_and_with_its_flaw_and_the_run_stops_after_each(self):
        outcome = model_probe.run_reviewer_request(
            "P5",
            [("gpt-6-astra", "high"), ("gpt-5.6-terra", "high")],
            planner=("gpt-5.6-sol", "high"),
            env=self.env,
            source=self.source,
        )
        self.assertTrue(outcome["source"]["reached_approval"], outcome)
        for (model, _level), graded in outcome["grades"].items():
            with self.subTest(model=model):
                self.assertIsNone(graded["error"])
                self.assertEqual(("approved", "changes_requested"), (graded["sound_verdict"], graded["flawed_verdict"]))
                self.assertTrue(graded["passed"], graded)
                self.assertTrue(graded["names_flaw"])
                self.assertEqual([model], graded["reviewed_by"], "the candidate itself reviewed both plans")
                self.assertEqual(
                    ["review_plan", "review_plan"], graded["review_stages"], "no revision followed a block"
                )
        self.assertIsInstance(outcome["spent_usd"], float)


if __name__ == "__main__":
    unittest.main()
