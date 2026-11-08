"""Scripted fixture reviewers cannot waive unknown source or weak behavior tests."""

import copy
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scenarios.harness import fake_changed_code as fixture
from tests import test_follow_up_acceptance_cli as follow_up

REFERENCE = Path(__file__).resolve().parents[1] / "scenarios/catalog/feature-stock-refusals/reference"


class ByProjectFixtureReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.root, self.reference = base / "project", base / "reference"
        scenario = REFERENCE.parents[1] / "feature-timesheet-by-project"
        shutil.copytree(scenario / "seed", self.root)
        shutil.copytree(scenario / "reference", self.reference)
        shutil.copytree(self.reference, self.root, dirs_exist_ok=True)
        self.producer = {
            "reference": str(self.reference),
            "paths": list(fixture.BY_PROJECT_PINS),
            "check": "python3 -m unittest discover -s tests -t .",
        }
        identity = ["a" * 64, "b" * 64, "timesheet/report.py", fixture.BY_PROJECT_PINS["timesheet/report.py"], 21]
        self.gap = "changed-code:" + hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
        self.proof = {
            "verdict": "PASS",
            "path": "current-proof.json",
            "source_revision": identity[0],
            "mutation_check": {
                "selected_tests": [
                    "tests.test_by_project.ByProjectTests." + name
                    for name in (
                        "test_combines_with_project_filter",
                        "test_rows_per_week_and_project",
                        "test_cli_preserves_default_and_filters_project_rows",
                    )
                ]
            },
            "changed_code_evidence": {
                "source_revision": identity[0],
                "proof_context": identity[1],
                "obligations": [
                    {
                        "gap_id": self.gap,
                        "path": identity[2],
                        "sha256": identity[3],
                        "line": 21,
                        "status": "reason_required",
                        "mutation_outcomes": [],
                    }
                ],
            },
        }

    def judge(self, proof=None, producer=None):
        return fixture.judgments(
            {"regression_proof": self.proof if proof is None else proof},
            self.root,
            producer=self.producer if producer is None else producer,
        )

    def test_only_the_exact_producer_and_executed_tests_authorize_the_signature(self):
        (reason,) = self.judge()
        self.assertEqual(self.gap, reason["gap_id"])
        self.assertIn("test_cli_preserves_default", reason["reason"])
        for tree in (self.root, self.reference):
            for name in fixture.BY_PROJECT_PINS:
                path = tree / name
                old = path.read_bytes()
                path.write_bytes(old + b"\n# changed\n")
                with self.subTest(tree=tree.name, path=name):
                    self.assertEqual([], self.judge())
                path.write_bytes(old)
        self.assertEqual([], fixture.judgments({"regression_proof": self.proof}, self.root))
        for change in (
            {"reference": str(self.root)},
            {"paths": []},
            {"check": "echo PASS"},
            {"turn_paths": [["timesheet/"]]},
        ):
            with self.subTest(change=change):
                self.assertEqual([], self.judge(producer={**self.producer, **change}))

    def test_stale_gap_failed_proof_survivors_and_missing_cli_test_are_refused(self):
        for change in (
            {"verdict": "FAIL"},
            {"source_revision": "old"},
            {"path": ""},
            {"mutation_check": {"selected_tests": []}},
        ):
            with self.subTest(change=change):
                self.assertEqual([], self.judge({**self.proof, **change}))
        for change in (
            {"gap_id": "old"},
            {"path": "timesheet/__main__.py"},
            {"line": 22},
            {"sha256": "wrong"},
            {"status": "tested"},
            {"mutation_outcomes": ["survived"]},
            {"mutation_outcomes": ["not_run"]},
            {"mutation_outcomes": ["killed"]},
        ):
            proof = copy.deepcopy(self.proof)
            proof["changed_code_evidence"]["obligations"][0].update(change)
            with self.subTest(change=change):
                self.assertEqual([], self.judge(proof))
        proof = copy.deepcopy(self.proof)
        proof["changed_code_evidence"]["obligations"] *= 2
        self.assertEqual([], self.judge(proof))

    def test_symlinked_producer_or_workspace_cannot_borrow_known_bytes(self):
        for tree in (self.root, self.reference):
            path = tree / "timesheet"
            held = tree / "held"
            path.rename(held)
            path.symlink_to(held, target_is_directory=True)
            with self.subTest(tree=tree.name):
                self.assertEqual([], self.judge())
            path.unlink()
            held.rename(path)


class PackageGreetingFixtureReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.root, self.reference = base / "project", base / "reference"
        catalog = REFERENCE.parents[1] / "feature-monorepo-package"
        shutil.copytree(catalog / "seed", self.root)
        shutil.copytree(catalog / "reference", self.reference)
        shutil.copytree(self.reference, self.root, dirs_exist_ok=True)
        self.producer = {
            "reference": str(self.reference),
            "paths": [fixture.PACKAGE_GREETING_SOURCE, fixture.PACKAGE_GREETING_FORMAL_TESTS],
            "check": "cd packages/greet && python3 -m unittest discover -v",
            "turn_paths": [],
        }
        identity = [
            "a" * 64,
            "b" * 64,
            fixture.PACKAGE_GREETING_SOURCE,
            fixture.PACKAGE_GREETING_PINS[fixture.PACKAGE_GREETING_SOURCE],
            8,
        ]
        self.gap = "changed-code:" + hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
        self.data = {
            "regression_proof": {
                "path": ".autocode/current-proof/report.json",
                "changed_code_evidence": {
                    "source_revision": identity[0],
                    "proof_context": identity[1],
                    "obligations": [
                        {
                            "path": identity[2],
                            "sha256": identity[3],
                            "line": 8,
                            "gap_id": self.gap,
                            "status": "reason_required",
                            "mutation_outcomes": [],
                        }
                    ],
                },
            }
        }

    def judge(self, data=None, producer=None):
        return fixture.judgments(
            self.data if data is None else data, self.root, producer=self.producer if producer is None else producer
        )

    def test_only_the_scripted_source_and_both_behavior_tests_authorize_its_header_reason(self):
        (reason,) = self.judge()
        self.assertEqual(self.gap, reason["gap_id"])
        self.assertIn("test_formal_greeting", reason["reason"])
        self.assertEqual(
            [self.data["regression_proof"]["path"], fixture.PACKAGE_GREETING_SOURCE], reason["evidence_refs"]
        )
        self.assertEqual([], fixture.judgments(self.data, self.root), "workspace bytes alone are not a producer")
        for name in fixture.PACKAGE_GREETING_PINS:
            path = self.root / name
            original = path.read_bytes()
            path.write_bytes(original + b"\n# unknown or weakened bytes\n")
            with self.subTest(workspace_file=name):
                self.assertEqual([], self.judge())
            path.write_bytes(original)
        for name in (fixture.PACKAGE_GREETING_SOURCE, fixture.PACKAGE_GREETING_FORMAL_TESTS):
            path = self.reference / name
            original = path.read_bytes()
            path.write_bytes(original + b"\n# unknown producer\n")
            with self.subTest(producer_file=name):
                self.assertEqual([], self.judge())
            path.write_bytes(original)

    def test_unknown_producer_and_workspace_masquerading_as_reference_are_refused(self):
        for change in (
            {"reference": str(self.root)},
            {"reference": str(self.reference / "missing")},
            {"paths": []},
            {"paths": [[]]},
            {"check": "echo PASS"},
            {"turn_paths": [["other.py"]]},
        ):
            with self.subTest(change=change):
                self.assertEqual([], self.judge(producer={**self.producer, **change}))
        path = self.reference / fixture.PACKAGE_GREETING_SOURCE
        path.unlink()
        path.symlink_to(self.root / fixture.PACKAGE_GREETING_SOURCE)
        self.assertEqual([], self.judge(), "a workspace link is not an owned reference source")

    def test_wrong_gap_source_line_and_every_mutation_outcome_are_refused(self):
        for change in (
            {"gap_id": "changed-code:" + "0" * 64},
            {"path": "packages/farewell/greet.py"},
            {"line": 9},
            {"line": True},
            {"sha256": "0" * 64},
            {"status": "tested"},
            *({"mutation_outcomes": value} for value in (["survived"], ["not_run"], ["killed"], None)),
        ):
            data = copy.deepcopy(self.data)
            data["regression_proof"]["changed_code_evidence"]["obligations"][0].update(change)
            with self.subTest(change=change):
                self.assertEqual([], self.judge(data))
        for key in ("source_revision", "proof_context"):
            data = copy.deepcopy(self.data)
            data["regression_proof"]["changed_code_evidence"][key] = "c" * 64
            with self.subTest(stale_binding=key):
                self.assertEqual([], self.judge(data))

    def test_reference_ancestor_cannot_borrow_workspace_bytes_as_its_producer(self):
        packages = self.reference / "packages"
        shutil.rmtree(packages)
        packages.symlink_to(self.root / "packages", target_is_directory=True)
        self.assertEqual([], self.judge())

    def test_workspace_ancestor_cannot_borrow_matching_bytes_outside_its_root(self):
        packages = self.root / "packages"
        outside = self.root.parent / "outside-packages"
        shutil.copytree(packages, outside)
        shutil.rmtree(packages)
        packages.symlink_to(outside, target_is_directory=True)
        self.assertEqual([], self.judge())

    def test_leaf_links_cyclic_ancestors_and_unreadable_inputs_are_refused(self):
        from unittest import mock

        source = self.root / fixture.PACKAGE_GREETING_SOURCE
        original = source.read_bytes()
        source.unlink()
        source.symlink_to(self.reference / fixture.PACKAGE_GREETING_SOURCE)
        self.assertEqual([], self.judge(), "workspace leaf link borrowed outside source")
        source.unlink()
        source.write_bytes(original)
        packages = self.reference / "packages"
        retained = self.reference / "owned-packages"
        packages.rename(retained)
        packages.symlink_to(packages, target_is_directory=True)
        self.assertEqual([], self.judge(), "cyclic producer ancestor did not refuse")
        packages.unlink()
        retained.rename(packages)
        with mock.patch.object(Path, "read_bytes", side_effect=PermissionError("unreadable fixture")):
            self.assertEqual([], self.judge())


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


