"""Whether a saved stage's process still holds the workspace (autocode_run_records.assert_stage_stopped)."""

import subprocess
import sys
import time
import unittest

import autocode_process as processes
import autocode_run_records as run_records
import autocode_util as util


class StageStoppedTests(unittest.TestCase):
    def test_a_zombie_stage_process_has_exited_and_does_not_hold_the_workspace(self):
        # Where PID 1 never reaps orphans (a cloud container's process API), a finished stage
        # process stays a zombie for good; it can write nothing (#668).
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        self.addCleanup(child.wait)
        deadline = time.monotonic() + 10
        while (processes.process_table({child.pid}).get(child.pid) or {}).get("state") != "Z":
            self.assertLess(time.monotonic(), deadline, "the child never became a zombie")
            time.sleep(0.01)
        run_records.assert_stage_stopped({"pid": child.pid, "exit_code": None})

    def test_a_live_stage_process_still_holds_the_workspace(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        with self.assertRaisesRegex(util.Paused, f"Stage process {child.pid} still exists"):
            run_records.assert_stage_stopped({"pid": child.pid, "exit_code": None})

    def test_a_collected_exit_or_a_vanished_pid_never_holds_it(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        run_records.assert_stage_stopped({"pid": child.pid, "exit_code": None})
        run_records.assert_stage_stopped({"pid": child.pid, "exit_code": 0})


if __name__ == "__main__":
    unittest.main()
