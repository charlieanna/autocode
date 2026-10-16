"""The offline OpenCode relay passes its visible output contract to the scripted model."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_provider_launch as provider_launch
from providers import opencode

TOOLS = Path(__file__).resolve().parents[1] / "tools"
SOURCE_VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "source_verdict": {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": ["PASS", "FAIL"]},
                "uncovered": {"type": "array", "items": {"type": "string"}},
                "summary": {"type": "string"},
            },
            "required": ["verdict", "uncovered", "summary"],
        }
    },
    "required": ["source_verdict"],
}
PROBE = """import json, sys
from pathlib import Path
prompt = sys.stdin.read()
schema = json.loads(Path(sys.argv[sys.argv.index('--output-schema') + 1]).read_text()) if '--output-schema' in sys.argv else None
Path(sys.argv[sys.argv.index('-o') + 1]).write_text(json.dumps({'schema': schema, 'prompt': prompt}))
"""


class OpenCodeFixtureSchemaTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="opencode-schema-relay-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.provider = self.root / "opencode"
        shutil.copy2(TOOLS / "fake_opencode.py", self.provider)
        self.delegate = self.root / "codex"
        self.delegate.write_text(PROBE)
        self.env = {
            **os.environ,
            "OPENCODE_CONFIG_CONTENT": json.dumps(
                {
                    "share": "disabled",
                    "agent": {"autocode_astra": {"permission": {"task": "deny", "question": "deny", "edit": "deny"}}},
                }
            ),
        }
        for key in tuple(self.env):
            if key.startswith(("AUTOCODE_FIXTURE_", "AUTOCODE_REPLAY_")):
                self.env.pop(key)

    def invoke(self, prompt):
        return subprocess.run(
            [sys.executable, str(self.provider), "run", "--format", "json", "--agent", "autocode_astra"],
            input=prompt,
            text=True,
            capture_output=True,
            cwd=self.root,
            env=self.env,
            timeout=10,
        )

    def rendered(self, packet, schema):
        prompt = (
            'Task quoting an earlier contract\nOPENCODE OUTPUT CONTRACT\n{"ignored":true}\nCURRENT HANDOFF DATA\n'
            + json.dumps(packet)
        )
        return opencode.prompt_for_schema(prompt, schema, self.root / "events.jsonl")

    def report(self, result):
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        events = [json.loads(line) for line in result.stdout.splitlines()]
        return json.loads(next(row["part"]["text"] for row in events if row["type"] == "text"))

    def test_repair_relay_preserves_current_schema(self):
        packet = {
            "execution_engine": "opencode",
            "stage": "astra_review_report_repair",
            "original": {"stage": "astra_review"},
        }
        prompt = self.rendered(packet, SOURCE_VERDICT_SCHEMA)
        report = self.report(self.invoke(prompt))
        self.assertEqual(SOURCE_VERDICT_SCHEMA, report["schema"])
        self.assertEqual(prompt, report["prompt"])

    def test_completion_repair_keeps_source_verdict_through_relay(self):
        shutil.copy2(TOOLS / "fake_codex.py", self.delegate)
        shutil.copy2(TOOLS / "goal_fixtures.py", self.root / "goal_fixtures.py")
        verdict = {"verdict": "FAIL", "uncovered": ["task:0"], "summary": "Uncovered source"}
        for existing in (None, verdict):
            with self.subTest(existing=existing):
                original = self.root / "original.json"
                value = {"status": "CONTINUE", "checks": [{"command": "false", "exit_code": 1}]}
                if existing is not None:
                    value["source_verdict"] = existing
                original.write_text(json.dumps(value))
                packet = {
                    "execution_engine": "opencode",
                    "stage": "astra_review_report_repair",
                    "report_repair": True,
                    "original": {"stage": "astra_review", "output": str(original)},
                }
                report = self.report(self.invoke(self.rendered(packet, SOURCE_VERDICT_SCHEMA)))
                self.assertEqual(value["checks"], report["checks"])
                if existing is not None:
                    self.assertEqual(existing, report["source_verdict"])
                else:
                    self.assertEqual("PASS", report["source_verdict"]["verdict"])

    def test_schema_less_loss_correction_remains_supported(self):
        cache = self.root / "lost-report.json"
        self.env["AUTOCODE_FIXTURE_REPORT_LOSS"] = str(cache)
        result = self.invoke("Your previous final message could not be parsed as the report: repair it")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIsNone(json.loads(cache.read_text())["schema"])

    def test_malformed_current_schema_does_not_launch_delegate(self):
        self.delegate.write_text("raise AssertionError('delegate must not launch')\n")
        boundary = "\n\nNATIVE TOOL BOUNDARY: commands run in a kernel-constrained subprocess."
        # The qualified fixture (#709) reads the contract up to a contained launch's boundary, no further.
        for qualified in (False, True):
            if qualified:
                self.env["AUTOCODE_FIXTURE_QUALIFIED_OPENCODE"] = "1"
            for schema in ("[]", "{} trailing text", "{} trailing text" + boundary, "[]" + boundary):
                with self.subTest(schema=schema, qualified=qualified):
                    result = self.invoke(
                        "\nOPENCODE OUTPUT CONTRACT\n"
                        + schema
                        + "\nCURRENT HANDOFF DATA\n"
                        + json.dumps({"execution_engine": "opencode", "stage": "astra_review"})
                    )
                    self.assertNotEqual(0, result.returncode)
                    self.assertNotIn("delegate must not launch", result.stderr)

    def test_a_contained_launch_relays_its_contract_only_from_the_qualified_fixture(self):
        # A contained launch follows the contract with the boundary instructions (#709).
        prompt = provider_launch.containment_prompt(
            self.rendered({"execution_engine": "opencode", "stage": "astra_review"}, SOURCE_VERDICT_SCHEMA),
            {"tool_containment": {"scratch": str(self.root / "scratch")}},
            stage="astra_review",
        )
        self.assertIn("\nNATIVE TOOL BOUNDARY:", prompt.rsplit("\nOPENCODE OUTPUT CONTRACT\n", 1)[1])
        unqualified = self.invoke(prompt)  # The default fixture, as before.
        self.assertNotEqual(0, unqualified.returncode)
        self.assertIn("fake_opencode: invalid current output schema", unqualified.stderr)
        self.env["AUTOCODE_FIXTURE_QUALIFIED_OPENCODE"] = "1"
        report = self.report(self.invoke(prompt))
        self.assertEqual(SOURCE_VERDICT_SCHEMA, report["schema"])
        self.assertEqual(prompt, report["prompt"])


if __name__ == "__main__":
    unittest.main()
