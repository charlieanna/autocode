"""A whole detected suite that times out on the original code falls back to the tests nearest the change (#3158).

The proof chooses the bounded command itself and records why; it never leaves the proof UNVERIFIED for a
person to pick a timeout or a test command.
"""

import shlex
import sys
import unittest
from pathlib import Path
from unittest import mock

import autocode_focused_suite as focused_suite
import autocode_regression as regression
import autocode_test_role_inventory as role_inventory
import autocode_util as util
import autocode_verify as verify

from .test_command_receipt import guarded_command_receipt
from .test_verify import Project

CALC_TESTS = (
    "import unittest\nfrom calc import add\nclass Calc(unittest.TestCase):\n"
    " def test_zero(self): self.assertEqual(0, add(0, 0))\n"
)


class FocusedFallbackProofTests(unittest.TestCase):
    def setUp(self):
        self.project = Project(
            {
                "calc.py": "def add(a, b): return a - b\n",
                "test_calc.py": CALC_TESTS,
                "test_slow.py": "import unittest\nclass Slow(unittest.TestCase):\n def test_slow(self): pass\n",
            }
        )
        self.addCleanup(self.project.close)
        self.project.write(
            {
                "calc.py": "def add(a, b): return a + b\n",
                "test_calc.py": CALC_TESTS + " def test_add(self): self.assertEqual(5, add(2, 3))\n",
            }
        )
        self.whole_runs = []
        self.run_dir = self.project.root / ".autocode" / "runs" / "proof"
        self.run_dir.mkdir(parents=True)

    def state(self, **regression_settings):
        return {
            "workspace": str(self.project.root),
            "base_commit": self.project.base,
            "iteration": 1,
            "stages": [],
            "history": [],
            "settings": {"regression": regression_settings},
            "goal_contract": {"body": {"task_kind": "bugfix"}},
        }

    def prove(self, state):
        """Prove with real commands, except that the whole discovered suite times out without waiting."""
        real = verify.run_suite

        def run(framework, command, tree, directory, label, **options):
            if "discover" not in command:
                return real(framework, command, tree, directory, label, **options)
            self.whole_runs.append(label)
            output = Path(directory) / (label + ".log")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("test_calc ... ok\n(timed out)\n")
            return guarded_command_receipt(
                {
                    "command": command,
                    "exit_code": None,
                    "timed_out": True,
                    "output": str(output),
                    "output_sha256": util.file_hash(output),
                    "tail": "",
                    "results_expected": True,
                    "results": None,
                }
            )

        with (
            mock.patch.object(verify, "run_suite", side_effect=run),
            mock.patch.object(verify.diff_coverage, "collect", return_value=None),
        ):
            return regression.prove(state, self.project.root, self.run_dir)

    def test_timed_out_whole_suite_is_replaced_by_the_nearest_tests_and_recorded(self):
        state = self.state(test_timeout=1800)
        proof = self.prove(state)
        self.assertEqual(["suite-on-base"], self.whole_runs)
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        fallback = proof["suite_fallback"]
        self.assertEqual("focused_suite", fallback["decision"])
        self.assertEqual(["test_calc.py"], fallback["tests"])
        self.assertIn("discover", fallback["whole_suite"])
        self.assertEqual(1800, fallback["timeout_seconds"])
        self.assertNotIn("discover", fallback["command"])
        self.assertIn("test_calc.py", fallback["command"])
        self.assertEqual(fallback["command"], proof["commands"]["suite"])
        self.assertIn(fallback["reason"], proof["notes"])
        self.assertIn("1800 seconds", fallback["reason"])
        self.assertIn(focused_suite.review_reason(fallback), proof["review_reasons"])
        self.assertFalse(any("timed out" in reason for reason in proof["unverified"]), proof["unverified"])
        self.assertTrue(any(test.endswith("test_add") for test in proof["fail_to_pass"]), proof["fail_to_pass"])
        recorded = util.read(proof["path"])
        self.assertEqual(fallback, recorded["suite_fallback"])

        # A later proof of the same run does not spend the whole suite's time limit again.
        self.project.write({"calc.py": "def add(a, b):\n    return a + b\n"})
        again = self.prove(state)
        self.assertEqual(["suite-on-base"], self.whole_runs)
        self.assertEqual(verify.PASS, again["verdict"], again)
        self.assertEqual(fallback["command"], again["suite_fallback"]["command"])

    def test_a_later_proof_does_not_time_the_whole_suite_out_again_when_the_runtime_changed(self):
        # The runtime a proof binds can change between proofs (a shared virtualenv) or be one the cache cannot
        # bind (an editable install): the whole suite's timeout on the same original code is still settled.
        real = verify.baseline_identity
        calls = []

        def unbound(*args, **kwargs):
            identity = real(*args, **kwargs)
            calls.append(1)
            return {**identity, "environment_hash": f"changed-{len(calls)}", "reuse_supported": False}

        state = self.state(test_timeout=1800)
        with mock.patch.object(verify, "baseline_identity", side_effect=unbound):
            first = self.prove(state)
            self.project.write({"calc.py": "def add(a, b):\n    return a + b\n"})
            again = self.prove(state)
        self.assertEqual(["suite-on-base"], self.whole_runs)
        self.assertEqual(verify.PASS, first["verdict"], first)
        self.assertEqual(verify.PASS, again["verdict"], again)
        self.assertEqual(first["suite_fallback"]["command"], again["suite_fallback"]["command"])

        # A longer limit is a different question, so the whole suite runs again under it.
        state["settings"]["regression"]["test_timeout"] = 3600
        self.project.write({"calc.py": "def add(a, b):\n    return b + a\n"})
        with mock.patch.object(verify, "baseline_identity", side_effect=unbound):
            self.prove(state)
        self.assertEqual(["suite-on-base", "suite-on-base"], self.whole_runs)

    def test_explicit_test_command_is_kept_even_when_it_times_out(self):
        command = shlex.quote(sys.executable) + " -m unittest discover -v"
        proof = self.prove(self.state(test_command=command))
        self.assertEqual(["suite-on-base"], self.whole_runs)
        self.assertEqual(verify.UNVERIFIED, proof["verdict"])
        self.assertIsNone(proof["suite_fallback"])
        self.assertTrue(any("timed out" in reason for reason in proof["unverified"]), proof["unverified"])


