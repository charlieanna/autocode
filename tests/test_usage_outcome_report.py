"""Tests for unified rejection classes, usage.jsonl outcome telemetry, and `autocode usage report` (#3346)."""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_process as process
import autocode_recovery_history as recovery_history
import autocode_rejection_classes as rejection_classes
import autocode_usage as usage
import autocode_usage_outcomes as usage_outcomes
import autocode_usage_report as usage_report
from autocode_taskrun import TaskRun

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
AUTOCODE = TOOLS / "autocode.py"

# The reason every stop the runner makes is archived under, whatever stopped it.
ARCHIVED_STOP = "Timed-out non-terminal provider request automatically archived; partial work retained"


def _make_live_row(
    run_id: str,
    *,
    accepted_s: float = 0.0,
    accepted_n: int = 0,
    rejections: list[dict] | None = None,
    repair_s: float = 0.0,
    repair_n: int = 0,
    repair_failures: list[dict] | None = None,
    started_at: str = "2026-11-11T10:00:00+00:00",
    provenance: str = "live",
    plan_size: str = "small",
    model_calls: int = 6,
    wall_seconds: float = 600.0,
    spent_usd: float = 0.42,
    first_question: tuple[float, bool] | None = (45.0, False),
    first_plan: tuple[float, bool] | None = None,
    first_builder: tuple[float, bool] | None = None,
) -> dict:
    rej_list = list(rejections or ()) + list(repair_failures or ())
    rej_s = round(sum(float(item.get("seconds") or 0.0) for item in (rejections or ())), 3)
    rej_n = len(rejections or ())
    route = {"provider": "codex", "model": "gpt-5.4", "level": "medium"}

    def _interaction_entry(sample: tuple[float, bool] | None) -> dict:
        if sample is None:
            return {"seconds": None, "human_wait": None, "route": None}
        return {"seconds": sample[0], "human_wait": sample[1], "route": route}

    return {
        "run": run_id,
        "status": "TASK_COMPLETE",
        "workflow": "build",
        "updated_at": started_at,
        "ledger_schema": usage_outcomes.LEDGER_SCHEMA,
        "evidence_provenance": provenance,
        "started_at": started_at,
        "tokens": {
            "input_tokens": 1000,
            "cached_input_tokens": 200,
            "output_tokens": 300,
            "reasoning_tokens": 0,
            "cache_write_tokens": 0,
            "complete": True,
        },
        "cost_usd": {"reported": spent_usd, "estimated": spent_usd, "complete": True},
        "outcome": {
            "state": "completed",
            "run_status": "TASK_COMPLETE",
            "pause_reason_code": None,
            "plan_size": plan_size,
            "model_calls": model_calls,
            "wall_seconds": wall_seconds,
            "spent_usd": {"value": spent_usd, "complete": True},
        },
        "outcomes": {
            "complete": True,
            "totals": {
                "accepted": {"attempts": accepted_n, "seconds": accepted_s, "complete": True},
                "rejected": {"attempts": rej_n, "seconds": rej_s, "complete": True},
                "report_repair": {"attempts": repair_n, "seconds": repair_s, "complete": True},
            },
            "by_job": {
                "Builder": {
                    "accepted": {"attempts": accepted_n, "seconds": accepted_s, "complete": True},
                    "rejected": {"attempts": rej_n, "seconds": rej_s, "complete": True},
                    "report_repair": {"attempts": repair_n, "seconds": repair_s, "complete": True},
                }
            },
        },
        "rejections": rej_list,
        "first_interaction": {
            "launched_at": started_at,
            "first_question": _interaction_entry(first_question),
            "first_plan": _interaction_entry(first_plan),
            "first_builder": _interaction_entry(first_builder),
        },
        "stage_prompts": [
            {
                "stage": "builder",
                "job": "Builder",
                "report_repair": False,
                "prompt_bytes": 4096,
                "first_request_cache_read_tokens": 512,
                "first_request_cache_write_tokens": 128,
                "route": route,
            }
        ],
    }


