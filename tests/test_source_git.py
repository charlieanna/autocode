"""Required Git source reads fail as source availability, never as a model answer."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_test_root as test_root
import autocode_util as util
import autocode_verify as verify


class SourceGitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "value.py").write_text("value = 1\n")
        util.git(self.root, "add", "value.py")
        util.git(self.root, "-c", "user.name=Fixture", "-c", "user.email=f@example.test", "commit", "-qm", "base")
        self.base = util.git(self.root, "rev-parse", "HEAD")
        (self.root / "value.py").write_text("value = 2\n")

    def refusal(self, operation, error):
        original = subprocess.run

        def run(command, *args, **kwargs):
            if command[0] == "git" and operation in command:
                if error == "os":
                    raise OSError("source read denied")
                text = kwargs.get("text") or kwargs.get("encoding")
                result = subprocess.CompletedProcess(
                    command, 128, "" if text else b"", "source read denied" if text else b"source read denied"
                )
                if kwargs.get("check"):
                    raise subprocess.CalledProcessError(result.returncode, command, result.stdout, result.stderr)
                return result
            return original(command, *args, **kwargs)

        return patch.object(subprocess, "run", side_effect=run)

    def assert_source_hold(self, action):
        try:
            action()
        except Exception as error:
            self.assertIsInstance(error, util.Paused)
            self.assertEqual("PAUSED_SOURCE_UNAVAILABLE", error.status)
            self.assertIn("source read denied", str(error))
        else:
            self.fail("A required source read must not accept an unavailable inventory")

    def test_required_inventory_queries_share_the_source_hold_for_command_and_os_errors(self):
        for operation in ("rev-parse", "diff-files", "ls-files", "diff-index"):
            for error in ("exit", "os"):
                with self.subTest(operation=operation, error=error), self.refusal(operation, error):
                    self.assert_source_hold(lambda: verify.changed_files(self.root, self.base))
        self.assertEqual({"value.py": "modified"}, verify.changed_files(self.root, self.base))

    def test_pinned_policy_and_current_snapshot_use_the_same_failure_classification(self):
        for error in ("exit", "os"):
            with self.subTest(read="policy", error=error), self.refusal("cat-file", error):
                self.assert_source_hold(lambda: test_root._blob(self.root, self.base + ":value.py"))
            with self.subTest(read="snapshot", error=error), self.refusal("ls-files", error):
                self.assert_source_hold(lambda: util.snapshot(self.root))

    def test_git_write_failure_keeps_its_existing_classification(self):
        with self.refusal("update-index", "exit"), self.assertRaises(RuntimeError) as failed:
            verify._git(self.root, "update-index", "--refresh")
        self.assertNotIsInstance(failed.exception, util.Paused)

    def test_source_holds_are_not_swallowed_by_policy_or_original_suite_fallbacks(self):
        unavailable = util.Paused("PAUSED_SOURCE_UNAVAILABLE", "source read denied")
        with self.subTest(read="policy"), patch.object(verify.test_roots, "read_policy", side_effect=unavailable):
            self.assert_source_hold(
                lambda: verify._rooted_python(self.root, "pkg", ["pkg/pytest.ini", "pkg/test_value.py"], sys.executable)
            )
        with self.subTest(read="base suite"), patch.object(verify, "_base_suite_definition", side_effect=unavailable):
            self.assert_source_hold(
                lambda: verify._run_suite_base_definition(
                    None, "python3 -m unittest", self.root, self.base, {}, self.root / "run", None, timeout=1
                )
            )
