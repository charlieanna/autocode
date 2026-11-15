"""Model-free metadata admission keeps unconfirmed ownership across invocations."""

import contextvars
import errno
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

import autocode_metadata_custody as custody

BOOT = "00000000-0000-0000-0000-000000000001"
NEXT_BOOT = "00000000-0000-0000-0000-000000000002"


@unittest.skipUnless(os.name == "posix", "Linux metadata filesystem and locks")
class MetadataCustodyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        for target, attribute, value in ((custody.sys, "platform", "linux"), (custody, "_boot_id", lambda: BOOT)):
            context = patch.object(target, attribute, value)
            context.start()
            self.addCleanup(context.stop)
        environment = patch.dict(os.environ, {"HOME": str(self.home), "XDG_STATE_HOME": ""})
        environment.start()
        self.addCleanup(environment.stop)

    def root(self):
        return self.home / ".local/state/autocode/metadata"

    def response(self, nonce, **changes):
        return {
            "schema": 1,
            "nonce": nonce,
            "clean": True,
            "started": True,
            "returncode": 0,
            "expired": False,
            **changes,
        }

    def publish(self, scope, **changes):
        scope.result.seek(0)
        scope.result.truncate()
        scope.result.write(json.dumps(self.response(scope.nonce, **changes)).encode())
        scope.result.flush()
        os.fsync(scope.result.fileno())

    def leave_pending(self):
        with self.assertRaises(custody.ProcessError):
            with custody.admission(timeout=1) as scope:
                stream, nonce = scope.arm()
                self.assertEqual((scope.result, scope.nonce), (stream, nonce))
        self.assertIsNone(custody.current())
        return scope

    def test_cache_only_admission_creates_no_pending_native_attempt(self):
        with custody.admission(timeout=5) as scope:
            self.assertIs(scope, custody.current())
            self.assertIsNone(scope.result)
        self.assertIsNone(custody.current())
        self.assertFalse((self.root() / "pending.json").exists())
        self.assertIsNone(custody.status())

    def test_metadata_preflight_runs_once_inside_original_target_deadline(self):
        import autocode_captured_process as captured

        clock = [0.0]
        deadlines = []
        remaining_timeout = custody.Admission.remaining_timeout

        def scheduling_gap(scope):
            remaining = remaining_timeout(scope)
            clock[0] += 0.25
            return remaining

        def preflight():
            clock[0] += 0.5

        def launch(words, **options):
            request = json.loads(os.pread(options["pass_fds"][0], 65536, 0))
            deadlines.append(request["deadline"])
            os.write(options["pass_fds"][1], json.dumps(self.response(request["nonce"])).encode())
            os.fsync(options["pass_fds"][1])
            return Mock(returncode=0)

        with (
            patch.object(captured.time, "monotonic", side_effect=lambda: clock[0]),
            patch.object(custody.Admission, "remaining_timeout", scheduling_gap),
            patch.object(captured, "preflight", side_effect=preflight) as checked,
            patch.object(captured.subprocess, "Popen", side_effect=launch),
        ):
            result = captured.run_metadata(["owned-metadata"], timeout=1)
        self.assertEqual(0, result.returncode)
        self.assertFalse((self.root() / "pending.json").exists())
        self.assertEqual(
            {"preflight_calls": 1, "one_target_within_original_budget": True},
            {
                "preflight_calls": checked.call_count,
                "one_target_within_original_budget": len(deadlines) == 1 and deadlines[0] <= 1,
            },
            f"target deadlines {deadlines} must include preparation and scheduling gaps",
        )

    def test_arm_commits_private_pending_before_returning_and_terminal_result_releases(self):
        with custody.admission(timeout=5) as scope:
            stream, nonce = scope.arm()
            saved = json.loads((self.root() / "pending.json").read_text())
            self.assertEqual((BOOT, os.geteuid()), (saved["boot_id"], saved["uid"]))
            self.assertEqual([{"nonce": nonce, "result_file": scope.result_path.name}], saved["attempts"])
            self.assertEqual(scope.group.nonce, saved["group_nonce"])
            self.assertEqual(b"", scope.result_path.read_bytes())
            self.assertEqual(0o700, stat.S_IMODE(self.root().stat().st_mode))
            for path in (self.root() / "pending.json", scope.result_path, self.root() / "admission.lock"):
                self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
            self.assertGreaterEqual(stream.fileno(), 3)
            self.assertEqual(custody.BUSY, custody.status())
            self.publish(scope, returncode=7)
        self.assertFalse((self.root() / "pending.json").exists())
        self.assertTrue((self.root() / ("collected-" + scope.group.nonce + ".json")).is_file())
        self.assertTrue(scope.result_path.is_file())
        self.assertTrue(stream.closed)
        self.assertIsNone(custody.status())

    def test_unconfirmed_result_blocks_new_context_despite_elapsed_time_and_child_home_change(self):
        scope = self.leave_pending()
        before = (self.root() / "pending.json").read_bytes()
        with patch.object(custody.util, "monotonic", return_value=10**12):
            with self.assertRaises(custody.ProcessError):
                contextvars.Context().run(lambda: self.enter_once({"HOME": "/different/child/home"}))
        self.assertEqual(before, (self.root() / "pending.json").read_bytes())
        self.assertEqual(custody.BLOCKED, custody.status({"XDG_STATE_HOME": "/different/child/state"}))
        self.assertEqual(b"", scope.result_path.read_bytes())

    @staticmethod
    def enter_once(env=None):
        with custody.admission(env, timeout=0.1):
            raise AssertionError("An unconfirmed earlier invocation was admitted")

    def test_strict_terminal_shapes_never_release_a_failed_record(self):
        invalid = [
            {"schema": True},
            {"schema": 2},
            {"nonce": "f" * 32},
            {"clean": False},
            {"started": None},
            {"returncode": True},
            {"expired": 1},
            {"error": "cleanup failed"},
            {"started": False, "returncode": None, "expired": True, "start_errno": errno.ENOENT},
            {"started": False, "returncode": None, "expired": False, "start_errno": True},
        ]
        for index, changes in enumerate(invalid):
            with self.subTest(changes=changes), patch.dict(os.environ, {"HOME": str(self.home / str(index))}):
                with self.assertRaises(custody.ProcessError):
                    with custody.admission(timeout=1) as scope:
                        scope.arm()
                        self.publish(scope, **changes)
                self.assertEqual(custody.BLOCKED, custody.status())

    def test_missing_malformed_and_wrong_owner_records_are_not_absence(self):
        scope = self.leave_pending()
        scope.result_path.write_bytes(b"{cut off")
        self.assertEqual(custody.BLOCKED, custody.status())
        scope.result_path.unlink()
        self.assertEqual(custody.BLOCKED, custody.status())
        path = self.root() / "pending.json"
        row = json.loads(path.read_text())
        row["uid"] += 1
        path.write_text(json.dumps(row))
        self.assertEqual(custody.BLOCKED, custody.status())
        with self.assertRaises(custody.ProcessError):
            self.enter_once()

    def test_status_observes_clean_response_without_repairing_until_next_admission(self):
        scope = self.leave_pending()
        scope.result_path.write_text(json.dumps(self.response(scope.nonce)))
        before = {path.name: path.read_bytes() for path in self.root().iterdir()}
        self.assertIsNone(custody.status())
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.root().iterdir()})
        with custody.admission(timeout=1):
            pass
        self.assertFalse((self.root() / "pending.json").exists())
        self.assertTrue(scope.result_path.exists())

    def test_only_kernel_boot_change_retires_unclean_record_and_retains_evidence(self):
        scope = self.leave_pending()
        path = self.root() / "pending.json"
        original = path.read_bytes()
        with patch.object(custody, "_boot_id", return_value=NEXT_BOOT):
            self.assertIsNone(custody.status())
            self.assertEqual(original, path.read_bytes())
            with custody.admission(timeout=1):
                pass
        archived = self.root() / ("previous-boot-" + scope.group.nonce + ".json")
        self.assertEqual(original, archived.read_bytes())
        self.assertFalse(path.exists())
        self.assertEqual(b"", scope.result_path.read_bytes())

    def test_nested_child_environments_share_one_lock_and_cannot_restart_deadline(self):
        clock = [0.0]
        with patch.object(custody.util, "monotonic", side_effect=lambda: clock[0]):
            with custody.admission({"HOME": "/child/one"}, timeout=5) as outer:
                clock[0] = 2
                with custody.admission({"HOME": "/child/two", "XDG_STATE_HOME": "/child/state"}, timeout=50) as inner:
                    self.assertIs(outer, inner)
                    self.assertEqual(3, inner.remaining_timeout())
                with custody.admission(timeout=0.5):
                    self.assertEqual(0.5, outer.remaining_timeout())
                clock[0] = 3
                self.assertEqual(0, outer.remaining_timeout())

    def test_busy_wait_uses_original_budget_and_none_never_waits_forever(self):
        clock = [0.0]

        def sleep(seconds):
            clock[0] += seconds

        def busy(timeout):
            with custody.admission(timeout=timeout):
                self.fail("Foreign lock was ignored")

        with (
            patch.object(custody.util, "monotonic", side_effect=lambda: clock[0]),
            patch.object(custody.util, "sleep", side_effect=sleep),
        ):
            with custody.admission(timeout=10):
                with self.assertRaisesRegex(custody.ProcessError, custody.BUSY):
                    contextvars.Context().run(busy, None)
                self.assertEqual(0, clock[0])
                with self.assertRaisesRegex(custody.ProcessError, custody.BUSY):
                    contextvars.Context().run(busy, 0.1)
                self.assertAlmostEqual(0.1, clock[0])

    def test_waiting_for_a_healthy_call_deducts_elapsed_lock_time(self):
        clock = [0.0]
        custody._private_root(self.root(), create=True)
        handle = custody._open(self.root() / "admission.lock", os.O_RDWR | os.O_CREAT)
        custody.util.lock_file(handle)

        def sleep(seconds):
            clock[0] += seconds
            if clock[0] >= 0.04:
                custody.util.unlock_file(handle)

        try:
            with (
                patch.object(custody.util, "monotonic", side_effect=lambda: clock[0]),
                patch.object(custody.util, "sleep", side_effect=sleep),
            ):
                with custody.admission(timeout=0.2) as scope:
                    self.assertAlmostEqual(0.16, scope.remaining_timeout())
        finally:
            handle.close()

    def test_verified_exec_error_is_preserved_and_never_writes_parent_clean_stamp(self):
        original = FileNotFoundError(errno.ENOENT, "test missing executable")
        with self.assertRaises(FileNotFoundError) as caught:
            with custody.admission(timeout=1) as scope:
                scope.arm()
                self.publish(scope, started=False, returncode=None, start_errno=errno.ENOENT)
                raise original
        self.assertIs(original, caught.exception)
        self.assertIsNone(custody.status())

    def test_verified_no_spawn_cancellation_preserves_original_interrupt_without_a_hold(self):
        original = KeyboardInterrupt("original cancellation")
        with self.assertRaises(KeyboardInterrupt) as caught:
            with custody.admission(timeout=1) as scope:
                scope.arm()
                self.publish(scope, started=False, returncode=None, expired=False)
                raise original
        self.assertIs(original, caught.exception)
        self.assertIsNone(custody.status())
        self.assertFalse((self.root() / "pending.json").exists())

    def test_scope_exit_requires_its_own_pending_nonce_even_when_a_result_is_clean(self):
        for name in ("missing", "swapped"):
            with self.subTest(name=name), patch.dict(os.environ, {"HOME": str(self.home / name)}):
                with self.assertRaises(custody.ProcessError):
                    with custody.admission(timeout=1) as scope:
                        scope.arm()
                        self.publish(scope)
                        pending = scope.root / "pending.json"
                        if name == "missing":
                            pending.unlink()
                        else:
                            foreign = "f" * 32
                            row = json.loads(pending.read_text())
                            row["attempts"] = [{"nonce": foreign, "result_file": "result-" + foreign + ".json"}]
                            path = scope.root / row["attempts"][0]["result_file"]
                            path.write_text(json.dumps(self.response(foreign)))
                            path.chmod(0o600)
                            pending.write_text(json.dumps(row))
                            replaced = pending.read_bytes()
                self.assertFalse((scope.root / ("collected-" + scope.group.nonce + ".json")).exists())
                if name == "swapped":
                    self.assertEqual(replaced, pending.read_bytes())

    def test_store_uses_ambient_xdg_state_not_project_or_command_environment(self):
        state = self.home / "state"
        with patch.dict(os.environ, {"XDG_STATE_HOME": str(state), "AUTOCODE_HOME": "/project/private-run"}):
            with custody.admission({"HOME": "/command/home", "XDG_STATE_HOME": "/command/state"}, timeout=1) as scope:
                self.assertEqual((state / "autocode").resolve() / "metadata", scope.root)

    def test_status_is_read_only_and_sanitizes_malformed_store(self):
        self.assertIsNone(custody.status())
        self.assertEqual([], list(self.home.iterdir()))
        custody._private_root(self.root(), create=True)
        (self.root() / "pending.json").write_text("SECRET corrupt path /private/internal/token")
        reason = custody.status()
        self.assertEqual(custody.BLOCKED, reason)
        self.assertNotIn("SECRET", reason)
        self.assertFalse((self.root() / "admission.lock").exists())

    def test_second_arm_refuses_to_reuse_an_attempt_identity(self):
        with self.assertRaises(custody.ProcessError):
            with custody.admission(timeout=1) as scope:
                scope.arm()
                scope.arm()

    def test_explicit_group_workers_have_distinct_durable_members_and_can_overlap(self):
        ready = threading.Barrier(4, timeout=3)
        scopes = []
        lock = threading.Lock()

        def member(index):
            self.assertIsNone(custody.current(), "only the group may be copied to a worker")
            with custody.admission({"HOME": "/child/" + str(index)}, timeout=3) as scope:
                scope.arm()
                with lock:
                    scopes.append(scope)
                ready.wait()
                self.publish(scope)

        with custody.group(timeout=5) as owner:
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = [pool.submit(contextvars.copy_context().run, member, index) for index in range(3)]
                ready.wait()
                pending = json.loads((self.root() / "pending.json").read_text())
                self.assertEqual(owner.nonce, pending["group_nonce"])
                self.assertEqual(3, len(pending["attempts"]))
                self.assertEqual(3, len({row["nonce"] for row in pending["attempts"]}))
                for future in futures:
                    future.result(timeout=5)
            self.assertEqual(3, len({scope.result_path for scope in scopes}))
            self.assertTrue((self.root() / "pending.json").exists(), "members cannot retire sibling custody")
        self.assertFalse((self.root() / "pending.json").exists())
        self.assertTrue((self.root() / ("collected-" + owner.nonce + ".json")).exists())

    def test_failed_group_blocks_new_arms_but_preserves_already_armed_members(self):
        ready = threading.Barrier(2, timeout=3)
        failed = threading.Event()
        scopes = []

        def member(broken):
            try:
                with custody.admission(timeout=3) as scope:
                    scope.arm()
                    scopes.append(scope)
                    ready.wait()
                    if not broken:
                        self.assertTrue(failed.wait(3))
                        self.publish(scope)
            except custody.ProcessError:
                if broken:
                    failed.set()
                return
            raise AssertionError("An uncertain sibling must fail the group result")

        with self.assertRaises(custody.ProcessError):
            with custody.group(timeout=5) as owner:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(contextvars.copy_context().run, member, broken) for broken in (True, False)]
                    for future in futures:
                        future.result(timeout=5)
                self.assertTrue(owner.failed)
                with self.assertRaises(custody.ProcessError):
                    with custody.admission(timeout=1):
                        self.fail("A late member was admitted after custody became uncertain")
                self.assertEqual(2, len(owner.members))
        self.assertEqual(2, len(json.loads((self.root() / "pending.json").read_text())["attempts"]))
        self.assertEqual(custody.BLOCKED, custody.status())
        self.assertEqual(1, sum(bool(scope.result_path.read_bytes()) for scope in scopes))
        with self.assertRaises(custody.ProcessError):
            contextvars.Context().run(self.enter_once)
        # A surviving helper may still finish after the parent failed. Recovery
        # needs every real member's response, never a parent success stamp.
        for scope in scopes:
            scope.result_path.write_text(json.dumps(self.response(scope.nonce)))
        with custody.admission(timeout=1):
            pass
        self.assertFalse((self.root() / "pending.json").exists())

    def test_unused_slots_need_no_record_and_copied_authority_expires_with_group(self):
        with custody.group(timeout=1):
            with custody.admission(timeout=1):
                pass
            late = contextvars.copy_context()
        self.assertFalse((self.root() / "pending.json").exists())
        with self.assertRaises(custody.ProcessError):
            late.run(self.enter_once)
        self.assertIsNone(custody.status())

    def test_group_and_member_deadlines_only_tighten(self):
        clock = [0.0]
        with patch.object(custody.util, "monotonic", side_effect=lambda: clock[0]):
            with custody.group(timeout=10) as owner:
                group_context = contextvars.copy_context()
                clock[0] = 2
                with custody.admission(timeout=3) as scope:
                    self.assertEqual(3, scope.remaining_timeout())
                    self.assertEqual(8, owner.remaining_timeout())
                    clock[0] = 3

                    def tighten(timeout):
                        with custody.group(timeout=timeout):
                            pass

                    group_context.copy().run(tighten, 1)
                    self.assertEqual(1, scope.remaining_timeout())
                    group_context.copy().run(tighten, 100)
                    self.assertEqual(1, owner.remaining_timeout())

    def test_group_rejects_a_dropped_member_even_if_the_remaining_result_is_clean(self):
        with self.assertRaises(custody.ProcessError):
            with custody.group(timeout=1) as owner:
                with custody.admission(timeout=1) as first:
                    first.arm()
                    self.publish(first)
                with custody.admission(timeout=1) as second:
                    second.arm()
                    pending = self.root() / "pending.json"
                    row = json.loads(pending.read_text())
                    self.assertEqual(2, len(row["attempts"]))
                    row["attempts"] = row["attempts"][:1]
                    pending.write_text(json.dumps(row))
        self.assertFalse((self.root() / ("collected-" + owner.nonce + ".json")).exists())
        self.assertTrue(pending.exists())

    def test_group_refuses_member_beyond_record_bound_before_allocating_or_launching(self):
        owner = custody.Group(self.root(), None, BOOT)
        owner.members = [{}] * 256
        scope = custody.Admission(owner, None)
        with patch.object(custody, "_open", side_effect=AssertionError("member allocated")):
            with self.assertRaises(custody.ProcessError):
                scope.arm()
        self.assertIsNone(scope.result)
        self.assertEqual(256, len(owner.members))

    def test_old_single_member_record_uses_the_same_strict_completion_rule(self):
        scope = self.leave_pending()
        row = {
            "schema": 1,
            "nonce": scope.nonce,
            "boot_id": BOOT,
            "uid": os.geteuid(),
            "result_file": scope.result_path.name,
        }
        pending = self.root() / "pending.json"
        pending.write_text(json.dumps(row))
        self.assertEqual(custody.BLOCKED, custody.status())
        scope.result_path.write_text(json.dumps(self.response(scope.nonce)))
        self.assertIsNone(custody.status())
        with custody.admission(timeout=1):
            pass
        self.assertEqual(row, json.loads((self.root() / ("collected-" + scope.nonce + ".json")).read_text()))

    def test_portable_host_adds_no_store_or_active_custody(self):
        with patch.object(custody.sys, "platform", "darwin"):
            with custody.admission(timeout=7) as scope:
                self.assertEqual((None, None), scope.arm())
                self.assertEqual(7, scope.remaining_timeout())
                self.assertIsNone(custody.current())
            self.assertIsNone(custody.status())
        self.assertEqual([], list(self.home.iterdir()))

    @unittest.skipUnless(os.name == "posix", "private POSIX descriptors")
    def test_guard_descriptors_do_not_reopen_closed_standard_streams(self):
        program = f"""
import errno,json,os,sys
from unittest.mock import patch
sys.path.insert(0,{str(Path(__file__).resolve().parents[1] / "tools")!r})
import autocode_metadata_custody as custody
for fd in (0,1,2):os.close(fd)
with patch.object(custody.sys,'platform','linux'), patch.object(custody,'_boot_id',return_value={BOOT!r}):
    with custody.admission(timeout=1) as scope:
        stream,nonce=scope.arm()
        assert stream.fileno()>2
        for fd in (0,1,2):
            try:os.fstat(fd)
            except OSError as error:assert error.errno==errno.EBADF
            else:raise AssertionError('guard reopened stdio')
        stream.write(json.dumps(dict(schema=1,nonce=nonce,clean=True,started=True,returncode=0,expired=False)).encode())
        stream.flush();os.fsync(stream.fileno())
"""
        result = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr)
