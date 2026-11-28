"""Shadow route suggestions: what measured results would pick, recorded beside the routes that run (#4251 step 2)."""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode as runner
import autocode_configure
import autocode_milestones as milestones
import autocode_opencode as oc
import autocode_planning as planning
import autocode_route_suggestion as suggestion
import autocode_stages_cli as stages_cli
import autopilot

from . import test_planning

HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-5-5"
OPUS = "claude-opus-5-5"


def _entry(job, model, *, attempts, first_time, effort="medium", backend="claude", usd=0.1):
    low, high = suggestion_range(first_time, attempts)
    return {
        "backend": backend,
        "job": job,
        "model": model,
        "effort": effort,
        "attempts": attempts,
        "first_time": first_time,
        "first_time_range": [low, high],
        "usd_per_run": usd,
    }


def suggestion_range(first_time, attempts):
    import autocode_route_scoreboard as scoreboard

    return scoreboard.wilson(first_time, attempts)


def _settings(**origins):
    roles = {
        "planner": {"model": SONNET, "reasoning_effort": "medium"},
        "plan_reviewer": {"model": OPUS, "reasoning_effort": "medium"},
        "builder": {"model": SONNET, "reasoning_effort": "medium"},
        "tester": {"model": OPUS, "reasoning_effort": "medium"},
        "completion": {"model": OPUS, "reasoning_effort": "medium"},
    }
    return {
        "provider": "claude",
        "engine": "opencode",
        "roles": roles,
        "route_origins": {role: origins.get(role, "runner_default") for role in roles},
    }


def _board(*entries):
    return {"counted_runs": 40, "routes": list(entries)}


