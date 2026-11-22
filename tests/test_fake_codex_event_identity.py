"""The scripted provider records separate real command events for each execution."""

import contextlib
import importlib.util
import io
import json
import os
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_support as support

REPO = Path(__file__).resolve().parents[1]


class FakeCodexEventIdentityTests(unittest.TestCase):
    def test_scripted_citations_name_only_ordinary_tracked_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.json"
            config.write_text(json.dumps({"check": "true", "paths": []}))
            spec = importlib.util.spec_from_file_location(
                "fake_codex_citation_fixture", REPO / "scenarios/harness/fake_codex.py"
            )
            module = importlib.util.module_from_spec(spec)
            with patch.dict(os.environ, {"SCENARIO_FAKE_CONFIG": str(config)}):
                spec.loader.exec_module(module)
            (root / "source with spaces.py").write_text("answer = 42\n")
            (root / "fixtures").mkdir()
            (root / "fixtures-current").symlink_to("fixtures")
            tracked = type("Listing", (), {"stdout": "fixtures-current\0missing.py\0source with spaces.py\0"})()
            with contextlib.chdir(root), patch.object(module.subprocess, "run", return_value=tracked):
                self.assertEqual(["source with spaces.py"], module.source_refs())

    def test_executed_checks_with_common_prefix_and_repeats_get_distinct_events(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config.json"
            config.write_text(json.dumps({"check": "true", "paths": []}))
            with patch.dict(os.environ, {"SCENARIO_FAKE_CONFIG": str(config)}):
                spec = importlib.util.spec_from_file_location(
                    "fake_codex_identity_fixture", REPO / "scenarios/harness/fake_codex.py"
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
            first = shlex.join([sys.executable, "-c", "print('one')"])
            second = shlex.join([sys.executable, "-c", "print('two')"])
            self.assertEqual(first[:24], second[:24])
            for progressive in (False, True):
                module.PROGRESSIVE = progressive
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    results = [module.run_verify(command) for command in (first, second, first)]
                events = [json.loads(line)["item"] for line in output.getvalue().splitlines()]
                self.assertEqual([row[0] for row in results], [0, 0, 0])
                self.assertEqual([event["command"] for event in events], [first, second, first])
                self.assertEqual([event["exit_code"] for event in events], [0, 0, 0])
                self.assertEqual([event["aggregated_output"].strip() for event in events], ["one", "two", "one"])
                self.assertEqual(len({event["id"] for event in events}), 3)
                self.assertEqual([row[1] for row in results], ["event:" + event["id"] for event in events])
                event_file = Path(temporary) / "events.jsonl"
                event_file.write_text(output.getvalue())
                checks = [
                    {"command": command, "exit_code": 0, "evidence_ref": result[1]}
                    for command, result in zip((first, second, first), results, strict=True)
                ]
                support.verify_checks(checks, temporary, event_file)
                self.assertEqual([item["evidence_ref"] for item in checks], [result[1] for result in results])
                historical = {
                    "type": "item.completed",
                    "item": {"id": "old-check", "type": "command_execution", "command": first, "exit_code": 0},
                }
                event_file.write_text(json.dumps(historical) + "\n")
                original = {"command": first, "exit_code": 0, "evidence_ref": "event:old-check"}
                support.verify_checks([original], temporary, event_file)
                event_file.write_text((json.dumps(historical) + "\n") * 2)
                with self.assertRaisesRegex(ValueError, "exact executed Validator event"):
                    support.verify_checks([original], temporary, event_file)

                report = {"checks": [original], "checks_run": [first]}
                repair = {"report_repair": True, "rejected_report": {"content": report}}
                with patch.object(module.subprocess, "run", side_effect=AssertionError("repair cannot run a check")):
                    repaired = module.report_for("tester", repair)
                self.assertEqual(repaired["checks"], report["checks"])
                self.assertEqual(repaired["checks_run"], report["checks_run"])


if __name__ == "__main__":
    unittest.main()
