"""Config-registered tools: validation, launch, reports, and drift."""

import hashlib
import json
import os
import shlex
import stat
import tempfile
import textwrap
import traceback
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import autocode_providers

from tools.providers import command


def write_config(directory, name, body):
    path = Path(directory) / "autocode" / "providers" / f"{name}.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


ROLES = """
[roles]
astra = { model = "demo", effort = "high" }
terra = { model = "demo", effort = "medium" }
sol = { model = "demo", effort = "high" }
completion = { model = "demo", effort = "medium" }
glm = { model = "demo", effort = "medium" }
plan_reviewer = { model = "demo", effort = "high" }
"""


class CommandProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.previous = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = str(self.home)
        self.addCleanup(self._restore_config_home)

    def _restore_config_home(self):
        if self.previous is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self.previous

    def test_unknown_placeholder_missing_role_and_bad_toml_are_rejected(self):
        write_config(self.home, "badplace", 'name = "badplace"\ncommand = ["tool", "{nope}"]\n' + ROLES)
        with self.assertRaisesRegex(ValueError, "unknown command placeholder"):
            command.load("badplace")
        write_config(
            self.home,
            "norole",
            'name = "norole"\ncommand = ["tool", "{report}"]\n[roles]\nastra = { model = "demo", effort = "high" }\n',
        )
        with self.assertRaisesRegex(ValueError, "missing roles"):
            command.load("norole")
        write_config(self.home, "badtoml", "name = [\n")
        with self.assertRaisesRegex(ValueError, "not valid TOML"):
            command.load("badtoml")

    def test_builder_retry_names_a_stronger_served_model_and_a_different_checker(self):
        head = 'command = ["tool", "{report}"]\nmodels = ["demo", "big", "other"]\n' + ROLES + "[builder_retry]\n"
        for name, table, message in (
            ("unknownkey", 'strong_model = "big"\nchecker_model = "other"\nchecker_effort = "high"\n', "accepts only"),
            ("nochecker", 'strong_model = "big"\n', "checker_model needs a model name"),
            ("unlisted", 'strong_model = "huge"\nchecker_model = "other"\n', "strong_model huge is not in models"),
            ("samebuilder", 'strong_model = "demo"\nchecker_model = "other"\n', "Builder's own model"),
            ("selfcheck", 'strong_model = "big"\nchecker_model = "big"\n', "checked by its own model"),
        ):
            with self.subTest(name):
                write_config(self.home, name, f'name = "{name}"\n' + head + table)
                with self.assertRaisesRegex(ValueError, message):
                    command.load(name)
        write_config(
            self.home,
            "escalates",
            'name = "escalates"\n' + head + 'strong_model = "big"\nchecker_model = "other"\nstrong_effort = "high"\n',
        )
        self.assertEqual(
            {"strong_model": "big", "checker_model": "other", "strong_effort": "high"},
            command.load("escalates").BUILDER_RETRY,
        )
        write_config(self.home, "plain", 'name = "plain"\ncommand = ["tool", "{report}"]\n' + ROLES)
        self.assertIsNone(command.load("plain").BUILDER_RETRY)

    def test_builder_retry_refuses_a_checker_in_the_strong_models_family_when_it_loads(self):
        # #612: a new run would refuse it anyway (autocode_builder_policy.configured); the config says so first.
        write_config(
            self.home,
            "familycheck",
            'name = "familycheck"\ncommand = ["tool", "{report}"]\n'
            + ROLES
            + '[builder_retry]\nstrong_model = "zai-coding-plan/glm-5.3"\nchecker_model = "zai-coding-plan/glm-5.2"\n',
        )
        with self.assertRaisesRegex(ValueError, "shares its model family"):
            command.load("familycheck")

    def test_literal_brace_escapes_are_filled_and_invalid_braces_are_rejected(self):
        write_config(
            self.home,
            "escaped",
            textwrap.dedent("""\
            name = "escaped"
            command = ["tool", "${{VAR}}", "{{\\\"key\\\":1}}", "{report}"]
            models = ["demo"]
        """)
            + ROLES,
        )
        provider = command.load("escaped")
        launched, _, _ = provider.launch(
            "terra", Path("/work"), Path("/run"), None, "demo", "medium", True, report=Path("/run/report.json")
        )
        self.assertEqual(["tool", "${VAR}", '{"key":1}', "/run/report.json"], launched)

        for name, part, message in (
            ("unknown", "{nope}", "unknown command placeholder"),
            ("open", "{", "other braces are not allowed"),
            ("close", "}", "other braces are not allowed"),
        ):
            write_config(self.home, name, f'name = "{name}"\ncommand = ["tool", "{part}"]\n' + ROLES)
            with self.assertRaisesRegex(ValueError, message):
                command.load(name)

        write_config(
            self.home,
            "fileprompt",
            'name = "fileprompt"\ncommand = ["tool", "{{prompt_file}}"]\nprompt = "file"\n' + ROLES,
        )
        with self.assertRaisesRegex(ValueError, "requires {prompt_file}"):
            command.load("fileprompt")

    def test_models_command_accepts_table_style_rows(self):
        write_config(
            self.home, "listed", 'name = "listed"\ncommand = ["tool"]\nmodels_command = ["tool", "models"]\n' + ROLES
        )
        provider = command.load("listed")
        listed = SimpleNamespace(
            returncode=0, stdout="  MODEL  DESCRIPTION\n# legacy aliases\n\n  demo  Fixture model\n"
        )
        with mock.patch("tools.providers.command.subprocess.run", return_value=listed):
            provider.check_models({"terra": {"model": "demo"}}, self.home)
            with self.assertRaisesRegex(RuntimeError, "Models unavailable"):
                provider.check_models({"terra": {"model": "missing"}}, self.home)

    def test_launch_fills_read_only_and_write_sandboxes(self):
        write_config(
            self.home,
            "filled",
            textwrap.dedent("""\
            name = "filled"
            command = ["tool", "--sandbox", "{sandbox}", "--model", "{model}", "--report", "{report}", "--role", "{role}"]
            prompt = "stdin"
            models = ["demo"]
        """)
            + ROLES,
        )
        provider = command.load("filled")
        read_only, _, _ = provider.launch(
            "sol",
            Path("/work"),
            Path("/run"),
            None,
            "demo",
            "high",
            False,
            planning=True,
            report=Path("/run/out.json"),
            sandbox="read-only",
        )
        write, _, _ = provider.launch(
            "terra",
            Path("/work"),
            Path("/run"),
            None,
            "demo",
            "medium",
            True,
            report=Path("/run/out.json"),
            sandbox="workspace-write",
        )
        self.assertEqual("read-only", read_only[read_only.index("--sandbox") + 1])
        self.assertEqual("workspace-write", write[write.index("--sandbox") + 1])
        self.assertEqual("/run/out.json", write[write.index("--report") + 1])

    def test_final_report_reads_the_sibling_json_file(self):
        write_config(self.home, "report", 'name = "report"\ncommand = ["tool"]\nmodels = ["demo"]\n' + ROLES)
        provider = command.load("report")
        folder = self.home / "stage"
        folder.mkdir()
        events = folder / "sol-01.jsonl"
        events.write_text("")
        (folder / "sol-01.json").write_text(json.dumps({"verdict": "PASS"}))
        self.assertEqual({"verdict": "PASS"}, provider.final_report(events))
        (folder / "sol-01.json").write_text("[1]")
        with self.assertRaisesRegex(RuntimeError, "JSON object"):
            provider.final_report(events)

    def test_malformed_report_file_is_preserved_for_repair(self):
        write_config(self.home, "report", 'name = "report"\ncommand = ["tool"]\n' + ROLES)
        provider = command.load("report")
        events = self.home / "events.jsonl"
        events.with_suffix(".json").write_text('{"unfinished":')
        response = self.home / "response.txt"
        with self.assertRaisesRegex(RuntimeError, "did not write a JSON report"):
            provider.final_report(events, response_path=response)
        self.assertEqual('{"unfinished":', response.read_text())

    def test_event_provider_forwards_repair_options(self):
        write_config(
            self.home,
            "events",
            'name = "events"\ncommand = ["tool"]\n'
            'output = "opencode_events"\nresume = ["--session", "{session}"]\n' + ROLES,
        )
        provider = command.load("events")
        events, response = self.home / "events.jsonl", self.home / "response.txt"
        with mock.patch.object(command._opencode_events, "final_report", return_value={"ok": True}) as parse:
            self.assertEqual({"ok": True}, provider.final_report(events, recover_wrapped=True, response_path=response))
        parse.assert_called_once_with(events, recover_wrapped=True, response_path=response)

    def test_drift_when_config_or_tool_version_changes(self):
        binary = self.home / "bin"
        binary.mkdir()
        tool = binary / "demo-tool"
        version = self.home / "version.txt"
        version.write_text("1\n")
        tool.write_text(f"#!/bin/sh\ncat {version}\n")
        tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
        previous = os.environ.get("PATH")
        os.environ["PATH"] = str(binary) + os.pathsep + previous
        self.addCleanup(lambda: os.environ.__setitem__("PATH", previous))
        path = write_config(
            self.home,
            "drift",
            textwrap.dedent("""\
            name = "drift"
            command = ["demo-tool", "{report}"]
            version_command = ["demo-tool"]
            models = ["demo"]
        """)
            + ROLES,
        )
        provider = command.load("drift")
        first = provider.local_settings(self.home)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), first["config_sha256"])
        path.write_text(path.read_text().replace("demo", "demo", 1) + "\n")
        second = provider.local_settings(self.home)
        self.assertTrue(provider.transport_drift(second, first))
        version.write_text("2\n")
        third = provider.local_settings(self.home)
        self.assertTrue(provider.transport_drift(third, second))
        self.assertFalse(provider.transport_drift(third, third))

    def test_event_output_requires_a_session_resume_template(self):
        for name, extra, message in (
            ("noresume", 'output = "opencode_events"\n', "requires a resume template"),
            ("nosession", 'output = "opencode_events"\nresume = ["--session"]\n', "resume requires {session}"),
            ("reportresume", 'resume = ["--session", "{session}"]\n', 'resume requires output = "opencode_events"'),
            ("badoutput", 'output = "stdout"\n', "output must be report_file or opencode_events"),
        ):
            write_config(self.home, name, f'name = "{name}"\ncommand = ["tool"]\n{extra}' + ROLES)
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, message):
                command.load(name)
        write_config(
            self.home,
            "sessioncmd",
            'name = "sessioncmd"\ncommand = ["tool", "{session}"]\n'
            'output = "opencode_events"\nresume = ["--session", "{session}"]\n' + ROLES,
        )
        with self.assertRaisesRegex(ValueError, "unknown command placeholder {session}"):
            command.load("sessioncmd")

    def test_report_file_prompt_scopes_reporting_scratch_to_the_run_artifacts(self):
        write_config(self.home, "scratch", 'name = "scratch"\ncommand = ["tool"]\n' + ROLES)
        provider = command.load("scratch")
        for events in (Path("/run with spaces/revision-01.jsonl"), Path(".autocode/run/revision-01.jsonl")):
            with self.subTest(events=events):
                prompt = provider.prompt_for_schema("Task\nCURRENT HANDOFF DATA\n{}", {"type": "object"}, events)
                self.assertIn(str(events.with_suffix(".json")), prompt)
                self.assertIn(str(events.with_suffix("")) + "-report-scratch", prompt)
                self.assertIn("do not stage or commit reporting artifacts", prompt)
                self.assertIn("leave repository source unchanged", prompt)

    def test_report_file_delivery_follows_schema_and_preserves_a_long_planning_handoff(self):
        write_config(self.home, "delivery", 'name = "delivery"\ncommand = ["tool"]\n' + ROLES)
        provider = command.load("delivery")
        handoff = json.dumps({"original_task": "task data " * 10000})
        prompt = provider.prompt_for_schema(
            "Work read-only on repository source. Follow the provider output contract for reporting."
            + "\nCURRENT HANDOFF DATA\n"
            + handoff,
            {"type": "object"},
            Path("/run with spaces/plan-challenge-01.jsonl"),
        )
        prefix, data = prompt.split("\nCURRENT HANDOFF DATA\n", 1)
        packet = json.loads(data)
        delivery = prefix.rsplit("\nFINAL REPORT DELIVERY\n", 1)[1]
        self.assertEqual(packet["original_task"], "task data " * 10000)
        self.assertIn("/run with spaces/plan-challenge-01.json", delivery)
        self.assertIn("Use a permitted tool to write that JSON file before finishing", delivery)
        self.assertIn("A chat response alone does not deliver the report", delivery)
        self.assertIn("leave repository source unchanged", delivery)
        self.assertNotIn("return the report, the runner saves it", prompt)

    def test_native_codex_persistence_does_not_request_reporting_scratch(self):
        write_config(
            self.home,
            "nativefile",
            'name = "nativefile"\n'
            + "command = "
            + json.dumps(["codex", "exec", command.codex_sandbox.TOKEN, "-o", "{report}"])
            + "\n"
            + 'version_command = ["codex", "--version"]\n'
            + 'sandbox_adapter = "'
            + command.codex_sandbox.ADAPTER
            + '"\n'
            + ROLES,
        )
        provider = command.load("nativefile")
        prompt = provider.prompt_for_schema(
            "Task\nCURRENT HANDOFF DATA\n{}", {"type": "object"}, Path("/run/revision-01.jsonl")
        )
        self.assertIn("no shell write of the final report is required", prompt)
        self.assertNotIn("Use a permitted tool to write that JSON file", prompt)
        self.assertNotIn("temporary report payloads", prompt.split("\nCURRENT HANDOFF DATA\n", 1)[0])

    def test_argv_visible_final_message_persistence_uses_final_response_wording(self):
        # gocode-style: -o {report} is a top-level argv slot, even without codex_artifacts.
        shapes = {
            "gocode_exec": [
                "sh",
                "-c",
                'eval "$(gocode env --shell bash)" && exec "$@"',
                "gocode",
                "codex",
                "exec",
                "-C",
                "{workspace}",
                "--sandbox",
                "{sandbox}",
                "--model",
                "{model}",
                "-c",
                'model_reasoning_effort="{effort}"',
                "--output-schema",
                "{schema}",
                "-o",
                "{report}",
                "-",
            ],
            "output_last_message": [
                "codex",
                "exec",
                "-C",
                "{workspace}",
                "--model",
                "{model}",
                "--output-schema",
                "{schema}",
                "--output-last-message",
                "{report}",
                "-",
            ],
        }
        for name, argv in shapes.items():
            with self.subTest(name=name):
                write_config(
                    self.home,
                    name,
                    f'name = "{name}"\ncommand = {json.dumps(argv)}\nversion_command = ["codex", "--version"]\n'
                    + ROLES,
                )
                provider = command.load(name)
                prompt = provider.prompt_for_schema(
                    "Task\nCURRENT HANDOFF DATA\n{}", {"type": "object"}, Path("/run/discovery-01.jsonl")
                )
                self.assertIn("Return exactly one JSON object as your final response", prompt)
                self.assertIn("no shell write of the final report is required", prompt)
                self.assertNotIn("Use a permitted tool to write that JSON file", prompt)
                self.assertNotIn("A chat response alone does not deliver the report", prompt)
                self.assertNotIn("FINAL REPORT DELIVERY", prompt)
                self.assertNotIn("temporary report payloads", prompt.split("\nCURRENT HANDOFF DATA\n", 1)[0])

    def test_persists_final_message_detects_report_flags(self):
        self.assertTrue(command.codex_sandbox.persists_final_message(["codex", "exec", "-o", "{report}"]))
        self.assertTrue(
            command.codex_sandbox.persists_final_message(["codex", "exec", "--output-last-message", "{report}"])
        )
        self.assertFalse(command.codex_sandbox.persists_final_message(["tool"]))
        self.assertFalse(command.codex_sandbox.persists_final_message(["sh", "-c", 'codex exec -o "$6"', "x"]))

    def test_event_output_resumes_sessions_and_reads_kilo_events(self):
        write_config(
            self.home,
            "events",
            'name = "events"\ncommand = ["kilo", "run", "--model", "{model}"]\n'
            'output = "opencode_events"\nresume = ["--session", "{session}"]\nmodels = ["demo"]\n' + ROLES,
        )
        provider = command.load("events")
        self.assertTrue(provider.SUPPORTS_SESSIONS)
        fresh, _, _ = provider.launch("terra", Path("/work"), Path("/run"), None, "demo", "medium", True)
        resumed, _, _ = provider.launch("terra", Path("/work"), Path("/run"), "ses_saved", "demo", "medium", True)
        self.assertEqual(["kilo", "run", "--model", "demo"], fresh)
        self.assertEqual(["kilo", "run", "--model", "demo", "--session", "ses_saved"], resumed)
        prompt = provider.prompt_for_schema(
            "Task\nCURRENT HANDOFF DATA\n{}", {"type": "object"}, Path("/run/sol-01.jsonl")
        )
        self.assertIn("event:<part.id>", prompt)
        self.assertNotIn("temporary report payloads", prompt.split("\nCURRENT HANDOFF DATA\n", 1)[0])

        # Captured from a real `kilo run --format json` call (Kilo 7.7.7).
        captured = Path(__file__).resolve().parents[1] / "tools" / "fixtures" / "kilo-7.7.7-run.jsonl"
        self.assertEqual({"ok": True}, provider.final_report(captured))
        events = provider.normalized_events(provider.raw_events(captured))
        self.assertEqual("thread.started", events[0]["type"])
        commands = [row["item"] for row in events if row["type"] == "item.completed"]
        self.assertEqual(
            [("command_execution", "echo hello-autocode", 0)],
            [(item["type"], item["command"], item["exit_code"]) for item in commands],
        )
        self.assertEqual(
            {"input_tokens": 19937, "cached_input_tokens": 9728, "output_tokens": 37, "reasoning_output_tokens": 0},
            events[-1]["usage"],
        )

    def test_bundled_kilocode_config_uses_kilo_run_events(self):
        provider = command.load("kilocode")
        self.assertEqual("opencode_events", provider.OUTPUT)
        self.assertTrue(provider.CONFIGURED)
        launched, _, _ = provider.launch(
            "terra", Path("/work"), Path("/run"), "ses_saved", provider.DEFAULT_MODELS["terra"], "medium", True
        )
        self.assertEqual(
            [
                "kilo",
                "run",
                "--auto",
                "--dir",
                "/work",
                "--model",
                "openai/gpt-5.6-terra",
                "--variant",
                "medium",
                "--format",
                "json",
                "--session",
                "ses_saved",
            ],
            launched,
        )
        self.assertEqual("zhipuai-coding-plan/glm-5.3", provider.DEFAULT_MODELS["glm"])
        policy = '{"permission":{"read":"deny"},"agent":{"code":{"permission":{"bash":"deny"}}}}'
        for planning in (False, True):
            with self.subTest(planning=planning):
                argv, environment, _ = provider.launch(
                    "astra",
                    Path("/work"),
                    Path("/run"),
                    None,
                    provider.DEFAULT_MODELS["astra"],
                    "high",
                    False,
                    planning=planning,
                    env={"KILO_CONFIG_CONTENT": policy},
                )
                self.assertIn("--auto", argv)
                self.assertNotIn("--agent", argv)
                self.assertEqual(policy, environment["KILO_CONFIG_CONTENT"])

    def test_list_models_falls_back_to_configured_role_models(self):
        write_config(self.home, "unlisted", 'name = "unlisted"\ncommand = ["tool"]\n' + ROLES)
        self.assertEqual(["demo"], command.load("unlisted").list_models())

    def test_auth_routes_require_the_declared_login_mode(self):
        binary = self.home / "bin"
        binary.mkdir()
        marker = self.home / "auth-ran"
        script = binary / "fake-auth"
        script.write_text(
            "#!/bin/sh\ntouch "
            + shlex.quote(str(marker))
            + '\nprintf \'%s\\n\' "$FAKE_LOGIN_OUTPUT"\nexit "${FAKE_LOGIN_CODE:-0}"\n'
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        auth = textwrap.dedent("""\
            [auth]
            command = ["fake-auth"]
            forbid_env = ["OPENAI_API_KEY"]

            [[auth.routes]]
            models = "openai/"
            pattern = "^\\\\s*OpenAI\\\\s+(\\\\S+)\\\\s*$"
            expect = "oauth"
        """)
        write_config(self.home, "authed", 'name = "authed"\ncommand = ["tool"]\nmodels = ["demo"]\n' + auth + ROLES)
        provider = command.load("authed")
        previous = os.environ.get("PATH")
        os.environ["PATH"] = str(binary) + os.pathsep + (previous or "")
        self.addCleanup(lambda: os.environ.__setitem__("PATH", previous) if previous else os.environ.pop("PATH", None))
        oauth = {"FAKE_LOGIN_OUTPUT": "  OpenAI oauth"}
        with mock.patch.dict(os.environ, oauth):
            provider.check_subscription_routes({"astra": {"model": "openai/x"}}, self.home)
        self.assertTrue(marker.is_file())
        marker.unlink()
        with mock.patch.dict(os.environ, {"FAKE_LOGIN_OUTPUT": "  OpenAI api"}):
            with self.assertRaisesRegex(RuntimeError, "oauth"):
                provider.check_subscription_routes({"astra": {"model": "openai/x"}}, self.home)
        with mock.patch.dict(os.environ, {**oauth, "OPENAI_API_KEY": "sk-test"}):
            with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY"):
                provider.check_subscription_routes({"astra": {"model": "openai/x"}}, self.home)
        marker.write_text("stale")
        # #888: an unrouted model refuses instead of skipping the check — and the
        # refusal happens before any probe, so the marker must stay stale.
        with mock.patch.dict(os.environ, oauth):
            with self.assertRaisesRegex(RuntimeError, "no subscription route for zai-coding-plan/glm-5.3"):
                provider.check_subscription_routes({"glm": {"model": "zai-coding-plan/glm-5.3"}}, self.home)
        self.assertEqual("stale", marker.read_text())
        with mock.patch.dict(os.environ, {"FAKE_LOGIN_CODE": "1", "FAKE_LOGIN_OUTPUT": ""}):
            with self.assertRaisesRegex(RuntimeError, "auth listing failed"):
                provider.check_subscription_routes({"astra": {"model": "openai/x"}}, self.home)
        os.environ["PATH"] = previous or ""
        with self.assertRaisesRegex(RuntimeError, "cannot verify"):
            provider.check_subscription_routes({"astra": {"model": "openai/x"}}, self.home)

        for name, extra, message in (
            ("twogroups", 'pattern = "(a)(b)"', "exactly one capture group"),
            ("nogroups", 'pattern = "OpenAI"', "exactly one capture group"),
            ("noroutes", "", "at least one route"),
        ):
            body = 'name = "' + name + '"\ncommand = ["tool"]\n[auth]\ncommand = ["fake-auth"]\n'
            if name != "noroutes":
                body += '[[auth.routes]]\nmodels = "openai/"\n' + extra + '\nexpect = "oauth"\n'
            body += ROLES
            write_config(self.home, name, body)
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, message):
                command.load(name)
        write_config(self.home, "nocmd", 'name = "nocmd"\ncommand = ["tool"]\n[auth]\nroutes = []\n' + ROLES)
        with self.assertRaisesRegex(ValueError, "command must be"):
            command.load("nocmd")

        bundled = command.load("kilocode")
        route = bundled._config["auth"]["routes"][0]
        self.assertEqual(("openai/", "oauth"), (route["models"], route["expect"]))

    def test_auth_timeout_is_bounded_and_kilo_uses_its_explicit_30_seconds(self):
        auth = '[auth]\ncommand = ["fake-auth"]\n[[auth.routes]]\nmodels = "demo/"\npattern = "(api)"\nexpect = "api"\n'
        write_config(self.home, "timed", 'name = "timed"\ncommand = ["tool"]\n' + auth + ROLES)
        timed = command.load("timed")
        response = SimpleNamespace(returncode=0, stdout="api", stderr="")
        with mock.patch.object(command.env_prep, "preflight_run", return_value=response) as probe:
            timed.check_subscription_routes({"terra": {"model": "demo/x"}}, self.home)
            self.assertEqual(15, probe.call_args.kwargs["timeout"])
        bundled = command.load("kilocode")
        self.assertEqual(30, bundled._config["auth"]["timeout_seconds"])
        with mock.patch.object(command.env_prep, "preflight_run", return_value=response) as probe:
            # The configured timeout reaches the subprocess; no clock or provider is used.
            with self.assertRaisesRegex(RuntimeError, "login mode"):
                bundled.check_subscription_routes({"astra": {"model": "openai/gpt-5.6-sol"}}, self.home)
            self.assertEqual(30, probe.call_args.kwargs["timeout"])
        for value in ("0", "61", "true", '"30"', "1.5"):
            with self.subTest(value=value):
                write_config(
                    self.home,
                    "badtime",
                    'name = "badtime"\ncommand = ["tool"]\n[auth]\ncommand = ["fake-auth"]\n'
                    + f"timeout_seconds = {value}\n"
                    + '[[auth.routes]]\nmodels = "demo/"\npattern = "(api)"\nexpect = "api"\n'
                    + ROLES,
                )
                with self.assertRaisesRegex(ValueError, "timeout_seconds must be an integer from 1 to 60"):
                    command.load("badtime")

    def test_auth_preflight_failures_keep_their_cause_without_raw_output(self):
        auth = (
            '[auth]\ncommand = ["fake-auth"]\ntimeout_seconds = 30\n'
            '[[auth.routes]]\nmodels = "demo/"\npattern = "(api)"\nexpect = "api"\n'
        )
        write_config(self.home, "timed", 'name = "timed"\ncommand = ["tool"]\n' + auth + ROLES)
        provider = command.load("timed")
        roles = {"terra": {"model": "demo/x"}}
        outcomes = (
            (
                command.subprocess.TimeoutExpired(["fake-auth"], 30, output=b"secret-output", stderr=b"secret-error"),
                "timed out after 30 seconds",
            ),
            (OSError("secret-path"), "could not start"),
            (SimpleNamespace(returncode=7, stdout="secret-output", stderr="secret-error"), "failed (exit 7)"),
        )
        for outcome, category in outcomes:
            with self.subTest(category=category):
                kwargs = {"side_effect": outcome} if isinstance(outcome, BaseException) else {"return_value": outcome}
                with mock.patch.object(command.env_prep, "preflight_run", **kwargs) as probe:
                    with self.assertRaises(RuntimeError) as caught:
                        provider.check_subscription_routes(roles, self.home)
                    self.assertEqual(30, probe.call_args.kwargs["timeout"])
                message = str(caught.exception)
                self.assertIn(category, message)
                self.assertIn("no provider request was launched", message)
                diagnostic = "".join(traceback.format_exception(caught.exception))
                for secret in ("secret-output", "secret-error", "secret-path"):
                    self.assertNotIn(secret, message)
                    self.assertNotIn(secret, diagnostic)

    def test_a_model_matching_no_route_cannot_launch(self):
        # #888, decided 2026-10-10: a role model no declared route covers used to skip
        # verification entirely; it now refuses before any probe runs, so an unrouted
        # billing route can never launch silently. This reverses the earlier position
        # ("a model outside every declared route is not this check's business").
        write_config(
            self.home,
            "authed",
            'name = "authed"\ncommand = ["tool"]\nmodels = ["demo"]\n'
            + textwrap.dedent("""\
                [auth]
                command = ["fake-auth"]

                [[auth.routes]]
                models = "openai/"
                pattern = "^\\\\s*OpenAI\\\\s+(\\\\S+)\\\\s*$"
                expect = "oauth"
                """)
            + ROLES,
        )
        provider = command.load("authed")
        binary = self.home / "bin"
        binary.mkdir()
        marker = self.home / "auth-ran"
        script = binary / "fake-auth"
        script.write_text("#!/bin/sh\ntouch " + shlex.quote(str(marker)) + "\nprintf '%s\\n' \"OpenAI oauth\"\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        previous = os.environ.get("PATH")
        os.environ["PATH"] = str(binary) + os.pathsep + (previous or "")
        self.addCleanup(lambda: os.environ.__setitem__("PATH", previous) if previous else os.environ.pop("PATH", None))
        with self.assertRaisesRegex(RuntimeError, "no subscription route for gateway/x"):
            provider.check_subscription_routes({"astra": {"model": "gateway/x"}}, self.home)
        self.assertFalse(marker.is_file())  # refused before any probe ran

    def test_unrouted_models_name_the_configured_prefixes_before_auth(self):
        write_config(
            self.home,
            "authed",
            'name = "authed"\ncommand = ["tool"]\n'
            + textwrap.dedent("""\
                [auth]
                command = ["fake-auth"]

                [[auth.routes]]
                models = "xiaomi-sgp/"
                pattern = "(oauth)"
                expect = "oauth"

                [[auth.routes]]
                models = "openai/"
                pattern = "(oauth)"
                expect = "oauth"
                """)
            + ROLES,
        )
        provider = command.load("authed")
        roles = {
            "terra": {"model": "xiaomi-token-plan-sgp/mimo-v2.6-pro"},
            "glm": {"model": "xiaomi-token-plan-sgp/mimo-v2.6-flash"},
        }
        with mock.patch.object(command.env_prep, "preflight_run") as probe:
            with self.assertRaises(RuntimeError) as caught:
                provider.check_subscription_routes(roles, self.home)
            probe.assert_not_called()
        message = str(caught.exception)
        self.assertIn(
            "no subscription route for xiaomi-token-plan-sgp/mimo-v2.6-flash, xiaomi-token-plan-sgp/mimo-v2.6-pro",
            message,
        )
        self.assertIn("Routed prefixes: openai/, xiaomi-sgp/.", message)
        self.assertIn("Choose a model with a routed prefix", message)
        response = SimpleNamespace(returncode=0, stdout="oauth", stderr="")
        with mock.patch.object(command.env_prep, "preflight_run", return_value=response) as probe:
            provider.check_subscription_routes({"terra": {"model": "xiaomi-sgp/mimo-v2.6-pro"}}, self.home)
            probe.assert_called_once()

    def test_the_bundled_kilocode_glm_default_is_a_route_kilo_publishes(self):
        # #887: kilo stopped publishing zai-coding-plan/; the default must name a live route.
        bundled = command.load("kilocode")
        prefixes = [route["models"] for route in bundled._config["auth"]["routes"]]
        glm = bundled._config["roles"]["glm"]["model"]
        self.assertTrue(any(glm.startswith(p) for p in prefixes), f"{glm} matches no route in {prefixes}")

    def test_the_bundled_kilocode_routes_verify_against_kilo_auth_list(self):
        # The live `kilo auth list` output shape (kilo 7.8.8, 2026-10-10): three plan rows.
        bundled = command.load("kilocode")
        listing = "●  OpenAI oauth\n●  Alibaba Token Plan api\n●  Zhipu AI Coding Plan api\n"
        response = SimpleNamespace(returncode=0, stdout=listing, stderr="")
        roles = {
            role: {"model": spec["model"]} for role, spec in bundled._config["roles"].items() if isinstance(spec, dict)
        }
        with mock.patch.object(command.env_prep, "preflight_run", return_value=response):
            bundled.check_subscription_routes(roles, self.home)  # every default routes and verifies
        wrong = SimpleNamespace(
            returncode=0, stdout=listing.replace("Zhipu AI Coding Plan api", "Zhipu AI Coding Plan oauth"), stderr=""
        )
        with mock.patch.object(command.env_prep, "preflight_run", return_value=wrong):
            with self.assertRaisesRegex(RuntimeError, "zhipuai-coding-plan/ models require login mode 'api'"):
                bundled.check_subscription_routes({"glm": {"model": "zhipuai-coding-plan/glm-5.3"}}, self.home)

    def test_pay_as_you_go_credit_errors_pause_as_budget(self):
        from tools import autocode_support as support

        events = self.home / "credit.jsonl"
        events.write_text(
            json.dumps(
                {
                    "type": "error",
                    "sessionID": "ses_fixture",
                    "error": {
                        "name": "APIError",
                        "data": {
                            "message": "Add credits to continue, or switch to a free model",
                            "statusCode": 402,
                            "responseBody": '{"error_type":"usage_limit_exceeded"}',
                        },
                    },
                }
            )
            + "\n"
        )
        self.assertEqual("PAUSED_BUDGET", support.failure_status(events))

    def test_missing_config_does_not_fall_back_to_opencode(self):
        with self.assertRaisesRegex(RuntimeError, "no provider config for 'missing'"):
            autocode_providers.resolve("missing")
        builtin = autocode_providers.resolve("opencode")
        self.assertEqual("providers.opencode", builtin.__name__)

    def test_event_final_report_forwards_wrapped_recovery(self):
        write_config(
            self.home,
            "events2",
            'name = "events2"\ncommand = ["kilo", "run"]\n'
            'output = "opencode_events"\nresume = ["--session", "{session}"]\nmodels = ["demo"]\n' + ROLES,
        )
        provider = command.load("events2")

        def events_file(name, final_text):
            folder = self.home / name
            folder.mkdir()
            path = folder / "sol-01.jsonl"
            rows = [
                {"type": "text", "sessionID": "ses", "part": {"id": "p1", "messageID": "m1", "text": final_text}},
                {"type": "step_finish", "sessionID": "ses", "part": {"id": "p2", "messageID": "m1", "reason": "stop"}},
                {"type": "turn.completed", "sessionID": "ses", "usage": {}},
            ]
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            return path

        report = json.dumps({"summary": "Repaired fixture report"})
        wrapped = events_file("wrapped", "Brief commentary; the repaired report follows twice.\n" + report + report)
        # A first attempt accepts brief prose followed by exactly one report, never duplicates.
        once = events_file("once", "Brief commentary; the report follows.\n" + report)
        self.assertEqual({"summary": "Repaired fixture report"}, provider.final_report(once))
        with self.assertRaisesRegex(RuntimeError, "not a JSON report"):
            provider.final_report(wrapped)
        self.assertEqual({"summary": "Repaired fixture report"}, provider.final_report(wrapped, recover_wrapped=True))
        # Conflicting duplicates and trailing content stay rejected even in recovery.
        conflicting = events_file("conflicting", "Commentary.\n" + report + json.dumps({"summary": "Different"}))
        with self.assertRaisesRegex(RuntimeError, "not a JSON report"):
            provider.final_report(conflicting, recover_wrapped=True)
        trailing = events_file("trailing", "Commentary.\n" + report + report + " trailing prose")
        with self.assertRaisesRegex(RuntimeError, "not a JSON report"):
            provider.final_report(trailing, recover_wrapped=True)

    def test_direct_anthropic_route_requires_native_api_login_without_gateway_fallback(self):
        bundled = command.load("kilocode")
        roles = {"sol": {"model": "anthropic/claude-opus-5-5"}}
        for mode, accepted in (("api", True), ("oauth", False), ("missing", False)):
            summary = "● Kilo Gateway oauth\n" + ("● Anthropic " + mode + "\n" if mode != "missing" else "")
            result = SimpleNamespace(returncode=0, stdout=summary, stderr="")
            with (
                self.subTest(mode=mode),
                mock.patch.object(command.env_prep, "preflight_run", return_value=result) as probe,
            ):
                if accepted:
                    bundled.check_subscription_routes(roles, self.home)
                else:
                    with self.assertRaisesRegex(RuntimeError, "require login mode 'api'"):
                        bundled.check_subscription_routes(roles, self.home)
                self.assertEqual(["kilo", "auth", "list"], probe.call_args.args[0])
                self.assertEqual(30, probe.call_args.kwargs["timeout"])

    def test_anthropic_addition_does_not_authorize_unrouted_or_gateway_models(self):
        bundled = command.load("kilocode")
        with mock.patch.object(command.env_prep, "preflight_run") as probe:
            with self.assertRaisesRegex(RuntimeError, "no subscription route"):
                bundled.check_subscription_routes({"sol": {"model": "unapproved/claude-opus-5-5"}}, self.home)
            probe.assert_not_called()
        summary = SimpleNamespace(returncode=0, stdout="● Anthropic api\n", stderr="")
        with mock.patch.object(command.env_prep, "preflight_run", return_value=summary):
            with self.assertRaisesRegex(RuntimeError, "kilo/ models require login mode 'oauth'"):
                bundled.check_subscription_routes({"sol": {"model": "kilo/claude-opus-5-5"}}, self.home)


class ExplicitRoleTransportTests(unittest.TestCase):
    """Public CLI configuration and provider boundary; no live provider is used."""

    def setUp(self):
        from contextlib import nullcontext

        import autocode
        import autocode_native_invocation as invocation
        import autocode_role_transport as role_transport

        self.cli, self.invocation, self.transport = autocode, invocation, role_transport
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        self.open_identity = {"engine": "opencode", "config_sha256": "open"}
        self.external_identity = {
            "engine": "kilocode",
            "identity_version": 1,
            "executable": "/fixture/kilo",
            "version": "fixture",
            "config_path": "/fixture/kilocode.toml",
            "config_sha256": "a" * 64,
        }
        self.open = SimpleNamespace(
            NAME="OpenCode",
            CONFIGURED=False,
            DEFAULT_MODELS={
                "astra": "openai/check",
                "terra": "zai-coding-plan/glm-5.3",
                "sol": "openai/check",
                "completion": "openai/check",
                "glm": "zai-coding-plan/glm-5.3",
                "plan_reviewer": "xiaomi-token-plan-sgp/mimo",
            },
            DEFAULT_REASONING_EFFORTS={
                role: "high" for role in ("astra", "terra", "sol", "completion", "glm", "plan_reviewer")
            },
            LISTED_MODELS=None,
            BUILDER_RETRY=None,
            local_settings=mock.Mock(return_value=self.open_identity),
            check_models=mock.Mock(),
            check_subscription_routes=mock.Mock(),
            transport_drift=lambda current, saved: current != saved,
            available_models=lambda workspace: [
                "zai-coding-plan/glm-5.3",
                "xiaomi-token-plan-sgp/mimo",
                "openai/check",
            ],
        )
        self.external = SimpleNamespace(
            NAME="kilocode",
            CONFIGURED=True,
            SUPPORTS_SESSIONS=False,
            local_settings=mock.Mock(return_value=self.external_identity),
            check_models=mock.Mock(),
            check_subscription_routes=mock.Mock(),
            transport_drift=lambda current, saved: current != saved,
            available_models=lambda workspace: ["anthropic/fixture-claude"],
        )
        # The built-in claude provider: identity version 2 (a launch policy, no config file).
        self.claude_identity = {
            "engine": "claude",
            "identity_version": 2,
            "adapter": "claude-cli-1",
            "launch_policy_sha256": "c" * 64,
            "executable": "/fixture/claude",
            "version": "2.1.0 (Claude Code)",
            "project_settings_sha256": None,
            "environment_sha256": {},
        }
        self.claude = SimpleNamespace(
            NAME="claude",
            CONFIGURED=True,
            SUPPORTS_SESSIONS=True,
            DEFAULT_MODELS={role: "sonnet" for role in self.open.DEFAULT_MODELS},
            DEFAULT_REASONING_EFFORTS=dict(self.open.DEFAULT_REASONING_EFFORTS),
            LISTED_MODELS=("haiku", "sonnet", "opus"),
            BUILDER_RETRY=None,
            local_settings=mock.Mock(return_value=self.claude_identity),
            check_models=mock.Mock(),
            check_subscription_routes=mock.Mock(),
            transport_drift=lambda current, saved: current != saved,
            available_models=lambda workspace: ["haiku", "sonnet", "opus"],
        )
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(self.cli, "opencode", self.open).start()
        mock.patch.object(self.cli.processes, "interrupts_held", side_effect=nullcontext).start()
        mock.patch.object(self.cli.supervision_cli, "guard", side_effect=lambda argv: nullcontext(argv)).start()
        mock.patch.object(self.cli.cli_args, "_find_run", return_value=None).start()
        mock.patch.object(autocode_providers, "resolve", side_effect=self.resolve).start()
        self.observed = []

    def resolve(self, name):
        if name == "opencode":
            return self.open
        if name == "kilocode":
            return self.external
        if name == "claude":
            return self.claude
        raise RuntimeError(f"no provider config for '{name}'")

    def claude_args(self, *extras):
        """A new run on Claude Code (--engine claude is provider claude on the facade engine)."""
        # The CLI resolves the claude facade as the run's tool before it configures (select_provider).
        mock.patch.object(self.cli, "opencode", self.claude).start()
        base = self.args()
        base[base.index("--engine") + 1] = "claude"
        del base[base.index("--provider") : base.index("--provider") + 2]
        for role in ("requirements", "glm", "plan-reviewer", "resolver"):
            base[base.index(f"--{role}-model") + 1] = "sonnet"
        return [*base, *extras]

    def args(self):
        return [
            "A discuss task",
            "--workspace",
            str(self.workspace),
            "--engine",
            "opencode",
            "--provider",
            "opencode",
            "--joint-planning",
            "--reasoning-effort",
            "high",
            "--requirements-model",
            "zai-coding-plan/glm-5.3",
            "--glm-model",
            "zai-coding-plan/glm-5.3",
            "--plan-reviewer-model",
            "xiaomi-token-plan-sgp/mimo",
            "--resolver-model",
            "xiaomi-token-plan-sgp/mimo",
            "--no-chat",
        ]

    def cli_configure(self, flags, *, saved=None):
        # Only the filesystem/controller boundary is substituted: the real public
        # parser and configure path consume the flags, models and tool selection.
        def resolved(base, args, parser, **kwargs):
            state = {"workspace": str(self.workspace), "task": "A discuss task", "iteration": 1}
            if saved is not None:
                state["settings"] = saved
            selected = base.configure(args, state)
            self.observed.append(selected)
            return 0

        with (
            mock.patch.object(self.invocation, "resolve_and_run", side_effect=resolved),
            mock.patch.object(self.cli.sys, "argv", ["autocode", *flags]),
        ):
            return self.cli.main()

    def test_saved_resolver_override_changes_only_its_model_and_effort(self):
        import copy

        self.assertEqual(0, self.cli_configure(self.args()))
        prior = copy.deepcopy(self.observed[-1])
        self.assertEqual(
            0,
            self.cli_configure(
                [
                    "--workspace",
                    str(self.workspace),
                    "--resolver-model",
                    "zai-coding-plan/glm-5.3",
                    "--resolver-reasoning-effort",
                    "medium",
                    "--no-chat",
                ],
                saved=prior,
            ),
        )
        selected = self.observed[-1]
        self.assertEqual("zai-coding-plan/glm-5.3", selected["roles"]["resolver"]["model"])
        self.assertEqual("medium", selected["roles"]["resolver"]["reasoning_effort"])
        for role in prior["roles"]:
            if role != "resolver":
                self.assertEqual(prior["roles"][role], selected["roles"][role])
        self.assertEqual(prior["limits"], selected["limits"])

    def test_public_cli_changes_only_the_explicit_claude_role(self):
        import copy

        self.assertEqual(0, self.cli_configure(self.args()))
        prior = copy.deepcopy(self.observed[-1])
        flags = [*self.args(), "--role-transport", "sol=kilocode", "--sol-model", "anthropic/fixture-claude"]
        self.assertEqual(0, self.cli_configure(flags))
        current = self.observed[-1]
        for role in prior["roles"]:
            if role != "sol":
                self.assertEqual(prior["roles"][role], current["roles"][role], role)
        self.assertEqual("opencode", current["provider"])
        self.assertEqual("kilocode", current["roles"]["sol"]["provider"])
        self.assertEqual("anthropic/fixture-claude", current["roles"]["sol"]["model"])
        self.assertEqual("high", current["roles"]["sol"]["reasoning_effort"])
        self.assertIs(self.open, self.cli.opencode)
        self.assertIs(self.external, self.transport.adapter(current, "sol", self.open, self.workspace))
        for role in ("requirements", "glm", "plan_reviewer", "resolver", "terra"):
            self.assertIs(self.open, self.transport.adapter(current, role, self.open, self.workspace))

    def test_a_run_on_claude_code_binds_one_role_to_another_connected_tool(self):
        # #2049: the base tool is any provider on the facade engine, not only OpenCode.
        flags = self.claude_args("--role-transport", "sol=kilocode", "--sol-model", "anthropic/fixture-claude")
        self.assertEqual(0, self.cli_configure(flags))
        current = self.observed[-1]
        self.assertEqual(("opencode", "claude"), (current["engine"], current["provider"]))
        self.assertEqual("kilocode", current["roles"]["sol"]["provider"])
        self.assertEqual("anthropic/fixture-claude", current["roles"]["sol"]["model"])
        self.assertEqual(self.external_identity, current["transport_identities"]["role:sol"]["identity"])
        self.assertIs(self.external, self.transport.adapter(current, "sol", self.claude, self.workspace))
        for role in ("requirements", "glm", "plan_reviewer", "resolver", "terra"):
            self.assertEqual("sonnet", current["roles"][role]["model"], role)
            self.assertIs(self.claude, self.transport.adapter(current, role, self.claude, self.workspace))
        # Each tool checked only its own role.
        self.external.check_models.assert_called_once()
        self.assertEqual({"sol"}, set(self.external.check_models.call_args.args[0]))
        for call in self.claude.check_models.call_args_list:
            self.assertNotIn("sol", call.args[0])

    def test_a_role_bound_to_the_run_s_own_tool_or_another_engine_is_refused(self):
        for flags, reason in (
            (self.claude_args("--role-transport", "sol=claude", "--sol-model", "opus"), "run's own tool"),
            (
                [*self.args(), "--role-transport", "sol=opencode", "--sol-model", "openai/check"],
                "run's own tool",
            ),
        ):
            with self.subTest(flags=flags), self.assertRaisesRegex(ValueError, reason):
                self.cli_configure(flags)
        # Codex is an engine with its own transport, not a facade provider.
        codex = {"engine": "codex", "provider": None, "roles": {"sol": {"model": "gpt-5"}}}
        with self.assertRaisesRegex(ValueError, "OpenCode facade engine"):
            self.transport.configure(
                codex, SimpleNamespace(role_transport=["sol=kilocode"], sol_model="anthropic/fixture-claude"), None
            )
        self.assertNotIn("transport_identities", codex)

    def test_valid_identity_accepts_each_facade_s_own_shape_and_nothing_else(self):
        valid = self.transport.valid_identity
        direct = {
            "engine": "autocode",
            "identity_version": 1,
            "executable": "/fixture/python",
            "version": "direct-1",
            "config_sha256": "d" * 64,
            "runtime_sha256": "e" * 64,
        }
        self.assertTrue(valid(self.external_identity, "kilocode"))
        self.assertTrue(valid(self.claude_identity, "claude"))
        self.assertTrue(valid(direct, "autocode"))
        for name, identity, provider in (
            ("other provider", self.claude_identity, "kilocode"),
            ("v2 without its policy", {**self.claude_identity, "launch_policy_sha256": None}, "claude"),
            ("v2 claiming v1", {**self.claude_identity, "identity_version": 1}, "claude"),
            ("v1 with a config hash only", {**self.external_identity, "config_path": ""}, "kilocode"),
            ("unknown version", {**direct, "identity_version": 3}, "autocode"),
            ("no executable", {**direct, "executable": ""}, "autocode"),
            ("short hash", {**direct, "config_sha256": "d" * 63}, "autocode"),
            ("not a dict", "autocode", "autocode"),
        ):
            with self.subTest(name=name):
                self.assertFalse(valid(identity, provider))

    def test_public_cli_refuses_a_saved_role_transport_change(self):
        self.cli_configure(
            [*self.args(), "--role-transport", "sol=kilocode", "--sol-model", "anthropic/fixture-claude"]
        )
        import copy

        saved = copy.deepcopy(self.observed[-1])
        before = copy.deepcopy(saved)
        with self.assertRaisesRegex(ValueError, "only available when starting a new run"):
            self.cli_configure(
                [*self.args(), "--role-transport", "sol=kilocode", "--sol-model", "anthropic/fixture-claude"],
                saved=saved,
            )
        self.assertEqual(before, saved)

    def test_selected_identity_drift_refuses_without_fallback(self):
        import autocode_util

        self.cli_configure(
            [*self.args(), "--role-transport", "sol=kilocode", "--sol-model", "anthropic/fixture-claude"]
        )
        settings = self.observed[-1]
        self.external.local_settings.return_value = {**self.external_identity, "config_sha256": "b" * 64}
        with self.assertRaises(autocode_util.Paused) as refused:
            self.transport.adapter(settings, "sol", self.open, self.workspace)
        self.assertEqual("PAUSED_TRANSPORT_CHANGED", refused.exception.status)
        self.assertEqual("The saved sol tool identity changed; no fallback", str(refused.exception))
        self.assertIs(self.open, self.transport.adapter(settings, "glm", self.open, self.workspace))
        self.assertIs(self.open, self.cli.opencode)

    def test_catalogues_cannot_move_glm_or_mimo_to_the_external_tool(self):
        import model_catalogue

        self.cli_configure(
            [*self.args(), "--role-transport", "sol=kilocode", "--sol-model", "anthropic/fixture-claude"]
        )
        import copy

        settings = copy.deepcopy(self.observed[-1])
        before = copy.deepcopy(settings["roles"])
        model_catalogue.choose(settings, self.open, self.workspace, interactive=False)
        self.assertEqual(before, settings["roles"])
        groups = list(self.transport.groups(settings, self.open))
        self.assertEqual({"sol"}, next(routes for facade, routes, _ in groups if facade is self.external).keys())
        self.assertEqual(
            set(before) - {"sol"}, next(routes for facade, routes, _ in groups if facade is self.open).keys()
        )

    def test_role_ladder_isolation_preserves_other_tools_escalation_ladders(self):
        import autocode_route_ladder as route_ladder
        import model_catalogue

        # OpenCode offers gpt-6-sol (which terra's ladder climbs) and gpt-6-astra (which resolver climbs).
        # KiloCode offers only anthropic/fixture-claude.
        self.open.available_models = lambda workspace: [
            "zai-coding-plan/glm-5.3",
            "xiaomi-token-plan-sgp/mimo",
            "openai/gpt-6-sol",
            "openai/gpt-6-astra",
        ]
        self.cli_configure(
            [
                *self.args(),
                "--completion-model",
                "openai/gpt-6-sol",
                "--astra-model",
                "openai/gpt-6-astra",
                "--terra-model",
                "zai-coding-plan/glm-5.3",
                "--role-transport",
                "sol=kilocode",
                "--sol-model",
                "anthropic/fixture-claude",
            ]
        )
        import copy

        settings = copy.deepcopy(self.observed[-1])
        model_catalogue.choose(settings, self.open, self.workspace, interactive=False)

        # sol on kilocode has its ladder filtered against kilocode (emptied because kilocode lacks gpt-6-sol).
        self.assertEqual([], settings["route_ladders"]["effort"]["sol"])
        # terra and resolver on opencode must NOT have their ladders wiped out by kilocode!
        expected_terra = [list(rung) for rung in route_ladder.EFFORT_LADDERS["terra"]]
        self.assertEqual(expected_terra, settings["route_ladders"]["effort"]["terra"])
        expected_resolver = [list(rung) for rung in route_ladder.EFFORT_LADDERS["resolver"]]
        self.assertEqual(expected_resolver, settings["route_ladders"]["effort"]["resolver"])

    def test_invalid_selection_cannot_fall_back_or_change_other_roles(self):
        for extras, reason in (
            (["--role-transport", "sol=missing", "--sol-model", "anthropic/fixture-claude"], "no provider config"),
            (["--role-transport", "not_a_role=kilocode"], "existing role"),
            (["--role-transport", "sol=kilocode"], "explicit --sol-model"),
            (
                [
                    "--role-transport",
                    "sol=kilocode",
                    "--role-transport",
                    "sol=kilocode",
                    "--sol-model",
                    "anthropic/fixture-claude",
                ],
                "named exactly once",
            ),
        ):
            with self.subTest(extras=extras), self.assertRaisesRegex((ValueError, RuntimeError), reason):
                self.cli_configure([*self.args(), *extras])
        self.assertIs(self.open, self.cli.opencode)

    def test_model_preflight_probes_the_selected_role_through_its_own_tool(self):
        import autocode_model_preflight as preflight

        self.cli_configure(
            [*self.args(), "--role-transport", "sol=kilocode", "--sol-model", "anthropic/fixture-claude"]
        )
        settings = self.observed[-1]
        # The selected tool declares billing logins; the built-in OpenCode run tool does not.
        self.external._config = {"auth": {"command": ["kilo", "auth", "list"], "routes": []}}
        self.external.check_subscription_routes.reset_mock()
        self.open.check_subscription_routes.reset_mock()
        probes = []

        def execute(**kwargs):
            probes.append((kwargs["adapter"], kwargs["model"]))
            kwargs["directory"].mkdir(parents=True, exist_ok=True)
            rows = [{"type": "step_start", "sessionID": "probe"}, {"type": "step_finish", "sessionID": "probe"}]
            (kwargs["directory"] / "events.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
            return {"exit_code": 0, "timed_out": False, "metrics": {}}

        run_dir = self.workspace / "run"
        run_dir.mkdir()
        runner = SimpleNamespace(opencode=self.cli.opencode)
        with mock.patch.object(preflight.transport, "execute", side_effect=execute):
            self.assertTrue(preflight.check(runner, {"settings": settings}, run_dir, self.workspace))
        self.assertEqual([(self.external, "anthropic/fixture-claude")], probes)
        self.external.check_subscription_routes.assert_called_once_with(
            {"sol": settings["roles"]["sol"]}, self.workspace
        )
        self.open.check_subscription_routes.assert_not_called()

    def test_external_facade_remains_ineligible_for_native_image_preparation(self):
        import autocode_provider_launch as launch
        import autocode_util

        with self.assertRaises(autocode_util.Paused) as refused:
            launch.prepare(
                engine="opencode",
                adapter=self.external,
                role="sol",
                route_role="sol",
                workspace=self.workspace,
                run_dir=self.workspace,
                session=None,
                model="anthropic/fixture-claude",
                effort="high",
                allow_write=False,
                planning=False,
                report=self.workspace / "report.json",
                schema=self.workspace / "schema.json",
                prompt_file=self.workspace / "prompt.md",
                sandbox="read-only",
                transport_args=(),
                chatgpt=True,
                provider="kilocode",
                native_image_preparation=object(),
            )
        self.assertEqual("PAUSED_VISUAL_EVIDENCE", refused.exception.status)
        self.assertEqual("Native image preparation needs the original fresh readonly Sol owner", str(refused.exception))

    def test_bare_external_model_is_not_normalized_to_openai(self):
        self.external.available_models = lambda workspace: ["fixture-claude"]
        self.cli_configure([*self.args(), "--role-transport", "sol=kilocode", "--sol-model", "fixture-claude"])
        self.assertEqual("fixture-claude", self.observed[-1]["roles"]["sol"]["model"])

    def test_missing_null_malformed_and_unknown_identity_never_fall_back(self):
        import copy

        self.cli_configure(
            [*self.args(), "--role-transport", "sol=kilocode", "--sol-model", "anthropic/fixture-claude"]
        )
        selected = self.observed[-1]
        for kind in (
            "absent",
            "absent_null_engine",
            "null",
            "malformed",
            "unknown_role",
            "unknown_provider",
            "bad_hash",
            "wrong_engine",
        ):
            saved = copy.deepcopy(selected)
            identities = saved["transport_identities"]
            if kind in ("absent", "absent_null_engine"):
                identities.pop("role:sol")
                if kind == "absent_null_engine":
                    saved["roles"]["sol"]["engine"] = None
            elif kind == "null":
                identities["role:sol"] = None
            elif kind == "malformed":
                identities["role:sol"] = "kilocode"
            elif kind == "unknown_role":
                identities["role:missing"] = identities.pop("role:sol")
            elif kind == "unknown_provider":
                identities["role:sol"]["provider"] = "not_configured"
            elif kind == "bad_hash":
                identities["role:sol"]["identity"]["config_sha256"] = None
            else:
                saved["roles"]["sol"]["engine"] = "codex"
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "Saved"):
                self.transport.adapter(saved, "sol", self.open, self.workspace)
        self.assertIs(self.open, self.cli.opencode)

    def test_admission_inventory_is_only_used_for_its_default_catalogue(self):
        from autocode_model_inventory import InvocationInventory

        self.cli_configure(
            [*self.args(), "--role-transport", "sol=kilocode", "--sol-model", "anthropic/fixture-claude"]
        )
        selected = self.observed[-1]
        inventory = InvocationInventory(self.open, self.workspace)
        inventory.available_models(self.workspace)
        self.open.check_models.reset_mock()
        self.external.check_models.reset_mock()
        self.transport.check_models(selected, self.open, self.workspace, inventory=inventory)
        self.assertEqual({"sol"}, self.external.check_models.call_args.args[0].keys())
        self.assertNotIn("sol", self.open.check_models.call_args.args[0])
        self.external.check_subscription_routes.reset_mock()
        self.open.check_subscription_routes.reset_mock()
        self.transport.check_subscription_routes(selected, self.open, self.workspace)
        self.assertEqual({"sol"}, self.external.check_subscription_routes.call_args.args[0].keys())
        self.assertNotIn("sol", self.open.check_subscription_routes.call_args.args[0])
        self.assertEqual(
            set(self.open.available_models(self.workspace)), self.open.check_models.call_args.kwargs["inventory"]
        )

    def test_inherit_copies_transport_identity_for_derived_role(self):
        import copy

        self.cli_configure(
            [*self.args(), "--role-transport", "sol=kilocode", "--sol-model", "anthropic/fixture-claude"]
        )
        settings = copy.deepcopy(self.observed[-1])
        settings["roles"]["acceptance_tester"] = copy.deepcopy(settings["roles"]["sol"])
        self.transport.inherit(settings, "acceptance_tester", "sol")
        self.assertIn("role:acceptance_tester", settings["transport_identities"])
        self.assertEqual("kilocode", self.transport.entry(settings, "acceptance_tester")["provider"])
        self.assertIs(self.external, self.transport.adapter(settings, "acceptance_tester", self.open, self.workspace))

    def test_inherit_auto_detects_source_role_for_known_derived_roles(self):
        import copy

        self.cli_configure(
            [
                *self.args(),
                "--role-transport",
                "plan_reviewer=kilocode",
                "--plan-reviewer-model",
                "anthropic/fixture-claude",
            ]
        )
        settings = copy.deepcopy(self.observed[-1])
        for derived_role in ("analyst", "investigator", "architect", "stuck_investigator"):
            with self.subTest(derived_role=derived_role):
                run_settings = copy.deepcopy(settings)
                run_settings["roles"][derived_role] = copy.deepcopy(run_settings["roles"]["plan_reviewer"])
                self.transport.inherit(run_settings, derived_role)
                self.assertIn(f"role:{derived_role}", run_settings["transport_identities"])
                self.assertEqual("kilocode", self.transport.entry(run_settings, derived_role)["provider"])
                self.assertIs(
                    self.external, self.transport.adapter(run_settings, derived_role, self.open, self.workspace)
                )

    def test_entry_auto_inherits_for_derived_role_when_not_previously_called(self):
        import copy

        self.cli_configure(
            [*self.args(), "--role-transport", "sol=kilocode", "--sol-model", "anthropic/fixture-claude"]
        )
        settings = copy.deepcopy(self.observed[-1])
        settings["roles"]["acceptance_tester"] = copy.deepcopy(settings["roles"]["sol"])
        self.assertNotIn("role:acceptance_tester", settings["transport_identities"])
        entry = self.transport.entry(settings, "acceptance_tester")
        self.assertEqual("kilocode", entry["provider"])
        self.assertIn("role:acceptance_tester", settings["transport_identities"])
        self.assertIs(self.external, self.transport.adapter(settings, "acceptance_tester", self.open, self.workspace))

    def test_a_derived_role_on_the_run_s_own_tool_inherits_no_external_identity(self):
        import copy

        # The Resolver keeps its own model on OpenCode while its source role, the
        # Completion Reviewer, moves to kilocode: nothing is copied and it stays on OpenCode.
        self.cli_configure(
            [*self.args(), "--role-transport", "astra=kilocode", "--astra-model", "anthropic/fixture-claude"]
        )
        settings = copy.deepcopy(self.observed[-1])
        self.assertIsNone(settings["roles"]["resolver"].get("provider"))
        self.assertIsNone(self.transport.entry(settings, "resolver"))
        self.assertIsNone(self.transport.inherit(settings, "resolver", "astra"))
        self.assertNotIn("role:resolver", settings["transport_identities"])
        self.assertIs(self.open, self.transport.adapter(settings, "resolver", self.open, self.workspace))
        self.assertEqual("kilocode", self.transport.entry(settings, "astra")["provider"])

    def test_external_route_without_matching_source_identity_is_refused_with_clear_message(self):
        import copy

        self.cli_configure(self.args())
        settings = copy.deepcopy(self.observed[-1])
        settings["roles"]["investigator"] = {
            "engine": "opencode",
            "provider": "kilocode",
            "model": "anthropic/fixture-claude",
        }
        with self.assertRaisesRegex(ValueError, "Saved external role transport identity is missing for investigator"):
            self.transport.entry(settings, "investigator")

    def test_autoresolver_prepare_answer_wires_analyst_transport_identity(self):
        import copy

        from units import autoresolver

        self.cli_configure(
            [
                *self.args(),
                "--role-transport",
                "plan_reviewer=kilocode",
                "--plan-reviewer-model",
                "anthropic/fixture-claude",
            ]
        )
        settings = copy.deepcopy(self.observed[-1])
        state = {"workspace": str(self.workspace), "task": "A discuss task", "settings": settings, "history": []}
        autoresolver.prepare_answer(state)
        self.assertIn("role:analyst", state["settings"]["transport_identities"])
        self.assertEqual("kilocode", state["settings"]["transport_identities"]["role:analyst"]["provider"])

    def test_autoresolver_prepare_investigation_wires_investigator_transport_identity(self):
        import copy

        from units import autoresolver

        self.cli_configure(
            [
                *self.args(),
                "--role-transport",
                "plan_reviewer=kilocode",
                "--plan-reviewer-model",
                "anthropic/fixture-claude",
            ]
        )
        settings = copy.deepcopy(self.observed[-1])
        state = {"workspace": str(self.workspace), "task": "A bug task", "settings": settings, "history": []}
        autoresolver.prepare_investigation(state)
        self.assertIn("role:investigator", state["settings"]["transport_identities"])
        self.assertEqual("kilocode", state["settings"]["transport_identities"]["role:investigator"]["provider"])

    def test_autoreview_prepare_acceptance_tester_wires_transport_identity(self):
        import copy

        from units import autoreview

        self.cli_configure(
            [*self.args(), "--role-transport", "sol=kilocode", "--sol-model", "anthropic/fixture-claude"]
        )
        settings = copy.deepcopy(self.observed[-1])
        state = {"workspace": str(self.workspace), "task": "A task", "settings": settings, "history": []}
        autoreview.prepare(state, "write_acceptance_tests", None, None)
        self.assertIn("role:acceptance_tester", state["settings"]["transport_identities"])
        self.assertEqual("kilocode", state["settings"]["transport_identities"]["role:acceptance_tester"]["provider"])

    def test_autoreview_prepare_architect_wires_transport_identity(self):
        import copy

        from units import autoreview

        self.cli_configure(
            [
                *self.args(),
                "--role-transport",
                "plan_reviewer=kilocode",
                "--plan-reviewer-model",
                "anthropic/fixture-claude",
            ]
        )
        settings = copy.deepcopy(self.observed[-1])
        state = {"workspace": str(self.workspace), "task": "A design task", "settings": settings, "history": []}
        autoreview.prepare(state, "review_design", None, None)
        self.assertIn("role:architect", state["settings"]["transport_identities"])
        self.assertEqual("kilocode", state["settings"]["transport_identities"]["role:architect"]["provider"])

    def test_utility_route_configure_wires_utility_transport_identity(self):
        import argparse
        import copy

        import autocode_utility_route as utility_route

        self.cli_configure(
            [
                *self.args(),
                "--role-transport",
                "plan_reviewer=kilocode",
                "--plan-reviewer-model",
                "anthropic/fixture-claude",
            ]
        )
        settings = copy.deepcopy(self.observed[-1])
        args = argparse.Namespace(utility_model="fixture/util", utility_reasoning_effort=None)
        utility_route.configure(settings, args, started=False)
        self.assertIn("role:utility", settings["transport_identities"])
        self.assertEqual("kilocode", settings["transport_identities"]["role:utility"]["provider"])

    def test_external_plan_reviewer_and_tester_across_all_five_paths(self):
        import copy
        from pathlib import Path

        import autocode_review_job as review_job
        import autocode_stuck_job as stuck_job
        from units import autoresolver, autoreview

        self.cli_configure(
            [
                *self.args(),
                "--role-transport",
                "plan_reviewer=kilocode",
                "--plan-reviewer-model",
                "anthropic/fixture-claude",
                "--role-transport",
                "sol=kilocode",
                "--sol-model",
                "anthropic/fixture-claude",
            ]
        )
        settings = copy.deepcopy(self.observed[-1])

        # 1. Bug-fix path: produces investigator derived from plan_reviewer
        state_bug = {
            "workspace": str(self.workspace),
            "task": "A bug fix",
            "settings": copy.deepcopy(settings),
            "history": [],
        }
        autoresolver.prepare_investigation(state_bug)
        adapter_bug = self.transport.adapter(state_bug["settings"], "investigator", self.open, self.workspace)
        self.assertIs(self.external, adapter_bug)

        # 2. Discuss path: produces analyst derived from plan_reviewer
        state_discuss = {
            "workspace": str(self.workspace),
            "task": "A discuss task",
            "settings": copy.deepcopy(settings),
            "history": [],
        }
        autoresolver.prepare_answer(state_discuss)
        adapter_discuss = self.transport.adapter(state_discuss["settings"], "analyst", self.open, self.workspace)
        self.assertIs(self.external, adapter_discuss)

        # 3. Design path: produces architect derived from plan_reviewer
        state_design = {
            "workspace": str(self.workspace),
            "task": "A design task",
            "settings": copy.deepcopy(settings),
            "history": [],
        }
        autoreview.prepare(state_design, "review_design", None, None)
        adapter_design = self.transport.adapter(state_design["settings"], "architect", self.open, self.workspace)
        self.assertIs(self.external, adapter_design)

        # 4. Code-review path: runs on sol (Tester)
        state_review = {
            "workspace": str(self.workspace),
            "task": "A review task",
            "settings": copy.deepcopy(settings),
            "history": [],
        }
        autoreview.prepare(state_review, review_job.STAGE, None, None)
        adapter_review = self.transport.adapter(state_review["settings"], "sol", self.open, self.workspace)
        self.assertIs(self.external, adapter_review)

        # 5. Stuck-investigator path: produces stuck_investigator derived from plan_reviewer
        state_stuck = {
            "workspace": str(self.workspace),
            "task": "A task",
            "settings": copy.deepcopy(settings),
            "history": [],
            "stuck_investigation": {"stage": "terra", "status": "PAUSED", "reason": "No progress"},
        }
        autoresolver.prepare_stuck(state_stuck, Path(self.workspace) / "state.json")
        adapter_stuck = self.transport.adapter(state_stuck["settings"], stuck_job.ROUTE, self.open, self.workspace)
        self.assertIs(self.external, adapter_stuck)


if __name__ == "__main__":
    unittest.main()
