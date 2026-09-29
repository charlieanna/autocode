"""Tests for the scenario harness and catalog.  python3 -m unittest scenarios/test_harness.py

Proves every oracle (seed fails, reference passes, broken variants fail), then
proves the full run path with the scripted model: a correct solution is judged
PASS and a plausible wrong one is judged FALSE_COMPLETE.
"""
import argparse
import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run  # noqa: E402
from harness import baseline, catalog, compare, oracle, routing, stats, verdict  # noqa: E402
from harness.driver import metrics, split_by_turn, turn_state  # noqa: E402


class CatalogTests(unittest.TestCase):
    def test_every_oracle_rejects_the_seed_accepts_the_reference_and_rejects_broken_variants(self):
        for scenario in catalog.load_all():
            if scenario.missing_tools():
                continue
            with self.subTest(scenario=scenario.id):
                self.assertIsNotNone(scenario.reference, "every scenario needs a reference solution")
                for name, ok, summary in run.self_test(scenario):
                    self.assertTrue(ok, f"{name}: {summary}")

    def test_nothing_here_imports_autocode(self):
        """Oracles and the harness judge AutoCode from outside; importing it would let its bugs hide."""
        here = Path(__file__).resolve().parent
        autocode_modules = {path.stem for path in (here.parent / "tools").glob("*.py")} | {"tools", "autocode_cli"}
        autocode_modules -= {"__init__", "__main__"}
        for path in here.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                names = ([alias.name for alias in node.names] if isinstance(node, ast.Import) else
                         [node.module] if isinstance(node, ast.ImportFrom) and node.module and not node.level else [])
                for name in names:
                    self.assertNotIn(name.split(".")[0], autocode_modules, f"{path.relative_to(here)} imports {name}")

    def test_briefs_are_plain_text(self):
        for scenario in catalog.load_all():
            with self.subTest(scenario=scenario.id):
                self.assertTrue(scenario.brief)
                self.assertNotIn("\n#", scenario.brief, "headings would become part of the task text")

    def test_known_failures_say_why(self):
        for scenario in catalog.load_all():
            with self.subTest(scenario=scenario.id):
                self.assertIsInstance(scenario.known_failure, str)
                if scenario.known_failure:
                    self.assertGreater(len(scenario.known_failure), 20, "a known failure names what is missing")
                self.assertIn(scenario.expected, catalog.EXPECTED)

    def test_routing_table_loads_and_names_known_workflows(self):
        table = routing.load()
        catalog.load(table["seed"])
        self.assertGreaterEqual(len(table["prompts"]), 10)
        self.assertEqual(set(routing.WORKFLOWS), {p["workflow"] for p in table["prompts"]},
                         "every workflow needs at least one prompt")


class JudgeTests(unittest.TestCase):
    passing = verdict.OracleResult([verdict.Check("a", True)])
    failing = verdict.OracleResult([verdict.Check("a", False)])

    def test_verdicts(self):
        self.assertEqual(verdict.PASS, verdict.judge("TASK_COMPLETE", self.passing)[0])
        self.assertEqual(verdict.FALSE_COMPLETE, verdict.judge("TASK_COMPLETE", self.failing)[0])
        self.assertEqual(verdict.HONEST_BLOCKER, verdict.judge("PAUSED_BUDGET", self.passing)[0])
        self.assertEqual(verdict.ERROR, verdict.judge("RUNNING", self.passing)[0])
        self.assertEqual(verdict.ERROR, verdict.judge("TASK_COMPLETE", verdict.OracleResult(error="boom"))[0])

    def test_a_scenario_that_expects_a_stop(self):
        self.assertEqual(verdict.PASS, verdict.judge("PAUSED_HUMAN", self.passing, "stop")[0])
        self.assertEqual(verdict.HONEST_BLOCKER, verdict.judge("PAUSED_HUMAN", self.failing, "stop")[0])
        self.assertEqual(verdict.FALSE_COMPLETE, verdict.judge("TASK_COMPLETE", self.passing, "stop")[0])
        self.assertEqual(verdict.PASS, verdict.judge("WAITING_FOR_USER", self.passing, "any")[0])
        self.assertEqual(verdict.PASS, verdict.judge("TASK_COMPLETE", self.passing, "any")[0])

    def test_an_oracle_with_no_checks_does_not_pass(self):
        self.assertFalse(verdict.OracleResult([]).passed)


