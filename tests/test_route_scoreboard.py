"""`autocode usage scoreboard`: how each model did at each job, counted only from verified work (#4251)."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import autocode_route_scoreboard as scoreboard
import autocode_usage_report as usage_report

MODELS = {
    "Requirements": "claude-sonnet-5-5",
    "Planner": "claude-sonnet-5-5",
    "Plan Reviewer": "claude-opus-5-5",
    "Builder": "claude-haiku-4-5-20251001",
    "Tester": "claude-sonnet-5-5",
    "Completion Reviewer": "claude-opus-5-5",
    "Investigator": "claude-opus-5-5",
}
STAGE_JOB = {
    "requirements_gather": "Requirements",
    "draft_plan": "Planner",
    "revise_plan": "Planner",
    "review_plan": "Plan Reviewer",
    "finalize_plan": "Plan Reviewer",
    "builder": "Builder",
    "tester": "Tester",
    "review_completion": "Completion Reviewer",
    "investigate_stuck": "Investigator",
}


def _attempts(sequence: str, *, task: str = "task-1", usd: float | None = 0.5) -> list[dict]:
    """Attempts from a run's stage sequence: ``stage`` (accepted), ``stage!`` (rejected), ``stage+`` (its repair)."""
    found = []
    for index, word in enumerate(sequence.split()):
        rejected = word.endswith("!")
        repair = word.endswith("+")
        stage = word.rstrip("!+")
        job = STAGE_JOB[stage]
        found.append(
            {
                "stage": f"{stage}_report_repair" if repair else stage,
                "original_stage": stage if repair else None,
                "job": job,
                "model": MODELS[job],
                "reasoning_effort": "medium",
                "execution_identity": {"provider": "claude"},
                "task_id": task if job in ("Builder", "Tester", "Completion Reviewer", "Investigator") else None,
                "started_at": f"2026-11-14T10:{index:02d}:00+00:00",
                "rejected": rejected,
                "reported_cost_usd": usd,
                "tokens": {},
            }
        )
    return found


def _row(run: str, sequence: str, *, provenance="live", state="completed", size="small", **kwargs) -> dict:
    return {
        "run": run,
        "status": "TASK_COMPLETE" if state == "completed" else "WAITING_FOR_USER",
        "started_at": "2026-11-14T10:00:00+00:00",
        "updated_at": "2026-11-14T11:00:00+00:00",
        "evidence_provenance": provenance,
        "outcome": {"state": state, "plan_size": size},
        "accounting": {"attempts": _attempts(sequence, **kwargs)},
    }


def _route(payload: dict, job: str) -> dict:
    (entry,) = [entry for entry in payload["routes"] if entry["job"] == job]
    return entry


