"""Cumulative product changes survive later test-only batches without widening scope."""

import copy
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_changed_code_trigger as trigger


class ChangedCodeTriggerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed_dir = tempfile.TemporaryDirectory(prefix="changed-code-trigger-seed-")
        cls.addClassCleanup(cls.seed_dir.cleanup)
        cls.seed = Path(cls.seed_dir.name) / "project"
        cls.seed.mkdir()
        (cls.seed / "calc.py").write_text("def answer():\n    return 1\n")
        (cls.seed / "README.md").write_text("A sample.\n")
        (cls.seed / ".gitignore").write_text("ignored/\n.autocode/\n")
        subprocess.run(["git", "init", "-q", str(cls.seed)], check=True)
        subprocess.run(["git", "-C", str(cls.seed), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(cls.seed),
                "-c",
                "user.name=T",
                "-c",
                "user.email=t@example.test",
                "commit",
                "-qm",
                "seed",
            ],
            check=True,
        )
        cls.base = subprocess.check_output(["git", "-C", str(cls.seed), "rev-parse", "HEAD"], text=True).strip()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="changed-code-trigger-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "project"
        shutil.copytree(self.seed, self.root)
        self.state = {"workspace": str(self.root), "base_commit": self.base, "changed_files": ["tests/test_calc.py"]}

    def test_actual_product_diff_survives_later_tests_only_batch_and_commit(self):
        (self.root / "calc.py").write_text("def answer():\n    return 2\n")
        (self.root / "tests").mkdir()
        (self.root / "tests/test_calc.py").write_text("assert True\n")
        before = copy.deepcopy(self.state)
        self.assertTrue(trigger.owes(self.state))
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.root),
                "-c",
                "user.name=T",
                "-c",
                "user.email=t@example.test",
                "commit",
                "-qm",
                "candidate",
            ],
            check=True,
        )
        self.state["changed_files"] = ["README.md"]
        self.assertTrue(trigger.owes(self.state))
        self.assertEqual(before["base_commit"], self.state["base_commit"])

    def test_untracked_and_explicit_ignored_product_are_in_the_canonical_diff(self):
        (self.root / "new.py").write_text("value = 1\n")
        self.assertTrue(trigger.owes(self.state))
        (self.root / "new.py").unlink()
        (self.root / "ignored").mkdir()
        (self.root / "ignored/selected.py").write_text("value = 1\n")
        self.assertFalse(trigger.owes(self.state))
        with mock.patch.object(trigger.source_scope, "paths", return_value=["ignored/selected.py"]) as paths:
            self.assertTrue(trigger.owes(self.state))
            paths.assert_called_once_with(self.state)

    def test_docs_and_tests_only_diff_stays_compatible_and_revert_retires_product(self):
        (self.root / "README.md").write_text("New documentation.\n")
        (self.root / "tests").mkdir()
        (self.root / "tests/test_calc.py").write_text("assert True\n")
        self.assertFalse(trigger.owes(self.state))
        self.state["stages"] = [{"changed_files": ["calc.py"]}]
        self.assertFalse(trigger.owes(self.state))

    def test_unavailable_diff_never_erases_runner_known_product_changes(self):
        variants = (
            {"changed_files": ["calc.py"]},
            {"stages": [{"changed_files": ["calc.py"]}]},
            {"regression_proof": {"source_files": ["calc.py"]}},
            {"regression_proof": {"changed_code_inputs": {"inventory": [{"path": "calc.py"}]}}},
        )
        for patch in variants:
            state = {**self.state, **patch}
            with (
                self.subTest(patch=patch),
                mock.patch.object(trigger.verify, "changed_files", side_effect=RuntimeError("base unavailable")),
            ):
                self.assertTrue(trigger.owes(state))
            state.pop("workspace")
            self.assertTrue(trigger.owes(state))
        with mock.patch.object(trigger.verify, "changed_files", side_effect=RuntimeError("unavailable")):
            self.assertFalse(trigger.owes(self.state))
        self.assertFalse(trigger.owes({"changed_files": ["README.md", "tests/test_calc.py"]}))

    def test_uses_recorded_base_and_approved_source_selection_without_mutation(self):
        before = copy.deepcopy(self.state)
        with (
            mock.patch.object(trigger.run_base, "base_commit", return_value="original-base") as base,
            mock.patch.object(trigger.source_scope, "paths", return_value=["approved/"]),
            mock.patch.object(trigger.verify, "changed_files", return_value={"approved/calc.py": "modified"}) as diff,
        ):
            self.assertTrue(trigger.owes(self.state))
            base.assert_called_once_with(self.state, self.root)
            diff.assert_called_once_with(self.root, "original-base", source_paths=["approved/"])
        self.assertEqual(before, self.state)

    # A project with nothing to measure records the fallback instead of owing an impossible proof (#2474).
    def test_no_test_command_and_no_test_file_is_unmeasured_and_recorded(self):
        import autocode_regression as regression

        (self.root / "calc.py").write_text("def answer():\n    return 2\n")
        self.assertTrue(trigger.owes(self.state))
        self.assertEqual(trigger.UNMEASURABLE, trigger.unmeasurable(self.state))
        self.assertFalse(regression.required(self.state, all_due=True))
        regression.record_unmeasured(self.state)
        self.assertEqual(
            {"version": 1, "decision": "unmeasured", "reason": trigger.UNMEASURABLE},
            self.state["changed_code_fallback"],
        )

    def test_a_test_file_or_a_test_command_keeps_the_obligation(self):
        import autocode_regression as regression

        (self.root / "calc.py").write_text("def answer():\n    return 2\n")
        with self.subTest("test command"):
            commanded = {**self.state, "settings": {"regression": {"test_command": "python -m unittest"}}}
            self.assertIsNone(trigger.unmeasurable(commanded))
            self.assertTrue(regression.required(commanded, all_due=True))
        (self.root / "tests").mkdir()
        (self.root / "tests/test_calc.py").write_text("assert True\n")  # untracked, as a Builder leaves it
        with self.subTest("test file"):
            self.assertIsNone(trigger.unmeasurable(self.state))
            self.assertTrue(regression.required(self.state, all_due=True))
            self.state["changed_code_fallback"] = {"reason": "stale"}
            regression.record_unmeasured(self.state)
            self.assertNotIn("changed_code_fallback", self.state)

    def test_an_unreadable_workspace_keeps_the_obligation(self):
        self.assertIsNone(trigger.unmeasurable({**self.state, "workspace": str(self.root / "missing")}))
