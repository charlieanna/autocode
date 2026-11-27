"""An uncollected build test receives feedback and can earn ordinary proof."""

import unittest

import autocode_proof_findings as proof_findings
import autocode_verify as verify

from tests.test_verify import Project, isolated_python


class CollectionRepairFeedbackTests(unittest.TestCase):
    def project(self, files, base=None):
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
                ),
                **(base or {}),
            }
        )
        self.addCleanup(project.close)
        python = isolated_python(self)
        project.write(files)
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

        return project, run

    def test_feature_test_importing_the_module_the_change_adds_is_proof(self):
        # Owner decision 2026-11-24: the base run names the missing module ``hello``, which only the
        # change adds, so the test that could not load there shows the feature is absent.
        _, run = self.project(
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
        proof = run()
        self.assertEqual("PASS", proof["verdict"], proof)
        self.assertEqual(["test_hello.TestHello.test_cli"], proof["fail_to_pass"], proof)
        self.assertIn("test_hello.TestHello.test_cli", proof["not_run_on_base"], proof)
        self.assertTrue(proof["checks"]["regression_on_base"]["results"]["collection_errors"], proof)
        self.assertIn("hello, which only the change adds", " ".join(proof["notes"]), proof)

    def test_feature_test_importing_the_added_name_in_its_body_is_proof(self):
        # The import is inside the test, so on the base the test runs and fails importing ``hello``: a
        # setup failure, which proves the feature only because the change adds what it imports.
        _, run = self.project(
            {
                "hello.py": 'def greeting():\n    return "hello"\n',
                "test_hello.py": (
                    "import unittest\n"
                    "class TestHello(unittest.TestCase):\n"
                    "    def test_greeting(self):\n"
                    "        from hello import greeting\n"
                    '        self.assertEqual("hello", greeting())\n'
                ),
            }
        )
        proof = run()
        self.assertEqual("PASS", proof["verdict"], proof)
        self.assertEqual(["test_hello.TestHello.test_greeting"], proof["fail_to_pass"], proof)
        self.assertIn("which only the change adds", " ".join(proof["notes"]), proof)

    def test_a_skipped_test_does_not_veto_the_import_proof(self):
        # A test the host cannot run (here one that needs a database) is skipped on both sides. It is no
        # evidence either way, so it does not cancel the proof the passing tests give (#122 in
        # adaptive-engine-svc, where the Postgres variants of two new tests were skipped).
        _, run = self.project(
            {
                "hello.py": 'def greeting():\n    return "hello"\n',
                "test_hello.py": (
                    "import unittest\n"
                    "class TestHello(unittest.TestCase):\n"
                    "    def test_greeting(self):\n"
                    "        from hello import greeting\n"
                    '        self.assertEqual("hello", greeting())\n'
                    '    @unittest.skip("needs a database")\n'
                    "    def test_greeting_stored(self):\n"
                    "        from hello import greeting\n"
                    '        self.assertEqual("hello", greeting())\n'
                ),
            }
        )
        proof = run()
        self.assertEqual("PASS", proof["verdict"], proof)
        self.assertEqual(["test_hello.TestHello.test_greeting"], proof["fail_to_pass"], proof)
        self.assertIn("which only the change adds", " ".join(proof["notes"]), proof)

    def test_builder_can_repair_uncollected_tests_without_relaxing_the_gate(self):
        # A collection failure the base run does not attribute to a name the change adds (here the base's
        # own hello.py imports a module that does not exist) is still not proof: the Builder is told to make
        # the test load on the base, so its assertion runs and fails there.
        project, run = self.project(
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
            },
            base={"hello.py": 'import greeting_backend\nprint("hi")\n'},
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