class RunChecksTests(unittest.TestCase):
    """Run-level checks judge how AutoCode worked; without a run there is nothing to judge."""

    def test_no_run_means_no_checks(self):
        self.assertEqual([], oracle.run_checks(None, workflow="review", no_build=True))

    def test_todays_build_pipeline_fails_a_review(self):
        run_record = {"view": {"workflow": None}, "cli_calls": ["start", "approve-plan", "resume"],
                      "stages": ["requirements_gather", "astra_discovery", "terra", "sol"], "answers": []}
        failed = {c.name for c in oracle.run_checks(run_record, workflow="review", no_build=True, no_requirements=True)
                  if not c.ok}
        self.assertEqual({"workflow_recognized", "no_builder_dispatched", "no_build_plan_approval_requested",
                          "no_requirements_gathering"}, failed)

    def test_a_recognized_read_only_review_passes(self):
        run_record = {"view": {"workflow": "review"}, "cli_calls": ["start", "resume"],
                      "stages": ["review", "sol"], "answers": []}
        self.assertTrue(all(c.ok for c in oracle.run_checks(run_record, workflow="review", no_build=True,
                                                            no_requirements=True, max_questions=0)))


    def test_a_planned_fix_needs_plan_review_and_the_users_approval(self):
        planned = {"view": {"workflow": "bugfix"}, "cli_calls": ["start", "approve-plan", "resume"],
                   "stages": ["investigate_bug", "astra_discovery", "astra_challenge", "terra"], "answers": []}
        self.assertTrue(all(c.ok for c in oracle.run_checks(planned, workflow="bugfix", plan_approved=True)))
        small = {**planned, "cli_calls": ["start", "resume"], "stages": ["investigate_bug", "terra"]}
        failed = {c.name for c in oracle.run_checks(small, workflow="bugfix", plan_approved=True) if not c.ok}
        self.assertEqual({"plan_reviewed", "plan_approved_by_user"}, failed)


    def test_the_stage_budget_counts_only_model_stages(self):
        run_record = {"view": {"workflow": "bugfix"}, "cli_calls": ["start"], "answers": [],
                      "stages": ["recognize_workflow", "investigate_bug", "orchestrator", "terra", "regression_proof",
                                 "sol", "astra_review"],
                      "model_stages": ["recognize_workflow", "investigate_bug", "terra", "sol", "astra_review"]}
        check, = [c for c in oracle.run_checks(run_record, workflow="bugfix", max_model_stages=5) if c.name == "stage_budget"]
        self.assertTrue(check.ok, check.detail)
        del run_record["model_stages"]  # older records: everything but orchestration counts
        check, = [c for c in oracle.run_checks(run_record, workflow="bugfix", max_model_stages=5) if c.name == "stage_budget"]
        self.assertFalse(check.ok)


class ExercisedTests(unittest.TestCase):
    """`[run] requires_stages` (issue #59): a run that never reached the stage under test proves nothing about it."""

    def test_a_good_ending_without_the_stage_is_not_exercised(self):
        outcome, summary = verdict.exercised(verdict.PASS, "fine", ("astra_resolve",), ["terra", "sol"])
        self.assertEqual(verdict.NOT_EXERCISED, outcome)
        self.assertIn("astra_resolve", summary)
        self.assertEqual(verdict.PASS, verdict.exercised(verdict.PASS, "fine", ("astra_resolve",),
                                                         ["terra", "sol", "astra_resolve", "terra"])[0])

    def test_a_false_completion_is_never_hidden_behind_not_exercised(self):
        self.assertEqual(verdict.FALSE_COMPLETE, verdict.exercised(verdict.FALSE_COMPLETE, "bad", ("astra_resolve",), [])[0])

    def test_the_refund_oracle_scores_what_autoresolver_said(self):
        scenario = catalog.load("feature-refund-window")
        self.assertEqual(("astra_resolve",), scenario.requires_stages)
        check = scenario.oracle()
        with tempfile.TemporaryDirectory() as root:
            from harness.project import materialize
            project = materialize(scenario.seed, Path(root) / "p", scenario.reference)
            vague = {"resolutions": [{"diagnosis": "The implementation has a bug; fix it.", "evidence": []}]}
            named = {"resolutions": [{"diagnosis": "store_date ignores the UTC-8 store offset, so the window "
                                                   "counts UTC days", "evidence": []}]}
            for run_record, ok in ((vague, False), (named, True), ({"resolutions": []}, None)):
                scored = [c for c in check(project, scenario, run_record) if c.name == "resolver_named_a_planted_defect"]
                self.assertEqual([] if ok is None else [ok], [c.ok for c in scored])


