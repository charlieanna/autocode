"""Direct tests for extracted verification domain modules and narrower --changed selection (#3468)."""

from __future__ import annotations

import functools
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import autocode_suite_boundary as suite_boundary
import autocode_suite_definition_policy as definition_policy
import autocode_test_results as test_results
import autocode_verify as verify
import run_suite
from autocode_test_framework import Framework


class TestResultsDomainTests(unittest.TestCase):
    def test_classification_and_product_files(self):
        self.assertTrue(test_results.is_test_path("tests/test_sample.py"))
        self.assertFalse(test_results.is_test_path("src/sample.py"))
        self.assertTrue(test_results.is_code_path("src/sample.py"))
        self.assertFalse(test_results.is_code_path("README.md"))
        changes = {
            "src/app.py": "modified",
            "tests/test_app.py": "modified",
            "node_modules/pkg/index.js": "added",
            "src/deleted.py": "deleted",
            "README.md": "modified",
        }
        self.assertEqual([("src/app.py", "modified")], test_results.product_files(changes))
        self.assertIs(verify.product_files, test_results.product_files)

    def test_removed_python_tests_with_stub_git_and_replaced_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tests").mkdir()
            (root / "tests" / "test_mod.py").write_text("def test_kept():\n    pass\n")
            removed = test_results.removed_python_tests(
                root,
                "HEAD",
                {"tests/test_mod.py": "modified"},
                git=lambda _ws, *_args, **_kw: (
                    "def test_kept():\n    pass\ndef test_old():\n    pass\nasync def test_replaced():\n    pass\n"
                ),
                roles=SimpleNamespace(protected=lambda path: path == "tests/test_mod.py"),
                replaced=lambda name: name == "tests/test_mod.py::test_replaced",
            )
        self.assertEqual(["tests/test_mod.py::test_old"], removed)

    def test_with_results_expects_results_and_parsers(self):
        pytest_fw = Framework("pytest", "python -m pytest")
        self.assertTrue(test_results.expects_results(pytest_fw, "python -m pytest"))
        self.assertIn("--junitxml=", test_results.with_results(pytest_fw, "python -m pytest", Path("/tmp/r.xml")))

        go_fw = Framework("go", "go test ./...")
        self.assertTrue(test_results.expects_results(go_fw, "go test ./..."))
        self.assertEqual("go test -json ./...", test_results.with_results(go_fw, "go test ./...", Path("/tmp/unused")))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            xml = root / "pytest.junit.xml"
            xml.write_text(
                '<testsuite><testcase classname="pkg.test_x" name="test_ok"/>'
                '<testcase classname="pkg.test_x" name="test_bad"><failure message="boom">assert False</failure></testcase>'
                "</testsuite>"
            )
            parsed = test_results.per_test_results(pytest_fw, {}, xml, tree=root)
            self.assertEqual(["pkg.test_x::test_ok"], parsed["passed"])
            self.assertEqual(["pkg.test_x::test_bad"], parsed["failed"])

            ut_log = root / "ut.log"
            ut_log.write_text("test_alpha (tests.test_x.Case) ... ok\n\nRan 1 test in 0.01s\n\nOK\n")
            ut_fw = Framework("unittest", "python -m unittest discover -v")
            ut_parsed = test_results.per_test_results(ut_fw, {"output": str(ut_log)}, root / "none.xml", tree=root)
            self.assertEqual(["tests.test_x.Case::test_alpha"], ut_parsed["passed"])


class SuiteBoundaryDomainTests(unittest.TestCase):
    def test_package_scripts_and_analyze_without_git(self):
        self.assertEqual(
            {"test": "node tests/run.js"}, suite_boundary.package_scripts('{"scripts": {"test": "node tests/run.js"}}')
        )
        self.assertEqual("test", suite_boundary.suite_package_script("npm test"))
        self.assertEqual("unit", suite_boundary.suite_package_script("pnpm run unit"))
        self.assertIsNone(suite_boundary.suite_package_script("npm test && rm -rf /"))

        files = {
            "package.json": json.dumps({"scripts": {"test": "node tests/runner.js"}}),
            "tests/runner.js": "require('fs').readFileSync('tests/helper.js');\n",
            "tests/helper.js": "// runner helper\n",
            "tests/unit.test.js": "const app = require('../src/app.js');\n",
            "src/app.js": "exports.ok = true;\n",
        }
        entries = {path: ("100644", f"obj-{i}") for i, path in enumerate(files)}
        found = suite_boundary.analyze(
            "unused-workspace",
            entries,
            files.__getitem__,
            set(),
            "npm test",
            is_test=lambda path: path == "tests/unit.test.js",
        )
        self.assertIsInstance(found, definition_policy.Definition)
        self.assertEqual("", found.boundary)
        self.assertIn("tests/runner.js", found.paths)
        self.assertIn("tests/helper.js", found.paths)
        self.assertNotIn("src/app.js", found.paths)

    def test_computed_cd_target_leaves_boundary_unestablished(self):
        files = {"tests/run.sh": "echo ok\n"}
        entries = {"tests/run.sh": ("100644", "obj-0")}
        found = suite_boundary.analyze(
            "unused-workspace",
            entries,
            files.__getitem__,
            set(),
            "cd $TARGET && sh tests/run.sh",
            is_test=lambda path: path == "tests/run.sh",
        )
        self.assertIn("computed", found.boundary)


class ChangedSelectionBlastRadiusTests(unittest.TestCase):
    def test_extracted_domain_modules_select_far_fewer_fast_tests_than_autocode_verify(self):
        tests_dir = Path(__file__).resolve().parent
        sources = {
            f"tests.{path.stem}": path.read_text(encoding="utf-8", errors="ignore")
            for path in sorted(tests_dir.glob("test_*.py"))
        }
        slow = json.loads((tests_dir / "suite_slow.json").read_text(encoding="utf-8"))

        with mock.patch.object(run_suite, "imported_names", side_effect=functools.cache(run_suite.imported_names)):
            verify_selected, _ = run_suite.drop_slow(
                run_suite.select_tests(["tools/autocode_verify.py"], sources), slow
            )
            results_selected, _ = run_suite.drop_slow(
                run_suite.select_tests(["tools/autocode_test_results.py"], sources), slow
            )
            boundary_selected, _ = run_suite.drop_slow(
                run_suite.select_tests(["tools/autocode_suite_boundary.py"], sources), slow
            )

        self.assertIn("tests.test_verify_domain_split", results_selected)
        self.assertIn("tests.test_verify_domain_split", boundary_selected)
        self.assertLess(len(results_selected), 25)
        self.assertLess(len(boundary_selected), 25)
        self.assertGreater(len(verify_selected), 80)
        self.assertLess(len(results_selected), len(verify_selected) // 3)
        self.assertLess(len(boundary_selected), len(verify_selected) // 3)


if __name__ == "__main__":
    unittest.main()
