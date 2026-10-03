"""Public CLI and adversarial oracle checks, with no live model requests."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import provider_conformance as cli
import provider_conformance_contract as contract
import provider_conformance_transport as transport


class OracleTests(unittest.TestCase):
    def setUp(self):
        self.data = {"probe_id": "current", "workspace": "/a path/project", "phase": "build"}
        self.report = {"probe_id": "current", "workspace": "/a path/project", "nonce": "abc",
                       "checks": [{"command": c, "exit_code": code} for c, code in contract.COMMANDS.items()]}
        self.rows = [{"type": "thread.started", "thread_id": "s1"}]
        for index, (command, code) in enumerate(contract.COMMANDS.items()):
            self.rows.append({"type": "item.completed", "item": {"type": "command_execution", "id": str(index),
                              "command": command, "exit_code": code,
                              "aggregated_output": "PROBE_OK" if code == 0 else "EXPECTED_FAILURE"}})
        self.rows.append({"type": "turn.completed", "usage": {"input_tokens": 100,
                         "cached_input_tokens": 20, "output_tokens": 30, "reasoning_output_tokens": 5}})
        self.before = {"head": "h", "files": {"output.txt": "old", "input.txt": "seed"}}
        self.after = {"head": "h", "files": {"output.txt": "new", "input.txt": "seed"}}

    def assess(self, **overrides):
        values = dict(report=self.report, rows=self.rows, data=self.data, nonce="abc",
                      before=self.before, after=self.after, output=b"abc\n")
        return contract.assess(**{**values, **overrides})

    def test_pass_preserves_nonzero_check_and_reasoning_subset(self):
        result = self.assess()
        self.assertEqual("PASS", result["status"])
        self.assertEqual([0, 7], [c["exit_code"] for c in result["evidence"]])
        self.assertEqual(30, result["usage"]["output_tokens"])

    def test_self_report_without_executed_evidence_fails(self):
        result = self.assess(rows=[self.rows[0], self.rows[-1]])
        self.assertIn("command_evidence: python3 probe.py success", result["failures"])

    def test_ambiguous_or_false_exit_evidence_fails(self):
        for rows in (self.rows + [self.rows[1]], copy.deepcopy(self.rows)):
            if len(rows) == len(self.rows):
                rows[2]["item"]["exit_code"] = 0
            with self.subTest(rows=rows):
                self.assertEqual("FAIL", self.assess(rows=rows)["status"])

    def test_missing_usage_is_unknown_not_zero_or_pass(self):
        rows = copy.deepcopy(self.rows)
        del rows[-1]["usage"]["input_tokens"]
        result = self.assess(rows=rows)
        self.assertIn("usage_missing_or_invalid: input_tokens", result["failures"])
        self.assertNotIn("input_tokens", result["usage"])

    def test_completed_report_does_not_override_incomplete_or_error_turn(self):
        for rows in (self.rows[:-1], self.rows + [{"type": "error"}], self.rows + [self.rows[-1]],
                     self.rows + [{"type": "turn.started"}]):
            with self.subTest(rows=rows):
                self.assertIn("terminal_completion", self.assess(rows=rows)["failures"])

    def test_token_subsets_cannot_exceed_their_totals(self):
        for key, error in (("cached_input_tokens", "usage_cache_exceeds_input"),
                           ("reasoning_output_tokens", "usage_reasoning_invalid")):
            rows = copy.deepcopy(self.rows)
            rows[-1]["usage"][key] = 101
            with self.subTest(key=key):
                self.assertIn(error, self.assess(rows=rows)["failures"])

    def test_stale_or_wrong_workspace_or_nonce_report_fails(self):
        for key in ("probe_id", "workspace", "nonce"):
            report = {**self.report, key: "wrong"}
            with self.subTest(key=key):
                self.assertIn("report_identity: " + key, self.assess(report=report)["failures"])

    def test_resume_requires_the_saved_session(self):
        self.assertIn("session_identity", self.assess(expected_session="other")["failures"])

    def test_validator_edits_and_changed_head_fail(self):
        result = self.assess(data={**self.data, "phase": "validate"})
        self.assertIn("workspace_changes", result["failures"])
        self.assertIn("workspace_changes", self.assess(after={**self.after, "head": "new"})["failures"])

    def test_report_shape_and_output_are_independently_checked(self):
        self.assertTrue(any(f.startswith("report_schema") for f in self.assess(report=None)["failures"]))
        self.assertIn("output_contents", self.assess(output=None)["failures"])


class CliTests(unittest.TestCase):
    def invoke(self, directory, *args):
        completed = subprocess.run([sys.executable, str(Path(cli.__file__)), "--output", str(directory), *args],
                                   capture_output=True, text=True, timeout=60)
        reports = list(Path(directory).glob("*/summary.json"))
        return completed, json.loads(reports[-1].read_text()) if reports else None

    def test_all_fake_adapters_build_validate_and_resume_with_same_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            result, summary = self.invoke(directory, "--fake")
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertEqual(list(cli.PROVIDERS), [r["provider"] for r in summary["routes"]])
            for route in summary["routes"]:
                self.assertEqual(["PASS"] * 3, [p["status"] for p in route["phases"]])
                self.assertNotEqual(route["phases"][0]["session"], route["phases"][1]["session"])
                self.assertEqual(route["phases"][1]["session"], route["phases"][2]["session"])
                self.assertTrue(all(p["usage"]["output_tokens"] == 30 for p in route["phases"]))

    def test_cli_preserves_parse_and_process_failures(self):
        # The larger fault matrix is available through --fake-fault. Keep the
        # routine gate small; pure oracle tests cover the other rejection rules.
        expected = {
            "malformed_report": "report_schema", "process_failure": None,
        }
        for fault, reason in expected.items():
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as directory:
                result, summary = self.invoke(directory, "--fake", "--fake-fault", fault)
                self.assertEqual(1, result.returncode, result.stdout + result.stderr)
                self.assertIsNotNone(summary, result.stderr)
                for route in summary["routes"]:
                    self.assertEqual("FAIL", route["status"], route)
                    failure = route["phases"][-1]
                    if reason:
                        self.assertTrue(any(f.startswith(reason) for f in failure["failures"]), failure)
                    else:
                        self.assertEqual(9, failure["process_exit"])

    def test_live_requires_explicit_authorization_before_any_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            result, summary = self.invoke(directory, "--route", "codex=probe")
            self.assertEqual(2, result.returncode)
            self.assertIn("--i-authorize-live-model-spend", result.stderr)
            self.assertIsNone(summary)

    def test_unavailable_route_cannot_pass(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
                transport, "preflight", side_effect=RuntimeError("not installed")):
            result = cli.run_route("codex", "probe", Path(directory) / "route",
                                   effort="medium", timeout=1, env={})
            self.assertEqual("UNAVAILABLE", result["status"])
            self.assertEqual([], result["phases"])

    def test_interruption_preserves_failure_and_stops_route(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
                transport, "preflight", return_value={}), patch.object(
                transport, "execute", side_effect=KeyboardInterrupt) as execute:
            result = cli.run_route("codex", "probe", Path(directory) / "route",
                                   effort="medium", timeout=1, env={})
            self.assertEqual("INTERRUPTED", result["status"])
            self.assertEqual(1, execute.call_count)
            self.assertTrue((Path(result["phases"][0]["artifacts"]) / "result.json").is_file())


if __name__ == "__main__":
    unittest.main()
