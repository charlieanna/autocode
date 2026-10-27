"""An added empty package marker must not hide original preservation tests."""

import json
import shlex
import unittest
from pathlib import Path

import autocode_regression as regression
import autocode_verify as verify

from .test_verify import Project, isolated_python

EXISTING = (
    "import sys, unittest\nfrom pathlib import Path\n"
    "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n"
    "from value import total\nclass Existing(unittest.TestCase):\n"
    " def test_existing_zero(self): self.assertEqual(total(0), 0)\n"
)
NEW = EXISTING.replace("Existing", "Regression").replace(
    "def test_existing_zero(self): self.assertEqual(total(0), 0)",
    "def test_t1_taxed_total(self): self.assertEqual(total(100), 110)",
)


class UnittestBaselineDiscoveryTests(unittest.TestCase):
    def fixture(self, directory="tests", marker=None):
        files = {"value.py": "def total(value): return value\n", f"{directory}/test_existing.py": EXISTING}
        if marker is not None:
            files[f"{directory}/__init__.py"] = marker
        project = Project(files)
        self.addCleanup(project.close)
        return project

    def candidate(self, project, directory="tests"):
        project.write(
            {
                "value.py": "def total(value): return value + value / 10\n",
                f"{directory}/__init__.py": "",
                f"{directory}/test_regression.py": NEW,
            }
        )

    def test_empty_added_marker_retains_discovery_on_both_trees(self):
        python = isolated_python(self)
        for directory in ("tests", "test"):
            with self.subTest(directory=directory):
                project = self.fixture(directory)
                self.candidate(project, directory)
                framework = verify.detect_framework(project.root, python=python, base=project.base)
                baseline = verify.baseline(
                    project.root, project.base, project.evidence, framework=framework, suite_command=framework.suite
                )
                self.assertEqual("passing", baseline["health"], baseline)
                self.assertEqual(1, baseline["receipt"]["results"]["total"])
                self.assertIn("test_existing_zero", baseline["receipt"]["results"]["passed"][0])
                proof = verify.verify(
                    project.root, project.base, project.evidence, framework=framework, base_suite=baseline
                )
                self.assertEqual(verify.PASS, proof["verdict"], proof)
                self.assertTrue(proof["fail_to_pass"])

    def test_runner_proves_fix_and_rejects_unfixed_and_broken_preservation(self):
        project = self.fixture()
        self.candidate(project)
        state = {
            "base_commit": project.base,
            "goal_contract": {"body": {"task_kind": "bugfix"}},
            "settings": {"regression": {"python": isolated_python(self)}},
        }
        proof = regression.prove(state, project.root, project.evidence)
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        receipt = json.loads(Path(proof["path"]).read_text())
        self.assertEqual("passing", receipt["baseline"]["health"])
        self.assertIn("test_existing_zero", Path(receipt["baseline"]["output"]).read_text())
        self.assertTrue(regression.complete(state, proof["source_revision"]))

        project.write({"value.py": "def total(value): return value\n"})
        unfixed = regression.prove(state, project.root, project.evidence)
        self.assertEqual(verify.FAIL, unfixed["verdict"], unfixed)
        self.assertFalse(regression.complete(state, unfixed["source_revision"]))

        project.write({"value.py": "def total(value): return 1 if value == 0 else value + value / 10\n"})
        broken_guard = regression.prove(state, project.root, project.evidence)
        self.assertEqual(verify.FAIL, broken_guard["verdict"], broken_guard)
        self.assertFalse(regression.complete(state, broken_guard["source_revision"]))

    def test_explicit_suite_is_not_rewritten_or_given_empty_baseline_credit(self):
        project = self.fixture()
        self.candidate(project)
        python = isolated_python(self)
        command = f"{shlex.quote(python)} -m unittest discover -v"
        state = {
            "base_commit": project.base,
            "goal_contract": {"body": {"task_kind": "bugfix"}},
            "settings": {"regression": {"test_command": command}},
        }
        proof = regression.prove(state, project.root, project.evidence)
        self.assertEqual(command, proof["commands"]["suite"])
        self.assertEqual(verify.UNVERIFIED, proof["verdict"], proof)
        self.assertFalse(regression.complete(state, proof["source_revision"]))

    def test_existing_packages_hooks_and_root_tests_keep_project_discovery(self):
        python = isolated_python(self)
        for case in ("existing", "hook", "root-test", "initializer-code", "linked-marker"):
            with self.subTest(case=case):
                project = self.fixture(marker="" if case == "existing" else None)
                self.candidate(project)
                if case == "hook":
                    project.write({"tests/__init__.py": "def load_tests(loader, tests, pattern): return tests\n"})
                elif case == "initializer-code":
                    project.write({"tests/__init__.py": "INITIALIZED = True\n"})
                elif case == "root-test":
                    project.write({"test_root.py": "import unittest\n"})
                elif case == "linked-marker":
                    project.write({"empty.txt": ""})
                    (project.root / "tests/__init__.py").unlink()
                    (project.root / "tests/__init__.py").symlink_to("../empty.txt")
                framework = verify.detect_framework(project.root, python=python, base=project.base)
                self.assertEqual(f"{shlex.quote(python)} -m unittest discover -v", framework.suite)
