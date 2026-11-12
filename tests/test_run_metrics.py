"""run_metrics: where a run's spend went, what was spent again, and what its checking produced."""

import unittest

import autocode_chat_panel as panel
import autocode_chat_spend as spend
import autocode_run_metrics as run_metrics
import autocode_run_view as run_view


def quantity(value, *, complete=True):
    return {"value": value if complete else None, "known": value, "complete": complete}


def stage(stage, job, spent, *, runs=1, complete=True):
    return {"stage": stage, "job": job, "stages": runs, "spent_usd": spent, "complete": complete}


def attempt(stage, job, usd, *, exit_code=0, rejected=False, complete=True, **extra):
    return {
        "stage": stage,
        "job": job,
        "runner_owned": False,
        "active": False,
        "rejected": rejected,
        "interrupted": False,
        "timed_out": False,
        "exit_code": exit_code,
        "attributed_reported_usd": quantity(usd, complete=complete),
        **extra,
    }


def view():
    """A finished bug fix: the Planner drafted once, the Builder ran twice (its first attempt was rejected),
    the Tester's report was repaired once, then the checks passed."""
    return {
        "cost": {
            "spent_usd": 2.0,
            "complete": True,
            "breakdown": {
                "by_stage": [
                    stage("draft_plan", "Planner", 0.2),
                    stage("builder", "Builder", 0.5, runs=2),
                    stage("review_completion", "Completion Reviewer", 0.8),
                    stage("tester", "Tester", 0.4),
                    stage("tester_report_repair", "Tester (report repair)", 0.1),
                    stage("regression_proof", "Regression Proof", 0.0),
                ]
            },
        },
        "usage": {
            "accounting": {
                "tokens": {
                    "input_tokens": quantity(1000),
                    "fresh_input_tokens": quantity(100),
                    "cache_write_tokens": quantity(200),
                    "cached_input_tokens": quantity(700),
                    "output_tokens": quantity(50),
                },
                "attempts": [
                    attempt("draft_plan", "Planner", 0.2, duration_seconds=100.0),
                    attempt("builder", "Builder", 0.2, rejected=True, duration_seconds=30.0),
                    attempt("builder", "Builder", 0.3, duration_seconds=60.0),
                    attempt("tester", "Tester", 0.4, duration_seconds=40.0),
                    # The core records a repair against the stage it repairs, under that stage's job.
                    attempt("tester_report_repair", "Tester", 0.1, original_stage="tester", duration_seconds=20.0),
                    attempt("review_completion", "Completion Reviewer", 0.8, duration_seconds=50.0),
                    attempt("regression_proof", "Regression Proof", 0.0, runner_owned=True, duration_seconds=5.0),
                ],
            }
        },
        "efficiency": {
            "criteria": {"requested": 3, "accepted": 3},
            # Only what a provider itself reported (none here): report repairs are priced like every other stage.
            "by_category": {"report_repair": {"reported_usd": quantity(0, complete=False)}},
        },
        "recoveries": [{"id": "r1", "kind": "builder_sent_back"}],
        "evidence": {
            "regression_proof": {"verdict": "PASS", "fail_to_pass": ["t1", "t2"]},
            "check_replay": {"verdict": "PASS", "checks": [{"id": "c1"}]},
            "mutation_check": {"files": [{"mutants": [{"outcome": "killed"}, {"outcome": "survived"}]}]},
            "findings": [{"blocking": True}, {"blocking": False}],
        },
    }


