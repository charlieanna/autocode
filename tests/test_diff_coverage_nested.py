"""Coverage observations retain the proving command and its owned data."""

import importlib.util
import json
import os
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_changed_code_measurement as measurement
import autocode_diff_coverage as coverage
import autocode_verify as verify


class NestedCoverageTests(unittest.TestCase):
    DIFF = "diff --git a/sample.py b/sample.py\n--- a/sample.py\n+++ b/sample.py\n@@ -0,0 +1,2 @@\n+def value():\n+    return 7\n"

    @unittest.skipUnless(
        all(importlib.util.find_spec(name) for name in ("coverage", "pytest", "pytest_cov")),
        "requires the project test interpreter to already have coverage, pytest, and pytest-cov",
    )
    def _real_pytest_collection(self, extra=(), *, no_plugin=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "sample.py").write_text("def value():\n    return 7\n")
            (root / "test_sample.py").write_text(
                "from sample import value\n\ndef test_value():\n    assert value() == 7\n"
            )
            (root / "pytest.ini").write_text(
                "[pytest]\n" if no_plugin else "[pytest]\naddopts = --cov=sample --cov-report=\n"
            )
            command = shlex.join([sys.executable, "-m", "pytest", "-q", "test_sample.py", *extra])
            seen = []

            def run(command, log):
                seen.append(command)
                return verify.run_command(command, root, log, timeout=30)

            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("COVERAGE_FILE", None)
                if no_plugin:
                    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
                else:
                    os.environ.pop("PYTEST_DISABLE_PLUGIN_AUTOLOAD", None)
                result = coverage.collect(
                    type("Framework", (), {"name": "pytest"})(),
                    command,
                    root,
                    root / "evidence",
                    lambda: self.DIFF,
                    run,
                )
            self.assertTrue(result["collected"], result)
            self.assertEqual([], result["unmeasured"])
            self.assertEqual([], result["uncovered"])
            self.assertEqual([0, 0], [row["exit_code"] for row in result["command_receipts"]])
            inventory = measurement.inventory(root, [("sample.py", "added")], self.DIFF)
            envelope = measurement.coverage_envelope(
                result,
                inventory,
                source_revision="source",
                proof_context="context",
                receipts=result["command_receipts"],
                root=root / "evidence",
            )
            self.assertEqual("coverage.py", envelope["tool"])
            self.assertTrue(envelope["files"][0]["executed_lines"])
            self.assertIn("-m pytest -q test_sample.py", seen[0])
            self.assertEqual(list(extra), shlex.split(seen[0])[shlex.split(seen[0]).index("test_sample.py") + 1 :])

    @unittest.skipUnless(
        all(importlib.util.find_spec(name) for name in ("coverage", "pytest", "pytest_cov")),
        "requires the project test interpreter to already have coverage, pytest, and pytest-cov",
    )
    def test_pytest_cov_data_is_collected_without_changing_proving_arguments(self):
        for extra in [(), ("--cov-context=test",), ("--cov-append",), ("--cov=sample",), ("--no-cov",)]:
            with self.subTest(extra=extra):
                self._real_pytest_collection(extra)
        self._real_pytest_collection(no_plugin=True)

    def test_no_plugin_keeps_outer_data_and_fresh_paths_are_unique(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(coverage, "_probe", return_value=True),
            patch.dict(os.environ, {}, clear=False),
        ):
            root = Path(directory).resolve()
            os.environ.pop("COVERAGE_FILE", None)
            framework = type("Framework", (), {"name": "pytest"})()
            command = shlex.join(["env", "SAMPLE=literal value", sys.executable, "-m", "pytest", "-q"])
            first = coverage.prepare(framework, command, root, root / "evidence")
            second = coverage.prepare(framework, command, root, root / "evidence")
            self.assertNotEqual(first["nested_data"], second["nested_data"])
            words = shlex.split(first["command"])
            self.assertIn("SAMPLE=literal value", words)
            self.assertEqual(["-m", "pytest", "-q"], words[-3:])
            self.assertFalse(first["nested_data"].exists())

    def test_redirected_nested_data_and_failed_run_cannot_authorize_coverage(self):
        for behavior in ("symlink", "failed", "directory", "hardlink", "replaced_parent"):
            with (
                self.subTest(behavior=behavior),
                tempfile.TemporaryDirectory() as directory,
                patch.object(coverage, "_probe", return_value=True),
                patch.dict(os.environ, {}, clear=False),
            ):
                root = Path(directory).resolve()
                os.environ.pop("COVERAGE_FILE", None)
                (root / "sample.py").write_text("def value():\n    return 7\n")
                calls = []

                def run(command, log):
                    calls.append(command)
                    nested = next(
                        word.split("=", 1)[1] for word in shlex.split(command) if word.startswith("COVERAGE_FILE=")
                    )
                    if behavior == "symlink":
                        Path(nested).symlink_to(root / "elsewhere")
                    elif behavior == "directory":
                        Path(nested).mkdir()
                    elif behavior == "hardlink":
                        other = root / "other-data"
                        other.write_text("old data")
                        os.link(other, nested)
                    elif behavior == "replaced_parent":
                        parent = Path(nested).parent
                        parent.rmdir()
                        parent.mkdir()
                    return {"exit_code": 1 if behavior == "failed" else 0, "output": str(log), "timed_out": False}

                result = coverage.collect(
                    type("Framework", (), {"name": "pytest"})(),
                    shlex.join([sys.executable, "-m", "pytest", "-q"]),
                    root,
                    root / "evidence",
                    lambda: self.DIFF,
                    run,
                )
                self.assertFalse(result["collected"], result)
                self.assertEqual(1, len(calls))

    def test_failed_or_redirected_final_report_is_not_accepted(self):
        for behavior in ("failed", "symlink", "hardlink"):
            with (
                self.subTest(behavior=behavior),
                tempfile.TemporaryDirectory() as directory,
                patch.object(coverage, "_probe", return_value=True),
                patch.dict(os.environ, {}, clear=False),
            ):
                root = Path(directory).resolve()
                os.environ.pop("COVERAGE_FILE", None)

                def run(command, log):
                    words = shlex.split(command)
                    if "run" in words:
                        nested = next(word.split("=", 1)[1] for word in words if word.startswith("COVERAGE_FILE="))
                        Path(nested).write_text("fresh")
                        return {"exit_code": 0, "output": str(log), "timed_out": False}
                    report = Path(words[words.index("-o") + 1])
                    outside = root / "elsewhere.json"
                    outside.write_text(
                        json.dumps({"files": {"sample.py": {"executed_lines": [1, 2], "missing_lines": []}}})
                    )
                    if behavior == "symlink":
                        report.symlink_to(outside)
                    elif behavior == "hardlink":
                        os.link(outside, report)
                    else:
                        report.write_bytes(outside.read_bytes())
                    return {"exit_code": 1 if behavior == "failed" else 0, "output": str(log), "timed_out": False}

                result = coverage.collect(
                    type("Framework", (), {"name": "pytest"})(),
                    shlex.join([sys.executable, "-m", "pytest", "-q"]),
                    root,
                    root / "evidence",
                    lambda: self.DIFF,
                    run,
                )
                self.assertFalse(result["collected"], result)
                self.assertEqual(2, len(result["command_receipts"]))

    def test_data_replaced_during_finalization_is_not_accepted(self):
        for behavior in ("symlink", "rewrite", "replace_parent", "swap_restore_parent"):
            with (
                self.subTest(behavior=behavior),
                tempfile.TemporaryDirectory() as directory,
                patch.object(coverage, "_probe", return_value=True),
                patch.dict(os.environ, {}, clear=False),
            ):
                root = Path(directory).resolve()
                os.environ.pop("COVERAGE_FILE", None)
                target = None

                def run(command, log):
                    nonlocal target
                    words = shlex.split(command)
                    if "run" in words:
                        target = Path(
                            next(word.split("=", 1)[1] for word in words if word.startswith("COVERAGE_FILE="))
                        )
                        target.write_text("fresh")
                    else:
                        assert target is not None
                        report = Path(words[words.index("-o") + 1])
                        report.write_text(
                            json.dumps({"files": {"sample.py": {"executed_lines": [1, 2], "missing_lines": []}}})
                        )
                        if behavior == "symlink":
                            target.unlink()
                            target.symlink_to(root / "elsewhere")
                        elif behavior == "rewrite":
                            target.write_text("replaced content")
                        elif behavior == "swap_restore_parent":
                            parent = target.parent
                            held = parent.with_name(parent.name + "-held")
                            parent.rename(held)
                            parent.mkdir()
                            target.write_text("substitute")
                            target.unlink()
                            parent.rmdir()
                            held.rename(parent)
                        else:
                            target.unlink()
                            target.parent.rmdir()
                            target.parent.mkdir()
                            target.write_text("fresh")
                    return {"exit_code": 0, "output": str(log), "timed_out": False}

                result = coverage.collect(
                    type("Framework", (), {"name": "pytest"})(),
                    shlex.join([sys.executable, "-m", "pytest", "-q"]),
                    root,
                    root / "evidence",
                    lambda: self.DIFF,
                    run,
                )
                self.assertFalse(result["collected"], result)

    def test_partial_nested_report_does_not_cover_unmeasured_files(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(coverage, "_probe", return_value=True),
            patch.dict(os.environ, {}, clear=False),
        ):
            root = Path(directory).resolve()
            os.environ.pop("COVERAGE_FILE", None)
            diff = (
                self.DIFF + "diff --git a/other.py b/other.py\n--- a/other.py\n+++ b/other.py\n@@ -0,0 +1 @@\n+x = 1\n"
            )

            def run(command, log):
                words = shlex.split(command)
                if "-m" in words and "run" in words:
                    nested = next(word.split("=", 1)[1] for word in words if word.startswith("COVERAGE_FILE="))
                    Path(nested).write_text("fresh owned data")
                else:
                    Path(words[words.index("-o") + 1]).write_text(
                        json.dumps({"files": {"sample.py": {"executed_lines": [1, 2], "missing_lines": []}}})
                    )
                return {"exit_code": 0, "output": str(log), "timed_out": False}

            result = coverage.collect(
                type("Framework", (), {"name": "pytest"})(),
                shlex.join([sys.executable, "-m", "pytest", "-q"]),
                root,
                root / "evidence",
                lambda: diff,
                run,
            )
            self.assertTrue(result["collected"], result)
            self.assertEqual(["other.py"], result["unmeasured"])

    def test_explicit_coverage_file_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(coverage, "_probe", return_value=True):
            root = Path(directory).resolve()
            framework = type("Framework", (), {"name": "pytest"})()
            command = shlex.join(["env", "COVERAGE_FILE=caller-data", sys.executable, "-m", "pytest", "-q"])
            spec = coverage.prepare(framework, command, root, root / "evidence")
            self.assertNotIn("nested_data", spec)
            self.assertIn("COVERAGE_FILE=caller-data", shlex.split(spec["command"]))
            with patch.dict(os.environ, {"COVERAGE_FILE": "inherited-data"}):
                # collect()'s only product caller passes run_command's default
                # test_environment, which inherits this non-secret setting.
                self.assertEqual("inherited-data", verify.test_environment(root)["COVERAGE_FILE"])
                spec = coverage.prepare(
                    framework, shlex.join([sys.executable, "-m", "pytest", "-q"]), root, root / "evidence2"
                )
                self.assertNotIn("nested_data", spec)


if __name__ == "__main__":
    unittest.main()
