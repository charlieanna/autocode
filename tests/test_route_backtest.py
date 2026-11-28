"""The saved-run backtest of shadow route suggestions and its pass bar (#4251 step 3)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_route_backtest as backtest
import autocode_route_suggestion as suggestion

HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-5-5"
OPUS = "claude-opus-5-5"


def _settings(builder=SONNET, origin="runner_default"):
    roles = {
        "builder": {"model": builder, "reasoning_effort": "medium"},
        "tester": {"model": OPUS, "reasoning_effort": "medium"},
        "completion": {"model": OPUS, "reasoning_effort": "medium"},
    }
    return {"provider": "claude", "engine": "opencode", "roles": roles, "route_origins": dict.fromkeys(roles, origin)}


def _entry(index, model, *, back, origin="runner_default"):
    first = {
        "stage": "builder",
        "job": "Builder",
        "model": model,
        "reasoning_effort": "medium",
        "execution_identity": {"provider": "claude"},
        "task_id": f"task-{index}",
        "started_at": f"2026-11-{10 + index // 60:02d}T10:{index % 60:02d}:00+00:00",
        "reported_cost_usd": 0.1,
    }
    attempts = [first] + ([{**first, "started_at": first["started_at"].replace("T10:", "T11:")}] if back else [])
    row = {
        "run": f"run-{index:03d}",
        "started_at": first["started_at"],
        "evidence_provenance": "live",
        "outcome": {"state": "completed", "plan_size": "small"},
        "accounting": {"attempts": attempts},
    }
    return {"row": row, "settings": _settings(model, origin)}


def _history(origin="runner_default"):
    """Sonnet builds sent back every other time, Haiku's never: Haiku is cheaper, same family and better."""
    found = []
    for index in range(40):
        found.append(_entry(2 * index, SONNET, back=index % 2 == 0, origin=origin))
        found.append(_entry(2 * index + 1, HAIKU, back=False, origin=origin))
    return found


class ReplayTests(unittest.TestCase):
    def test_a_clearly_better_cheaper_route_is_suggested_and_the_bar_passes(self):
        report = backtest.replay(_history())

        self.assertTrue(report["passed"], report["checks"])
        self.assertGreater(report["cases"]["unchosen"]["builder"]["suggested"], 0)
        self.assertEqual([], report["too_little_data"])

    def test_each_run_is_judged_only_on_the_runs_before_it(self):
        report = backtest.replay(list(reversed(_history())))

        reasons = report["reasons"]["unchosen"]["builder"]
        # The oldest runs had no earlier evidence, whatever order the entries arrive in: the first
        # MIN_ATTEMPTS Haiku runs (one attempt each) cannot have enough for their own route.
        self.assertGreaterEqual(reasons["too little evidence"], suggestion.MIN_ATTEMPTS)
        self.assertEqual(80, sum(reasons.values()))

    def test_the_persons_choice_is_never_overridden(self):
        report = backtest.replay(_history(origin="user_explicit"))

        self.assertEqual(0, report["cases"]["as_saved"]["builder"]["suggested"])
        self.assertGreater(report["cases"]["unchosen"]["builder"]["suggested"], 0)
        self.assertTrue(report["passed"])

    def test_one_measured_route_per_job_is_named_as_too_little_data(self):
        report = backtest.replay([_entry(index, SONNET, back=False) for index in range(20)])

        self.assertEqual([{"backend": "claude", "job": "Builder"}], report["too_little_data"])
        self.assertEqual(0, report["cases"]["unchosen"]["builder"]["suggested"])


class PassBarCatchesBrokenSuggestionsTests(unittest.TestCase):
    """A defect in the suggesting code must fail the bar, so each rule is checked here independently."""

    def broken(self, model, **extra):
        real = suggestion.suggest

        def suggest(settings, board, *, backend):
            found = real(settings, board, backend=backend)
            record = found["stages"]["builder"]
            record.update(
                {
                    "suggested": {"model": model, "effort": "medium", "attempts": 30, "first_time": 30},
                    "running": {"attempts": 30},
                    **extra,
                }
            )
            record.pop("blocked", None)
            return found

        return patch.object(suggestion, "suggest", suggest)

    def test_a_stronger_model_slipped_through_fails_the_bar(self):
        with self.broken(OPUS):
            report = backtest.replay(_history())
        self.assertGreater(report["checks"]["stronger_than_checker"], 0)
        self.assertFalse(report["passed"])

    def test_a_model_its_checker_shares_a_family_with_fails_the_bar(self):
        entries = _history()
        for entry in entries:
            entry["settings"].update(provider="opencode")
            entry["settings"]["roles"]["tester"]["model"] = "openai/gpt-6-luna"
            entry["settings"]["roles"]["builder"]["model"] = "openai/gpt-6-sol"
        with self.broken("openai/gpt-6-luna"):
            report = backtest.replay(entries)
        self.assertGreater(report["checks"]["independence_breaks"], 0)

    def test_a_suggestion_over_the_persons_choice_fails_the_bar(self):
        with self.broken(HAIKU):
            report = backtest.replay(_history(origin="user_explicit"))
        self.assertGreater(report["checks"]["over_person_choice"], 0)

    def test_a_suggestion_on_too_few_attempts_fails_the_bar(self):
        with self.broken(HAIKU, running={"attempts": 3}):
            report = backtest.replay(_history())
        self.assertGreater(report["checks"]["under_evidence"], 0)

    def test_a_suggestion_that_did_worse_over_all_runs_fails_the_bar(self):
        entries = [_entry(index, HAIKU, back=True) for index in range(30)]
        entries += [_entry(100 + index, SONNET, back=False) for index in range(30)]
        with self.broken(HAIKU):
            report = backtest.replay(entries)
        self.assertGreater(report["checks"]["worse_than_running"], 0)


class BundleTests(unittest.TestCase):
    def test_a_saved_bundle_is_projected_into_the_row_the_ledger_would_hold(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "bundle-a" / "run"
            run.mkdir(parents=True)
            state = {
                "status": "TASK_COMPLETE",
                "settings": {"evidence_provenance": {"kind": "live"}, "roles": {"builder": {"model": HAIKU}}},
                "stages": [
                    {
                        "stage": "builder",
                        "role": "builder",
                        "engine": "opencode",
                        "model": HAIKU,
                        "task_id": "task-1",
                        "started_at": "2026-11-14T10:00:00+00:00",
                        "duration_seconds": 3.0,
                    }
                ],
            }
            (run / "state.json").write_text(json.dumps(state))

            entry = backtest.project(run / "state.json")

        self.assertEqual("live", entry["row"]["evidence_provenance"])
        self.assertEqual("completed", entry["row"]["outcome"]["state"])
        self.assertEqual(["builder"], [item["stage"] for item in entry["row"]["accounting"]["attempts"]])
        self.assertEqual(HAIKU, entry["settings"]["roles"]["builder"]["model"])


if __name__ == "__main__":
    unittest.main()
