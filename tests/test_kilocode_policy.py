"""Kilo permission intersection and opt-in native tool execution, with no model calls."""

import json
import os
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import autocode_output_cap as output_cap
import autocode_provider_launch as launch
from providers import kilocode
from providers import kilocode_policy as policy

from tests import test_kilo_progress as progress_tests


class KiloPolicyTests(unittest.TestCase):
    def test_the_qualified_kilo_release_is_the_only_one_a_linux_launch_accepts(self):
        for version in ("7.8.3", "7.8.3-issue1063-source"):
            with self.subTest(version=version):
                kilocode.check_version(version, platform="linux")
        for version in ("7.8.12", "7.8.2", "kilo 7.8.3", "", None):
            with (
                self.subTest(version=version),
                self.assertRaisesRegex(RuntimeError, "no provider request was launched"),
            ):
                kilocode.check_version(version, platform="linux")

    def test_macos_keeps_any_kilo_because_its_checkers_use_kilos_own_shell(self):
        kilocode.check_version("7.8.12", platform="darwin")

    def test_a_launch_settings_read_refuses_an_unqualified_kilo_on_linux(self):
        provider = kilocode.load("kilocode")
        with (
            patch.object(kilocode.command.CommandProvider, "local_settings", return_value={"version": "7.8.12"}),
            patch.object(kilocode.sys, "platform", "linux"),
            self.assertRaisesRegex(RuntimeError, "requires Kilo 7.8.3 \\(found 7.8.12\\)"),
        ):
            provider.local_settings()

    def test_intersection_preserves_ordered_operator_denies_and_blocks_unknown_tools(self):
        rules = [
            {"permission": "*", "pattern": "*", "action": "allow"},
            {"permission": "r*", "pattern": "secret*", "action": "deny"},
            {"permission": "read", "pattern": "ask*", "action": "ask"},
            {"permission": "bash", "pattern": "*", "action": "deny"},
        ]
        actual = policy.intersect(rules, policy.READ_TOOLS)
        self.assertEqual("deny", actual["*"])
        self.assertEqual({"*": "allow", "secret*": "deny", "ask*": "deny"}, actual["read"])
        self.assertEqual("deny", actual["external_directory"])
        self.assertEqual({"*": "deny"}, policy.intersect(rules, ("bash",))["bash"])
        self.assertNotIn("background_process", actual)

    def test_write_stage_keeps_operator_restrictions_and_cannot_delegate(self):
        rules = [
            {"permission": "*", "pattern": "*", "action": "allow"},
            {"permission": "edit", "pattern": "protected*", "action": "deny"},
            {"permission": "bash", "pattern": "danger*", "action": "ask"},
            {"permission": "task", "pattern": "*", "action": "allow"},
            {"permission": "websearch", "pattern": "private*", "action": "deny"},
        ]
        child = {"PATH": os.environ["PATH"], "KILO_CONFIG_CONTENT": "{}"}
        argv = [sys.executable, "run", "--agent", "code", "--session", "old", "--model", "fixture/model"]
        original = {"permission": rules, "prompt": "Preserve the operator coding prompt"}
        with patch.object(
            policy.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(original), "")
        ):
            ran, env = policy.restrict(argv, child, Path.cwd(), role="sol", write_stage=True)
        agent = json.loads(env["KILO_CONFIG_CONTENT"])["agent"][ran[-1]]
        self.assertEqual(original["prompt"], agent["prompt"])
        self.assertEqual("fixture/model", agent["model"])
        self.assertEqual({"*": "allow", "protected*": "deny"}, agent["permission"]["edit"])
        self.assertEqual({"*": "allow", "danger*": "deny"}, agent["permission"]["bash"])
        self.assertEqual("deny", agent["permission"]["*"])
        for tool in ("task", "question", "plan_enter", "plan_exit", "unknown_mcp"):
            self.assertNotIn(tool, agent["permission"])
        self.assertNotIn("--session", ran)
        self.assertEqual("deny", agent["permission"]["external_directory"])
        self.assertEqual("allow", agent["permission"]["todowrite"])
        self.assertEqual("allow", agent["permission"]["webfetch"])
        self.assertEqual("deny", agent["permission"]["websearch"], "unsupported patterned action stays denied")
        with self.assertRaisesRegex(ValueError, "read-only"):
            policy.restrict(argv, child, Path.cwd(), role="sol", write_stage=True, request={})

    def test_native_permission_wildcards_do_not_treat_brackets_as_character_classes(self):
        rules = [
            {"permission": "*", "pattern": "*", "action": "deny"},
            {"permission": "r[ea]ad", "pattern": "*", "action": "allow"},
        ]
        self.assertEqual({"*": "deny"}, policy.intersect(rules, ("read",))["read"])

    def test_remote_or_inherited_launch_cannot_reuse_foreign_tool_grants(self):
        for flag in ("--attach", "--attach=http://localhost:123", "--continue", "-c"):
            with self.subTest(flag=flag), self.assertRaisesRegex(ValueError, "fresh local"):
                policy.restrict(["kilo", "run", flag], {}, Path("/unused"), role="glm")

    def test_a_macos_checker_keeps_read_tools_and_kilos_shell_but_never_edits(self):
        # #1766 (owner decision 2026-10-21): no kernel shell on macOS, so Kilo's own policy, edits denied.
        rules = [{"permission": "*", "pattern": "*", "action": "allow"}]

        def debug(argv, **_kwargs):
            answer = {"default_agent": "code"} if argv[2:] == ["config"] else {"permission": rules}
            return subprocess.CompletedProcess(argv, 0, json.dumps(answer), "")

        child = {"PATH": os.environ["PATH"], "KILO_CONFIG_CONTENT": "{}"}
        argv = [sys.executable, "run", "--model", "zai-coding-plan/glm-5.3"]
        with patch.object(policy.subprocess, "run", side_effect=debug):
            ran, env = policy.restrict(argv, child, Path.cwd(), role="sol", portable_shell=True)
        (agent,) = json.loads(env["KILO_CONFIG_CONTENT"])["agent"].values()
        allowed = {tool for tool, rule in agent["permission"].items() if rule == {"*": "allow"}}
        self.assertEqual({*policy.READ_TOOLS, "bash"}, allowed)
        self.assertEqual("deny", agent["permission"]["*"])
        self.assertNotIn("edit", allowed)
        with self.assertRaisesRegex(ValueError, "cannot accompany"):
            policy.restrict(argv, child, Path.cwd(), role="sol", request={}, portable_shell=True)

    def test_malformed_or_absent_policy_refuses_before_launch(self):
        for rules in (None, [], [{"permission": "read"}], [{"permission": "*", "pattern": "*", "action": "maybe"}]):
            with self.subTest(rules=rules), self.assertRaises(ValueError):
                policy.intersect(rules, policy.READ_TOOLS)

    def test_restriction_preserves_effective_custom_prompt_without_copying_tool_grants(self):
        rules = [{"permission": "*", "pattern": "*", "action": "allow"}]
        original = {
            "permission": rules,
            "prompt": "Review only the supplied plan. Return the requested report.",
            "tools": {"edit": True, "task": True},
        }
        child = {"PATH": os.environ["PATH"], "KILO_CONFIG_CONTENT": "{}"}
        argv = [sys.executable, "run", "--agent", "selected", "--session", "old-session", "--model", "fixture/selected"]
        with patch.object(
            policy.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(original), "")
        ):
            ran, env = policy.restrict(argv, dict(child), Path.cwd(), role="plan_reviewer")
        name = ran[ran.index("--agent") + 1]
        agent = json.loads(env["KILO_CONFIG_CONTENT"])["agent"][name]
        self.assertEqual(original["prompt"], agent["prompt"])
        self.assertEqual("fixture/selected", agent["model"])
        self.assertEqual("deny", agent["permission"]["*"])
        self.assertNotIn("edit", agent["permission"])
        self.assertNotIn("tools", agent)
        self.assertNotIn("--session", ran)
        self.assertEqual("{}", child["KILO_CONFIG_CONTENT"])

    def test_effective_prompt_preserves_empty_and_rejects_non_strings(self):
        argv = [sys.executable, "run", "--agent", "selected", "--model", "fixture/selected"]
        for prompt in ("", None, {"text": "invalid"}, ["invalid"], 42):
            original = {"permission": [{"permission": "*", "pattern": "*", "action": "allow"}], "prompt": prompt}
            child = {"PATH": os.environ["PATH"], "KILO_CONFIG_CONTENT": "{}"}
            with (
                self.subTest(prompt=prompt),
                patch.object(
                    policy.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(original), "")
                ),
            ):
                if prompt is not None and not isinstance(prompt, str):
                    with self.assertRaisesRegex(ValueError, "prompt must be a string"):
                        policy.restrict(argv, child, Path.cwd(), role="plan_reviewer")
                    self.assertEqual("{}", child["KILO_CONFIG_CONTENT"])
                else:
                    ran, env = policy.restrict(argv, child, Path.cwd(), role="plan_reviewer")
                    agent = json.loads(env["KILO_CONFIG_CONTENT"])["agent"][ran[-1]]
                    if prompt is None:
                        self.assertNotIn("prompt", agent)
                    else:
                        self.assertEqual(prompt, agent["prompt"])

    def test_restriction_preserves_the_operators_native_steps_bound_for_every_stage_kind(self):
        argv = [sys.executable, "run", "--agent", "selected", "--model", "fixture/selected"]
        original = {
            "permission": [{"permission": "*", "pattern": "*", "action": "allow"}],
            "steps": 20,
            "model": "operator/default",
            "tools": {"task": True},
        }
        for options in ({"portable_shell": True}, {"write_stage": True}, {"report_recovery": True}):
            with (
                self.subTest(options=options),
                patch.object(
                    policy.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(original), "")
                ),
            ):
                child = {"PATH": os.environ["PATH"], "KILO_CONFIG_CONTENT": "{}"}
                ran, env = policy.restrict(argv, child, Path.cwd(), role="requirements", **options)
                agent = json.loads(env["KILO_CONFIG_CONTENT"])["agent"][ran[-1]]
                self.assertEqual(20, agent.get("steps"))
                self.assertEqual("fixture/selected", agent["model"])
                self.assertEqual("deny", agent["permission"]["*"])
                self.assertEqual("deny", agent["permission"]["external_directory"])
                self.assertNotIn("tools", agent)
                self.assertNotIn("task", agent["permission"])

    def test_invalid_native_steps_refuse_without_widening_the_config(self):
        argv = [sys.executable, "run", "--agent", "selected", "--model", "fixture/selected"]
        for steps in (True, 0, -1, 2.5, "20"):
            original = {"permission": [{"permission": "*", "pattern": "*", "action": "allow"}], "steps": steps}
            child = {"PATH": os.environ["PATH"], "KILO_CONFIG_CONTENT": "{}"}
            with (
                self.subTest(steps=steps),
                patch.object(
                    policy.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(original), "")
                ),
                self.assertRaisesRegex(ValueError, "steps must be a positive integer"),
            ):
                policy.restrict(argv, child, Path.cwd(), role="requirements")
            self.assertEqual("{}", child["KILO_CONFIG_CONTENT"])


