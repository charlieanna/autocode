"""Successive milestones keep their named tests in a saved literal proof selection (#1105)."""

import importlib.util
import json
import shlex
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_regression as regression

from tests.test_test_cases import feature_state
from tests.test_verify import Project, isolated_python

BASE = {
    "calc.py": "def old():\n    return 1\n",
    "test_legacy.py": "import unittest\nfrom calc import old\nclass Legacy(unittest.TestCase):\n"
    "    def test_legacy(self): self.assertEqual(1, old())\n",
}
M1 = {
    "calc.py": BASE["calc.py"] + "def sub(a, b):\n    return a - b\n",
    "test_m1.py": "import unittest\nfrom calc import sub\nclass First(unittest.TestCase):\n"
    "    def test_c1_subtracts(self): self.assertEqual(2, sub(5, 3))\n",
}
M2 = {
    "calc.py": M1["calc.py"] + "def mul(a, b):\n    return a * b\n",
    "test_m2.py": "import unittest\nfrom calc import mul\nclass Second(unittest.TestCase):\n"
    "    def test_c2_multiplies(self): self.assertEqual(6, mul(2, 3))\n",
}


class MilestoneProofSelectionTests(unittest.TestCase):
    def setUp(self):
        self.project = Project(BASE)
        self.addCleanup(self.project.close)
        temporary = tempfile.TemporaryDirectory(prefix="milestone-selection-")
        self.addCleanup(temporary.cleanup)
        self.run = Path(temporary.name)
        self.python = isolated_python(self)

    def state(self, *, kind="unittest", python=None):
        python = python or self.python
        prefix = shlex.quote(python)
        if kind == "pytest":
            suite = prefix + " -m pytest -p no:cacheprovider -vv"
            command = suite + " test_m1.py"
        else:
            suite = prefix + " -m unittest discover -v"
            command = prefix + " -m unittest -v test_m1"
        criteria = [
            {"id": "C1", "criterion": "Subtract", "verification_method": "test: test_c1_subtracts"},
            {"id": "C2", "criterion": "Multiply", "verification_method": "test: test_c2_multiplies"},
        ]
        state = feature_state(self.project, criteria, milestones=2)
        state["settings"] = {
            "regression": {"python": python, "test_command": suite, "regression_command": command, "test_timeout": 30}
        }
        state["goal_contract"]["hash"] = "h"
        state["goal_contract"]["body"]["milestones"] = [
            {"id": "M1", "acceptance_criteria": ["C1"]},
            {"id": "M2", "acceptance_criteria": ["C2"]},
        ]
        state["current_task"] = {"milestone_id": "M1"}
        return state

    def advance(self, state):
        state["current_task"] = {"milestone_id": "M2"}
        state["milestone_progress"] = {"M1": {"id": "M1", "accepted": True, "contract_hash": "h"}}

    def prove(self, state, label):
        return regression.prove(state, self.project.root, self.run / label)

    def test_both_checkpoints_prove_new_files_without_changing_the_saved_unittest_command(self):
        self.project.write(M1)
        state = self.state()
        saved = json.dumps(state["settings"], sort_keys=True)
        first = self.prove(state, "m1")
        self.assertEqual("PASS", first["verdict"], first)
        self.assertEqual(["C1"], first["case_scope"])
        self.project.write(M2)
        self.advance(state)
        second = self.prove(state, "m2")
        self.assertEqual("PASS", second["verdict"], second)
        self.assertEqual(["C1", "C2"], second["case_scope"])
        self.assertEqual(
            {"C1": ["test_m1.First.test_c1_subtracts"], "C2": ["test_m2.Second.test_c2_multiplies"]},
            second["case_tests"],
        )
        self.assertEqual(saved, json.dumps(state["settings"], sort_keys=True))

    @unittest.skipUnless(importlib.util.find_spec("pytest"), "pytest is not installed")
    def test_saved_pytest_file_list_includes_the_next_milestones_new_file(self):
        self.project.write({**M1, **M2})
        state = self.state(kind="pytest", python=sys.executable)
        self.advance(state)
        proof = self.prove(state, "pytest")
        self.assertEqual("PASS", proof["verdict"], proof)
        self.assertEqual(
            {"C1": ["test_m1.First::test_c1_subtracts"], "C2": ["test_m2.Second::test_c2_multiplies"]},
            proof["case_tests"],
        )
        self.assertIn("-p no:cacheprovider -vv", proof["commands"]["regression"])

    def test_missing_next_milestone_test_still_fails(self):
        self.project.write({**M1, "calc.py": M2["calc.py"]})
        state = self.state()
        self.advance(state)
        proof = self.prove(state, "missing")
        self.assertEqual("FAIL", proof["verdict"], proof)
        self.assertEqual([], proof["case_tests"]["C2"])

    def test_failing_next_milestone_test_still_fails(self):
        self.project.write({**M1, **M2, "calc.py": M2["calc.py"].replace("a * b", "a + b")})
        state = self.state()
        self.advance(state)
        proof = self.prove(state, "failing")
        self.assertEqual("FAIL", proof["verdict"], proof)
        self.assertEqual([], proof["case_tests"]["C2"])
        self.assertTrue(any("fail on the candidate" in failure for failure in proof["failures"]), proof)

    def test_deleting_an_accepted_milestones_test_still_fails(self):
        self.project.write({**M1, **M2})
        (self.project.root / "test_m1.py").unlink()
        state = self.state()
        self.advance(state)
        proof = self.prove(state, "deleted-accepted")
        self.assertEqual("FAIL", proof["verdict"], proof)
        self.assertEqual([], proof["case_tests"]["C1"])

    def test_explicit_bugfix_selection_cannot_be_widened_into_a_pass(self):
        self.project.write({**M1, **M2})
        state = self.state()
        self.advance(state)
        state["goal_contract"]["body"]["task_kind"] = "bugfix"
        command = shlex.quote(self.python) + " -m unittest -v test_legacy"
        state["settings"]["regression"]["regression_command"] = command
        proof = self.prove(state, "bugfix-narrow")
        self.assertEqual("FAIL", proof["verdict"], proof)
        self.assertEqual(command, proof["commands"]["regression"])

    def test_saved_filter_retains_its_authority_and_cannot_hide_a_missing_case(self):
        self.project.write({**M1, **M2})
        state = self.state()
        self.advance(state)
        command = shlex.quote(self.python) + " -m unittest -v -k c1 test_m1"
        state["settings"]["regression"]["regression_command"] = command
        proof = self.prove(state, "filtered")
        self.assertEqual("FAIL", proof["verdict"], proof)
        self.assertEqual([], proof["case_tests"]["C2"])
        self.assertIn("-k c1", proof["commands"]["regression"])
        self.assertIn("test_m2.py", proof["commands"]["regression"])
