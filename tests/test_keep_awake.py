"""A controller keeps a Mac from idling to sleep while it drives a run (#1717)."""

import os
import subprocess
import sys
import unittest
from unittest import mock

import autocode_keep_awake as keep_awake


class KeepAwakeTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(keep_awake._held.clear)
        keep_awake._held.clear()

    def test_one_assertion_per_process_and_no_child_process(self):
        with (
            mock.patch.object(keep_awake.sys, "platform", "darwin"),
            mock.patch.object(keep_awake, "_assert", return_value=7) as asserted,
        ):
            self.assertEqual((True, True), (keep_awake.hold(), keep_awake.hold()))
        asserted.assert_called_once_with("PreventUserIdleSystemSleep", "AutoCode is driving a run")
        self.assertEqual({"pid": os.getpid(), "assertion": 7}, keep_awake._held)

    def test_off_elsewhere_by_setting_or_when_macos_refuses(self):
        for platform, env, granted in (
            ("linux", {}, 7),
            ("darwin", {keep_awake.SETTING: "0"}, 7),
            ("darwin", {}, None),
        ):
            with self.subTest(platform=platform, env=env, granted=granted):
                keep_awake._held.clear()
                with (
                    mock.patch.object(keep_awake.sys, "platform", platform),
                    mock.patch.dict(os.environ, env),
                    mock.patch.object(keep_awake, "_assert", return_value=granted),
                ):
                    self.assertFalse(keep_awake.hold())

    @unittest.skipUnless(sys.platform == "darwin", "macOS power assertions")
    def test_the_assertion_is_this_processs_own_and_ends_with_it(self):
        # A supervised CLI is SIGKILLed when a child outlives it; the assertion must leave none.
        code = (
            "import os, subprocess, sys; sys.path.insert(0, sys.argv[1]); import autocode_keep_awake as k; "
            "assert k.hold(); out = subprocess.run(['pmset', '-g', 'assertions'], capture_output=True, text=True).stdout; "
            "print(f'pid {os.getpid()}(' in out and 'AutoCode is driving a run' in out); "
            "print(subprocess.run(['pgrep', '-P', str(os.getpid())], capture_output=True, text=True).stdout.strip())"
        )
        tools = os.path.dirname(keep_awake.__file__)
        result = subprocess.run([sys.executable, "-c", code, tools], capture_output=True, text=True, timeout=60)
        held, children = (result.stdout.splitlines() + ["", ""])[:2]
        self.assertEqual("True", held, result.stderr)
        self.assertEqual("", children, "no child process may outlive the controller")


if __name__ == "__main__":
    unittest.main()
