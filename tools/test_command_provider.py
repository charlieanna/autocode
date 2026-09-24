"""Config-registered tools: validation, launch, reports, and drift."""
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import textwrap
import unittest

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

    def test_missing_config_does_not_fall_back_to_opencode(self):
        with self.assertRaisesRegex(RuntimeError, "no provider config for 'missing'"):
            autocode_providers.resolve("missing")
        builtin = autocode_providers.resolve("opencode")
        self.assertEqual("tools.providers.opencode", builtin.__name__)


if __name__ == "__main__":
    unittest.main()
