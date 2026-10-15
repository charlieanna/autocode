"""A guarded CLI's slow keeper discharge is typed and timed, never an untyped traceback (#1143).

No keeper waits here: a fake keeper reports the expired eight-second bound and a fake clock
measures it, so the regression is deterministic. The real discharge receipt's timing is
covered beside the other guarded-CLI discharge tests in test_supervision.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_supervision as supervision
import autocode_supervision_keeper as keeper

OWNER = {"pid": 1, "birth_identity": 1.0}
NONCE = "0" * 32


class SlowDischargeKeeper:
    """Admitted and armed, but still discharging when the CLI's bound expires."""

    pid, returncode = 4242, None

    def __init__(self, clock):
        self.clock = clock
        self.waits = []

    def poll(self):
        return None

    def wait(self, timeout=None):
        self.waits.append(timeout)
        self.clock[0] += timeout
        raise subprocess.TimeoutExpired("keeper", timeout)


class HarnessDischargeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.receipt = Path(temp.name) / "cli-receipt.json"
        read, write = os.pipe()  # the harness keeps the writer: the lifeline stays unreadable
        self.addCleanup(os.close, read)
        self.addCleanup(os.close, write)
        self.lifeline = read

    def test_an_expired_discharge_wait_is_a_typed_supervision_error_naming_the_receipt(self):
        clock = [1000.0]
        slow = SlowDischargeKeeper(clock)
        sent = []
        with (
            patch.object(supervision.time, "monotonic", lambda: clock[0]),
            patch.object(supervision.processes, "live_processes", return_value=[OWNER]),
            patch.object(supervision.subprocess, "Popen", return_value=slow),
            patch.object(supervision, "_identity", lambda pid: {"pid": pid}),
            patch.object(supervision, "_send", lambda fd, payload: sent.append(payload)),
            patch.object(supervision, "_read_line", return_value={"armed": NONCE}),
            patch.object(supervision, "receipt", return_value={"phase": "armed", "cause": None}),
            self.assertRaisesRegex(
                supervision.SupervisionError,
                "^Harness keeper discharge did not finish within its bound: "
                "receipt armed 8.000 s after the finished message$",
            ) as raised,
            supervision.protect_owner(
                self.lifeline, receipt_path=self.receipt, timeout=30, owner_identity=OWNER, nonce=NONCE
            ),
        ):
            pass
        # The CLI's exit handling reports a RuntimeError (exit 2), not an uncaught traceback.
        self.assertIsInstance(raised.exception, RuntimeError)
        self.assertIsInstance(raised.exception.__cause__, subprocess.TimeoutExpired)
        # The finished message was sent once and the eight-second bound itself is unchanged.
        self.assertEqual([{"finished": NONCE}], sent[1:])
        self.assertEqual([8], slow.waits)

    def test_stop_timing_reports_bounded_marks_before_publication(self):
        published = 110.0
        timing = keeper.stop_timing(
            published,
            100.0,
            101.5,
            {"last": (99.25, 101.5, 0.75), "final": (101.5, 109.875, 8.5)},
        )
        self.assertEqual(
            {
                "requested": -10.0,
                "loop_exit": -8.5,
                # The loop's sample was in flight when the finished message arrived.
                "last_sample": {"started": -10.75, "finished": -8.5, "enumeration_seconds": 0.75},
                "final_sample": {"started": -8.5, "finished": -0.125, "enumeration_seconds": 8.5},
            },
            timing,
        )
        # A provider that stopped on its own has no trigger and no discharge sample.
        self.assertEqual(
            {"requested": None, "loop_exit": 0.0, "last_sample": None, "final_sample": None},
            keeper.stop_timing(5.0, None, 5.0, {"last": None, "final": None}),
        )


if __name__ == "__main__":
    unittest.main()
