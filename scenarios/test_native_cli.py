"""Native scenarios complete through the real CLI with current named proof.

Providers are scripted; Go, node:test and locked Vitest tests execute normally.
Each method is an independent scenario so the harness gate can schedule them
in separate processes. This test reads only result.json and the public status
view, never the runner's private saved state.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness.processes import run_cli

REPO = Path(__file__).resolve().parents[1]


class NativeScenarioCliTests(unittest.TestCase):
    def run_native(self, scenario_id, runner, names, *, plan_approved=True):
        required = ("go",) if runner == "go" else ("node", "npm")
        missing = [tool for tool in required if shutil.which(tool) is None]
        if missing:
            message = f"{scenario_id} requires native tools: {', '.join(missing)}"
            if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
                self.fail(message)
            self.skipTest(message)
        with tempfile.TemporaryDirectory(prefix=f"native-cli-{scenario_id}-") as directory:
            root = Path(directory)
            out = root / "results"
            env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
                   "AUTOCODE_HOME": str(root / "registry")}
            # Keep sys.executable lexical: resolving a venv's Python symlink
            # changes its environment and can drop the required psutil package.
            command = [sys.executable, str(REPO / "scenarios/run.py"), "run",
                       scenario_id, "--fake", "--out", str(out),
                       "--timeout-minutes", "10"]
            completed = run_cli(command, env=env, cwd=REPO, timeout=720)
            files = list(out.glob("*/result.json"))
            self.assertEqual(1, len(files), completed.stdout + completed.stderr)
            result = json.loads(files[0].read_text())
            # A launch can fail before a run directory exists. Preserve its
            # actual result and CLI error before inspecting completion evidence.
            diagnostic = (f"{scenario_id}: harness_error={result.get('harness_error')!r}\n"
                          + json.dumps(result, indent=2) + "\n"
                          + completed.stdout + completed.stderr)
            self.assertEqual(0, completed.returncode, diagnostic)
            self.assertEqual(scenario_id, result["scenario"])
            self.assertEqual("PASS", result["verdict"], diagnostic)
            self.assertEqual("TASK_COMPLETE", result["runner_status"], diagnostic)
            self.assertTrue(result["oracle_passed"], result)
            self.assertEqual("", result.get("oracle_error"), result)
            checks = {row["name"]: row for row in result["checks"]}
            self.assertTrue(checks, result)
            self.assertEqual([], [name for name, row in checks.items() if not row["ok"]], checks)
            required_checks = ("project_tests_pass", "hidden_tests_pass", "existing_tests_kept",
                               "required_case_tests", "new_tests_fail_on_original_code",
                               "named_regression_proof")
            if plan_approved:
                required_checks += ("plan_reviewed", "plan_approved_by_user")
            for name in required_checks:
                self.assertIn(name, checks,
                              f"{scenario_id} oracle must report {name}; "
                              f"plan approval required={plan_approved}, observed={sorted(checks)}")
                self.assertTrue(checks[name]["ok"], checks[name])

            # Inspect the public CLI contract against the current checkout,
            # rather than trusting the harness or a provider's completion text.
            self.assertTrue(result.get("run_dir"), diagnostic)
            self.assertTrue(result.get("evidence"), diagnostic)
            status = run_cli([sys.executable, str(REPO / "tools/autocode.py"),
                              "--workspace", str(Path(result["evidence"]) / "project"),
                              "--run-dir", result["run_dir"], "--status", "--inspect-evidence"],
                             env=env, cwd=REPO, timeout=60)
            self.assertEqual(0, status.returncode, status.stdout + status.stderr)
            inspection = json.loads(status.stdout)
            view = inspection["view"]
            self.assertIs(inspection["completion_current"], True, inspection)
            self.assertEqual("TASK_COMPLETE", view["status"], view)
            self.assertIs(view["done"], True, view)
            self.assertIsNone(view["needs"], view)
            evidence = view["evidence"]
            proof = evidence["regression_proof"]
            self.assertEqual("PASS", proof["verdict"], proof)
            self.assertEqual([], proof["failures"], proof)
            self.assertEqual([], proof["unverified"], proof)
            source = proof["source_revision"]
            self.assertRegex(source, r"^[a-f0-9]{64}$")
            self.assertEqual(source, evidence["validator_source_revision"], evidence)
            self.assertEqual("current", view["verification"]["freshness"], view["verification"])
            self.assertEqual(source, view["verification"]["inspected_source_revision"], view["verification"])

            case_tests = proof["case_tests"]
            self.assertGreaterEqual(len(case_tests), 2, proof)
            self.assertTrue(all(isinstance(rows, list) and rows for rows in case_tests.values()), proof)
            matched = {identity for rows in case_tests.values() for identity in rows}
            flipped = set(proof["fail_to_pass"])
            expected = set()
            for name in names:
                concrete = {identity for identity in matched if identity.endswith("::" + name)}
                self.assertTrue(concrete, f"{name} lacks a native case identity: {proof}")
                self.assertTrue(concrete <= flipped, f"{name} has no original-source comparison: {proof}")
                expected.update(concrete)
            # The first Go package intentionally has no runnable Go project on
            # base; its explicit no-project comparison still contributes named
            # fail_to_pass identities. Other scenarios execute the base cases.
            commands = proof["commands"]
            self.assertTrue(commands["suite"], proof)
            regression = commands["regression"]
            native = {"go": "go test", "node": "node --test", "vitest": "vitest"}[runner]
            self.assertIn(native, regression, proof)

            replay = evidence["check_replay"]
            self.assertEqual("PASS", replay["verdict"], replay)
            self.assertEqual(source, replay["source_revision"], replay)
            replayed = set()
            self.assertTrue(replay["checks"], replay)
            self.assertTrue(any(native in check["command"] for check in replay["checks"]), replay)
            for check in replay["checks"]:
                self.assertEqual(0, check["exit_code"], check)
                self.assertIs(check["timed_out"], False, check)
                results = check.get("results")
                if results is None:
                    continue  # Auxiliary commands do not report native cases.
                self.assertIs(results["complete"], True, check)
                self.assertEqual([], results["failed"], check)
                self.assertEqual([], results["collection_errors"], check)
                replayed.update(results["passed"])
            # The public Vitest replay contract records the command receipt,
            # not parsed cases. Its native identities and original-code failures
            # are proved above, and the catalog oracle independently executes
            # every required case. Require typed replay identities whenever the
            # runner exposes them (including a future Vitest collector).
            if runner != "vitest" or replayed:
                self.assertTrue(expected <= replayed,
                                f"The independent replay did not execute the proven cases: {expected - replayed}")

    def test_bugfix_node_interval_boundary(self):
        self.run_native("bugfix-node-interval-boundary", "node", (
            "test_c1_touching_intervals_are_disjoint", "test_c2_empty_interval_never_overlaps"))

    def test_feature_node_stable_sort(self):
        self.run_native("feature-node-stable-sort", "node", (
            "test_c1_numeric_score_order_in_both_directions", "test_c2_stable_ties_and_caller_data_preserved"))

    def test_bugfix_go_interval_boundary(self):
        self.run_native("bugfix-go-interval-boundary", "go", (
            "TestC1ExcludesUpperEndpoint", "TestC2EmptyInterval"), plan_approved=False)

    def test_feature_go_first_package(self):
        self.run_native("feature-go-first-package", "go", (
            "TestC1GermanRetention", "TestC2ExceptionRetention"))

    def test_bugfix_vitest_half_open_bookings(self):
        self.run_native("bugfix-vitest-half-open-bookings", "vitest", (
            "test_c1_touching_bookings_do_not_conflict", "test_c2_empty_intervals_do_not_conflict_or_mutate_inputs"))

    def test_feature_vitest_ledger_subtotals(self):
        self.run_native("feature-vitest-ledger-subtotals", "vitest", (
            "test_c1_account_groups_keep_exact_keys_and_zero_subtotals",
            "test_c2_large_cents_empty_inputs_and_input_order_are_preserved"))


if __name__ == "__main__":
    unittest.main()