class TurnTests(unittest.TestCase):
    """Follow-up turns (issue #51): parsed from scenario.toml, matched to run states, and split afterwards."""

    def test_turns_load_in_order(self):
        scenario = catalog.load("review-then-fix")
        self.assertEqual(["complete"], [turn.after for turn in scenario.turns])
        self.assertTrue(scenario.turns[0].say.startswith("Fix them."))
        self.assertEqual((), catalog.load("review-planted-defects").turns)

    def test_a_turn_must_say_something_after_a_known_state(self):
        with tempfile.TemporaryDirectory() as root:
            original = catalog.CATALOG
            catalog.CATALOG = Path(root)
            self.addCleanup(setattr, catalog, "CATALOG", original)
            for turn, message in (('after = "later"\nsay = "x"', "after must be"), ('after = "complete"', "exactly")):
                scenario = Path(root) / "bad"
                scenario.mkdir(exist_ok=True)
                (scenario / "brief.md").write_text("Do it.")
                (scenario / "scenario.toml").write_text(f'title = "t"\ncategory = "conversation"\n[[turn]]\n{turn}\n')
                with self.assertRaisesRegex(ValueError, message):
                    catalog.load("bad")

    def test_turn_state_names_what_a_turn_may_follow(self):
        self.assertEqual(["complete"], turn_state({"done": True, "needs": {"kind": "none"}}))
        self.assertEqual(["stop", "needs:answer"], turn_state({"done": False, "needs": {"kind": "answer"}}))

    def test_stages_are_split_at_the_moment_each_follow_up_was_said(self):
        state = {"stages": [{"stage": "review_change", "finished_at": "2026-09-28T10:00:01+00:00"},
                            {"stage": "orchestrator", "started_at": None, "finished_at": "2026-09-28T10:05:00+00:00"},
                            {"stage": "terra", "started_at": "2026-09-28T10:05:01+00:00",
                             "finished_at": "2026-09-28T10:06:00+00:00"}]}
        first, second = split_by_turn(state, [{"said_at": "2026-09-28T10:04:00+00:00"}])
        self.assertEqual(["review_change"], [stage["stage"] for stage in first])
        self.assertEqual(["orchestrator", "terra"], [stage["stage"] for stage in second])


class MetricsTests(unittest.TestCase):
    def test_stages_are_broken_down_by_name_and_report_repairs_are_counted(self):
        state = {"stages": [
            {"stage": "terra", "duration_seconds": 30}, {"stage": "orchestrator", "duration_seconds": 1},
            {"stage": "sol", "duration_seconds": 10}, {"stage": "sol_report_repair", "duration_seconds": 4},
            {"stage": "regression_proof", "runner_owned": True, "duration_seconds": 2},
            {"stage": "sol", "duration_seconds": 12}]}
        result = metrics(state)
        self.assertEqual(4, result["model_stages"])
        self.assertEqual(1, result["report_repairs"])
        self.assertEqual({"count": 2, "seconds": 22}, result["by_stage"]["sol"])
        self.assertEqual(59, result["model_seconds"])


