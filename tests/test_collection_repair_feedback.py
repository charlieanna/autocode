"""An uncollected build test receives feedback and can earn ordinary proof."""

import unittest

import autocode_proof_findings as proof_findings
import autocode_verify as verify

from tests.test_verify import Project, isolated_python


class CollectionRepairFeedbackTests(unittest.TestCase):
    def test_builder_can_repair_uncollected_tests_without_relaxing_the_gate(self):
        # The base already has a passing suite, so the new test is not a project's first suite: that
        # case counts an uncollected test as first-suite proof under its own preservation judgment
        # (tests.test_verify.PreservationEvidenceCase). Here the test must load and fail on base.
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

        held = run()
        self.assertEqual([], held["fail_to_pass"], held)
        self.assertIn("test_hello", str(held["failures"]))
        self.assertIn("collect", str(held["failures"]).lower())
        self.assertIn("load", str(held["failures"]).lower())
        state = {"regression_proof": {**held, "path": "/run/proof.json"}}
        finding = proof_findings.reconcile(
            state, held["source_revision"], required=True, proved=False, reason="; ".join(held["failures"])
        )
        self.assertIn("test_hello", finding["finding"])
        self.assertIn("load", finding["finding"])
        self.assertEqual("open", finding["status"])
        # An explicit Builder repair removes a module-level import unrelated to
        # the CLI assertion; the runner never rewrites or assumes this repair.
        test_file = project.root / "test_hello.py"
        test_file.write_text(test_file.read_text().replace("import hello\n", ""))
        repaired = run()
        self.assertIn("test_hello.TestHello.test_cli", repaired["fail_to_pass"], repaired)
        base = repaired["checks"]["regression_on_base"]["results"]
        self.assertIn("test_hello.TestHello.test_cli", base["failed"], repaired)
        self.assertEqual([], base["collection_errors"], repaired)
        self.assertIn(
            "test_hello.TestHello.test_cli", repaired["checks"]["regression_on_candidate"]["results"]["passed"]
        )