class SuggestTests(unittest.TestCase):
    def test_a_cheaper_same_family_route_measured_clearly_better_is_suggested(self):
        board = _board(
            _entry("Builder", SONNET, attempts=30, first_time=12), _entry("Builder", HAIKU, attempts=30, first_time=29)
        )

        record = suggestion.suggest(_settings(), board, backend="claude")["stages"]["builder"]

        self.assertEqual(HAIKU, record["suggested"]["model"])
        self.assertNotIn("blocked", record)
        self.assertEqual({"model": SONNET, "effort": "medium"}, record["ran"])

    def test_the_persons_choice_is_kept_with_no_suggestion(self):
        board = _board(
            _entry("Builder", SONNET, attempts=30, first_time=12), _entry("Builder", HAIKU, attempts=30, first_time=29)
        )

        record = suggestion.suggest(_settings(builder="user_explicit"), board, backend="claude")["stages"]["builder"]

        self.assertIsNone(record["suggested"])
        self.assertIn("the person chose", record["reason"])

    def test_single_model_mode_is_the_persons_choice_too(self):
        settings = {**_settings(), "single_model_mode": True}
        board = _board(
            _entry("Builder", SONNET, attempts=30, first_time=12), _entry("Builder", HAIKU, attempts=30, first_time=29)
        )

        self.assertIsNone(suggestion.suggest(settings, board, backend="claude")["stages"]["builder"]["suggested"])

    def test_checking_stages_get_no_suggestion_because_the_ledger_cannot_judge_them(self):
        board = _board(
            _entry("Tester", OPUS, attempts=30, first_time=10), _entry("Tester", SONNET, attempts=30, first_time=30)
        )

        stages = suggestion.suggest(_settings(), board, backend="claude")["stages"]

        for role in ("tester", "completion", "plan_reviewer"):
            self.assertIsNone(stages[role]["suggested"], role)
            self.assertIn("not judged", stages[role]["reason"])

    def test_too_few_attempts_on_the_running_route_suggest_nothing(self):
        board = _board(
            _entry("Builder", SONNET, attempts=7, first_time=0), _entry("Builder", HAIKU, attempts=30, first_time=30)
        )

        record = suggestion.suggest(_settings(), board, backend="claude")["stages"]["builder"]

        self.assertIsNone(record["suggested"])
        self.assertIn("7 of 8", record["reason"])

    def test_a_candidate_with_too_few_attempts_or_an_overlapping_range_is_not_suggested(self):
        for candidate in (
            _entry("Builder", HAIKU, attempts=7, first_time=7),
            _entry("Builder", HAIKU, attempts=30, first_time=24),
        ):
            with self.subTest(candidate=candidate["attempts"]):
                board = _board(_entry("Builder", SONNET, attempts=30, first_time=20), candidate)
                record = suggestion.suggest(_settings(), board, backend="claude")["stages"]["builder"]
                self.assertIsNone(record["suggested"])
                self.assertIn("no better route", record["reason"])

    def test_another_backends_results_are_never_used(self):
        board = _board(
            _entry("Builder", SONNET, attempts=30, first_time=12),
            _entry("Builder", HAIKU, attempts=30, first_time=29, backend="kilocode"),
        )

        self.assertIsNone(suggestion.suggest(_settings(), board, backend="claude")["stages"]["builder"]["suggested"])

    def test_a_stronger_or_unpriced_or_other_family_model_is_recorded_but_blocked(self):
        cases = (
            (OPUS, "priced above"),
            ("gpt-5.6-sol", "another model family"),
            ("claude-unpriced-9", "no price"),
        )
        for model, reason in cases:
            with self.subTest(model=model):
                settings = _settings()
                settings["roles"]["tester"]["model"] = "glm-5.3"
                settings["roles"]["completion"]["model"] = "glm-5.3"
                board = _board(
                    _entry("Builder", SONNET, attempts=30, first_time=12),
                    _entry("Builder", model, attempts=30, first_time=29),
                )
                record = suggestion.suggest(settings, board, backend="claude")["stages"]["builder"]
                self.assertEqual(model, record["suggested"]["model"])
                self.assertIn(reason, record["blocked"])

    def test_a_suggestion_its_checker_would_share_a_family_with_is_blocked(self):
        settings = {**_settings(), "provider": "opencode"}
        settings["roles"]["tester"]["model"] = "openai/gpt-6-luna"
        board = _board(
            _entry("Builder", "openai/gpt-6-sol", attempts=30, first_time=12, backend="opencode"),
            _entry("Builder", "openai/gpt-6-luna", attempts=30, first_time=29, backend="opencode"),
        )
        settings["roles"]["builder"]["model"] = "openai/gpt-6-sol"

        record = suggestion.suggest(settings, board, backend="opencode")["stages"]["builder"]

        self.assertIn("independence", record["blocked"])

    def test_the_planner_is_judged_like_the_builder(self):
        board = _board(
            _entry("Planner", SONNET, attempts=40, first_time=10), _entry("Planner", HAIKU, attempts=40, first_time=38)
        )

        self.assertEqual(
            HAIKU, suggestion.suggest(_settings(), board, backend="claude")["stages"]["planner"]["suggested"]["model"]
        )

    def test_the_suggestion_changes_nothing_it_reads(self):
        settings = _settings()
        before = json.dumps(settings, sort_keys=True)
        board = _board(
            _entry("Builder", SONNET, attempts=30, first_time=12), _entry("Builder", HAIKU, attempts=30, first_time=29)
        )

        suggestion.suggest(settings, board, backend="claude")

        self.assertEqual(before, json.dumps(settings, sort_keys=True))


class SwitchTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(suggestion.ENV, None)

    def test_shadow_is_the_default_and_the_saved_switch_turns_it_off(self):
        self.assertEqual("shadow", suggestion.mode(self.folder))
        suggestion.save_mode(self.folder, "off")
        self.assertEqual("off", suggestion.mode(self.folder))

    def test_the_environment_wins_over_the_saved_switch(self):
        suggestion.save_mode(self.folder, "off")
        os.environ[suggestion.ENV] = "shadow"
        self.assertEqual("shadow", suggestion.mode(self.folder))

    def test_an_unreadable_or_unknown_switch_is_shadow_and_a_bad_value_is_refused(self):
        suggestion.switch_path(self.folder).write_text("{not json")
        self.assertEqual("shadow", suggestion.mode(self.folder))
        with self.assertRaises(ValueError):
            suggestion.save_mode(self.folder, "on")

    def test_stages_auto_shows_and_sets_the_switch(self):
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.folder)}):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(0, stages_cli.cli(["auto", "off"]))
                self.assertEqual(0, stages_cli.cli(["auto", "--json"]))
            self.assertIn("off", out.getvalue().splitlines()[0])
            self.assertEqual("off", json.loads(out.getvalue().splitlines()[-1])["mode"])
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(2, stages_cli.cli(["auto", "on"]))


