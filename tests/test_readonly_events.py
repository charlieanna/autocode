"""A reviewer cannot hide a repository write by restoring it before returning."""
import json
import importlib
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch

import autocode as runner
import autocode_support as support


class ReadonlyReportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.events = self.root / "review.jsonl"
        self.schema = self.root / "schema.json"
        self.schema.write_text(json.dumps({"type": "object", "properties": {"ok": {"type": "boolean"}},
                                          "required": ["ok"], "additionalProperties": False}))
        self.record = {"engine": "opencode", "output_mode": "opencode_events", "role": "sol", "stage": "sol",
                       "events": str(self.events), "output": str(self.root / "report.json"), "schema": str(self.schema)}

    def write_events(self, snapshots):
        rows = []
        for index, snapshot in enumerate(snapshots):
            rows.append({"type": "step_start", "sessionID": "ses_review", "part": {
                "id": f"start-{index}", "type": "step-start", "snapshot": snapshot,
                "messageID": "msg_review", "sessionID": "ses_review"}})
        rows.extend([
            {"type": "text", "sessionID": "ses_review", "part": {
                "id": "text", "type": "text", "messageID": "msg_review", "text": '{"ok":true}'}},
            {"type": "step_finish", "sessionID": "ses_review", "part": {
                "id": "finish", "type": "step-finish", "messageID": "msg_review", "reason": "stop",
                "snapshot": snapshots[-1], "tokens": {"input": 1, "output": 1, "reasoning": 0,
                                                      "cache": {"read": 0, "write": 0}}}},
        ])
        self.events.write_text("\n".join(json.dumps(row) for row in rows))

    def test_rejects_review_after_a_write_was_reverted(self):
        self.write_events(["a" * 40, "b" * 40, "a" * 40])
        original = self.events.read_bytes()
        with self.assertRaisesRegex(support.Paused, "changed during read-only") as caught:
            runner.load_stage_report(self.record)
        self.assertEqual("PAUSED_STALE_VALIDATION", caught.exception.status)
        self.assertEqual(original, self.events.read_bytes())
        self.assertFalse(Path(self.record["output"]).exists())

    def test_accepts_unchanged_review(self):
        self.write_events(["a" * 40, "a" * 40])
        self.assertEqual({"ok": True}, runner.load_stage_report(self.record))

    def test_builder_may_change_repository(self):
        self.record.update(role="terra", stage="terra")
        self.write_events(["a" * 40, "b" * 40])
        self.assertEqual({"ok": True}, runner.load_stage_report(self.record))

    def test_builder_report_repair_remains_readonly(self):
        self.record.update(role="terra", stage="terra_report_repair", report_only=True)
        self.write_events(["a" * 40, "b" * 40, "a" * 40])
        with self.assertRaises(support.Paused):
            runner.load_stage_report(self.record)

    def test_report_repair_cannot_reuse_a_review_that_changed_source(self):
        self.write_events(["a" * 40, "b" * 40, "a" * 40])
        original = dict(self.record)
        original_events = self.root / "original.jsonl"
        original_events.write_bytes(self.events.read_bytes())
        original["events"] = str(original_events)
        self.record.update(stage="sol_report_repair", report_only=True)
        self.write_events(["a" * 40, "a" * 40])
        with self.assertRaises(support.Paused):
            runner.load_stage_report(self.record, evidence_record=original)

    def test_saved_review_recovery_keeps_transient_write_pause(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=Fixture", "-c",
                        "user.email=fixture@example.test", "commit", "--allow-empty", "-qm", "fixture"], check=True)
        self.write_events(["a" * 40, "b" * 40, "a" * 40])
        run = self.root / ".autocode/runs/recovery"
        run.mkdir(parents=True)
        before = run / "review.before.json"
        support.atomic_json(before, support.snapshot(self.root))
        self.record.update(before_ref=str(before), iteration=1, exit_code=0)
        state = {"version": 2, "workspace": str(self.root), "status": "RUNNING", "next_stage": "sol",
                 "sessions": {}, "stages": [], "history": [], "iteration": 1,
                 "settings": {"engine": "opencode"}, "active_stage": self.record}
        with patch.object(runner, "commit_stage_result", side_effect=AssertionError("Review must not be applied")):
            with self.assertRaises(support.Paused) as caught:
                runner.reconcile_active(state, run, self.root)
        self.assertEqual("PAUSED_STALE_VALIDATION", caught.exception.status)
        self.assertNotIn("pending_report_repair", state)


