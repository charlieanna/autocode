"""Setup never extends the deadline an owner-protected CLI inherited (#622)."""

import contextlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_supervision as supervision
import autocode_supervision_cli as supervision_cli

OWNER = {"pid": 1, "birth_identity": 1.0}
NONCE = "0" * 32


class Stop(Exception):
    """Ends protect_owner right after it hands the keeper its deadline."""


class Keeper:
    pid, returncode = 4242, 0

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0


class OwnerDeadlineTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.receipt = Path(temp.name) / "cli-receipt.json"
        read, write = os.pipe()
        self.addCleanup(os.close, read)
        self.addCleanup(os.close, write)
        self.lifeline = read

    def admit(self, *, timeout, deadline=None, setup=0.0):
        """protect_owner on a fake clock, its keeper launch taking ``setup`` seconds;
        (deadline sent to the keeper, deadline of the admission wait)."""
        clock = [1000.0]
        sent, waits = [], []

        def popen(*args, **kwargs):
            clock[0] += setup
            return Keeper()

        def read_line(fd, until):
            waits.append(until)
            raise Stop

        with (
            patch.object(supervision.time, "monotonic", lambda: clock[0]),
            patch.object(supervision.processes, "live_processes", return_value=[OWNER]),
            patch.object(supervision.subprocess, "Popen", popen),
            patch.object(supervision, "_identity", lambda pid: {"pid": pid}),
            patch.object(supervision, "_send", lambda fd, payload: sent.append(payload)),
            patch.object(supervision, "_read_line", read_line),
            self.assertRaises(Stop),
            supervision.protect_owner(
                self.lifeline,
                receipt_path=self.receipt,
                timeout=timeout,
                owner_identity=OWNER,
                nonce=NONCE,
                **({} if deadline is None else {"deadline": deadline}),
            ),
        ):
            pass
        return sent[0]["deadline"], waits[0]

    def test_slow_setup_does_not_extend_the_keepers_deadline_or_restart_admission(self):
        # Entered at 1000 with 100 s; launching the keeper took 50 s. The keeper still gets 1100
        # (not 1150), and the admission wait ends at 1065, the 15 s cap from when it starts.
        self.assertEqual((1100.0, 1065.0), self.admit(timeout=100, setup=50))

    def test_the_inherited_deadline_wins_when_it_is_earlier(self):
        # The harness's absolute deadline 1060 is earlier than entry + 100: the admission wait shrinks to it.
        self.assertEqual((1060.0, 1060.0), self.admit(timeout=100, deadline=1060, setup=50))
        self.assertEqual((1100.0, 1015.0), self.admit(timeout=100, deadline=1500))

    def test_invalid_and_expired_deadlines_are_refused_before_a_keeper_starts(self):
        for timeout, deadline, message in (
            (True, None, "invalid"),
            (float("nan"), None, "invalid"),
            (float("inf"), None, "invalid"),
            ("5", None, "invalid"),
            (30, True, "invalid"),
            (30, float("nan"), "invalid"),
            (0, None, "expired"),
            (30, 999.0, "expired"),
        ):
            with (
                self.subTest(timeout=timeout, deadline=deadline),
                patch.object(supervision.time, "monotonic", lambda: 1000.0),
                patch.object(supervision.processes, "live_processes", return_value=[OWNER]),
                patch.object(supervision.subprocess, "Popen", side_effect=AssertionError("no keeper may start")),
                self.assertRaisesRegex(supervision.SupervisionError, message),
                supervision.protect_owner(
                    self.lifeline,
                    receipt_path=self.receipt,
                    timeout=timeout,
                    owner_identity=OWNER,
                    nonce=NONCE,
                    deadline=deadline,
                ),
            ):
                pass

    def test_the_cli_guard_passes_the_declared_deadline_through(self):
        seen = {}

        @contextlib.contextmanager
        def protect(fd, **kwargs):
            seen.update(kwargs)
            yield

        read, write = os.pipe()
        os.close(write)
        packet = {"receipt": str(self.receipt), "owner": OWNER, "nonce": NONCE, "deadline": 1234.5}
        with (
            patch.object(supervision_cli, "declaration", return_value=(packet, 20.0)),
            patch.object(supervision_cli.supervision, "protect_owner", protect),
            supervision_cli.guard([supervision_cli.FLAG, str(read), "task"]) as argv,
        ):
            self.assertEqual(["task"], argv)
        self.assertEqual((20.0, 1234.5), (seen["timeout"], seen["deadline"]))


if __name__ == "__main__":
    unittest.main()
