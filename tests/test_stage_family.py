"""A stage's family proof is scoped to its recorded launch and boot."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autocode_stage_family as stage_family
import autocode_supervision as supervision


@unittest.skipIf(os.name == "nt", "kernel family certificates use POSIX process and file semantics")
class StageFamilyTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "the private store uses POSIX account identities")
    def test_evidence_store_is_private_and_rejects_public_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("pwd.getpwuid", return_value=SimpleNamespace(pw_dir=directory)):
                self.assertFalse(stage_family._root(create=False).exists())
                root = stage_family._root(create=True)
                self.assertEqual(0, root.stat().st_mode & 0o077)
                root.chmod(0o755)
                with self.assertRaisesRegex(ValueError, "not private"):
                    stage_family._root(create=False)

    def test_boot_identity_must_be_canonical(self):
        boot = "36db2920-0a4c-4a27-85e4-278b5cc25007"
        with patch.object(Path, "read_text", return_value=boot + "\n"):
            self.assertEqual(boot, stage_family._boot())
        with patch.object(Path, "read_text", return_value=boot.upper()), self.assertRaises(ValueError):
            stage_family._boot()

    def test_observer_distinguishes_missing_receipts_from_invalid_ones(self):
        for diagnostic, missing in ((("open", "not_found"), True), (("parse", "json_invalid"), False)):
            with (
                self.subTest(diagnostic=diagnostic),
                patch.object(supervision, "_read_receipt", return_value=(None, diagnostic)),
                patch.object(stage_family, "clean", return_value=True),
            ):
                observed = supervision.observe({})
                self.assertTrue(observed["family_cleanup"])
                self.assertEqual(missing, observed.get("family_receipt_missing") is True)

    def test_failed_enrollment_refuses_the_launch_before_spawning_a_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(supervision.boundary, "require_supported"),
                patch.object(supervision.socket, "SOCK_SEQPACKET", supervision.socket.SOCK_STREAM),
                patch.object(supervision, "_identity", return_value={"pid": 1, "birth_identity": 1}),
                patch.object(stage_family, "enroll", side_effect=stage_family.family.FamilyError("denied")),
                patch.object(supervision.subprocess, "Popen") as spawn,
                self.assertRaisesRegex(supervision.LaunchRefused, "no provider launched"),
            ):
                with supervision.launch(
                    ["model"],
                    receipt_path=Path(directory) / "receipt.json",
                    checkpoint=lambda _: None,
                    start_new_session=True,
                ):
                    self.fail("A refused enrollment must not release a provider")
            spawn.assert_not_called()

    def test_no_capture_or_non_linux_host_enrolls_nothing(self):
        for platform, environment in (("darwin", {stage_family.family.ENVIRONMENT_KEY: "present"}), ("linux", {})):
            with (
                self.subTest(platform=platform),
                patch.object(stage_family.sys, "platform", platform),
                patch.dict(stage_family.os.environ, environment, clear=True),
                patch.object(stage_family.family, "register_backup") as register,
            ):
                self.assertEqual((None, None), stage_family.enroll(Path("/run/stage.json"), "nonce", cwd="/project"))
                register.assert_not_called()

    def test_enrollment_uses_the_attempt_nonce_and_needs_an_ancestor(self):
        registration = Mock(enrolled=[{"nonce": "ancestor"}])
        with (
            patch.object(stage_family.sys, "platform", "linux"),
            patch.dict(stage_family.os.environ, {stage_family.family.ENVIRONMENT_KEY: "present"}),
            patch.object(stage_family, "_boot", return_value="boot"),
            patch.object(stage_family, "_root", return_value=Path("/private/store")),
            patch.object(stage_family.family, "register_backup", return_value=registration) as register,
        ):
            binding, result = stage_family.enroll(Path("/run/stage.supervision.json"), "nonce", cwd="/project")
        self.assertEqual({"boot_id": "boot", "directory": "/private/store"}, binding)
        self.assertIs(registration, result)
        register.assert_called_once_with(Path("/private/store"), "nonce", None, boot_id="boot")

    def test_workspace_writable_store_is_not_used_for_enrollment(self):
        with (
            patch.object(stage_family.sys, "platform", "linux"),
            patch.dict(stage_family.os.environ, {stage_family.family.ENVIRONMENT_KEY: "present"}),
            patch.object(stage_family, "_boot", return_value="boot"),
            patch.object(stage_family, "_root", return_value=Path("/project/store")),
            patch.object(stage_family.family, "register_backup") as register,
        ):
            self.assertEqual(
                (None, None), stage_family.enroll(Path("/project/.autocode/runs/stage.json"), "nonce", cwd="/other")
            )
            self.assertEqual((None, None), stage_family.enroll(Path("/elsewhere/run.json"), "nonce", cwd="/project"))
        register.assert_not_called()

    def test_symlinked_store_inside_workspace_is_neither_created_nor_used(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home, workspace = root / "home", root / "project"
            home.mkdir()
            workspace.mkdir()
            (home / ".local").symlink_to(workspace, target_is_directory=True)
            with (
                patch.object(stage_family.sys, "platform", "linux"),
                patch.dict(stage_family.os.environ, {stage_family.family.ENVIRONMENT_KEY: "present"}),
                patch("pwd.getpwuid", return_value=SimpleNamespace(pw_dir=str(home))),
                patch.object(stage_family, "_boot", return_value="boot"),
                patch.object(stage_family.family, "register_backup") as register,
            ):
                self.assertEqual(
                    (None, None),
                    stage_family.enroll(workspace / ".autocode" / "stage.json", "nonce", cwd=workspace),
                )
            register.assert_not_called()
            self.assertFalse((workspace / "state").exists())

    def test_no_acknowledged_ancestor_grants_no_binding(self):
        registration = Mock(enrolled=[])
        with (
            patch.object(stage_family.sys, "platform", "linux"),
            patch.dict(stage_family.os.environ, {stage_family.family.ENVIRONMENT_KEY: "present"}),
            patch.object(stage_family, "_boot", return_value="boot"),
            patch.object(stage_family, "_root", return_value=Path("/private/store")),
            patch.object(stage_family.family, "register_backup", return_value=registration),
        ):
            self.assertEqual(
                (None, registration), stage_family.enroll(Path("/run/stage.json"), "nonce", cwd="/project")
            )

    def test_unknown_workspace_never_enrolls(self):
        with (
            patch.object(stage_family.sys, "platform", "linux"),
            patch.dict(stage_family.os.environ, {stage_family.family.ENVIRONMENT_KEY: "present"}),
            patch.object(stage_family, "_boot", return_value="boot"),
            patch.object(stage_family, "_root", return_value=Path("/private/store")),
            patch.object(stage_family.family, "register_backup") as register,
        ):
            self.assertEqual((None, None), stage_family.enroll(Path("/external/stage.json"), "nonce"))
            self.assertEqual((None, None), stage_family.enroll(Path("/external/stage.json"), "nonce", cwd=object()))
            register.assert_not_called()

    def test_unavailable_private_store_keeps_the_existing_keeper_without_enrollment(self):
        for outcomes in (
            [ValueError("public")],
            [Path("/private/store"), PermissionError("denied")],
            [Path("/private/store"), Path("/different/store")],
        ):
            with (
                self.subTest(outcomes=outcomes),
                patch.object(stage_family.sys, "platform", "linux"),
                patch.dict(stage_family.os.environ, {stage_family.family.ENVIRONMENT_KEY: "present"}),
                patch.object(stage_family, "_boot", return_value="boot"),
                patch.object(stage_family, "_root", side_effect=outcomes),
                patch.object(stage_family.family, "register_backup") as register,
            ):
                self.assertEqual((None, None), stage_family.enroll(Path("/run/stage.json"), "nonce", cwd="/project"))
                register.assert_not_called()

    def test_only_same_boot_bound_certificate_is_accepted(self):
        metadata = {
            "receipt": "/run/stage.supervision.json",
            "nonce": "attempt",
            "family_backup": {"boot_id": "boot", "directory": "/private/store"},
        }
        with (
            patch.object(stage_family.sys, "platform", "linux"),
            patch.object(stage_family, "_boot", return_value="boot"),
            patch.object(stage_family, "_root", return_value=Path("/private/store")),
            patch.object(stage_family.family, "backup_clean", return_value=True) as clean,
        ):
            self.assertTrue(stage_family.clean(metadata))
            clean.assert_called_once_with(Path("/private/store"), "attempt", "boot")
            for changed in (
                {},
                {**metadata, "family_backup": {"boot_id": "other", "directory": "/private/store"}},
                {**metadata, "family_backup": {"boot_id": "boot", "directory": "/project"}},
                {**metadata, "receipt": None},
            ):
                with self.subTest(changed=changed):
                    self.assertFalse(stage_family.clean(changed))

    def test_absent_or_invalid_certificate_never_proves_cleanup(self):
        metadata = {
            "receipt": "/run/stage.supervision.json",
            "nonce": "attempt",
            "family_backup": {"boot_id": "boot", "directory": "/private/store"},
        }
        with (
            patch.object(stage_family.sys, "platform", "linux"),
            patch.object(stage_family, "_boot", return_value="boot"),
            patch.object(stage_family, "_root", return_value=Path("/private/store")),
            patch.object(stage_family.family, "backup_clean", return_value=False),
        ):
            self.assertFalse(stage_family.clean(metadata))
            with patch.object(stage_family.family, "backup_clean", side_effect=stage_family.family.FamilyError("bad")):
                self.assertFalse(stage_family.clean(metadata))
        with patch.object(stage_family.sys, "platform", "darwin"):
            self.assertFalse(stage_family.clean(metadata))

    @unittest.skipIf(os.name == "nt", "kernel family certificates use POSIX descriptors")
    def test_real_certificate_must_match_attempt_boot_and_private_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            attempt, ancestor = "a" * 32, "b" * 32
            boot = "36db2920-0a4c-4a27-85e4-278b5cc25007"
            metadata = {
                "receipt": str(root / "stage.supervision.json"),
                "nonce": attempt,
                "family_backup": {"boot_id": boot, "directory": str(root)},
            }
            path = root / f"family-result-{attempt}-{ancestor}.json"
            packet = {
                "schema": 1,
                "family_clean": True,
                "attempt_nonce": attempt,
                "ancestor_nonce": ancestor,
                "boot_id": boot,
            }
            path.write_text(json.dumps(packet))
            path.chmod(0o600)
            with (
                patch.object(stage_family.sys, "platform", "linux"),
                patch.object(stage_family, "_boot", return_value=boot),
                patch.object(stage_family, "_root", return_value=root),
            ):
                self.assertTrue(stage_family.clean(metadata))
                self.assertFalse(stage_family.clean({**metadata, "nonce": "c" * 32}))
                path.chmod(0o644)
                self.assertFalse(stage_family.clean(metadata))


class PortableStageFamilyTests(unittest.TestCase):
    def test_other_platforms_do_not_create_or_accept_family_proofs(self):
        with patch.object(stage_family.sys, "platform", "darwin"):
            self.assertEqual((None, None), stage_family.enroll(Path("stage.json"), "nonce"))
            self.assertFalse(stage_family.clean({}))


if __name__ == "__main__":
    unittest.main()
