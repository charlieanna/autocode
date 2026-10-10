"""Task-footprint validation and actual kernel denial; no provider/image claim."""

import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_tool_containment as containment
import autocode_toolchain as toolchain


class FootprintValidationTests(unittest.TestCase):
    def test_ambiguous_external_metadata_and_glob_scopes_refuse(self):
        for paths in (
            [],
            True,
            ["."],
            ["/outside"],
            ["../outside"],
            ["ui/../outside"],
            ["ui/**"],
            ["ui/*.css"],
            ["ui/[a].css"],
            ["ui\\shared.css"],
            ["ui/shared.css", "ui/shared.css"],
            ["ui/", "ui"],
            [".autocode"],
            [".git/index"],
            [".opencode/agent.json"],
        ):
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                containment.writer_footprint("/workspace", paths)

    def test_readonly_footprint_refuses_before_files_or_native_setup(self):
        with (
            patch.object(containment.sys, "platform", "darwin"),
            patch.object(Path, "is_file", return_value=True),
            patch.object(Path, "mkdir") as mkdir,
            patch.object(containment.subprocess, "run") as launch,
        ):
            with self.assertRaisesRegex(ValueError, "Read-only stages"):
                containment.prepare("/workspace", write_paths=["ui"])
            with self.assertRaisesRegex(ValueError, "Read-only stages"):
                containment.configure(
                    ["opencode"], {}, "/workspace", allow_write=False, request={"write_paths": ["ui"]}
                )
            mkdir.assert_not_called()
            launch.assert_not_called()

    def test_preexisting_shared_inode_refuses_file_and_directory_before_setup(self):
        for kind in ("file", "directory"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(prefix="autocode-alias-refusal-") as temporary:
                root = Path(temporary).resolve()
                original = root / "protected-original.css"
                original.write_text("immutable fixture original\n")
                if kind == "directory":
                    directory = root / "allowed"
                    directory.mkdir()
                    alias = directory / "alias.css"
                    declared = ["allowed"]
                else:
                    alias = root / "allowed.css"
                    declared = ["allowed.css"]
                os.link(original, alias)
                self.assertEqual(2, original.stat().st_nlink)
                real_is_file = Path.is_file

                def file_kind(path):
                    return str(path) == "/usr/bin/sandbox-exec" or real_is_file(path)

                with (
                    patch.object(containment.sys, "platform", "darwin"),
                    patch.object(Path, "is_file", autospec=True, side_effect=file_kind),
                    patch.object(Path, "mkdir") as mkdir,
                    patch.object(containment.subprocess, "run") as launch,
                ):
                    with self.assertRaisesRegex(ValueError, "shared regular-file inode"):
                        containment.prepare(root, allow_write=True, write_paths=declared)
                    with self.assertRaisesRegex(ValueError, "shared regular-file inode"):
                        containment.configure(
                            ["opencode"], {}, root, allow_write=True, request={"write_paths": declared}
                        )
                    mkdir.assert_not_called()
                    launch.assert_not_called()
                self.assertFalse((root / ".autocode").exists())
                self.assertEqual("immutable fixture original\n", original.read_text())
                self.assertEqual("immutable fixture original\n", alias.read_text())

    def test_optout_policy_preserves_exact_legacy_bytes(self):
        with tempfile.TemporaryDirectory(prefix="autocode-legacy-policy-") as temporary:
            root = Path(temporary).resolve()
            scratch = root / ".autocode" / "scratch"
            q = lambda path: json.dumps(str(path))
            expected = (
                "(version 1)\n(deny default)\n"
                "(deny process-info*)\n(allow process-info* (target self))\n"
                "(allow process-exec process-fork)\n(allow signal (target same-sandbox))\n"
                '(allow sysctl-read (sysctl-name-prefix "hw.") '
                '(sysctl-name "kern.ostype" "kern.osrelease" "kern.osversion" '
                '"kern.version" "kern.hostname" "kern.osrevision" "kern.hostid"))\n'
                '(allow file-read-metadata)\n(allow file-read* (literal "/"))\n'
                "(allow file-read* (subpath " + q(root) + ") (subpath " + q(scratch) + "))\n"
                '(allow file-read* (literal "/dev/null") (literal "/dev/urandom") (literal "/dev/random"))\n'
                '(allow file-write* (literal "/dev/null") (subpath ' + q(scratch) + "))\n"
                "(allow file-write* (require-all (subpath " + q(root) + ") "
                "(require-not (subpath " + q(root / ".autocode") + ")) "
                "(require-not (subpath " + q(root / ".git") + ")) "
                "(require-not (subpath " + q(root / ".opencode") + ")) "
                "(require-not (subpath " + q(root / "opencode.json") + ")) "
                "(require-not (subpath " + q(root / "opencode.jsonc") + "))))\n"
                "(deny file-write* (literal " + q(root / ".autocode") + "))\n"
                "(deny file-write* (literal " + q(root / ".git") + "))\n"
                "(deny file-write* (literal " + q(root / ".opencode") + "))\n"
                "(deny file-write* (literal " + q(root / "opencode.json") + "))\n"
                "(deny file-write* (literal " + q(root / "opencode.jsonc") + "))\n"
                '(deny file-read* (regex "(^|/)\\\\.env($|\\\\.)") '
                '(regex "(^|/)(auth\\\\.json|id_rsa|id_ed25519)$"))\n'
            )
            with patch.object(containment, "SYSTEM_READ_ROOTS", ()):
                self.assertEqual(expected, containment.policy(root, scratch, allow_write=True))
                self.assertEqual(expected, containment.policy(root, scratch, allow_write=True, write_paths=None))

    def test_optout_prepare_preserves_legacy_record_shape(self):
        with tempfile.TemporaryDirectory(prefix="autocode-legacy-record-") as temporary:
            root = Path(temporary).resolve()
            with (
                patch.object(containment.sys, "platform", "darwin"),
                patch.object(Path, "is_file", return_value=True),
                patch.object(
                    containment.subprocess, "run", return_value=SimpleNamespace(returncode=0, stderr="")
                ) as probe,
            ):
                boundary = containment.prepare(root, allow_write=True, environment={"PATH": "/controlled/no-provider"})
            self.assertEqual(
                {"version", "platform", "workspace", "shell", "profile", "scratch", "allow_write", "tools"},
                set(boundary),
            )
            self.assertEqual(1, probe.call_count)
            self.assertEqual([boundary["shell"], "-c", "exit 0"], probe.call_args.args[0])
            self.assertNotIn("write_paths", boundary)
            self.assertNotIn("writer_path_kinds", boundary)

    def test_kind_change_refuses_pinned_manifest_before_launch(self):
        with tempfile.TemporaryDirectory(prefix="autocode-file-kind-change-") as temporary:
            root = Path(temporary).resolve()
            source = root / "approved.css"
            source.write_text("approved original\n")
            with (
                patch.object(containment.sys, "platform", "darwin"),
                patch.object(Path, "is_file", return_value=True),
                patch.object(containment.subprocess, "run", return_value=SimpleNamespace(returncode=0, stderr="")),
            ):
                boundary = containment.prepare(root, allow_write=True, write_paths=["approved.css"])
            control = Path(boundary["profile"]).parent
            denied = "Permission denied (controlled fixture; not a native receipt)"
            conformance = control / "conformance.json"
            conformance.write_text(
                json.dumps(
                    {
                        "writer_footprint": {
                            "write_paths": ["approved.css"],
                            "writer_path_kinds": {"approved.css": "file"},
                            "writer_policy_inputs": boundary["writer_policy_inputs"],
                            "tool": "bash",
                            "command": "controlled-denial",
                            "exit": 0,
                            "output": denied,
                            "outside_hardlink": {"command": "controlled-hardlink-denial", "exit": 0, "output": denied},
                        }
                    }
                )
            )
            boundary.update(native_version=containment.SUPPORTED_VERSION, conformance=str(conformance))
            for name in ("profile", "shell", "conformance"):
                boundary[name + "_sha256"] = hashlib.sha256(Path(boundary[name]).read_bytes()).hexdigest()
            source.unlink()
            source.mkdir()
            with (
                patch.object(containment.sys, "platform", "darwin"),
                patch.object(containment.subprocess, "run") as launch,
            ):
                with self.assertRaisesRegex(RuntimeError, "footprint authority changed"):
                    containment.verify(boundary)
                launch.assert_not_called()
            self.assertFalse((source / "undeclared.css").exists())

    @staticmethod
    def _controlled_writer_proof(boundary):
        """Controlled structural fixture, never a real native conformance receipt."""
        control = Path(boundary["profile"]).parent
        conformance = control / "conformance.json"
        denied = "Permission denied (controlled fixture; not a native receipt)"
        conformance.write_text(
            json.dumps(
                {
                    "writer_footprint": {
                        "write_paths": boundary["write_paths"],
                        "writer_path_kinds": boundary["writer_path_kinds"],
                        "writer_policy_inputs": boundary["writer_policy_inputs"],
                        "tool": "bash",
                        "command": "controlled-denial",
                        "exit": 0,
                        "output": denied,
                        "outside_hardlink": {"command": "controlled-hardlink-denial", "exit": 0, "output": denied},
                    }
                }
            )
        )
        boundary.update(native_version=containment.SUPPORTED_VERSION, conformance=str(conformance))
        for name in ("profile", "shell", "conformance"):
            boundary[name + "_sha256"] = hashlib.sha256(Path(boundary[name]).read_bytes()).hexdigest()

    def test_transient_file_directory_file_render_keeps_measured_literal_grant(self):
        with tempfile.TemporaryDirectory(prefix="autocode-transient-kind-") as temporary:
            root = Path(temporary).resolve()
            source = root / "approved.css"
            original = b"approved original\n"
            source.write_bytes(original)
            actual_render = containment._render_policy
            actual_kinds = containment._writer_path_kinds

            def transient_render(*args, **options):
                source.unlink()
                source.mkdir()
                try:
                    # Filesystem differs while rendering, deterministically; no sleep.
                    return actual_render(*args, **options)
                finally:
                    source.rmdir()
                    source.write_bytes(original)

            with (
                patch.object(containment.sys, "platform", "darwin"),
                patch.object(Path, "is_file", return_value=True),
                patch.object(containment, "_writer_path_kinds", wraps=actual_kinds) as measured,
                patch.object(containment, "_render_policy", side_effect=transient_render),
                patch.object(containment.subprocess, "run", return_value=SimpleNamespace(returncode=0, stderr="")),
            ):
                boundary = containment.prepare(root, allow_write=True, write_paths=["approved.css"])
                self.assertEqual(1, measured.call_count)
            text = Path(boundary["profile"]).read_text()
            exact_path = json.dumps(str(source))
            self.assertIn("(require-any (literal " + exact_path + "))", text)
            self.assertNotIn("(require-any (subpath " + exact_path + "))", text)
            self.assertEqual({"approved.css": "file"}, boundary["writer_path_kinds"])
            self.assertEqual(original, source.read_bytes())
            self._controlled_writer_proof(boundary)
            with (
                patch.object(containment.sys, "platform", "darwin"),
                patch.object(containment.subprocess, "run") as launch,
            ):
                self.assertIs(boundary, containment.verify(boundary))
                launch.assert_not_called()

    def test_rehashed_wide_profile_refuses_the_pinned_file_map(self):
        with tempfile.TemporaryDirectory(prefix="autocode-profile-map-") as temporary:
            root = Path(temporary).resolve()
            source = root / "approved.css"
            source.write_text("approved original\n")
            with (
                patch.object(containment.sys, "platform", "darwin"),
                patch.object(Path, "is_file", return_value=True),
                patch.object(containment.subprocess, "run", return_value=SimpleNamespace(returncode=0, stderr="")),
            ):
                boundary = containment.prepare(root, allow_write=True, write_paths=["approved.css"])
            profile = Path(boundary["profile"])
            exact_path = json.dumps(str(source))
            literal = "(require-any (literal " + exact_path + "))"
            self.assertIn(literal, profile.read_text())
            profile.chmod(0o600)
            profile.write_text(profile.read_text().replace(literal, "(require-any (subpath " + exact_path + "))"))
            # Rehashing the structural fixture alone must not legitimize broader grants.
            self._controlled_writer_proof(boundary)
            with (
                patch.object(containment.sys, "platform", "darwin"),
                patch.object(containment.subprocess, "run") as launch,
            ):
                with self.assertRaisesRegex(RuntimeError, "footprint authority changed"):
                    containment.verify(boundary)
                launch.assert_not_called()
            self.assertEqual("approved original\n", source.read_text())

    def test_public_policy_measures_once_and_keeps_directory_and_file_grants(self):
        with tempfile.TemporaryDirectory(prefix="autocode-policy-measured-once-") as temporary:
            root = Path(temporary).resolve()
            file = root / "approved.css"
            file.write_text("approved original\n")
            directory = root / "ui"
            directory.mkdir()
            actual_kinds = containment._writer_path_kinds
            with patch.object(containment, "_writer_path_kinds", wraps=actual_kinds) as measured:
                text = containment.policy(
                    root, root / ".autocode" / "scratch", allow_write=True, write_paths=["approved.css", "ui"]
                )
                self.assertEqual(1, measured.call_count)
            expected = (
                "(require-any (literal " + json.dumps(str(file)) + ") (subpath " + json.dumps(str(directory)) + "))"
            )
            self.assertIn(expected, text)
            self.assertEqual("approved original\n", file.read_text())

    def test_pinned_policy_input_drift_refuses_without_native_launch(self):
        with tempfile.TemporaryDirectory(prefix="autocode-policy-input-drift-") as temporary:
            root = Path(temporary).resolve()
            (root / "approved.css").write_text("approved original\n")
            with (
                patch.object(containment.sys, "platform", "darwin"),
                patch.object(Path, "is_file", return_value=True),
                patch.object(containment.subprocess, "run", return_value=SimpleNamespace(returncode=0, stderr="")),
            ):
                boundary = containment.prepare(root, allow_write=True, write_paths=["approved.css"])
            self._controlled_writer_proof(boundary)
            boundary["writer_policy_inputs"]["protected_paths"].append(str(root / "new-protected.css"))
            with (
                patch.object(containment.sys, "platform", "darwin"),
                patch.object(containment.subprocess, "run") as launch,
            ):
                with self.assertRaisesRegex(RuntimeError, "footprint authority changed"):
                    containment.verify(boundary)
                launch.assert_not_called()

    def test_canonical_json_roundtrip_preserves_nonalphabetical_footprint(self):
        with tempfile.TemporaryDirectory(prefix="autocode-footprint-json-roundtrip-") as temporary:
            root = Path(temporary).resolve()
            (root / "ui").mkdir()
            footprint = ["ui/z.css", "ui/a.css"]
            for name in footprint:
                (root / name).write_text("approved original\n")
            with (
                patch.object(containment.sys, "platform", "darwin"),
                patch.object(Path, "is_file", return_value=True),
                patch.object(containment.subprocess, "run", return_value=SimpleNamespace(returncode=0, stderr="")),
            ):
                boundary = containment.prepare(root, allow_write=True, write_paths=footprint)
            self._controlled_writer_proof(boundary)
            restored = json.loads(json.dumps(boundary, sort_keys=True))
            self.assertEqual(footprint, restored["write_paths"])
            self.assertEqual(["ui/a.css", "ui/z.css"], list(restored["writer_path_kinds"]))
            profile_before = Path(restored["profile"]).read_bytes()
            with (
                patch.object(containment.sys, "platform", "darwin"),
                patch.object(containment.subprocess, "run") as launch,
            ):
                self.assertIs(restored, containment.verify(restored))
                launch.assert_not_called()
            self.assertEqual(profile_before, Path(restored["profile"]).read_bytes())
            for name in footprint:
                self.assertEqual("approved original\n", (root / name).read_text())

    def test_failed_native_hardlink_conformance_holds_before_model_launch(self):
        for returned_exit in (0, 1):
            with (
                self.subTest(returned_exit=returned_exit),
                tempfile.TemporaryDirectory(prefix="autocode-link-hold-") as temporary,
            ):
                root = Path(temporary).resolve()
                ui = root / "ui"
                ui.mkdir()
                source = ui / "approved.css"
                source.write_text("approved original\n")
                calls = []

                def controlled_native(argv, **options):
                    calls.append(list(argv))
                    if argv[-1] == "--version":
                        return SimpleNamespace(returncode=0, stdout=containment.SUPPORTED_VERSION + "\n", stderr="")
                    if argv[-2:] == ["-c", "exit 0"]:
                        return SimpleNamespace(returncode=0, stdout="", stderr="")
                    response = {
                        "permission": [{"permission": "bash", "pattern": "*", "action": "allow"}],
                        "tools": {"bash": True, "read": False},
                    }
                    if "--params" in argv:
                        params = json.loads(argv[argv.index("--params") + 1])
                        command = params["command"]
                        if params["description"].startswith("AutoCode kernel containment"):
                            control = Path(options["env"]["SHELL"]).parent
                            (control / "scratch" / "conformance-capture").touch()
                        if params["description"].startswith("AutoCode outside-source hardlink"):
                            # Controlled failed native outcome: link actually appeared;
                            # even a lying zero-exit response must fail effect checks.
                            words = shlex.split(command)
                            os.link(words[-2], words[-1])
                            response = {
                                "input": {"command": command},
                                "result": {
                                    "metadata": {"exit": returned_exit},
                                    "output": "Permission denied (controlled response)",
                                },
                            }
                        else:
                            response = {
                                "input": {"command": command},
                                "result": {
                                    "metadata": {"exit": 0},
                                    "output": "Permission denied (controlled response)",
                                },
                            }
                    return SimpleNamespace(returncode=0, stdout=json.dumps(response), stderr="")

                real_is_file = Path.is_file

                def file_kind(path):
                    return str(path) == "/usr/bin/sandbox-exec" or real_is_file(path)

                command = ["opencode", "run", "--agent", "writer", "--model", "approved-model"]
                environment = {"PATH": "/controlled", "OPENCODE_CONFIG_CONTENT": json.dumps({"agent": {"writer": {}}})}
                with (
                    patch.object(containment.sys, "platform", "darwin"),
                    patch.object(Path, "is_file", autospec=True, side_effect=file_kind),
                    patch.object(containment.shutil, "which", return_value="/controlled/opencode"),
                    patch.object(toolchain, "discover", return_value={"read_roots": [], "probes": []}),
                    patch.object(containment.subprocess, "run", side_effect=controlled_native),
                ):
                    with self.assertRaisesRegex(RuntimeError, "outside-source hardlink; footprint unsupported"):
                        containment.configure(
                            command, environment, root, allow_write=True, request={"write_paths": ["ui"]}
                        )
                self.assertFalse(any("run" in argv for argv in calls))
                self.assertTrue(any("--params" in argv for argv in calls))
                self.assertEqual("approved original\n", source.read_text())
                self.assertFalse(list(root.glob("tool-containment-*-workspace-sentinel")))
                self.assertFalse(list((root / ".autocode").glob("tool-containment-*/conformance.json")))


@unittest.skipUnless(
    sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file(),
    "requires real macOS Seatbelt task-footprint enforcement",
)
class WriterFootprintKernelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="autocode-task-footprint-")
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name).resolve()
        self.root = self.parent / "project"
        self.root.mkdir()
        self.ui = self.root / "ui"
        self.ui.mkdir()
        self.source = self.ui / "shared.css"
        self.source.write_text("original shared style\n")
        self.other = self.root / "unrelated.py"
        self.other.write_text("original unrelated source\n")
        self.metadata = self.root / ".autocode"
        self.metadata.mkdir()
        self.receipt = self.metadata / "protected-test-receipt.json"
        self.receipt.write_text('{"test_only":true}\n')
        self.environment = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}

    def boundary(self, paths):
        return containment.prepare(self.root, allow_write=True, write_paths=paths, environment=self.environment)

    def shell(self, boundary, command):
        return subprocess.run(
            [boundary["shell"], "-c", command], cwd=self.root, capture_output=True, text=True, timeout=20
        )

    def denied(self, boundary, command):
        result = self.shell(boundary, command)
        self.assertNotEqual(0, result.returncode, (command, result.stdout, result.stderr))
        self.assertRegex(result.stderr, "Operation not permitted|Permission denied")

    def test_literal_source_write_succeeds_and_other_source_and_receipt_stay_protected(self):
        boundary = self.boundary(["ui/shared.css"])
        result = self.shell(boundary, 'printf "new shared style\\n" > ' + shlex.quote(str(self.source)))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("new shared style\n", self.source.read_text())
        for target in (self.other, self.receipt):
            with self.subTest(target=target):
                self.denied(boundary, ": > " + shlex.quote(str(target)))
        self.assertEqual("original unrelated source\n", self.other.read_text())
        self.assertEqual('{"test_only":true}\n', self.receipt.read_text())

    def test_directory_scope_permits_new_files_without_granting_sibling_writes(self):
        components = self.ui / "components"
        components.mkdir()
        boundary = self.boundary(["ui/components"])
        added = components / "new.css"
        result = self.shell(boundary, 'printf "component\\n" > ' + shlex.quote(str(added)))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("component\n", added.read_text())
        self.denied(boundary, ": > " + shlex.quote(str(self.source)))
        self.assertEqual("original shared style\n", self.source.read_text())

    def test_restored_outside_write_is_denied_at_its_first_write(self):
        boundary = self.boundary(["ui"])
        command = (
            'printf "transient corruption" > '
            + shlex.quote(str(self.other))
            + ' && printf "original unrelated source\\n" > '
            + shlex.quote(str(self.other))
        )
        self.denied(boundary, command)
        self.assertEqual("original unrelated source\n", self.other.read_text())

    def test_symlink_and_ancestor_replacement_do_not_expand_kernel_scope(self):
        boundary = self.boundary(["ui"])
        link = self.ui / "outside-link"
        result = self.shell(boundary, shlex.join(["ln", "-s", str(self.other), str(link)]))
        self.assertEqual(0, result.returncode, result.stderr)
        self.denied(boundary, ": > " + shlex.quote(str(link)))
        self.denied(boundary, shlex.join(["mv", str(self.ui), str(self.root / "escaped-ui")]))
        self.assertTrue(self.ui.is_dir())
        self.assertFalse((self.root / "escaped-ui").exists())
        self.assertEqual("original unrelated source\n", self.other.read_text())

    def test_forked_shell_cannot_escape_and_owned_scratch_remains_writable(self):
        boundary = self.boundary(["ui"])
        self.denied(
            boundary, shlex.join(["/bin/bash", "--noprofile", "--norc", "-c", ": > " + shlex.quote(str(self.other))])
        )
        scratch_file = Path(boundary["scratch"]) / "tool-output"
        result = self.shell(boundary, ": > " + shlex.quote(str(scratch_file)))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(scratch_file.is_file())
        self.assertEqual("original unrelated source\n", self.other.read_text())

    def test_symlinked_declared_scope_refuses_before_a_child_launch(self):
        alias = self.root / "alias"
        alias.symlink_to(self.ui, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlinked authority path"):
            self.boundary(["alias"])

    def test_file_grant_does_not_become_a_subtree_after_parent_replaces_file(self):
        boundary = self.boundary(["ui/shared.css"])
        self.assertEqual({"ui/shared.css": "file"}, boundary["writer_path_kinds"])
        # Parent fixture mutation isolates the old profile's path-kind boundary.
        # It does not assert that a contained worker can replace the file.
        self.source.unlink()
        self.source.mkdir()
        descendant = self.source / "undeclared.css"
        self.denied(boundary, 'printf "undeclared descendant" > ' + shlex.quote(str(descendant)))
        self.assertTrue(self.source.is_dir())
        self.assertFalse(descendant.exists())
        self.assertEqual("original unrelated source\n", self.other.read_text())
        self.assertEqual('{"test_only":true}\n', self.receipt.read_text())

    def test_outside_or_protected_source_cannot_be_hardlinked_to_scratch_or_allowed_directory(self):
        boundary = self.boundary(["ui"])
        for source in (self.other, self.receipt):
            raw = source.read_bytes()
            for location, directory in (("scratch", Path(boundary["scratch"])), ("allowed", self.ui)):
                destination = directory / (location + "-" + source.name + "-alias")
                with self.subTest(source=source, destination=destination):
                    self.denied(boundary, shlex.join(["/bin/ln", str(source), str(destination)]))
                    self.assertFalse(destination.exists())
                    self.assertFalse(destination.is_symlink())
                    self.assertEqual(raw, source.read_bytes())
                    self.assertEqual(1, source.stat().st_nlink)

    def test_allowed_source_cannot_be_hardlinked_to_undeclared_or_private_destination(self):
        boundary = self.boundary(["ui"])
        for destination in (self.root / "outside-alias.css", self.metadata / "private-alias.css"):
            with self.subTest(destination=destination):
                self.denied(boundary, shlex.join(["/bin/ln", str(self.source), str(destination)]))
                self.assertFalse(destination.exists())
                self.assertFalse(destination.is_symlink())
                self.assertEqual("original shared style\n", self.source.read_text())
                self.assertEqual(1, self.source.stat().st_nlink)

    def test_preexisting_shared_inode_under_directory_or_file_is_refused_before_native_probe(self):
        outside_alias = self.root / "outside-existing-alias.css"
        os.link(self.source, outside_alias)
        existing = {path.name for path in self.metadata.iterdir()}
        with patch.object(containment.subprocess, "run") as launch:
            for paths in (["ui/shared.css"], ["ui"]):
                with self.subTest(paths=paths), self.assertRaisesRegex(ValueError, "shared regular-file inode"):
                    self.boundary(paths)
            launch.assert_not_called()
        self.assertEqual(existing, {path.name for path in self.metadata.iterdir()})
        self.assertEqual("original shared style\n", outside_alias.read_text())


if __name__ == "__main__":
    unittest.main()
