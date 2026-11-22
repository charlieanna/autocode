"""#4028: a capture family a stage's tools leave unsettled pauses that run, typed, until its cleanup is proven.

`autocode capture --compare-base` is a separate CLI an agent runs. When it cannot confirm its supervised
command stopped, it keeps its custody journal and exits with error text, and before this fix the run that
launched the agent carried on as if nothing happened. Core now reads the store the provider's own
environment names, before and after the attempt.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

import autocode_capture_handoff as handoff
import autocode_metadata_custody as custody
import autocode_operational_block as operational_block
import autocode_taskrun as taskrun

from .test_metadata_custody import BOOT

TOOLS = Path(__file__).resolve().parents[1] / "tools"
OPTIONS = ("--evidence-provenance", "fake", "--engine", "codex")


def journal(boot=BOOT):
    member = uuid.uuid4().hex
    return {
        "schema": 2,
        "group_nonce": uuid.uuid4().hex,
        "boot_id": boot,
        "uid": os.geteuid(),
        "attempts": [{"nonce": member, "result_file": f"result-{member}.json"}],
    }


def leave(store: Path, row: dict) -> None:
    """The files a capture CLI that could not confirm its command stopped leaves in its store."""
    store.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name, body in (("admission.lock", ""), ("pending.json", json.dumps(row))):
        descriptor = os.open(store / name, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        os.write(descriptor, body.encode())
        os.close(descriptor)


def settle(store: Path, row: dict) -> None:
    for member in row["attempts"]:
        clean = {"schema": 1, "nonce": member["nonce"], "clean": True, "started": True, "returncode": 0}
        descriptor = os.open(store / member["result_file"], os.O_CREAT | os.O_WRONLY, 0o600)
        os.write(descriptor, json.dumps({**clean, "expired": False}).encode())
        os.close(descriptor)


@unittest.skipUnless(os.name == "posix", "private custody store permissions")
class HandoffTests(unittest.TestCase):
    """The observation itself, over the environment Core handed the provider."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="capture-handoff-")
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        for target, attribute, value in ((custody.sys, "platform", "linux"), (custody, "_boot_id", lambda: BOOT)):
            context = patch.object(target, attribute, value)
            context.start()
            self.addCleanup(context.stop)
        # The provider's environment, not Core's own: a role's isolation can redirect its state directory.
        self.environment = {"HOME": "/nonexistent", "XDG_STATE_HOME": str(self.home / "role-state")}
        self.store = self.home / "role-state/autocode/metadata"

    def test_a_journal_the_attempt_left_is_a_typed_hold_bound_to_that_exact_journal(self):
        start = handoff.before(self.environment)
        row = journal()
        leave(self.store, row)
        hold = handoff.after(start, self.environment)
        self.assertIsInstance(hold, custody.CustodyUncertain)
        self.assertEqual(
            {"schema": 1, "scope": "metadata_check", "root": str(self.store), "pending": row}, hold.binding
        )
        self.assertEqual(
            {"root": str(self.store), "group_nonce": row["group_nonce"], "bound": True}, handoff.describe(hold)
        )
        self.assertFalse(custody.recover(hold.binding), "no cleanup was proven")
        settle(self.store, row)
        self.assertTrue(custody.recover(hold.binding), "the original family's own receipt releases it")

    def test_no_journal_a_settled_one_or_one_held_before_the_attempt_is_not_this_attempt_s(self):
        self.assertIsNone(handoff.after(handoff.before(self.environment), self.environment))
        earlier = journal()
        leave(self.store, earlier)
        start = handoff.before(self.environment)
        self.assertEqual({"readable": True, "group": earlier["group_nonce"]}, start)
        self.assertIsNone(handoff.after(start, self.environment), "an earlier hold already refused every admission")
        settle(self.store, earlier)
        self.assertIsNone(handoff.after(handoff.before({**self.environment}), self.environment))
        later = journal()
        leave(self.store, later)
        self.assertIsNotNone(handoff.after(start, self.environment), "a different journal than the one held before")

    def test_a_store_that_became_unreadable_is_a_hold_no_cleanup_can_release(self):
        start = handoff.before(self.environment)
        leave(self.store, journal())
        (self.store / "pending.json").write_text("{tampered")
        hold = handoff.after(start, self.environment)
        self.assertIsInstance(hold, custody.CustodyUncertain)
        self.assertIsNone(hold.binding)
        self.assertFalse(custody.recover(hold.binding))
        self.assertIsNone(handoff.after({"readable": False, "group": None}, self.environment))

    def test_another_boot_s_journal_or_core_s_own_store_is_read_from_the_given_environment(self):
        leave(self.store, journal())
        other = {"HOME": str(self.home / "elsewhere"), "XDG_STATE_HOME": ""}
        self.assertIsNone(handoff.after(handoff.before(other), other), "a store the provider never named")
        self.assertEqual(self.home / "elsewhere/.local/state/autocode/metadata", custody.store_root(other))

    def test_the_typed_hold_survives_the_stage_s_pause_wrapper(self):
        start = handoff.before(self.environment)
        leave(self.store, journal())
        hold = handoff.after(start, self.environment)
        self.assertIsNotNone(hold)
        try:
            raise operational_block.util.Paused("PAUSED_PROCESS_CLEANUP", str(hold)) from hold
        except operational_block.util.Paused as wrapped:
            self.assertIs(hold, operational_block.error(wrapped))


