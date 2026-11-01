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

    def test_greeting_judgments_serve_the_planning_layout_with_tests_in_a_subdirectory(self):
        reference = REFERENCE.parents[1] / "greenfield-greeting-cli/reference"
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copytree(reference, root, dirs_exist_ok=True)
            (root / "tests").mkdir()
            (root / "test_greet.py").rename(root / "tests/test_greet.py")
            (root / "tests/__init__.py").write_text("")
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
            (root / "tests/test_greet.py").write_text("# CLI assertions removed\n")
            self.assertEqual([], fixture.judgments(data, root))

    def test_the_capacity_broken_variant_gets_reasons_only_with_its_own_tests_and_no_empty_name_claim(self):
        broken = REFERENCE.parents[1] / "capacity-recovery/broken/accepts-empty-name"
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copytree(broken, root, dirs_exist_ok=True)
            source = (root / "greet.py").read_bytes()
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
                                "sha256": hashlib.sha256(source).hexdigest(),
                                "mutation_outcomes": [],
                            }
                            for i in range(1, len(source.splitlines()) + 1)
                        ]
                    },
                }
            }
            judged = fixture.judgments(data, root)
            self.assertEqual(7, len(judged))
            self.assertFalse(
                [row for row in judged if "empty name" in row["reason"] or "empty_name" in row["reason"]], judged
            )
            # The reference tests include test_cli_empty_name, which this product fails: no borrowed reasons.
            shutil.copy(REFERENCE.parents[1] / "greenfield-greeting-cli/reference/test_greet.py", root)
            self.assertEqual([], fixture.judgments(data, root))


class IsoWeeksFixtureReviewTests(unittest.TestCase):
    def test_only_the_iso_lookup_of_the_exact_reference_gets_a_reason_and_its_tests_observe_it(self):
        scenario = REFERENCE.parents[1] / "bugfix-iso-weeks"
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copytree(scenario / "seed", root, dirs_exist_ok=True)
            shutil.copytree(scenario / "reference", root, dirs_exist_ok=True)
            source = root / "timesheet/weeks.py"
            lines = source.read_text().splitlines()
            data = {
                "regression_proof": {
                    "path": "proof.json",
                    "changed_code_evidence": {
                        "obligations": [
                            {
                                "gap_id": f"gap:{i}",
                                "path": "timesheet/weeks.py",
                                "line": i,
                                "status": "reason_required",
                                "sha256": fixture.WEEKS_SOURCE,
                                "mutation_outcomes": [],
                            }
                            for i in range(1, len(lines) + 1)
                        ]
                    },
                }
            }
            self.assertEqual(["gap:14"], [row["gap_id"] for row in fixture.judgments(data, root)])
            self.assertEqual("iso = day.isocalendar()", lines[13].strip())
            for mutation in ({"mutation_outcomes": ["survived"]}, {"sha256": "0" * 64}, {"path": "other.py"}):
                changed = copy.deepcopy(data)
                for row in changed["regression_proof"]["changed_code_evidence"]["obligations"]:
                    row.update(mutation)
                with self.subTest(mutation=mutation):
                    self.assertEqual([], fixture.judgments(changed, root))
            # The reason's claim: the delivered tests fail when the ISO lookup is broken.
            command = [sys.executable, "-m", "unittest", "tests.test_weeks"]
            self.assertEqual(0, subprocess.run(command, cwd=root, capture_output=True).returncode)
            original = source.read_bytes()
            source.write_text(source.read_text().replace("iso = day.isocalendar()", "iso = day"))
            broken = subprocess.run(command, cwd=root, capture_output=True, text=True)
            self.assertNotEqual(0, broken.returncode, broken.stderr)
            self.assertEqual([], fixture.judgments(data, root), "changed source bytes get no reason")
            source.write_bytes(original)
            (root / "tests/test_weeks.py").write_text("import unittest\n")
            self.assertEqual([], fixture.judgments(data, root), "weakened tests cannot borrow the reason")


