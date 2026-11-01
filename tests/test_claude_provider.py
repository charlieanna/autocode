"""The built-in claude provider's facade (tools/providers/claude.py, #717): launch, login, identity, reports.

No test runs a real ``claude``: each uses a fake on an explicit PATH, or doctor's injected runner.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_agent_env as agent_env
import autocode_builder_policy as builder_policy
import autocode_format_correction
import autocode_report_retry
import autocode_stage_recovery
import autocode_usage
from providers import claude, claude_stream

FAKE = """#!{python}
import json, os, sys
if sys.argv[1:] == ["--version"]:
    if os.environ.get("FAKE_VERSION_EXIT"):
        sys.exit(int(os.environ["FAKE_VERSION_EXIT"]))
    print(os.environ.get("FAKE_VERSION", "2.1.299 (Claude Code)"))
elif sys.argv[1:] == ["auth", "status", "--json"]:
    print(os.environ.get("FAKE_AUTH", json.dumps({{"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty"}})))
    sys.exit(int(os.environ.get("FAKE_AUTH_EXIT", "0")))
else:
    sys.exit(3)
"""


class FakeClaude:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.claude = self.bin / "claude"
        self.claude.write_text(FAKE.format(python=sys.executable))
        self.claude.chmod(0o755)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def env(self, **values):
        return {"PATH": str(self.bin), **values}


class SurfaceTests(unittest.TestCase):
    def test_attribute_surface(self):
        self.assertEqual(
            (True, "claude", "codex_events", True, False, "stdin"),
            (
                claude.CONFIGURED,
                claude.NAME,
                claude.OUTPUT,
                claude.SUPPORTS_SESSIONS,
                claude.RESUME_ACROSS_STAGES,
                claude.PROMPT_MODE,
            ),
        )
        self.assertEqual(
            {"requirements", "glm", "plan_reviewer", "terra", "sol", "completion", "astra"}, set(claude.DEFAULT_MODELS)
        )
        self.assertEqual(set(claude.DEFAULT_MODELS), set(claude.DEFAULT_REASONING_EFFORTS))
        self.assertLessEqual(set(claude.DEFAULT_MODELS.values()), set(claude.LISTED_MODELS))
        self.assertEqual("claude-haiku-4-5-20251001", claude.DEFAULT_MODELS["terra"])
        for absent in ("parse_opencode_version", "transport_version", "openai_auth", "configuration_inputs"):
            self.assertFalse(hasattr(claude, absent), absent)
        policy = builder_policy.configured(None, claude)
        self.assertEqual(("claude-sonnet-5-5", "claude-opus-5-5"), (policy["strong_model"], policy["checker_model"]))
        self.assertEqual("2.1.299", claude.parse_version("2.1.299 (Claude Code)"))
        self.assertIsNone(claude.parse_version("Claude Code"))


class LaunchTests(FakeClaude, unittest.TestCase):
    def launch(self, *, allow_write, planning=False, sandbox=None, session=None, model="claude-haiku-4-5-20251001"):
        return claude.launch(
            "terra",
            self.workspace,
            self.root / "run",
            session,
            model,
            "medium",
            allow_write,
            planning=planning,
            report=self.root / "run" / "iterations" / "builder-01.json",
            schema=self.root / "schema.json",
            sandbox=sandbox,
            env=self.env(),
        )

    @staticmethod
    def after(argv, flag):
        return argv[argv.index(flag) + 1]

    def test_launch_argv_by_stage(self):
        builder, child, facts = self.launch(allow_write=True)
        judge, _, judge_facts = self.launch(allow_write=False, sandbox="workspace-write")
        planner, _, planner_facts = self.launch(allow_write=False, planning=True)
        self.assertEqual(
            [sys.executable, "-I", str(claude.STREAM), "--ledger", str(self.root / "run" / "claude-sessions")],
            builder[:5],
        )
        self.assertEqual(
            str(self.root / "run" / "iterations" / "builder-01.claude.jsonl"), self.after(builder, "--raw")
        )
        self.assertEqual([str(self.claude), "-p"], builder[builder.index("--") + 1 :][:2])
        self.assertEqual(
            ("workspace-write", "workspace-write", "read-only"),
            (facts["sandbox"], judge_facts["sandbox"], planner_facts["sandbox"]),
        )
        for argv, mode in ((builder, "acceptEdits"), (judge, "acceptEdits"), (planner, "default")):
            self.assertEqual(mode, self.after(argv, "--permission-mode"))
            self.assertEqual(claude.TOOLS, self.after(argv, "--tools"))
            self.assertEqual(list(claude.DENY_GIT), argv[-len(claude.DENY_GIT) :])
            self.assertEqual("medium", self.after(argv, "--effort"))
            self.assertEqual("claude-haiku-4-5-20251001", autocode_usage._model({"command": argv}))
            self.assertNotIn("--resume", argv)
            self.assertNotIn("--resumed", argv)
            self.assertNotIn("--json-schema", argv)  # the translator adds it, so state.json never holds it
            for flag in ("--strict-mcp-config", "--disable-slash-commands"):
                self.assertIn(flag, argv)
            self.assertEqual("project", self.after(argv, "--setting-sources"))
        allowed = lambda argv: argv[argv.index("--allowedTools") + 1 : argv.index("--disallowedTools")]  # noqa: E731
        denied = lambda argv: argv[argv.index("--disallowedTools") + 1 :]  # noqa: E731
        self.assertEqual(list(claude.WRITE_ALLOWED), allowed(builder))
        self.assertEqual(list(claude.WRITE_ALLOWED), allowed(judge))  # judges keep Edit/Write, as qualified
        self.assertEqual(list(claude.READ_ALLOWED), allowed(planner))
        self.assertEqual(list(claude.DENY_GIT), denied(builder))
        self.assertEqual([*claude.READ_DISALLOWED, *claude.DENY_GIT], denied(planner))
        resumed, _, _ = self.launch(allow_write=False, planning=True, session="s1")
        self.assertEqual("s1", self.after(resumed, "--resumed"))
        self.assertEqual("s1", self.after(resumed, "--resume"))
        self.assertLess(resumed.index("--resumed"), resumed.index("--"))
        self.assertGreater(resumed.index("--resume"), resumed.index("--"))
        self.assertEqual("1", child["DISABLE_AUTOUPDATER"])

    def test_launch_refuses_before_any_request(self):
        with self.assertRaisesRegex(ValueError, "reasoning effort must be one of"):
            claude.launch(
                "terra",
                self.workspace,
                self.root,
                None,
                "claude-haiku-4-5-20251001",
                "huge",
                True,
                schema="s",
                env=self.env(),
            )
        with self.assertRaisesRegex(ValueError, "needs a model"):
            claude.launch("terra", self.workspace, self.root, None, "", "medium", True, schema="s", env=self.env())
        with self.assertRaisesRegex(ValueError, "needs a report schema"):
            claude.launch("terra", self.workspace, self.root, None, "claude-opus-5-5", None, True, env=self.env())
        with self.assertRaisesRegex(RuntimeError, "claude is not on PATH; no provider request was launched"):
            claude.launch(
                "terra",
                self.workspace,
                self.root,
                None,
                "claude-opus-5-5",
                None,
                True,
                schema="s",
                env={"PATH": str(self.root / "empty")},
            )


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        # The developer's own shell (Claude Code on Bedrock or Vertex, a pass-through list) must not decide
        # these tests: each sets exactly the variables it is about.
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("AUTOCODE_PASS_ENV", *claude.BILLING_KEYS, *claude.BILLING_ROUTES):
            os.environ.pop(name, None)

    def test_the_child_environment_drops_the_parent_session_and_api_keys(self):
        canaries = {
            "ANTHROPIC_API_KEY": "CANARY",
            "CLAUDE_CODE_SESSION_ID": "CANARY",
            "CLAUDE_EFFORT": "xhigh",
            "KEEP": "1",
        }
        with mock.patch.dict(os.environ, canaries):
            child = claude.child_environment()
        self.assertNotIn("ANTHROPIC_API_KEY", child)
        self.assertNotIn("CLAUDE_CODE_SESSION_ID", child)
        self.assertNotIn("CLAUDE_EFFORT", child)
        self.assertEqual(("1", "1"), (child["DISABLE_AUTOUPDATER"], child["KEEP"]))
        self.assertEqual("1", agent_env.scrubbed(child)["DISABLE_AUTOUPDATER"])
        given = {"CLAUDE_CODE_SESSION_ID": "x", "PATH": "/bin"}
        self.assertEqual({"PATH": "/bin", "DISABLE_AUTOUPDATER": "1"}, claude.child_environment(given))
        self.assertEqual({"CLAUDE_CODE_SESSION_ID": "x", "PATH": "/bin"}, given)

    def test_only_a_billing_route_that_reaches_claude_is_refused(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ambient"}):
            claude.check_forbidden_environment()  # withheld by the agent scrub: never reaches Claude
            claude.check_forbidden_environment(dict(os.environ))  # the conformance preflight's unscrubbed mapping
        passed = {"ANTHROPIC_API_KEY": "sk", "AUTOCODE_PASS_ENV": "ANTHROPIC_API_KEY"}
        with self.assertRaisesRegex(RuntimeError, "ANTHROPIC_API_KEY reaches Claude Code"):
            claude.check_forbidden_environment(passed)
        with mock.patch.dict(os.environ, passed):
            with self.assertRaisesRegex(RuntimeError, "will not silently change billing routes"):
                claude.check_forbidden_environment()
        with self.assertRaisesRegex(RuntimeError, "CLAUDE_CODE_USE_BEDROCK reaches Claude Code"):
            claude.check_forbidden_environment({"CLAUDE_CODE_USE_BEDROCK": "1"})
        claude.check_forbidden_environment({"CLAUDE_CODE_USE_VERTEX": "0", "ANTHROPIC_BASE_URL": "https://gateway"})
        for route in ("CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_USE_GATEWAY", "CLAUDE_CODE_USE_ANTHROPIC_AWS"):
            with self.assertRaisesRegex(RuntimeError, f"{route} reaches Claude Code"):
                claude.check_forbidden_environment({route: "1"})
        # Feature flags with the same prefix choose no billing route; a cloud host sets CCR_V2 itself.
        claude.check_forbidden_environment({"CLAUDE_CODE_USE_CCR_V2": "1", "CLAUDE_CODE_USE_NATIVE_FILE_SEARCH": "1"})

    def test_project_settings_that_would_change_billing_are_refused(self):
        """Every stage loads <workspace>/.claude/settings.json and -p trusts it, while `claude auth status`
        does not apply an untrusted project's settings (checked live, 2.1.299), so the file itself is read."""
        refused = {
            "apiKeyHelper": {"apiKeyHelper": "echo sk-project"},
            "awsAuthRefresh": {"awsAuthRefresh": "aws sso login"},
            "env.ANTHROPIC_API_KEY": {"env": {"ANTHROPIC_API_KEY": "sk-project"}},
            "env.ANTHROPIC_AUTH_TOKEN": {"env": {"ANTHROPIC_AUTH_TOKEN": "t"}},
            "env.CLAUDE_CODE_USE_BEDROCK": {"env": {"CLAUDE_CODE_USE_BEDROCK": "1"}},
            "env.CLAUDE_CODE_USE_MANTLE": {"env": {"CLAUDE_CODE_USE_MANTLE": "1"}},
            "env.ANTHROPIC_BASE_URL": {"env": {"ANTHROPIC_BASE_URL": "https://elsewhere.example"}},
            "env.ANTHROPIC_VERTEX_BASE_URL": {"env": {"ANTHROPIC_VERTEX_BASE_URL": "https://elsewhere.example"}},
            "env.CLAUDE_CODE_OAUTH_TOKEN": {"env": {"CLAUDE_CODE_OAUTH_TOKEN": "another-account"}},
        }
        allowed = [
            {},
            {"hooks": {}, "permissions": {"allow": ["Bash(make test)"]}},
            {"env": {"CLAUDE_CODE_USE_VERTEX": "0", "MAKEFLAGS": "-j4"}},
            {"env": {"CLAUDE_CODE_USE_NATIVE_FILE_SEARCH": "1", "CLAUDE_CODE_USE_POWERSHELL_TOOL": "1"}},
        ]
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            env = {"PATH": "/usr/bin"}
            claude.check_forbidden_environment(env, workspace=workspace)  # no file
            settings = workspace / ".claude" / "settings.json"
            settings.parent.mkdir()
            for name, value in refused.items():
                with self.subTest(name):
                    settings.write_text(json.dumps(value))
                    with self.assertRaisesRegex(RuntimeError, f"sets {name}, .*will not silently change billing"):
                        claude.check_forbidden_environment(env, workspace=workspace)
                    claude.check_forbidden_environment(env)  # without a workspace only the environment counts
            for value in allowed:
                with self.subTest(value):
                    settings.write_text(json.dumps(value))
                    claude.check_forbidden_environment(env, workspace=workspace)
            for text in ('{"apiKeyHelper": "echo sk",}', "[]"):
                with self.subTest(text):
                    settings.write_text(text)
                    with self.assertRaisesRegex(RuntimeError, "no provider request was launched"):
                        claude.check_forbidden_environment(env, workspace=workspace)


