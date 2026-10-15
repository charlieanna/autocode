"""Review mutation evidence through the real CLI, with a scripted provider.

The original PR tests pass while bypassing its public lookup. The Reviewer's
separate F1 regression must still fail on that PR and pass once it is fixed.
No runtime modules or private run state are imported or read here.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import catalog
from harness.oracle import apply_patch
from harness.oracle import run as run_command
from harness.processes import run_cli
from harness.project import materialize

REPO = Path(__file__).resolve().parents[1]
PR_TESTS = {
    "tests.test_policies.AtPolicyTests.test_at_allows_one_retry",
    "tests.test_policies.AtPolicyTests.test_at_retries_only_server_busy",
}
FINDING_TEST = "review.tests.test_at_policy.AtPolicyThroughTheClient.test_f1_policy_for_resolves_at"
LOOKUP_GUARD = """import unittest

from regclient.policies import POLICIES, policy_for


class LookupGuard(unittest.TestCase):
    def test_de_policy_uses_public_lookup(self):
        self.assertEqual(POLICIES["de"], policy_for("DE"))
"""


class ReviewMutationScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenario = catalog.load("review-vacuous-tests")

    def test_original_pr_tests_report_a_survivor_without_replacing_finding_proof(self):
        with tempfile.TemporaryDirectory(prefix="review-mutation-cli-") as directory:
            root = Path(directory)
            out = root / "results"
            env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "AUTOCODE_HOME": str(root / "registry")}
            completed = run_cli(
                [
                    sys.executable,
                    str(REPO / "scenarios/run.py"),
                    "run",
                    self.scenario.id,
                    "--fake",
                    "--out",
                    str(out),
                    "--timeout-minutes",
                    "10",
                ],
                env=env,
                cwd=REPO,
                timeout=720,
            )
            results = list(out.glob("*/result.json"))
            self.assertEqual(1, len(results), completed.stdout + completed.stderr)
            result = json.loads(results[0].read_text())
            diagnostic = completed.stdout + completed.stderr + json.dumps(result, indent=2)
            self.assertEqual(0, completed.returncode, diagnostic)
            self.assertEqual("PASS", result["verdict"], diagnostic)
            self.assertEqual("TASK_COMPLETE", result["runner_status"], diagnostic)
            self.assertTrue(result["oracle_passed"], diagnostic)
            checks = {row["name"]: row for row in result["checks"]}
            self.assertTrue(checks, diagnostic)
            self.assertEqual([], [name for name, row in checks.items() if not row["ok"]], diagnostic)
            for name in (
                "finds_case_mismatch",
                "targeted_test_fails_on_patched_code",
                "targeted_test_passes_once_fixed",
                "canonical evidence: task",
            ):
                self.assertIn(name, checks, diagnostic)

            project = Path(result["evidence"]) / "project"
            inspected = run_cli(
                [
                    sys.executable,
                    str(REPO / "tools/autocode.py"),
                    "--workspace",
                    str(project),
                    "--run-dir",
                    result["run_dir"],
                    "--status",
                    "--inspect-evidence",
                ],
                env=env,
                cwd=REPO,
                timeout=60,
            )
            self.assertEqual(0, inspected.returncode, inspected.stdout + inspected.stderr)
            view = json.loads(inspected.stdout)["view"]
            self.assertEqual("TASK_COMPLETE", view["status"], view)
            self.assertTrue(view["done"], view)
            self.assertEqual("review", view["workflow"], view)
            self.assertIsNone(view["needs"], view)
            workflow = view["evidence"]["workflow_result"]
            self.assertEqual("request_changes", workflow["outcome"], workflow)
            self.assertEqual("review/findings.json", workflow["artifact"], workflow)
            findings_raw = (project / workflow["artifact"]).read_bytes()
            self.assertEqual(workflow["artifact_sha256"], hashlib.sha256(findings_raw).hexdigest())
            findings = json.loads(findings_raw)
            f1 = next(row for row in findings["findings"] if row["id"] == "F1")
            self.assertEqual("blocking", f1["severity"], f1)
            self.assertIn(FINDING_TEST, f1["proven_by"], f1)

            mutation = view["evidence"]["mutation_check"]
            self.assertEqual("reviewed_patch_tests", mutation["subject"], mutation)
            self.assertTrue(mutation["collected"], mutation)
            self.assertEqual("pr-197.patch", mutation["patch_path"], mutation)
            self.assertEqual(
                hashlib.sha256((project / "pr-197.patch").read_bytes()).hexdigest(), mutation["patch_sha256"]
            )
            self.assertEqual(["tests/test_policies.py"], mutation["selected_files"], mutation)
            self.assertEqual(PR_TESTS, set(mutation["selected_tests"]), mutation)
            product = next(row for row in mutation["files"] if row["path"] == "regclient/policies.py")
            returned = [row for row in product["mutants"] if row["operator"] == "constant_return"]
            self.assertEqual(1, len(returned), mutation)
            mutant = returned[0]
            self.assertIn("POLICIES.get(tld.lower(), DEFAULT)", mutant["before"])
            self.assertEqual("None", mutant["after"])
            self.assertEqual("survived", mutant["outcome"], mutant)
            self.assertEqual(PR_TESTS, set(mutant["survived_tests"]), mutant)
            fixed_tree = ast.parse((self.scenario.dir / "hidden/fix/regclient/policies.py").read_text())
            lookup = next(
                node for node in fixed_tree.body if isinstance(node, ast.FunctionDef) and node.name == "policy_for"
            )
            self.assertEqual(lookup.body[0].lineno, mutant["line"], mutant)
            self.assertEqual(PR_TESTS, set(mutation["survivors"]), mutation)
            for identity, survivors in mutation["survivors"].items():
                self.assertTrue(survivors, mutation)
                self.assertTrue(
                    any(
                        row["test"] == identity
                        and row["path"] == "regclient/policies.py"
                        and row["operator"] == "constant_return"
                        and row["after"] == "None"
                        for row in survivors
                    ),
                    survivors,
                )
            self.assertEqual(mutation, workflow["mutation_check"], workflow)

            report = view["evidence_report"]
            self.assertIn(report["availability"], ("recorded", "current"), report)
            self.assertEqual(mutation, report["document"]["workflow_result"]["mutation_check"])
            self.assertIsNone(report["document"]["regression_proof"])
            for kind in ("json", "markdown"):
                raw = Path(report[kind + "_path"]).read_bytes()
                self.assertEqual(report[kind + "_sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(report["document"], json.loads(Path(report["json_path"]).read_bytes()))
            self.assertEqual(report["markdown"], Path(report["markdown_path"]).read_text())
            headings = [line.lstrip("#").strip() for line in report["markdown"].splitlines() if line.startswith("#")]
            self.assertIn("Review test mutation check", headings)
            for identity in PR_TESTS:
                self.assertIn(identity, report["markdown"])
            self.assertEqual(
                (self.scenario.seed / "regclient/policies.py").read_bytes(),
                (project / "regclient/policies.py").read_bytes(),
                "Review or mutation collection changed the original product workspace",
            )

    def test_direct_pr_tests_miss_a_wrong_return_that_a_public_lookup_guard_rejects(self):
        with tempfile.TemporaryDirectory(prefix="review-mutation-fixture-") as directory:
            project = materialize(self.scenario.seed, Path(directory) / "project")
            applied = apply_patch(project, project / "pr-197.patch")
            self.assertEqual(0, applied.returncode, applied.stdout + applied.stderr)
            (project / "tests/test_lookup_guard.py").write_text(LOOKUP_GUARD)

            def execute(module):
                return run_command([sys.executable, "-B", "-m", "unittest", "-v", module], project, timeout=30)

            weak = execute("tests.test_policies")
            self.assertEqual(0, weak.returncode, weak.stdout + weak.stderr)
            self.assertIn("Ran 2 tests", weak.stdout + weak.stderr)
            strong = execute("tests.test_lookup_guard")
            self.assertEqual(0, strong.returncode, strong.stdout + strong.stderr)
            self.assertIn("Ran 1 test", strong.stdout + strong.stderr)

            product = project / "regclient/policies.py"
            original = product.read_text()
            self.assertEqual(1, original.count("return POLICIES.get(tld.lower(), DEFAULT)"))
            product.write_text(original.replace("return POLICIES.get(tld.lower(), DEFAULT)", "return None"))
            weak = execute("tests.test_policies")
            self.assertEqual(0, weak.returncode, weak.stdout + weak.stderr)
            self.assertIn("Ran 2 tests", weak.stdout + weak.stderr)
            strong = execute("tests.test_lookup_guard")
            output = strong.stdout + strong.stderr
            self.assertNotEqual(0, strong.returncode, output)
            self.assertIn("FAIL: test_de_policy_uses_public_lookup", output)
            self.assertIn("FAILED (failures=1)", output)
            self.assertNotIn("ERROR:", output)


if __name__ == "__main__":
    unittest.main()
