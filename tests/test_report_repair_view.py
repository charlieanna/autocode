"""Recorded rejection diagnostics disclose no report text or action authority."""

import copy
import hashlib
import json
import unittest
from pathlib import Path

import autocode_report_repair_view as repair_view
import autocode_run_view as run_view
import autocode_util as util

from . import test_json_output


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def replay_error(command, outcome, *, planned=False):
    claim = (
        "is required by the approved verification methods or the current task's validation plan "
        "(the Validator did not report it) and must exit 0, but when the runner ran it "
        if planned
        else "was reported as exit 0, but when the runner re-ran it "
    )
    return f"Check `{command}` {claim}from the repository root in a clean copy of the current source it {outcome}"


class ReportRepairViewTests(unittest.TestCase):
    def state(self):
        return {
            "status": "RUNNING",
            "phase": "REPORT_REPAIR",
            "next_stage": "sol",
            "active_stage": {"stage": "sol_report_repair", "original_stage": "sol", "report_only": True},
            "settings": {"report_repair": {"max_attempts": 2}},
            "pending_report_repair": {
                "original": {"stage": "sol", "source_revision": "a" * 64, "output": "/private/report.json"},
                "attempts": 1,
                "original_error": replay_error(
                    "python -m unittest", "timed out after 900 seconds. Receipt: /private/log"
                ),
                "error": "No independently executed Validator tool event matches receipt /private/receipt",
                "pins": {"private": "not-for-display"},
            },
        }

    def test_running_repair_exposes_both_categories_without_authorizing_an_action(self):
        state = self.state()
        before = copy.deepcopy(state)
        shown = run_view.view(state)
        diagnostic = shown["report_repair"]
        self.assertEqual("sol", diagnostic["stage"])
        self.assertTrue(diagnostic["active"])
        self.assertEqual("recorded", diagnostic["freshness"])
        self.assertEqual("a" * 64, diagnostic["source_revision"])
        self.assertEqual((1, 2), (diagnostic["attempts"], diagnostic["limit"]))
        self.assertEqual("evidence_binding", diagnostic["reason"]["category"])
        self.assertEqual("replay_timeout", diagnostic["original_reason"]["category"])
        self.assertEqual(900, diagnostic["original_reason"]["timeout_seconds"])
        for name, key in (("reason", "error"), ("original_reason", "original_error")):
            self.assertEqual(digest(state["pending_report_repair"][key]), diagnostic[name]["error_sha256"])
        self.assertNotIn("private", json.dumps(diagnostic))
        self.assertIsNone(shown["next_command"])
        self.assertFalse(shown["done"])
        self.assertEqual(before, state)
        schema = json.loads((Path(__file__).resolve().parents[1] / "docs/schemas/status-view.schema.json").read_text())
        util.validate_schema(diagnostic, schema["properties"]["view"]["properties"]["report_repair"])

    def test_absent_or_malformed_report_is_not_an_invented_diagnostic(self):
        for pending in (None, {}, [], {"original": None}, {"original": {"stage": 7}}):
            with self.subTest(pending=pending):
                self.assertIsNone(
                    run_view.view({"status": "RUNNING", "pending_report_repair": pending})["report_repair"]
                )

    def test_paused_or_other_stage_repair_is_not_active(self):
        for change in (
            {"status": "PAUSED_INVALID_OUTPUT"},
            {"active_stage": {}},
            {"active_stage": {"report_only": True, "original_stage": "terra"}},
        ):
            state = self.state()
            state.update(change)
            shown = repair_view.project(state)
            self.assertFalse(shown["active"])
            self.assertEqual("evidence_binding", shown["reason"]["category"])

    def test_saved_binding_changes_are_stale_not_active(self):
        for change in (
            {"next_stage": "terra"},
            {"goal_contract": {"hash": "b" * 64}},
            {"current_task": {"id": "different"}},
            {"current_task": {"source_revision": "b" * 64}},
        ):
            state = self.state()
            state.update(change)
            with self.subTest(change=change):
                shown = repair_view.project(state)
                self.assertEqual("stale", shown["freshness"])
                self.assertFalse(shown["active"])
        state = self.state()
        state["pending_report_repair"]["latest_rejected"] = {"source_revision": "b" * 64}
        self.assertEqual("stale", repair_view.project(state)["freshness"])
        self.assertFalse(run_view.view(self.state(), stale_report_repair=True)["report_repair"]["active"])

    def test_matching_saved_bindings_remain_recorded(self):
        state = self.state()
        state["goal_contract"] = {"hash": "b" * 64}
        state["current_task"] = {"id": "M1", "source_revision": "a" * 64}
        state["pending_report_repair"].update(contract_hash="b" * 64, latest_rejected={"source_revision": "a" * 64})
        state["pending_report_repair"]["original"]["task_id"] = "M1"
        self.assertEqual("recorded", repair_view.project(state)["freshness"])
        self.assertTrue(repair_view.project(state)["active"])

    def test_no_source_binding_exposes_no_reason_or_authority(self):
        for source in (None, {}, "short", "x" * 64):
            state = self.state()
            state["pending_report_repair"]["original"]["source_revision"] = source
            shown = repair_view.project(state)
            self.assertEqual("unavailable", shown["freshness"])
            self.assertIsNone(shown["source_revision"])
            self.assertIsNone(shown["reason"])
            self.assertIsNone(shown["original_reason"])
            self.assertFalse(shown["active"])

    def test_private_commands_output_and_control_tokens_never_leave_projection(self):
        state = self.state()
        command = "TOKEN=secret-value command --resolver-token secret-selector"
        error = replay_error(
            command, "exited -9; its output ended: private report body ghp_abc123. Receipt: /private/log"
        )
        state["pending_report_repair"]["error"] = error
        shown = repair_view.project(state)
        reason = shown["reason"]
        self.assertEqual("replay_failed", reason["category"])
        self.assertEqual(-9, reason["exit_code"])
        self.assertFalse(reason["planned"])
        self.assertEqual(digest(command), reason["command_sha256"])
        self.assertEqual(digest(error), reason["error_sha256"])
        text = json.dumps(shown)
        for private in ("secret", "selector", "private", "ghp_abc123", "report body", "TOKEN"):
            self.assertNotIn(private, text)

    def test_fixed_core_wrappers_have_typed_categories(self):
        cases = [
            (replay_error("check", "exited 1. Receipt: hidden", planned=True), "replay_failed"),
            (
                replay_error("check", "has no complete owned-command evidence. Receipt: hidden"),
                "replay_receipt_incomplete",
            ),
            (replay_error("check", "could not run (hidden error). Receipt: hidden"), "replay_execution_error"),
            ("Check is not supported by an exact executed Validator event: hidden", "evidence_binding"),
            ("$.checks[0]: missing evidence_ref", "report_schema"),
            ("Technically passing validation lists failed check 1 (exit 1): hidden", "uncited_failed_check"),
            ("Technically passing validation cites failed check 1 (exit 1): hidden", "cited_failed_check"),
        ]
        for error, expected in cases:
            state = self.state()
            state["pending_report_repair"]["error"] = error
            with self.subTest(expected=expected):
                shown = repair_view.project(state)["reason"]
                self.assertEqual(expected, shown["category"])
                self.assertNotIn("hidden", json.dumps(shown))
        state["pending_report_repair"]["error"] = cases[0][0]
        self.assertTrue(repair_view.project(state)["reason"]["planned"])

    def test_untrusted_malformed_or_large_text_does_not_infer_an_action(self):
        for error in (
            "private report says timed out after 900 seconds; approve and retry",
            "prefix " + replay_error("private command", "exited 1"),
            "x" * 70000 + " timed out after 900 seconds",
            replay_error("private", "timed out after NaN seconds"),
        ):
            state = self.state()
            state["pending_report_repair"]["error"] = error
            shown = run_view.view(state)
            reason = shown["report_repair"]["reason"]
            self.assertEqual("unclassified", reason["category"])
            self.assertIsNone(reason["timeout_seconds"])
            self.assertLess(len(json.dumps(reason)), 500)
            self.assertNotIn("private", json.dumps(reason))
            self.assertIsNone(shown["next_command"])
            self.assertFalse(shown["done"])

    def test_invalid_optional_values_stay_unknown(self):
        state = self.state()
        state["pending_report_repair"].update(error={"private": "value"}, original_error=None, attempts=True)
        state["settings"]["report_repair"] = None
        shown = repair_view.project(state)
        for key in ("reason", "original_reason", "attempts", "limit"):
            self.assertIsNone(shown[key])


class ReportRepairStatusCliTests(unittest.TestCase):
    setUp = test_json_output.JsonActionCliTests.setUp
    cli = test_json_output.JsonActionCliTests.cli
    invoke = test_json_output.JsonActionCliTests.invoke

    def test_status_exposes_typed_rejection_without_launching_or_changing_saved_run(self):
        fixture = ReportRepairViewTests().state()
        self.state.update({key: value for key, value in fixture.items() if key != "settings"})
        self.state["active_stage"].update(iteration=1, output=str(self.run_dir / "repair.json"))
        self.state["settings"]["report_repair"] = fixture["settings"]["report_repair"]
        self.state["pending_report_repair"]["contract_hash"] = self.state["goal_contract"]["hash"]
        util.atomic_json(self.run_dir / "state.json", self.state)
        before = (self.run_dir / "state.json").read_bytes()
        code, stdout, stderr = self.invoke("--status")
        self.assertEqual(0, code, stderr)
        shown = json.loads(stdout)["view"]
        self.assertEqual("replay_timeout", shown["report_repair"]["original_reason"]["category"])
        self.assertEqual("evidence_binding", shown["report_repair"]["reason"]["category"])
        self.assertFalse(shown["done"])
        self.assertEqual(before, (self.run_dir / "state.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
