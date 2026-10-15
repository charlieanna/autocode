"""Setup never extends the deadline an owner-protected CLI inherited (#622)."""

import contextlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

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

    def admit(self, *, timeout, deadline=None, setup=0.0, expired=False):
        """Fake keeper setup with the real reader for an expired admission.

        Return the fixed owner deadline and the admission deadline supplied to
        its reader. No provider, checkpoint or guarded body may be reached.
        """
        clock = [1000.0]
        sent, waits, launches, actual_reads = [], [], [], []
        actual_read_line = supervision._read_line
        checkpoint = Mock(side_effect=AssertionError("Admission reached checkpoint"))

        def popen(*args, **kwargs):
            launches.append((args, kwargs))
            clock[0] += setup
            return Keeper()

        def read_line(fd, until):
            waits.append(until)
            if expired:
                actual_reads.append(until)
                # This is the original reader, not a supplied rejection. Its
                # nonpositive remaining time must refuse before select/read.
                return actual_read_line(fd, until)
            raise Stop

        expected = (
            self.assertRaisesRegex(supervision.SupervisionError, "Keeper did not arm within the launch limit")
            if expired
            else self.assertRaises(Stop)
        )
        with (
            patch.object(supervision.time, "monotonic", lambda: clock[0]),
            patch.object(supervision.processes, "live_processes", return_value=[OWNER]),
            patch.object(supervision.subprocess, "Popen", popen),
            patch.object(supervision, "_identity", lambda pid: {"pid": pid}),
            patch.object(supervision, "_send", lambda fd, payload: sent.append(payload)),
            patch.object(supervision, "_read_line", read_line),
            patch.object(
                supervision.select, "select", side_effect=AssertionError("Expired admission started an OS wait")
            ) as select_wait,
            patch.object(
                supervision.os, "read", side_effect=AssertionError("Expired admission read an acknowledgement")
            ) as read_ack,
            expected,
            supervision.protect_owner(
                self.lifeline,
                receipt_path=self.receipt,
                timeout=timeout,
                owner_identity=OWNER,
                nonce=NONCE,
                checkpoint=checkpoint,
                **({} if deadline is None else {"deadline": deadline}),
            ),
        ):
            self.fail("Unadmitted keeper allowed the guarded body to continue")
        self.assertEqual(1, len(launches), "Only the controlled keeper was spawned; no provider may continue")
        self.assertEqual(int(expired), len(actual_reads))
        checkpoint.assert_not_called()
        select_wait.assert_not_called()
        read_ack.assert_not_called()
        return sent[0]["deadline"], waits[0]

    def test_slow_setup_does_not_extend_the_keepers_deadline_or_restart_admission(self):
        # Both bounds start at entry. Exactly 15 s or 50 s of setup cannot buy
        # a fresh admission wait; the real reader refuses while the keeper's
        # original 1100 owner deadline remains fixed.
        for setup in (15.0, 50.0):
            with self.subTest(setup=setup):
                self.assertEqual((1100.0, 1015.0), self.admit(timeout=100, setup=setup, expired=True))

    def test_the_inherited_deadline_wins_when_it_is_earlier(self):
        # A short inherited owner bound also ends admission sooner than 15 s.
        self.assertEqual((1010.0, 1010.0), self.admit(timeout=100, deadline=1010, setup=5))
        # A longer inherited bound never renews the entry admission budget.
        self.assertEqual((1060.0, 1015.0), self.admit(timeout=100, deadline=1060, setup=50, expired=True))
        self.assertEqual((1100.0, 1015.0), self.admit(timeout=100, deadline=1500))

    def test_setup_within_admission_budget_never_restarts_its_wait(self):
        self.assertEqual((1100.0, 1015.0), self.admit(timeout=100, setup=5))
        self.assertEqual((1100.0, 1015.0), self.admit(timeout=100, setup=14))

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