class CsvcheckFixtureReviewTests(unittest.TestCase):
    def test_csvcheck_judgments_cover_every_reference_line_and_require_the_pinned_tests(self):
        reference = REFERENCE.parents[1] / "ladder-03-csv-validation-cli/reference"
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copytree(reference, root, dirs_exist_ok=True)
            obligations = []
            for name in ("csvcheck/__init__.py", "csvcheck/__main__.py"):
                original = (root / name).read_bytes()
                obligations.extend(
                    {
                        "gap_id": f"gap:{name}:{i}",
                        "path": name,
                        "line": i,
                        "status": "reason_required",
                        "sha256": hashlib.sha256(original).hexdigest(),
                        "mutation_outcomes": [],
                    }
                    for i in range(1, len(original.splitlines()) + 1)
                )
            data = {
                "regression_proof": {
                    "path": "proof.json",
                    "changed_code_evidence": {
                        "obligations": [
                            row
                            for row in obligations
                            if (root / row["path"]).read_text().splitlines()[row["line"] - 1].strip()
                        ]
                    },
                }
            }
            served = fixture.judgments(data, root)
            self.assertEqual(
                {row["gap_id"] for row in data["regression_proof"]["changed_code_evidence"]["obligations"]},
                {row["gap_id"] for row in served},
            )
            (root / "tests/test_csvcheck.py").write_text("# CLI assertions removed\n")
            self.assertEqual([], fixture.judgments(data, root))

    def test_csvcheck_judgments_accept_only_the_equivalent_age_bound_survivor(self):
        reference = REFERENCE.parents[1] / "ladder-03-csv-validation-cli/reference"
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copytree(reference, root, dirs_exist_ok=True)
            digest = hashlib.sha256((root / "csvcheck/__init__.py").read_bytes()).hexdigest()

            def build(survivor):
                return {
                    "regression_proof": {
                        "path": "proof.json",
                        "mutation_check": {
                            "files": [
                                {
                                    "path": "csvcheck/__init__.py",
                                    "mutants": [
                                        {
                                            "line": 13,
                                            "operator": "negate_condition",
                                            "before": 'state == "quoted"',
                                            "after": 'not (state == "quoted")',
                                            "outcome": "killed",
                                        },
                                        {
                                            "line": 60,
                                            "operator": "swap_bound",
                                            "before": ">",
                                            "after": ">=",
                                            "outcome": "killed",
                                        },
                                        dict(
                                            {
                                                "line": 60,
                                                "operator": "shift_bound",
                                                "before": "3",
                                                "after": "4",
                                                "outcome": "survived",
                                            },
                                            **survivor,
                                        ),
                                    ],
                                }
                            ]
                        },
                        "changed_code_evidence": {
                            "obligations": [
                                {
                                    "gap_id": "gap:csvcheck/__init__.py:60",
                                    "path": "csvcheck/__init__.py",
                                    "line": 60,
                                    "status": "reason_required",
                                    "sha256": digest,
                                    "mutation_outcomes": ["killed", "survived"],
                                }
                            ]
                        },
                    }
                }

            served = fixture.judgments(build({}), root)
            self.assertEqual(
                [
                    {
                        "gap_id": "gap:csvcheck/__init__.py:60",
                        "decision": "accept",
                        "reason_kind": "equivalent_mutant",
                    }
                ],
                [{k: row[k] for k in ("gap_id", "decision", "reason_kind")} for row in served],
            )
            for patch in ({"after": "5"}, {"operator": "constant_return"}, {"outcome": "not_run"}, {"line": 34}):
                with self.subTest(patch=patch):
                    self.assertEqual([], fixture.judgments(build(patch), root))
            (root / "tests/test_csvcheck.py").write_text("# CLI assertions removed\n")
            self.assertEqual([], fixture.judgments(build({}), root), "weakened tests cannot borrow the judgment")


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
        # A line with a mutant gets a reason only when it is the unreachable raise and its mutant survived
        # (drop_raise, #2815): a killed or unrun one, or any other line, gets none.
        for mutation, expected in (
            ({"mutation_outcomes": ["survived"]}, ["regclient/client.py:38"]),
            ({"mutation_outcomes": ["killed"]}, []),
            ({"mutation_outcomes": ["survived", "not_run"]}, []),
            ({"sha256": "0" * 64}, []),
            ({"path": "unknown.py"}, []),
        ):
            changed = copy.deepcopy(self.data)
            for row in changed["regression_proof"]["changed_code_evidence"]["obligations"]:
                row.update(mutation)
            with self.subTest(mutation=mutation):
                self.assertEqual(expected, [row["gap_id"] for row in fixture.judgments(changed, self.root)])
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


class LeaseCleanupFixtureReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.root, self.reference = base / "project", base / "reference"
        scenario = REFERENCE.parents[1] / "ladder-18-durable-lease-queue"
        shutil.copytree(scenario / "reference", self.reference)
        shutil.copytree(self.reference, self.root)
        self.path = "leasequeue/__init__.py"
        self.producer = {
            "reference": str(self.reference),
            "paths": [self.path, "tests/test_behavior.py"],
            "check": "python3 -m unittest discover -s tests -t .",
            "turn_paths": [],
        }

    def data(self, line=23):
        identity = ["a" * 64, "b" * 64, self.path, fixture.LEASE_SOURCE, line]
        gap = "changed-code:" + hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
        mutant = {
            "line": line,
            "operator": "never_catch" if line == 23 else "drop_call",
            "before": "BaseException" if line == 23 else "db.rollback()",
            "after": "()" if line == 23 else "None",
            "source_sha256": fixture.LEASE_SOURCE,
            "outcome": "survived",
            "failed_tests": [],
            "survived_tests": [fixture.LEASE_TRANSACTION_CASE],
            "evidence": ["/runner/mutant.log"],
        }
        return {
            "regression_proof": {
                "path": "/runner/verification.json",
                "verdict": "PASS",
                "source_revision": identity[0],
                "fail_to_pass": [fixture.LEASE_TRANSACTION_CASE],
                "changed_code_evidence": {
                    "source_revision": identity[0],
                    "proof_context": identity[1],
                    "obligations": [
                        {
                            "gap_id": gap,
                            "path": self.path,
                            "line": line,
                            "status": "reason_required",
                            "sha256": fixture.LEASE_SOURCE,
                            "mutation_outcomes": ["survived"],
                        }
                    ],
                },
                "mutation_check": {"files": [{"path": self.path, "sha256": fixture.LEASE_SOURCE, "mutants": [mutant]}]},
            }
        }

    def judge(self, data=None, producer=None):
        rows = fixture.judgments(
            self.data() if data is None else data,
            self.root,
            producer=self.producer if producer is None else producer,
        )
        return [row for row in rows if row["reason_kind"] == "equivalent_mutant"]

    def test_exact_cleanup_observations_require_the_delivered_transaction_test(self):
        for line in (23, 24):
            data = self.data(line)
            (reason,) = self.judge(data)
            self.assertEqual(
                data["regression_proof"]["changed_code_evidence"]["obligations"][0]["gap_id"], reason["gap_id"]
            )
            for change in (
                {"operator": "drop_raise"},
                {"before": "db.close()"},
                {"after": "pass"},
                {"source_sha256": "0" * 64},
                {"line": 20},
                {"outcome": "not_run"},
                {"failed_tests": [fixture.LEASE_TRANSACTION_CASE]},
                {"survived_tests": ["unrelated"]},
                {"evidence": []},
            ):
                altered = copy.deepcopy(data)
                altered["regression_proof"]["mutation_check"]["files"][0]["mutants"][0].update(change)
                with self.subTest(line=line, change=change):
                    self.assertEqual([], self.judge(altered))
            altered = copy.deepcopy(data)
            altered["regression_proof"]["mutation_check"]["files"].append(
                altered["regression_proof"]["mutation_check"]["files"][0]
            )
            self.assertEqual([], self.judge(altered))

    def test_stale_gap_or_incomplete_proof_has_no_equivalence_authority(self):
        for change in (
            {"gap_id": "changed-code:" + "0" * 64},
            {"line": 27},
            {"line": 23.0},
            {"status": "tested"},
            {"sha256": "0" * 64},
            {"mutation_outcomes": ["timeout"]},
        ):
            data = self.data()
            data["regression_proof"]["changed_code_evidence"]["obligations"][0].update(change)
            with self.subTest(gap=change):
                self.assertEqual([], self.judge(data))
        for change in ({"verdict": "FAIL"}, {"source_revision": "c" * 64}, {"path": ""}, {"fail_to_pass": []}):
            data = self.data()
            data["regression_proof"].update(change)
            with self.subTest(proof=change):
                self.assertEqual([], self.judge(data))
        for key in ("source_revision", "proof_context"):
            data = self.data()
            data["regression_proof"]["changed_code_evidence"][key] = "c" * 64
            self.assertEqual([], self.judge(data))

    def test_unknown_or_borrowed_producer_source_and_test_bytes_are_refused(self):
        for change in (
            {"paths": []},
            {"paths": [[]]},
            {"check": "echo PASS"},
            {"turn_paths": [["other.py"]]},
            {"reference": str(self.root)},
        ):
            with self.subTest(producer=change):
                self.assertEqual([], self.judge(producer={**self.producer, **change}))
        self.assertEqual([], self.judge(producer={}))
        for root in (self.root, self.reference):
            for name in (self.path, "tests/test_behavior.py"):
                path = root / name
                original = path.read_bytes()
                path.write_bytes(original + b"\n# unknown bytes\n")
                with self.subTest(root=root, file=name):
                    self.assertEqual([], self.judge())
                path.write_bytes(original)
        path = self.reference / self.path
        path.unlink()
        path.symlink_to(self.root / self.path)
        self.assertEqual([], self.judge())


