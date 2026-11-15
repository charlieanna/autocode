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

    def bound_hold(self):
        with self.assertRaises(custody.ProcessError) as caught:
            with custody.admission(timeout=1) as scope:
                scope.arm()
        self.assertEqual("CustodyUncertain", type(caught.exception).__name__)
        binding = getattr(caught.exception, "binding", None)
        self.assertIsInstance(binding, dict)
        return scope, binding

    def test_uncertain_error_freezes_exact_metadata_identity_without_command_authority(self):
        scope, binding = self.bound_hold()
        self.assertEqual(
            {"schema": 1, "scope": "metadata_check", "root": str(scope.root), "pending": custody._pending(scope.root)},
            json.loads(json.dumps(binding)),
        )
        scope.group.members.append({"nonce": "0" * 32, "result_file": "result-" + "0" * 32 + ".json"})
        self.assertEqual(1, len(binding["pending"]["attempts"]), "later membership cannot rewrite saved evidence")
        self.assertFalse(custody.recover(binding))

    def test_advancement_revalidates_primary_after_exact_journal_archive_without_writes(self):
        scope, binding = self.bound_hold()
        scope.result_path.write_text(json.dumps(self.response(scope.nonce)))
        before = {path.name: path.read_bytes() for path in scope.root.iterdir()}
        self.assertTrue(custody.recover(binding))
        self.assertEqual(before, {path.name: path.read_bytes() for path in scope.root.iterdir()})
        with custody.admission(timeout=1):
            pass
        archive = scope.root / ("collected-" + scope.group.nonce + ".json")
        self.assertEqual(binding["pending"], json.loads(archive.read_text()))
        self.assertTrue(scope.result_path.is_file(), "archiving retains the proof it depends on")
        with patch.dict(os.environ, {"HOME": str(self.home / "another-user-store")}):
            self.assertIsNone(custody.status())
            self.assertTrue(custody.recover(binding), "recovery reads the original store, not global status")
        scope.result_path.write_text(json.dumps(self.response(scope.nonce, clean=False)))
        self.assertFalse(custody.recover(binding), "an archive alone is no cleanup stamp")

    def test_advancement_accepts_retained_ancestor_proof_without_fabricating_command_result(self):
        scope, binding = self.bound_hold()
        ancestor = "a" * 32
        path = scope.root / f"family-result-{scope.nonce}-{ancestor}.json"
        packet = {
            "schema": 1,
            "family_clean": True,
            "attempt_nonce": scope.nonce,
            "ancestor_nonce": ancestor,
            "boot_id": BOOT,
        }
        path.write_text(json.dumps(packet))
        path.chmod(0o600)
        self.assertTrue(custody.recover(binding))
        with custody.admission(timeout=1):
            pass
        self.assertTrue(custody.recover(binding))
        self.assertEqual(b"", scope.result_path.read_bytes(), "family settlement grants no old command result")
        path.write_text(json.dumps({**packet, "family_clean": 1}))
        self.assertFalse(custody.recover(binding))

    def test_erasure_and_unrelated_success_never_release_original_hold(self):
        scope, binding = self.bound_hold()
        (scope.root / "pending.json").unlink()
        with custody.admission(timeout=1) as other:
            other.arm()
            self.publish(other)
        self.assertIsNone(custody.status())
        self.assertFalse(custody.recover(binding))
        with patch.object(custody, "_boot_id", return_value=NEXT_BOOT):
            self.assertFalse(custody.recover(binding), "even a different boot needs the retained original journal")

    def test_advancement_refuses_wrong_family_members_boot_root_and_malformed_bindings(self):
        scope, binding = self.bound_hold()
        scope.result_path.write_text(json.dumps(self.response(scope.nonce)))
        self.assertTrue(custody.recover(binding))
        variants = [None, {}, {**binding, "schema": True}, {**binding, "scope": "model_listing"}]
        for key, value in (
            ("group_nonce", "b" * 32),
            ("boot_id", NEXT_BOOT),
            ("uid", os.geteuid() + 1),
            ("attempts", [{"nonce": "c" * 32, "result_file": "result-" + "c" * 32 + ".json"}]),
        ):
            variants.append({**binding, "pending": {**binding["pending"], key: value}})
        variants.extend(({**binding, "root": str(self.home / "elsewhere")}, {**binding, "pending": {}}))
        for bad in variants:
            with self.subTest(binding=bad):
                self.assertFalse(custody.recover(bad))
        pending = scope.root / "pending.json"
        original = pending.read_bytes()
        pending.write_text(json.dumps({**binding["pending"], "attempts": []}))
        self.assertFalse(custody.recover(binding))
        pending.write_bytes(original)
        archive = scope.root / ("collected-" + scope.group.nonce + ".json")
        archive.write_bytes(original)
        archive.chmod(0o600)
        self.assertFalse(custody.recover(binding), "duplicate journals are ambiguous")

    def test_only_actual_kernel_boot_change_releases_preserved_original_identity(self):
        scope, binding = self.bound_hold()
        self.assertFalse(custody.recover(binding))
        with patch.object(custody, "_boot_id", return_value=NEXT_BOOT):
            self.assertTrue(custody.recover(binding))
            with custody.admission(timeout=1):
                pass
            self.assertTrue(custody.recover(binding))
        self.assertFalse(custody.recover(binding), "a recorded previous-boot label grants no boot authority")

    def test_advancement_requires_every_original_group_member_without_partial_membership(self):
        with self.assertRaises(custody.ProcessError) as caught:
            with custody.group(timeout=2):
                with custody.admission(timeout=1) as first:
                    first.arm()
                    self.publish(first)
                with custody.admission(timeout=1) as second:
                    second.arm()
        self.assertEqual("CustodyUncertain", type(caught.exception).__name__)
        binding = caught.exception.binding
        self.assertEqual(2, len(binding["pending"]["attempts"]))
        self.assertFalse(custody.recover(binding))
        second.result_path.write_text(json.dumps(self.response(second.nonce)))
        self.assertTrue(custody.recover(binding))
        partial = {**binding, "pending": {**binding["pending"], "attempts": binding["pending"]["attempts"][:1]}}
        self.assertFalse(custody.recover(partial))
        (second.root / "pending.json").write_text(json.dumps(partial["pending"]))
        self.assertFalse(custody.recover(binding))

    def test_missing_or_swapped_journal_retains_original_error_identity_without_release(self):
        for swapped in (False, True):
            with self.subTest(swapped=swapped), patch.dict(os.environ, {"HOME": str(self.home / str(swapped))}):
                with self.assertRaises(custody.ProcessError) as caught:
                    with custody.admission(timeout=1) as scope:
                        scope.arm()
                        pending = scope.root / "pending.json"
                        original = json.loads(pending.read_text())
                        if swapped:
                            pending.write_text(json.dumps({**original, "group_nonce": "d" * 32}))
                        else:
                            pending.unlink()
                        self.publish(scope)
                self.assertEqual("CustodyUncertain", type(caught.exception).__name__)
                self.assertEqual(original, caught.exception.binding["pending"])
                self.assertFalse(custody.recover(caught.exception.binding))

    def test_busy_healthy_lock_has_no_uncertainty_binding_and_corrupt_journal_stays_unbindable(self):
        def busy():
            with custody.admission(timeout=0):
                self.fail("A separate context acquired a healthy held lock")

        with custody.group(timeout=1):
            with self.assertRaises(custody.ProcessError) as caught:
                contextvars.Context().run(busy)
            self.assertNotEqual("CustodyUncertain", type(caught.exception).__name__)
            self.assertIsNone(getattr(caught.exception, "binding", None))
        scope = self.leave_pending()
        (scope.root / "pending.json").write_text("{")
        with self.assertRaises(custody.ProcessError) as caught:
            self.enter_once()
        self.assertEqual("CustodyUncertain", type(caught.exception).__name__)
        self.assertIsNone(caught.exception.binding)
        self.assertFalse(custody.recover(caught.exception.binding))

    def test_known_preparation_failure_without_committed_targets_is_not_permanent_custody(self):
        with (
            patch.object(custody.family, "register_backup", side_effect=OSError("PRIVATE preparation failed")),
            self.assertRaises(custody.ProcessError) as caught,
        ):
            with custody.admission(timeout=1) as scope:
                scope.prepare()
        self.assertEqual("ProcessError", type(caught.exception).__name__)
        self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertFalse((scope.root / "pending.json").exists())
        self.assertIsNone(custody.status())
        with custody.admission(timeout=1):
            pass

    def test_preparation_failure_survives_settlement_of_already_dispatched_sibling(self):
        prepared, commit = threading.Event(), threading.Event()
        original = custody.ProcessError("Metadata preparation was refused")
        prepare = custody.Group.prepare
        successful = []

        def controlled_prepare(owner, scope):
            if scope not in successful:
                raise original
            return prepare(owner, scope)

        def sibling():
            with custody.admission(timeout=2) as scope:
                successful.append(scope)
                scope.prepare()
                prepared.set()
                self.assertTrue(commit.wait(2))
                scope.arm()
                self.publish(scope)

        def refused():
            self.assertTrue(prepared.wait(2))
            with custody.admission(timeout=2) as scope:
                scope.prepare()

        with self.assertRaises(custody.ProcessError) as caught:
            with custody.group(timeout=3), patch.object(custody.Group, "prepare", controlled_prepare):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    good = pool.submit(contextvars.copy_context().run, sibling)
                    bad = pool.submit(contextvars.copy_context().run, refused)
                    error = bad.exception(timeout=2)
                    commit.set()
                    good.result(timeout=2)
                raise error
        self.assertIs(original, caught.exception, "settling siblings must preserve the preparation failure")
        self.assertEqual("ProcessError", type(caught.exception).__name__)
        self.assertFalse((successful[0].root / "pending.json").exists())
        self.assertIsNone(custody.status())

    def test_only_group_exit_binds_uncertainty_after_every_dispatched_member_settles(self):
        first_armed, second_armed, finish_second = threading.Event(), threading.Event(), threading.Event()
        scopes = []

        def uncertain():
            with custody.admission(timeout=2) as scope:
                scopes.append(scope)
                scope.arm()
                first_armed.set()
                self.assertTrue(second_armed.wait(2))

        def collected():
            self.assertTrue(first_armed.wait(2))
            with custody.admission(timeout=2) as scope:
                scopes.append(scope)
                scope.arm()
                second_armed.set()
                self.assertTrue(finish_second.wait(2))
                self.publish(scope)

        with self.assertRaises(custody.ProcessError) as caught:
            with custody.group(timeout=3):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    first = pool.submit(contextvars.copy_context().run, uncertain)
                    second = pool.submit(contextvars.copy_context().run, collected)
                    error = first.exception(timeout=2)
                    self.assertEqual("ProcessError", type(error).__name__, "member refusal is not the settled group")
                    finish_second.set()
                    self.assertIsInstance(second.exception(timeout=2), custody.ProcessError)
                raise error
        self.assertEqual("CustodyUncertain", type(caught.exception).__name__)
        binding = caught.exception.binding
        self.assertEqual(custody._pending(scopes[0].root), binding["pending"])
        self.assertEqual(2, len(binding["pending"]["attempts"]))
        self.assertFalse(custody.recover(binding))
        scopes[0].result_path.write_text(json.dumps(self.response(scopes[0].nonce)))
        self.assertTrue(custody.recover(binding))

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
            os.write(request["ready"], json.dumps({"schema": 1, "nonce": request["nonce"], "ready": True}).encode())
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

    def test_ancestor_enrollment_precedes_pending_and_uses_the_effective_deadline(self):
        clock = [0.0]
        registration = Mock()
        observed = []

        def enroll(root, nonce, deadline, *, boot_id):
            scope = custody.current()
            self.assertEqual((self.root().resolve(), scope.nonce, 3, BOOT), (root, nonce, deadline, boot_id))
            self.assertEqual([], scope.group.members)
            self.assertFalse((root / "pending.json").exists())
            self.assertEqual(b"", scope.result_path.read_bytes())
            observed.append(nonce)
            return registration

        with (
            patch.object(custody.util, "monotonic", side_effect=lambda: clock[0]),
            patch.object(custody.family, "register_backup", side_effect=enroll),
        ):
            with custody.group(timeout=10):
                clock[0] = 1
                with custody.admission({"HOME": "/different/command/home"}, timeout=2) as scope:
                    scope.arm()
                    pending = json.loads((self.root() / "pending.json").read_text())
                    self.publish(scope)
                    self.assertEqual(observed, [member["nonce"] for member in pending["attempts"]])
        registration.release.assert_called_once_with()

    def test_preparation_enrolls_once_without_pending_until_helper_ready_commit(self):
        registration = Mock()
        with patch.object(custody.family, "register_backup", return_value=registration) as enroll:
            with custody.admission(timeout=1) as scope:
                prepared = scope.prepare()
                self.assertEqual(prepared, scope.prepare())
                self.assertEqual([], scope.group.members)
                self.assertFalse((scope.root / "pending.json").exists())
                enroll.assert_called_once()
                self.assertEqual(prepared, scope.arm())
                self.assertEqual(1, len(scope.group.members))
                self.assertTrue((scope.root / "pending.json").exists())
                self.publish(scope)
        registration.release.assert_called_once_with()

    def test_failure_before_helper_ready_preserves_error_and_rolls_back_preparation(self):
        original = FileNotFoundError(errno.ENOENT, "private helper did not start")
        registration = Mock()
        with patch.object(custody.family, "register_backup", return_value=registration):
            with self.assertRaises(FileNotFoundError) as caught:
                with custody.admission(timeout=1) as scope:
                    scope.prepare()
                    raise original
        self.assertIs(original, caught.exception)
        registration.release.assert_called_once_with()
        self.assertTrue(scope.result.closed)
        self.assertEqual(set(), scope.group.prepared)
        self.assertFalse((scope.root / "pending.json").exists())
        self.assertIsNone(custody.status())

    def test_explicit_preparation_abort_never_discards_committed_custody(self):
        registration = Mock()
        with patch.object(custody.family, "register_backup", return_value=registration):
            with custody.admission(timeout=1) as scope:
                scope.prepare()
                scope.arm()
                before = (scope.root / "pending.json").read_bytes()
                with self.assertRaises(custody.ProcessError):
                    scope.abort_prepared()
                self.assertEqual(before, (scope.root / "pending.json").read_bytes())
                registration.release.assert_not_called()
                self.publish(scope)
        registration.release.assert_called_once_with()

    def test_failed_ancestor_enrollment_never_publishes_pending_custody(self):
        for index, error in enumerate(
            (
                custody.family.FamilyError("PRIVATE ancestor unavailable"),
                OSError("PRIVATE denied"),
                ValueError("bad ACK"),
            )
        ):
            with (
                self.subTest(error=type(error).__name__),
                patch.dict(os.environ, {"HOME": str(self.home / str(index))}),
                patch.object(custody.family, "register_backup", side_effect=error),
                self.assertRaisesRegex(custody.ProcessError, custody.BLOCKED),
            ):
                with custody.admission(timeout=1) as scope:
                    scope.arm()
            self.assertEqual([], scope.group.members)
            self.assertFalse((scope.root / "pending.json").exists())
            self.assertTrue(scope.result.closed)
            with patch.dict(os.environ, {"HOME": str(self.home / str(index))}):
                self.assertIsNone(custody.status())
                with custody.admission(timeout=1):
                    pass

    def test_primary_receipt_releases_backup_even_when_unregistration_is_unavailable(self):
        registration = Mock()
        registration.release.side_effect = custody.family.FamilyError("PRIVATE ancestor stopped")
        with patch.object(custody.family, "register_backup", return_value=registration):
            with custody.admission(timeout=1) as scope:
                scope.arm()
                self.publish(scope, returncode=7)
        registration.release.assert_called_once_with()
        self.assertFalse((self.root() / "pending.json").exists())
        self.assertIsNone(custody.status())

    def test_authenticated_empty_setup_failure_preserves_machinery_error_without_a_hold(self):
        original = custody.ProcessError("Capture helper setup failed")
        registration = Mock()
        with patch.object(custody.family, "register_backup", return_value=registration):
            with self.assertRaises(custody.ProcessError) as caught:
                with custody.admission(timeout=1) as scope:
                    scope.arm()
                    self.publish(scope, started=False, returncode=None, expired=False, setup_failed=True)
                    raise original
        self.assertIs(original, caught.exception)
        registration.release.assert_called_once_with()
        self.assertFalse((scope.root / "pending.json").exists())
        self.assertIsNone(custody.status())

    def test_cancelled_ancestor_enrollment_preserves_original_interrupt_without_pending(self):
        original = KeyboardInterrupt("original enrollment cancellation")
        with (
            patch.object(custody.family, "register_backup", side_effect=original),
            patch.object(custody, "_clean", side_effect=AssertionError("Unarmed attempt has no child result")),
            self.assertRaises(KeyboardInterrupt) as caught,
        ):
            with custody.admission(timeout=1) as scope:
                scope.arm()
        self.assertIs(original, caught.exception)
        self.assertEqual([], scope.group.members)
        self.assertFalse((self.root() / "pending.json").exists())
        self.assertTrue(scope.result.closed)
        self.assertIsNone(custody.status())

    def test_active_scope_never_substitutes_ancestor_custody_for_its_primary_result(self):
        registration = Mock()
        with (
            patch.object(custody.family, "register_backup", return_value=registration),
            patch.object(custody.family, "backup_clean", return_value=True) as ancestor,
        ):
            with self.assertRaises(custody.ProcessError):
                with custody.admission(timeout=1) as scope:
                    scope.arm()
            ancestor.assert_not_called()
            registration.release.assert_not_called()
            self.assertEqual(b"", scope.result_path.read_bytes())
            self.assertTrue((self.root() / "pending.json").exists())
            self.assertIsNone(custody.status(), "a fresh observation may recover ownership only")
            ancestor.assert_called_once_with(self.root().resolve(), scope.nonce, BOOT)

    def test_fresh_ancestor_settlement_preserves_missing_primary_result_and_read_only_status(self):
        scope = self.leave_pending()
        original = (self.root() / "pending.json").read_bytes()
        scope.result_path.write_bytes(b"{interrupted primary packet")
        before = {path.name: path.read_bytes() for path in self.root().iterdir()}
        with patch.object(custody.family, "backup_clean", return_value=True) as ancestor:
            self.assertIsNone(custody.status())
            self.assertEqual(before, {path.name: path.read_bytes() for path in self.root().iterdir()})
            ancestor.assert_called_once_with(self.root().resolve(), scope.nonce, BOOT)
            with custody.admission(timeout=1):
                pass
        self.assertFalse((self.root() / "pending.json").exists())
        self.assertEqual(original, (self.root() / ("collected-" + scope.group.nonce + ".json")).read_bytes())
        self.assertEqual(b"{interrupted primary packet", scope.result_path.read_bytes())

    def test_valid_outer_ancestor_releases_custody_despite_incomplete_inner_backup(self):
        scope = self.leave_pending()
        inner_nonce, outer_nonce = "a" * 32, "b" * 32
        inner = scope.root / f"family-result-{scope.nonce}-{inner_nonce}.json"
        outer = scope.root / f"family-result-{scope.nonce}-{outer_nonce}.json"
        outer.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "family_clean": True,
                    "attempt_nonce": scope.nonce,
                    "ancestor_nonce": outer_nonce,
                    "boot_id": BOOT,
                }
            )
        )
        outer.chmod(0o600)
        for incomplete in (b"", b"{interrupted ancestor packet", b'{"schema":1,"family_clean":false}'):
            inner.write_bytes(incomplete)
            inner.chmod(0o600)
            before = {path.name: path.read_bytes() for path in scope.root.iterdir()}
            with self.subTest(incomplete=incomplete), patch.object(Path, "glob", return_value=iter((inner, outer))):
                self.assertIsNone(custody.status())
            self.assertEqual(before, {path.name: path.read_bytes() for path in scope.root.iterdir()})
        with custody.admission(timeout=1):
            pass
        self.assertFalse((scope.root / "pending.json").exists())
        self.assertEqual(b"", scope.result_path.read_bytes(), "family evidence cannot fabricate the command result")

    def test_ancestor_settlement_does_not_release_an_uncertain_sibling(self):
        with self.assertRaises(custody.ProcessError):
            with custody.group(timeout=2):
                with custody.admission(timeout=1) as first:
                    first.arm()
                    self.publish(first)
                with custody.admission(timeout=1) as second:
                    second.arm()
        before = (self.root() / "pending.json").read_bytes()
        with patch.object(
            custody.family, "backup_clean", side_effect=lambda _root, nonce, _boot: nonce == first.nonce
        ) as ancestor:
            self.assertEqual(custody.BLOCKED, custody.status())
            ancestor.assert_called_once_with(self.root().resolve(), second.nonce, BOOT)
            with self.assertRaises(custody.ProcessError):
                self.enter_once()
        self.assertEqual(before, (self.root() / "pending.json").read_bytes())
        with patch.object(
            custody.family, "backup_clean", side_effect=lambda _root, nonce, _boot: nonce == second.nonce
        ):
            with custody.admission(timeout=1):
                pass
        self.assertFalse((self.root() / "pending.json").exists())

    def test_ancestor_receipt_inspection_failure_stays_blocked_and_sanitized(self):
        scope = self.leave_pending()
        before = (self.root() / "pending.json").read_bytes()
        with patch.object(custody.family, "backup_clean", side_effect=custody.family.FamilyError("PRIVATE failure")):
            self.assertEqual(custody.BLOCKED, custody.status())
            with self.assertRaisesRegex(custody.ProcessError, custody.BLOCKED):
                self.enter_once()
        self.assertEqual(before, (self.root() / "pending.json").read_bytes())
        self.assertEqual(b"", scope.result_path.read_bytes())

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
            {"started": False, "returncode": None, "setup_failed": False},
            {"started": False, "returncode": None, "setup_failed": None},
            {"started": False, "returncode": None, "setup_failed": 1},
            {"setup_failed": True},
            {"started": False, "returncode": None, "expired": True, "setup_failed": True},
            {"started": False, "returncode": None, "start_errno": errno.ENOENT, "setup_failed": True},
            {"started": False, "returncode": None, "error": "setup failed", "setup_failed": True},
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

    def test_uncommitted_preparations_reserve_the_same_member_resource_bound(self):
        owner = custody.Group(self.root(), None, BOOT)
        owner.prepared = {format(index, "032x") for index in range(256)}
        scope = custody.Admission(owner, None)
        with patch.object(custody, "_open", side_effect=AssertionError("member allocated")):
            with self.assertRaises(custody.ProcessError):
                scope.prepare()
        self.assertIsNone(scope.result)
        self.assertEqual(256, len(owner.prepared))

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
                self.assertEqual((None, None), scope.prepare())
                self.assertEqual((None, None), scope.arm())
                scope.abort_prepared()
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
