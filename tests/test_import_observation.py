"""Project-import checks start with a named failure from runner-owned output."""

import hashlib
import os
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_import_observation as observation
import autocode_verify as verify


class ImportObservationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.log = self.root / "suite.log"
        self.xml = self.root / "suite.xml"
        self.log.write_text("runner output\n")

    def receipt(self, failed):
        return {
            "exit_code": 1,
            "timed_out": False,
            "output": str(self.log),
            "output_sha256": hashlib.sha256(self.log.read_bytes()).hexdigest(),
            "results": {"failed": failed},
        }

    def test_unittest_reads_only_the_named_failure_section(self):
        text = (
            "=" * 70 + "\nERROR: test_a (test_x.TestX)\n" + "-" * 70 + "\n"
            "Traceback (most recent call last):\n"
            "ImportError: cannot import name 'new' from 'app.api' (/scratch/app/api.py)\n"
            + "-"
            * 70
            + "\nRan 1 test in 0.01s\n\nFAILED (errors=1)\n"
            "ModuleNotFoundError: No module named 'unrelated_later_output'\n"
        )
        self.log.write_text(text)
        result = observation.targets(self.receipt(["test_x.TestX::test_a"]), self.root, "unittest")
        self.assertEqual({"test_x.TestX::test_a": ("app.api", "new")}, result)

    def test_pytest_uses_the_pinned_testcase_not_the_log_or_an_external_name(self):
        self.xml.write_text(
            '<testsuite><testcase classname="test_x.TestX" name="test_a">'
            "<failure>ModuleNotFoundError: No module named 'external_dependency'</failure>"
            "</testcase></testsuite>"
        )
        receipt = self.receipt(["test_x.TestX::test_a"])
        receipt["results_capture"] = {
            "path": str(self.xml),
            "sha256": hashlib.sha256(self.xml.read_bytes()).hexdigest(),
        }
        self.assertEqual(
            {"test_x.TestX::test_a": ("external_dependency", "")},
            observation.targets(receipt, self.root, "pytest"),
        )
        self.xml.write_text(self.xml.read_text().replace("external_dependency", "different"))
        self.assertEqual({}, observation.targets(receipt, self.root, "pytest"))

    def test_ambiguous_exception_chains_do_not_choose_a_target(self):
        self.assertIsNone(
            observation._target(
                "ModuleNotFoundError: No module named 'one'\n"
                "ImportError: cannot import name 'two' from 'app' (/scratch/app.py)\n"
            )
        )

    def test_candidate_probe_observes_project_imports_and_excludes_test_and_external_modules(self):
        (self.root / "src" / "pkg").mkdir(parents=True)
        (self.root / "src" / "pkg" / "__init__.py").write_text("from .core import new\n")
        (self.root / "src" / "pkg" / "core.py").write_text("def new(): return 42\n")
        (self.root / "src" / "fresh.py").write_text("value = 1\n")
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        external = Path(outside.name) / "external.py"
        external.write_text("value = 1\n")
        (self.root / "src" / "linked.py").symlink_to(external)
        (self.root / "tests").mkdir()
        (self.root / "tests" / "__init__.py").write_text("")
        (self.root / "tests" / "helper.py").write_text("value = 1\n")
        requests = {
            "reexport": ("pkg", "new"),
            "added_module": ("fresh", ""),
            "stdlib": ("json", "dumps"),
            "test_helper": ("tests.helper", "value"),
            "missing": ("missing_autocode_dependency", ""),
            "external_link": ("linked", "value"),
        }
        token = "AUTOCODE_IMPORT_TEST:"
        command = observation.command(sys.executable, requests, token)
        receipt = verify.run_command(command, self.root, self.root / "probe.log", timeout=30)
        found = observation.project_targets(
            receipt,
            self.root,
            requests,
            token,
            is_product=lambda path: not path.startswith("tests/"),
            expected_environment=receipt["execution_environment_sha256"],
            expected_source="candidate-revision",
            source_before="candidate-revision",
            source_after="candidate-revision",
        )
        self.assertEqual({"reexport", "added_module"}, set(found))
        self.assertEqual("src/pkg/__init__.py", found["reexport"]["origin"])
        self.assertEqual("src/fresh.py", found["added_module"]["origin"])
        self.assertEqual(
            {},
            observation.project_targets(
                receipt,
                self.root,
                requests,
                token,
                is_product=lambda path: True,
                expected_environment="stale",
                expected_source="candidate-revision",
                source_before="candidate-revision",
                source_after="candidate-revision",
            ),
        )
        self.assertEqual(
            {},
            observation.project_targets(
                receipt,
                self.root,
                requests,
                token,
                is_product=lambda path: True,
                expected_environment=receipt["execution_environment_sha256"],
                expected_source="candidate-revision",
                source_before="candidate-revision",
                source_after="changed-revision",
            ),
        )

    def test_owned_command_binds_the_actual_execution_environment(self):
        command = shlex.join([sys.executable, "-c", "import os; print(os.environ['AUTOCODE_IMPORT_ENV_TEST'])"])
        with patch.dict(os.environ, {"AUTOCODE_IMPORT_ENV_TEST": "first"}):
            first = verify.run_command(command, self.root, self.root / "first.log", timeout=30)
            same = verify.run_command(command, self.root, self.root / "same.log", timeout=30)
        with patch.dict(os.environ, {"AUTOCODE_IMPORT_ENV_TEST": "second"}):
            changed = verify.run_command(command, self.root, self.root / "changed.log", timeout=30)
        self.assertEqual("first", Path(first["output"]).read_text().strip())
        self.assertEqual("second", Path(changed["output"]).read_text().strip())
        self.assertEqual(first["execution_environment_sha256"], same["execution_environment_sha256"])
        self.assertNotEqual(first["execution_environment_sha256"], changed["execution_environment_sha256"])

    def test_observe_candidate_reruns_the_same_tests_and_binds_source_and_environment(self):
        base, candidate, output = (self.root / name for name in ("base", "candidate", "evidence"))
        for tree, implementation in ((base, "def old(): return 1\n"), (candidate, "def new(): return 42\n")):
            tree.mkdir()
            (tree / "app.py").write_text(implementation)
            (tree / "test_app.py").write_text(
                "import unittest\nclass TestApp(unittest.TestCase):\n"
                "    def test_export(self):\n        from app import new\n        self.assertEqual(new(), 42)\n"
            )
        output.mkdir()
        command = shlex.join([sys.executable, "-m", "unittest", "-v", "test_app"])
        framework = verify.Framework("unittest", command, python=sys.executable)
        original = verify.run_suite(framework, command, base, output, "original", timeout=30)
        reference = verify.run_suite(framework, command, candidate, output, "reference", timeout=30)
        selected = reference["results"]["passed"]

        def snapshot(tree):
            content = b"".join(path.name.encode() + path.read_bytes() for path in sorted(Path(tree).glob("*.py")))
            return hashlib.sha256(content).hexdigest()

        kwargs = {
            "run_suite": verify.run_suite,
            "run_command": verify.run_command,
            "snapshot": snapshot,
            "identity": lambda _tree: {"cache_binding_complete": True, "dependencies": "fixed"},
            "expected_source": snapshot(candidate),
            "is_product": lambda path: not path.startswith("test_"),
            "timeout": 30,
        }
        observed = observation.observe_candidate(
            original, base, candidate, framework, selected, command, output / "stable", **kwargs
        )
        self.assertEqual({selected[0]}, set(observed["imports"]))
        self.assertEqual("app.py", observed["imports"][selected[0]]["origin"])
        self.assertEqual(
            observed["candidate"]["execution_environment_sha256"], observed["probe"]["execution_environment_sha256"]
        )
        wrong_framework_python = verify.Framework("unittest", command, python="/missing/not-the-runner")
        observed = observation.observe_candidate(
            original, base, candidate, wrong_framework_python, selected, command, output / "actual-runner", **kwargs
        )
        self.assertEqual({selected[0]}, set(observed["imports"]))
        self.assertEqual(
            {},
            observation.observe_candidate(
                original,
                base,
                candidate,
                framework,
                selected,
                "env IMPORT_CONTEXT=other " + command,
                output / "command-environment",
                **kwargs,
            ),
        )

        def changed_environment(*args, **options):
            with patch.dict(os.environ, {"AUTOCODE_IMPORT_ENV_TEST": "changed"}):
                return verify.run_command(*args, **options)

        rejected = observation.observe_candidate(
            original,
            base,
            candidate,
            framework,
            selected,
            command,
            output / "different-env",
            **(kwargs | {"run_command": changed_environment}),
        )
        self.assertEqual({}, rejected["imports"])
        self.assertIn("probe", rejected)
        self.assertTrue(Path(rejected["path"]).is_file())
        rejected = observation.observe_candidate(
            original,
            base,
            candidate,
            framework,
            selected,
            command,
            output / "incomplete-identity",
            **(kwargs | {"identity": lambda _tree: {"cache_binding_complete": False}}),
        )
        self.assertEqual({}, rejected["imports"])
        self.assertIn("not fully bound", rejected["reason"])
        self.assertTrue(Path(rejected["path"]).is_file())
        identities = iter(
            (
                {"cache_binding_complete": True, "dependencies": "before"},
                {"cache_binding_complete": True, "dependencies": "after"},
            )
        )
        rejected = observation.observe_candidate(
            original,
            base,
            candidate,
            framework,
            selected,
            command,
            output / "different-dependencies",
            **(kwargs | {"identity": lambda _tree: next(identities)}),
        )
        self.assertEqual({}, rejected["imports"])
        self.assertIn("candidate", rejected)
        self.assertTrue(Path(rejected["path"]).is_file())

        def changed_source(*args, **options):
            receipt = verify.run_command(*args, **options)
            with (candidate / "app.py").open("a") as stream:
                stream.write("# changed during probe\n")
            return receipt

        rejected = observation.observe_candidate(
            original,
            base,
            candidate,
            framework,
            selected,
            command,
            output / "different-source",
            **(kwargs | {"run_command": changed_source}),
        )
        self.assertEqual({}, rejected["imports"])
        self.assertIn("probe", rejected)
        self.assertTrue(Path(rejected["path"]).is_file())


if __name__ == "__main__":
    unittest.main()
