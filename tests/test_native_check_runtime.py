"""Controlled policy admission for Node checks; no Node or sandbox execution."""

import json
import os
import sys
import tempfile
import unittest
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_native_check_execution as execution
import autocode_native_check_runtime as runtime
import autocode_util as util
import autocode_visual_protection as protection


class _BeforeTarget(Exception):
    pass


_SANDBOX = Path("/usr/bin/sandbox-exec")


def _sandbox_identity():
    """Pin Seatbelt's launcher by a controlled digest; these tests never run it.

    The check boundary records and rechecks a hash of every launcher binary.
    Linux has no /usr/bin/sandbox-exec to hash, and its bytes do not affect the
    rendered policy, so every host uses the same digest for it. Other files
    (including the profile and the pinned Node runtime) keep their real hash.
    """
    real = util.file_hash
    return patch.object(util, "file_hash", lambda path: "e" * 64 if Path(path) == _SANDBOX else real(path))


class NativeCheckRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="native-check-runtime-unit-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.run = self.workspace / ".autocode/runs/owned"
        self.run.mkdir(parents=True)
        external = self.root / "external"
        external.mkdir()
        self.files = {}
        for name in ("node", "libnode.dylib", "playwright.js", "browser"):
            path = external / name
            path.write_bytes(("controlled, never executable: " + name).encode())
            path.chmod(0o500 if name in ("node", "browser") else 0o400)
            self.files[str(path)] = util.file_hash(path)
        self.node = external / "node"
        self.logical = self.root / "logical-node"
        self.logical.symlink_to(self.node)
        runtime = {
            "version": 1,
            "node": str(self.node),
            "playwright_module": str(external / "playwright.js"),
            "browser": str(external / "browser"),
            "files": self.files,
        }
        manifest = self.workspace / ".autocode/capture-runtime.json"
        manifest.write_text(json.dumps(runtime))
        manifest.chmod(0o400)
        self.capture = {"path": ".autocode/capture-runtime.json", "sha256": util.file_hash(manifest)}
        profile = {
            "version": 1,
            "mode": "isolated-builtin-openai",
            "opencode_version": "1.18.33",
            "model": "openai/controlled",
            "reasoning_effort": "high",
            "sdk_path": ".autocode/sdk",
            "sdk_manifest_sha256": "a" * 64,
            "transport_identity_sha256": "b" * 64,
            "executable_sha256": "c" * 64,
            "max_requests": 2,
            "image_limits": {"width": 320, "height": 240, "pixels": 76800, "bytes": 1048576},
            "capture_runtime": self.capture,
        }
        self.contract = {"body": {"constraints": ["VISUAL_REVIEW_PROFILE=" + json.dumps(profile)]}}

    def render_check(self, *, setup=False):
        control = self.workspace / ".autocode/tool-containment-controlled"
        control.mkdir(exist_ok=True)
        scratch = control / "scratch"
        scratch.mkdir(exist_ok=True)
        (scratch / "verification").mkdir(exist_ok=True)
        target = execution.NativeCheckExecution.__new__(execution.NativeCheckExecution)
        target.workspace, target.run = self.workspace, self.run
        target.review_record = None
        target.state = {"goal_contract": self.contract}
        target.scope = {
            "control": control,
            "scratch": scratch,
            "tree": scratch / "verification",
            "slots": {},
            "copy": None if setup else {"manifest": "controlled", "sha256": "d" * 64},
        }
        facts = {"binding": {"contract": deepcopy(self.contract), "source": {"files": {}}}}
        target._fresh = lambda: facts
        captured = {}

        def prepared(*args, **kwargs):
            captured.update(kwargs)
            raise _BeforeTarget()

        target.owner = SimpleNamespace(prepare_check=prepared)
        runtime = sys.modules.get("autocode_native_check_runtime")
        locator = patch.object(runtime, "LOGICAL_NODE", str(self.logical)) if runtime else nullcontext()
        with locator, _sandbox_identity(), self.assertRaises(_BeforeTarget):
            target._execute(
                "python3 test_app.py",
                self.run / "check.log",
                timeout=300,
                setup=setup,
                environment={"controlled": "no child"},
            )
        return captured, Path(captured["boundary"]["profile"]).read_text()

    def test_original_check_policy_grants_exact_pinned_node_dependency_files(self):
        prepared, policy = self.render_check()
        for name in self.files:
            self.assertIn("(literal " + json.dumps(name) + ")", policy)
            self.assertNotIn("(subpath " + json.dumps(name) + ")", policy)
        self.assertNotIn("(subpath " + json.dumps(str(self.node.parent)) + ")", policy)
        self.assertIn("(deny default)", policy)
        self.assertIn("(deny file-write* ", policy)
        self.assertFalse(any(line.startswith("(allow network-") for line in policy.splitlines()))
        self.assertEqual(prepared["command"][-1], "python3 test_app.py")

    def runtime_inputs(self, *, setup=False):
        with patch.object(runtime, "LOGICAL_NODE", str(self.logical)):
            return runtime.inputs(self.workspace, self.contract["body"], setup=setup)

    def verify_boundary(self, prepared):
        owner = protection.NativeProtection.__new__(protection.NativeProtection)
        owner.workspace, owner.run = self.workspace, self.run
        body = {
            **prepared,
            "cwd": str(prepared["cwd"]),
            "binding": {"contract": deepcopy(self.contract), "source": {"files": {}}},
            "command_sha256": util.digest(prepared["command"]),
        }
        with (
            patch.object(runtime, "LOGICAL_NODE", str(self.logical)),
            _sandbox_identity(),
            patch.object(protection.source_snapshot, "inventory", return_value={}),
            patch.object(protection.copies, "execution", return_value=(Path(prepared["cwd"]), {})),
        ):
            owner._check_boundary(body)

    def test_original_boundary_reconstructs_runtime_and_refuses_omission_or_forgery(self):
        prepared, _policy = self.render_check()
        self.verify_boundary(prepared)
        for change in ("missing", "unapproved-file", "wrong-manifest", "wrong-node"):
            altered = deepcopy(prepared)
            if change == "missing":
                del altered["boundary"]["node_runtime"]
            elif change == "unapproved-file":
                altered["boundary"]["node_runtime"]["files"][str(self.root / "private")] = "a" * 64
            elif change == "wrong-manifest":
                altered["boundary"]["node_runtime"]["manifest"]["sha256"] = "b" * 64
            else:
                altered["boundary"]["node_runtime"]["logical_node"]["target"] = str(self.root / "foreign")
            with self.subTest(change=change), self.assertRaisesRegex(util.Paused, "Node runtime differs"):
                self.verify_boundary(altered)

    def test_boundary_rechecks_changed_node_alias_and_changed_runtime_bytes(self):
        prepared, _policy = self.render_check()
        self.logical.unlink()
        self.logical.symlink_to(self.node.parent / "browser")
        with self.assertRaisesRegex(util.Paused, "logical Node differs"):
            self.verify_boundary(prepared)
        self.logical.unlink()
        self.logical.symlink_to(self.node)
        with self.assertRaisesRegex(util.Paused, "Node runtime differs"):
            self.verify_boundary(prepared)
        changed = self.node.parent / "libnode.dylib"
        changed.chmod(0o600)
        changed.write_bytes(b"changed runtime")
        changed.chmod(0o400)
        with self.assertRaises(util.Paused):
            self.runtime_inputs()

    def test_symlink_fifo_and_missing_runtime_members_refuse_before_any_hash_read(self):
        member = self.node.parent / "libnode.dylib"
        original = member.read_bytes()
        for kind in ("symlink", "fifo", "missing"):
            member.unlink()
            if kind == "symlink":
                member.symlink_to(self.node)
            elif kind == "fifo":
                os.mkfifo(member, 0o400)
            observed = []
            original_hash = util.file_hash

            def digest(path):
                observed.append(str(path))
                return original_hash(path)

            with (
                self.subTest(kind=kind),
                patch.object(util, "file_hash", side_effect=digest),
                self.assertRaises((util.Paused, FileNotFoundError)),
            ):
                self.runtime_inputs()
            self.assertNotIn(str(member), observed)
            if member.exists() or member.is_symlink():
                member.unlink()
            member.write_bytes(original)
            member.chmod(0o400)

    def test_writable_changed_or_duplicate_runtime_manifest_refuses(self):
        manifest = self.workspace / self.capture["path"]
        original = manifest.read_bytes()
        manifest.chmod(0o600)
        with self.assertRaises(util.Paused):
            self.runtime_inputs()
        manifest.write_bytes(original + b"\n")
        manifest.chmod(0o400)
        with self.assertRaises(util.Paused):
            self.runtime_inputs()
        manifest.chmod(0o600)
        manifest.write_bytes(original)
        manifest.chmod(0o400)
        self.contract["body"]["constraints"] *= 2
        with self.assertRaises(util.Paused):
            self.runtime_inputs()

    def test_policy_widening_refuses_even_with_updated_hash(self):
        prepared, _policy = self.render_check()
        profile = Path(prepared["boundary"]["profile"])
        profile.chmod(0o600)
        with profile.open("a") as stream:
            stream.write("(allow file-read* (subpath " + json.dumps(str(self.node.parent)) + "))\n")
        profile.chmod(0o400)
        prepared["boundary"]["profile_sha256"] = util.file_hash(profile)
        with self.assertRaisesRegex(util.Paused, "kernel policy changed or widened"):
            self.verify_boundary(prepared)

    def test_git_setup_and_profiles_without_capture_runtime_keep_original_policy(self):
        with patch.object(runtime.capture_policy, "runtime_inputs", side_effect=AssertionError("unused input")):
            self.assertIsNone(self.runtime_inputs(setup=True))
            self.assertIsNone(runtime.inputs(self.workspace, {"constraints": []}))
        original = deepcopy(self.contract)
        profile = json.loads(self.contract["body"]["constraints"][0].split("=", 1)[1])
        del profile["capture_runtime"]
        self.contract["body"]["constraints"] = ["VISUAL_REVIEW_PROFILE=" + json.dumps(profile)]
        prepared, policy = self.render_check()
        self.verify_boundary(prepared)
        self.assertNotIn("node_runtime", prepared["boundary"])
        self.assertNotIn(str(self.node), policy)
        self.assertEqual(runtime.policy(None), "")
        self.contract = original

    def test_logical_node_must_resolve_to_pinned_executable_with_plain_parent(self):
        self.logical.unlink()
        self.logical.symlink_to(self.node.parent / "browser")
        with self.assertRaises(util.Paused):
            self.runtime_inputs()
        self.logical.unlink()
        alias = self.root / "aliased-parent"
        alias.symlink_to(self.node.parent, target_is_directory=True)
        self.logical = alias / "node"
        with self.assertRaises(util.Paused):
            self.runtime_inputs()