class RejectionClassifierTests(unittest.TestCase):
    def test_taxonomy_and_answer_classes(self):
        self.assertEqual(
            (
                "provider_timeout",
                "stage_wall",
                "not_a_report",
                "schema",
                "contract_rule",
                "evidence_not_authenticated",
                "other",
            ),
            rejection_classes.CLASSES,
        )
        self.assertEqual(
            frozenset({"not_a_report", "schema", "contract_rule", "evidence_not_authenticated"}),
            rejection_classes.ANSWER_CLASSES,
        )

    def test_classifies_stage_wall_before_provider_timeout(self):
        self.assertEqual(
            "stage_wall",
            rejection_classes.classify(
                "Timed-out non-terminal provider request after 1200s",
                timeout_kind="stage",
                timeout_reason="Stage exceeded its 1200-second hard runtime limit",
                timed_out=True,
            ),
        )
        self.assertEqual(
            "stage_wall",
            rejection_classes.classify(
                "Stage exceeded its 900-second hard runtime limit",
                timed_out=True,
            ),
        )

    def test_classifies_provider_timeouts(self):
        for kind in ("idle", "first_event", "tool"):
            self.assertEqual(
                "provider_timeout",
                rejection_classes.classify("stream ended early", timeout_kind=kind, timed_out=True),
            )
        self.assertEqual(
            "provider_timeout",
            rejection_classes.classify("Timed-out non-terminal provider request after 600s"),
        )
        self.assertEqual(
            "provider_timeout",
            rejection_classes.classify("No provider activity for 300s", timed_out=True),
        )
        self.assertEqual(
            "provider_timeout",
            rejection_classes.classify("Saved repair timeout exceeded (max 900s)"),
        )

    def test_a_stop_is_classified_by_the_kind_the_runner_recorded(self):
        """A launch the runner stopped is not always a provider that failed to answer.

        Every stop is saved as timed out with the archive's "Timed-out non-terminal" reason, so a person's
        Stop now and the Requirements reply budget were both counted as provider timeouts.
        """
        self.assertEqual(
            "other",
            rejection_classes.classify(
                ARCHIVED_STOP, timeout_kind="person", timeout_reason=process.PERSON_STOP_REASON, timed_out=True
            ),
        )
        self.assertEqual(
            "stage_wall",
            rejection_classes.classify(
                ARCHIVED_STOP,
                timeout_kind="progress",
                timeout_reason="Requirements pass reached its 12-tool-call reply budget; finish from collected evidence",
                timed_out=True,
            ),
        )
        for kind in ("idle", "tool"):
            self.assertEqual(
                "provider_timeout", rejection_classes.classify(ARCHIVED_STOP, timeout_kind=kind, timed_out=True)
            )
        # The recorded kind is what the runner saw; the text beside it does not overrule it.
        self.assertEqual(
            "provider_timeout",
            rejection_classes.classify(
                "Investigator: Stage exceeded its 120-second hard runtime limit", timeout_kind="idle", timed_out=True
            ),
        )
        # A launch saved without a kind is still read from its text.
        self.assertEqual("provider_timeout", rejection_classes.classify(ARCHIVED_STOP, timed_out=True))
        self.assertEqual("provider_timeout", rejection_classes.classify(ARCHIVED_STOP, timeout_kind="", timed_out=True))

    def test_every_stop_kind_the_runner_records_has_a_class(self):
        self.assertEqual(set(), set(process.STOP_KINDS) - set(rejection_classes.KIND_CLASSES))
        self.assertEqual(set(), set(rejection_classes.KIND_CLASSES.values()) - set(rejection_classes.CLASSES))

    def test_classifies_not_a_report_schema_contract_and_evidence(self):
        self.assertEqual(
            "not_a_report",
            rejection_classes.classify(
                "Completed intake output was rejected (Model output is not a JSON report); attempt archived"
            ),
        )
        self.assertEqual(
            "not_a_report",
            rejection_classes.classify("Report edits did not produce valid JSON: not in a form autocode can apply"),
        )
        self.assertEqual("not_a_report", rejection_classes.classify("Leaked transport markup in final response"))
        self.assertEqual("not_a_report", rejection_classes.classify("Response failed at output token limit"))

        schema_msg = (
            "Completed draft_plan output was rejected ($: 'milestones' is a required property); attempt archived"
        )
        self.assertEqual("schema", rejection_classes.classify(schema_msg))
        self.assertEqual("schema", rejection_classes.classify("$.verification.commands[0]: must be an object"))
        self.assertTrue(recovery_history._is_schema_rejection(schema_msg))
        self.assertFalse(recovery_history._is_schema_rejection("Missing required source_quote"))

        self.assertEqual(
            "contract_rule",
            rejection_classes.classify(
                "Completed review_plan output was rejected (Missing required source_quote); attempt archived"
            ),
        )
        self.assertEqual(
            "contract_rule",
            rejection_classes.classify("Checklist must name every modified file"),
        )
        self.assertEqual(
            "contract_rule",
            rejection_classes.classify("Reproduced bug claim has no failing test evidence"),
        )

        self.assertEqual(
            "evidence_not_authenticated",
            rejection_classes.classify(
                "Completed tester output was rejected (Check claim is not supported by an exact executed command); attempt archived"
            ),
        )
        self.assertEqual(
            "evidence_not_authenticated",
            rejection_classes.classify("Tester checks require capture receipts before PASS"),
        )
        self.assertEqual(
            "evidence_not_authenticated",
            rejection_classes.classify("No independently executed tester tool event found"),
        )

    def test_unwrap_and_other_fallback(self):
        self.assertEqual(
            "Missing required summary",
            rejection_classes.unwrap_rejection(
                "Completed builder output was rejected (Missing required summary); attempt archived in history"
            ),
        )
        self.assertEqual("other", rejection_classes.classify(None))
        self.assertEqual("other", rejection_classes.classify(""))
        self.assertEqual("other", rejection_classes.classify("Cancelled by operator"))


