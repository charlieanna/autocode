"""Scripted fixture reviewers cannot waive unknown source or weak behavior tests."""

import copy
import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scenarios.harness import fake_changed_code as fixture
from tests import test_follow_up_acceptance_cli as follow_up

REFERENCE = Path(__file__).resolve().parents[1] / "scenarios/catalog/feature-stock-refusals/reference"


class StockFixtureReviewTests(unittest.TestCase):
    def test_only_known_unsupported_statements_get_precise_source_bound_reasons(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copytree(REFERENCE, root, dirs_exist_ok=True)
            data = {
                "regression_proof": {
                    "path": "proof.json",
                    "changed_code_evidence": {
                        "obligations": [
                            {
                                "gap_id": f"gap:{i}",
                                "path": "stock.py",
                                "sha256": fixture.STOCK_SOURCE,
                                "line": i,
                                "status": "reason_required",
                                "mutation_outcomes": [],
                            }
                            for i in range(1, len((root / "stock.py").read_text().splitlines()) + 1)
                        ]
                    },
                }
            }
            rows = fixture.judgments(data, root)
            self.assertEqual(set(fixture.STOCK_REASONS), {int(row["gap_id"].split(":")[1]) for row in rows})
            self.assertEqual(23, len(rows))
            self.assertNotIn(77, fixture.STOCK_REASONS, "the missing empty-location assertion must be fixed")
            for mutation in (
                {"mutation_outcomes": ["survived"]},
                {"mutation_outcomes": ["not_run"]},
                {"sha256": "0" * 64},
                {"path": "unknown.py"},
            ):
                changed = copy.deepcopy(data)
                for row in changed["regression_proof"]["changed_code_evidence"]["obligations"]:
                    row.update(mutation)
                with self.subTest(mutation=mutation):
                    self.assertEqual([], fixture.judgments(changed, root))
            for name in ("stock.py", "tests/test_stock.py"):
                target = root / name
                original = target.read_bytes()
                target.write_bytes(b"# unknown or weakened input\n")
                with self.subTest(name=name):
                    self.assertEqual([], fixture.judgments(data, root))
                target.write_bytes(original)

    def test_empty_location_guard_fails_for_the_actual_surviving_mutant(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copytree(REFERENCE, root, dirs_exist_ok=True)
            source = root / "stock.py"
            self.assertEqual(fixture.STOCK_SOURCE, hashlib.sha256(source.read_bytes()).hexdigest())
            command = [
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-v",
                "-s",
                "tests",
                "-k",
                "test_remove_last_holding_drops_the_empty_location",
            ]
            passing = subprocess.run(command, cwd=root, capture_output=True, text=True)
            self.assertEqual(0, passing.returncode, passing.stderr)
            source.write_text(source.read_text().replace("del data[location]\n", "pass\n"))
            broken = subprocess.run(command, cwd=root, capture_output=True, text=True)
            self.assertNotEqual(0, broken.returncode, broken.stderr)
            self.assertIn("AssertionError", broken.stderr)
            self.assertIn("Ran 1 test", broken.stderr)


class TotalsFixtureReviewTests(unittest.TestCase):
    def test_only_the_zero_identity_mutant_is_accepted_for_the_exact_numeric_fixture(self):
        sources = (
            "def sum_positive(values): return sum(v for v in values if v >= 0)\n",
            "def sum_positive(values, *, start=0): return start + sum(v for v in values if v >= 0)\n",
        )
        for source, tests in zip(sources, (follow_up.FIRST_TESTS, follow_up.SECOND_TESTS), strict=True):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                (root / "totals.py").write_text(source)
                (root / "test_totals.py").write_text(tests)
                digest = hashlib.sha256(source.encode()).hexdigest()
                mutant = {"line": 1, "operator": "swap_bound", "before": ">=", "after": ">", "outcome": "survived"}
                data = {
                    "regression_proof": {
                        "path": "proof.json",
                        "mutation_check": {"files": [{"path": "totals.py", "mutants": [mutant]}]},
                        "changed_code_evidence": {
                            "obligations": [
                                {
                                    "gap_id": "zero",
                                    "path": "totals.py",
                                    "line": 1,
                                    "sha256": digest,
                                    "status": "reason_required",
                                    "mutation_outcomes": ["survived"],
                                }
                            ]
                        },
                    }
                }
                self.assertEqual(["equivalent_mutant"], [row["reason_kind"] for row in fixture.judgments(data, root)])
                for patch in ({"after": "1"}, {"operator": "constant_return"}, {"outcome": "not_run"}):
                    changed = copy.deepcopy(data)
                    changed["regression_proof"]["mutation_check"]["files"][0]["mutants"][0].update(patch)
                    with self.subTest(patch=patch):
                        self.assertEqual([], fixture.judgments(changed, root))
                (root / "test_totals.py").write_text(follow_up.SEED_TESTS)
                self.assertEqual([], fixture.judgments(data, root), "weakened tests cannot borrow the judgment")


class GreetingFixtureReviewTests(unittest.TestCase):
    def test_greeting_judgments_require_the_exact_cli_assertions_and_no_surviving_observations(self):
        reference = REFERENCE.parents[1] / "greenfield-greeting-cli/reference"
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copytree(reference, root, dirs_exist_ok=True)
            original = (root / "greet.py").read_bytes()
            data = {
                "regression_proof": {
                    "path": "proof.json",
                    "changed_code_evidence": {
                        "obligations": [
                            {
                                "gap_id": f"gap:{i}",
                                "path": "greet.py",
                                "line": i,
                                "status": "reason_required",
                                "sha256": hashlib.sha256(original).hexdigest(),
                                "mutation_outcomes": [],
                            }
                            for i in range(1, len(original.splitlines()) + 1)
                        ]
                    },
                }
            }
            self.assertEqual(7, len(fixture.judgments(data, root)))
            for row in data["regression_proof"]["changed_code_evidence"]["obligations"]:
                row["mutation_outcomes"] = ["survived"]
            self.assertEqual([], fixture.judgments(data, root))
            for row in data["regression_proof"]["changed_code_evidence"]["obligations"]:
                row["mutation_outcomes"] = []
            (root / "test_greet.py").write_text("# CLI assertions removed\n")
            self.assertEqual([], fixture.judgments(data, root))


if __name__ == "__main__":
    unittest.main()