class CheckA(threading.Thread):
    """A metadata check of another invocation on the same store: it holds admission with one armed member.

    Its group and admission wait for nothing (timeout=None), so no patched clock can expire it; it publishes
    its clean result only when released, then reconciles at its group's exit.
    """

    def __init__(self):
        super().__init__(daemon=True)
        self.armed, self.release, self.exited = threading.Event(), threading.Event(), threading.Event()
        self.errors, self.group = [], None

    def run(self):
        try:
            with custody.group(timeout=None) as owner:
                self.group = owner.nonce
                with custody.admission(timeout=None) as scope:
                    scope.arm()
                    self.armed.set()
                    if not self.release.wait(30):
                        raise AssertionError("check A was never released")
                    scope.result.seek(0)
                    scope.result.truncate()
                    clean = {"schema": 1, "nonce": scope.nonce, "clean": True, "started": True, "returncode": 0}
                    scope.result.write(json.dumps({**clean, "expired": False}).encode())
                    scope.result.flush()
                    os.fsync(scope.result.fileno())
        except BaseException as error:
            self.errors.append(error)
        finally:
            self.exited.set()


def observing(thread, original, act):
    """A patch for custody.util.sleep/monotonic that acts only on the observing ``thread``."""

    def patched(*arguments):
        return act(*arguments) if threading.current_thread() is thread else original(*arguments)

    return patched