class ConfiguredRunTests(unittest.TestCase):
    """The gate for step 2: a shadow run's routes equal the same run's with the switch off."""

    def setUp(self):
        for boundary in (
            patch.object(runner, "opencode", oc),
            patch.object(autocode_configure, "_listed_levels", return_value={}),
            patch(
                "autocode_route_discovery.discover", return_value={"tool": "opencode", "candidates": [], "accounts": []}
            ),
            patch("autocode_captured_process.run_metadata", side_effect=AssertionError("no metadata CLI")),
        ):
            boundary.start()
            self.addCleanup(boundary.stop)
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        env = patch.dict(os.environ, {"XDG_CONFIG_HOME": home.name})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("AUTOCODE_PROVIDER", None)
        project = tempfile.TemporaryDirectory()
        self.addCleanup(project.cleanup)
        self.workspace = Path(project.name)

    def configure(self, switch, **overrides):
        with (
            patch.dict(os.environ, {suggestion.ENV: switch}),
            patch.object(oc, "local_settings", return_value={"engine": "opencode"}),
        ):
            return autocode_configure.configure(
                test_planning.PlanningTests.configure_args(None, **overrides),
                {"workspace": str(self.workspace), "iteration": 0},
                planning=planning,
                milestones=milestones,
                autopilot=autopilot,
                opencode=runner.opencode,
            )

    def _ledger(self, builder_model, better_model, effort):
        def attempt(model, index, *, back):
            return {
                "stage": "builder",
                "job": "Builder",
                "model": model,
                "reasoning_effort": effort,
                "execution_identity": {"provider": "opencode"},
                "task_id": f"task-{index}",
                "started_at": f"2026-11-14T10:{index % 60:02d}:00+00:00",
                "reported_cost_usd": 0.1,
            }, back

        rows = []
        for index in range(30):
            for model, fails in ((builder_model, index % 2 == 0), (better_model, False)):
                first, back = attempt(model, index, back=fails)
                attempts = [first] + (
                    [{**first, "started_at": first["started_at"].replace("10:", "11:")}] if back else []
                )
                rows.append(
                    {
                        "run": f"{model}-{index}",
                        "started_at": "2026-11-14T10:00:00+00:00",
                        "updated_at": "2026-11-14T11:00:00+00:00",
                        "evidence_provenance": "live",
                        "outcome": {"state": "completed", "plan_size": "small"},
                        "accounting": {"attempts": attempts},
                    }
                )
        ledger = self.workspace / ".autocode" / "usage.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text("".join(json.dumps(row) + "\n" for row in rows))

    def test_off_records_nothing_and_shadow_changes_no_route(self):
        off = self.configure("off")
        self.assertNotIn("route_suggestion", off)
        builder = off["roles"]["builder"]["model"]
        self._ledger(builder, "openai/gpt-0-cheap", off["roles"]["builder"]["reasoning_effort"])

        shadow = self.configure("shadow")

        record = shadow.pop("route_suggestion")
        self.assertEqual("shadow", record["mode"])
        self.assertEqual(builder, record["stages"]["builder"]["ran"]["model"])
        self.assertEqual("openai/gpt-0-cheap", record["stages"]["builder"]["suggested"]["model"])
        self.assertEqual(json.dumps(off, sort_keys=True, default=str), json.dumps(shadow, sort_keys=True, default=str))

    def test_an_unreadable_ledger_is_recorded_and_never_stops_the_launch(self):
        with patch("autocode_usage_report.collect_rows", side_effect=OSError("disk gone")):
            settings = self.configure("shadow")

        self.assertIn("disk gone", settings["route_suggestion"]["unavailable"])


class LedgerTests(unittest.TestCase):
    def test_the_ledger_row_carries_the_runs_shadow_record_and_nothing_when_off(self):
        import autocode_usage as usage
        import autocode_usage_report as usage_report

        record = {"schema": 1, "mode": "shadow", "backend": "claude", "stages": {}}
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            for name, settings in (("with", {"route_suggestion": record}), ("without", {})):
                run_dir = project / ".autocode" / "runs" / name
                run_dir.mkdir(parents=True)
                state = {
                    "run_dir": str(run_dir),
                    "status": "TASK_COMPLETE",
                    "settings": settings,
                    "stages": [{"stage": "builder", "engine": "codex", "model": "gpt-5.4", "duration_seconds": 1.0}],
                }
                usage.record(run_dir / "state.json", state)
            rows = {row["run"]: row for row in usage_report.read_ledger(project / ".autocode" / "usage.jsonl")}

        self.assertEqual(record, rows["with"]["route_suggestion"])
        self.assertNotIn("route_suggestion", rows["without"])


if __name__ == "__main__":
    unittest.main()
