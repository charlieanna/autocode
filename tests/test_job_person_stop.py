"""An intentional workflow-job stop retains work without becoming a model failure."""

import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autocode_bundle as bundle
import autocode_interventions as interventions
import autocode_job_failure as failure
import autocode_job_source as source
import autocode_jobs as jobs
import autocode_run_records as records
import autocode_stop as stop
import autocode_support as support
import autocode_util as util


class JobPersonStopTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="job-person-stop-")
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve() / "project"
        self.workspace.mkdir()
        (self.workspace / ".gitignore").write_text(".autocode/\n")
        (self.workspace / "product.py").write_text("original = True\n")
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run(["git", "-C", str(self.workspace), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.workspace),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=f@test",
                "commit",
                "-qm",
                "original",
            ],
            check=True,
        )
        self.run = self.workspace / ".autocode/runs/test"
        self.run.mkdir(parents=True)

    def prepared(
        self, stage, *, kind="person", partial_name="new_test.py", content=b"# exact partial test\nassert False\n"
    ):
        base = self.run / "iterations/001" / stage
        base.parent.mkdir(parents=True, exist_ok=True)
        before = util.snapshot(self.workspace)
        util.atomic_json(base.with_suffix(".before.json"), before)
        base.with_suffix(".jsonl").write_text('{"type":"thread.started","thread_id":"offline"}\n')
        record = {
            "stage": stage,
            "role": "tester",
            "route_role": "acceptance_tester",
            "iteration": 1,
            "output": str(base.with_suffix(".json")),
            "events": str(base.with_suffix(".jsonl")),
            "before_ref": str(base.with_suffix(".before.json")),
            "pid": 123,
            "started_at": "2026-11-14T00:00:00+00:00",
            "finished_at": "2026-11-14T00:00:01+00:00",
            "timed_out": True,
            "timeout_kind": kind,
            "timeout_reason": "Deliberately unrelated prose",
            "stage_timeout_seconds": 600,
            "exit_code": -15,
        }
        source.capture(self.workspace, base, record, before)
        partial = self.workspace / partial_name
        partial.parent.mkdir(parents=True, exist_ok=True)
        partial.write_bytes(content)
        source.stopped(self.workspace, base, record)
        state = {
            "status": "RUNNING",
            "task": "Verify an intentionally stopped workflow job",
            "phase": "EXECUTING",
            "next_stage": stage,
            "settings": {
                "engine": "codex",
                "roles": {"acceptance_tester": {"model": "fixture", "reasoning_effort": "high"}},
                "limits": {"stage_timeout_seconds": 600, "max_seconds": 3600},
            },
            "workspace": str(self.workspace),
            "run_dir": str(self.run),
            "sessions": {"acceptance_tester": "old-session"},
            "active_stage": record,
        }
        runtime = SimpleNamespace(
            support=support,
            stage_completed=Mock(return_value=False),
            assert_stage_stopped=Mock(),
            attempt_id=records.attempt_id,
            account_stage=Mock(),
            archive_rejected_stage=records.archive_rejected_stage,
            write_json=util.atomic_json,
            now=util.now,
        )
        return state, record, runtime

    def recover(self, state, runtime):
        with patch.object(failure.quota_route, "stopped_attempt", return_value=None):
            return failure.recover(
                runtime, state, self.run, self.workspace, util.Paused("PAUSED_PROVIDER_TIMEOUT", "stop")
            )

    def test_every_workflow_job_preserves_stopped_bytes_and_returns_to_a_person_pause(self):
        for stage in jobs.STAGES:
            with self.subTest(stage=stage):
                state, record, runtime = self.prepared(stage)
                settings = copy.deepcopy(state["settings"])
                original = record["job_source"]["before_identity"]
                self.assertTrue(self.recover(state, runtime))
                self.assertEqual("PAUSED_INTERVENTION", state["status"])
                self.assertEqual(stop.STOPPED_NOW_REASON, state["stop_reason"])
                self.assertEqual(stage, state["next_stage"])
                self.assertEqual(settings, state["settings"])
                self.assertNotIn("job_failure", state)
                self.assertNotIn("job_retry_authorization", state)
                self.assertNotIn("active_stage", state)
                self.assertEqual("person", record["job_failure_kind"])
                self.assertEqual(original, record["job_source"]["before_identity"])
                self.assertTrue(source.matches_original(self.workspace, original))
                self.assertFalse((self.workspace / "new_test.py").exists())
                kept = record["job_source"]["stopped_capture"]
                self.assertEqual(kept["sha256"], util.file_hash(Path(kept["path"])))
                self.assertNotIn("stopped_capture", state["recovery_context"])
                self.assertNotIn(kept["sha256"], json.dumps(state["recovery_context"]))
                job = next(job for job in jobs.JOBS if stage == job.STAGE)
                state["design_intake"] = {"references": []}
                state["stuck_investigation"] = {
                    "stage": "builder",
                    "status": "PAUSED_REPEATED_FAILURE",
                    "reason": "fixture",
                    "identity": "fixture",
                }
                prompt, _ = (
                    job.prompt(state, self.run / "state.json") if stage == "investigate_stuck" else job.prompt(state)
                )
                self.assertNotIn(kept["sha256"], prompt)
                self.assertNotIn('"stopped_capture"', prompt)
                manifest = json.loads(Path(kept["path"]).read_text())
                self.assertEqual("unverified", manifest["verification"])
                self.assertEqual(
                    b"# exact partial test\nassert False\n", Path(manifest["blobs"]["new_test.py"]).read_bytes()
                )
                self.assertEqual(1, runtime.account_stage.call_count)
                self.assertEqual([record], state["stages"])

    def test_person_classification_uses_structured_metadata_not_prose(self):
        runtime = SimpleNamespace(support=support)
        for kind in ("person", "stage", "idle", "tool"):
            with self.subTest(kind=kind):
                record = {
                    "stage": "write_acceptance_tests",
                    "timed_out": True,
                    "timeout_kind": kind,
                    "timeout_reason": "Stopped by you",
                }
                self.assertEqual("person" if kind == "person" else "timeout", failure._reason(runtime, record, None)[0])
        for malformed in (1, "true"):
            record.update(timed_out=malformed, timeout_kind="person")
            self.assertEqual("timeout", failure._reason(runtime, record, None)[0])

    def test_unknown_cleanup_does_not_archive_restore_or_clear_the_attempt(self):
        state, record, runtime = self.prepared("write_acceptance_tests")
        runtime.assert_stage_stopped.side_effect = util.Paused("PAUSED_PROCESS_CLEANUP", "unknown")
        self.assertFalse(self.recover(state, runtime))
        self.assertIs(record, state["active_stage"])
        self.assertTrue((self.workspace / "new_test.py").exists())
        self.assertNotIn("stages", state)
        runtime.account_stage.assert_not_called()

    def test_recovery_itself_applies_the_exact_accepted_pause_after_archival(self):
        state, record, runtime = self.prepared("write_acceptance_tests")
        util.atomic_json(self.run / "state.json", state)
        receipt = interventions.submit(
            self.workspace, self.run, request_id="writer-stop", kind="pause", text="", now=True
        )["receipt"]
        saved = []

        def persist(path, value):
            saved.append(copy.deepcopy(value))
            util.atomic_json(path, value)

        runtime.write_json = persist
        self.assertTrue(self.recover(state, runtime))
        self.assertNotIn("active_stage", saved[0])
        self.assertTrue(saved[0]["stages"])
        self.assertNotIn("applied_interventions", saved[0])
        self.assertEqual([receipt["id"]], [row["id"] for row in state["applied_interventions"]])
        self.assertEqual([], interventions.pending(self.run))
        self.assertEqual("PAUSED_INTERVENTION", state["status"])
        self.assertNotIn("held_pause", state["pause_intent"])
        self.assertNotIn("job_failure", state)

    def test_changed_stopped_source_is_retained_and_cannot_become_a_plain_resume(self):
        state, record, runtime = self.prepared("write_acceptance_tests")
        util.atomic_json(self.run / "state.json", state)
        receipt = interventions.submit(
            self.workspace, self.run, request_id="unsafe-source-stop", kind="pause", text="", now=True
        )["receipt"]
        (self.workspace / "new_test.py").write_text("later unrelated change\n")
        self.assertTrue(self.recover(state, runtime))
        self.assertEqual("PAUSED_JOB_FAILURE", state["status"])
        self.assertEqual("PAUSED_JOB_FAILURE", util.read_object(self.run / "state.json")["status"])
        self.assertEqual("person", state["job_failure"]["kind"])
        self.assertNotIn("route", state["job_failure"])
        self.assertTrue(state["job_failure"]["unrestored"])
        self.assertEqual("later unrelated change\n", (self.workspace / "new_test.py").read_text())
        self.assertEqual([record], state["stages"])
        self.assertEqual([receipt["id"]], [item["id"] for item in interventions.pending(self.run)])
        self.assertNotIn("applied_interventions", state)

    def test_failed_restoration_keeps_the_exact_original_source_retry_gate(self):
        state, record, runtime = self.prepared("write_acceptance_tests")
        with patch.object(
            source, "restore", return_value={"restored": [], "deleted": [], "unrestored": ["new_test.py"]}
        ):
            self.assertTrue(self.recover(state, runtime))
        self.assertEqual("PAUSED_JOB_FAILURE", state["status"])
        self.assertEqual("person", state["job_failure"]["kind"])
        self.assertNotIn("route", state["job_failure"])
        self.assertNotIn("pause_status", state["job_failure"])
        self.assertEqual(record["job_source"]["before_identity"], state["job_failure"]["source_identity"])
        self.assertEqual(["new_test.py"], state["job_failure"]["unrestored"])
        with self.assertRaisesRegex(ValueError, "Unrestored source"):
            failure._validate_retry(state, self.workspace, state["job_failure"]["job_retry_token"])

    def test_lost_original_capture_keeps_partial_work_and_the_original_source_hold(self):
        state, record, runtime = self.prepared("write_acceptance_tests")
        Path(record["job_source"]["capture"]).unlink()
        self.assertTrue(self.recover(state, runtime))
        self.assertEqual("PAUSED_JOB_FAILURE", state["status"])
        self.assertEqual("PAUSED_JOB_FAILURE", util.read_object(self.run / "state.json")["status"])
        self.assertTrue(state["job_failure"]["unrestored"])
        self.assertTrue((self.workspace / "new_test.py").exists())
        self.assertEqual([record], state["stages"])

    def test_verified_original_source_needs_no_lost_capture_or_partial_copy(self):
        state, record, runtime = self.prepared("write_acceptance_tests")
        (self.workspace / "new_test.py").unlink()
        Path(record["job_source"]["capture"]).unlink()
        Path(record["job_source"]["witness"]).unlink()
        self.assertTrue(self.recover(state, runtime))
        self.assertEqual("PAUSED_INTERVENTION", util.read_object(self.run / "state.json")["status"])
        self.assertNotIn("job_failure", state)
        self.assertNotIn("stopped_capture", record["job_source"])
        self.assertTrue(record["write_diagnosis"]["original_identity_verified"])

    def test_stopped_copy_never_reads_a_replaced_final_or_parent_symlink(self):
        for parent_link in (False, True):
            with self.subTest(parent_link=parent_link):
                stage = "collect_design" if parent_link else "write_acceptance_tests"
                name = "nested/new_test.py" if parent_link else "new_test.py"
                state, record, runtime = self.prepared(stage, partial_name=name)
                outside = self.workspace.parent / ("outside-parent" if parent_link else "outside-file")
                if parent_link:
                    outside.mkdir()
                    (outside / "new_test.py").write_text("not task-owned bytes\n")
                else:
                    outside.write_text("not task-owned bytes\n")
                real_open = os.open

                def replace_before_open(path, *args, **kwargs):
                    if path == ("nested" if parent_link else "new_test.py"):
                        target = self.workspace / path
                        if parent_link:
                            (target / "new_test.py").unlink()
                            target.rmdir()
                        else:
                            target.unlink()
                        target.symlink_to(outside, target_is_directory=parent_link)
                    return real_open(path, *args, **kwargs)

                with patch.object(source.os, "open", side_effect=replace_before_open):
                    self.assertTrue(self.recover(state, runtime))
                self.assertEqual("PAUSED_JOB_FAILURE", state["status"])
                self.assertTrue(
                    state["job_failure"]["write_diagnosis"]["retention_error"].startswith(
                        ("OSError:", "NotADirectoryError:")
                    )
                )
                self.assertTrue(state["job_failure"]["unrestored"])
                self.assertEqual([record], state["stages"])
                (self.workspace / ("nested" if parent_link else "new_test.py")).unlink()

    def test_an_actual_stage_timeout_keeps_its_existing_failure_and_does_not_preserve_person_bytes(self):
        state, record, runtime = self.prepared("write_acceptance_tests", kind="idle")
        self.assertTrue(self.recover(state, runtime))
        self.assertEqual("PAUSED_JOB_FAILURE", state["status"])
        self.assertEqual("timeout", state["job_failure"]["kind"])
        self.assertNotIn("stopped_capture", record["job_source"])

    def test_support_bundle_keeps_only_redacted_metadata_not_the_retained_source_bytes(self):
        secret = "fixture-environment-secret-canary"
        raw = b"# private stopped product source\n" + secret.encode() + b"\n"
        state, record, runtime = self.prepared("write_acceptance_tests", content=raw)
        state["settings"]["fixture_secret"] = secret
        self.assertTrue(self.recover(state, runtime))

        def no_recording(*args):
            raise ValueError("No model recording in this pure fixture")

        files, _ = bundle.collect(
            self.run, environ={"FIXTURE_SECRET": secret}, doctor=lambda _: {}, recording=no_recording
        )
        kept = record["job_source"]["stopped_capture"]
        self.assertIn("run/" + str(Path(kept["path"]).relative_to(self.run)), files)
        for name, data in files.items():
            self.assertNotIn(raw, data, name)
            self.assertNotIn(secret.encode(), data, name)
        self.assertIn(b"[REDACTED]", files["settings.json"])
        retained = json.loads(Path(kept["path"]).read_text())
        blob = Path(retained["blobs"]["new_test.py"])
        self.assertEqual(raw, blob.read_bytes())
        self.assertNotIn("run/" + str(blob.relative_to(self.run)), files)


if __name__ == "__main__":
    unittest.main()
