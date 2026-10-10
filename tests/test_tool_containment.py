"""Pure policy checks and real macOS kernel/tool boundary conformance."""

import hashlib
import json
import os
import shlex
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_tool_containment as containment
from providers import opencode

ROOT = Path(__file__).resolve().parents[1]


class PolicyTests(unittest.TestCase):
    def test_deny_default_has_no_whole_home_tmp_or_network_grant(self):
        profile = containment.policy("/workspace", "/workspace/.autocode/stage/scratch")
        self.assertIn("(deny default)", profile)
        self.assertNotIn("(allow default)", profile)
        self.assertNotIn('(subpath "/tmp")', profile)
        self.assertNotIn("(allow network", profile)
        self.assertNotIn('(subpath "/Users")', profile)
        self.assertIn('(literal "/")', profile)

    def test_builder_excludes_runner_and_config_authority(self):
        profile = containment.policy(
            "/workspace", "/workspace/.autocode/stage/scratch", allow_write=True, protected_paths=["/workspace/runtime"]
        )
        for path in (".autocode", ".git", ".opencode", "opencode.json", "runtime"):
            self.assertIn('(require-not (subpath "/workspace/' + path + '"))', profile)

    def test_unscoped_read_or_write_authority_rejected(self):
        for scratch in ("/tmp/escape", "/workspace/.autocode"):
            with self.assertRaises(ValueError):
                containment.policy("/workspace", scratch)
        for root in ("/", str(Path.home())):
            with self.assertRaises(ValueError):
                containment.policy("/workspace", "/workspace/.autocode/stage/scratch", read_roots=[root])

    def test_unsupported_platform_is_explicit_prelaunch_failure(self):
        with patch.object(containment.sys, "platform", "linux"):
            with self.assertRaisesRegex(RuntimeError, "requires macOS"):
                containment.prepare("/workspace")

    def test_effective_shell_patterns_and_stricter_actions_are_preserved(self):
        rules = [
            {"permission": "*", "pattern": "*", "action": "allow"},
            {"permission": "bash", "pattern": "git *", "action": "ask"},
            {"permission": "bash", "pattern": "git push*", "action": "deny"},
            {"permission": "read", "pattern": "*", "action": "deny"},
        ]
        result = containment.shell_permissions(rules)
        self.assertEqual({"*": "allow", "git *": "ask", "git push*": "deny"}, result["bash"])
        self.assertEqual("deny", result["*"])
        self.assertEqual("deny", result["external_directory"])
        rules += [{"permission": "*", "pattern": "*", "action": "deny"}]
        self.assertEqual(["git *", "git push*", "*"], list(containment.shell_permissions(rules)["bash"]))
        for unsupported in (None, [], [{"permission": "bash", "pattern": "*", "action": "auto"}]):
            with self.assertRaises(RuntimeError):
                containment.shell_permissions(unsupported)

    def test_adapter_opt_in_retains_model_effort_and_command(self):
        with patch.object(opencode.tool_containment, "configure") as configure:
            configure.side_effect = lambda command, env, workspace, **kw: (
                {**env, "AUTOCODE_TOOL_CONTAINMENT": '{"qualified":true}'},
                {"shell": "/owned/shell"},
            )
            command, env, overrides = opencode.launch(
                "sol",
                "/workspace",
                "/run",
                None,
                "test/model",
                "high",
                False,
                env={"PATH": "/bin"},
                containment={"read_roots": ["/runtime"]},
            )
        self.assertEqual("test/model", command[command.index("--model") + 1])
        self.assertEqual("high", command[command.index("--variant") + 1])
        self.assertNotIn("--auto", command)
        self.assertEqual("/owned/shell", overrides["shell"])
        self.assertEqual({"read_roots": ["/runtime"]}, configure.call_args.kwargs["request"])
        self.assertFalse(configure.call_args.kwargs["allow_write"])
        self.assertIn("AUTOCODE_TOOL_CONTAINMENT", env)

    def test_loopback_requests_fail_before_files_or_native_debug_are_created(self):
        authority = {"purpose": "approved-readonly-unittest", "binding_sha256": "a" * 64}
        command = shlex.join([sys.executable, "-m", "unittest", "-v", "test_http"])
        with (
            patch.object(containment.sys, "platform", "darwin"),
            patch.object(containment.subprocess, "run") as launch,
            patch.object(Path, "mkdir") as mkdir,
        ):
            for writable in (False, True):
                for request in (
                    {"loopback_checks": [command]},
                    {"loopback_authority": authority},
                    {"loopback_authority": {}},
                    {"loopback_checks": [command], "loopback_authority": authority},
                ):
                    with self.subTest(writable=writable, request=request):
                        with self.assertRaisesRegex(RuntimeError, "Exact-IP loopback containment is unsupported"):
                            containment.prepare("/workspace", allow_write=writable, **request)
                        with self.assertRaisesRegex(RuntimeError, "runner-mediated approved check"):
                            containment.configure(["opencode"], {}, "/workspace", allow_write=writable, request=request)
            launch.assert_not_called()
            mkdir.assert_not_called()
        containment._reject_loopback_request([], None)
        with self.assertRaises(ValueError):
            containment._reject_loopback_request(command, None)

    def test_recorded_scratch_is_only_the_layout_prepare_creates_in_this_workspace(self):
        # A contained stage captures evidence in its scratch; the launch record says whose it is (#419).
        private = Path("/workspace/.autocode")
        control = "tool-containment-" + "a" * 32
        genuine = private / control / "scratch"
        lookalikes = [
            private / ("tool-containment-" + "A" * 32) / "scratch",
            private / ("tool-containment-" + "a" * 31) / "scratch",
            private / ("tool-containment-" + "a" * 32 + "0") / "scratch",
            private / ("tool-containment-" + "a" * 32 + "/../" + control) / "scratch",
            private / control,
            private / control / "scratch-copy",
            private / control / "scratch" / "nested",
            private / "runs" / "other" / control / "scratch",
            Path("/elsewhere/.autocode") / control / "scratch",
            Path("workspace/.autocode") / control / "scratch",
        ]
        records = [
            {"stage": "terra"},
            {"stage": "sol", "tool_containment": {"scratch": str(genuine)}},
            {"stage": "sol", "tool_containment": {"scratch": str(genuine)}},
            *({"stage": "sol", "tool_containment": {"scratch": str(path)}} for path in lookalikes),
            {"tool_containment": None},
            {"tool_containment": {"scratch": 7}},
            {"tool_containment": "x"},
            "x",
        ]
        self.assertEqual((genuine,), containment.recorded_scratch(records, "/workspace"))
        self.assertEqual((), containment.recorded_scratch(records, "/other"))


class KernelReceiptTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = Path(directory.name).resolve()
        self.control = self.workspace / ".autocode" / ("tool-containment-" + "a" * 32)
        self.scratch = self.control / "scratch"
        self.scratch.mkdir(parents=True)
        for name in ("shell", "policy.sb"):
            (self.control / name).write_text("runner-owned fixture\n")
        self.boundary = {
            "profile": str(self.control / "policy.sb"),
            "shell": str(self.control / "shell"),
            "scratch": str(self.scratch),
        }
        self.command = ["opencode", "run", "--agent", "autocode_designer", "--model", "test/model"]
        self.environment = {
            "PATH": "/bin",
            "OPENCODE_CONFIG_CONTENT": json.dumps({"agent": {"autocode_designer": {"permission": {"bash": "allow"}}}}),
        }
        self.agent = {
            "permission": [{"permission": "bash", "pattern": "*", "action": "allow"}],
            "tools": {"bash": True, "read": False},
        }

    def configure(self, change=lambda response: response, *, scratch=True, sentinel=True):
        import autocode_toolchain

        def native(argv, **kwargs):
            if argv[-1] == "--version":
                return SimpleNamespace(returncode=0, stdout=containment.SUPPORTED_VERSION, stderr="")
            response = self.agent
            if "--params" in argv:
                params = json.loads(argv[-1])
                response = {"input": params, "result": {"metadata": {"exit": 0}, "output": "Operation not permitted"}}
                if scratch:
                    (self.scratch / "conformance-capture").touch()
                if not sentinel:
                    (self.control / "conformance-forbidden").write_text("changed\n")
                response = change(response)
            return SimpleNamespace(returncode=0, stdout=json.dumps(response), stderr="")

        with (
            patch.object(containment.sys, "platform", "darwin"),
            patch.object(containment.shutil, "which", return_value="/owned/opencode"),
            patch.object(containment.subprocess, "run", side_effect=native) as run,
            patch.object(containment, "prepare", return_value=self.boundary),
            patch.object(autocode_toolchain, "discover", return_value={"read_roots": [], "probes": []}),
            patch.object(containment, "verify") as verify,
        ):
            try:
                return containment.configure(
                    self.command, self.environment, self.workspace, allow_write=False, request={}
                )
            finally:
                self.native_calls = run.call_args_list
                self.verification_calls = verify.call_count

    def failure(self, predicate, change=lambda response: response, **kwargs):
        with self.assertRaisesRegex(RuntimeError, predicate):
            self.configure(change, **kwargs)
        self.assertEqual(0, self.verification_calls)
        self.assertFalse((self.control / "conformance.json").exists())
        receipt = json.loads((self.control / "conformance-failure.json").read_text())
        self.assertEqual("rejected", receipt["status"])
        self.assertIn(predicate, receipt["failed_predicates"])
        self.assertFalse(receipt["checks"][predicate])
        self.assertEqual(0o400, (self.control / "conformance-failure.json").stat().st_mode & 0o777)
        self.assertLess(len(json.dumps(receipt)), 3000)
        return receipt

    def test_success_retains_only_real_proof_and_original_limits(self):
        self.configure()
        self.assertEqual(1, self.verification_calls)
        self.assertTrue((self.control / "conformance.json").is_file())
        self.assertFalse((self.control / "conformance-failure.json").exists())
        self.assertEqual([15, 60, 60, 60], [call.kwargs["timeout"] for call in self.native_calls])
        self.assertEqual(10000, json.loads(self.native_calls[-1].args[0][-1])["timeout"])

    def test_each_kernel_predicate_is_required_even_when_other_checks_pass(self):
        cases = [
            ("input_command_match", lambda r: {**r, "input": {"command": "different"}}, {}),
            ("metadata_exit_int", lambda r: {**r, "result": {**r["result"], "metadata": {"exit": False}}}, {}),
            ("exit_zero", lambda r: {**r, "result": {**r["result"], "metadata": {"exit": 7}}}, {}),
            ("scratch_created", lambda r: r, {"scratch": False}),
            ("sentinel_untouched", lambda r: r, {"sentinel": False}),
        ]
        for predicate, change, kwargs in cases:
            with self.subTest(predicate=predicate):
                self.setUp()
                self.failure(predicate, change, **kwargs)

    def test_malformed_native_objects_fail_closed_without_attribute_or_type_errors(self):
        cases = [
            ("response_object", lambda r: []),
            ("input_object", lambda r: {**r, "input": "not an object"}),
            ("result_object", lambda r: {**r, "result": None}),
            ("metadata_object", lambda r: {**r, "result": {**r["result"], "metadata": []}}),
        ]
        for predicate, change in cases:
            with self.subTest(predicate=predicate):
                self.setUp()
                self.failure(predicate, change)

    def test_missing_or_malformed_display_output_does_not_replace_direct_evidence(self):
        for output, kind, omit in (
            ("", "string", False),
            (None, "null", False),
            ({"secret": "SECRET"}, "object", False),
            (None, "null", True),
        ):
            with self.subTest(kind=kind, omit=omit):
                self.setUp()
                self.configure(
                    lambda r: {
                        **r,
                        "result": {
                            **{key: value for key, value in r["result"].items() if key != "output"},
                            **({} if omit else {"output": output}),
                        },
                    }
                )
                self.assertEqual(1, self.verification_calls)
                self.assertFalse((self.control / "conformance-failure.json").exists())
                proof_text = (self.control / "conformance.json").read_text()
                self.assertNotIn("SECRET", proof_text)
                receipt = json.loads(proof_text)["native_receipt"]
                self.assertEqual("passed", receipt["status"])
                self.assertEqual([], receipt["failed_predicates"])
                self.assertFalse(receipt["checks"]["denial_present"])
                self.assertEqual(kind == "string", receipt["checks"]["output_string"])
                self.assertEqual(kind, receipt["types"]["output"])

    def test_adversarial_text_and_extra_keys_never_enter_receipt_or_exception(self):
        secret = "SECRET-https://user:password@private.invalid/token"

        def malicious(response):
            return {
                "input": {"command": secret + "\ud800"},
                "result": {"metadata": {"exit": 2**100, "credentials": secret}, "output": secret},
                secret: secret,
            }

        with self.assertRaises(RuntimeError) as caught:
            self.configure(malicious)
        receipt_text = (self.control / "conformance-failure.json").read_text()
        self.assertNotIn(secret, receipt_text + str(caught.exception))
        receipt = json.loads(receipt_text)
        self.assertIsNone(receipt["exit"])
        self.assertEqual("integer", receipt["types"]["exit"])
        self.assertEqual(64, len(receipt["observed_command_sha256"]))

    def test_rejected_command_hashing_has_a_fixed_allocation_bound(self):
        original = containment.hashlib.sha256

        def bounded_hash(data):
            self.assertLessEqual(len(data), 4 * 16384)
            return original(data)

        for length in (16384, 16385):
            with self.subTest(length=length):
                self.setUp()
                command = "\U0001f642" * length
                with patch.object(containment.hashlib, "sha256", side_effect=bounded_hash):
                    receipt = self.failure("input_command_match", lambda r: {**r, "input": {"command": command}})
                if length == 16384:
                    self.assertEqual(64, len(receipt["observed_command_sha256"]))
                else:
                    self.assertIsNone(receipt["observed_command_sha256"])

    def test_unreadable_sentinel_is_a_rejected_predicate_not_raw_io_error(self):
        original = Path.read_text

        def read(path, *args, **kwargs):
            if path.name == "conformance-forbidden":
                raise OSError("SECRET credential-bearing filesystem error")
            return original(path, *args, **kwargs)

        with patch.object(Path, "read_text", read):
            receipt = self.failure("sentinel_untouched")
        self.assertTrue(receipt["sentinel_io_error"])

    def test_diagnostic_write_failure_still_rejects_without_raw_error(self):
        original = os.open

        def open_file(path, *args, **kwargs):
            if Path(path).name == "conformance-failure.json":
                raise OSError("SECRET credentials")
            return original(path, *args, **kwargs)

        with patch.object(containment.os, "open", side_effect=open_file):
            with self.assertRaisesRegex(RuntimeError, "scratch_created; diagnostic unavailable") as caught:
                self.configure(scratch=False)
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertFalse((self.control / "conformance.json").exists())
        self.assertEqual(0, self.verification_calls)

    def test_unreadable_scratch_is_a_rejected_predicate(self):
        original = Path.is_file

        def is_file(path):
            if path.name == "conformance-capture":
                raise OSError("SECRET filesystem error")
            return original(path)

        with patch.object(Path, "is_file", is_file):
            receipt = self.failure("scratch_created")
        self.assertTrue(receipt["scratch_io_error"])

    def test_missing_fields_and_string_exit_are_not_success(self):
        for change in (
            lambda r: {},
            lambda r: {"input": {}, "result": {}},
            lambda r: {**r, "result": {**r["result"], "metadata": {"exit": "0"}}},
        ):
            with self.subTest(change=change):
                self.setUp()
                self.failure("metadata_exit_int", change)

    def test_existing_diagnostic_symlink_is_never_followed(self):
        outside = self.workspace / "unrelated"
        outside.write_text("untouched\n")
        (self.control / "conformance-failure.json").symlink_to(outside)
        with self.assertRaisesRegex(RuntimeError, "diagnostic unavailable"):
            self.configure(scratch=False)
        self.assertEqual("untouched\n", outside.read_text())
        self.assertFalse((self.control / "conformance.json").exists())


