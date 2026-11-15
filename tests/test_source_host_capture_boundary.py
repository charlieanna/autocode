"""A retained host cannot borrow another invocation's kernel capture ownership."""

import contextlib
import importlib
import io
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import autocode_capture_family as family
import autocode_source_host as host


class SourceHostCaptureBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, dict(os.environ), clear=True))
        os.environ.pop(family.ENVIRONMENT_KEY, None)
        os.environ[host.SETTING] = "1"
        self.enterContext(patch.dict(host._state, {"failed": False, "started": False}))
        self.enterContext(patch.object(host, "_in_request", return_value=False))
        self.enterContext(patch.object(host, "_needs_capability_reexec", return_value=False))
        self.path = self.enterContext(patch.object(host, "socket_path", return_value="/fixture/host.sock"))
        self.socket = self.enterContext(patch.object(host.socket, "socket"))
        self.connection = self.socket.return_value.__enter__.return_value
        self.read = self.enterContext(
            patch.object(host, "_read_line", return_value=b'{"exit_code":0,"stdout":"status\\n","stderr":""}')
        )
        self.start = self.enterContext(patch.object(host, "_start"))
        self.util = Mock()
        self.enterContext(patch.object(host, "_util", return_value=self.util))
        self.enterContext(patch("subprocess.Popen", side_effect=AssertionError("No process in boundary tests")))
        self.stdout = self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def assert_no_host(self):
        self.path.assert_not_called()
        self.socket.assert_not_called()
        self.start.assert_not_called()

    def test_owned_cli_and_snapshot_never_contact_a_retained_host(self):
        os.environ[family.ENVIRONMENT_KEY] = "copied capture capability"
        self.assertIsNone(host.cli_fast_path(["--version"]))
        self.assertIsNone(host.snapshot("/fixture/project", False))
        self.assert_no_host()
        self.assertEqual("", self.stdout.getvalue())

    def test_owned_calls_never_start_a_missing_host(self):
        os.environ[family.ENVIRONMENT_KEY] = "copied capture capability"
        self.connection.connect.side_effect = FileNotFoundError("No retained host")
        self.assertIsNone(host.cli_fast_path(["--version"]))
        self.assertIsNone(host.snapshot("/fixture/project", False))
        self.assert_no_host()

    def test_presence_guard_uses_the_selected_environment_and_skips_install(self):
        unowned = {host.SETTING: "1", "HOME": "/fixture/home"}
        for value in ("", "[]", "copied capture capability", None):
            with self.subTest(value=value):
                supplied = {**unowned, family.ENVIRONMENT_KEY: value}
                before = dict(supplied)
                self.assertFalse(host.enabled(supplied))
                self.assertEqual(before, supplied)
        os.environ[family.ENVIRONMENT_KEY] = ""
        self.assertFalse(host.enabled())
        self.assertEqual(os.name != "nt", host.enabled(unowned))
        host.install()
        self.util.use_snapshot_service.assert_not_called()
        self.assert_no_host()

    def test_invalid_advertisement_stays_intact_and_grants_no_capture_authority(self):
        for value in ("", "not a capability", "[]"):
            with self.subTest(value=value):
                os.environ[family.ENVIRONMENT_KEY] = value
                self.assertIsNone(host.cli_fast_path(["--version"]))
                self.assertIsNone(host.snapshot("/fixture/project", False))
                self.assertEqual(value, os.environ[family.ENVIRONMENT_KEY])
                with self.assertRaises(family.FamilyError):
                    family.channels()
        self.assert_no_host()

    def test_ordinary_warm_cli_and_direct_snapshot_keep_their_existing_behavior(self):
        if os.name == "nt":
            self.assertFalse(host.enabled())
            return
        self.assertEqual(0, host.cli_fast_path(["--version"]))
        self.assertEqual("status\n", self.stdout.getvalue())
        request = json.loads(self.connection.sendall.call_args.args[0])
        self.assertEqual(("cli", ["--version"]), (request["kind"], request["argv"]))
        self.assertNotIn(family.ENVIRONMENT_KEY, request["env"])
        # Direct snapshot callers have never required the enabling flag.
        os.environ.pop(host.SETTING)
        self.read.return_value = b'{"snapshot":{"files":{"app.py":"digest"}}}'
        self.assertEqual({"files": {"app.py": "digest"}}, host.snapshot("/fixture/project", False))
        self.start.assert_not_called()

    def test_qualified_package_and_source_imports_apply_the_same_guard(self):
        name = "_source_host_capture_boundary_package"
        package = types.ModuleType(name)
        package.__path__ = [str(Path(host.__file__).resolve().parent)]
        with patch.dict(sys.modules, {name: package}):
            qualified = importlib.import_module(name + ".autocode_source_host")
            environment = {host.SETTING: "1", family.ENVIRONMENT_KEY: ""}
            self.assertFalse(host.enabled(environment))
            self.assertFalse(qualified.enabled(environment))


if __name__ == "__main__":
    unittest.main()