class LifecycleFixtureReviewTests(unittest.TestCase):
    def test_only_the_exact_outbox_cleanup_mutants_get_equivalence_reasons(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            scenario = REFERENCE.parents[1] / "ladder-19-transactional-outbox"
            shutil.copytree(scenario / "seed", root, dirs_exist_ok=True)
            shutil.copytree(scenario / "reference", root, dirs_exist_ok=True)
            for line, operator, before, after in (
                (24, "never_catch", "BaseException", "()"),
                (25, "drop_call", "db.rollback()", "None"),
            ):
                gap = {
                    "gap_id": f"gap:{line}",
                    "path": "outbox/__init__.py",
                    "line": line,
                    "status": "reason_required",
                    "sha256": fixture.OUTBOX_SOURCE,
                    "mutation_outcomes": ["survived"],
                }
                mutant = {
                    "line": line,
                    "operator": operator,
                    "before": before,
                    "after": after,
                    "source_sha256": fixture.OUTBOX_SOURCE,
                    "outcome": "survived",
                    "failed_tests": [],
                    "survived_tests": ["tests.test_behavior.OutboxTests.test_orders_publish_and_restart"],
                    "evidence": ["/runner/mutant.log"],
                }
                data = {
                    "regression_proof": {
                        "path": "/runner/verification.json",
                        "changed_code_evidence": {"obligations": [gap]},
                        "mutation_check": {
                            "files": [{"path": gap["path"], "sha256": fixture.OUTBOX_SOURCE, "mutants": [mutant]}]
                        },
                    }
                }
                (reason,) = fixture.judgments(data, root)
                self.assertEqual("equivalent_mutant", reason["reason_kind"])
                self.assertEqual(gap["gap_id"], reason["gap_id"])
                for change in (
                    {"operator": "drop_raise"},
                    {"before": "db.close()"},
                    {"after": "pass"},
                    {"source_sha256": "0" * 64},
                    {"line": 21},
                    {"outcome": "not_run"},
                    {"failed_tests": ["tests.failed"]},
                    {"survived_tests": []},
                    {"evidence": "not an observation list"},
                ):
                    altered = copy.deepcopy(data)
                    altered["regression_proof"]["mutation_check"]["files"][0]["mutants"][0].update(change)
                    with self.subTest(line=line, change=change):
                        self.assertEqual([], fixture.judgments(altered, root))
                for change in ({"line": 28}, {"mutation_outcomes": ["timeout"]}, {"sha256": "0" * 64}):
                    altered = copy.deepcopy(data)
                    altered["regression_proof"]["changed_code_evidence"]["obligations"][0].update(change)
                    with self.subTest(gap=change):
                        self.assertEqual([], fixture.judgments(altered, root))
                altered = copy.deepcopy(data)
                altered["regression_proof"]["mutation_check"]["files"][0]["mutants"].append(mutant)
                self.assertEqual([], fixture.judgments(altered, root))
            tests = root / "tests/test_behavior.py"
            tests.write_text(tests.read_text() + "\n# unknown test bytes\n")
            self.assertEqual([], fixture.judgments(data, root))

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


class ProgressiveFixtureReviewTests(unittest.TestCase):
    def test_only_scripted_variants_exact_import_gaps_receive_reasons(self):
        scenario = Path(__file__).resolve().parents[1] / "scenarios/catalog/progressive-learning-journey"
        reference = scenario / "reference"
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copy2(scenario / "seed/test_journey.py", root / "test_journey.py")
            for slice_id, variant in (
                ("S1", {}),
                ("S2", {}),
                ("S3", {"catalog": True}),
                ("S2", {"note": True}),
                ("S2", {"corrupt": True}),
            ):
                sources = fixture.progressive_sources(reference, slice_id, **variant)
                for name, content in sources.items():
                    (root / name).write_bytes(content)
                obligations = [
                    {
                        "gap_id": f"gap:{name}:{line}",
                        "path": name,
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "line": line,
                        "status": "reason_required",
                        "mutation_outcomes": [],
                    }
                    for name, content in sources.items()
                    for line in range(1, len(content.splitlines()) + 1)
                ]
                data = {
                    "regression_proof": {"path": "proof.json", "changed_code_evidence": {"obligations": obligations}}
                }

                def review(packet=data):
                    return fixture.progressive_judgments(packet, reference, slice_id, root=root, **variant)

                with self.subTest(slice_id=slice_id, variant=variant):
                    self.assertEqual(
                        {"gap:lessons.py:1", "gap:progress.py:1", "gap:progress.py:2"}, {r["gap_id"] for r in review()}
                    )
                    for mutation in (
                        {"mutation_outcomes": ["survived"]},
                        {"mutation_outcomes": ["not_run"]},
                        {"sha256": "0" * 64},
                        {"status": "tested"},
                        {"path": "unknown.py"},
                        {"line": True},
                        {"gap_id": ""},
                    ):
                        changed = copy.deepcopy(data)
                        for row in changed["regression_proof"]["changed_code_evidence"]["obligations"]:
                            row.update(mutation)
                        self.assertEqual([], review(changed), mutation)
                    for name in (*sources, "test_journey.py"):
                        path = root / name
                        original = path.read_bytes()
                        path.write_bytes(original + b"\n# Unknown workspace bytes\n")
                        self.assertEqual([], review(), name)
                        path.write_bytes(original)
            unknown = root / "unknown-reference"
            shutil.copytree(reference, unknown)
            (unknown / "progress.py").write_text("import json\n")
            self.assertEqual([], fixture.progressive_judgments(data, unknown, "S2", root=root))


if __name__ == "__main__":
    unittest.main()