class NearestTestsTests(unittest.TestCase):
    def roles(self, project, changes):
        return role_inventory.from_git(project.root, project.base, verify.is_test_path, changes=changes)

    def test_changed_original_tests_and_tests_named_after_changed_products(self):
        project = Project(
            {
                "pkg/calc.py": "X = 1\n",
                "pkg/other.py": "Y = 1\n",
                "tests/test_calc.py": "def test_x(): pass\n",
                "tests/test_other.py": "def test_y(): pass\n",
                "tests/test_unrelated.py": "def test_z(): pass\n",
                "tests/test_gone.py": "def test_g(): pass\n",
            }
        )
        self.addCleanup(project.close)
        project.write({"pkg/calc.py": "X = 2\n", "tests/test_other.py": "def test_y(): assert 1\n"})
        project.write({"tests/test_new.py": "def test_n(): pass\n"})
        (project.root / "tests/test_gone.py").unlink()
        changes = {
            "pkg/calc.py": "modified",
            "tests/test_other.py": "modified",
            "tests/test_new.py": "added",
            "tests/test_gone.py": "deleted",
        }
        base_files = role_inventory.entries(project.root, project.base)
        roles = self.roles(project, changes)
        self.assertEqual(
            ["tests/test_calc.py", "tests/test_other.py"], focused_suite.nearest_tests(base_files, changes, roles)
        )
        framework = verify.Framework("pytest", "python -m pytest -q", python="python")
        chosen = focused_suite.choose(framework, base_files, changes, roles)
        self.assertEqual(["tests/test_calc.py", "tests/test_other.py"], chosen["tests"])
        self.assertIn("tests/test_calc.py tests/test_other.py", chosen["command"])
        self.assertNotIn("test_new.py", chosen["command"])

    def test_nothing_is_chosen_without_an_original_test_or_a_narrower_command(self):
        project = Project({"calc.py": "X = 1\n", "test_suite.py": "def test_x(): pass\n"})
        self.addCleanup(project.close)
        project.write({"calc.py": "X = 2\n", "test_new.py": "def test_n(): pass\n"})
        changes = {"calc.py": "modified", "test_new.py": "added"}
        base_files = role_inventory.entries(project.root, project.base)
        roles = self.roles(project, changes)
        framework = verify.Framework("pytest", "python -m pytest -q", python="python")
        self.assertIsNone(focused_suite.choose(framework, base_files, changes, roles))
        self.assertIsNone(focused_suite.choose(None, base_files, changes, roles))
        whole = mock.Mock(suite="whole")
        whole.targeted.return_value = "whole"
        changes = {"calc.py": "modified", "test_suite.py": "modified"}
        self.assertIsNone(focused_suite.choose(whole, base_files, changes, self.roles(project, changes)))

    def test_timed_out_reads_only_a_settled_timeout(self):
        self.assertTrue(focused_suite.timed_out({"receipt": {"timed_out": True}}))
        self.assertFalse(focused_suite.timed_out({"receipt": {"timed_out": False}}))
        self.assertFalse(focused_suite.timed_out(None))

    def test_record_without_a_limit_says_so(self):
        fallback = focused_suite.record(
            {"command": "pytest t.py", "tests": ["t.py"]}, {"command": "pytest", "receipt": {"output": "o"}}, None
        )
        self.assertIn("within its time limit", fallback["reason"])
        self.assertEqual("o", fallback["whole_suite_output"])


if __name__ == "__main__":
    unittest.main()