class StatsTests(unittest.TestCase):
    """`run.py stats`: how often each scenario ran and passed, never mixing fake and live."""

    def result(self, scenario, mode, outcome, started, stages=5, wall=60):
        return {"scenario": scenario, "mode": mode, "verdict": outcome, "started_at": started, "wall_seconds": wall,
                "metrics": {"model_stages": stages, "model_seconds": wall / 2}}

    def test_streak_counts_consecutive_passes_from_the_latest_run(self):
        results = [self.result("s", "fake", verdict.PASS, "1"), self.result("s", "fake", verdict.FALSE_COMPLETE, "2"),
                   self.result("s", "fake", verdict.PASS, "3"), self.result("s", "fake", verdict.PASS, "4")]
        results.append(self.result("s", "fake", verdict.NOT_EXERCISED, "0"))
        row, = stats.summarize(results)
        self.assertEqual((5, 3, 2, 1, verdict.PASS),
                         (row["runs"], row["passes"], row["streak"], row["not_exercised"], row["last"]))

    def test_fake_and_live_runs_are_summarized_separately(self):
        results = [self.result("s", "fake", verdict.PASS, "1", stages=9),
                   self.result("s", "glm53-openai", verdict.FALSE_COMPLETE, "2", stages=14, wall=900)]
        rows = {row["mode"]: row for row in stats.summarize(results)}
        self.assertEqual((1, 9), (rows["fake"]["passes"], rows["fake"]["median_model_stages"]))
        self.assertEqual((0, 14, 15), (rows["glm53-openai"]["passes"], rows["glm53-openai"]["median_model_stages"],
                                       rows["glm53-openai"]["median_wall_minutes"]))
        self.assertEqual(["fake"], [row["mode"] for row in stats.summarize(results, mode="fake")])

    def test_skipped_runs_do_not_count_and_older_results_still_read(self):
        old = {"scenario": "s", "mode": "fake", "verdict": verdict.PASS, "started_at": "1",
               "metrics": {"model_stage_names": ["terra", "sol", "astra_review"]}}
        skipped = {"scenario": "s", "mode": "fake", "verdict": verdict.SKIPPED, "started_at": "2"}
        row, = stats.summarize([old, skipped])
        self.assertEqual((1, 3, None), (row["runs"], row["median_model_stages"], row["median_wall_minutes"]))
        self.assertIn("s", stats.format_table([row]))


class FakeSchemaTests(unittest.TestCase):
    """The scripted model answers "none" for any required field its script does not know yet."""

    def test_missing_required_fields_get_empty_values_of_their_type(self):
        import importlib, json, os
        with tempfile.TemporaryDirectory() as root:
            config = Path(root) / "config.json"
            config.write_text(json.dumps({"check": "true", "paths": [], "brief": "x"}))
            os.environ["SCENARIO_FAKE_CONFIG"] = str(config)
            try:
                fake = importlib.import_module("harness.fake_codex")
            finally:
                del os.environ["SCENARIO_FAKE_CONFIG"]
        schema = {"type": "object", "required": ["kept", "rows", "note", "flag", "kind", "nested"], "properties": {
            "kept": {"type": "string"}, "rows": {"type": "array", "items": {"type": "object", "required": ["id", "extra"],
                "properties": {"id": {"type": "string"}, "extra": {"type": "array"}}}},
            "note": {"type": "string"}, "flag": {"type": "boolean"}, "kind": {"type": "string", "enum": ["none", "some"]},
            "nested": {"type": "object", "required": ["n"], "properties": {"n": {"type": "integer"}}}}}
        report = fake.complete({"kept": "yes", "rows": [{"id": "R1"}]}, schema)
        self.assertEqual({"kept": "yes", "rows": [{"id": "R1", "extra": []}], "note": "", "flag": False,
                          "kind": "none", "nested": {"n": 0}}, report)