class RegclientFixtureReviewTests(unittest.TestCase):
    """review-then-fix: reasons only for the fix's lines without a mutation site, bound to its pinned tests."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        shutil.copytree(REFERENCE.parents[1] / "review-then-fix/reference", self.root, dirs_exist_ok=True)
        self.data = {
            "regression_proof": {
                "path": "proof.json",
                "changed_code_evidence": {
                    "obligations": [
                        {
                            "gap_id": f"{path}:{i}",
                            "path": path,
                            "line": i,
                            "status": "reason_required",
                            "sha256": digest,
                            "mutation_outcomes": [],
                        }
                        for path, digest in fixture.REGCLIENT_SOURCES.items()
                        for i in range(1, len((self.root / path).read_text().splitlines()) + 1)
                    ]
                },
            }
        }

    def test_only_the_unsupported_statements_of_the_exact_fix_get_reasons(self):
        rows = fixture.judgments(self.data, self.root)
        self.assertEqual(
            {f"regclient/client.py:{i}" for i in (3, 18, 20, 24, 37, 38)}
            | {f"regclient/policies.py:{i}" for i in (15, 21, 22, 23, 29, 30, 32, 36, 37, 38, 39)},
            {row["gap_id"] for row in rows},
        )
        self.assertNotIn("regclient/policies.py:13", {row["gap_id"] for row in rows}, "a killed default, not a reason")
        self.assertEqual(
            ["regclient/client.py:38"], [row["gap_id"] for row in rows if row["reason_kind"] == "defensive_unreachable"]
        )
        for mutation in ({"mutation_outcomes": ["survived"]}, {"sha256": "0" * 64}, {"path": "unknown.py"}):
            changed = copy.deepcopy(self.data)
            for row in changed["regression_proof"]["changed_code_evidence"]["obligations"]:
                row.update(mutation)
            with self.subTest(mutation=mutation):
                self.assertEqual([], fixture.judgments(changed, self.root))
        for name in (*fixture.REGCLIENT_SOURCES, *fixture.REGCLIENT_TESTS):
            target = self.root / name
            original = target.read_bytes()
            target.write_bytes(original + b"\n# changed\n")
            with self.subTest(name=name):
                self.assertEqual([], fixture.judgments(self.data, self.root))
            target.write_bytes(original)

    def test_the_policy_tests_kill_a_changed_default_backoff(self):
        shutil.copytree(
            REFERENCE.parents[1] / "review-then-fix/seed/regclient", self.root / "regclient", dirs_exist_ok=True
        )
        shutil.copytree(REFERENCE.parents[1] / "review-then-fix/reference", self.root, dirs_exist_ok=True)
        command = [sys.executable, "-m", "unittest", "tests.test_policies"]
        self.assertEqual(0, subprocess.run(command, cwd=self.root, capture_output=True).returncode)
        policies = self.root / "regclient/policies.py"
        policies.write_text(policies.read_text().replace("backoff_seconds: float = 0.0", "backoff_seconds: float = 1"))
        broken = subprocess.run(command, cwd=self.root, capture_output=True, text=True)
        self.assertIn("FAILED (failures=1)", broken.stderr)


class LifecycleFixtureReviewTests(unittest.TestCase):
    def test_lifecycle_references_get_reasons_only_for_their_exact_source_and_tests(self):
        catalog = REFERENCE.parents[1]
        for scenario, variant, path, reasons in (
            ("ladder-18-durable-lease-queue", "reference", "leasequeue/__init__.py", fixture.LEASE_REASONS),
            (
                "ladder-18-durable-lease-queue",
                "broken/process-local-tokens",
                "leasequeue/__init__.py",
                fixture.LEASE_TOKENS_REASONS,
            ),
            ("ladder-19-transactional-outbox", "reference", "outbox/__init__.py", fixture.OUTBOX_REASONS),
        ):
            with self.subTest(scenario=scenario, variant=variant), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                shutil.copytree(catalog / scenario / "seed", root, dirs_exist_ok=True)
                shutil.copytree(catalog / scenario / variant, root, dirs_exist_ok=True)
                source = (root / path).read_bytes()
                data = {
                    "regression_proof": {
                        "path": "proof.json",
                        "changed_code_evidence": {
                            "obligations": [
                                {
                                    "gap_id": f"gap:{i}",
                                    "path": path,
                                    "line": i,
                                    "status": "reason_required",
                                    "sha256": hashlib.sha256(source).hexdigest(),
                                    "mutation_outcomes": [],
                                }
                                for i in range(1, len(source.splitlines()) + 1)
                            ]
                        },
                    }
                }
                rows = fixture.judgments(data, root)
                self.assertEqual(set(reasons), {int(row["gap_id"].split(":")[1]) for row in rows})
                self.assertTrue(all(row["evidence_refs"] == ["proof.json", path] for row in rows))
                for mutation in (
                    {"mutation_outcomes": ["survived"]},
                    {"sha256": "0" * 64},
                    {"path": "unknown.py"},
                ):
                    changed = copy.deepcopy(data)
                    for row in changed["regression_proof"]["changed_code_evidence"]["obligations"]:
                        row.update(mutation)
                    with self.subTest(mutation=mutation):
                        self.assertEqual([], fixture.judgments(changed, root))
                (root / "tests/test_behavior.py").write_text("# behavior assertions removed\n")
                self.assertEqual([], fixture.judgments(data, root), "weakened tests cannot borrow the reasons")


if __name__ == "__main__":
    unittest.main()
