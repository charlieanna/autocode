"""Qualification measurements disclose failed work, metric scope and coverage."""

import copy
import unittest
from unittest.mock import patch

import autocode_route_selector as selector
import qualify_routes

from . import test_qualify_routes as fixtures

GLM = "zai-coding-plan/glm-5.3"
META = {"date": "2026-10-27", "run": "sample-1", "scenario": "bugfix-trivial", "profile": "test"}


def attempts():
    return [
        {"role": "glm", "model": GLM, "duration_seconds": 60, "reported_cost_usd": 0.25},
        {
            "role": "glm",
            "model": GLM,
            "duration_seconds": 600,
            "reported_cost_usd": 2.0,
            "rejected": True,
            "timed_out": True,
        },
        {"role": "glm", "model": GLM, "duration_seconds": 20, "report_only": True},
    ]


def record(rows):
    with patch.object(qualify_routes.usage, "accounting", return_value={"attempts": rows}):
        return qualify_routes.run_record(fixtures.state(), "glm", GLM, META)


def entry(records):
    return {"model": GLM, "evidence": "fixture", "provenance": "qualified", "runs": records}


class RouteEvidenceTests(unittest.TestCase):
    def test_new_record_retains_failure_and_unpriced_repair_without_qualifying_them(self):
        rows = attempts()
        original = copy.deepcopy(rows)
        result = record(rows)
        self.assertEqual((1, 1.0, 0.25), (result["attempts"], result["minutes"], result["cost_usd"]))
        accounting = result.get("attempt_accounting")
        self.assertIsInstance(accounting, dict, "accepted-only totals cannot explain the failed/repair spend")
        self.assertEqual(
            (3, 1, 1, 1),
            tuple(accounting[k] for k in ("attempts", "accepted_attempts", "failed_attempts", "report_only_attempts")),
        )
        self.assertEqual(680, accounting["duration_seconds"]["value"])
        reported = accounting["reported_cost_usd"]
        self.assertEqual(
            (2.25, None, False, 2, 1),
            tuple(reported[k] for k in ("known", "value", "complete", "known_items", "unknown_items")),
        )
        self.assertEqual(original, rows)
        with patch.object(qualify_routes.usage, "accounting", return_value={"attempts": rows[1:]}):
            self.assertEqual({}, qualify_routes.reached(fixtures.state()))

    def test_summary_labels_legacy_scope_and_missing_run_denominators(self):
        records = [
            {**META, "run": "a", "minutes": 10, "cost_usd": 0.5},
            {**META, "run": "b", "minutes": 30, "cost_usd": None},
            {**META, "run": "c", "minutes": None, "cost_usd": 1.5},
        ]
        text = selector.evidence_summary(entry(records))
        self.assertIn("accepted-only", text)
        self.assertIn("2/3", text)
        self.assertIn("failed/repair", text)
        self.assertIn("not whole-task", text)

    def test_all_attempt_summary_does_not_pool_legacy_or_reported_and_estimated_costs(self):
        rows = attempts()
        rows[2]["historical_estimated_cost_usd"] = 0.1
        fresh = record(rows)
        old = {**META, "run": "old", "minutes": 999, "cost_usd": 999}
        text = selector.evidence_summary(entry([fresh, old]))
        self.assertIn("all-attempt", text)
        self.assertIn("1 failed", text)
        self.assertIn("1 repair", text)
        self.assertIn("reported", text)
        self.assertIn("$2.25", text)
        self.assertIn("historical estimate", text)
        self.assertIn("$0.10", text)
        self.assertIn("accepted-only", text)
        self.assertNotIn("$2.35", text)
        self.assertNotIn("median $500", text)

    def test_selector_rejects_nonfinite_record_metrics(self):
        for value in (float("inf"), float("nan"), True, -1):
            with self.subTest(value=value):
                table = {"as_of": "x", "roles": {"glm": [entry([{**META, "minutes": value, "cost_usd": None}])]}}
                with self.assertRaisesRegex(ValueError, "runs must be records"):
                    selector.parse(table)

    def test_uncertain_native_usage_and_pending_workers_never_become_complete_totals(self):
        for flag in ("unobserved_usage_possible", "active"):
            with self.subTest(flag=flag):
                rows = attempts()[:1]
                rows[0][flag] = True
                result = record(rows)["attempt_accounting"]
                metric = result["reported_cost_usd"]
                self.assertEqual(0.25, metric["known"])
                self.assertIsNone(metric["value"])
                self.assertFalse(metric["complete"])
        with patch.object(
            qualify_routes.usage,
            "accounting",
            return_value={"attempts": attempts()[:1], "parallel_workers_pending": ["M2"]},
        ):
            result = qualify_routes.run_record(fixtures.state(), "glm", GLM, META)
        self.assertTrue(result["attempt_accounting"]["pending_workers"])
        self.assertIsNone(result["attempt_accounting"]["duration_seconds"]["value"])
        selector.parse({"as_of": "x", "roles": {"glm": [entry([result])]}})

    def test_native_attribution_is_not_replaced_with_duplicate_raw_reported_cost(self):
        rows = attempts()[:1]
        rows[0]["attributed_reported_usd"] = {
            "known": 0,
            "value": 0,
            "complete": True,
            "known_items": 1,
            "unknown_items": 0,
        }
        result = record(rows)
        self.assertEqual(0, result["attempt_accounting"]["reported_cost_usd"]["value"])
        self.assertIn("reported known $0.00", selector.evidence_summary(entry([result])))

    def test_optional_accounting_rejects_inconsistent_counts_quantities_and_versions(self):
        good = record(attempts())
        selector.parse({"as_of": "x", "roles": {"glm": [entry([good])]}})
        for fault in ("version", "scope", "count", "overlap", "coverage", "complete", "infinity", "legacy_count"):
            with self.subTest(fault=fault):
                result = copy.deepcopy(good)
                value = result["attempt_accounting"]
                if fault == "version":
                    value["schema"] = 2
                elif fault == "scope":
                    value["scope"] = "whole_task"
                elif fault == "count":
                    value["failed_attempts"] = True
                elif fault == "overlap":
                    value["accepted_attempts"] = 3
                elif fault == "coverage":
                    value["reported_cost_usd"]["unknown_items"] = 0
                elif fault == "complete":
                    value["reported_cost_usd"]["complete"] = True
                elif fault == "infinity":
                    value["reported_cost_usd"]["known"] = float("inf")
                elif fault == "legacy_count":
                    result["attempts"] = True
                with self.assertRaisesRegex(ValueError, "runs must be records"):
                    selector.parse({"as_of": "x", "roles": {"glm": [entry([result])]}})

    def test_real_public_accounting_deduplicates_a_shared_native_receipt(self):
        native = {
            "requests": [
                {
                    "identity": ["session-1", "finish-1"],
                    "tokens": {"input_tokens": 100, "output_tokens": 20},
                    "reported_cost_usd": 0.25,
                }
            ],
            "issues": [],
        }
        source = fixtures.state()
        source["stages"] = [
            {
                "stage": "glm_revise",
                "role": "glm",
                "engine": "opencode",
                "command": ["fake", "--model", GLM],
                "attempt_id": "001/planner-01",
                "output": "planner-01.json",
                "duration_seconds": 60,
                "metrics": {"request_context": {"accounting": native}},
            },
            {
                "stage": "glm_revise_report_repair",
                "role": "glm",
                "engine": "opencode",
                "command": ["fake", "--model", GLM],
                "attempt_id": "001/planner-repair-01",
                "output": "planner-repair-01.json",
                "duration_seconds": 10,
                "report_only": True,
                "metrics": {"request_context": {"accounting": native}},
            },
        ]
        before = copy.deepcopy(source)
        result = qualify_routes.run_record(source, "glm", GLM, META)
        self.assertEqual(2, result["attempt_accounting"]["attempts"])
        self.assertEqual(0.25, result["attempt_accounting"]["reported_cost_usd"]["value"])
        self.assertEqual(before, source)

    def test_only_the_actual_role_model_scope_contributes_measurements(self):
        rows = attempts()
        rows[0]["role"] = "astra"
        rows[0]["route_role"] = "glm"
        rows += [
            {"role": "glm", "route_role": "plan_reviewer", "model": GLM, "reported_cost_usd": 9},
            {"role": "glm", "model": "different/model", "reported_cost_usd": 9},
            {"role": "glm", "model": GLM, "runner_owned": True, "reported_cost_usd": 9},
        ]
        result = record(rows)["attempt_accounting"]
        self.assertEqual((3, 1), (result["attempts"], result["accepted_attempts"]))
        self.assertEqual(2.25, result["reported_cost_usd"]["known"])
