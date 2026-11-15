"""Measured model ranking (#3630, part 1): shortlist, Requirements grader, recorded ranking.

AutoCode suggests a short list of an account's models at no cost, grades a Requirements answer
mechanically, and records the ranking where automatic selection and the quota fallback read it.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_measured_routes as measured_routes
import autocode_model_shortlist as shortlist
import autocode_requirements_probe as probe
import autocode_route_selector as route_selector

FIXTURE = Path(route_selector.__file__).with_name("fixtures") / "requirements_probe"


def offered(model, billing="subscription", **metadata):
    return {
        "model": model,
        "tool": "kilocode",
        "account": "account",
        "billing": billing,
        "levels": None,
        "probe": None,
        "metadata": metadata or None,
    }


class ShortlistTests(unittest.TestCase):
    def test_codex_list_suggests_the_three_current_models(self):
        models = ["gpt-6.1-sol", "gpt-6-sol", "gpt-6-luna", "gpt-6-astra", "gpt-5.6-terra", "gpt-5.6-sol"]
        self.assertEqual(
            ["gpt-6.1-sol", "gpt-6-astra", "gpt-6-luna"],
            shortlist.suggested_models([offered(m) for m in models]),
        )
        rows = {row["model"]: row for row in shortlist.shortlist([offered(m) for m in models])}
        self.assertEqual("a newer version is listed: gpt-6.1-sol", rows["gpt-6-sol"]["reason"])

    def test_a_large_gateway_is_cut_to_the_limit(self):
        models = [offered(f"kilo/vendor{v}/model-{n}", "pay per token") for v in range(40) for n in range(12)]
        suggested = shortlist.suggested_models(models)
        self.assertEqual(shortlist.LIMIT, len(suggested))
        rows = shortlist.shortlist(models)
        self.assertEqual(len(models), len(rows))
        self.assertTrue(all(row["reason"] for row in rows))

    def test_the_listing_rules_out_what_cannot_do_the_stage(self):
        rows = {
            row["model"]: row
            for row in shortlist.shortlist(
                [
                    offered("x/no-tools-1", toolcall=False),
                    offered("x/no-reasoning-1", reasoning=False),
                    offered("x/short-1", context=8000),
                    offered("x/big-1-mini"),
                    offered("x/gemini-3"),
                    offered("x/unknown-1"),
                ]
            )
        }
        for model in ("x/no-tools-1", "x/no-reasoning-1", "x/short-1", "x/big-1-mini"):
            self.assertFalse(rows[model]["suggested"], model)
        self.assertTrue(rows["x/gemini-3"]["suggested"], "'mini' inside a word is not a mini variant")
        self.assertTrue(rows["x/unknown-1"]["suggested"], "an unknown field never drops a model")

    def test_one_model_reached_two_ways_keeps_the_subscription_route(self):
        rows = {
            row["model"]: row
            for row in shortlist.shortlist(
                [offered("kilo/openai/gpt-6.1-sol", "pay per token"), offered("gpt-6.1-sol", "subscription")]
            )
        }
        self.assertTrue(rows["gpt-6.1-sol"]["suggested"])
        self.assertEqual("the same model as gpt-6.1-sol", rows["kilo/openai/gpt-6.1-sol"]["reason"])

    def test_newer_release_date_wins_over_version_number(self):
        models = [offered("x/line-9", release_date="2025-01-01"), offered("x/line-2", release_date="2026-10-01")]
        self.assertEqual(["x/line-2"], shortlist.suggested_models(models))


def answer(**fields):
    base = {"job": "build", "clarity": "clear", "assistant_reply": "Here is what I understood.", "open_questions": []}
    return {**base, **fields}


def question(text, category="requested_outcome"):
    return {"id": "q1", "question": text, "category": category}


class GraderTests(unittest.TestCase):
    def test_a_good_greeting_answer_passes(self):
        result = probe.grade(
            "R1",
            answer(
                job="none",
                clarity="vague",
                assistant_reply="Hi! What would you like to build or change?",
                open_questions=[question("What would you like to build or change?")],
            ),
            seconds=20,
            tool_calls=0,
        )
        self.assertTrue(result["passed"], result["failed"])
        self.assertTrue(result["checks"]["latency"])

    def test_reading_the_repository_for_a_greeting_fails(self):
        result = probe.grade(
            "R1",
            answer(job="none", clarity="vague", assistant_reply="What next?", open_questions=[question("What next?")]),
            tool_calls=4,
        )
        self.assertEqual(["tool_calls"], result["failed"])

    def test_asking_on_an_exact_change_fails(self):
        result = probe.grade(
            "R8",
            answer(assistant_reply="Which format?", open_questions=[question("Which format?")]),
            tool_calls=2,
        )
        self.assertIn("asks", result["failed"])

    def test_a_clear_build_without_behaviors_fails_the_handoff(self):
        result = probe.grade("R8", answer(required_behaviors=["prints JSON"], acceptance_tests=[]))
        self.assertEqual(["handoff"], result["failed"])
        passed = probe.grade("R8", answer(required_behaviors=["prints JSON"], acceptance_tests=["--json prints"]))
        self.assertTrue(passed["passed"], passed["failed"])

    def test_clear_while_asking_the_outcome_fails(self):
        result = probe.grade(
            "R2",
            answer(
                job="none", assistant_reply="What should improve?", open_questions=[question("What should improve?")]
            ),
            tool_calls=0,
        )
        self.assertEqual(["clarity"], result["failed"])

    def test_reply_that_asks_another_question_fails(self):
        result = probe.grade(
            "R2",
            answer(
                job="none",
                clarity="vague",
                assistant_reply="Do you mean speed?",
                open_questions=[question("What should get better?")],
            ),
            tool_calls=0,
        )
        self.assertEqual(["reply_matches_question"], result["failed"])

    def test_blocking_confirmation_on_a_clear_request_fails(self):
        result = probe.grade(
            "R9",
            answer(
                job="bugfix",
                assistant_reply="average([]) will return 0.0. Is that correct?",
                required_behaviors=["returns 0.0"],
                acceptance_tests=["average([]) == 0.0"],
            ),
        )
        self.assertEqual(["no_blocking_confirmation"], result["failed"])

    def test_wrong_job_and_missing_report_fail(self):
        self.assertIn("job", probe.grade("R5", answer(job="build"))["failed"])
        self.assertEqual(list(probe.QUALITY_CHECKS), probe.grade("R5", None)["failed"])

    def test_slow_answer_is_measured_not_failed(self):
        result = probe.grade("R6", answer(job="review"), seconds=300)
        self.assertTrue(result["passed"])
        self.assertFalse(result["checks"]["latency"])

    def test_quick_pass_needs_every_quick_request(self):
        grades = [{"request": r, "passed": True} for r in probe.QUICK]
        self.assertTrue(probe.passes_quick(grades))
        self.assertFalse(probe.passes_quick(grades[:-1]))


class FixtureTests(unittest.TestCase):
    """The facts the nine requests rely on are true in the bundled project."""

    def test_the_requests_match_the_project(self):
        self.assertIn("def main", (FIXTURE / "report.py").read_text())
        self.assertIn("TTL_SECONDS = 3600", (FIXTURE / "cache.py").read_text())
        sys.path.insert(0, str(FIXTURE))
        try:
            import totals

            with self.assertRaises(ZeroDivisionError):
                totals.average([])
        finally:
            sys.path.remove(str(FIXTURE))
            sys.modules.pop("totals", None)

    def test_exactly_one_test_fails(self):
        with tempfile.TemporaryDirectory() as cache:
            run = subprocess.run(
                [sys.executable, "-m", "unittest", "test_totals"],
                cwd=FIXTURE,
                capture_output=True,
                text=True,
                env={**os.environ, "PYTHONPYCACHEPREFIX": cache},
            )
        self.assertNotEqual(0, run.returncode)
        self.assertIn("FAILED (failures=1)", run.stderr)


def measured(model, effort, passed, *, seconds=40.0, cost=0.5, total=9):
    grades = [
        {"request": f"R{i + 1}", "passed": i < passed, "seconds": seconds, "cost_usd": cost / total}
        for i in range(total)
    ]
    return measured_routes.result(model, effort, grades, date="2026-11-15")


class MeasuredRankingTests(unittest.TestCase):
    def test_lowest_qualifying_level_then_passes_then_cost(self):
        rows = measured_routes.rank(
            [
                measured("anthropic/claude-opus-5-5", "medium", 8, cost=2.6),
                measured("zai-coding-plan/glm-5.3", "high", 7, seconds=132, cost=1.0),
                measured("zai-coding-plan/glm-5.3", "low", 6, seconds=70, cost=0.54),
                measured("openai/gpt-6.1-sol", "medium", 6, seconds=37, cost=0.81),
                measured("openai/gpt-6-luna", "low", 4),
            ]
        )
        self.assertEqual(
            [
                ("anthropic/claude-opus-5-5", "medium", True),
                ("zai-coding-plan/glm-5.3", "low", True),
                ("openai/gpt-6.1-sol", "medium", True),
                ("openai/gpt-6-luna", "low", False),
            ],
            [(row["model"], row["effort"], row["qualified"]) for row in rows],
        )

    def test_only_the_quick_pass_does_not_qualify(self):
        self.assertFalse(measured_routes.qualifies(measured("x/m-1", "low", 3, total=3)))

    def test_recording_again_replaces_the_same_model_and_level(self):
        with tempfile.TemporaryDirectory() as home:
            file = Path(home) / "measured.json"
            measured_routes.record("requirements", [measured("x/m-1", "low", 9)], file=file)
            measured_routes.record(
                "requirements", [measured("x/m-1", "low", 2), measured("x/m-1", "high", 9)], file=file
            )
            saved = measured_routes.load(file)["roles"]["requirements"]["results"]
        self.assertEqual([("high", 9), ("low", 2)], [(r["effort"], r["summary"]["passed"]) for r in saved])

    def test_unreadable_file_is_no_measurement(self):
        with tempfile.TemporaryDirectory() as home:
            file = Path(home) / "measured.json"
            file.write_text("{not json")
            self.assertEqual({"schema_version": 1, "roles": {}}, measured_routes.load(file))


class SelectionReadsTheMeasurementTests(unittest.TestCase):
    """Selection and the quota fallback follow the person's measured ranking."""

    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        patcher = patch.dict("os.environ", {"XDG_CONFIG_HOME": self.home.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def candidates(self):
        return [
            offered("zai-coding-plan/glm-5.3"),
            offered("anthropic/claude-opus-5-5"),
            offered("openai/gpt-6.1-sol"),
        ]

    def chosen(self):
        return route_selector.select(["requirements"], self.candidates())["routes"]["requirements"]

    def test_without_a_measurement_the_shipped_order_holds(self):
        self.assertEqual("zai-coding-plan/glm-5.3", self.chosen()["model"])

    def test_measured_ranking_leads_and_a_failed_model_is_left_out(self):
        measured_routes.record(
            "requirements",
            [
                measured("openai/gpt-6.1-sol", "medium", 8),
                measured("zai-coding-plan/glm-5.3", "low", 3),
            ],
        )
        route = self.chosen()
        self.assertEqual(("openai/gpt-6.1-sol", "medium"), (route["model"], route["reasoning_effort"]))
        models = [entry["model"] for entry in route_selector.qualified("requirements")]
        self.assertEqual(["openai/gpt-6.1-sol", "anthropic/claude-opus-5-5"], models)
        self.assertEqual("measured", route_selector.qualified("requirements")[0]["provenance"])
        self.assertIn("8/9 requests", route_selector.evidence_summary(route_selector.qualified("requirements")[0]))

    def test_quota_fallback_follows_the_measured_order(self):
        measured_routes.record(
            "requirements",
            [measured("openai/gpt-6.1-sol", "medium", 9), measured("anthropic/claude-opus-5-5", "medium", 7)],
        )
        selection = route_selector.select(["requirements"], self.candidates())
        selection["candidates"] = route_selector.permitted_set(self.candidates())
        first = selection["routes"]["requirements"]["model"]
        self.assertEqual("openai/gpt-6.1-sol", first)
        self.assertEqual(
            "anthropic/claude-opus-5-5", route_selector.next_route(selection, "requirements", first)["model"]
        )
        after = route_selector.next_route(selection, "requirements", "anthropic/claude-opus-5-5", failed_models=[first])
        self.assertEqual("zai-coding-plan/glm-5.3", after["model"])


if __name__ == "__main__":
    unittest.main()
