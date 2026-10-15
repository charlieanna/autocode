"""Offline final-config controls, not native capture or live image acceptance."""

import hashlib  # noqa: F401 - preserve existing import behavior
import json
import os
import tempfile
import unittest
from contextlib import ExitStack, nullcontext
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autocode_contract_identity as contract
import autocode_native_calls as calls
import autocode_native_conformance as conformance
import autocode_native_image as images
import autocode_native_role_isolation as isolation
import autocode_provider_launch as launch
import autocode_tool_containment as containment
import autocode_util as util
import autocode_visual_history as history
import autocode_visual_protection as protection
import autocode_visual_runtime as visual
import goal_fixtures
from providers import opencode

from tests.visual_capture_fixtures import make_capture, png


class PromptDescriptorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="native-image-stdin-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / "prompt"
        self.path.write_bytes(b"exact final prompt\n")
        self.path.chmod(0o400)
        self.binding = {"path": str(self.path), "sha256": util.file_hash(self.path)}

    def test_original_readonly_fd_matches_without_consuming_input(self):
        with self.path.open("rb") as stream:
            images.require_prompt_fd(self.binding, stream)
            self.assertEqual(0, stream.tell())
            self.assertEqual(b"exact final prompt\n", stream.read())

    def test_foreign_identical_file_nonzero_offset_and_writable_fd_refuse(self):
        other = self.root / "other"
        other.write_bytes(self.path.read_bytes())
        other.chmod(0o400)
        with other.open("rb") as stream, self.assertRaisesRegex(ValueError, "original readonly"):
            images.require_prompt_fd(self.binding, stream)
        with self.path.open("rb") as stream:
            stream.read(1)
            with self.assertRaisesRegex(ValueError, "already been consumed"):
                images.require_prompt_fd(self.binding, stream)
            self.assertEqual(1, stream.tell())
        self.path.chmod(0o600)
        with self.path.open("r+b") as stream, self.assertRaisesRegex(ValueError, "original readonly"):
            images.require_prompt_fd(self.binding, stream)

    def test_pipe_symlink_path_and_same_inode_changed_bytes_refuse(self):
        reader, writer = os.pipe()
        os.close(writer)
        with os.fdopen(reader, "rb") as stream, self.assertRaises(ValueError):
            images.require_prompt_fd(self.binding, stream)
        link = self.root / "link"
        link.symlink_to(self.path)
        with self.path.open("rb") as stream, self.assertRaisesRegex(ValueError, "pathname changed"):
            images.require_prompt_fd({**self.binding, "path": str(link)}, stream)
        with self.path.open("rb") as stream:
            self.path.chmod(0o600)
            self.path.write_bytes(b"wrong final prompt\n")
            self.path.chmod(0o400)
            with self.assertRaisesRegex(ValueError, "bytes changed"):
                images.require_prompt_fd(self.binding, stream)


class NativeImageConfigTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="native-image-config-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.run = self.root / ".autocode" / "runs" / "fixture"
        self.run.mkdir(parents=True)
        (self.root / "greet.py").write_text('print("original supplied source")\n')
        self.sdk = self.root / ".autocode" / "sdk"
        self.sdk.mkdir()
        dependencies = {"@opencode-ai/plugin": "1.18.33"}
        (self.sdk / "package.json").write_text(json.dumps({"private": True, "dependencies": dependencies}))
        packages = {"": {"dependencies": dependencies}}
        for name in ("plugin", "sdk"):
            relative = "node_modules/@opencode-ai/" + name
            installed = self.sdk / relative
            (installed / "dist").mkdir(parents=True)
            (installed / "package.json").write_text(json.dumps({"name": "@opencode-ai/" + name, "version": "1.18.33"}))
            (installed / "dist" / "index.js").write_text("// controlled fixture, never executed\n")
            packages[relative] = {
                "version": "1.18.33",
                "resolved": "https://registry.npmjs.org/" + name,
                "integrity": "sha512-controlled-not-qualified",
            }
        (self.sdk / "package-lock.json").write_text(json.dumps({"lockfileVersion": 3, "packages": packages}))
        manifest = self.sdk / "sdk-manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "version": 1,
                    "files": {
                        str(p.relative_to(self.sdk)): util.file_hash(p) for p in self.sdk.rglob("*") if p.is_file()
                    },
                }
            )
        )
        manifest.chmod(0o400)
        self.binary = self.root / ".autocode" / "opencode"
        self.binary.write_text("#!/bin/sh\nexit 99 # no native or model request permitted\n")
        self.binary.chmod(0o500)
        activity = Path(opencode.__file__).with_name("opencode_activity.mjs").resolve()
        identity = {
            "engine": "opencode",
            "version": "1.18.33",
            "identity_version": 2,
            "executable": str(self.binary),
            "config_hashes": {str(activity): util.file_hash(activity)},
            "environment_config_hashes": {},
        }
        self.environment = {"PATH": str(self.binary.parent) + ":/usr/bin:/bin", "HOME": str(self.root / "unused-auth")}
        # Qualify on the same simulated macOS host that prepare() launches on.
        with patch.object(containment.sys, "platform", "darwin"):
            identity.update(opencode.configuration_identity(self.root, env=self.environment))
        self.profile = {
            "version": 1,
            "mode": "isolated-builtin-openai",
            "opencode_version": "1.18.33",
            "model": "openai/gpt-6-sol",
            "reasoning_effort": "high",
            "sdk_path": ".autocode/sdk",
            "sdk_manifest_sha256": util.file_hash(manifest),
            "transport_identity_sha256": util.digest(identity),
            "executable_sha256": util.file_hash(self.binary),
            "max_requests": 4,
            "image_limits": {"width": 2, "height": 1, "pixels": 2, "bytes": 4096},
        }
        exports = self.root / ".autocode" / "reference"
        exports.mkdir()
        cases = []
        for cid in ("light", "dark"):
            image, context = exports / (cid + ".png"), exports / (cid + ".json")
            png(image, 2, 1)
            context.write_text(json.dumps({"state": cid}))
            cases.append(
                {
                    "id": cid,
                    "file_key": "Fixture",
                    "node_id": "1:2",
                    "state": cid,
                    "route": "/" + cid,
                    "implementation_paths": ["greet.py"],
                    "viewport": {"width": 2, "height": 1, "device_scale_factor": 1},
                    "export_scale": 1,
                    "artifacts": {
                        kind: {"path": p.name, "sha256": util.file_hash(p)}
                        for kind, p in (("screenshot", image), ("design_context", context))
                    },
                }
            )
        manifest_body = {"version": 1, "files": [{"key": "Fixture", "nodes": ["1:2"]}], "cases": cases}
        body = goal_fixtures.body()
        body["acceptance_criteria"] = [
            {"id": "C1", "criterion": "light visual match"},
            {"id": "C2", "criterion": "dark visual match"},
        ]
        for criterion in body["acceptance_criteria"]:
            criterion.update(verification_method="Inspect exact reference and candidate images", human_review=False)
        body["milestones"][0]["acceptance_criteria"] = ["C1", "C2"]
        body["initial_task"] = {
            "kind": "validate",
            "milestone_id": body["milestones"][0]["id"],
            "acceptance_criteria": ["C1", "C2"],
            "affected_paths": ["greet.py"],
        }
        body["constraints"] += [
            'VISUAL_CASE_CRITERIA={"light":["C1"],"dark":["C2"]}',
            "VISUAL_REVIEW_PROFILE=" + json.dumps(self.profile),
        ]
        goal = {"task_id": "fixture", "revision": 1, "approval_status": "approved", "body": body}
        goal["hash"] = util.digest({k: goal[k] for k in ("task_id", "revision", "body")})
        goal["approval_event"] = {"actor": "user_cli", "token": contract.token(goal)}
        self.state = {
            "workspace": str(self.root),
            "task": "original operator input",
            "goal_contract": goal,
            "user_events": [deepcopy(goal["approval_event"])],
            "acceptance_criteria": deepcopy(body["acceptance_criteria"]),
            "current_task": {
                "id": "T1",
                "kind": "validate",
                "milestone_id": body["milestones"][0]["id"],
                "acceptance_criteria": ["C1", "C2"],
                "affected_paths": ["greet.py"],
            },
            "stages": [],
            "settings": {
                "engine": "opencode",
                "provider": "opencode",
                "transport_identity": identity,
                "roles": {
                    "sol": {
                        "engine": "opencode",
                        "provider": "opencode",
                        "model": self.profile["model"],
                        "reasoning_effort": "high",
                        "model_pinned": True,
                    }
                },
                "design_manifest": {
                    "root": str(exports),
                    "body": manifest_body,
                    "manifest_hash": util.digest(manifest_body),
                },
            },
        }
        self.snapshot = {
            "head": "controlled-original-source",
            "files": {"greet.py": util.file_hash(self.root / "greet.py")},
        }
        self.snapshot["revision"] = util.digest(self.snapshot)
        with patch.object(util, "snapshot", return_value=self.snapshot):
            for case in cases:
                make_capture(self.root, util.digest(manifest_body), case)
        self.native = calls.PlanningCalls.__new__(calls.PlanningCalls)
        self.native.workspace, self.native.run_dir = self.root, self.run
        self.native.intent, self.native.initial_task = self.profile, self.state["task"]
        self.native.context = SimpleNamespace(
            factory=SimpleNamespace(scope_authority=lambda **_: self.fail("unexpected slice authority"))
        )
        self.observed_captures = None
        self.capture_proof = self.run / "test-only-capture-proof.json"
        self.capture_proof.write_text('{"test_only":"not native capture qualification"}\n')

        def capture_authority(caller, *, state, snapshot, captures):
            # TEST-ONLY seam: production requires the original capture journal.
            self.assertIs(caller, self.native)
            self.assertIs(state, self.state)
            self.assertEqual(self.snapshot, snapshot)
            self.assertEqual({"light", "dark"}, set(captures))
            self.observed_captures = captures
            return {str(self.capture_proof): util.file_hash(self.capture_proof)}

        self.authority = capture_authority
        self.owner = isolation.NativeRoleIsolation(
            self.state,
            self.root,
            self.run,
            "sol",
            sdk_path=self.profile["sdk_path"],
            sdk_manifest_sha256=self.profile["sdk_manifest_sha256"],
            transport_identity_sha256=self.profile["transport_identity_sha256"],
            executable_sha256=self.profile["executable_sha256"],
        )
        self.schema = self.run / "schema.json"
        self.schema.write_text('{"type":"object"}')
        self.base = self.run / "sol"
        self.prompt = "Review original source\nCURRENT HANDOFF DATA\n" + json.dumps(
            {"source_revision": self.snapshot["revision"]}
        )

    def configure(self, command, environment, workspace, *, allow_write, request, retain_launch_identity):
        self.assertIsNotNone(self.observed_captures)
        self.assertFalse(allow_write)
        self.assertTrue(retain_launch_identity)
        self.assertEqual(4, command.count("--file"))
        config = json.loads(environment["OPENCODE_CONFIG_CONTENT"])
        self.assertEqual(2, len(config["plugin"]))
        control = self.root / ".autocode" / ("tool-containment-" + "a" * 32)
        control.mkdir()
        (control / "scratch").mkdir()
        (control / "shell").write_text("#!/bin/sh\nexit 99\n")
        (control / "policy.sb").write_text(containment.policy(self.root, control / "scratch"))
        boundary = {
            "version": 1,
            "platform": "darwin",
            "native_version": "1.18.33",
            "workspace": str(self.root),
            "allow_write": False,
            "tools": ["bash"],
            "scratch": str(control / "scratch"),
        }
        for key, filename in (("shell", "shell"), ("profile", "policy.sb")):
            path = control / filename
            path.chmod(0o500 if key == "shell" else 0o400)
            boundary[key], boundary[key + "_sha256"] = str(path), util.file_hash(path)
        actual = {
            "name": "autocode_sol",
            "tools": {"bash": True, "read": False},
            "permission": [
                {"permission": "*", "pattern": "*", "action": "deny"},
                {"permission": "bash", "pattern": "*", "action": "allow"},
                {"permission": "external_directory", "pattern": "*", "action": "deny"},
            ],
        }
        config["shell"] = boundary["shell"]
        config["agent"]["autocode_sol"]["permission"] = containment.shell_permissions(actual["permission"])
        child = {**environment, "OPENCODE_CONFIG_CONTENT": json.dumps(config), "SHELL": boundary["shell"]}
        proof = control / "conformance.json"
        proof.write_text(
            json.dumps(
                {
                    "test_only_native_response": True,
                    "launch_identity": conformance.observe(command, child, self.binary, actual),
                }
            )
        )
        proof.chmod(0o400)
        boundary.update(conformance=str(proof), conformance_sha256=util.file_hash(proof))
        child["AUTOCODE_TOOL_CONTAINMENT"] = json.dumps(boundary)
        return child, boundary

    def prepare(self, configure=None):
        with (
            patch.object(calls.PlanningCalls, "image_capture_authority", self.authority, create=True),
            patch.object(containment, "configure", side_effect=configure or self.configure),
            patch.object(containment.sys, "platform", "darwin"),
        ):
            packet = images.NativeImagePreparation(
                native=self.native,
                isolation=self.owner,
                state=self.state,
                workspace=self.root,
                run_dir=self.run,
                base=self.base,
                snapshot=self.snapshot,
                prompt=self.prompt,
                schema=self.schema,
                events=self.base.with_suffix(".jsonl"),
                adapter=opencode,
                render_prompt=launch.render_native_image_prompt,
            )
            result = launch.prepare(
                engine="opencode",
                adapter=opencode,
                role="sol",
                route_role="sol",
                workspace=self.root,
                run_dir=self.run,
                session=None,
                model=self.profile["model"],
                effort="high",
                allow_write=False,
                planning=False,
                report=self.base.with_suffix(".json"),
                schema=self.schema,
                prompt_file=self.base.with_suffix(".prompt.md"),
                sandbox="read-only",
                transport_args=[],
                chatgpt=False,
                provider="opencode",
                settings=self.state["settings"],
                native_role_isolation=self.owner,
                native_environment=self.environment,
                native_image_preparation=packet,
            )
        return packet, result

    def test_final_config_contains_images_plugin_audit_and_prompt_before_observation(self):
        packet, (command, env, _, worker) = self.prepare()
        context = packet.context()
        self.assertEqual("READY", context["status"])
        self.assertEqual("operator_supplied_source", context["producer"]["kind"])
        self.assertEqual(["reference", "candidate", "reference", "candidate"], [r["kind"] for r in context["images"]])
        self.assertEqual(["light", "light", "dark", "dark"], [r["case_id"] for r in context["images"]])
        self.assertEqual(4, context["audit_options"]["max_requests"])
        self.assertFalse(Path(context["audit_path"]).exists())
        self.assertIn("Inspect BOTH images", packet.prompt)
        self.assertIn("NATIVE TOOL BOUNDARY", packet.prompt)
        self.assertEqual(context, worker["visual_runtime"])
        self.assertEqual(util.digest(env), context["environment_sha256"])
        self.assertNotEqual(
            context["child_identity"]["environment_sha256"], context["native_child_identity"]["environment_sha256"]
        )
        descriptor = worker["native_role_isolation"]
        self.assertEqual(descriptor["image_review"]["prompt"], context["prompt"])
        self.assertTrue(
            all(Path(row["path"]).is_relative_to(Path(descriptor["manifest"]).parent) for row in context["images"])
        )
        with packet.prompt_path.open("rb") as stream:
            images.require_prompt_fd(context["prompt"], stream)
        with patch.object(containment.sys, "platform", "darwin"):
            self.owner.verify_launch(
                state=self.state, command=command, environment=env, boundary=worker["tool_containment"]
            )
        self.assertIn(context["launch_manifest"], self.owner.image_pins(context))

    def test_post_seal_attachment_environment_and_context_mutations_refuse(self):
        packet, (command, env, _, worker) = self.prepare()
        with patch.object(containment.sys, "platform", "darwin"):
            for argv, child in (
                (command + ["--file", "foreign"], env),
                (command, {**env, "AUTOCODE_IMAGE_AUDIT": "{}"}),
            ):
                with self.assertRaises((ValueError, RuntimeError)):
                    self.owner.verify_launch(
                        state=self.state, command=argv, environment=child, boundary=worker["tool_containment"]
                    )
        context = packet.context()
        context["images"][0]["sha256"] = "a" * 64
        with self.assertRaisesRegex(ValueError, "original image packet"):
            self.owner.image_pins(context)
        target = Path(packet.context()["images"][0]["path"])
        target.chmod(0o600)
        target.write_bytes(b"not a PNG")
        target.chmod(0o400)
        with (
            patch.object(containment.sys, "platform", "darwin"),
            self.assertRaisesRegex(ValueError, "staged SDK/plugin/config bytes changed"),
        ):
            self.owner.verify_launch(
                state=self.state, command=command, environment=env, boundary=worker["tool_containment"]
            )

    def test_audit_created_by_native_debug_refuses_without_deleting_evidence(self):
        def configure(*args, **kwargs):
            child, boundary = self.configure(*args, **kwargs)
            Path(json.loads(child["AUTOCODE_IMAGE_AUDIT"])["path"]).write_text("premature audit\n")
            return child, boundary

        with self.assertRaisesRegex(util.Paused, "native setup used the image audit"):
            self.prepare(configure)
        audit = next(self.run.glob("native-role-*/visual/delivery.jsonl"))
        self.assertEqual("premature audit\n", audit.read_text())

    def test_missing_native_capture_proof_refuses_before_png_staging_or_debug(self):
        def refused(caller, **kwargs):
            raise ValueError("missing original captured process row")

        self.authority = refused
        with self.assertRaisesRegex(util.Paused, "missing original captured process row"):
            self.prepare()
        self.assertEqual([], list(self.run.glob("native-role-*/visual")))
        self.assertFalse((self.root / ".autocode" / ("tool-containment-" + "a" * 32)).exists())

    def test_actual_run_role_passes_original_scope_authority_before_launch(self):
        # Controlled provider preparation only: the actual caller and final
        # visual guard run, then stop before any child or model request.
        import autocode as runner

        packet, result = self.prepare()
        self.state.update(version=2, iteration=1, next_stage="sol")
        self.native.guard = Mock()
        self.native.role_isolation = Mock(return_value=self.owner)
        self.native.context.factory.prepare_launch = Mock(return_value=object())
        original = visual.verify_prelaunch
        observed = []

        def checked(context, state, **kwargs):
            self.assertIs(kwargs["scope_authority"], self.native.context.factory.scope_authority)
            original(context, state, **kwargs)
            observed.append(context["launch_sha256"])
            raise util.Paused("PAUSED_VISUAL_EVIDENCE", "controlled stop before child launch")

        mocks = (
            (runner.dispatch, "enforce_cross_model_verification", Mock()),
            (runner.processes, "preflight", Mock()),
            (runner.processes, "interruption_handler", lambda: nullcontext()),
            (runner.interventions, "admission", lambda _: nullcontext()),
            (runner.artifacts, "reserve", Mock(return_value=self.base)),
            (runner.support, "review_generation_schema", lambda schema, *_: schema),
            (runner.source_scope, "snapshot", Mock(return_value=self.snapshot)),
            (runner.task_preflight, "guard", Mock()),
            (runner.readonly_events, "prepare_opencode_snapshots", Mock()),
            (runner.job_source, "capture", Mock()),
            (runner.provider_launch, "prepare", Mock(return_value=result)),
            (runner.provider_launch, "verify_containment", Mock()),
            (runner.native_image, "NativeImagePreparation", Mock(return_value=packet)),
            (visual, "verify_prelaunch", checked),
        )
        with ExitStack() as stack:
            for module, name, value in mocks:
                stack.enter_context(patch.object(module, name, value))
            with self.assertRaisesRegex(util.Paused, "controlled stop before child launch"):
                runner.run_role(
                    role="sol",
                    prompt=self.prompt,
                    sandbox="read-only",
                    workspace=self.root,
                    run_dir=self.run,
                    state=self.state,
                    schema=self.schema,
                    model=self.profile["model"],
                    allow_write=False,
                    dry_run=False,
                    native=self.native,
                )
        self.assertEqual([packet.context()["launch_sha256"]], observed)
        self.native.context.factory.prepare_launch.assert_called_once()

    def original_image_protection(self, context, environment, boundary):
        owner = protection.NativeProtection.__new__(protection.NativeProtection)
        owner.workspace, owner.run = self.root, self.run
        body = {
            "grants": [],
            "boundary": boundary,
            "record_identity": {"stage": "sol", "role": "sol"},
            "command_sha256": util.digest(context["command"]),
            "environment_sha256": util.digest(environment),
            "native_isolation": context["native_isolation"],
            "pins": {**context["pins"], context["launch_manifest"]: context["launch_sha256"]},
        }
        owner._finished_for = lambda record: ([], {"body": body}, {"body": {"record": {"exit_code": 0}}})
        return owner

    def test_original_launch_replay_requires_full_environment_and_protected_ready_bytes(self):
        packet, (_, environment, _, worker) = self.prepare()
        context = packet.context()
        owner = self.original_image_protection(context, environment, worker["tool_containment"])
        self.assertEqual(
            context["native_isolation"], owner.verify_original_image_configuration({}, context)["descriptor"]
        )
        changed = deepcopy(context)
        changed["environment_sha256"] = "a" * 64
        with self.assertRaisesRegex(util.Paused, "original readonly Sol launch"):
            owner.verify_original_image_configuration({}, changed)
        ready = Path(context["launch_manifest"])
        ready.chmod(0o600)
        ready.write_text("{}\n")
        ready.chmod(0o400)
        with self.assertRaisesRegex(util.Paused, "pre-dispatch protection"):
            owner.verify_original_image_configuration({}, context)

    def test_native_history_replays_same_owner_config_without_legacy_profile_files(self):
        packet, (_, environment, _, worker) = self.prepare()
        context = packet.context()
        reader = history.NativeReviewHistory.__new__(history.NativeReviewHistory)
        reader.workspace, reader.run = self.root, self.run
        reader.protection = self.original_image_protection(context, environment, worker["tool_containment"])
        metadata = {
            "record": {},
            "runtime_root": str(Path(history.__file__).parent),
            "receipt": {
                "binding": visual.acceptance.binding(context["current"], self.state["settings"]["design_manifest"])
            },
        }
        self.assertFalse((Path(context["plugin_path"]).parent / "profile.json").exists())
        reader._replay_profile(self.state, context, lambda path: Path(path).read_bytes(), metadata)
        inline = Path(context["native_isolation"]["configuration_sources"]["inline"]["path"])
        inline.chmod(0o600)
        inline.write_text("{}\n")
        inline.chmod(0o400)
        with self.assertRaises(util.Paused):
            reader._replay_profile(self.state, context, lambda path: Path(path).read_bytes(), metadata)