class FakeRunTests(unittest.TestCase):
    """End to end through AutoCode's real CLI, with the scripted model (about 30 s each)."""

    def run_fake(self, solution, scenario="bugfix-iso-weeks"):
        with tempfile.TemporaryDirectory(prefix="scenario-test-") as out:
            args = argparse.Namespace(
                fake=True, profile=None, fake_solution=solution, out=Path(out), autocode=None,
                max_steps=None, timeout_minutes=10)
            return run.run_one(catalog.load(scenario), args)

    def test_correct_solution_is_judged_pass(self):
        result = self.run_fake("reference")
        self.assertEqual(verdict.PASS, result["verdict"], result["summary"])
        self.assertEqual("TASK_COMPLETE", result["runner_status"])

    def test_wrong_solution_that_autocode_accepts_is_judged_false_complete(self):
        result = self.run_fake("broken/special-case")
        self.assertEqual(verdict.FALSE_COMPLETE, result["verdict"], result["summary"])

    def test_a_review_runs_only_the_reviewer_and_leaves_the_tree_alone(self):
        result = self.run_fake("reference", "review-clean-pr")
        self.assertEqual(verdict.PASS, result["verdict"], result["summary"])
        self.assertEqual("review", result["workflow"])
        self.assertEqual(["recognize_workflow", "review_change"], result["metrics"]["stage_names"])

    def test_tiny_jobs_do_not_quietly_take_more_steps(self):
        # Issue #15: small jobs already take many model calls. These are today's counts
        # with the scripted model; lower them when a step is trimmed, never raise them
        # without deciding that the extra step is worth its time.
        # bugfix-trivial was 5 with the short path for small fixes; it is 9 while that path
        # is off (2026-09-29), and fails only its proportionality checks (scenario.toml).
        for scenario, ceiling in (("greenfield-greeting-cli", 9), ("bugfix-trivial", 9)):
            with self.subTest(scenario=scenario):
                result = self.run_fake("reference", scenario)
                if catalog.load(scenario).known_failure:
                    failing = {check["name"] for check in result["checks"] if not check["ok"]}
                    self.assertEqual({"no_plan_review_rounds", "stage_budget"}, failing, result["summary"])
                else:
                    self.assertEqual(verdict.PASS, result["verdict"], result["summary"])
                self.assertLessEqual(result["metrics"]["model_stages"], ceiling,
                                     result["metrics"]["model_stage_names"])
                self.assertGreater(result["wall_seconds"], 0)

    def test_a_conversation_reviews_first_then_says_its_follow_up_in_the_same_run(self):
        result = self.run_fake("reference", "review-then-fix")
        self.assertEqual(2, len(result["turns"]), result["summary"])
        self.assertEqual(["recognize_workflow", "review_change"], result["turns"][0]["model_stage_names"])
        self.assertEqual("review", result["turns"][0]["workflow"])
        self.assertTrue(result["turns"][1]["say"].startswith("Fix them."))

    def test_an_invented_blocker_in_a_review_is_judged_false_complete(self):
        result = self.run_fake("broken/invented-blocker", "review-clean-pr")
        self.assertEqual(verdict.FALSE_COMPLETE, result["verdict"], result["summary"])