class CountingRuleTests(unittest.TestCase):
    def test_a_builder_attempt_another_builder_attempt_follows_on_the_same_task_was_sent_back(self):
        # The shape of a saved run where the Tester returned the first build (live-cargo4-probe-rust-tests-nolock).
        payload = scoreboard.build(
            [_row("r1", "draft_plan review_plan builder tester builder tester review_completion")]
        )

        builder = _route(payload, "Builder")
        self.assertEqual((2, 1, 1, 0), (builder["attempts"], builder["first_time"], builder["sent_back"], 0))
        self.assertEqual(0.5, builder["first_time_rate"])

    def test_a_builder_attempt_on_another_task_does_not_send_the_first_back(self):
        attempts = _attempts("builder", task="task-a") + _attempts("builder", task="task-b")
        attempts[1]["started_at"] = "2026-11-14T10:05:00+00:00"
        row = _row("r1", "requirements_gather")
        row["accounting"]["attempts"] = attempts

        builder = _route(scoreboard.build([row]), "Builder")

        self.assertEqual((2, 2, 0), (builder["attempts"], builder["first_time"], builder["sent_back"]))

    def test_a_planner_draft_a_revision_follows_before_building_was_sent_back(self):
        payload = scoreboard.build([_row("r1", "draft_plan review_plan revise_plan finalize_plan builder")])

        planner = _route(payload, "Planner")
        self.assertEqual((2, 1, 1), (planner["attempts"], planner["first_time"], planner["sent_back"]))

    def test_a_rejected_revision_still_shows_the_draft_was_returned(self):
        # p86-c-refactor-fixed: the revision's report was rejected and repaired; the draft was still returned.
        payload = scoreboard.build(
            [_row("r1", "draft_plan review_plan revise_plan! revise_plan+ finalize_plan builder")]
        )

        planner = _route(payload, "Planner")
        self.assertEqual(
            (2, 0, 1, 1, 1),
            (
                planner["attempts"],
                planner["first_time"],
                planner["sent_back"],
                planner["rejected"],
                planner["report_repairs"],
            ),
        )

    def test_a_planner_attempt_after_building_does_not_send_the_plan_back(self):
        payload = scoreboard.build([_row("r1", "draft_plan review_plan builder tester draft_plan review_plan builder")])

        planner = _route(payload, "Planner")
        self.assertEqual((2, 2, 0), (planner["attempts"], planner["first_time"], planner["sent_back"]))

    def test_a_rejected_attempt_is_not_first_time_and_its_repair_is_counted_apart(self):
        payload = scoreboard.build([_row("r1", "draft_plan! draft_plan+ review_plan builder")])

        planner = _route(payload, "Planner")
        self.assertEqual(
            (1, 0, 0, 1, 1),
            (
                planner["attempts"],
                planner["first_time"],
                planner["sent_back"],
                planner["rejected"],
                planner["report_repairs"],
            ),
        )
        self.assertEqual(0.0, planner["first_time_rate"])

    def test_a_timed_out_or_interrupted_attempt_counts_as_rejected(self):
        row = _row("r1", "builder builder")
        row["accounting"]["attempts"][0].update(rejected=False, timed_out=True)
        row["accounting"]["attempts"][1].update(rejected=False, interrupted=True)

        builder = _route(scoreboard.build([row]), "Builder")

        self.assertEqual((2, 0, 2), (builder["attempts"], builder["first_time"], builder["rejected"]))

    def test_a_check_is_judged_only_on_whether_its_answer_was_taken(self):
        payload = scoreboard.build([_row("r1", "builder tester! tester+ review_completion")])

        tester = _route(payload, "Tester")
        self.assertEqual("answer_only", tester["judged_by"])
        self.assertIsNone(tester["sent_back"])
        self.assertEqual(
            (1, 0, 1, 1), (tester["attempts"], tester["first_time"], tester["rejected"], tester["report_repairs"])
        )
        self.assertEqual("next_stage", _route(payload, "Builder")["judged_by"])

    def test_attempts_are_judged_in_the_order_they_started_not_the_order_saved(self):
        row = _row("r1", "builder tester builder")
        row["accounting"]["attempts"].reverse()

        builder = _route(scoreboard.build([row]), "Builder")

        self.assertEqual((1, 1), (builder["first_time"], builder["sent_back"]))

    def test_runner_owned_speculative_and_running_attempts_are_left_out(self):
        row = _row("r1", "builder builder builder builder")
        attempts = row["accounting"]["attempts"]
        attempts[1]["runner_owned"] = True
        attempts[2]["speculative"] = True
        attempts[3]["active"] = True

        builder = _route(scoreboard.build([row]), "Builder")

        self.assertEqual((1, 1, 0), (builder["attempts"], builder["first_time"], builder["sent_back"]))