class UsageOutcomesProjectionTests(unittest.TestCase):
    def setUp(self):
        usage._written.clear()

    def test_requirements_first_pass_keeps_the_first_outcome_and_route_after_repairs_and_later_rounds(self):
        first = {
            "stage": "requirements_gather",
            "provider": "claude",
            "model": "claude-sonnet-4-6",
            "reasoning_effort": "high",
            "duration_seconds": 12.0,
        }
        repair = {
            **first,
            "stage": "requirements_gather_report_repair",
            "original_stage": "requirements_gather",
            "report_only": True,
            "model": "repair-model",
            "duration_seconds": 3.0,
        }
        for failure in (
            {},
            {"stage": "requirements", "rejected": True},
            {"rejected": True},
            {"timed_out": True},
            {"exit_code": 1},
            {"interrupted": True},
            {"abandoned": True},
        ):
            with self.subTest(failure=failure):
                records = [
                    {"stage": "model_preflight", "engine": "runner", "runner_owned": True},
                    {**first, **failure},
                    repair,
                    {**first, "model": "later-round", "rejected": not bool(failure)},
                ]
                fields = usage_outcomes.ledger_fields(
                    {"stages": records},
                    records=records,
                    priced_rows=[],
                    has_running_stage=False,
                    has_pending_workers=False,
                    model_of=usage._model,
                )
                self.assertEqual(
                    {
                        "accepted": not bool(failure),
                        "route": {"provider": "claude", "model": "claude-sonnet-4-6", "level": "high"},
                    },
                    fields["requirements_first_pass"],
                )

    def test_requirements_first_pass_is_unknown_without_an_original_settled_attempt(self):
        repair = {"stage": "requirements_gather_report_repair", "report_only": True, "duration_seconds": 4.0}
        later = {"stage": "requirements_gather", "duration_seconds": 5.0}
        for records in ([], [repair, later]):
            with self.subTest(records=records):
                fields = usage_outcomes.ledger_fields(
                    {"stages": records, "active_stage": {"stage": "requirements_gather"}},
                    records=records,
                    priced_rows=[],
                    has_running_stage=True,
                    has_pending_workers=False,
                    model_of=usage._model,
                )
                self.assertIsNone(fields["requirements_first_pass"])

    def test_a_finished_requirements_launch_is_not_accepted_before_admission(self):
        record = {
            "stage": "requirements_gather",
            "finished_at": "2026-11-14T12:00:17+00:00",
            "duration_seconds": 12.0,
            "exit_code": 0,
        }
        preflight = {**record, "preflight_complete": False}
        for state, observed in (
            ({"stages": [], "active_stage": record}, record),
            ({"stages": [preflight]}, preflight),
        ):
            with self.subTest(state=state):
                fields = usage_outcomes.ledger_fields(
                    state,
                    records=[observed],
                    priced_rows=[],
                    has_running_stage=False,
                    has_pending_workers=False,
                    model_of=usage._model,
                )
                self.assertIsNone(fields["requirements_first_pass"])

    def test_record_projects_additive_privacy_safe_fields_and_keeps_summary_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "repo"
            run_dir = project / ".autocode" / "runs" / "20261114-120000"
            run_dir.mkdir(parents=True)
            prompt_file = run_dir / "draft_plan.prompt.txt"
            prompt_text = "TOP_SECRET_PROMPT_TEXT should never land in usage.jsonl"
            prompt_file.write_text(prompt_text, encoding="utf-8")

            state = {
                "status": "TASK_COMPLETE",
                "workflow": {"kind": "build"},
                "settings": {"engine": "codex", "evidence_provenance": {"kind": "fake"}},
                "planning": {"adaptive": {"size": "small"}},
                "interaction_timing": {
                    "launched_at": "2026-11-14T12:00:00+00:00",
                    "first_question_at": "2026-11-14T12:00:30+00:00",
                    "first_plan_at": "2026-11-14T12:05:00+00:00",
                    "first_builder_at": None,
                },
                "resolver": {
                    "human_escalations": {
                        "esc-1": {
                            "status": "consumed",
                            "issued_at": "2026-11-14T12:00:31+00:00",
                            "response": {"actor": "user_cli", "at": "2026-11-14T12:03:00+00:00"},
                        }
                    }
                },
                "completed_at": "2026-11-14T12:08:00+00:00",
                "stages": [
                    {"stage": "model_preflight", "runner_owned": True, "engine": "runner", "duration_seconds": 1.0},
                    {
                        "stage": "requirements_gather",
                        "engine": "codex",
                        "model": "gpt-5.4",
                        "reasoning_effort": "low",
                        "duration_seconds": 12.5,
                        "started_at": "2026-11-14T12:00:05+00:00",
                        "finished_at": "2026-11-14T12:00:17+00:00",
                        "exit_code": 0,
                        "metrics": {
                            "provider_tokens": {"input_tokens": 500, "output_tokens": 100},
                            "request_context": {
                                "accounting": {
                                    "requests": [
                                        {
                                            "identity": ["codex", "gpt-5.4", "req-1"],
                                            "tokens": {"cached_input_tokens": 300, "cache_write_tokens": 50},
                                        }
                                    ]
                                }
                            },
                        },
                    },
                    {
                        "stage": "draft_plan",
                        "engine": "codex",
                        "model": "gpt-5.4",
                        "reasoning_effort": "high",
                        "prompt": str(prompt_file),
                        "duration_seconds": 25.0,
                        "started_at": "2026-11-14T12:03:10+00:00",
                        "finished_at": "2026-11-14T12:03:35+00:00",
                        "rejected": True,
                        "rejection_reason": (
                            "Completed draft_plan output was rejected "
                            "(SECRET_RAW_PROSE $: 'summary' is a required property); attempt archived"
                        ),
                        "metrics": {
                            "provider_tokens": {"input_tokens": 800, "output_tokens": 200},
                            "request_context": {
                                "accounting": {
                                    "requests": [
                                        {
                                            "identity": ["codex", "gpt-5.4", "req-2"],
                                            "tokens": {"cached_input_tokens": 1024, "cache_write_tokens": 256},
                                        }
                                    ]
                                }
                            },
                        },
                    },
                    {
                        "stage": "draft_plan_report_repair",
                        "original_stage": "draft_plan",
                        "report_only": True,
                        "engine": "codex",
                        "model": "gpt-5.4",
                        "reasoning_effort": "high",
                        "prompt_bytes": 1234,
                        "duration_seconds": 5.0,
                        "started_at": "2026-11-14T12:03:40+00:00",
                        "finished_at": "2026-11-14T12:03:45+00:00",
                        "exit_code": 0,
                        "metrics": {"provider_tokens": {"input_tokens": 200, "output_tokens": 50}},
                    },
                    {"stage": "resolve", "runner_owned": True, "engine": "runner", "duration_seconds": 0.0},
                ],
            }

            summary_before = usage.summary(state)
            self.assertEqual(
                {
                    "stages",
                    "active_stage",
                    "tokens",
                    "cost_usd",
                    "unknown_stages",
                    "partial_stages",
                    "by_role",
                    "by_job",
                    "accounting",
                    "legacy_scope",
                },
                set(summary_before),
            )

            state_path = run_dir / "state.json"
            usage.record(state_path, state)
            ledger_path = project / ".autocode" / "usage.jsonl"
            raw_text = ledger_path.read_text(encoding="utf-8")
            self.assertNotIn("SECRET_RAW_PROSE", raw_text)
            self.assertNotIn("TOP_SECRET_PROMPT_TEXT", raw_text)

            rows = usage_report.read_ledger(ledger_path)
            self.assertEqual(1, len(rows))
            row = rows[0]
            self.assertEqual(2, row["ledger_schema"])
            self.assertEqual("fake", row["evidence_provenance"])
            self.assertEqual(1, row["outcomes"]["totals"]["accepted"]["attempts"])
            self.assertEqual(12.5, row["outcomes"]["totals"]["accepted"]["seconds"])
            self.assertEqual(1, row["outcomes"]["totals"]["rejected"]["attempts"])
            self.assertEqual(25.0, row["outcomes"]["totals"]["rejected"]["seconds"])
            self.assertEqual(1, row["outcomes"]["totals"]["report_repair"]["attempts"])
            self.assertEqual(5.0, row["outcomes"]["totals"]["report_repair"]["seconds"])
            self.assertEqual(1, len(row["rejections"]))
            self.assertEqual("schema", row["rejections"][0]["class"])
            self.assertEqual({"provider": "codex", "model": "gpt-5.4", "level": "high"}, row["rejections"][0]["route"])

            self.assertFalse(row["first_interaction"]["first_question"]["human_wait"])
            self.assertTrue(row["first_interaction"]["first_plan"]["human_wait"])
            self.assertEqual(3, row["outcome"]["model_calls"])
            self.assertEqual("small", row["outcome"]["plan_size"])
            self.assertEqual("completed", row["outcome"]["state"])
            self.assertEqual(480.0, row["outcome"]["wall_seconds"])
            self.assertEqual(len(prompt_text.encode("utf-8")), row["stage_prompts"][1]["prompt_bytes"])
            self.assertEqual(1024, row["stage_prompts"][1]["first_request_cache_read_tokens"])
            self.assertEqual(256, row["stage_prompts"][1]["first_request_cache_write_tokens"])

            # Idempotent second call does not modify usage.jsonl; a changed stage updates it in place.
            usage.record(state_path, state)
            self.assertEqual(raw_text, ledger_path.read_text(encoding="utf-8"))
            state["stages"].append(
                {
                    "stage": "builder",
                    "engine": "codex",
                    "model": "gpt-5.4",
                    "reasoning_effort": "medium",
                    "duration_seconds": 40.0,
                    "exit_code": 0,
                }
            )
            usage.record(state_path, state)
            updated_rows = usage_report.read_ledger(ledger_path)
            self.assertEqual(1, len(updated_rows))
            self.assertEqual(4, updated_rows[0]["outcome"]["model_calls"])

    def test_a_step_the_person_stopped_is_not_reported_as_a_provider_timeout(self):
        """The report's route table blames a route for a provider timeout; a person's stop is not one."""

        def stopped(stage, kind, reason, seconds):
            return {
                "stage": stage,
                "engine": "opencode",
                "provider": "kilocode",
                "model": "zai-coding-plan/glm-5.3",
                "reasoning_effort": "high",
                "duration_seconds": seconds,
                "started_at": "2026-11-13T02:36:14+00:00",
                "finished_at": "2026-11-13T02:37:44+00:00",
                "exit_code": -15,
                "timed_out": True,
                "timeout_kind": kind,
                "timeout_reason": reason,
                "abandoned": True,
                "rejected": True,
                "rejection_reason": ARCHIVED_STOP,
            }

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "repo"
            run_dir = project / ".autocode" / "runs" / "20261113-023600"
            run_dir.mkdir(parents=True)
            state = {
                "status": "PAUSED_INTERRUPTED",
                "workflow": {"kind": "build"},
                "settings": {"engine": "opencode", "evidence_provenance": {"kind": "live"}},
                "stages": [
                    stopped("builder", "person", process.PERSON_STOP_REASON, 90.0),
                    stopped(
                        "tester",
                        "idle",
                        "No new provider activity within the inactivity limit (300 seconds, runner default)",
                        2400.0,
                    ),
                ],
            }
            usage.record(run_dir / "state.json", state)
            rejections = usage_report.read_ledger(project / ".autocode" / "usage.jsonl")[0]["rejections"]
            self.assertEqual(
                [("builder", "other", 90.0), ("tester", "provider_timeout", 2400.0)],
                [(item["stage"], item["class"], item["seconds"]) for item in rejections],
            )
            cohort = usage_report.build_report(project)["by_provenance"]["live"]["rejections"]
            by_class = {item["class"]: (item["launches"], item["seconds"]) for item in cohort["by_class"]}
            self.assertEqual((1, 2400.0), by_class["provider_timeout"])
            self.assertEqual((1, 90.0), by_class["other"])


