"""Controlled byte/API fixtures only; no native SDK/kernel/model qualification."""

import json
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_contract_identity as contract
import autocode_native_conformance as native
import autocode_native_role_isolation as isolation
import autocode_provider_launch as provider_launch
import autocode_tool_containment as containment
import autocode_toolchain as toolchain
import autocode_util as util
from providers import opencode


class IsolationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.run = self.root / ".autocode" / "runs" / "owned"
        self.run.mkdir(parents=True)
        self.sdk = self.root / ".autocode" / "sdk"
        self.sdk.mkdir()
        dependencies = {"@opencode-ai/plugin": "1.18.33"}
        (self.sdk / "package.json").write_text(json.dumps({"private": True, "dependencies": dependencies}))
        packages = {"": {"dependencies": dependencies}}
        for name in ("plugin", "sdk"):
            key = "node_modules/@opencode-ai/" + name
            folder = self.sdk / key
            (folder / "dist").mkdir(parents=True)
            (folder / "package.json").write_text(json.dumps({"name": "@opencode-ai/" + name, "version": "1.18.33"}))
            (folder / "dist" / "index.js").write_text("// controlled structural fixture, never imported\n")
            packages[key] = {
                "version": "1.18.33",
                "resolved": "https://registry.npmjs.org/" + name,
                "integrity": "sha512-structural-only-not-qualified",
            }
        (self.sdk / "package-lock.json").write_text(json.dumps({"lockfileVersion": 3, "packages": packages}))
        members = {
            str(path.relative_to(self.sdk)): util.file_hash(path) for path in self.sdk.rglob("*") if path.is_file()
        }
        self.manifest = self.sdk / "sdk-manifest.json"
        self.manifest.write_text(json.dumps({"version": 1, "files": members}))
        self.manifest.chmod(0o400)
        self.executable = self.root / ".autocode" / "opencode"
        self.executable.write_text("#!/bin/sh\nexit 99 # every native/provider request refused\n")
        self.executable.chmod(0o500)
        self.activity = Path(opencode.__file__).with_name("opencode_activity.mjs").resolve()
        self.identity = {
            "engine": "opencode",
            "version": "1.18.33",
            "identity_version": 2,
            "executable": str(self.executable),
            "config_hashes": {str(self.activity): util.file_hash(self.activity)},
            "environment_config_hashes": {
                key: None
                for key in (
                    "OPENCODE_CONFIG_CONTENT",
                    "OPENCODE_PERMISSION",
                    "OPENCODE_CONFIG_DIR",
                    "OPENCODE_DISABLE_PROJECT_CONFIG",
                    "OPENCODE_PURE",
                    "OPENCODE_TEST_MANAGED_CONFIG_DIR",
                )
            },
        }
        self.state = {
            "workspace": str(self.root),
            "settings": {
                "engine": "opencode",
                "provider": "opencode",
                "roles": {
                    "terra": {
                        "engine": "opencode",
                        "model": "zai-coding-plan/glm-5.3",
                        "reasoning_effort": "high",
                        "model_pinned": True,
                    }
                },
                "transport_identity": deepcopy(self.identity),
                "total_time_limit": None,
                "usage_limit": None,
            },
        }
        self.command = [
            str(self.executable),
            "run",
            "--dir",
            str(self.root),
            "--format",
            "json",
            "--agent",
            "autocode_terra",
            "--model",
            "zai-coding-plan/glm-5.3",
            "--variant",
            "high",
            "--title",
            "controlled fixture",
        ]
        self.env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(self.root / "original-home-not-opened"),
            "OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX": "54321",
            # The snapshot excludes file (#1630) travels as a whole GIT_CONFIG_* set; a stray index does not.
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.excludesFile",
            "GIT_CONFIG_VALUE_0": "/project/.autocode/snapshot-excludes",
            "GIT_CONFIG_KEY_7": "must-not-survive",
            "NODE_OPTIONS": "--require must-not-survive.js",
            "UNRELATED_SECRET": "must-not-be-retained",
            "OPENCODE_CONFIG_CONTENT": json.dumps(
                {
                    "$schema": "https://opencode.ai/config.json",
                    "share": "disabled",
                    "autoupdate": False,
                    "plugin": [self.activity.as_uri()],
                    "agent": {
                        "autocode_terra": {
                            "description": "controlled fixture",
                            "mode": "primary",
                            "permission": {"task": "deny", "question": "deny", "edit": "deny"},
                        }
                    },
                }
            ),
        }
        self.actual = {
            "name": "autocode_terra",
            "tools": {"bash": True, "read": False, "edit": False},
            "permission": [
                {"permission": "*", "pattern": "*", "action": "deny"},
                {"permission": "bash", "pattern": "*", "action": "allow"},
                {"permission": "bash", "pattern": "git push*", "action": "deny"},
                {"permission": "external_directory", "pattern": "*", "action": "deny"},
            ],
        }

    def owner(self):
        return isolation.NativeRoleIsolation(
            self.state,
            self.root,
            self.run,
            "terra",
            sdk_path=".autocode/sdk",
            sdk_manifest_sha256=util.file_hash(self.manifest),
            transport_identity_sha256=util.digest(self.state["settings"]["transport_identity"]),
            executable_sha256=util.file_hash(self.executable),
        )

    def stage(self, owner, writable=False):
        return owner.stage(
            command=self.command,
            environment=self.env,
            configuration_identity={key: self.identity[key] for key in ("config_hashes", "environment_config_hashes")},
            allow_write=writable,
        )

    def finalize_fixture(self, owner, *, private_reads=None, control_name="a" * 32):
        command, env = self.stage(owner)
        control = self.root / ".autocode" / ("tool-containment-" + control_name)
        control.mkdir()
        (control / "scratch").mkdir()
        (control / "shell").write_text("#!/bin/sh\nexit 99\n")
        (control / "policy.sb").write_text(
            containment.policy(self.root, control / "scratch")
            if private_reads is None
            else containment._render_policy(self.root, control / "scratch", private_reads=private_reads)
        )
        boundary = {
            "version": 1,
            "platform": "darwin",
            "native_version": "1.18.33",
            "workspace": str(self.root),
            "allow_write": False,
            "tools": ["bash"],
            "scratch": str(control / "scratch"),
            **({"private_reads": private_reads} if private_reads is not None else {}),
        }
        for name, filename in (("shell", "shell"), ("profile", "policy.sb")):
            path = control / filename
            path.chmod(0o500 if name == "shell" else 0o400)
            boundary[name] = str(path)
            boundary[name + "_sha256"] = util.file_hash(path)
        final = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        final["shell"] = boundary["shell"]
        agent = command[command.index("--agent") + 1]
        final["agent"][agent]["permission"] = containment.shell_permissions(self.actual["permission"])
        env.update(OPENCODE_CONFIG_CONTENT=json.dumps(final), SHELL=boundary["shell"])
        proof = control / "conformance.json"
        proof.write_text(
            json.dumps(
                {
                    "test_only_controlled_native_response": True,
                    "launch_identity": native.observe(command, env, self.executable, self.actual),
                    **(
                        {"private_reads": {**private_reads, "command": "controlled", "tool": "bash", "exit": 0}}
                        if private_reads is not None
                        else {}
                    ),
                }
            )
        )
        proof.chmod(0o400)
        boundary.update(conformance=str(proof), conformance_sha256=util.file_hash(proof))
        env["AUTOCODE_TOOL_CONTAINMENT"] = json.dumps(boundary)
        with patch.object(containment, "sys", SimpleNamespace(platform="darwin")):
            descriptor = owner.finalize(command=command, environment=env, boundary=boundary)
        boundary["native_role_isolation"] = descriptor
        env["AUTOCODE_TOOL_CONTAINMENT"] = json.dumps(boundary)
        env["AUTOCODE_NATIVE_ROLE_ISOLATION"] = json.dumps(descriptor)
        return command, env, boundary, descriptor

    def test_preapproval_readonly_stages_exact_route_sdk_and_caps_without_execution(self):
        saved = deepcopy(self.state)
        owner = self.owner()
        command, env = self.stage(owner)
        self.assertEqual(self.command, command)
        self.assertEqual("54321", env["OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX"])
        self.assertEqual(
            ("1", "core.excludesFile", "/project/.autocode/snapshot-excludes"),
            (env["GIT_CONFIG_COUNT"], env["GIT_CONFIG_KEY_0"], env["GIT_CONFIG_VALUE_0"]),
        )
        self.assertNotIn("GIT_CONFIG_KEY_7", env)
        self.assertNotIn("NODE_OPTIONS", env)
        self.assertNotIn("UNRELATED_SECRET", env)
        final = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        self.assertEqual(["zai-coding-plan"], final["enabled_providers"])
        self.assertEqual("zai-coding-plan/glm-5.3", final["agent"]["autocode_terra"]["model"])
        self.assertEqual(
            {"title", "summary", "compaction"},
            {name for name, value in final["agent"].items() if value == {"disable": True}},
        )
        self.assertEqual(saved, self.state)
        with self.assertRaisesRegex(ValueError, "already used"):
            self.stage(owner)

    def test_writer_without_approved_contract_refuses_before_staging(self):
        owner = self.owner()
        before = set(self.run.iterdir())
        with self.assertRaisesRegex(ValueError, "writer requires the approved contract"):
            self.stage(owner, True)
        self.assertEqual(before, set(self.run.iterdir()))

    def test_custom_singapore_configuration_is_held_without_route_substitution(self):
        self.state["settings"]["roles"]["terra"]["model"] = "xiaomi-token-plan-sgp/mimo-v2.6-pro"
        self.state["settings"]["transport_identity"]["config_hashes"]["/custom/opencode.json"] = "a" * 64
        saved = deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "cannot discard saved inputs"):
            self.owner()
        self.assertEqual(saved, self.state)
        self.assertEqual([], list(self.run.iterdir()))

    def test_saved_route_or_limit_change_after_construction_refuses(self):
        for key, value in (("reasoning_effort", "max"), ("model", "openai/gpt-6-sol")):
            with self.subTest(key=key):
                owner = self.owner()
                old = self.state["settings"]["roles"]["terra"][key]
                self.state["settings"]["roles"]["terra"][key] = value
                with self.assertRaisesRegex(ValueError, "route, caps or workspace changed"):
                    self.stage(owner)
                self.state["settings"]["roles"]["terra"][key] = old

    def test_actual_native_name_tools_and_ordered_rules_are_required(self):
        for change in (
            {"name": "different"},
            {"tools": {"bash": 1}},
            {"tools": {"bash": True, "edit": True}},
            {"permission": [{"permission": "bash", "pattern": "*", "action": "allow", "invented": True}]},
        ):
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                native.observe(self.command, self.env, self.executable, {**self.actual, **change})
        observed = native.observe(self.command, self.env, self.executable, self.actual)
        self.assertEqual(self.actual["permission"], observed["effective_permission"])
        self.assertEqual(self.actual["tools"], observed["effective_tools"])
        self.assertEqual(observed, native.verify(observed, self.command, self.env))

    def test_inherited_native_defaults_are_shadowed_and_output_allowance_is_suppressed(self):
        output = str(Path(self.env["HOME"]) / ".local/share/opencode/tool-output") + "/*"
        inherited = [
            {"permission": "*", "pattern": "*", "action": "allow"},
            {"permission": "doom_loop", "pattern": "*", "action": "ask"},
            {"permission": "read", "pattern": "*", "action": "allow"},
            {"permission": "read", "pattern": "*.env", "action": "ask"},
            {"permission": "external_directory", "pattern": output, "action": "deny"},
        ]
        self.actual["permission"] = inherited + self.actual["permission"]
        original = deepcopy(self.actual["permission"])
        owner = self.owner()
        command, env, boundary, _ = self.finalize_fixture(owner)
        self.assertEqual(
            {"external_directory": {output: "deny"}},
            json.loads(env["OPENCODE_CONFIG_CONTENT"])["permission"],
        )
        receipt = json.loads(Path(boundary["conformance"]).read_text())
        self.assertEqual(original, receipt["launch_identity"]["effective_permission"])
        with patch.object(containment, "sys", SimpleNamespace(platform="darwin")):
            owner.verify_launch(state=self.state, command=command, environment=env, boundary=boundary)

    def test_planning_agent_launch_requires_this_runs_private_read_denial(self):
        # #1250: the owner refuses a native planning shell that could read private run state.
        agent = "autocode_terra_plan_" + "0" * 32
        self.command[self.command.index("--agent") + 1] = agent
        config = json.loads(self.env["OPENCODE_CONFIG_CONTENT"])
        config["agent"] = {agent: config["agent"]["autocode_terra"]}
        self.env["OPENCODE_CONFIG_CONTENT"] = json.dumps(config)
        self.actual["name"] = agent
        sibling = self.root / ".autocode" / "runs" / "sibling"
        sibling.mkdir()
        for number, reads in enumerate((None, containment.private_run_reads(self.root, sibling))):
            with self.subTest(reads=reads), self.assertRaisesRegex(ValueError, "private run-state read denial"):
                self.finalize_fixture(self.owner(), private_reads=reads, control_name=str(number) * 32)
        owner = self.owner()
        reads = containment.private_run_reads(self.root, self.run)
        command, env, boundary, descriptor = self.finalize_fixture(owner, private_reads=reads, control_name="f" * 32)
        self.assertEqual(reads, boundary["private_reads"])
        with patch.object(containment.sys, "platform", "darwin"):
            self.assertEqual(
                descriptor, owner.verify_launch(state=self.state, command=command, environment=env, boundary=boundary)
            )

    def test_unshadowed_native_output_allowance_refuses_before_launch(self):
        self.actual["permission"].append(
            {"permission": "external_directory", "pattern": "/native/tool-output/*", "action": "allow"}
        )
        with self.assertRaisesRegex(ValueError, "actual native ordered permission"):
            self.finalize_fixture(self.owner())

    def test_finalized_descriptor_pins_members_and_refuses_later_image_extension(self):
        owner = self.owner()
        command, env, boundary, descriptor = self.finalize_fixture(owner)
        self.assertEqual(
            descriptor, owner.verify_launch(state=self.state, command=command, environment=env, boundary=boundary)
        )
        self.assertEqual(util.file_hash(descriptor["manifest"]), descriptor["manifest_sha256"])
        self.assertTrue(all(util.file_hash(path) == digest for path, digest in descriptor["files"].items()))
        self.assertNotIn("must-not-be-retained", Path(descriptor["manifest"]).read_text())
        changed = dict(env)
        final = json.loads(changed["OPENCODE_CONFIG_CONTENT"])
        final["plugin"].append("file:///unreviewed/image-extension.mjs")
        changed["OPENCODE_CONFIG_CONTENT"] = json.dumps(final)
        with self.assertRaisesRegex(RuntimeError, "current launch inputs changed"):
            owner.verify_launch(state=self.state, command=command, environment=changed, boundary=boundary)

    def test_late_staged_member_and_executable_mutation_both_refuse(self):
        owner = self.owner()
        command, env, boundary, descriptor = self.finalize_fixture(owner)
        member = Path(next(path for path in descriptor["files"] if path.endswith("activity.mjs")))
        original = member.read_bytes()
        member.chmod(0o600)
        member.write_bytes(original + b"\n// changed\n")
        with self.assertRaisesRegex(ValueError, "staged SDK/plugin/config bytes changed"):
            owner.verify_launch(state=self.state, command=command, environment=env, boundary=boundary)
        member.write_bytes(original)
        member.chmod(0o400)
        self.executable.chmod(0o700)
        self.executable.write_text("#!/bin/sh\nexit 98\n")
        with self.assertRaisesRegex(ValueError, "native executable changed"):
            owner.verify_launch(state=self.state, command=command, environment=env, boundary=boundary)

    def test_fake_owner_cannot_reach_adapter_or_native_debug(self):
        class Subclass(isolation.NativeRoleIsolation):
            pass

        for owner in (True, False, object(), Subclass.__new__(Subclass)):
            with self.subTest(owner_type=type(owner).__name__), patch.object(containment.subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "exact CODE owner"):
                    opencode.launch(
                        "terra",
                        self.root,
                        self.run,
                        None,
                        "zai-coding-plan/glm-5.3",
                        "high",
                        False,
                        env=self.env,
                        containment={},
                        isolation=owner,
                    )
                run.assert_not_called()

    def approve_writer(self, paths):
        saved = {
            "task_id": "one",
            "revision": 1,
            "approval_status": "approved",
            "body": {
                "constraints": [],
                "open_blocking_questions": [],
                "milestones": [{"id": "M1", "acceptance_criteria": ["AC1"], "affected_paths": paths}],
            },
        }
        saved["hash"] = util.digest({key: saved[key] for key in ("task_id", "revision", "body")})
        saved["approval_event"] = {"actor": "user_cli", "token": contract.token(saved)}
        self.state["goal_contract"] = saved
        self.state["user_events"] = [deepcopy(saved["approval_event"])]
        self.state["current_task"] = {
            "id": "task-one",
            "kind": "implement",
            "milestone_id": "M1",
            "criterion_ids": ["AC1"],
            "affected_paths": paths,
            "source_revision": "a" * 64,
            "contract_hash": saved["hash"],
            "contract_revision": saved["revision"],
        }

    def test_writer_request_and_profile_have_exact_literal_file_and_directory_restrictions(self):
        (self.root / "single.py").write_text("preserved source\n")
        (self.root / "component").mkdir()
        paths = ["single.py", "component"]
        self.approve_writer(paths)
        owner = self.owner()
        restriction = owner.containment_request(allow_write=True)
        self.assertEqual({"write_paths": paths}, restriction)
        profile = containment.policy(
            self.root,
            self.root / ".autocode" / "scratch-fixture",
            allow_write=True,
            write_paths=restriction["write_paths"],
        )
        self.assertIn(
            '(require-any (literal "'
            + str(self.root / "single.py")
            + '") (subpath "'
            + str(self.root / "component")
            + '"))',
            profile,
        )
        self.assertNotIn('(subpath "' + str(self.root / "single.py") + '")', profile)
        with patch.object(opencode, "launch", side_effect=RuntimeError("controlled before-native stop")) as launch:
            with self.assertRaisesRegex(util.Paused, "controlled before-native stop"):
                provider_launch.prepare(
                    engine="opencode",
                    adapter=opencode,
                    role="terra",
                    route_role="terra",
                    workspace=self.root,
                    run_dir=self.run,
                    session=None,
                    model="zai-coding-plan/glm-5.3",
                    effort="high",
                    allow_write=True,
                    planning=False,
                    report=self.run / "report",
                    schema=self.run / "schema",
                    prompt_file=self.run / "prompt",
                    sandbox="workspace-write",
                    transport_args=[],
                    chatgpt=False,
                    provider="opencode",
                    native_role_isolation=owner,
                )
        self.assertEqual(paths, launch.call_args.kwargs["containment"]["write_paths"])
        self.assertEqual("NOT_AUTHENTICATED", owner._writer_restriction["causal_assignment"])

    def test_missing_or_changed_writer_paths_refuse_before_adapter_and_native_setup(self):
        self.approve_writer(["single.py"])
        for paths in (None, [], ["../outside"], ["*.py"]):
            self.state["current_task"]["affected_paths"] = paths
            owner = self.owner()
            with patch.object(opencode, "launch") as launch, patch.object(containment.subprocess, "run") as run:
                with self.assertRaises(util.Paused):
                    provider_launch.prepare(
                        engine="opencode",
                        adapter=opencode,
                        role="terra",
                        route_role="terra",
                        workspace=self.root,
                        run_dir=self.run,
                        session=None,
                        model="zai-coding-plan/glm-5.3",
                        effort="high",
                        allow_write=True,
                        planning=False,
                        report=self.run / "report",
                        schema=self.run / "schema",
                        prompt_file=self.run / "prompt",
                        sandbox="workspace-write",
                        transport_args=[],
                        chatgpt=False,
                        provider="opencode",
                        native_role_isolation=owner,
                    )
                launch.assert_not_called()
                run.assert_not_called()
        self.state["current_task"]["affected_paths"] = ["single.py"]
        owner = self.owner()
        owner.containment_request(allow_write=True)
        self.state["current_task"]["id"] = "changed-task"
        with self.assertRaisesRegex(ValueError, "restriction or file/directory kind changed"):
            owner.containment_request(allow_write=True)

    def test_missing_policy_or_removed_launch_envelope_cannot_bypass_owner_verification(self):
        owner = self.owner()
        command, env, boundary, descriptor = self.finalize_fixture(owner)
        with self.assertRaisesRegex(util.Paused, "mandatory tool boundary"):
            provider_launch.verify_containment(
                {"native_role_isolation": descriptor}, state=self.state, native_role_isolation=owner
            )
        for name in native.ENVELOPES:
            changed = dict(env)
            del changed[name]
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "mandatory final native launch locators"):
                owner.verify_launch(state=self.state, command=command, environment=changed, boundary=boundary)
        changed = dict(boundary)
        del changed["native_role_isolation"]
        with self.assertRaisesRegex(ValueError, "mandatory final native launch locators"):
            owner.verify_launch(state=self.state, command=command, environment=env, boundary=changed)

    def test_removed_worker_descriptor_is_a_typed_hold_after_original_owner_verification(self):
        owner = self.owner()
        command, env, boundary, descriptor = self.finalize_fixture(owner)
        worker = {
            "command": command,
            "environment": env,
            "tool_containment": boundary,
            "native_role_isolation": descriptor,
        }
        with patch.object(containment, "sys", SimpleNamespace(platform="darwin")):
            provider_launch.verify_containment(worker, state=self.state, native_role_isolation=owner)
            del worker["native_role_isolation"]
            with self.assertRaisesRegex(util.Paused, "locator differs"):
                provider_launch.verify_containment(worker, state=self.state, native_role_isolation=owner)

    def test_missing_or_malformed_final_worker_inputs_are_typed_holds(self):
        owner = self.owner()
        command, env, boundary, descriptor = self.finalize_fixture(owner)
        valid = {
            "command": command,
            "environment": env,
            "tool_containment": boundary,
            "native_role_isolation": descriptor,
        }
        with patch.object(containment, "sys", SimpleNamespace(platform="darwin")):
            for key, value in (
                ("command", None),
                ("command", []),
                ("command", True),
                ("environment", None),
                ("environment", []),
                ("environment", {"bad": True}),
            ):
                worker = {**valid, key: value}
                with (
                    self.subTest(key=key, value=value),
                    self.assertRaisesRegex(util.Paused, "final native command/environment"),
                ):
                    provider_launch.verify_containment(worker, state=self.state, native_role_isolation=owner)
            for key in ("command", "environment"):
                worker = dict(valid)
                del worker[key]
                with self.subTest(missing=key), self.assertRaisesRegex(util.Paused, "final native command/environment"):
                    provider_launch.verify_containment(worker, state=self.state, native_role_isolation=owner)

    def test_opted_in_planning_writer_refuses_before_native_setup(self):
        owner = self.owner()
        with patch.object(containment.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "planning must remain readonly"):
                opencode.launch(
                    "terra",
                    self.root,
                    self.run,
                    None,
                    "zai-coding-plan/glm-5.3",
                    "high",
                    True,
                    planning=True,
                    env=self.env,
                    containment={},
                    isolation=owner,
                )
            run.assert_not_called()
        self.assertEqual([], list(self.run.iterdir()))

    def test_legacy_conformance_does_not_require_name_or_add_observation_fields(self):
        control = self.root / ".autocode" / ("tool-containment-" + "c" * 32)
        control.mkdir()
        (control / "scratch").mkdir()
        (control / "shell").write_text("controlled shell\n")
        (control / "policy.sb").write_text("controlled profile\n")
        boundary = {
            "version": 1,
            "workspace": str(self.root),
            "allow_write": False,
            "tools": ["bash"],
            "profile": str(control / "policy.sb"),
            "shell": str(control / "shell"),
            "scratch": str(control / "scratch"),
        }
        actual = {key: value for key, value in self.actual.items() if key != "name"}

        def controlled_run(argv, **kwargs):
            if argv[-1] == "--version":
                return subprocess.CompletedProcess(argv, 0, "1.18.33\n", "")
            if "--tool" in argv:
                params = json.loads(argv[-1])
                (control / "scratch" / "conformance-capture").write_text("controlled probe output\n")
                response = {
                    "input": {"command": params["command"]},
                    "result": {"metadata": {"exit": 0}, "output": "Permission denied (controlled fixture)"},
                }
            else:
                response = actual
            return subprocess.CompletedProcess(argv, 0, json.dumps(response), "")

        with (
            patch.object(containment, "sys", SimpleNamespace(platform="darwin")),
            patch.object(containment, "prepare", return_value=boundary),
            patch.object(containment, "verify", side_effect=lambda value: value),
            patch.object(toolchain, "discover", return_value={"read_roots": [], "probes": []}),
            patch.object(containment.subprocess, "run", side_effect=controlled_run),
        ):
            env, final = containment.configure(self.command, self.env, self.root, allow_write=False, request={})
        proof = json.loads(Path(final["conformance"]).read_text())
        self.assertEqual(
            {
                "toolchain_checks",
                "command",
                "tool",
                "exit",
                "output",
                "native_receipt",
                "native_version",
                "profile_sha256",
                "shell_sha256",
                "backend",
                "backend_version",
            },
            set(proof),
        )
        self.assertNotIn("launch_identity", proof)
        self.assertNotIn("readonly_startup_boundary", proof)
        receipt = proof["native_receipt"]
        self.assertEqual("passed", receipt["status"])
        self.assertEqual([], receipt["failed_predicates"])
        for predicate in (
            "response_object",
            "input_object",
            "result_object",
            "metadata_object",
            "input_command_match",
            "metadata_exit_int",
            "exit_zero",
            "scratch_created",
            "sentinel_untouched",
        ):
            self.assertIs(True, receipt["checks"][predicate], predicate)

    def test_writer_startup_is_readonly_before_the_exact_native_target_footprint(self):
        source = self.root / "single.py"
        source.write_text("preserved controlled source\n")
        events = []

        def controlled_prepare(workspace, **kwargs):
            writable = kwargs["allow_write"]
            events.append(("prepare", writable, kwargs.get("write_paths")))
            control = self.root / ".autocode" / ("tool-containment-" + ("d" if writable else "e") * 32)
            control.mkdir()
            (control / "scratch").mkdir()
            (control / "shell").write_text("controlled shell\n")
            (control / "policy.sb").write_text(
                containment.policy(
                    self.root, control / "scratch", allow_write=writable, write_paths=kwargs.get("write_paths")
                )
            )
            result = {
                "version": 1,
                "workspace": str(self.root),
                "allow_write": writable,
                "tools": ["bash"],
                "profile": str(control / "policy.sb"),
                "shell": str(control / "shell"),
                "scratch": str(control / "scratch"),
            }
            if writable:
                result.update(
                    write_paths=kwargs["write_paths"],
                    writer_path_kinds={"single.py": "file"},
                    writer_policy_inputs=containment._policy_inputs([], []),
                )
            return result

        def controlled_run(argv, **kwargs):
            if argv[-1] == "--version":
                return subprocess.CompletedProcess(argv, 0, "1.18.33\n", "")
            if "--tool" not in argv:
                return subprocess.CompletedProcess(argv, 0, json.dumps(self.actual), "")
            params = json.loads(argv[-1])
            shell = Path(kwargs["env"]["SHELL"])
            writable = shell.parent.name == "tool-containment-" + "d" * 32
            if params["description"] == "AutoCode selected toolchain startup check (no model)":
                events.append(("startup_probe", writable))
                self.assertFalse(writable)
            if params["description"] == "AutoCode kernel containment conformance (no model)":
                (shell.parent / "scratch" / "conformance-capture").write_text("controlled scratch\n")
            return subprocess.CompletedProcess(
                argv,
                0,
                json.dumps(
                    {
                        "input": {"command": params["command"]},
                        "result": {"metadata": {"exit": 0}, "output": "Permission denied (controlled fixture)"},
                    }
                ),
                "",
            )

        with (
            patch.object(containment, "sys", SimpleNamespace(platform="darwin")),
            patch.object(containment, "prepare", side_effect=controlled_prepare),
            patch.object(containment, "verify", side_effect=lambda value: value),
            patch.object(
                toolchain, "discover", return_value={"read_roots": [], "probes": ["controlled-canonical-startup"]}
            ),
            patch.object(containment.subprocess, "run", side_effect=controlled_run),
        ):
            env, final = containment.configure(
                self.command,
                self.env,
                self.root,
                allow_write=True,
                request={"write_paths": ["single.py"]},
                retain_launch_identity=True,
            )
        self.assertEqual([("prepare", False, None), ("startup_probe", False), ("prepare", True, ["single.py"])], events)
        proof = json.loads(Path(final["conformance"]).read_text())
        self.assertFalse(proof["readonly_startup_boundary"]["allow_write"])
        self.assertEqual(["single.py"], final["write_paths"])
        self.assertEqual({"single.py": "file"}, proof["writer_footprint"]["writer_path_kinds"])
        self.assertEqual(
            native._sha(env["OPENCODE_CONFIG_CONTENT"].encode()), proof["launch_identity"]["final_inline_sha256"]
        )
        self.assertEqual("preserved controlled source\n", source.read_text())

    def test_approved_task_and_finalized_writer_restriction_do_not_grant_causal_dispatch(self):
        (self.root / "single.py").write_text("preserved source\n")
        self.approve_writer(["single.py"])
        owner = self.owner()
        command, env = self.stage(owner, True)
        control = self.root / ".autocode" / ("tool-containment-" + "f" * 32)
        control.mkdir()
        (control / "scratch").mkdir()
        (control / "shell").write_text("controlled writer shell\n")
        (control / "policy.sb").write_text(
            containment.policy(self.root, control / "scratch", allow_write=True, write_paths=["single.py"])
        )
        boundary = {
            "version": 1,
            "workspace": str(self.root),
            "allow_write": True,
            "tools": ["bash"],
            "profile": str(control / "policy.sb"),
            "shell": str(control / "shell"),
            "scratch": str(control / "scratch"),
            "write_paths": ["single.py"],
            "writer_path_kinds": {"single.py": "file"},
        }
        final = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        final["shell"] = boundary["shell"]
        final["agent"]["autocode_terra"]["permission"] = containment.shell_permissions(self.actual["permission"])
        env.update(OPENCODE_CONFIG_CONTENT=json.dumps(final), SHELL=boundary["shell"])
        proof = control / "conformance.json"
        proof.write_text(
            json.dumps(
                {
                    "test_only_controlled_native_response": True,
                    "launch_identity": native.observe(command, env, self.executable, self.actual),
                }
            )
        )
        proof.chmod(0o400)
        boundary.update(conformance=str(proof), conformance_sha256=util.file_hash(proof))
        env["AUTOCODE_TOOL_CONTAINMENT"] = json.dumps(boundary)
        # Controlled owner/byte fixture, not native kernel or assignment proof.
        with patch.object(containment, "verify", side_effect=lambda value: value):
            descriptor = owner.finalize(command=command, environment=env, boundary=boundary)
        boundary["native_role_isolation"] = descriptor
        env["AUTOCODE_TOOL_CONTAINMENT"] = json.dumps(boundary)
        env["AUTOCODE_NATIVE_ROLE_ISOLATION"] = json.dumps(descriptor)
        with self.assertRaisesRegex(ValueError, "causal writer admission is not wired"):
            owner.verify_launch(state=self.state, command=command, environment=env, boundary=boundary)
        self.assertEqual("NOT_AUTHENTICATED", descriptor["writer_restriction"]["causal_assignment"])