class RunMetricsTests(unittest.TestCase):
    def test_spend_by_job_is_costliest_first_with_its_share_of_the_run(self):
        metrics = run_metrics.project(view())
        self.assertEqual(
            [("Completion Reviewer", 0.8, 0.4, 1), ("Builder", 0.5, 0.25, 2), ("Tester", 0.4, 0.2, 1)],
            [(row["job"], row["spent_usd"], row["share"], row["attempts"]) for row in metrics["by_job"][:3]],
        )

    def test_each_job_carries_its_own_stages_time_and_a_running_one_is_not_complete(self):
        jobs = {row["job"]: row for row in run_metrics.project(view())["by_job"]}
        self.assertEqual(
            {
                "Builder": (90.0, True),
                "Tester": (40.0, True),
                "Tester (report repair)": (20.0, True),
                "Planner": (100.0, True),
            },
            {
                job: (jobs[job]["seconds"], jobs[job]["seconds_complete"])
                for job in ("Builder", "Tester", "Tester (report repair)", "Planner")
            },
        )
        running = view()
        running["usage"]["accounting"]["attempts"][2].update(active=True, duration_seconds=None)
        builder = next(row for row in run_metrics.project(running)["by_job"] if row["job"] == "Builder")
        self.assertEqual((30.0, False), (builder["seconds"], builder["seconds_complete"]))

    def test_token_split_names_new_input_cache_writes_and_reads(self):
        tokens = run_metrics.project(view())["tokens"]
        self.assertEqual(
            {"input": 1000, "new": 100, "cache_write": 200, "cache_read": 700, "output": 50, "complete": True},
            {key: tokens[key] for key in ("input", "new", "cache_write", "cache_read", "output", "complete")},
        )
        self.assertEqual((0.7, 0.2), (tokens["cache_read_share"], tokens["cache_write_share"]))

    def test_a_partly_recorded_count_shows_its_known_part_and_is_not_complete(self):
        partial = view()
        partial["usage"]["accounting"]["tokens"]["cache_write_tokens"] = quantity(200, complete=False)
        tokens = run_metrics.project(partial)["tokens"]
        self.assertEqual((200, False), (tokens["cache_write"], tokens["complete"]))
        del partial["usage"]["accounting"]["tokens"]["fresh_input_tokens"]
        self.assertIsNone(run_metrics.project(partial)["tokens"]["new"])

    def test_a_count_some_attempts_did_not_report_is_unknown_not_its_known_part(self):
        older = view()
        older["usage"]["accounting"]["tokens"]["cache_write_tokens"] = {
            "value": None,
            "known": 0,
            "complete": False,
            "known_items": 2,
            "unknown_items": 6,
        }
        tokens = run_metrics.project(older)["tokens"]
        self.assertEqual((None, None, False), (tokens["cache_write"], tokens["cache_write_share"], tokens["complete"]))
        self.assertEqual(0.7, tokens["cache_read_share"])

    def test_waste_is_the_failed_attempts_the_jobs_run_again_and_report_repairs(self):
        waste = run_metrics.project(view())["waste"]
        self.assertEqual(
            {
                "failed_attempts": 1,
                "failed_usd": 0.2,
                "failed_usd_complete": True,
                "repeated_jobs": [
                    {"job": "Builder", "attempts": 2, "runs": 2, "report_repairs": 0},
                    {"job": "Tester", "attempts": 2, "runs": 1, "report_repairs": 1},
                ],
                "report_repair_usd": 0.1,
                "report_repair_complete": True,
            },
            waste,
        )

    def test_a_failed_attempt_with_unknown_cost_is_counted_but_not_called_complete(self):
        unknown = view()
        unknown["usage"]["accounting"]["attempts"][1] = attempt("builder", "Builder", None, exit_code=1)
        waste = run_metrics.project(unknown)["waste"]
        self.assertEqual((1, 0.0, False), (waste["failed_attempts"], waste["failed_usd"], waste["failed_usd_complete"]))

    def test_the_builder_stands_in_for_one_model_beside_what_the_checking_produced(self):
        single = run_metrics.project(view())["single_model"]
        self.assertEqual((0.5, 0.25, 1.5), (single["builder_usd"], single["builder_share"], single["checking_usd"]))
        self.assertIn("not a measured single-model run", single["basis"])
        self.assertEqual(
            {
                "criteria_requested": 3,
                "criteria_accepted": 3,
                "regression_verdict": "PASS",
                "fail_to_pass_tests": 2,
                "clean_replay_checks": 1,
                "clean_replay_verdict": "PASS",
                "mutants_killed": 1,
                "mutants_tried": 2,
                "findings": 2,
                "blocking_findings": 1,
                "problems_handled": 1,
            },
            single["produced"],
        )

    def test_planning_is_named_apart_from_the_checking_after_it(self):
        # #3268: "Builder 18% · checking 82%" counted Requirements and the Planner ($2.1 of $3.1) as checking.
        planned = view()
        planned["cost"]["breakdown"]["by_stage"] += [
            stage("requirements_gather", "Requirements", 0.4),
            stage("draft_plan", "Planner", 1.2),
            stage("review_plan", "Plan Reviewer", 0.4),
        ]
        planned["cost"]["spent_usd"] = 4.0
        single = run_metrics.project(planned)["single_model"]
        self.assertEqual((0.5, 3.5), (single["builder_usd"], single["checking_usd"]))
        # view() already has a $0.20 Planner.
        self.assertEqual((2.2, 1.3), (single["planning_usd"], single["review_usd"]))
        self.assertEqual((0.55, 0.325), (single["planning_share"], single["review_share"]))
        planned["run_metrics"] = run_metrics.project(planned)
        self.assertEqual("Cost: Builder 12% · planning 55% · checking 32%", spend.compact(planned)[0])
        self.assertIn("  Builder $0.50 (12%); planning $2.20; checking $1.30", spend.details(planned).splitlines())

    def test_a_view_from_an_older_core_says_everything_else_not_checking(self):
        older = {"run_metrics": {"schema": 1, "spent_usd": 2.0, "by_job": [], "single_model": {"builder_share": 0.25}}}
        self.assertEqual("Cost: Builder 25% · everything else 75%", spend.compact(older)[0])

    def test_a_run_with_no_builder_yet_has_no_stand_in(self):
        early = view()
        early["cost"]["breakdown"]["by_stage"] = [stage("requirements_gather", "Requirements", 0.1)]
        early["cost"]["spent_usd"] = 0.1
        single = run_metrics.project(early)["single_model"]
        self.assertEqual((None, None, None), (single["builder_usd"], single["builder_share"], single["checking_usd"]))
        self.assertEqual((None, None), (single["planning_usd"], single["review_usd"]))

    def test_budget_exhausted_mutants_do_not_inflate_the_reported_work(self):
        partial = view()
        partial["evidence"]["mutation_check"]["files"] = [
            {"mutants": [{"outcome": "killed"}] * 15 + [{"outcome": "survived"}] * 18},
            {"mutants": [{"outcome": "not_run"}] * 212},
        ]
        metrics = run_metrics.project(partial)
        produced = metrics["single_model"]["produced"]
        self.assertEqual(33, produced["mutants_tried"])
        self.assertEqual(15, produced["mutants_killed"])

    def test_only_recorded_execution_outcomes_count_as_mutants_tried(self):
        for outcome, tried in (
            ("killed", 1),
            ("survived", 1),
            ("timeout", 1),
            ("not_run", 0),
            ("unknown", 0),
            (None, 0),
            ({}, 0),
        ):
            with self.subTest(outcome=outcome):
                observed = view()
                observed["evidence"]["mutation_check"]["files"] = [{"mutants": [{"outcome": outcome}]}]
                self.assertEqual(tried, run_metrics.project(observed)["single_model"]["produced"]["mutants_tried"])

    def test_the_status_view_carries_run_metrics_for_a_run_with_nothing_spent(self):
        public = run_view.view({"status": "RUNNING"})
        metrics = public["run_metrics"]
        self.assertEqual((1, [], 0), (metrics["schema"], metrics["by_job"], metrics["waste"]["failed_attempts"]))
        self.assertEqual(public["cost"].get("spent_usd"), metrics["spent_usd"])