class OutcomeReportAggregationTests(unittest.TestCase):
    def test_requirements_acceptance_and_repair_time_use_recorded_denominators_and_separate_provenances(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            ledger = project / ".autocode" / "usage.jsonl"
            ledger.parent.mkdir()
            route = {"provider": "claude", "model": "claude-sonnet-4-6", "level": "high"}
            other_route = {"provider": "codex", "model": "gpt-5.4", "level": "medium"}
            rows = []
            for run, accepted, seconds, complete in (
                ("accepted", True, 0.0, True),
                ("repaired", False, 7.5, True),
                ("legacy", None, 10.0, False),
                ("fake", True, 100.0, True),
            ):
                row = _make_live_row(run, provenance="fake" if run == "fake" else "live")
                if accepted is not None:
                    row["requirements_first_pass"] = {
                        "accepted": accepted,
                        "route": other_route if run == "accepted" else route,
                    }
                row["outcomes"]["by_job"]["Requirements"] = {
                    "report_repair": {"attempts": 2 if seconds else 0, "seconds": seconds, "complete": complete}
                }
                rows.append(row)
            rows.append({"run": "unmeasured", "evidence_provenance": "live"})
            ledger.write_text("".join(json.dumps(row) + "\n" for row in rows))

            payload = usage_report.build_report(project)
            requirements = payload["by_provenance"]["live"]["requirements"]
            self.assertEqual(
                {
                    "accepted_runs": 1,
                    "rejected_runs": 1,
                    "not_recorded_runs": 2,
                    "acceptance_rate": 0.5,
                    "by_route": [
                        {"route": route, "accepted_runs": 0, "rejected_runs": 1, "acceptance_rate": 0.0},
                        {"route": other_route, "accepted_runs": 1, "rejected_runs": 0, "acceptance_rate": 1.0},
                    ],
                },
                requirements["first_pass"],
            )
            self.assertEqual(
                {"attempts": 4, "seconds": 17.5, "complete": False, "recorded_runs": 3, "not_recorded_runs": 1},
                requirements["report_repair"],
            )
            fake = payload["by_provenance"]["fake"]["requirements"]
            self.assertEqual(1.0, fake["first_pass"]["acceptance_rate"])
            self.assertEqual(100.0, fake["report_repair"]["seconds"])
            text = usage_report.format_report(payload)
            self.assertIn("Requirements first pass: 1/2 accepted (50.0%); not recorded=2", text)
            self.assertIn("claude:claude-sonnet-4-6@high: 0/1 accepted (0.0%)", text)
            self.assertIn("codex:gpt-5.4@medium: 1/1 accepted (100.0%)", text)
            self.assertIn("Requirements report repair: 4 attempts, >=17.5s (incomplete); not recorded=1", text)

    def test_cost_coverage_keeps_reported_estimated_and_unknown_amounts_apart_per_provenance(self):
        def model_attempt(*, reported=None, historical=None, observed=True):
            return {
                "stage": "builder",
                "job": "Builder",
                "model": "claude-opus-5-5",
                "tokens": {"input_tokens": 1000 if observed else None, "output_tokens": 100 if observed else None},
                "reported_cost_usd": reported,
                "historical_estimated_cost_usd": historical,
                "finished_at": "2026-11-27T11:00:00+00:00",
                "duration_seconds": 30.0,
                "exit_code": 0,
                "usage_truncated": False,
                "provider_request_ids": ["msg_1"] if observed else None,
                "resolved_models": ["claude-opus-5-5"] if observed else None,
            }

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            ledger = project / ".autocode" / "usage.jsonl"
            ledger.parent.mkdir()
            live = _make_live_row("live-run")
            live["accounting"] = {
                "attempts": [model_attempt(reported=1.25), model_attempt(historical=0.5), model_attempt(observed=False)]
            }
            fake = _make_live_row("fake-run", provenance="fake")
            fake["accounting"] = {"attempts": [model_attempt(reported=9.0)]}
            ledger.write_text("".join(json.dumps(row) + "\n" for row in (live, fake)))

            payload = usage_report.build_report(project)
            live_coverage = payload["by_provenance"]["live"]["cost_coverage"]
            self.assertEqual(
                {
                    "reported_usd": 1.25,
                    "reported_attempts": 1,
                    "estimated_usd": 0.5,
                    "estimated_attempts": 1,
                    "unknown_attempts": 1,
                    "known_usd": 1.75,
                    "total_usd": None,
                    "complete": False,
                },
                live_coverage["usd"],
            )
            self.assertEqual("partial", live_coverage["by_run"][0]["coverage"])
            fake_coverage = payload["by_provenance"]["fake"]["cost_coverage"]
            self.assertEqual((9.0, 9.0), (fake_coverage["usd"]["reported_usd"], fake_coverage["usd"]["total_usd"]))

            text = usage_report.format_report(payload)
            self.assertIn(
                "Cost coverage (1 runs): complete=0 partial=1 unknown=0 no model stages=0 not recorded=0", text
            )
            self.assertIn(
                "Model stages: 3; provider-reported $1.25 (1); API-equivalent estimate $0.50 (1); "
                "unknown, not zero: 1; total >=$1.75 (incomplete)",
                text,
            )
            self.assertIn("Completion events: complete=2 truncated=0 missing=1 running=0", text)
            self.assertIn("Provider request ids: recorded=2 none named=1 not recorded=0; resolved model=2", text)

    def test_requirements_without_observations_do_not_report_perfect_acceptance_or_zero_repair_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            ledger = project / ".autocode" / "usage.jsonl"
            ledger.parent.mkdir()
            ledger.write_text(json.dumps({"run": "legacy", "evidence_provenance": "live"}) + "\n")
            payload = usage_report.build_report(project)
            requirements = payload["by_provenance"]["live"]["requirements"]
            self.assertIsNone(requirements["first_pass"]["acceptance_rate"])
            self.assertEqual([], requirements["first_pass"]["by_route"])
            self.assertFalse(requirements["report_repair"]["complete"])
            self.assertEqual(0, requirements["report_repair"]["recorded_runs"])
            self.assertIn(
                "Requirements report repair: 0 attempts, not recorded; not recorded=1",
                usage_report.format_report(payload),
            )

    def test_reproduces_2026_11_11_baseline_separates_provenances_and_counts_legacy_not_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "project"
            ledger = workspace / ".autocode" / "usage.jsonl"
            ledger.parent.mkdir(parents=True)

            # 58 live runs matching the 2026-11-11 efficiency note (99.03h accepted, 49.17h rejected,
            # 6.80h report_repair => 155.00h total; 36.1% non-productive, 18.6% rejected-answer).
            rows: list[dict] = [
                _make_live_row(
                    "run-00",
                    accepted_s=356508.0 - 57 * 0.0,  # full accepted total in first run, split count cleanly
                    accepted_n=58,
                    rejections=[
                        {
                            "stage": "draft_plan",
                            "job": "Planner",
                            "class": "provider_timeout",
                            "seconds": 72000.0,
                            "report_repair": False,
                            "route": {"provider": "codex", "model": "gpt-5.4", "level": "high"},
                        },
                        {
                            "stage": "builder",
                            "job": "Builder",
                            "class": "stage_wall",
                            "seconds": 25920.0,
                            "report_repair": False,
                            "route": {"provider": "codex", "model": "gpt-5.4", "level": "medium"},
                        },
                        {
                            "stage": "requirements",
                            "job": "Requirements",
                            "class": "not_a_report",
                            "seconds": 29232.0,
                            "report_repair": False,
                            "route": {"provider": "codex", "model": "gpt-5.4", "level": "low"},
                        },
                        {
                            "stage": "draft_plan",
                            "job": "Planner",
                            "class": "schema",
                            "seconds": 27324.0,
                            "report_repair": False,
                            "route": {"provider": "codex", "model": "gpt-5.4", "level": "high"},
                        },
                        {
                            "stage": "review_plan",
                            "job": "Plan Reviewer",
                            "class": "contract_rule",
                            "seconds": 4536.0,
                            "report_repair": False,
                            "route": {"provider": "codex", "model": "gpt-5.4", "level": "high"},
                        },
                        {
                            "stage": "tester",
                            "job": "Tester",
                            "class": "evidence_not_authenticated",
                            "seconds": 18000.0,
                            "report_repair": False,
                            "route": {"provider": "codex", "model": "gpt-5.4", "level": "medium"},
                        },
                    ],
                    repair_s=24480.0,
                    repair_n=40,
                    first_question=(120.0, False),
                    first_plan=(700.0, True),
                    model_calls=6,
                )
            ]
            for idx in range(1, 58):
                rows.append(
                    _make_live_row(
                        f"run-{idx:02d}",
                        first_question=(180.0, idx % 2 == 0),
                        model_calls=6,
                    )
                )
            # Legacy row without Step 1 fields -> must increment not_recorded, never zero!
            rows.append(
                {
                    "run": "legacy-run",
                    "status": "COMPLETE",
                    "workflow": "build",
                    "updated_at": "2026-11-11T08:00:00+00:00",
                    "tokens": {"input_tokens": 100, "output_tokens": 50, "complete": True},
                    "cost_usd": {"reported": 0.10, "estimated": 0.10, "complete": True},
                }
            )
            # Fake run -> strictly separated from live cohort!
            rows.append(
                _make_live_row(
                    "fake-run",
                    accepted_s=100.0,
                    accepted_n=1,
                    provenance="fake",
                    first_question=(500.0, False),
                    model_calls=20,
                )
            )
            ledger.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

            # Poison state.json on disk to prove build_report never reads state.json.
            bad_state = workspace / ".autocode" / "runs" / "run-00" / "state.json"
            bad_state.parent.mkdir(parents=True)
            bad_state.write_text("{NOT JSON", encoding="utf-8")

            report = usage_report.build_report(workspace)
            self.assertEqual(["live", "unknown", "fake"], report["provenances_present"])
            self.assertEqual(60, report["total_runs"])

            live = report["by_provenance"]["live"]
            self.assertEqual(58, live["runs"])
            so = live["stage_outcomes"]
            self.assertEqual(155.0, so["total_hours"])
            self.assertAlmostEqual(0.6389, so["buckets"]["accepted"]["share"], places=4)
            self.assertAlmostEqual(0.3172, so["buckets"]["rejected"]["share"], places=4)
            self.assertAlmostEqual(0.0439, so["buckets"]["report_repair"]["share"], places=4)
            self.assertAlmostEqual(0.3611, so["non_productive_share"], places=4)
            self.assertAlmostEqual(0.1856, live["rejections"]["rejected_answer_share"], places=4)

            tm = live["targets"]["metrics"]
            self.assertFalse(tm["non_productive_stage_time_share"]["within_target"])
            self.assertTrue(tm["rejected_answer_stage_time_share"]["within_target"])
            self.assertTrue(tm["first_question_seconds"]["within_target"])
            self.assertTrue(tm["small_completed_task_model_calls"]["within_target"])
            self.assertGreater(live["first_interaction"]["events"]["first_question"]["human_wait_count"], 0)
            self.assertIsNotNone(
                live["first_interaction"]["events"]["first_question"]["without_human_wait"]["median_seconds"]
            )

            unknown = report["by_provenance"]["unknown"]
            self.assertEqual(1, unknown["not_recorded"]["outcomes_runs"])
            self.assertEqual(1, unknown["not_recorded"]["rejections_runs"])
            self.assertEqual(1, unknown["not_recorded"]["first_interaction_runs"])
            self.assertEqual(1, unknown["not_recorded"]["outcome_runs"])
            self.assertEqual(1, unknown["not_recorded"]["stage_prompts_runs"])

            fake = report["by_provenance"]["fake"]
            self.assertEqual(1, fake["runs"])
            self.assertEqual(100.0, fake["stage_outcomes"]["total_seconds"])

            rendered = usage_report.format_report(report)
            self.assertIn("=== Provenance: live (58 runs) ===", rendered)
            self.assertIn("=== Provenance: fake (1 runs) ===", rendered)
            self.assertIn("Rejected time by class:", rendered)

    def test_since_and_all_projects_discovery_without_reading_state_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proj1 = root / "proj1"
            proj2 = root / "proj2"
            (proj1 / ".autocode").mkdir(parents=True)
            (proj2 / ".autocode").mkdir(parents=True)

            row_old = _make_live_row("r-old", accepted_s=30.0, accepted_n=1, started_at="2026-10-01T10:00:00+00:00")
            row_new_1 = _make_live_row("r-new-1", accepted_s=60.0, accepted_n=1, started_at="2026-11-12T10:00:00+00:00")
            row_new_2 = _make_live_row("r-new-2", accepted_s=90.0, accepted_n=1, started_at="2026-11-13T10:00:00+00:00")
            (proj1 / ".autocode" / "usage.jsonl").write_text(
                json.dumps(row_old) + "\n" + json.dumps(row_new_1) + "\n", encoding="utf-8"
            )
            (proj2 / ".autocode" / "usage.jsonl").write_text(json.dumps(row_new_2) + "\n", encoding="utf-8")

            reg_home = root / "autocode-home"
            reg_home.mkdir()
            (reg_home / "registry.json").write_text(
                json.dumps({"workspaces": {"w1": {"path": str(proj1)}, "w2": {"path": str(proj2)}}}),
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"AUTOCODE_HOME": str(reg_home), "XDG_CONFIG_HOME": str(root / "cfg")}):
                # Re-point cached storage_root paths cleanly via env mock on registry calls
                payload = usage_report.build_report(proj1, all_projects=True, since=dt.date(2026, 11, 10))
            self.assertEqual("all_registered_projects", payload["scope"])
            self.assertEqual(2, payload["total_runs"])
            self.assertEqual(150.0, payload["by_provenance"]["live"]["stage_outcomes"]["total_seconds"])

    def test_stage_prompt_splits_overages_and_claude_first_request_cache_share(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            run_dir = project / ".autocode" / "runs" / "20261114-120000"
            run_dir.mkdir(parents=True)
            state = {
                "status": "TASK_COMPLETE",
                "workflow": {"kind": "build"},
                "settings": {"engine": "claude", "evidence_provenance": {"kind": "live"}},
                "planning": {"adaptive": {"size": "small"}},
                "stages": [
                    {
                        "stage": "builder",
                        "engine": "claude",
                        "model": "claude-sonnet-4-6",
                        "reasoning_effort": "medium",
                        "prompt_bytes": 16000,
                        "duration_seconds": 12.0,
                        "started_at": "2026-11-14T12:00:05+00:00",
                        "finished_at": "2026-11-14T12:00:17+00:00",
                        "exit_code": 0,
                        "context": {
                            "instruction_bytes": 9000,
                            "stable_handoff_bytes": 5000,
                            "changing_handoff_bytes": 2000,
                            "top_handoff_fields": [{"field": "requirements", "bytes": 4000}],
                            "budgeted_fields": [],
                        },
                        "metrics": {
                            "provider_tokens": {
                                "input_tokens": 5000,
                                "cached_input_tokens": 4500,
                                "output_tokens": 200,
                            },
                            "first_request_tokens": {
                                "input_tokens": 4000,
                                "cached_input_tokens": 3000,
                                "cache_write_tokens": 1000,
                                "fresh_input_tokens": 0,
                                "output_tokens": 100,
                            },
                        },
                    },
                    {
                        "stage": "review_completion",
                        "engine": "claude",
                        "model": "claude-sonnet-4-6",
                        "reasoning_effort": "high",
                        "prompt_bytes": 20000,
                        "duration_seconds": 15.0,
                        "started_at": "2026-11-14T12:00:20+00:00",
                        "finished_at": "2026-11-14T12:00:35+00:00",
                        "exit_code": 0,
                        "context": {
                            "instruction_bytes": 8000,
                            "stable_handoff_bytes": 4000,
                            "changing_handoff_bytes": 8000,
                            "top_handoff_fields": [{"field": "regression_proof", "bytes": 6000}],
                            "budgeted_fields": ["regression_proof", "validation"],
                        },
                        "metrics": {
                            "provider_tokens": {"input_tokens": 2000, "output_tokens": 100},
                        },
                    },
                    {
                        # Legacy stage record without split byte metrics must preserve None (never zero).
                        "stage": "tester",
                        "engine": "codex",
                        "model": "gpt-5.4",
                        "prompt_bytes": 5000,
                        "duration_seconds": 8.0,
                        "exit_code": 0,
                        "metrics": {"provider_tokens": {"input_tokens": 500, "output_tokens": 50}},
                    },
                ],
            }
            usage.record(run_dir / "state.json", state)
            ledger_rows = usage_report.read_ledger(project / ".autocode" / "usage.jsonl")
            self.assertEqual(1, len(ledger_rows))
            sp_entries = ledger_rows[0]["stage_prompts"]
            self.assertEqual(9000, sp_entries[0]["instruction_bytes"])
            self.assertEqual(5000, sp_entries[0]["stable_handoff_bytes"])
            self.assertEqual(2000, sp_entries[0]["changing_handoff_bytes"])
            self.assertEqual(3000, sp_entries[0]["first_request_cache_read_tokens"])
            self.assertEqual(1000, sp_entries[0]["first_request_cache_write_tokens"])
            self.assertEqual(["regression_proof", "validation"], sp_entries[1]["budgeted_fields"])
            self.assertIsNone(sp_entries[2]["instruction_bytes"])
            self.assertIsNone(sp_entries[2]["stable_handoff_bytes"])
            self.assertIsNone(sp_entries[2]["changing_handoff_bytes"])
            self.assertIsNone(sp_entries[2]["budgeted_fields"])

            report = usage_report.build_report(project)
            sp_summary = report["by_provenance"]["live"]["stage_prompts"]
            self.assertEqual({"regression_proof": 1, "validation": 1}, sp_summary["field_budget_overages"])
            by_job = {item["job"]: item for item in sp_summary["by_job"]}
            self.assertAlmostEqual(0.75, by_job["Builder"]["first_request_cache_hit_share"], places=4)
            self.assertEqual(5000.0, by_job["Builder"]["stable_handoff_bytes"]["median"])
            self.assertEqual(0, by_job["Tester"]["instruction_bytes"]["count"])

            text = usage_report.format_report(report)
            self.assertIn("Stage prompts (1 runs):", text)
            self.assertIn("Field budget overages: regression_proof=1, validation=1", text)


class UsageReportCliEndToEndTests(unittest.TestCase):
    def test_real_cli_run_writes_outcomes_and_report_reads_only_ledger(self):
        with tempfile.TemporaryDirectory(prefix="usage-report-cli-") as tmp:
            root = Path(tmp)
            workspace = root / "project"
            workspace.mkdir()
            git = ["git", "-C", str(workspace), "-c", "user.name=T", "-c", "user.email=t@example.test"]
            subprocess.run(["git", "init", "-q", str(workspace)], check=True)
            subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "base"], check=True)

            bindir = root / "bin"
            bindir.mkdir()
            base = bindir / "base_codex.py"
            shutil.copy2(TOOLS / "fake_codex.py", base)
            shutil.copy2(TOOLS / "goal_fixtures.py", bindir / "goal_fixtures.py")
            (bindir / "codex").write_text(f"""#!{sys.executable}
import json, subprocess, sys
from pathlib import Path
if sys.argv[1:] == ['login', 'status']:
    print('Logged in using ChatGPT (offline fixture)')
    raise SystemExit(0)
prompt = sys.stdin.read()
data = json.loads(prompt.split('CURRENT HANDOFF DATA\\n', 1)[1])
result = subprocess.run([sys.executable, {str(base)!r}, *sys.argv[1:]],
                        input=prompt, text=True, capture_output=True, check=True)
output = Path(sys.argv[sys.argv.index('-o') + 1])
report = json.loads(output.read_text())
report['assistant_reply'] = 'Should the greeting be a CLI or web endpoint?'
if data.get('report_repair'):
    assert 'summary' in data['error'], data['error']
    report['summary'] = 'Repaired fixture report'
output.write_text(json.dumps(report))
print(result.stdout, end='')
""")
            (bindir / "codex").chmod(0o755)

            env = {
                **os.environ,
                "PATH": f"{bindir}{os.pathsep}{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
                "AUTOCODE_HOME": str(root / "registry"),
                "XDG_CONFIG_HOME": str(root / "config"),
                "CODEX_HOME": str(root / "codex-home"),
                "AUTOCODE_FIXTURE_MODE": "no-human",
                "AUTOCODE_FIXTURE_REPORT_REPAIR_STAGE": "requirements_gather",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            run_proc = subprocess.run(
                [
                    sys.executable,
                    str(AUTOCODE),
                    "Build greeting",
                    "--no-chat",
                    "--evidence-provenance",
                    "fake",
                    "--engine",
                    "codex",
                    "--joint-planning",
                    "--spec-first",
                    "--in-place",
                    "--max-cost-usd",
                    "0",
                ],
                cwd=workspace,
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertEqual(2, run_proc.returncode, run_proc.stdout + "\n" + run_proc.stderr)

            # Corrupt every state.json (including worktrees) to prove `autocode usage` reads only usage.jsonl ledgers.
            state_files = [
                *(workspace / ".autocode" / "runs").glob("*/state.json"),
                *(workspace / ".autocode" / "worktrees").glob("*/.autocode/runs/*/state.json"),
            ]
            self.assertTrue(state_files)
            view = TaskRun(
                workspace,
                state_files[0].parent,
                command=(sys.executable, str(AUTOCODE)),
                env=env,
                cwd=workspace,
            ).status()
            self.assertEqual("answer", view["needs"]["kind"], view)
            for state_file in state_files:
                state_file.write_text("CORRUPT_STATE_JSON_MUST_NOT_BE_READ", encoding="utf-8")

            report_json = subprocess.run(
                [sys.executable, str(AUTOCODE), "usage", "report", "--workspace", str(workspace), "--json"],
                cwd=workspace,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(0, report_json.returncode, report_json.stderr)
            payload = json.loads(report_json.stdout)
            self.assertEqual(["fake"], payload["provenances_present"])
            fake_cohort = payload["by_provenance"]["fake"]
            buckets = fake_cohort["stage_outcomes"]["buckets"]
            self.assertGreaterEqual(buckets["rejected"]["launches"], 1)
            self.assertGreaterEqual(buckets["report_repair"]["launches"], 1)
            seen_classes = {item["class"] for item in fake_cohort["rejections"]["by_class"]}
            self.assertIn("schema", seen_classes)
            requirements = fake_cohort["requirements"]
            self.assertEqual(0, requirements["first_pass"]["accepted_runs"])
            self.assertEqual(1, requirements["first_pass"]["rejected_runs"])
            self.assertEqual(0.0, requirements["first_pass"]["acceptance_rate"])
            self.assertEqual(1, requirements["report_repair"]["attempts"], run_proc.stdout + "\n" + run_proc.stderr)
            self.assertGreater(requirements["report_repair"]["seconds"], 0.0)
            self.assertTrue(requirements["report_repair"]["complete"])

            report_text = subprocess.run(
                [sys.executable, str(AUTOCODE), "usage", "report", "--workspace", str(workspace)],
                cwd=workspace,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(0, report_text.returncode, report_text.stderr)
            self.assertIn("=== Provenance: fake", report_text.stdout)
            self.assertIn("Targets (observational only):", report_text.stdout)

            legacy_cli = subprocess.run(
                [sys.executable, str(TOOLS / "autocode_usage.py"), str(workspace), "--json"],
                cwd=workspace,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(0, legacy_cli.returncode, legacy_cli.stderr)
            legacy_rows = json.loads(legacy_cli.stdout)
            self.assertTrue(legacy_rows)
            self.assertEqual(2, legacy_rows[0]["ledger_schema"])


if __name__ == "__main__":
    unittest.main()