class WhichRunsCountTests(unittest.TestCase):
    def test_only_live_completed_runs_count_and_the_rest_are_counted_by_reason(self):
        rows = [
            _row("live-done", "builder"),
            _row("fake", "builder", provenance="fake"),
            _row("mixed", "builder", provenance="mixed"),
            _row("paused", "builder", state="paused"),
            {"run": "legacy", "evidence_provenance": "live", "outcome": {"state": "completed"}},
        ]

        payload = scoreboard.build(rows)

        self.assertEqual((5, 1), (payload["total_runs"], payload["counted_runs"]))
        self.assertEqual({"no_attempts": 1, "not_completed": 1, "not_live": 2}, payload["excluded_runs"])
        self.assertEqual(1, _route(payload, "Builder")["attempts"])

    def test_routes_are_kept_apart_by_backend_model_and_level(self):
        rows = [_row("a", "builder"), _row("b", "builder")]
        rows[1]["accounting"]["attempts"][0]["reasoning_effort"] = "high"
        rows[0]["accounting"]["attempts"][0]["execution_identity"] = {"provider": "codex"}

        keys = {(entry["backend"], entry["effort"]) for entry in scoreboard.build(rows)["routes"]}

        self.assertEqual({("codex", "medium"), ("claude", "high")}, keys)

    def test_plan_size_splits_a_routes_counts(self):
        rows = [
            _row("a", "builder tester builder", size="large"),
            _row("b", "builder", size="small"),
            _row("c", "builder", size=None),
        ]

        builder = _route(scoreboard.build(rows), "Builder")

        self.assertEqual(
            {
                "small": {"attempts": 1, "first_time": 1, "sent_back": 0, "rejected": 0},
                "large": {"attempts": 2, "first_time": 1, "sent_back": 1, "rejected": 0},
                "unknown": {"attempts": 1, "first_time": 1, "sent_back": 0, "rejected": 0},
            },
            builder["by_plan_size"],
        )

    def test_burn_per_run_counts_every_attempt_and_says_when_it_is_a_floor(self):
        rows = [_row("a", "builder tester builder", usd=0.5), _row("b", "builder", usd=None)]

        builder = _route(scoreboard.build(rows), "Builder")

        self.assertEqual(
            (2, 1.0, 0.5, 1, False),
            (
                builder["runs"],
                builder["burn_usd"],
                builder["usd_per_run"],
                builder["unknown_burn_attempts"],
                builder["burn_complete"],
            ),
        )


class RangeTests(unittest.TestCase):
    def test_a_few_attempts_give_a_wide_range_and_many_a_narrow_one(self):
        low, high = scoreboard.wilson(3, 3)
        self.assertLess(low, 0.5)
        self.assertEqual(1.0, high)
        low, high = scoreboard.wilson(177, 192)
        self.assertAlmostEqual(0.88, low, places=2)
        self.assertAlmostEqual(0.95, high, places=2)
        self.assertIsNone(scoreboard.wilson(0, 0))


class TextTests(unittest.TestCase):
    def test_the_table_names_what_was_left_out_and_what_cannot_be_judged(self):
        text = scoreboard.format_text(
            scoreboard.build([_row("a", "builder tester builder tester"), _row("b", "builder", provenance="fake")])
        )

        self.assertIn("runs counted: 1 of 2", text)
        self.assertIn("Left out: 1 not live", text)
        self.assertRegex(text, r"Builder\s+claude-haiku-4-5-20251001\s+medium\s+2\s+1\s+1\s+0\s+50%")
        self.assertRegex(text, r"Tester\s+claude-sonnet-5-5\s+medium\s+2\s+2\s+-\s+0\s+100%")

    def test_an_empty_ledger_says_nothing_is_counted(self):
        self.assertIn("nothing is counted", scoreboard.format_text(scoreboard.build([])))


class CliTests(unittest.TestCase):
    def _project(self, rows: list[dict]) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        project = Path(tmp.name) / "project"
        ledger = project / ".autocode" / "usage.jsonl"
        ledger.parent.mkdir(parents=True)
        ledger.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        return project

    def _run(self, *argv: str) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(0, usage_report.cli(list(argv)))
        return out.getvalue()

    def test_scoreboard_prints_the_table_from_the_ledger(self):
        project = self._project([_row("20261114-a", "draft_plan review_plan builder tester review_completion")])

        text = self._run("scoreboard", "--workspace", str(project))

        self.assertIn("Route scoreboard (scope: workspace, runs counted: 1 of 1)", text)
        self.assertIn("claude-haiku-4-5-20251001", text)

    def test_scoreboard_json_is_structured(self):
        project = self._project([_row("20261114-a", "builder tester builder")])

        payload = json.loads(self._run("scoreboard", "--workspace", str(project), "--json"))

        self.assertEqual(1, payload["schema"])
        self.assertEqual(1, _route(payload, "Builder")["sent_back"])

    def test_since_keeps_only_runs_started_on_or_after_the_date(self):
        old = _row("20261101-old", "builder")
        old["started_at"] = old["updated_at"] = "2026-11-01T10:00:00+00:00"
        project = self._project([old, _row("20261114-new", "builder builder")])

        payload = json.loads(self._run("scoreboard", "--workspace", str(project), "--since", "2026-11-10", "--json"))

        self.assertEqual(1, payload["total_runs"])
        self.assertEqual(2, _route(payload, "Builder")["attempts"])


if __name__ == "__main__":
    unittest.main()
