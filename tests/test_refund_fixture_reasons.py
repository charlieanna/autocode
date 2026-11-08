"""Refund fixture evidence is bound to the actual first or corrected Builder recipe."""

import copy
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scenarios.harness import fake_changed_code as fixture
from scenarios.harness import refund_clock_provider as provider


class RefundFixtureReasonTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.scenario = self.base / "scenario"
        source = Path(__file__).resolve().parents[1] / "scenarios/catalog/feature-refund-window"
        shutil.copytree(source, self.scenario)
        self.root = self.base / "project"
        shutil.copytree(self.scenario / "seed", self.root)
        self.config = {
            "fault": "refund_store_clock",
            "check": "python3 -m unittest discover -s tests -t .",
            "reference": str(self.scenario / "reference"),
            "paths": list(fixture.REFUND_FINAL_PINS),
            "turn_paths": [],
        }

    def recipe(self, first):
        reference = provider.source_for_attempt(self.config, 1 if first else 2)
        shutil.copytree(reference, self.root, dirs_exist_ok=True)
        methods = [
            "test_partial_refunds_are_capped_by_their_total",
            "test_refused_refunds_leave_the_order_unchanged",
            "test_one_cent_is_a_valid_amount",
            "test_non_integer_amounts_are_refused_before_any_mutation",
        ]
        methods += (
            [
                "test_refund_policy_uses_supplied_dates_and_includes_the_last_day",
                "test_refund_policy_refuses_the_following_day_without_mutating_the_order",
            ]
            if first
            else ["test_window_ends_after_the_30th_store_day", "test_window_counts_store_days_not_utc_days"]
        )
        names = ["tests.test_refunds.RefundTests." + name for name in methods]
        proof = {
            "path": "current-proof.json",
            "verdict": "FAIL" if first else "PASS",
            "source_revision": "a" * 64,
            "fail_to_pass": names,
            "not_run_on_base": names,
            "pass_to_pass": [],
            "failures": [
                "Test case has no test named " + name + " that passes with the change"
                for name in ("test_window_ends_after_the_30th_store_day", "test_window_counts_store_days_not_utc_days")
            ]
            if first
            else [],
            "mutation_check": {"collected": not first, "files": [], "selected_tests": [] if first else names},
            "changed_code_evidence": {"source_revision": "a" * 64, "proof_context": "b" * 64, "obligations": []},
        }
        pins = fixture.REFUND_FIRST_PINS if first else fixture.REFUND_FINAL_PINS
        reasons = {"shop/refunds.py": fixture.REFUND_FIRST_REASONS} if first else fixture.REFUND_FINAL_REASONS
        for path, lines in reasons.items():
            for line in lines:
                identity = ["a" * 64, "b" * 64, path, pins[path], line]
                gap = "changed-code:" + hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
                proof["changed_code_evidence"]["obligations"].append(
                    {
                        "gap_id": gap,
                        "path": path,
                        "line": line,
                        "sha256": pins[path],
                        "status": "reason_required",
                        "mutation_outcomes": [],
                    }
                )
        return proof, {**self.config, "reference": str(reference)}, pins

    def judge(self, proof, producer):
        return fixture.judgments({"regression_proof": proof}, self.root, producer=producer)

    def test_each_exact_recipe_covers_only_its_owned_site_less_lines(self):
        for first in (True, False):
            with self.subTest(first=first):
                proof, producer, pins = self.recipe(first)
                gaps = proof["changed_code_evidence"]["obligations"]
                self.assertEqual(
                    {row["gap_id"] for row in gaps}, {row["gap_id"] for row in self.judge(proof, producer)}
                )
                if first:
                    self.assertNotIn("shop/clock.py", {row["path"] for row in gaps})
                for tree in (self.root, Path(producer["reference"])):
                    for name in pins:
                        path = tree / name
                        old = path.read_bytes()
                        path.write_bytes(old + b"\n# unknown\n")
                        self.assertEqual([], self.judge(proof, producer), (first, tree, name))
                        path.write_bytes(old)
                self.assertEqual([], self.judge(proof, {**producer, "reference": str(self.root)}))
                self.assertEqual([], self.judge(proof, {**producer, "paths": []}))

    def test_missing_tests_unrelated_failure_or_incomplete_mutation_never_get_reasons(self):
        for first in (True, False):
            proof, producer, _ = self.recipe(first)
            changes = [
                {"source_revision": "old"},
                {"path": ""},
                {"failed_on_candidate": ["test_failed"]},
                {"unverified": ["missing evidence"]},
                {"not_run_on_base": ["unrelated_missing_test"]},
                {"failures": ["unrelated failure"]},
            ]
            changes.append(
                {"fail_to_pass": []} if first else {"mutation_check": {"collected": True, "selected_tests": []}}
            )
            changes.append({"verdict": "PASS" if first else "FAIL"})
            for change in changes:
                with self.subTest(first=first, change=change):
                    self.assertEqual([], self.judge({**proof, **change}, producer))
            for outcomes in (["survived"], ["not_run"], ["killed"]):
                candidate = copy.deepcopy(proof)
                candidate["changed_code_evidence"]["obligations"] = [
                    {**proof["changed_code_evidence"]["obligations"][0], "mutation_outcomes": outcomes}
                ]
                self.assertEqual([], self.judge(candidate, producer))
            candidate = copy.deepcopy(proof)
            candidate["changed_code_evidence"]["proof_context"] = "c" * 64
            self.assertEqual([], self.judge(candidate, producer))

    def test_tester_recipe_follows_actual_builder_trace_not_handoff_claims(self):
        config_file = self.base / "fake-config.json"
        trace = self.base / "refund-trace.jsonl"
        with patch.dict(os.environ, {"SCENARIO_FAKE_CONFIG": str(config_file)}):
            self.assertEqual({}, provider.tester_producer(self.config))
            for attempt in (1, 2, 3):
                trace.write_text(
                    "\n".join(json.dumps({"stage": stage}) for stage in ["terra", "sol", "astra_resolve"] * attempt)
                    + "\n"
                )
                producer = provider.tester_producer(self.config)
                self.assertEqual(str(provider.source_for_attempt(self.config, attempt)), producer["reference"])
                self.assertEqual(self.config["paths"], producer["paths"])