class IdentityTests(FakeClaude, unittest.TestCase):
    def identity(self, **values):
        return claude.local_settings(self.workspace, env=self.env(**values))

    def test_identity_names_what_a_saved_run_depends_on(self):
        first = self.identity()
        self.assertEqual(
            {
                "engine",
                "identity_version",
                "adapter",
                "launch_policy_sha256",
                "executable",
                "version",
                "project_settings_sha256",
                "environment_sha256",
            },
            set(first),
        )
        self.assertEqual(
            ("claude", 2, "2.1.299", str(self.claude)),
            (first["engine"], first["identity_version"], first["version"], first["executable"]),
        )
        self.assertIsNone(first["project_settings_sha256"])
        self.assertEqual({"ANTHROPIC_BASE_URL": None, "CLAUDE_CONFIG_DIR": None}, first["environment_sha256"])
        self.assertNotIn("https://gateway", json.dumps(self.identity(ANTHROPIC_BASE_URL="https://gateway")))
        drifts = {
            "version": self.identity(FAKE_VERSION="2.1.300 (Claude Code)"),
            "endpoint": self.identity(ANTHROPIC_BASE_URL="https://gateway"),
        }
        with mock.patch.dict(claude.POLICY, {"removed_env": (*claude.REMOVED_ENV, "CLAUDE_EXTRA")}):
            drifts["policy"] = self.identity()
        settings = self.workspace / ".claude" / "settings.json"
        settings.parent.mkdir()
        settings.write_text("{}")
        drifts["project settings"] = self.identity()
        settings.write_text('{"hooks": {}}')
        self.assertTrue(claude.transport_drift(self.identity(), drifts["project settings"]))
        settings.unlink()
        for name, current in drifts.items():
            with self.subTest(name):
                self.assertTrue(claude.transport_drift(current, first))
        self.assertFalse(claude.transport_drift(self.identity(CLAUDE_CODE_MAX_OUTPUT_TOKENS="128000"), first))
        example = {
            "engine": "claude",
            "identity_version": 1,
            "executable": "/bin/sh",
            "version": "2.1.299 (Claude Code)",
        }
        self.assertTrue(claude.transport_drift(first, example))

    def test_prelaunch_failures_launch_nothing(self):
        with self.assertRaisesRegex(
            RuntimeError, "^claude is not on PATH; no provider request was launched. Run `autocode doctor`"
        ):
            claude.local_settings(self.workspace, env={"PATH": str(self.root / "empty")})
        with self.assertRaisesRegex(RuntimeError, r"claude version check failed \(exit 4\); no agent was launched"):
            self.identity(FAKE_VERSION_EXIT="4")
        with self.assertRaisesRegex(RuntimeError, "ANTHROPIC_AUTH_TOKEN reaches Claude Code"):
            self.identity(ANTHROPIC_AUTH_TOKEN="t", AUTOCODE_PASS_ENV="ANTHROPIC_AUTH_TOKEN")

    def test_a_project_api_key_helper_is_refused_before_every_stage(self):
        """The fake's `auth status` reports a clean subscription login, as the real one does for a project
        helper; the identity check (before every stage) and the route check (run start, joint stages,
        --accept-transport-change) still refuse it."""
        settings = self.workspace / ".claude" / "settings.json"
        settings.parent.mkdir()
        settings.write_text(json.dumps({"apiKeyHelper": "echo sk-project"}))
        with self.assertRaisesRegex(RuntimeError, "sets apiKeyHelper"):
            self.identity()
        with self.assertRaisesRegex(RuntimeError, "sets apiKeyHelper"):
            claude.check_subscription_routes({}, self.workspace, env=self.env())