class AvailabilityTests(unittest.TestCase):
    """The cheap run-setup check (#413): platform, sandbox-exec, OpenCode version; no conformance."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.bin = Path(directory.name).resolve()
        # Simulated macOS anywhere (CI is Linux): any existing file stands in for sandbox-exec.
        self.mac = SimpleNamespace(platform="darwin")
        self.sandbox = patch.object(containment, "SANDBOX_EXEC", sys.executable)

    def opencode(self, version, code=0):
        client = self.bin / "opencode"
        client.write_text(f"#!/bin/sh\necho {version}\nexit {code}\n")
        client.chmod(0o755)
        return {"PATH": str(self.bin)}

    def test_other_platform_is_named(self):
        with patch.object(containment, "sys", SimpleNamespace(platform="linux")):
            reason = containment.unavailable(environment=self.opencode(containment.SUPPORTED_VERSION))
        self.assertIn("requires macOS sandbox-exec", reason)
        self.assertIn("linux", reason)

    def test_missing_sandbox_exec_is_named(self):
        with (
            patch.object(containment, "sys", self.mac),
            patch.object(containment, "SANDBOX_EXEC", str(self.bin / "no-sandbox-exec")),
        ):
            self.assertIn("sandbox-exec, which is missing", containment.unavailable(environment=self.opencode("x")))

    def test_other_or_unreadable_opencode_version_is_named(self):
        with patch.object(containment, "sys", self.mac), self.sandbox:
            reason = containment.unavailable(environment=self.opencode("1.18.34"))
            self.assertIn("only for OpenCode " + containment.SUPPORTED_VERSION, reason)
            self.assertIn("this machine has OpenCode 1.18.34", reason)
            failed = containment.unavailable(environment=self.opencode(containment.SUPPORTED_VERSION, code=3))
            self.assertIn("unknown (exit 3)", failed)
            self.assertIn(
                "cannot find the opencode executable",
                containment.unavailable(environment={"PATH": str(self.bin / "empty")}),
            )

    def test_qualified_setup_is_available_without_running_conformance(self):
        with (
            patch.object(containment, "sys", self.mac),
            self.sandbox,
            patch.object(containment, "configure") as configure,
            patch.object(containment, "prepare") as prepare,
        ):
            self.assertIsNone(containment.unavailable(self.bin, self.opencode(containment.SUPPORTED_VERSION)))
        configure.assert_not_called()
        prepare.assert_not_called()


@unittest.skipUnless(
    sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file(), "requires real macOS Seatbelt enforcement"
)
class KernelTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / ".autocode"
        parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="containment-tests-", dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.workspace = self.home / "project"
        self.workspace.mkdir()
        self.source = self.workspace / "source.py"
        self.source.write_text("VALUE = 42\n")
        self.outside = self.home / "forbidden.txt"
        self.outside.write_text("forbidden sentinel\n")
        self.runtime = self.workspace / "runtime"
        self.runtime.mkdir()
        self.code = self.runtime / "runner.py"
        self.code.write_text("runner authority\n")
        private = self.workspace / ".autocode"
        private.mkdir()
        self.state = private / "state.json"
        self.state.write_text('{"owned":"runner"}\n')
        self.events = private / "live.jsonl"
        self.events.write_text('{"type":"step_start"}\n')
        self.receipt = private / "accepted.json"
        self.receipt.write_text('{"accepted":true}\n')
        self.roots = [Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve(), ROOT / "tools"]
        self.env = {**os.environ, "PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin:/opt/homebrew/bin"}
        self.before = {
            p: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (self.source, self.outside, self.code, self.state, self.events, self.receipt)
        }
        self.boundary = containment.prepare(
            self.workspace, read_roots=self.roots, protected_paths=[self.runtime], environment=self.env
        )

    def run_shell(self, command, boundary=None):
        result = subprocess.run(
            [(boundary or self.boundary)["shell"], "-c", command],
            cwd=self.workspace,
            capture_output=True,
            text=True,
            timeout=20,
        )
        return result

    def assert_denied(self, command, boundary=None):
        result = self.run_shell(command, boundary)
        self.assertNotEqual(0, result.returncode, (command, result.stdout, result.stderr))
        self.assertRegex(result.stderr, "Operation not permitted|PermissionError|Permission denied")

    def test_external_and_readonly_writes_are_kernel_denied(self):
        escape = self.home / "outside-created"
        commands = [
            shlex.join(["mkdir", str(escape)]),
            shlex.join(["cp", str(self.source), str(self.outside)]),
            "python -c "
            + shlex.quote("from pathlib import Path; Path(" + repr(str(self.outside)) + ').write_text("bad")'),
            ": > " + shlex.quote(str(self.outside)),
            "true && (: > " + shlex.quote(str(self.source)) + ")",
        ]
        for path in (self.state, self.events, self.receipt, self.code):
            commands.append(": > " + shlex.quote(str(path)))
        for command in commands:
            with self.subTest(command=command):
                self.assert_denied(command)
        self.assertFalse(escape.exists())
        self.assertEqual(self.before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.before})

    def test_symlink_escape_denied_even_from_writable_scratch(self):
        link = Path(self.boundary["scratch"]) / "escape"
        link.symlink_to(self.outside)
        self.assert_denied(": > " + shlex.quote(str(link)))
        self.assertEqual(self.before[self.outside], hashlib.sha256(self.outside.read_bytes()).hexdigest())

    def test_legitimate_reads_tests_and_stage_capture_work_without_client_secrets(self):
        test = self.workspace / "test_source.py"
        test.write_text(
            "import unittest\nfrom source import VALUE\n"
            "class SourceTest(unittest.TestCase):\n"
            "    def test_value(self): self.assertEqual(VALUE, 42)\n"
        )
        result = self.run_shell("python -m unittest -v test_source")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Ran 1 test", result.stderr)
        output = Path(self.boundary["scratch"]) / "capture.json"
        command = "python -c " + shlex.quote(
            "import json,os,pathlib,sys; "
            'assert pathlib.Path("source.py").read_text() == "VALUE = 42\\n"; '
            'assert "CONTAINMENT_TEST_SECRET" not in os.environ; '
            'assert os.environ.get("PYTHONNOUSERSITE") == "1" and sys.flags.no_user_site == 1; '
            "pathlib.Path(" + repr(str(output)) + ').write_text(json.dumps({"exit_code": 0}))'
        )
        with patch.dict(os.environ, {"CONTAINMENT_TEST_SECRET": "must-not-reach-tool"}):
            result = self.run_shell(command)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual({"exit_code": 0}, json.loads(output.read_text()))
        self.assertFalse((self.workspace / "__pycache__").exists())

    def test_public_capture_cli_uses_only_owned_stage_scratch(self):
        output = Path(self.boundary["scratch"]) / "approved-capture.json"
        command = shlex.join(
            [
                "python",
                "-m",
                "autocode_cli",
                "capture",
                "--output",
                str(output),
                "--",
                "python",
                "-c",
                'print("actual captured command")',
            ]
        )
        result = self.run_shell(command)
        self.assertEqual(0, result.returncode, result.stderr)
        receipt = json.loads(output.read_text())
        self.assertEqual(0, receipt["exit_code"])
        self.assertEqual("actual captured command\n", Path(receipt["full_output"]).read_text())
        self.assertTrue(Path(receipt["full_output"]).is_relative_to(self.boundary["scratch"]))

    def test_builder_may_edit_app_not_state_runtime_or_other_stage(self):
        writer = containment.prepare(
            self.workspace,
            allow_write=True,
            read_roots=self.roots,
            protected_paths=[self.runtime],
            environment=self.env,
        )
        result = self.run_shell(
            "python -c " + shlex.quote('from pathlib import Path; Path("source.py").write_text("VALUE = 43\\n")'),
            writer,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        for path in (
            self.state,
            self.events,
            self.receipt,
            self.code,
            Path(self.boundary["scratch"]) / "other-stage.json",
        ):
            with self.subTest(path=path):
                self.assert_denied(": > " + shlex.quote(str(path)), writer)

    def test_outside_read_and_network_are_denied(self):
        self.assert_denied(
            "python -c "
            + shlex.quote("from pathlib import Path; print(Path(" + repr(str(self.outside)) + ").read_text())")
        )
        self.assert_denied("python -c " + shlex.quote('import socket; socket.socket().connect(("127.0.0.1", 9))'))

    def test_hardlink_cannot_turn_runner_evidence_into_writable_scratch(self):
        link = Path(self.boundary["scratch"]) / "linked-state"
        command = shlex.join(["ln", str(self.state), str(link)]) + " && : > " + shlex.quote(str(link))
        self.assert_denied(command)
        self.assertEqual(self.before[self.state], hashlib.sha256(self.state.read_bytes()).hexdigest())

    def test_wrapper_ignores_poisoned_shell_startup_before_sandbox(self):
        marker = self.home / "startup-escaped"
        startup = self.home / "poison.sh"
        startup.write_text(": > " + shlex.quote(str(marker)) + "\n")
        env = {
            **os.environ,
            "BASH_ENV": str(startup),
            "ENV": str(startup),
            "SHELLOPTS": "xtrace",
            "PS4": "$(: > " + shlex.quote(str(marker)) + ")",
        }
        result = subprocess.run(
            [self.boundary["shell"], "-c", "exit 0"],
            cwd=self.workspace,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(marker.exists())

    def test_manifest_verification_rejects_changed_files_and_symlink_ancestors(self):
        control = Path(self.boundary["profile"]).parent
        proof = control / "conformance.json"
        proof.write_text("{}")  # Integrity-only fixture, not native qualification.
        boundary = {**self.boundary, "native_version": containment.SUPPORTED_VERSION, "conformance": str(proof)}
        for name in ("profile", "shell", "conformance"):
            boundary[name + "_sha256"] = hashlib.sha256(Path(boundary[name]).read_bytes()).hexdigest()
        containment.verify(boundary)
        for key, value in (
            ("loopback_checks", ["claimed network permission"]),
            ("loopback_checks", []),
            ("loopback_authority", None),
            ("loopback_profile", ""),
        ):
            with self.subTest(key=key, value=value):
                with self.assertRaisesRegex(RuntimeError, "authority changed"):
                    containment.verify({**boundary, key: value})
        proof.write_text('{"changed":true}')
        with self.assertRaisesRegex(RuntimeError, "authority changed"):
            containment.verify(boundary)
        proof.write_text("{}")
        old = control.with_name(control.name + "-moved")
        control.rename(old)
        control.symlink_to(old, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "authority changed"):
            containment.verify(boundary)


@unittest.skipUnless(
    sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file(), "requires real macOS Seatbelt enforcement"
)
class LoopbackLimitTests(unittest.TestCase):
    setUp = KernelTests.setUp
    run_shell = KernelTests.run_shell

    def test_default_policy_denies_ephemeral_http_listener(self):
        (self.workspace / "test_http.py").write_text(
            "import http.server, unittest\n"
            "class HTTP(unittest.TestCase):\n"
            " def test_listener(self):\n"
            '  with http.server.HTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler): pass\n'
        )
        result = self.run_shell(shlex.join([sys.executable, "-m", "unittest", "-v", "test_http"]))
        self.assertEqual(1, result.returncode)
        self.assertIn("PermissionError: [Errno 1] Operation not permitted", result.stderr)
        self.assertEqual(self.before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.before})

    def test_seatbelt_localhost_is_not_an_exact_loopback_ip_filter(self):
        import ipaddress

        import psutil

        addresses = [
            a.address
            for values in psutil.net_if_addrs().values()
            for a in values
            if a.family == socket.AF_INET and not ipaddress.ip_address(a.address).is_loopback
        ]
        if not addresses:
            self.skipTest("No owned non-loopback interface available to test Seatbelt address matching")
        with socket.socket() as listener:
            listener.bind((addresses[0], 0))
            listener.listen()
            listener.settimeout(2)
            code = (
                "import socket; s=socket.socket(); s.settimeout(1); s.connect("
                + repr(listener.getsockname())
                + '); print("connected-owned-nonloopback")'
            )
            denied = self.run_shell(shlex.join([sys.executable, "-c", code]))
            self.assertEqual(1, denied.returncode)
            self.assertIn("Operation not permitted", denied.stderr)
            profile = containment.policy(self.workspace, self.boundary["scratch"], read_roots=self.roots)
            # This is a rejected candidate profile, NOT a production capability.
            candidate = profile + '(allow network-outbound (remote tcp "localhost:*"))\n'
            result = subprocess.run(
                ["/usr/bin/sandbox-exec", "-p", candidate, sys.executable, "-B", "-c", code],
                cwd=self.workspace,
                env={"PYTHONNOUSERSITE": "1"},
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("connected-owned-nonloopback\n", result.stdout)
            accepted, _ = listener.accept()
            accepted.close()

    def test_literal_loopback_ip_is_not_supported_by_sbpl_network_filter(self):
        for host in ("127.0.0.1:*", "[::1]:*"):
            with self.subTest(host=host):
                profile = containment.policy(self.workspace, self.boundary["scratch"], read_roots=self.roots)
                profile += "(allow network-outbound (remote tcp " + json.dumps(host) + "))"
                result = subprocess.run(
                    ["/usr/bin/sandbox-exec", "-p", profile, "/usr/bin/true"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertNotEqual(0, result.returncode)
                self.assertIn("host must be * or localhost", result.stderr)


@unittest.skipUnless(
    os.environ.get("AUTOCODE_NATIVE_CONTAINMENT_TEST") == "1", "opt-in model-free OpenCode debug conformance"
)
class NativeToolTests(KernelTests):
    def setUp(self):
        super().setUp()
        from autocode_preflight_worker import execute

        self.execute = execute
        command, env, _ = opencode.launch(
            "investigate_stuck",
            self.workspace,
            self.workspace / ".autocode" / "run",
            None,
            "zai-coding-plan/glm-5.3",
            "high",
            False,
            env=self.env,
            containment={"read_roots": [str(p) for p in self.roots], "protected_paths": [str(self.runtime)]},
        )
        self.worker = {
            "command": command,
            "environment": env,
            "engine": "opencode",
            "configured": False,
            "model": "zai-coding-plan/glm-5.3",
        }
        self.boundary = json.loads(env["AUTOCODE_TOOL_CONTAINMENT"])
        self.counter = 0

    def test_final_boundary_in_one_native_call(self):
        """Small retained native gate, including shell/env startup hardening."""
        targets = [self.source, self.outside, self.state, self.events, self.receipt, self.code]
        escape = Path(self.boundary["scratch"]) / "escape-final"
        escape.symlink_to(self.outside)
        programs = [
            ["mkdir", str(self.home / "outside-final")],
            ["cp", str(self.source), str(self.outside)],
            *[
                ["python", "-c", "from pathlib import Path; Path(" + repr(str(p)) + ').write_text("bad")']
                for p in [*targets, escape]
            ],
            ["bash", "-c", "true && (: > " + shlex.quote(str(self.outside)) + ")"],
        ]
        test = self.workspace / "test_source.py"
        test.write_text(
            "import unittest\nfrom source import VALUE\n"
            "class SourceTest(unittest.TestCase):\n"
            "    def test_value(self): self.assertEqual(VALUE, 42)\n"
        )
        capture = Path(self.boundary["scratch"]) / "final-capture.json"
        code = (
            "import subprocess,json,pathlib; results=[]\n"
            "for argv in " + repr(programs) + ":\n"
            " r=subprocess.run(argv,capture_output=True,text=True)\n"
            ' assert r.returncode != 0 and "Operation not permitted" in r.stderr, (argv,r)\n'
            ' results.append({"argv":argv,"exit":r.returncode,"stderr":r.stderr})\n'
            'subprocess.run(["python","-m","unittest","-v","test_source"],check=True)\n'
            "subprocess.run("
            + repr(
                [
                    "python",
                    "-m",
                    "autocode_cli",
                    "capture",
                    "--output",
                    str(capture),
                    "--",
                    "python",
                    "-c",
                    'print("approved final capture")',
                ]
            )
            + ",check=True)\n"
            "print(json.dumps(results))\n"
        )
        result = self.run_shell("python -c " + shlex.quote(code))
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertEqual(self.before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.before})
        self.assertEqual(0, json.loads(capture.read_text())["exit_code"])
        if os.environ.get("AUTOCODE_CONTAINMENT_RETAIN_PROOF") == "1":
            destination = (
                ROOT / ".autocode" / ("native-containment-proof-" + Path(self.boundary["profile"]).parent.name)
            )
            destination.mkdir()
            # Copy only this runner-owned fixture's proof, never auth/config data.
            import shutil

            shutil.copytree(self.home, destination / "fixture")
            (destination / "result.json").write_text(
                json.dumps(
                    {
                        "native_version": containment.SUPPORTED_VERSION,
                        "passed": True,
                        "command": result.args,
                        "output": result.stdout,
                        "source_and_forbidden_hashes_unchanged": True,
                        "before": {str(p): value for p, value in self.before.items()},
                        "runtime_sha256": hashlib.sha256(Path(containment.__file__).read_bytes()).hexdigest(),
                    },
                    indent=2,
                )
            )
            print("RETAINED_NATIVE_PROOF=" + str(destination))

    def run_shell(self, command, boundary=None):
        if boundary is not None:
            # Builder's kernel policy has separate direct-process coverage.
            return super().run_shell(command, boundary)
        self.counter += 1
        output = self.home / ("native-" + str(self.counter) + ".json")
        argv = ["bash", "-c", command]
        result, text = self.execute(self.worker, argv, self.workspace, output, timeout=60)
        raw = json.loads(output.read_text())
        self.assertEqual(shlex.join(argv), raw["input"]["command"])
        self.assertIs(type(raw["result"]["metadata"]["exit"]), int)
        print(
            json.dumps(
                {"native_command": shlex.join(argv), "exit": result["exit_code"], "output": text}, sort_keys=True
            )
        )
        return subprocess.CompletedProcess(argv, result["exit_code"], text, text)


if __name__ == "__main__":
    unittest.main()
