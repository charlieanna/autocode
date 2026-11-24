"""A build test that cannot load on the original code proves the feature, as a loadable one does."""

import unittest

import autocode_verify as verify
import autocode_verify_judgment as judgment

from tests.test_verify import Project, isolated_python


class CollectionRepairFeedbackTests(unittest.TestCase):
    def test_a_test_importing_the_new_module_proves_it_without_a_repair(self):
        # The base already has a passing suite, so the new test is not a project's first suite. Its file
        # imports ``hello``, which only the change adds, so it cannot load on base: it did not pass there and
        # passes with the change, the feature's proof (decision 2026-11-24). Moving the import into the test
        # body proves the same feature by a test that ran and failed on base.
        project = Project(
            {
                "test_seed.py": (
                    "import unittest\n"
                    "class Seed(unittest.TestCase):\n"
                    "    def test_seed(self):\n"
                    "        self.assertTrue(True)\n"
                )
            }
        )
        self.addCleanup(project.close)
        python = isolated_python(self)
        project.write(
            {
                "hello.py": 'print("hello")\n',
                "test_hello.py": (
                    "import subprocess, sys, unittest\n"
                    "import hello\n"
                    "class TestHello(unittest.TestCase):\n"
                    "    def test_cli(self):\n"
                    '        result = subprocess.run([sys.executable, "hello.py"], capture_output=True, text=True)\n'
                    '        self.assertEqual(result.stdout, "hello\\n")\n'
                ),
            }
        )
        framework = verify.detect_framework(project.root, python=python)
        baseline = verify.baseline(
            project.root,
            project.base,
            project.evidence,
            framework=framework,
            suite_command=framework.suite,
            timeout=120,
        )

        def run():
            return verify.verify(
                project.root,
                project.base,
                project.evidence,
                framework=framework,
                base_suite=baseline,
                timeout=120,
                new_behavior=True,
            )

        unloaded = run()
        self.assertEqual(verify.PASS, unloaded["verdict"], unloaded["failures"] + unloaded["unverified"])
        self.assertEqual(["test_hello.TestHello.test_cli"], unloaded["fail_to_pass"], unloaded)
        self.assertEqual(["test_hello.TestHello.test_cli"], unloaded["not_run_on_base"], unloaded)
        self.assertIn(judgment.NEW_NAMES, unloaded["notes"])
        self.assertTrue(unloaded["checks"]["regression_on_base"]["results"]["collection_errors"], unloaded)
        test_file = project.root / "test_hello.py"
        test_file.write_text(test_file.read_text().replace("import hello\n", ""))
        repaired = run()
        self.assertIn("test_hello.TestHello.test_cli", repaired["fail_to_pass"], repaired)
        base = repaired["checks"]["regression_on_base"]["results"]
        self.assertIn("test_hello.TestHello.test_cli", base["failed"], repaired)
        self.assertEqual([], base["collection_errors"], repaired)
        self.assertNotIn(judgment.NEW_NAMES, repaired["notes"])