class SpendDisplayTests(unittest.TestCase):
    def public(self):
        result = view()
        result["run_metrics"] = run_metrics.project(result)
        return result

    def test_the_panel_shows_the_cost_split_costliest_jobs_cache_and_waste(self):
        section = next(section for section in panel.sections(self.public()) if section.title == "Spend")
        self.assertEqual("Cost: Builder 25% · planning 10% · checking 65%", section.summary)
        self.assertEqual(
            [
                "Completion Reviewer $0.80 · 40% of cost · 50s",
                "Builder $0.50 · 25% of cost · 1m30s, 2 runs",
                "Tester $0.40 · 20% of cost · 40s",
                "Cache reads 70% of input, cache writes 20%",
                "1 failed attempt $0.20; ran again: Builder ×2; report repairs: Tester ×1",
            ],
            [row.text for row in section.rows],
        )

    def test_spend_lists_every_job_the_tokens_and_what_the_checking_bought(self):
        text = panel.text("/spend", self.public(), panel.StageLog())
        self.assertEqual(spend.details(self.public()), text)
        for line in (
            "Where the money went: $2.00",
            "  Tokens: input 1.0k, new 100, cache write 200, cache read 700, output 50",
            "  Report repairs $0.10",
            "  Builder $0.50 (25%); planning $0.20; checking $1.30",
            "  What that bought: 3/3 criteria accepted; 2 tests fail before, pass after; 1 check rerun clean; "
            "1/2 broken lines caught; 1 blocking review finding; 1 problem handled",
        ):
            self.assertIn(line, text.splitlines())
        self.assertNotIn("Regression Proof", text)  # a job with no model spend is not a spend line

    def test_nothing_is_shown_before_any_spend(self):
        public = {"run_metrics": run_metrics.project({})}
        self.assertNotIn("Spend", [section.title for section in panel.sections(public)])
        self.assertEqual("Nothing has been spent on models yet.", panel.text("/spend", public, panel.StageLog()))

    def test_an_absurd_figure_is_unknown_not_a_crash(self):
        # A JSON integer too large for a float once raised OverflowError in _number, killing /spend.
        absurd = 10**400
        self.assertEqual("unknown", spend._money(absurd))
        self.assertEqual("?", spend._percent(absurd))
        self.assertEqual("unknown", spend._tokens(absurd))

    def test_spend_is_a_run_command(self):
        self.assertIn("/spend", [command.name for command in panel.commands(asking=False)])
        self.assertIn("/spend", panel.RUN_COMMANDS)


if __name__ == "__main__":
    unittest.main()