@unittest.skipUnless(os.name == "posix", "private custody store permissions")
class ConcurrentObservationTests(unittest.TestCase):
    """#4099: the observation never reads a live invocation's in-progress journal as a hold."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="capture-handoff-race-")
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        for target, attribute, value in ((custody.sys, "platform", "linux"), (custody, "_boot_id", lambda: BOOT)):
            context = patch.object(target, attribute, value)
            context.start()
            self.addCleanup(context.stop)
        state = str(self.home / "role-state")
        # Core's own checks (custody.group) read its ambient environment; the provider's names the same store.
        environment = patch.dict(os.environ, {"XDG_STATE_HOME": state})
        environment.start()
        self.addCleanup(environment.stop)
        self.environment = {"HOME": "/nonexistent", "XDG_STATE_HOME": state}
        self.store = self.home / "role-state/autocode/metadata"
        self.observer = threading.current_thread()

    def check_a(self):
        check = CheckA()
        check.start()

        def stop():
            check.release.set()
            check.join(30)

        self.addCleanup(stop)
        self.assertTrue(check.armed.wait(30), "check A never armed its member")
        return check

    def assert_a_settled(self, check):
        check.release.set()
        check.join(30)
        self.assertFalse(check.is_alive())
        self.assertEqual([], check.errors)
        self.assertFalse((self.store / "pending.json").exists())
        self.assertTrue((self.store / f"collected-{check.group}.json").exists())

    def test_c1_concurrent_checks_never_pause_on_a_live_reconciling_record(self):
        start = handoff.before(self.environment)
        self.assertEqual({"readable": True, "group": None}, start)
        check = self.check_a()
        reached = []

        def first_sleep(seconds):
            reached.append(check.release.is_set())
            if len(reached) == 1:
                check.release.set()
                self.assertTrue(check.exited.wait(30), "check A group never exited")

        record, persist = {}, Mock()
        with patch.object(custody.util, "sleep", observing(self.observer, custody.util.sleep, first_sleep)):
            handoff.require_settled(start, self.environment, record, persist)
        self.assertTrue(reached, "B never waited for A's admission lock")
        self.assertFalse(reached[0], "the wait was reached before A was released")
        self.assertNotIn("capture_custody", record)
        persist.assert_not_called()
        self.assert_a_settled(check)

    def test_c3_live_owner_beyond_allowance_reports_busy_not_uncertain(self):
        check = self.check_a()
        before = (self.store / "pending.json").read_bytes()
        clock = [0.0]

        def advance(seconds):
            clock[0] += seconds

        with (
            patch.object(custody.util, "monotonic", observing(self.observer, custody.util.monotonic, lambda: clock[0])),
            patch.object(custody.util, "sleep", observing(self.observer, custody.util.sleep, advance)),
        ):
            with self.assertRaises(custody.MetadataAdmissionBusy) as caught:
                custody.unsettled(self.environment, timeout=0)
            self.assertNotIsInstance(caught.exception, custody.CustodyUncertain)
            self.assertEqual(0, clock[0], "timeout=0 gives up on its first lock try")
            self.assertEqual({"readable": True, "group": None}, handoff.before(self.environment))
            self.assertAlmostEqual(handoff.ALLOWANCE_SECONDS, clock[0])
            self.assertIsNone(handoff.after({"readable": True, "group": None}, self.environment))
            self.assertAlmostEqual(2 * handoff.ALLOWANCE_SECONDS, clock[0])
        self.assertEqual(before, (self.store / "pending.json").read_bytes())
        self.assert_a_settled(check)

    def test_unsettled_by_default_tries_a_live_owner_s_lock_once_and_reports_busy(self):
        check = self.check_a()
        before = (self.store / "pending.json").read_bytes()
        clock, slept = [0.0], []

        def advance(seconds):
            slept.append(seconds)
            clock[0] += seconds

        with (
            patch.object(custody.util, "monotonic", observing(self.observer, custody.util.monotonic, lambda: clock[0])),
            patch.object(custody.util, "sleep", observing(self.observer, custody.util.sleep, advance)),
        ):
            with self.assertRaises(custody.MetadataAdmissionBusy) as caught:
                custody.unsettled(self.environment)
        self.assertNotIsInstance(caught.exception, custody.CustodyUncertain)
        self.assertEqual(0, clock[0], "with no timeout given the observation waits for nothing")
        self.assertEqual([], slept)
        self.assertEqual(before, (self.store / "pending.json").read_bytes())
        self.assert_a_settled(check)

    def test_c4_unsettled_member_hold_still_raises(self):
        with self.assertRaises(custody.ProcessError):
            with custody.admission(timeout=1) as scope:
                scope.arm()
        row = json.loads((self.store / "pending.json").read_text())
        self.assertEqual(scope.nonce, row["attempts"][0]["nonce"])
        later = custody.util.monotonic() + 86400
        with patch.object(custody.util, "monotonic", return_value=later):
            hold = handoff.after({"readable": True, "group": None}, self.environment)
            self.assertIsInstance(hold, custody.CustodyUncertain)
            self.assertEqual(row, hold.binding["pending"])
            self.assertEqual((self.store, row), custody.unsettled(self.environment))
            with self.assertRaises(custody.CustodyUncertain):
                with custody.group(timeout=1):
                    self.fail("a fresh group was admitted past an unsettled member")
            self.assertFalse(custody.recover(hold.binding))

    def test_c10_leftover_hold_seen_after_brief_lock_holder_releases(self):
        start = handoff.before(self.environment)
        row = journal()
        leave(self.store, row)
        holder = open(self.store / "admission.lock", "r+b")  # noqa: SIM115 - held across the observation
        self.addCleanup(holder.close)
        custody.util.lock_file(holder)  # a group that refuses the leftover at entry, for a moment
        calls = []

        def release(seconds):
            calls.append(seconds)
            if len(calls) == 1:
                custody.util.unlock_file(holder)

        with patch.object(custody.util, "sleep", observing(self.observer, custody.util.sleep, release)):
            hold = handoff.after(start, self.environment)
        self.assertIsInstance(hold, custody.CustodyUncertain)
        self.assertEqual(row, hold.binding["pending"])

    def test_c11_missing_admission_lock_still_judges_journal_without_creating_lock(self):
        row = journal()
        self.store.mkdir(mode=0o700, parents=True)
        descriptor = os.open(self.store / "pending.json", os.O_CREAT | os.O_WRONLY, 0o600)
        os.write(descriptor, json.dumps(row).encode())
        os.close(descriptor)
        hold = handoff.after({"readable": True, "group": None}, self.environment)
        self.assertIsInstance(hold, custody.CustodyUncertain)
        self.assertEqual(row, hold.binding["pending"])
        self.assertFalse((self.store / "admission.lock").exists())

    def test_tampered_journal_hold_releases_the_admission_lock_it_was_judged_under(self):
        start = handoff.before(self.environment)
        self.assertEqual({"readable": True, "group": None}, start)
        self.store.mkdir(mode=0o700, parents=True)
        for name, body in (("admission.lock", b""), ("pending.json", b"{tampered")):
            descriptor = os.open(self.store / name, os.O_CREAT | os.O_WRONLY, 0o600)
            os.write(descriptor, body)
            os.close(descriptor)
        # The kept hold's traceback keeps unsettled()'s frame alive; its lock handle must still be closed.
        hold = handoff.after(start, self.environment)
        self.assertIsInstance(hold, custody.CustodyUncertain)
        self.assertIsNone(hold.binding)
        self.assertIsNotNone(hold.__traceback__)
        with open(self.store / "admission.lock", "r+b") as other:
            custody.util.lock_file(other)  # raises BlockingIOError if the observation still holds the lock
            custody.util.unlock_file(other)


@unittest.skipUnless(sys.platform == "linux", "metadata custody is held only on Linux")
class CaptureHoldCliTests(unittest.TestCase):
    """Through the real CLI and TaskRun: the scripted stage leaves a capture journal, then finishes normally."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="capture-hold-cli-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        self.workspace = root / "project"
        self.workspace.mkdir()
        git = ["git", "-C", str(self.workspace), "-c", "user.name=T", "-c", "user.email=t@example.test"]
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "base"], check=True)
        bindir = root / "bin"
        bindir.mkdir()
        shutil.copy2(TOOLS / "fake_codex.py", bindir / "codex")
        shutil.copy2(TOOLS / "goal_fixtures.py", bindir / "goal_fixtures.py")
        (bindir / "codex").chmod(0o755)
        self.state_home = root / "state"
        self.store = self.state_home / "autocode/metadata"
        self.env = {
            "PATH": f"{bindir}{os.pathsep}{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
            "AUTOCODE_HOME": str(root / "registry"),
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_STATE_HOME": str(self.state_home),
            "CODEX_HOME": str(root / "codex-home"),
            "AUTOCODE_FIXTURE_MODE": "milestones",
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def test_the_run_pauses_typed_and_resumes_only_after_the_original_family_s_cleanup_is_proven(self):
        held = {**self.env, "AUTOCODE_FIXTURE_CAPTURE_HOLD": "draft_plan"}
        run = taskrun.TaskRun.start(
            self.workspace, "Build greeting and goodbye", options=OPTIONS, env=held, timeout=300
        )
        row = json.loads((self.store / "pending.json").read_text())
        view = run.status()
        self.assertEqual("PAUSED_PROCESS_CLEANUP", view["status"])
        self.assertEqual("blocked", view["recovery"]["category"])
        self.assertIsNone(view["next_command"], "a person is never handed AutoCode's own cleanup")
        state = json.loads((run.run_dir / "state.json").read_text())
        receipts = state["resolver"]["operational_receipts"]
        (receipt,) = receipts.values()
        self.assertEqual("blocked", receipt["decision"]["action"])
        self.assertEqual(row, receipt["receipt"]["evidence"]["custody_binding"]["pending"])
        attempt = next(r for r in state["stages"] + [state.get("active_stage") or {}] if r.get("capture_custody"))
        self.assertEqual(row["group_nonce"], attempt["capture_custody"]["group_nonce"])

        run.env = self.env  # the stage that left the journal does not run again: nothing is retried
        self.assertEqual("PAUSED_PROCESS_CLEANUP", run.advance()["status"], "no cleanup proof, no progress")
        settle(self.store, row)
        advanced = run.advance()
        self.assertEqual("WAITING_FOR_USER", advanced["status"], "the plan is ready for review again")
        state = json.loads((run.run_dir / "state.json").read_text())
        actions = [row["decision"]["action"] for row in state["resolver"]["operational_receipts"].values()]
        self.assertEqual(["blocked", "recovered"], actions)


if __name__ == "__main__":
    unittest.main()
