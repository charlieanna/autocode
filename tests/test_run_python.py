"""A run's checks use the interpreter the run recorded, not a virtualenv that appeared later.

A live Kilo run recorded its suite with the system python3, which has pytest. The Builder's `uv sync`,
with no network, then left an empty `.venv` in the project, and the completion proof found its interpreter again with
`python_for`, which prefers any virtualenv: it ran `.venv/bin/python -m pytest`, "No module named
pytest", no test ran, and the Resolver asked the person to provide a test environment. #3790 gave
the clean scratch runners the recorded interpreter; the proof and the checks beside it now read the
same answer (`autocode_test_environment.run_python`).
"""

import importlib.util
import subprocess
import sys
import unittest

import autocode_regression as regression
import autocode_test_environment as test_environment
import autocode_verify as verify

from tests.test_verify import Project

SEED = {
    ".gitignore": ".venv\n",
    "calc.py": "def add(a, b):\n    return a + b\n",
    "tests/test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
}
CANDIDATE = {
    "calc.py": "def add(a, b):\n    return a + b\n\n\ndef mul(a, b):\n    return a * b\n",
    # The new test loads on the original code and fails there on a named assertion (the recorded-interpreter
    # proof needs a fail-to-pass test that ran on base, not one that only fails to import the new name, #3981).
    "tests/test_mul.py": "import calc\n\n\ndef test_mul():\n    assert getattr(calc, 'mul', lambda *a: 0)(2, 3) == 6\n",
}


def recorded(command=None, **regression_settings):
    return {
        "protected_tests": {"command": command or f"{sys.executable} -m pytest -q -p no:cacheprovider"},
        "regression": {"test_timeout": 120, **regression_settings},
    }


@unittest.skipUnless(importlib.util.find_spec("pytest"), "the suite's interpreter needs pytest")
class RecordedInterpreterTests(unittest.TestCase):
    def setUp(self):
        self.project = Project(SEED)
        self.addCleanup(self.project.close)
        self.project.write(CANDIDATE)
        # The empty virtualenv a Builder's `uv sync` leaves without network: no pytest, nor the suite's own.
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(self.project.root / ".venv")], check=True)
        self.venv = str(self.project.root / ".venv" / "bin" / "python")

    def state(self, settings):
        return {
            "base_commit": self.project.base,
            "iteration": 1,
            "stages": [],
            "history": [],
            "settings": settings,
            "goal_contract": {"body": {"task_kind": "build"}},
        }

    def test_the_proof_runs_with_the_recorded_interpreter_after_a_virtualenv_appears(self):
        self.assertEqual(self.venv, verify.python_for(self.project.root))  # what the proof used to take
        run = self.project.root / ".autocode" / "runs" / "proof"
        run.mkdir(parents=True)
        proof = regression.prove(self.state(recorded()), self.project.root, run)
        self.assertEqual("PASS", proof["verdict"], proof.get("failures"))
        self.assertTrue(proof["commands"]["suite"].startswith(sys.executable), proof["commands"])
        self.assertFalse(proof["commands"]["regression"].startswith(self.venv), proof["commands"])

    def test_one_answer_explicit_first_then_recorded_then_discovered(self):
        root = self.project.root
        self.assertEqual(sys.executable, test_environment.run_python(self.state(recorded()), root))
        explicit = recorded(python=self.venv)
        self.assertEqual(self.venv, test_environment.run_python(self.state(explicit), root))
        # A recorded command without an interpreter path, or no record: the project's own discovery.
        self.assertEqual(self.venv, test_environment.run_python(self.state(recorded("pytest -q")), root))
        self.assertEqual(self.venv, test_environment.run_python(self.state({}), root))
        self.assertEqual(self.venv, test_environment.run_python(None, root))


if __name__ == "__main__":
    unittest.main()
