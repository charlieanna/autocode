"""Kilo permission intersection and opt-in native tool execution, with no model calls."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_provider_launch as launch
from providers import kilocode_policy as policy

from tests import test_kilo_progress as progress_tests


class KiloPolicyTests(unittest.TestCase):
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

    def test_malformed_or_absent_policy_refuses_before_launch(self):
        for rules in (None, [], [{"permission": "read"}], [{"permission": "*", "pattern": "*", "action": "maybe"}]):
            with self.subTest(rules=rules), self.assertRaises(ValueError):
                policy.intersect(rules, policy.READ_TOOLS)


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
                    "models": {"check": {"name": "Offline", "limit": {"context": 32000, "output": 1024}}},
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
