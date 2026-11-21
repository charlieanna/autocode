"""A named fail-to-pass proof needs a named test that actually failed on base."""

import importlib.util
import shlex
import shutil
import sys
import unittest

import autocode_verify as verify
import autocode_verify_judgment as judgment

from tests.test_verify import Project


class CompiledFeatureEvidenceTests(unittest.TestCase):
    def test_first_suite_absence_requires_matching_completed_baseline(self):
        receipt = {"results_expected": True, "results": None, "exit_code": 1, "timed_out": False}
        baseline = {"base": "pinned", "command": "suite", "receipt": receipt}
        self.assertTrue(judgment.first_suite_absent(baseline, "pinned", "suite"))
        self.assertFalse(judgment.first_suite_absent(baseline, "other", "suite"))
        self.assertFalse(judgment.first_suite_absent(baseline, "pinned", "other"))
        self.assertFalse(judgment.first_suite_absent({**baseline, "base": None}, None, "suite"))
        self.assertFalse(judgment.first_suite_absent({**baseline, "command": None}, "pinned", None))
        self.assertFalse(
            judgment.first_suite_absent({**baseline, "receipt": {**receipt, "timed_out": True}}, "pinned", "suite")
        )
        self.assertFalse(
            judgment.first_suite_absent(
                {**baseline, "receipt": {**receipt, "supervision_errors": []}}, "pinned", "suite"
            )
        )

    def test_go_build_failure_is_bound_to_its_exact_package(self):
        base = {
            "passed": [],
            "failed": ["example/a::[build failed]"],
            "skipped": [],
            "collection_errors": ["example/a::[build failed]"],
        }
        passed = {"example/a::TestNew", "example/b::TestOther"}
        self.assertEqual({"example/a::TestNew"}, judgment.compiled_feature_tests("go", base, passed, lambda _: True))

    def test_cargo_requires_build_failure_and_excludes_skips_and_unchanged(self):
        base = {
            "passed": [],
            "failed": ['pager (test "pages")::[build failed]'],
            "skipped": ["tests/pages.rs::skipped"],
            "collection_errors": ['pager (test "pages")::[build failed]'],
        }
        passed = {"tests/pages.rs::new", "tests/pages.rs::skipped", "tests/pages.rs::unchanged"}
        changed = lambda name: not name.endswith("::unchanged")
        self.assertEqual({"tests/pages.rs::new"}, judgment.compiled_feature_tests("cargo", base, passed, changed))
        base["collection_errors"] = ["tests/pages.rs::[collection]"]
        self.assertEqual(set(), judgment.compiled_feature_tests("cargo", base, passed, changed))

    def test_compiled_exception_requires_a_nonempty_failed_build_marker(self):
        test = {"tests/pages.rs::new"}
        changed = lambda _: True
        base = {
            "passed": [],
            "failed": [],
            "skipped": [],
            "collection_errors": ['pager (test "pages")::[build failed]'],
        }
        self.assertEqual(set(), judgment.compiled_feature_tests("cargo", base, test, changed))
        base["failed"] = ["::[build failed]"]
        base["collection_errors"] = ["::[build failed]"]
        self.assertEqual(set(), judgment.compiled_feature_tests("cargo", base, test, changed))


