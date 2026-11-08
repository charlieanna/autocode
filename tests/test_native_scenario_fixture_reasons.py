"""Native fixture reasons belong only to their exact scripted producer and proof."""

import copy
import hashlib
import json
import shutil
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from scenarios.harness import catalog, fake_changed_code
from scenarios.harness.project import overlay_paths

CASES = {
    "feature-node-stable-sort": (
        "src/records.js",
        [*range(7, 19), 20],
        "tests/score-order.test.js",
        (
            "test_c1_numeric_score_order_in_both_directions",
            "test_c2_stable_ties_and_caller_data_preserved",
        ),
    ),
    "feature-vitest-ledger-subtotals": (
        "src/ledger.ts",
        [*range(10, 15), *range(16, 29)],
        "tests/regression.test.ts",
        (
            "test_c1_account_groups_keep_exact_keys_and_zero_subtotals",
            "test_c2_large_cents_empty_inputs_and_input_order_are_preserved",
        ),
    ),
    "bugfix-node-interval-boundary": (
        "src/interval.js",
        [13, 14],
        "tests/interval-boundaries.test.js",
        "test_c1_touching_intervals_are_disjoint",
    ),
    "bugfix-vitest-half-open-bookings": (
        "src/bookings.ts",
        [19, 21],
        "tests/regression.test.ts",
        "test_c2_empty_intervals_do_not_conflict_or_mutate_inputs",
    ),
    "bugfix-go-interval-boundary": (
        "window/interval.go",
        [5],
        "window/regression_test.go",
        "TestC1ExcludesUpperEndpoint",
    ),
    "feature-java-page-count": (
        "src/main/java/example/PageCount.java",
        [10],
        "src/test/java/example/PageCountTest.java",
        "test_c1_partial_page_is_counted",
    ),
    "feature-go-first-package": (
        "cmd/retention/main.go",
        [1, 3, 4, 5, 7, 8, *range(10, 21)],
        "cmd/retention/main_test.go",
        "TestCLIStreamsAndExits",
    ),
}


@contextmanager
def fixture_case(case):
    scenario = catalog.load(case)
    source, lines, test, _ = CASES[case]
    with tempfile.TemporaryDirectory() as folder:
        base = Path(folder)
        root, reference = base / "project", base / "reference"
        shutil.copytree(scenario.seed, root)
        shutil.copytree(scenario.reference, reference)
        shutil.copytree(reference, root, dirs_exist_ok=True)
        pin = hashlib.sha256((root / source).read_bytes()).hexdigest()
        revision, context = "a" * 64, "b" * 64
        obligations = []
        sites = [(source, pin, line) for line in lines]
        if case == "feature-go-first-package":
            policy_pin = hashlib.sha256((root / "policy/policy.go").read_bytes()).hexdigest()
            sites.extend(("policy/policy.go", policy_pin, line) for line in range(4, 12))
        for site_source, site_pin, line in sites:
            identity = [revision, context, site_source, site_pin, line]
            gap = "changed-code:" + hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
            obligations.append(
                {
                    "gap_id": gap,
                    "path": site_source,
                    "sha256": site_pin,
                    "line": line,
                    "status": "reason_required",
                    "mutation_outcomes": [],
                }
            )
        if case == "feature-node-stable-sort":
            names = [
                "tests/score-order.test.js::test_c1_numeric_score_order_in_both_directions",
                "tests/score-order.test.js::test_c2_stable_ties_and_caller_data_preserved",
            ]
        elif case == "feature-vitest-ledger-subtotals":
            names = [
                "tests/regression.test.ts::test_c1_account_groups_keep_exact_keys_and_zero_subtotals",
                "tests/regression.test.ts::test_c2_large_cents_empty_inputs_and_input_order_are_preserved",
            ]
        elif case == "bugfix-node-interval-boundary":
            names = [
                "tests/interval-boundaries.test.js::test_c1_touching_intervals_are_disjoint",
                "tests/interval-boundaries.test.js::test_c2_empty_interval_never_overlaps",
            ]
        elif case == "bugfix-vitest-half-open-bookings":
            names = [
                "tests/regression.test.ts::test_c1_touching_bookings_do_not_conflict",
                "tests/regression.test.ts::test_c2_empty_intervals_do_not_conflict_or_mutate_inputs",
            ]
        elif case == "bugfix-go-interval-boundary":
            names = [
                "example.test/scenario/window::TestC1ExcludesUpperEndpoint",
                "example.test/scenario/window::TestC2EmptyInterval",
            ]
        elif case == "feature-go-first-package":
            names = [
                "example.test/scenario/policy::TestC1GermanRetention",
                "example.test/scenario/policy::TestC2ExceptionRetention",
                "example.test/scenario/cmd/retention::TestCLIStreamsAndExits",
            ]
        else:
            names = [
                "example.PageCountTest::test_c1_partial_page_is_counted",
                "example.PageCountTest::test_c2_exact_multiple_remains_two_pages",
            ]
        data = {
            "regression_proof": {
                "path": ".autocode/runs/current/regression/proof-current/verification.json",
                "verdict": "PASS",
                "source_revision": revision,
                "test_files": [test, "policy/policy_test.go"] if case == "feature-go-first-package" else [test],
                "source_files": [source, "policy/policy.go"] if case == "feature-go-first-package" else [source],
                "case_tests": {"C1": [names[0]], "C2": [names[1]]},
                "fail_to_pass": names[:1] if case == "feature-java-page-count" else names,
                "pass_to_pass": names[1:] if case == "feature-java-page-count" else [],
                "mutation_check": {"collected": False, "files": []},
                "changed_code_evidence": {
                    "source_revision": revision,
                    "proof_context": context,
                    "obligations": obligations,
                },
            }
        }
        producer = {
            "title": scenario.title,
            "reference": str(reference),
            "paths": overlay_paths(reference),
            "check": scenario.fake_check,
            "turn_paths": [],
        }
        yield root, reference, data, producer


