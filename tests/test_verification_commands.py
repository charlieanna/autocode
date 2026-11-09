"""Explicit collectors keep the result parser used by their actual execution."""

import json
import shlex
import sys
import unittest
from pathlib import Path

import autocode_regression as regression
import autocode_verify as verify
from autocode_python_tests import parse, verbose_unittest

from . import test_verify as fixtures


class CommandRecognitionTests(unittest.TestCase):
    def test_framework_recognizes_the_literal_unittest_command_the_runner_will_instrument(self):
        for command in (
            ".venv/bin/python -m unittest tests.test_one",
            "python3 -m unittest -q tests.test_one",
            "env MODE=proof '/venv with spaces/bin/python' -m unittest discover --quiet -s tests",
        ):
            with self.subTest(command=command):
                framework = verify.command_framework(command)
                self.assertIsNotNone(framework)
                self.assertEqual("unittest", framework.name)
                self.assertEqual(command, framework.suite)
                invocation = parse(verbose_unittest(command))
                self.assertEqual(invocation, framework.invocation)
                self.assertEqual(invocation.python, framework.python)
                self.assertEqual(invocation.targeted(["tests/test_new.py"]), framework.targeted(["tests/test_new.py"]))

    def test_framework_does_not_normalize_an_opaque_shell_program_into_unittest(self):
        for command in (
            "python3 -m unittest && true",
            "env -i python3 -m unittest",
            "env X=$(echo proof) python3 -m unittest",
            "sh -c 'python3 -m unittest'",
            "python3 custom_runner.py",
        ):
            with self.subTest(command=command):
                self.assertIsNone(verify.command_framework(command))


class ExplicitUnittestProofTests(unittest.TestCase):
    @unittest.skipUnless(verify._python_can_import(sys.executable, "pytest"), "pytest is not installed")
    def test_quiet_unittest_proof_keeps_named_results_despite_pytest_configuration_and_node_fixtures(self):
        python = fixtures.VerifyCase.proof_python(self, pytest=True)
        existing = (
            "import unittest\nfrom app import value, keep\n"
            "class Existing(unittest.TestCase):\n"
            " def test_existing(self): self.assertEqual(9, keep())\n"
        )
        project = fixtures.Project(
            {
                "app.py": "def value(): return 1\ndef keep(): return 9\n",
                "tests/__init__.py": "",
                "tests/test_app.py": existing,
                "pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
                "fixtures/node/package.json": json.dumps({"scripts": {"start": "cd dist && node server.js"}}),
            }
        )
        self.addCleanup(project.close)
        self.assertEqual("pytest", verify.detect_framework(project.root, python=python).name)
        project.write(
            {
                "app.py": "def value(): return 2\ndef keep(): return 9\n",
                "tests/test_app.py": existing + " def test_t1_fixed(self): self.assertEqual(2, value())\n",
                "docs/progress.md": "The fix is implemented.\n",
            }
        )
        suite = f"{shlex.quote(python)} -m unittest tests.test_app"
        state = {
            "goal_contract": {"body": {"task_kind": "bugfix"}},
            "base_commit": project.base,
            "settings": {"regression": {"test_command": suite, "test_timeout": 30, "python": python}},
        }
        proof = regression.prove(state, project.root, project.evidence)
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual("unittest", proof["framework"]["name"])
        self.assertEqual(suite, proof["commands"]["suite"])
        self.assertEqual(["tests.test_app.Existing.test_t1_fixed"], proof["fail_to_pass"])
        receipt = json.loads(Path(proof["path"]).read_text())
        for label in ("regression_on_base", "regression_on_candidate", "suite_on_candidate"):
            self.assertTrue(receipt["checks"][label]["results"]["complete"], label)
        self.assertEqual(1, proof["checks"]["regression_on_base"]["exit_code"])
        self.assertEqual(0, proof["checks"]["suite_on_candidate"]["exit_code"])

        project.write({"app.py": "def value(): return 1\ndef keep(): return 9\n"})
        broken = regression.prove(state, project.root, project.evidence)
        self.assertEqual(verify.FAIL, broken["verdict"], broken)
        self.assertEqual(1, broken["checks"]["regression_on_candidate"]["exit_code"])
        self.assertFalse(regression.complete(state, broken["source_revision"]))

        project.write({"app.py": "def value(): return 2\ndef keep(): return 8\n"})
        broken_existing = regression.prove(state, project.root, project.evidence)
        self.assertEqual(verify.FAIL, broken_existing["verdict"], broken_existing)
        self.assertIn("tests.test_app.Existing.test_existing", "\n".join(broken_existing["failures"]))
        self.assertEqual(1, broken_existing["checks"]["suite_on_candidate"]["exit_code"])
        self.assertFalse(regression.complete(state, broken_existing["source_revision"]))