@unittest.skipUnless(shutil.which("go"), "Go is required for the real cross-package proof")
class GoCompiledProofTests(unittest.TestCase):
    def test_unrelated_build_failure_does_not_promote_a_skipped_test(self):
        project = Project(
            {
                "go.mod": "module example\n\ngo 1.20\n",
                "a/a.go": "package a\n",
                "b/b.go": "package b\nconst Enabled = false\n",
            }
        )
        self.addCleanup(project.close)
        command = "go test ./..."
        framework = verify.Framework("go", command)
        base = verify.baseline(
            project.root, project.base, project.evidence, framework=framework, suite_command=command, timeout=120
        )
        project.write(
            {
                "a/a.go": "package a\nfunc Feature() int { return 42 }\n",
                "a/a_test.go": 'package a\nimport "testing"\nfunc TestFeature(t *testing.T) { if Feature() != 42 { t.Fail() } }\n',
                "b/b.go": "package b\nconst Enabled = true\n",
                "b/b_test.go": 'package b\nimport "testing"\nfunc TestSkipped(t *testing.T) { if !Enabled { t.Skip("absent") } }\n',
            }
        )
        proof = verify.verify(
            project.root,
            project.base,
            project.evidence,
            framework=framework,
            base_suite=base,
            suite_command=command,
            regression_command=command,
            timeout=120,
            new_behavior=True,
        )
        original = proof["checks"]["regression_on_base"]["results"]
        self.assertIn("example/a::[build failed]", original["collection_errors"])
        self.assertIn("example/b::TestSkipped", original["skipped"])
        self.assertIn("example/a::TestFeature", proof["fail_to_pass"])
        self.assertNotIn("example/b::TestSkipped", proof["fail_to_pass"])
        self.assertIn("example/b::TestSkipped", proof["not_run_on_base"])


@unittest.skipUnless(importlib.util.find_spec("pytest"), "pytest is required for the real named-result control")
class NamedBaseProofTests(unittest.TestCase):
    def proof(self, test_source, *, new_behavior=True):
        project = Project(
            {
                "app.py": "def old():\n    return 1\n",
                "test_existing.py": "import app\ndef test_old():\n    assert app.old() == 1\n",
            }
        )
        self.addCleanup(project.close)
        command = shlex.join([sys.executable, "-m", "pytest", "-q"])
        framework = verify.Framework("pytest", command, python=sys.executable)
        base = verify.baseline(
            project.root, project.base, project.evidence, framework=framework, suite_command=command, timeout=120
        )
        project.write(
            {"app.py": "def old():\n    return 1\ndef new():\n    return 42\n", "test_feature.py": test_source}
        )
        return verify.verify(
            project.root,
            project.base,
            project.evidence,
            framework=framework,
            base_suite=base,
            timeout=120,
            new_behavior=new_behavior,
        )

    def test_base_skip_is_not_a_named_failure(self):
        proof = self.proof(
            "import app, pytest\ndef test_new():\n"
            "    if not hasattr(app, 'new'): pytest.skip('new feature absent')\n"
            "    assert app.new() == 42\n"
        )
        original = proof["checks"]["regression_on_base"]["results"]
        self.assertEqual(["test_feature::test_new"], original["skipped"])
        self.assertEqual([], original["failed"])
        self.assertEqual([], proof["fail_to_pass"], proof)
        self.assertNotEqual(verify.PASS, proof["verdict"], proof)

    def test_base_missing_test_is_not_a_named_failure(self):
        proof = self.proof(
            "import app\ndef test_present():\n    assert app.old() == 1\n"
            "if hasattr(app, 'new'):\n    def test_new():\n        assert app.new() == 42\n"
        )
        original = proof["checks"]["regression_on_base"]["results"]
        self.assertEqual(["test_feature::test_present"], original["passed"])
        self.assertEqual([], original["failed"])
        self.assertEqual([], proof["fail_to_pass"], proof)
        self.assertNotEqual(verify.PASS, proof["verdict"], proof)

    def test_real_named_failure_passes_for_feature_and_bugfix(self):
        test = "import app\ndef test_new():\n    assert getattr(app, 'new', lambda: 0)() == 42\n"
        for new_behavior in (True, False):
            with self.subTest(new_behavior=new_behavior):
                proof = self.proof(test, new_behavior=new_behavior)
                self.assertEqual(["test_feature::test_new"], proof["checks"]["regression_on_base"]["results"]["failed"])
                self.assertEqual(["test_feature::test_new"], proof["fail_to_pass"], proof)
                self.assertEqual(verify.PASS, proof["verdict"], proof)


if __name__ == "__main__":
    unittest.main()