class LoginTests(FakeClaude, unittest.TestCase):
    def login(self, auth=None, code="0"):
        values = {"FAKE_AUTH_EXIT": code}
        if auth is not None:
            values["FAKE_AUTH"] = json.dumps(auth)
        return claude.login_check(self.workspace, env=self.env(**values))

    def test_a_subscription_login_is_the_only_route(self):
        account = self.login(
            {
                "loggedIn": True,
                "authMethod": "claude.ai",
                "apiProvider": "firstParty",
                "email": "a@b.c",
                "orgName": "Org",
            }
        )
        self.assertEqual(
            {
                "executable": str(self.claude),
                "version": "2.1.299",
                "loggedIn": True,
                "authMethod": "claude.ai",
                "apiProvider": "firstParty",
            },
            account,
        )
        self.assertEqual(
            "oauth_token",
            self.login({"loggedIn": True, "authMethod": "oauth_token", "apiProvider": "firstParty"})["authMethod"],
        )
        with self.assertRaisesRegex(RuntimeError, "not logged in.*run `claude auth login`"):
            self.login({"loggedIn": False}, code="1")
        with self.assertRaisesRegex(RuntimeError, "not logged in"):
            claude.login_check(self.workspace, env=self.env(FAKE_AUTH="not json", FAKE_AUTH_EXIT="1"))
        with self.assertRaisesRegex(RuntimeError, r"would bill an API key \(api_key, key from ANTHROPIC_API_KEY\)"):
            self.login(
                {
                    "loggedIn": True,
                    "authMethod": "api_key",
                    "apiKeySource": "ANTHROPIC_API_KEY",
                    "apiProvider": "firstParty",
                }
            )
        with self.assertRaisesRegex(RuntimeError, "would bill an API key"):
            self.login(
                {
                    "loggedIn": True,
                    "authMethod": "claude.ai",
                    "apiKeySource": "apiKeyHelper",
                    "apiProvider": "firstParty",
                }
            )
        with self.assertRaisesRegex(RuntimeError, "would bill through bedrock"):
            self.login({"loggedIn": True, "authMethod": "third_party", "apiProvider": "bedrock"})

    def test_an_injected_runner_is_the_only_thing_called(self):
        calls = []

        class Done:
            def __init__(self, stdout):
                self.returncode, self.stdout, self.stderr = 0, stdout, ""

        def run(command):
            calls.append(command)
            if command[1:] == ["--version"]:
                return Done("2.1.299 (Claude Code)\n")
            return Done(json.dumps({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty"}))

        with mock.patch.dict(os.environ, {"PATH": str(self.root / "empty")}):
            account = claude.login_check(self.workspace, run=run)
        self.assertEqual("2.1.299", account["version"])
        self.assertEqual([["claude", "--version"], ["claude", "auth", "status", "--json"]], calls)

    def test_the_subscription_route_check_is_the_login_check(self):
        roles = {"terra": {"model": "claude-haiku-4-5-20251001"}}
        self.assertTrue(claude.check_subscription_routes(roles, self.workspace, env=self.env())["loggedIn"])
        with self.assertRaisesRegex(RuntimeError, "not logged in"):
            claude.check_subscription_routes(roles, self.workspace, env=self.env(FAKE_AUTH='{"loggedIn": false}'))


class ModelTests(unittest.TestCase):
    def test_unlisted_models_and_unknown_efforts_are_refused(self):
        claude.check_models({"terra": {"model": "claude-haiku-4-5-20251001", "reasoning_effort": "max"}})
        with self.assertRaisesRegex(RuntimeError, "Models unavailable in claude: openai/gpt-6-sol"):
            claude.check_models({"sol": {"model": "openai/gpt-6-sol"}})
        with self.assertRaisesRegex(RuntimeError, "reasoning effort must be one of low, medium, high, xhigh, max"):
            claude.check_models({"sol": {"model": "claude-sonnet-5-5", "reasoning_effort": "extreme"}})
        claude.check_models({"sol": {"model": "claude-next"}}, inventory={"claude-next"})
        self.assertEqual(sorted(claude.LISTED_MODELS), claude.list_models())
        self.assertEqual(set(claude.LISTED_MODELS), claude.available_models())


class PromptTests(unittest.TestCase):
    def test_the_output_contract_precedes_the_handoff_and_ends_with_the_schema(self):
        schema = {"type": "object", "required": ["checks"]}
        events = "/run/iterations/001/tester-01.jsonl"
        prompt = claude.prompt_for_schema("Validate the work.\nCURRENT HANDOFF DATA\n{}", schema, events)
        contract, marker, handoff = prompt.partition("\nCURRENT HANDOFF DATA\n")
        self.assertTrue(marker)
        self.assertIn("CLAUDE CODE OUTPUT CONTRACT", contract)
        self.assertIn(
            "Your stage events log is the `stage_events_log` path in CURRENT HANDOFF DATA. It is read-only evidence",
            contract,
        )
        self.assertNotIn(events, contract)
        for words in ("StructuredOutput", "event:item_N", "capture_command"):
            self.assertIn(words, contract)
        self.assertTrue(contract.endswith(json.dumps(schema, separators=(",", ":")) + "\n"))
        handoff_data = json.loads(handoff)
        self.assertIn("capture_command", handoff_data)
        self.assertEqual(events, handoff_data["stage_events_log"])
        for absent in (
            "Do not cite event: IDs",
            "Write your final report as exactly one JSON object to this file",
            "Use a permitted tool to write that JSON file",
        ):
            self.assertNotIn(absent, prompt)
        self.assertEqual("Fix the report.", claude.prompt_for_schema("Fix the report.", schema, events))


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def events(self, *rows, name="tester-01.jsonl"):
        path = self.root / name
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        return path

    def test_final_report_reads_the_accepted_report_only(self):
        report = {
            "type": "item.completed",
            "item": {"id": "structured_output", "type": "agent_message", "text": '{"ok": true}'},
        }
        started = {"type": "thread.started", "thread_id": "s"}
        done = {"type": "turn.completed"}
        response = self.root / "response.txt"
        self.assertEqual({"ok": True}, claude.final_report(self.events(started, report, done), response_path=response))
        self.assertEqual('{"ok": true}', response.read_text())
        prose = {"type": "item.completed", "item": {"id": "item_3", "type": "agent_message", "text": "All done."}}
        with self.assertRaises(RuntimeError) as raised:
            claude.final_report(self.events(started, prose, done), response_path=response)
        self.assertEqual(claude.FORMAT_REJECTION, str(raised.exception))
        self.assertEqual("All done.", response.read_text())
        failed = {"type": "turn.failed", "error": {"code": "provider_error", "message": "x"}}
        with self.assertRaisesRegex(RuntimeError, "no successful terminal result"):
            claude.final_report(self.events(started, report, failed))
        # A stage recorded by the pre-#717 example tool: no thread, the report is the sibling .json file.
        legacy = self.events({"type": "turn.completed", "cost_usd": 0.1}, name="builder-01.jsonl")
        (self.root / "builder-01.json").write_text('{"summary": "done"}')
        self.assertEqual({"summary": "done"}, claude.final_report(legacy))
        (self.root / "builder-01.json").unlink()
        with self.assertRaisesRegex(RuntimeError, "Tool did not write a JSON report"):
            claude.final_report(legacy)

    def test_the_rejection_is_the_literal_the_recovery_modules_match(self):
        self.assertEqual(autocode_report_retry.RETRYABLE_ERRORS[0], claude.FORMAT_REJECTION)
        self.assertTrue(claude.FORMAT_REJECTION.startswith(autocode_format_correction.FORMAT_ERROR))
        source = Path(autocode_stage_recovery.__file__).read_text()
        self.assertIn(f'not in ("{claude.FORMAT_REJECTION}")', source)

    def test_incomplete_response_is_the_partial_report(self):
        started = {"type": "turn.started"}
        cap = {"type": "turn.failed", "error": {"code": "output_token_limit", "message": "Claude Code exhausted"}}
        partial = {
            "type": "item.completed",
            "item": {"id": claude_stream.PARTIAL_ITEM, "type": "agent_message", "text": '{"findings": ['},
        }
        note = {"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": "Checking."}}
        self.assertEqual('{"findings": [', claude.incomplete_response(self.events(started, note, partial, cap)))
        self.assertEqual("Checking.", claude.incomplete_response(self.events(started, note, cap)))
        self.assertIsNone(claude.incomplete_response(self.events(started, cap)))
        self.assertIsNone(claude.incomplete_response(self.events(started, note, {"type": "turn.completed"})))


if __name__ == "__main__":
    unittest.main()