class NativeScenarioFixtureReasonTests(unittest.TestCase):
    def judge(self, root, data, producer):
        return fake_changed_code.judgments(data, root, producer=producer)

    def test_exact_native_fixtures_get_only_their_listed_test_supported_line_reasons(self):
        for case, (_, _, _, assertion) in CASES.items():
            with self.subTest(case=case), fixture_case(case) as (root, _, data, producer):
                rows = self.judge(root, data, producer)
                offered = data["regression_proof"]["changed_code_evidence"]["obligations"]
                self.assertEqual(len(offered), len(rows))
                self.assertEqual(
                    {row["gap_id"] for row in data["regression_proof"]["changed_code_evidence"]["obligations"]},
                    {row["gap_id"] for row in rows},
                )
                for row in rows:
                    self.assertEqual("instrumentation_unavailable", row["reason_kind"])
                    assertions = (assertion,) if isinstance(assertion, str) else assertion
                    self.assertTrue(any(name in row["reason"] for name in assertions), row)
                    source = next(gap["path"] for gap in offered if gap["gap_id"] == row["gap_id"])
                    self.assertEqual([data["regression_proof"]["path"], source], row["evidence_refs"])
                self.assertEqual(
                    [], fake_changed_code.judgments(data, root), "workspace bytes do not establish a producer"
                )

    def test_unknown_producer_case_check_and_copy_scope_are_refused(self):
        for case in CASES:
            with fixture_case(case) as (root, _, data, producer):
                for change in (
                    {"title": "another case"},
                    {"reference": str(root)},
                    {"reference": str(root / "missing")},
                    {"paths": []},
                    {"paths": [[]]},
                    {"check": "echo PASS"},
                    {"turn_paths": [["other"]]},
                ):
                    with self.subTest(case=case, change=change):
                        self.assertEqual([], self.judge(root, data, {**producer, **change}))

    def test_every_scripted_source_and_test_pin_must_match_in_workspace_and_producer(self):
        for case in CASES:
            with fixture_case(case) as (root, reference, data, producer):
                for base in (root, reference):
                    for name in sorted(overlay_paths(base)):
                        path = base / name
                        owned_sources = {
                            row["path"] for row in data["regression_proof"]["changed_code_evidence"]["obligations"]
                        }
                        if path.suffix not in (".js", ".ts", ".go", ".java", ".cjs") or (
                            base == root and "test" not in name and name not in owned_sources
                        ):
                            continue
                        original = path.read_bytes()
                        path.write_bytes(original + b"\n// changed or weakened fixture\n")
                        with self.subTest(case=case, root=base.name, path=name):
                            self.assertEqual([], self.judge(root, data, producer))
                        path.write_bytes(original)

    def test_wrong_gap_source_line_context_and_all_observed_mutants_are_refused(self):
        for case in CASES:
            with fixture_case(case) as (root, _, data, producer):
                changes = [
                    {"gap_id": "changed-code:" + "0" * 64},
                    {"path": "unknown.go"},
                    {"sha256": "0" * 64},
                    {"line": True},
                    {"line": 999},
                    {"status": "tested"},
                    *(
                        {"mutation_outcomes": value}
                        for value in (["killed"], ["survived"], ["not_run"], ["incomplete"], None)
                    ),
                ]
                for change in changes:
                    current = copy.deepcopy(data)
                    for row in current["regression_proof"]["changed_code_evidence"]["obligations"]:
                        row.update(change)
                    with self.subTest(case=case, change=change):
                        self.assertEqual([], self.judge(root, current, producer))
                for field in ("source_revision", "proof_context"):
                    current = copy.deepcopy(data)
                    current["regression_proof"]["changed_code_evidence"][field] = "c" * 64
                    with self.subTest(case=case, stale=field):
                        self.assertEqual([], self.judge(root, current, producer))
                current = copy.deepcopy(data)
                gaps = current["regression_proof"]["changed_code_evidence"]["obligations"]
                gaps.extend(copy.deepcopy(gaps))
                self.assertEqual([], self.judge(root, current, producer), "duplicate gaps have no unique binding")
                for change in (
                    {"path": ""},
                    {"verdict": "FAIL"},
                    {"source_revision": "c" * 64},
                    {"test_files": []},
                    {"source_files": []},
                    {"case_tests": {}},
                    {"fail_to_pass": []},
                    {"mutation_check": {"collected": True, "files": []}},
                    {"mutation_check": {"collected": False, "files": [{"mutants": [{"outcome": "killed"}]}]}},
                ):
                    current = copy.deepcopy(data)
                    current["regression_proof"].update(change)
                    with self.subTest(case=case, proof=change):
                        self.assertEqual([], self.judge(root, current, producer))

    def test_leaf_and_ancestor_alias_links_and_unreadable_files_are_refused(self):
        for case in CASES:
            with fixture_case(case) as (root, reference, data, producer):
                source = CASES[case][0]
                for base, other in ((root, reference), (reference, root)):
                    path = base / source
                    original = path.read_bytes()
                    path.unlink()
                    path.symlink_to(other / source)
                    with self.subTest(case=case, alias=base.name):
                        self.assertEqual([], self.judge(root, data, producer))
                    path.unlink()
                    path.write_bytes(original)
                    ancestor = base / Path(source).parts[0]
                    saved = ancestor.with_name("owned-" + ancestor.name)
                    ancestor.rename(saved)
                    ancestor.symlink_to(saved, target_is_directory=True)
                    with self.subTest(case=case, ancestor=base.name):
                        self.assertEqual([], self.judge(root, data, producer))
                    ancestor.unlink()
                    saved.rename(ancestor)
                with patch.object(Path, "read_bytes", side_effect=PermissionError("unreadable fixture")):
                    self.assertEqual([], self.judge(root, data, producer))

    def test_boundary_aliases_and_non_object_obligations_cannot_claim_fixture_ownership(self):
        for case in CASES:
            with fixture_case(case) as (root, reference, data, producer):
                root_alias = root.with_name("linked-project")
                root_alias.symlink_to(root, target_is_directory=True)
                self.assertEqual([], self.judge(root_alias, data, producer))
                reference_alias = reference.with_name("linked-reference")
                reference_alias.symlink_to(reference, target_is_directory=True)
                self.assertEqual([], self.judge(root, data, {**producer, "reference": str(reference_alias)}))
                current = copy.deepcopy(data)
                current["regression_proof"]["changed_code_evidence"]["obligations"] = [None]
                self.assertEqual([], self.judge(root, current, producer))


if __name__ == "__main__":
    unittest.main()
