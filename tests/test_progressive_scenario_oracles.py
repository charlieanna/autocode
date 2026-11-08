"""The cumulative oracle requires rejection of its planted defect before fresh completion."""

import copy
import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

from scenarios.harness import catalog, verdict


class CumulativeRegressionOracleTests(unittest.TestCase):
    def setUp(self):
        self.scenario = catalog.load("progressive-cumulative-regression")
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name) / "project"
        shutil.copytree(self.scenario.seed, self.project)
        shutil.copytree(self.scenario.reference, self.project, dirs_exist_ok=True)
        corrupt = (
            (self.scenario.reference / "lessons.py")
            .read_bytes()
            .replace(
                b"if response != LESSONS[lesson][1]:", b"if lesson == 'addition' or response != LESSONS[lesson][1]:"
            )
        )
        self.repair = {
            "action": "builder",
            "source_revision": "rejected-source",
            "assessment": {
                "obligations": [
                    {
                        "path": "lessons.py",
                        "sha256": hashlib.sha256(corrupt).hexdigest(),
                        "line": 12,
                        "status": "reason_required",
                        "mutation_outcomes": ["survived"],
                    }
                ]
            },
        }
        self.record = {
            "status": "TASK_COMPLETE",
            "view": {
                "workflow": "build",
                "progressive": {
                    "demonstrated_slices": [{"slice_id": "S1"}, {"slice_id": "S2"}],
                    "current_whole_product_proof": {"verified": True},
                },
                "verification": {"source_revision": "repaired-source", "changed_code_repairs": [self.repair]},
            },
            "stages": ["astra_challenge", "terra", "sol", "terra", "sol", "terra", "sol", "astra_review"],
            "cli_calls": ["approve-plan", "resume"],
            "steps": [{"kind": "approve-plan", "exit": 0}],
        }

    def score(self, record):
        result = verdict.evaluate(self.scenario, self.project, record)
        self.assertEqual("", result.error)
        return verdict.judge("TASK_COMPLETE", result)[0], {row.name: row.ok for row in result.checks}

    def test_rejected_planted_line_and_fresh_repair_complete_without_a_resolver(self):
        self.assertEqual(verdict.PASS, self.score(self.record)[0])
        self.assertNotIn("astra_resolve", self.scenario.requires_stages)

    def test_extra_attempts_cannot_substitute_for_the_actual_rejected_defect(self):
        variants = [
            [],
            [{**self.repair, "action": "complete"}],
            [{**self.repair, "source_revision": "repaired-source"}],
        ]
        for change in (
            {"path": "progress.py"},
            {"sha256": "unknown"},
            {"line": 13},
            {"status": "tested"},
            {"mutation_outcomes": ["killed"]},
        ):
            repair = copy.deepcopy(self.repair)
            repair["assessment"]["obligations"][0].update(change)
            variants.append([repair])
        for repairs in variants:
            with self.subTest(repairs=repairs):
                record = copy.deepcopy(self.record)
                record["view"]["verification"]["changed_code_repairs"] = repairs
                outcome, checks = self.score(record)
                self.assertFalse(checks["planted_regression_rejected_before_repair"])
                self.assertEqual(verdict.FALSE_COMPLETE, outcome)

    def test_current_product_proof_and_fresh_validation_remain_required(self):
        for field in ("proof", "attempts"):
            with self.subTest(field=field):
                record = copy.deepcopy(self.record)
                if field == "proof":
                    record["view"]["progressive"]["current_whole_product_proof"] = {"verified": False}
                else:
                    record["stages"] = ["astra_challenge", "terra", "sol"]
                self.assertEqual(verdict.FALSE_COMPLETE, self.score(record)[0])
