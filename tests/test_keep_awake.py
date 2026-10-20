"""A controller keeps a Mac from idling to sleep while it drives a run (#1717)."""

import os
import unittest
from unittest import mock

import autocode_keep_awake as keep_awake


class KeepAwakeTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(keep_awake._held.clear)
        keep_awake._held.clear()

    def hold(self, platform="darwin", env=None, found="/usr/bin/caffeinate"):
        with (
            mock.patch.object(keep_awake.sys, "platform", platform),
            mock.patch.dict(os.environ, env or {}, clear=False),
            mock.patch.object(keep_awake.shutil, "which", return_value=found),
            mock.patch.object(keep_awake.subprocess, "Popen") as started,
        ):
            held = keep_awake.hold(), keep_awake.hold()
        return held, started

    def test_a_mac_holds_one_idle_sleep_assertion_tied_to_this_process(self):
        held, started = self.hold()
        self.assertEqual((True, True), held)
        self.assertEqual(1, started.call_count, "one assertion per process")
        self.assertEqual(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())], started.call_args.args[0])

    def test_off_elsewhere_by_setting_or_without_caffeinate(self):
        for kwargs in ({"platform": "linux"}, {"env": {keep_awake.SETTING: "0"}}, {"found": None}):
            with self.subTest(kwargs=kwargs):
                keep_awake._held.clear()
                held, started = self.hold(**kwargs)
                self.assertEqual((False, False), held)
                started.assert_not_called()


if __name__ == "__main__":
    unittest.main()
