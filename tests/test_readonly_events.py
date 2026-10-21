"""A reviewer cannot hide a repository write by restoring it before returning."""

import importlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
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
        self.schema.write_text(
            json.dumps(
                {
                    "type": "object",
                    "properties": {"ok": {"type": "boolean"}},
                    "required": ["ok"],
                    "additionalProperties": False,
                }
            )
        )
        self.record = {
            "engine": "opencode",
            "output_mode": "opencode_events",
            "role": "sol",
            "stage": "sol",
            "events": str(self.events),
            "output": str(self.root / "report.json"),
            "schema": str(self.schema),
        }

    def write_events(self, snapshots):
        rows = []
        for index, snapshot in enumerate(snapshots):
            rows.append(
                {
                    "type": "step_start",
                    "sessionID": "ses_review",
                    "part": {
                        "id": f"start-{index}",
                        "type": "step-start",
                        "snapshot": snapshot,
                        "messageID": "msg_review",
                        "sessionID": "ses_review",
                    },
                }
            )
        rows.extend(
            [
                {
                    "type": "text",
                    "sessionID": "ses_review",
                    "part": {"id": "text", "type": "text", "messageID": "msg_review", "text": '{"ok":true}'},
                },
                {
                    "type": "step_finish",
                    "sessionID": "ses_review",
                    "part": {
                        "id": "finish",
                        "type": "step-finish",
                        "messageID": "msg_review",
                        "reason": "stop",
                        "snapshot": snapshots[-1],
                        "tokens": {"input": 1, "output": 1, "reasoning": 0, "cache": {"read": 0, "write": 0}},
                    },
                },
            ]
        )
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

    def test_spec_first_tester_may_write_tests_but_its_report_repair_may_not(self):
        # The job's own check limits what it leaves to test files (autocode_acceptance_tests_job).
        self.record.update(role="sol", stage="write_acceptance_tests")
        self.write_events(["a" * 40, "b" * 40])
        self.assertEqual({"ok": True}, runner.load_stage_report(self.record))
        self.record.update(stage="write_acceptance_tests_report_repair", report_only=True)
        self.write_events(["a" * 40, "b" * 40, "a" * 40])
        with self.assertRaises(support.Paused):
            runner.load_stage_report(self.record)

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
        subprocess.run(
            [
                "git",
                "-C",
                str(self.root),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.test",
                "commit",
                "--allow-empty",
                "-qm",
                "fixture",
            ],
            check=True,
        )
        self.write_events(["a" * 40, "b" * 40, "a" * 40])
        run = self.root / ".autocode/runs/recovery"
        run.mkdir(parents=True)
        before = run / "review.before.json"
        support.atomic_json(before, support.snapshot(self.root))
        self.record.update(before_ref=str(before), iteration=1, exit_code=0)
        state = {
            "version": 2,
            "workspace": str(self.root),
            "status": "RUNNING",
            "next_stage": "sol",
            "sessions": {},
            "stages": [],
            "history": [],
            "iteration": 1,
            "settings": {"engine": "opencode"},
            "active_stage": self.record,
        }
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
        rows.insert(
            1,
            {
                "type": "text",
                "sessionID": "ses_review",
                "part": {"type": "text", "snapshot": "b" * 40, "text": "repository changed"},
            },
        )
        rows.insert(
            2,
            {
                "type": "tool_use",
                "sessionID": "ses_review",
                "part": {"type": "tool", "snapshot": "b" * 40, "state": {"output": '"snapshot":"changed"'}},
            },
        )
        self.events.write_text("\n".join(json.dumps(row) for row in rows))
        self.assertIsNone(self.check_policy())

    def test_policy_does_not_compare_different_sessions(self):
        self.write_events(["a" * 40])
        with self.events.open("a") as sink:
            sink.write(
                "\n"
                + json.dumps(
                    {"type": "step_start", "sessionID": "other", "part": {"type": "step-start", "snapshot": "b" * 40}}
                )
            )
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
        self.assertEqual(
            1, subprocess.run(["git", "check-ignore", "-q", "pkg/probe_test.go"], cwd=self.root).returncode
        )

    def test_preserves_user_excludes_without_duplicate_writes(self):
        prefix = "# User setting\nlocal-secret.txt"
        self.excludes.write_text(prefix)
        self.prepare()
        prepared = self.excludes.read_bytes()
        self.assertTrue(prepared.startswith(prefix.encode() + b"\n"))
        self.prepare()
        self.assertEqual(prepared, self.excludes.read_bytes())

    def test_invalid_git_workspace_refuses_preparation(self):
        with patch(
            "autocode_readonly_events.subprocess.check_output", side_effect=subprocess.CalledProcessError(128, "git")
        ):
            with self.assertRaises(support.Paused):
                self.prepare()

    def snapshot_repository(self):
        """A provider's snapshot repository: its own git dir, core.worktree on the project (autocode_opencode_snapshots)."""
        snap = self.root.parent / (self.root.name + "-snapshot")
        subprocess.run(["git", "init", "-q", "--bare", str(snap)], check=True)
        subprocess.run(["git", f"--git-dir={snap}", "config", "core.bare", "false"], check=True)
        subprocess.run(["git", f"--git-dir={snap}", "config", "core.worktree", str(self.root)], check=True)
        self.addCleanup(shutil.rmtree, snap, True)
        return snap

    def ignored(self, snap, path, env=None):
        base = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.root / "home")}
        result = subprocess.run(
            ["git", f"--git-dir={snap}", "check-ignore", "-q", path], cwd=self.root, env={**base, **(env or {})}
        )
        return result.returncode == 0

    def test_the_snapshot_repository_reads_the_runner_excludes_only_through_the_environment(self):
        # #1630: a Kilo Investigator ran unittest in the workspace; the bytecode it left changed Kilo's
        # snapshot and the read-only guard paused the run. The project's info/exclude never reached that repository.
        for path in ("pkg/__pycache__/a.cpython-314.pyc", "pkg/a.pyc", ".autocode/evidence/x.json", "pkg/a.py"):
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
            (self.root / path).write_text("x")
        self.prepare()
        snap = self.snapshot_repository()
        self.assertFalse(self.ignored(snap, "pkg/__pycache__/a.cpython-314.pyc"), "info/exclude is the project's")
        policy = importlib.import_module("autocode_readonly_events")
        env = policy.snapshot_excludes(self.root, {"HOME": str(self.root / "home")})
        self.assertEqual(
            {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.excludesFile"},
            {key: value for key, value in env.items() if key != "GIT_CONFIG_VALUE_0"},
        )
        self.assertEqual(str(self.root.resolve() / ".autocode/snapshot-excludes"), env["GIT_CONFIG_VALUE_0"])
        for path in ("pkg/__pycache__/a.cpython-314.pyc", "pkg/a.pyc", ".autocode/evidence/x.json"):
            with self.subTest(path=path):
                self.assertTrue(self.ignored(snap, path, env))
        self.assertFalse(self.ignored(snap, "pkg/a.py", env))

    def test_the_users_global_ignore_rules_come_first_and_the_file_is_stable(self):
        home = self.root / "home"
        (home / ".config/git").mkdir(parents=True)
        (home / ".config/git/ignore").write_text("*.log\n")
        policy = importlib.import_module("autocode_readonly_events")
        env = policy.snapshot_excludes(self.root, {"HOME": str(home)})
        path = Path(env["GIT_CONFIG_VALUE_0"])
        self.assertEqual(
            ["*.log", "/.autocode/", "/.autocode-ui/", "__pycache__/", "*.pyc"], path.read_text().splitlines()
        )
        snap = self.snapshot_repository()
        (self.root / "debug.log").write_text("x")
        self.assertTrue(self.ignored(snap, "debug.log", env))
        before = path.stat().st_mtime_ns
        self.assertEqual(env, policy.snapshot_excludes(self.root, {"HOME": str(home)}))
        self.assertEqual(before, path.stat().st_mtime_ns, "an unchanged file is not rewritten")

    def test_excludes_env_appends_to_a_git_config_set_the_environment_keeps(self):
        policy = importlib.import_module("autocode_readonly_events")
        kept = {
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "credential.interactive",
            "GIT_CONFIG_VALUE_0": "false",
            "GIT_CONFIG_KEY_1": "core.excludesFile",
            "GIT_CONFIG_VALUE_1": "/elsewhere/ignore",
        }
        env = policy.snapshot_excludes(self.root, {**kept, "HOME": str(self.root / "home")})
        self.assertEqual(
            {"GIT_CONFIG_COUNT": "3", "GIT_CONFIG_KEY_2": "core.excludesFile"},
            {k: env[k] for k in ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_2")},
        )
        snapshots = importlib.import_module("autocode_opencode_snapshots")
        self.assertEqual(
            {**kept, **env}, snapshots.git_config_env({**kept, **env, "HOME": "x", "GIT_CONFIG_KEY_9": "stray"})
        )
        self.assertEqual({}, snapshots.git_config_env({"GIT_CONFIG_KEY_0": "orphan", "GIT_CONFIG_COUNT": "zero"}))

    def test_one_predicate_decides_the_guard_and_the_exclusions(self):
        snapshots = importlib.import_module("autocode_opencode_snapshots")
        policy = importlib.import_module("autocode_readonly_events")
        self.assertIs(policy.records_native_snapshots, snapshots.records_native_snapshots)
        self.assertTrue(snapshots.records_native_snapshots("opencode", "opencode_events"))  # OpenCode, Kilo
        self.assertTrue(snapshots.records_native_snapshots("opencode", None))  # a record saved before output_mode
        self.assertFalse(snapshots.records_native_snapshots("opencode", "report_file"))
        self.assertFalse(snapshots.records_native_snapshots("codex", "codex_events"))
        self.assertFalse(snapshots.records_native_snapshots(None, "opencode_events"))

    def test_launch_excludes_follows_the_predicate_and_fills_an_isolated_environment(self):
        policy = importlib.import_module("autocode_readonly_events")

        class Adapter:
            def __init__(self, output):
                self.OUTPUT = output

        home = {"HOME": str(self.root / "home")}
        self.assertEqual({}, policy.launch_excludes(self.root, "codex", Adapter("codex_events"), dict(home)))
        self.assertEqual({}, policy.launch_excludes(self.root, "opencode", Adapter("report_file"), dict(home)))
        self.assertEqual(
            {}, policy.launch_excludes(self.root, "opencode", Adapter("opencode_events"), None, dry_run=True)
        )
        isolated = dict(home)
        entries = policy.launch_excludes(self.root, "opencode", Adapter("opencode_events"), isolated)
        self.assertEqual("core.excludesFile", entries["GIT_CONFIG_KEY_0"])
        self.assertEqual({**home, **entries}, isolated, "an isolated launch's base environment gains the entries")
        self.assertTrue(Path(entries["GIT_CONFIG_VALUE_0"]).is_file())

    def test_prepare_aligns_a_kilo_record_and_leaves_a_report_file_tool_alone(self):
        policy = importlib.import_module("autocode_readonly_events")
        kilo = {"engine": "opencode", "output_mode": "opencode_events", "stage": "investigate_bug", "command": ["kilo"]}
        env = {"HOME": str(self.root / "home")}
        excludes = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.excludesFile", "GIT_CONFIG_VALUE_0": "/x"}
        policy.prepare_opencode_snapshots(self.root, record=kilo, env=env, excludes=excludes)
        self.assertIn("__pycache__/", self.excludes.read_text().splitlines())
        self.assertEqual("/x", env["GIT_CONFIG_VALUE_0"])
        self.excludes.unlink()
        env = {"HOME": str(self.root / "home")}
        tool = {**kilo, "output_mode": "report_file"}
        policy.prepare_opencode_snapshots(self.root, record=tool, env=env, excludes=excludes)
        self.assertFalse(self.excludes.exists())
        self.assertNotIn("GIT_CONFIG_COUNT", env)

    def test_prepares_a_linked_git_worktree(self):
        subprocess.run(
            [
                "git",
                "-C",
                str(self.root),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.test",
                "commit",
                "--allow-empty",
                "-qm",
                "fixture",
            ],
            check=True,
        )
        worktree = self.root / "linked"
        subprocess.run(["git", "-C", str(self.root), "worktree", "add", "-qb", "review", str(worktree)], check=True)
        policy = importlib.import_module("autocode_readonly_events")
        policy.prepare_opencode_snapshots(worktree)
        self.assertEqual(
            0, subprocess.run(["git", "check-ignore", "-q", ".autocode/evidence/check.json"], cwd=worktree).returncode
        )


if __name__ == "__main__":
    unittest.main()