@unittest.skipUnless(
    sys.platform == "linux" and os.environ.get("AUTOCODE_KILO_LOCAL_SSE_TEST") == "1",
    "Opt-in original Kilo 7.8.3, loopback-only network, bubblewrap; no paid models",
)
class NativeKiloPolicyTests(unittest.TestCase):
    setUp = progress_tests.RealKiloProgressTests.setUp

    def configured(self, permissions=None):
        config = {
            "enabled_providers": ["localfixture"],
            "model": "localfixture/check",
            "small_model": "localfixture/check",
            "permission": permissions or {"*": "allow"},
            "provider": {
                "localfixture": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "Offline fixture",
                    "options": {"baseURL": "http://127.0.0.1:9/v1", "apiKey": "unused-offline-fixture"},
                    "models": {
                        "check": {
                            "name": "Offline",
                            "variants": {"medium": {}},
                            "limit": {"context": 32000, "output": 1024},
                        }
                    },
                }
            },
        }
        return {**self.env, "KILO_CONFIG_CONTENT": json.dumps(config)}

    def debug(self, argv, child, tool, params):
        agent = argv[argv.index("--agent") + 1]
        return subprocess.run(
            [self.kilo, "debug", "agent", agent, "--tool", tool, "--params", json.dumps(params)],
            env=child,
            cwd=self.workspace,
            capture_output=True,
            text=True,
            timeout=40,
        )

    def test_native_requests_use_the_kilo_output_cap_and_operator_precedence(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                for delta, finish in (({"role": "assistant", "content": "Fixture complete"}, None), ({}, "stop")):
                    row = {
                        "id": "local-cap",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": "check",
                        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                    }
                    self.wfile.write(("data: " + json.dumps(row) + "\n\n").encode())
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        env = self.configured()
        config = json.loads(env["KILO_CONFIG_CONTENT"])
        config["small_model"] = "localfixture/check"
        config["provider"]["localfixture"]["options"]["baseURL"] = f"http://127.0.0.1:{server.server_port}/v1"
        config["provider"]["localfixture"]["models"]["check"]["limit"] = {"context": 262144, "output": 128000}
        env["KILO_CONFIG_CONTENT"] = json.dumps(config)
        for overrides, expected in (
            ({}, 64000),
            ({output_cap.VARIABLE: "48000"}, 48000),
            ({output_cap.VARIABLE: "48000", output_cap.KILO_VARIABLE: "56000"}, 56000),
            ({output_cap.KILO_VARIABLE: "invalid"}, 64000),
        ):
            with self.subTest(overrides=overrides):
                requests.clear()
                argv, child, _ = self.provider.launch(
                    "terra",
                    self.workspace,
                    self.root,
                    None,
                    "localfixture/check",
                    "medium",
                    True,
                    env={**env, **overrides},
                )
                result = subprocess.run(
                    argv,
                    input="Say fixture complete.",
                    env=child,
                    cwd=self.workspace,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertTrue(requests, result.stdout)
                self.assertTrue(all(row.get("max_tokens") == expected for row in requests), requests)
                for row in requests:
                    names = {tool["function"]["name"] for tool in row.get("tools", [])}
                    self.assertIn("bash", names)
                    self.assertTrue(names & {"edit", "apply_patch", "write"}, names)
                    self.assertFalse(names & {"task", "question", "plan_enter", "plan_exit"}, names)

    def test_report_recovery_removes_tools_from_actual_native_model_request(self):
        self.check_report_recovery_requests((False, True))

    def test_report_only_recovery_needs_no_kernel_shell_or_tool_conformance(self):
        with patch.object(policy.containment, "configure", side_effect=AssertionError("Report repair granted a shell")):
            self.check_report_recovery_requests((True,))

    def check_report_recovery_requests(self, recovering_states):
        requests = []
        report = '{"summary":"Evidence insufficient; return a concern","concerns":[]}'
        role_prompt = "OPERATOR_REVIEW_PROMPT_SENTINEL: review only; return the supplied report."

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                for delta, finish in (({"role": "assistant", "content": report}, None), ({}, "stop")):
                    row = {
                        "id": "local-review",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": "check",
                        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                    }
                    self.wfile.write(("data: " + json.dumps(row) + "\n\n").encode())
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        env = self.configured()
        config = json.loads(env["KILO_CONFIG_CONTENT"])
        config["provider"]["localfixture"]["options"]["baseURL"] = f"http://127.0.0.1:{server.server_port}/v1"
        config["default_agent"] = "code"
        config["agent"] = {"code": {"prompt": role_prompt}}
        env["KILO_CONFIG_CONTENT"] = json.dumps(config)
        run_dir = self.workspace / ".autocode" / "runs" / "prompt-policy"
        run_dir.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True, env=env)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.workspace),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.test",
                "commit",
                "-q",
                "--allow-empty",
                "-m",
                "Fixture baseline",
            ],
            check=True,
            env=env,
        )
        for recovering in recovering_states:
            with self.subTest(recovering=recovering), patch.dict(os.environ, env, clear=True):
                requests.clear()
                argv, child, _, worker = launch.prepare(
                    engine="opencode",
                    adapter=self.provider,
                    role="astra",
                    route_role="plan_reviewer",
                    workspace=self.workspace,
                    run_dir=run_dir,
                    session="old-session",
                    model="localfixture/check",
                    effort="medium",
                    allow_write=False,
                    planning=True,
                    report=None,
                    schema=None,
                    prompt_file=None,
                    sandbox="read-only",
                    transport_args=[],
                    chatgpt=False,
                    provider=None,
                    settings={},
                    planning_report_recovery=recovering,
                )
                result = subprocess.run(
                    argv,
                    input="Return the supplied JSON report.",
                    env=child,
                    cwd=self.workspace,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertTrue(requests, result.stdout)
                for row in requests:
                    system = [message["content"] for message in row["messages"] if message["role"] == "system"]
                    self.assertIn(
                        role_prompt, json.dumps(system), "Restriction must retain the selected agent's prompt"
                    )
                names = {tool["function"]["name"] for row in requests for tool in row.get("tools", [])}
                if recovering:
                    self.assertEqual(set(), names, "Recovery must not advertise exploratory tools")
                    self.assertIn("all tools disabled", launch.stage_record(worker)["isolation"])
                    attempted = self.debug(argv, child, "read", {"filePath": str(self.workspace / "fixture.txt")})
                    self.assertNotEqual(0, attempted.returncode, attempted.stdout)
                else:
                    # Every Planner has a shell (owner decision 2026-10-21): the kernel shell does its reading
                    # where it exists; on macOS Kilo's own shell sits beside the read tools (#1766).
                    self.assertIn("bash", names)
                    if worker.get("tool_containment"):
                        self.assertEqual({"bash"}, names)
                    else:
                        self.assertTrue({"read", "glob", "grep"}.issubset(names), names)
                self.assertNotIn("--session", argv)
                self.assertEqual("localfixture/check", worker["model"])
                self.assertIn(report.replace('"', '\\"'), result.stdout)

    def test_planning_blocks_real_write_shell_background_and_preserves_read_denial(self):
        for permissions, read_allowed in (({"*": "allow"}, True), ({"*": "allow", "read": "deny"}, False)):
            argv, child, _ = self.provider.launch(
                "glm",
                self.workspace,
                self.root,
                "old-session",
                "localfixture/check",
                "medium",
                False,
                planning=True,
                env=self.configured(permissions),
            )
            self.assertNotIn("--session", argv)
            read = self.debug(argv, child, "read", {"filePath": str(self.workspace / "fixture.txt")})
            self.assertEqual(0 if read_allowed else 1, read.returncode, read.stderr)
            for tool, params in (
                ("write", {"filePath": str(self.workspace / "forbidden.txt"), "content": "bad"}),
                ("bash", {"command": "touch forbidden.txt", "description": "attempted write"}),
                ("background_process", {"action": "start", "command": "touch forbidden.txt"}),
            ):
                attempted = self.debug(argv, child, tool, params)
                self.assertNotEqual(0, attempted.returncode, attempted.stdout)
                self.assertFalse((self.workspace / "forbidden.txt").exists())

    def test_readonly_tester_executes_checks_but_cannot_modify_source(self):
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run(["git", "-C", str(self.workspace), "add", "fixture.txt"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.workspace),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=f@example.test",
                "commit",
                "-qm",
                "fixture",
            ],
            check=True,
        )
        run = self.workspace / ".autocode/runs/check"
        run.mkdir(parents=True)
        with patch.dict(
            os.environ, self.configured({"*": "allow", "bash": {"*": "deny", "echo *": "allow"}}), clear=True
        ):
            with self.assertRaisesRegex(RuntimeError, "Native tool permissions deny") as refused:
                launch.prepare(
                    engine="opencode",
                    adapter=self.provider,
                    role="sol",
                    route_role="sol",
                    workspace=self.workspace,
                    run_dir=run,
                    session=None,
                    model="localfixture/check",
                    effort="medium",
                    allow_write=False,
                    planning=False,
                    report=None,
                    schema=None,
                    prompt_file=None,
                    sandbox="workspace-write",
                    transport_args=[],
                    chatgpt=False,
                    provider=None,
                    source_paths=["fixture.txt"],
                )
            self.assertNotIn("Restore qualified", str(refused.exception))
            self.assertNotIn("allow-uncontained-tools", str(refused.exception))
        with patch.dict(os.environ, self.configured(), clear=True):
            argv, child, _, worker = launch.prepare(
                engine="opencode",
                adapter=self.provider,
                role="sol",
                route_role="sol",
                workspace=self.workspace,
                run_dir=run,
                session=None,
                model="localfixture/check",
                effort="medium",
                allow_write=False,
                planning=False,
                report=None,
                schema=None,
                prompt_file=None,
                sandbox="workspace-write",
                transport_args=[],
                chatgpt=False,
                provider=None,
                source_paths=["fixture.txt"],
            )
        launch.verify_containment(worker)
        scratch = Path(worker["tool_containment"]["scratch"])
        import shlex

        command = (
            "test "
            + shlex.quote((self.workspace / "fixture.txt").read_text().strip())
            + " = LOCAL_TOOL_RESULT_SENTINEL && ! (printf bad > "
            + shlex.quote(str(self.workspace / "fixture.txt"))
            + ") && printf passed > "
            + shlex.quote(str(scratch / "test-result"))
        )
        result = self.debug(argv, child, "bash", {"command": command, "description": "Check read-only source boundary"})
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(0, json.loads(result.stdout)["result"]["metadata"]["exit"], result.stdout)
        self.assertEqual("LOCAL_TOOL_RESULT_SENTINEL\n", (self.workspace / "fixture.txt").read_text())
        self.assertEqual("passed", (scratch / "test-result").read_text())
        attempted = self.debug(
            argv, child, "write", {"filePath": str(self.workspace / "forbidden.txt"), "content": "bad"}
        )
        self.assertNotEqual(0, attempted.returncode)
        self.assertFalse((self.workspace / "forbidden.txt").exists())
        self.assertIn("Kernel-constrained Kilo shell", launch.stage_record(worker)["isolation"])
