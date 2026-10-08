import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from tests import test_command_flow


class ArtifactRetentionTests(unittest.TestCase):
    def test_fixture_is_copied_before_cleanup(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.dict(os.environ, {"BUILD_AUDIT_ARTIFACTS": directory}):
            # Exercise fixture lifecycle only, never another test method.
            fixture = test_command_flow.ConfigToolFlow()
            self.addCleanup(fixture.doCleanups)
            fixture.setUp()
            root = fixture.root
            self.assertFalse(root.is_relative_to(Path(directory).resolve()))
            (root / "first-failure.txt").write_text("retained evidence")
            fixture.doCleanups()
            self.assertFalse(root.exists())
            self.assertEqual("retained evidence",
                (Path(directory) / root.name / "first-failure.txt").read_text())

    def test_timeout_preserves_text_bytes_and_original_deadline(self):
        for stdout, stderr in (("out", b"err"), (None, None)):
            with self.subTest(stdout=stdout, stderr=stderr), tempfile.TemporaryDirectory() as directory, \
                    mock.patch.dict(os.environ, {"BUILD_AUDIT_ARTIFACTS": directory}):
                fixture = test_command_flow.ConfigToolFlow()
                self.addCleanup(fixture.doCleanups)
                fixture.setUp()
                root = fixture.root
                error = subprocess.TimeoutExpired(["fixture"], 90, output=stdout, stderr=stderr)
                try:
                    with mock.patch("subprocess.run", side_effect=error) as run:
                        with self.assertRaises(subprocess.TimeoutExpired) as raised:
                            fixture.launch("--no-chat")
                        self.assertIs(error, raised.exception)
                        self.assertEqual(90, run.call_args.kwargs["timeout"])
                finally:
                    fixture.doCleanups()
                retained = Path(directory) / root.name
                self.assertEqual((stdout or "").encode(), (retained / "command-timeout.stdout.log").read_bytes())
                self.assertEqual(stderr or b"", (retained / "command-timeout.stderr.log").read_bytes())

    def test_default_cleanup_does_not_retain_fixture(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("BUILD_AUDIT_ARTIFACTS", None)
            fixture = test_command_flow.ConfigToolFlow()
            self.addCleanup(fixture.doCleanups)
            fixture.setUp()
            root = fixture.root
            fixture.doCleanups()
            self.assertFalse(root.exists())
