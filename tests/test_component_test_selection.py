"""The owned unittest collector retains collection semantics when selecting tests."""

import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_component_tests as collector
import autocode_mutation_check as mutation
import autocode_mutation_results as outcomes
import autocode_proof_stability as stability
import autocode_python_tests as python_tests


class CollectorSelectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.script = str(Path(collector.__file__).resolve())

    def command(self, *args):
        return shlex.join([sys.executable, self.script, "-v", *args])

    def run_command(self, command, label="check", timeout=30):
        result = subprocess.run(shlex.split(command), cwd=self.root, capture_output=True, text=True, timeout=timeout)
        output = self.root / (label + ".txt")
        output.write_text(result.stdout + result.stderr)
        return {
            "output": str(output),
            "command": command,
            "exit_code": result.returncode,
            "duration_seconds": 0.1,
            "results": python_tests.unittest_results(result.stderr, component=True),
        }

    def test_named_selection_does_not_claim_file_targeting_for_home_discovery(self):
        command = self.command("--root", ".", "--components", "one-service")
        self.assertIsNone(python_tests.parse(command), "module-only consumers must not reinterpret an owned script")
        invocation = python_tests.parse(command, include_owned=True)
        self.assertIsNone(invocation.targeted(["components/one-service/test_answer.py"]))
        self.assertIsNotNone(
            stability.narrow("unittest", command, ["components.one-service.test_answer.Answer.test_selected"])
        )

    def test_parent_hook_and_root_tests_keep_their_own_collection(self):
        (self.root / "components").mkdir()
        (self.root / "components/__init__.py").write_text(
            "import unittest\nclass Hook(unittest.TestCase):\n"
            " def test_selected(self): pass\n"
            " def test_other(self): self.fail('not selected')\n"
            "def load_tests(loader, suite, pattern):\n"
            " return loader.loadTestsFromTestCase(Hook)\n"
        )
        (self.root / "test_root.py").write_text(
            "import unittest\nclass Root(unittest.TestCase):\n"
            " def test_selected(self): pass\n"
            " def test_other(self): self.fail('not selected')\n"
        )
        # The family's hook owns collection; a listed missing member must not be traversed.
        command = self.command("--root", ".", "--components", "missing")
        selected = ["components.Hook.test_selected", "test_root.Root.test_selected"]
        narrowed = stability.narrow("unittest", command, selected)
        observed = self.run_command(narrowed)
        self.assertEqual(0, observed["exit_code"])
        self.assertEqual(2, observed["results"]["total"])
        self.assertEqual(selected, [name.replace("::", ".") for name in observed["results"]["passed"]])

    def test_failfast_stops_before_other_isolated_members(self):
        for member in ("first", "second"):
            directory = self.root / "components" / member
            directory.mkdir(parents=True)
            (directory / "test_case.py").write_text(
                "import unittest\nfrom pathlib import Path\nclass Case(unittest.TestCase):\n"
                f" def test_selected(self):\n  Path('{member}.ran').touch()\n  self.fail('selected failure')\n"
            )
        command = self.command("--root", ".", "--components", "first", "second")
        selected = [f"components.{member}.test_case.Case.test_selected" for member in ("first", "second")]
        stopped = outcomes.stop_command(stability.narrow("unittest", command, selected))
        self.assertTrue(outcomes.stops_after_failure(stopped))
        observed = self.run_command(stopped)
        self.assertNotEqual(0, observed["exit_code"])
        self.assertTrue((self.root / "first.ran").exists())
        self.assertFalse((self.root / "second.ran").exists())
        self.assertEqual("killed", outcomes.outcome(observed, observed["results"]["failed"], stopped=True)[0])

    def test_mutation_executes_selected_tests_and_detects_broken_product_lines(self):
        directory = self.root / "components" / "one-service"
        directory.mkdir(parents=True)
        source = directory / "answer.py"
        source.write_text("def answer():\n    return 2\n")
        (directory / "test_answer.py").write_text(
            "import unittest\nfrom answer import answer\nclass Answer(unittest.TestCase):\n"
            " def test_selected(self): self.assertEqual(2, answer())\n"
            " def test_other(self): self.fail('not selected')\n"
        )
        command = self.command("--root", ".", "--components", "one-service")
        narrowed = stability.narrow("unittest", command, ["components.one-service.test_answer.Answer.test_selected"])
        selected = self.run_command(narrowed)["results"]["passed"]
        path = "components/one-service/answer.py"
        report = mutation.collect_selected_tests(
            "unittest",
            command,
            selected,
            self.root,
            self.root / "evidence",
            [(path, "modified")],
            lambda files: (
                f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -2 +2 @@\n-    return 1\n+    return 2\n"
            ),
            self.run_command,
            enforcement=True,
        )
        self.assertTrue(report["collected"], report)
        mutants = report["files"][0]["mutants"]
        self.assertTrue(mutants, report)
        self.assertEqual({"killed"}, {row["outcome"] for row in mutants}, report)
        self.assertEqual("def answer():\n    return 2\n", source.read_text())

    def test_unrelated_scripts_and_shell_commands_remain_unsupported(self):
        for command in (
            shlex.join([sys.executable, str(self.root / "autocode_component_tests.py"), "-v"]),
            self.command("--root", ".") + " && echo other",
        ):
            with self.subTest(command=command):
                self.assertIsNone(python_tests.parse(command))
                self.assertIsNone(stability.narrow("unittest", command, ["test_a.Case.test_ok"]))
