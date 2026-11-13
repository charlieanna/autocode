"""The base-suite cache is keyed on the base result, not the candidate tree (#426)."""

import copy
import shlex
import sys
import unittest
from pathlib import Path
from unittest import mock

import autocode_regression as regression
import autocode_util as util
import autocode_verify as verify

from .test_verify import Project, isolated_python


class BaselineIdentityTests(unittest.TestCase):
    def setUp(self):
        self.command = f"{shlex.quote(isolated_python(self))} -m unittest -v"

    def test_candidate_source_edits_do_not_change_the_binding(self):
        project = Project({"app.py": "x = 1\n", "test_app.py": "import unittest\n"})
        self.addCleanup(project.close)
        before = verify.baseline_identity(project.root, command=self.command)
        project.write({"app.py": "x = 2\n", "new_feature.py": "y = 3\n"})
        after = verify.baseline_identity(project.root, command=self.command)
        self.assertEqual(before, after)
        self.assertNotIn("source_revision", after)
        self.assertNotIn("source_metadata", after)

    def test_runtime_change_rebinds(self):
        project = Project({"app.py": "x = 1\n", ".gitignore": "node_modules\n"})
        self.addCleanup(project.close)
        before = verify.baseline_identity(project.root, command=self.command)
        project.write({"node_modules/left-pad/index.js": "1\n"})
        after = verify.baseline_identity(project.root, command=self.command)
        self.assertNotEqual(before.get("dependencies"), after.get("dependencies"))

    def test_global_interpreter_still_offers_reuse(self):
        project = Project({"app.py": "x = 1\n", "test_app.py": "import unittest\n"})
        self.addCleanup(project.close)
        command = f"{shlex.quote(sys._base_executable)} -m unittest -v"
        execution = verify.execution_identity(project.root, command=command)
        self.assertEqual("fresh_execution_only", execution["cache_policy"])
        self.assertFalse(execution["reuse_supported"])
        identity = verify.baseline_identity(project.root, command=command)
        self.assertEqual("baseline_runtime_identity", identity["cache_policy"])
        self.assertTrue(identity["cache_binding_complete"])
        self.assertTrue(identity["reuse_supported"])


class BaselineCacheTests(unittest.TestCase):
    def setUp(self):
        self.command = f"{shlex.quote(isolated_python(self))} -m unittest -v"

    def _receipt(self, base, run_dir):
        directory = Path(run_dir) / "baseline"
        directory.mkdir(parents=True, exist_ok=True)
        log = directory / "suite-on-base.log"
        log.write_text("OK\n")
        return {
            "base": base,
            "command": self.command,
            "receipt": {
                "command": self.command,
                "exit_code": 0,
                "timed_out": False,
                "error": "",
                "output": str(log),
                "output_sha256": util.file_hash(log),
                "results": {
                    "passed": ["t"],
                    "failed": [],
                    "skipped": [],
                    "collection_errors": [],
                    "complete": True,
                    "total": 1,
                },
            },
            "health": "passing",
        }

    def test_base_suite_is_not_rerun_after_a_builder_edit(self):
        project = Project({"app.py": "x = 1\n", "test_app.py": "import unittest\n"})
        self.addCleanup(project.close)
        self.run_dir = project.root / ".autocode" / "run"
        state = {}
        calls = []

        def fake_baseline(workspace, base, run_dir, **kwargs):
            calls.append(base)
            return self._receipt(base, run_dir)

        def ready():
            return regression._baseline(
                state,
                project.root,
                self.run_dir,
                "base-rev",
                verify.command_framework(self.command),
                self.command,
                project.root,
            )

        with mock.patch.object(verify, "baseline", side_effect=fake_baseline):
            first = ready()
            project.write({"app.py": "x = 99\n"})  # Builder edit
            second = ready()
        self.assertEqual(1, len(calls), "base suite must not re-run for a candidate edit")
        self.assertEqual(first["health"], second["health"])

    def test_a_changed_base_reruns(self):
        project = Project({"app.py": "x = 1\n", "test_app.py": "import unittest\n"})
        self.addCleanup(project.close)
        self.run_dir = project.root / ".autocode" / "run"
        state = {}
        calls = []

        def fake_baseline(workspace, base, run_dir, **kwargs):
            calls.append(base)
            return self._receipt(base, run_dir)

        with mock.patch.object(verify, "baseline", side_effect=fake_baseline):
            regression._baseline(
                state,
                project.root,
                self.run_dir,
                "base-a",
                verify.command_framework(self.command),
                self.command,
                project.root,
            )
            regression._baseline(
                state,
                project.root,
                self.run_dir,
                "base-b",
                verify.command_framework(self.command),
                self.command,
                project.root,
            )
        self.assertEqual(["base-a", "base-b"], calls)

    def test_legacy_timeout_never_hides_changed_inputs_or_bad_evidence(self):
        project = Project({"app.py": "x = 1\n"})
        self.addCleanup(project.close)
        run_dir = project.root / ".autocode" / "run"
        framework = verify.command_framework(self.command)
        binding = {
            "base": "base-a",
            "command": self.command,
            "framework": framework.to_dict(),
            "base_patch": None,
            "timeout": 1800,
            "runtime": {"reuse_supported": False, "environment_hash": "old-runtime"},
        }
        record_path = run_dir / "old-baseline.json"
        record = self._receipt("base-a", run_dir)
        record["health"] = "timeout"
        record["receipt"]["timed_out"] = True
        record["receipt"]["exit_code"] = None
        for change in ("base", "command", "framework", "base_patch", "timeout", "hash", "not-timeout", "explicit"):
            with self.subTest(change=change):
                old_binding = copy.deepcopy(binding)
                old_record = copy.deepcopy(record)
                if change in binding:
                    old_binding[change] = "different-input"
                if change == "not-timeout":
                    old_record["receipt"]["timed_out"] = False
                util.atomic_json(record_path, old_record)
                pin = {"path": str(record_path), "sha256": util.file_hash(record_path)}
                if change == "hash":
                    pin["sha256"] = "wrong-hash"
                state = {
                    "settings": {"regression": {"test_timeout": 1800}},
                    "regression_baseline": {
                        **pin,
                        "binding": old_binding,
                        "settled_timeouts": {util.digest(old_binding): pin},
                    },
                }
                with (
                    mock.patch.object(verify, "baseline_identity", return_value={"reuse_supported": False}),
                    mock.patch.object(verify, "baseline", return_value=self._receipt("base-a", run_dir)) as launch,
                ):
                    result = regression._baseline(
                        state,
                        project.root,
                        run_dir,
                        "base-a",
                        framework,
                        self.command,
                        project.root,
                        settled_timeout=change != "explicit",
                    )
                launch.assert_called_once()
                self.assertEqual("passing", result["health"])


if __name__ == "__main__":
    unittest.main()