class BaselineTests(unittest.TestCase):
    """The plain agent AutoCode is compared against: how it is launched and what its exit means."""

    def test_presets_and_templates(self):
        self.assertEqual(["opencode", "run", "--dir", "/p", "--model", "openai/gpt-6-sol"],
                         baseline.command("opencode", None, Path("/p"), "openai/gpt-6-sol"))
        self.assertEqual(["codex", "exec", "-C", "/p", "--sandbox", "workspace-write", "-"],
                         baseline.command("codex", None, Path("/p"), None))
        self.assertEqual(["agent", "--cwd", "/p", "--model", "m"],
                         baseline.command("codex", "agent --cwd {project} --model {model}", Path("/p"), "m"))
        with self.assertRaisesRegex(ValueError, "unknown baseline"):
            baseline.command("nope", None, Path("/p"), None)

    def test_only_exit_zero_claims_completion(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            ran = baseline.run([sys.executable, "-c", "import sys; sys.exit(sys.stdin.read() != 'brief')"], root,
                               "brief", root / "ok.log", env={}, timeout_seconds=60)
            self.assertEqual((0, baseline.CLAIMED), (ran["exit"], ran["status"]))
            failed = baseline.run([sys.executable, "-c", "raise SystemExit(3)"], root, "", root / "x.log",
                                  env={}, timeout_seconds=60)
            self.assertEqual((3, baseline.STOPPED), (failed["exit"], failed["status"]))
            missing = baseline.run(["no-such-agent-binary"], root, "", root / "m.log", env={}, timeout_seconds=60)
            self.assertEqual((127, baseline.STOPPED), (missing["exit"], missing["status"]))
            self.assertIn("no-such-agent-binary", (root / "m.log").read_text())


class CompareSummaryTests(unittest.TestCase):
    @staticmethod
    def row(name, auto, base, auto_ok, base_ok, auto_s=10.0, base_s=1.0):
        return {"scenario": name, "autocode": {"verdict": auto, "deliverable_passed": auto_ok, "seconds": auto_s,
                                               "model_stages": 5},
                "baseline": {"verdict": base, "deliverable_passed": base_ok, "seconds": base_s}}

    def test_summary_counts_each_side(self):
        rows = [self.row("a", verdict.PASS, verdict.FALSE_COMPLETE, True, False, 30.0, 2.0),
                self.row("b", verdict.PASS, verdict.PASS, True, True, 10.0, 4.0),
                self.row("c", verdict.HONEST_BLOCKER, verdict.PASS, False, True, 20.0, 6.0),
                {"scenario": "d", "skipped": "requires go"}]
        summary = compare.summarize(rows)
        self.assertEqual((4, 3, 1), (summary["scenarios"], summary["compared"], summary["skipped"]))
        self.assertEqual({"autocode only": 1, "both": 1, "baseline only": 1}, summary["outcomes"])
        self.assertEqual({"deliverable_passed": 2, "verdicts": {"PASS": 2, "HONEST_BLOCKER": 1},
                          "false_completions": 0, "total_seconds": 60.0, "median_seconds": 20.0},
                         summary["autocode"])
        self.assertEqual((2, 1, 4.0), (summary["baseline"]["deliverable_passed"],
                                       summary["baseline"]["false_completions"], summary["baseline"]["median_seconds"]))
        text = compare.markdown({"baseline": "opencode (one call)", "mode": "fake", "started_at": "t", "out": "/o",
                                 "autocode": {"commit": "0123456789abcdef", "dirty": False}}, rows, summary)
        self.assertIn("| Deliverable accepted by the oracle | 2/3 | 2/3 |", text)
        self.assertIn("| a | PASS | FALSE_COMPLETE | 30.0 | 2.0 | 5 | autocode only |", text)
        self.assertIn("| d | skipped: requires go |", text)


class CompareRunTests(unittest.TestCase):
    """AutoCode and the scripted agent on one scenario, through run.py compare (a few seconds each)."""

    def compare(self, *extra):
        with tempfile.TemporaryDirectory(prefix="compare-test-") as out:
            self.assertEqual(0, run.main(["compare", "bugfix-iso-weeks", "--fake", "--out", out, *extra]))
            [report] = Path(out).glob("*-compare-fake/comparison.json")
            self.assertTrue((report.parent / "comparison.md").is_file())
            return json.loads(report.read_text())

    def test_both_sides_deliver_the_reference(self):
        [row] = self.compare()["rows"]
        self.assertEqual((verdict.PASS, verdict.PASS), (row["autocode"]["verdict"], row["baseline"]["verdict"]))
        self.assertEqual("both", compare.outcome(row))

    def test_a_wrong_baseline_is_a_false_completion_on_the_same_oracle(self):
        report = self.compare("--fake-baseline-solution", "broken/special-case")
        [row] = report["rows"]
        self.assertEqual(verdict.PASS, row["autocode"]["verdict"])
        self.assertEqual(verdict.FALSE_COMPLETE, row["baseline"]["verdict"], row["baseline"]["summary"])
        self.assertEqual(1, report["summary"]["baseline"]["false_completions"])
        self.assertEqual("autocode only", compare.outcome(row))


if __name__ == "__main__":
    unittest.main()