class ReadonlyEventPolicyTests(unittest.TestCase):
    setUp = ReadonlyReportTests.setUp
    write_events = ReadonlyReportTests.write_events

    def check_policy(self):
        policy = importlib.import_module("autocode_readonly_events")
        return policy.assert_unchanged_review(self.record)

    def test_policy_rejects_native_snapshot_transition(self):
        self.write_events(["a" * 40, "b" * 40, "a" * 40])
        with self.assertRaises(support.Paused):
            self.check_policy()

    def test_policy_ignores_model_text_and_tool_output(self):
        self.write_events(["a" * 40, "a" * 40])
        rows = [json.loads(line) for line in self.events.read_text().splitlines()]
        rows.insert(1, {"type": "text", "sessionID": "ses_review", "part": {
            "type": "text", "snapshot": "b" * 40, "text": "repository changed"}})
        rows.insert(2, {"type": "tool_use", "sessionID": "ses_review", "part": {
            "type": "tool", "snapshot": "b" * 40, "state": {"output": '"snapshot":"changed"'}}})
        self.events.write_text("\n".join(json.dumps(row) for row in rows))
        self.assertIsNone(self.check_policy())

    def test_policy_does_not_compare_different_sessions(self):
        self.write_events(["a" * 40])
        with self.events.open("a") as sink:
            sink.write("\n" + json.dumps({"type": "step_start", "sessionID": "other", "part": {
                "type": "step-start", "snapshot": "b" * 40}}))
        self.assertIsNone(self.check_policy())

    def test_policy_does_not_apply_to_non_native_output(self):
        self.write_events(["a" * 40, "b" * 40])
        for field, value in (("engine", "codex"), ("output_mode", "report_file")):
            with self.subTest(field=field):
                original = self.record[field]
                self.record[field] = value
                self.assertIsNone(self.check_policy())
                self.record[field] = original

    def test_policy_leaves_missing_snapshot_support_unchanged(self):
        self.write_events([None, None])
        self.assertIsNone(self.check_policy())

    def test_policy_leaves_missing_event_validation_to_provider(self):
        self.assertIsNone(self.check_policy())


class SnapshotIgnoreTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.excludes = self.root / ".git/info/exclude"

    def prepare(self):
        policy = importlib.import_module("autocode_readonly_events")
        policy.prepare_opencode_snapshots(self.root)

    def test_native_git_ignores_runtime_and_cache_but_keeps_source(self):
        self.prepare()
        for path in (".autocode/evidence/check.json", ".autocode-ui/state.json", "pkg/__pycache__/a.pyc", "pkg/a.pyc"):
            with self.subTest(path=path):
                result = subprocess.run(["git", "check-ignore", "-q", path], cwd=self.root)
                self.assertEqual(0, result.returncode)
        self.assertEqual(1, subprocess.run(["git", "check-ignore", "-q", "pkg/probe_test.go"], cwd=self.root).returncode)

    def test_preserves_user_excludes_without_duplicate_writes(self):
        prefix = "# User setting\nlocal-secret.txt"
        self.excludes.write_text(prefix)
        self.prepare()
        prepared = self.excludes.read_bytes()
        self.assertTrue(prepared.startswith(prefix.encode() + b"\n"))
        self.prepare()
        self.assertEqual(prepared, self.excludes.read_bytes())

    def test_invalid_git_workspace_refuses_preparation(self):
        with patch("autocode_readonly_events.subprocess.check_output", side_effect=subprocess.CalledProcessError(128, "git")):
            with self.assertRaises(support.Paused):
                self.prepare()

    def test_prepares_a_linked_git_worktree(self):
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=Fixture", "-c",
                        "user.email=fixture@example.test", "commit", "--allow-empty", "-qm", "fixture"], check=True)
        worktree = self.root / "linked"
        subprocess.run(["git", "-C", str(self.root), "worktree", "add", "-qb", "review", str(worktree)], check=True)
        policy = importlib.import_module("autocode_readonly_events")
        policy.prepare_opencode_snapshots(worktree)
        self.assertEqual(0, subprocess.run(["git", "check-ignore", "-q", ".autocode/evidence/check.json"],
                                          cwd=worktree).returncode)


if __name__ == "__main__":
    unittest.main()
