"""Config-registered tools: validation, launch, reports, and drift."""
# path bootstrap: runtime in tools/, fakes in tests/fakes/
import sys as _sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[2] if 'fakes' in _Path(__file__).parts else _Path(__file__).resolve().parents[1]
_TOOLS = _ROOT / 'tools'
_FAKES = _ROOT / 'tests' / 'fakes'
for _p in (_ROOT, _TOOLS, _ROOT / 'tests', _FAKES):
    _s = str(_p)
    if _s not in _sys.path:
        _sys.path.insert(0, _s)
import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import tempfile
import textwrap
import unittest
from types import SimpleNamespace
from unittest import mock

from tools import autocode_providers
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
        write_config(self.home, "norole", 'name = "norole"\ncommand = ["tool", "{report}"]\n[roles]\nastra = { model = "demo", effort = "high" }\n')
        with self.assertRaisesRegex(ValueError, "missing roles"):
            command.load("norole")
        write_config(self.home, "badtoml", "name = [\n")
        with self.assertRaisesRegex(ValueError, "not valid TOML"):
            command.load("badtoml")

    def test_literal_brace_escapes_are_filled_and_invalid_braces_are_rejected(self):
        write_config(self.home, "escaped", textwrap.dedent('''\
            name = "escaped"
            command = ["tool", "${{VAR}}", "{{\\\"key\\\":1}}", "{report}"]
            models = ["demo"]
        ''') + ROLES)
        provider = command.load("escaped")
        launched, _, _ = provider.launch("terra", Path("/work"), Path("/run"), None, "demo", "medium", True,
                                          report=Path("/run/report.json"))
        self.assertEqual(["tool", "${VAR}", '{"key":1}', "/run/report.json"], launched)

        for name, part, message in (
            ("unknown", "{nope}", "unknown command placeholder"),
            ("open", "{", "other braces are not allowed"),
            ("close", "}", "other braces are not allowed"),
        ):
            write_config(self.home, name, f'name = "{name}"\ncommand = ["tool", "{part}"]\n' + ROLES)
            with self.assertRaisesRegex(ValueError, message):
                command.load(name)

        write_config(self.home, "fileprompt", 'name = "fileprompt"\ncommand = ["tool", "{{prompt_file}}"]\nprompt = "file"\n' + ROLES)
        with self.assertRaisesRegex(ValueError, 'requires {prompt_file}'):
            command.load("fileprompt")

    def test_models_command_accepts_table_style_rows(self):
        write_config(self.home, "listed", 'name = "listed"\ncommand = ["tool"]\nmodels_command = ["tool", "models"]\n' + ROLES)
        provider = command.load("listed")
        listed = SimpleNamespace(returncode=0, stdout="  MODEL  DESCRIPTION\n# legacy aliases\n\n  demo  Fixture model\n")
        with mock.patch("tools.providers.command.subprocess.run", return_value=listed):
            provider.check_models({"terra": {"model": "demo"}}, self.home)
            with self.assertRaisesRegex(RuntimeError, "Models unavailable"):
                provider.check_models({"terra": {"model": "missing"}}, self.home)

    def test_launch_fills_read_only_and_write_sandboxes(self):
        write_config(self.home, "filled", textwrap.dedent("""\
            name = "filled"
            command = ["tool", "--sandbox", "{sandbox}", "--model", "{model}", "--report", "{report}", "--role", "{role}"]
            prompt = "stdin"
            models = ["demo"]
        """) + ROLES)
        provider = command.load("filled")
        read_only, _, _ = provider.launch("sol", Path("/work"), Path("/run"), None, "demo", "high", False,
                                          planning=True, report=Path("/run/out.json"), sandbox="read-only")
        write, _, _ = provider.launch("terra", Path("/work"), Path("/run"), None, "demo", "medium", True,
                                      report=Path("/run/out.json"), sandbox="workspace-write")
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
        path = write_config(self.home, "drift", textwrap.dedent(f"""\
            name = "drift"
            command = ["demo-tool", "{{report}}"]
            version_command = ["demo-tool"]
            models = ["demo"]
        """) + ROLES)
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
        write_config(self.home, "sessioncmd", 'name = "sessioncmd"\ncommand = ["tool", "{session}"]\n'
                     'output = "opencode_events"\nresume = ["--session", "{session}"]\n' + ROLES)
        with self.assertRaisesRegex(ValueError, "unknown command placeholder {session}"):
            command.load("sessioncmd")

    def test_event_output_resumes_sessions_and_reads_kilo_events(self):
        write_config(self.home, "events", 'name = "events"\ncommand = ["kilo", "run", "--model", "{model}"]\n'
                     'output = "opencode_events"\nresume = ["--session", "{session}"]\nmodels = ["demo"]\n' + ROLES)
        provider = command.load("events")
        self.assertTrue(provider.SUPPORTS_SESSIONS)
        fresh, _, _ = provider.launch("terra", Path("/work"), Path("/run"), None, "demo", "medium", True)
        resumed, _, _ = provider.launch("terra", Path("/work"), Path("/run"), "ses_saved", "demo", "medium", True)
        self.assertEqual(["kilo", "run", "--model", "demo"], fresh)
        self.assertEqual(["kilo", "run", "--model", "demo", "--session", "ses_saved"], resumed)
        prompt = provider.prompt_for_schema("Task\nCURRENT HANDOFF DATA\n{}", {"type": "object"}, Path("/run/sol-01.jsonl"))
        self.assertIn("event:<part.id>", prompt)

        # Captured from a real `kilo run --format json` call (Kilo 7.7.7).
        captured = Path(__file__).resolve().parents[1] / "tools" / "fixtures" / "kilo-7.7.7-run.jsonl"
        self.assertEqual({"ok": True}, provider.final_report(captured))
        events = provider.normalized_events(provider.raw_events(captured))
        self.assertEqual("thread.started", events[0]["type"])
        commands = [row["item"] for row in events if row["type"] == "item.completed"]
        self.assertEqual([("command_execution", "echo hello-autocode", 0)],
                         [(item["type"], item["command"], item["exit_code"]) for item in commands])
        self.assertEqual({"input_tokens": 19937, "cached_input_tokens": 9728, "output_tokens": 37,
                          "reasoning_output_tokens": 0}, events[-1]["usage"])

    def test_bundled_kilocode_config_uses_kilo_run_events(self):
        provider = command.load("kilocode")
        self.assertEqual("opencode_events", provider.OUTPUT)
        self.assertTrue(provider.CONFIGURED)
        launched, _, _ = provider.launch("terra", Path("/work"), Path("/run"), "ses_saved",
                                          provider.DEFAULT_MODELS["terra"], "medium", True)
        self.assertEqual(["kilo", "run", "--dir", "/work", "--model", "openai/gpt-5.6-terra",
                          "--variant", "medium", "--format", "json", "--session", "ses_saved"], launched)
        self.assertEqual("zai-coding-plan/glm-5.3", provider.DEFAULT_MODELS["glm"])

    def test_list_models_falls_back_to_configured_role_models(self):
        write_config(self.home, "unlisted", 'name = "unlisted"\ncommand = ["tool"]\n' + ROLES)
        self.assertEqual(["demo"], command.load("unlisted").list_models())

    def test_auth_routes_require_the_declared_login_mode(self):
        binary = self.home / "bin"
        binary.mkdir()
        marker = self.home / "auth-ran"
        script = binary / "fake-auth"
        script.write_text("#!/bin/sh\ntouch " + shlex.quote(str(marker)) + "\nprintf '%s\\n' \"$FAKE_AUTH_OUTPUT\"\nexit \"${FAKE_AUTH_CODE:-0}\"\n")
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
        oauth = {"FAKE_AUTH_OUTPUT": "  OpenAI oauth"}
        with mock.patch.dict(os.environ, oauth):
            provider.check_subscription_routes({"astra": {"model": "openai/x"}}, self.home)
        self.assertTrue(marker.is_file())
        marker.unlink()
        with mock.patch.dict(os.environ, {"FAKE_AUTH_OUTPUT": "  OpenAI api"}):
            with self.assertRaisesRegex(RuntimeError, "oauth"):
                provider.check_subscription_routes({"astra": {"model": "openai/x"}}, self.home)
        with mock.patch.dict(os.environ, {**oauth, "OPENAI_API_KEY": "sk-test"}):
            with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY"):
                provider.check_subscription_routes({"astra": {"model": "openai/x"}}, self.home)
        marker.write_text("stale")
        with mock.patch.dict(os.environ, oauth):
            provider.check_subscription_routes({"glm": {"model": "zai-coding-plan/glm-5.3"}}, self.home)
        self.assertEqual("stale", marker.read_text())
        with mock.patch.dict(os.environ, {"FAKE_AUTH_CODE": "1", "FAKE_AUTH_OUTPUT": ""}):
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

    def test_pay_as_you_go_credit_errors_pause_as_budget(self):
        from tools import autocode_support as support
        events = self.home / "credit.jsonl"
        events.write_text(json.dumps({"type": "error", "sessionID": "ses_fixture", "error": {
            "name": "APIError", "data": {"message": "Add credits to continue, or switch to a free model",
                                         "statusCode": 402,
                                         "responseBody": '{"error_type":"usage_limit_exceeded"}'}}}) + "\n")
        self.assertEqual("PAUSED_BUDGET", support.failure_status(events))

    def test_missing_config_does_not_fall_back_to_opencode(self):
        with self.assertRaisesRegex(RuntimeError, "no provider config for 'missing'"):
            autocode_providers.resolve("missing")
        builtin = autocode_providers.resolve("opencode")
        self.assertEqual("tools.providers.opencode", builtin.__name__)

    def test_event_final_report_forwards_wrapped_recovery(self):
        write_config(self.home, "events2", 'name = "events2"\ncommand = ["kilo", "run"]\n'
                     'output = "opencode_events"\nresume = ["--session", "{session}"]\nmodels = ["demo"]\n' + ROLES)
        provider = command.load("events2")

        def events_file(final_text):
            folder = self.home / f"stage{abs(hash(final_text)) % 1000}"
            folder.mkdir(exist_ok=True)
            path = folder / "sol-01.jsonl"
            report = json.dumps({"summary": "Repaired fixture report"})
            rows = [
                {"type": "text", "sessionID": "ses", "part": {"id": "p1", "messageID": "m1", "text": final_text}},
                {"type": "step_finish", "sessionID": "ses", "part": {"id": "p2", "messageID": "m1", "reason": "stop"}},
                {"type": "turn.completed", "sessionID": "ses", "usage": {}},
            ]
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            return path

        report = json.dumps({"summary": "Repaired fixture report"})
        wrapped = events_file("Brief commentary; the repaired report follows twice.\n" + report + report)
        # The default stays strict: wrapped prose is never accepted implicitly.
        with self.assertRaisesRegex(RuntimeError, "not a JSON report"):
            provider.final_report(wrapped)
        self.assertEqual({"summary": "Repaired fixture report"},
                         provider.final_report(wrapped, recover_wrapped=True))
        # Conflicting duplicates and trailing content stay rejected even in recovery.
        conflicting = events_file("Commentary.\n" + report + json.dumps({"summary": "Different"}))
        with self.assertRaisesRegex(RuntimeError, "not a JSON report"):
            provider.final_report(conflicting, recover_wrapped=True)
        trailing = events_file("Commentary.\n" + report + report + " trailing prose")
        with self.assertRaisesRegex(RuntimeError, "not a JSON report"):
            provider.final_report(trailing, recover_wrapped=True)


if __name__ == "__main__":
    unittest.main()
