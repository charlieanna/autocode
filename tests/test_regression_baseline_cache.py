"""The base-suite cache is keyed on the base result, not the candidate tree (#426)."""
import sys
import unittest
from pathlib import Path
from unittest import mock

from .test_verify import Project
import autocode_regression as regression
import autocode_util as util
import autocode_verify as verify

COMMAND = f"{sys.executable} -m unittest -v"


class BaselineIdentityTests(unittest.TestCase):
    def test_candidate_source_edits_do_not_change_the_binding(self):
        project = Project({"app.py": "x = 1\n", "test_app.py": "import unittest\n"})
        self.addCleanup(project.close)
        before = verify.baseline_identity(project.root, command=COMMAND)
        project.write({"app.py": "x = 2\n", "new_feature.py": "y = 3\n"})
        after = verify.baseline_identity(project.root, command=COMMAND)
        self.assertEqual(before, after)
        self.assertNotIn("source_revision", after)
        self.assertNotIn("source_metadata", after)

    def test_runtime_change_rebinds(self):
        project = Project({"app.py": "x = 1\n", ".gitignore": "node_modules\n"})
        self.addCleanup(project.close)
        before = verify.baseline_identity(project.root, command=COMMAND)
        project.write({"node_modules/left-pad/index.js": "1\n"})
        after = verify.baseline_identity(project.root, command=COMMAND)
        self.assertNotEqual(before.get("dependencies"), after.get("dependencies"))

    def test_global_interpreter_still_offers_reuse(self):
        project = Project({"app.py": "x = 1\n", "test_app.py": "import unittest\n"})
        self.addCleanup(project.close)
        identity = verify.baseline_identity(project.root, command=COMMAND)
        self.assertTrue(identity["cache_binding_complete"])
        self.assertTrue(identity["reuse_supported"])


class BaselineCacheTests(unittest.TestCase):
    def _receipt(self, base, run_dir):
        directory = Path(run_dir) / "baseline"
        directory.mkdir(parents=True, exist_ok=True)
        log = directory / "suite-on-base.log"
        log.write_text("OK\n")
        return {"base": base, "command": COMMAND,
                "receipt": {"command": COMMAND, "exit_code": 0, "timed_out": False, "error": "",
                            "output": str(log), "output_sha256": util.file_hash(log),
                            "results": {"passed": ["t"], "failed": [], "skipped": [],
                                        "collection_errors": [], "complete": True, "total": 1}},
                "health": "passing"}

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
            return regression._baseline(state, project.root, self.run_dir,
                                        "base-rev", verify.command_framework(COMMAND), COMMAND,
                                        project.root)

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
            regression._baseline(state, project.root, self.run_dir,
                                 "base-a", verify.command_framework(COMMAND), COMMAND, project.root)
            regression._baseline(state, project.root, self.run_dir,
                                 "base-b", verify.command_framework(COMMAND), COMMAND, project.root)
        self.assertEqual(["base-a", "base-b"], calls)


if __name__ == "__main__":
    unittest.main()

