"""Source access is AutoCode's recovery, never a question about Git machinery."""

import copy
import importlib
import subprocess
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_client_actions as client
import autocode_run_view as run_view
import autocode_source_reconciliation as reconciliation
import autocode_status_observation as status_observation
import autocode_status_observation_worker as status_worker
import autocode_status_summary as summary
import autocode_util as util
import autocode_verify as verify

from .test_liveness import inspection, metadata


class SourceReconciliationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name).resolve()
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        (self.workspace / "source.txt").write_text("retained source\n")
        subprocess.run(["git", "-C", str(self.workspace), "add", "source.txt"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.workspace),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-qm",
                "seed",
            ],
            check=True,
        )
        self.run = self.workspace / ".autocode/runs/source-read"
        self.state = {
            "status": "PAUSED_SOURCE_UNAVAILABLE",
            "workspace": str(self.workspace),
            "run_dir": str(self.run),
            "stop_reason": "The source inventory could not be read",
            "next_stage": "review_plan",
            "settings": {"limits": {"max_cost_usd": 0.75}},
            "active_stage": {
                "stage": "review_plan",
                "role": "planner",
                "iteration": 1,
                "output": str(self.run / "iterations/001/review.json"),
                "events": str(self.run / "iterations/001/review.jsonl"),
                "finished_at": "2026-11-18T10:15:43Z",
                "exit_code": -15,
                "timed_out": True,
                "supervision": metadata(),
            },
        }
        self.fresh = inspection(
            self.state["active_stage"]["supervision"],
            owner=False,
            keeper=False,
            provider=False,
            phase="stopped",
            cause="provider_stopped",
        )

    def view(self, **kwargs):
        return run_view.view(
            self.state,
            workspace=self.workspace,
            run_dir=self.run,
            liveness=self.fresh,
            controller={"kind": "ended", "checked": True, "alive": False},
            **kwargs,
        )

    def test_package_import_checks_the_same_readable_source(self):
        package = importlib.import_module("tools.autocode_source_reconciliation")
        self.assertEqual("tools.autocode_source_snapshot", package.source_snapshot.__name__)
        self.assertTrue(package.readable(self.workspace, self.state))

    def test_readable_source_names_one_bound_continuation_for_all_stages(self):
        for stage in ("draft_plan", "review_plan", "builder", "review_change", "review_completion"):
            with self.subTest(stage=stage):
                self.state["active_stage"]["stage"] = self.state["next_stage"] = stage
                before = copy.deepcopy(self.state)
                view = self.view()
                named = client.named(view, self.workspace, self.run)
                self.assertEqual("continue", view["needs"]["kind"])
                self.assertEqual(["--resume-paused", "--expected-recovery-token", "-"], named.argv)
                self.assertTrue(named.env.get("AUTOCODE_EXPECTED_RECOVERY_TOKEN"))
                self.assertNotEqual("blocked", view["recovery"]["category"])
                self.assertEqual(before, self.state, "A source read never resumes or approves the run")

    def test_unreadable_source_has_no_person_or_replay_action(self):
        before = copy.deepcopy(self.state)
        with (
            patch("autocode_source_scope.snapshot", side_effect=util.Paused("PAUSED_SOURCE_UNAVAILABLE", "unreadable")),
            patch.object(
                status_observation,
                "_observe",
                side_effect=lambda op, payload: status_worker._worker({"operation": op, "payload": payload}),
            ),
        ):
            view = self.view()
        self.assertIsNone(view["needs"])
        self.assertIsNone(view["next_command"])
        self.assertEqual("blocked", view["recovery"]["category"])
        self.assertNotIn("resume", view["recovery"]["what_happened"].lower())
        self.assertEqual(before, self.state)

    def test_unsafe_copy_parent_is_not_offered_or_resumed_until_source_is_safe(self):
        shared = self.workspace / "shared"
        shared.mkdir()
        (shared / "test_original.py").write_text("original = True\n")
        subprocess.run(["git", "-C", str(self.workspace), "add", "shared"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.workspace),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=f@example.test",
                "commit",
                "-qm",
                "shared",
            ],
            check=True,
        )
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        external = Path(outside.name).resolve()
        (external / "test_original.py").write_text("external = True\n")
        shared.rename(self.workspace / "old-shared")
        shared.symlink_to(external, target_is_directory=True)
        with self.assertRaises(util.Paused) as held:
            verify.scratch_run(self.workspace, self.run / "copy", command="true", timeout=30)
        self.assertEqual("PAUSED_SOURCE_UNAVAILABLE", held.exception.status)
        self.state["stop_reason"] = str(held.exception)
        view = self.view()
        self.assertIsNone(view["next_command"])
        self.assertIsNone(view["needs"])
        self.state["stop_reason"] = "An unrelated source observation failed"
        self.assertIsNone(self.view()["next_command"], "Admission must not trust stop-reason wording")
        self.state["stop_reason"] = str(held.exception)
        runner = SimpleNamespace(
            native_calls=None,
            assert_stage_stopped=lambda _active: None,
            support=SimpleNamespace(snapshot=util.snapshot),
        )
        with self.assertRaises(util.Paused) as resumed:
            reconciliation.prepare(runner, self.state, self.workspace)
        self.assertEqual("PAUSED_SOURCE_UNAVAILABLE", resumed.exception.status)
        with (
            patch.object(reconciliation.source_scope, "paths", return_value=["shared/test_original.py"]),
            self.assertRaises(util.Paused) as scoped,
        ):
            reconciliation.prepare(runner, self.state, self.workspace)
        self.assertEqual("PAUSED_SOURCE_UNAVAILABLE", scoped.exception.status)
        self.assertIn("symlinked parent", str(scoped.exception))
        import autocode as actual_runner
        import autocode_action_recovery as recovery

        args = SimpleNamespace(
            resume_paused=True,
            abandon_stage=None,
            accept_source_edit=False,
            grant_recovery=None,
            retry_report=None,
            retry_failed_stage=False,
            diagnose_failed_stage=False,
            _resumed_pause={"status": self.state["status"]},
        )
        before = copy.deepcopy(self.state)
        with (
            patch.object(actual_runner, "assert_stage_stopped"),
            patch.object(actual_runner, "write_json") as write,
            patch.object(recovery.operational_admission, "guard", return_value=None),
            patch.object(recovery.recovery_dispatch, "recover", return_value=None),
            self.assertRaises(util.Paused),
        ):
            recovery.resume(
                actual_runner,
                args,
                None,
                self.state,
                self.run / "state.json",
                self.run,
                self.workspace,
                actual_runner.run_actions.Decided(),
            )
        write.assert_not_called()
        self.assertEqual(before, self.state)
        shared.unlink()
        (self.workspace / "old-shared").rename(shared)
        self.assertEqual("continue", self.view()["needs"]["kind"])

    def test_disposable_copy_corruption_does_not_block_a_fresh_source_check(self):
        self.state["stop_reason"] = "Source path has a symlinked parent: scratch-only/file.py"
        self.assertEqual("continue", self.view()["needs"]["kind"])

    def test_other_source_holds_still_recover_with_a_tracked_link_leaf(self):
        (self.workspace / "source-link.txt").symlink_to("source.txt")
        subprocess.run(["git", "-C", str(self.workspace), "add", "source-link.txt"], check=True)
        self.state["stop_reason"] = "The source inventory could not be read"
        self.assertEqual("continue", self.view()["needs"]["kind"])

    def test_unrelated_snapshot_errors_keep_their_original_type(self):
        runner = SimpleNamespace(
            native_calls=None,
            assert_stage_stopped=lambda _active: None,
            support=SimpleNamespace(snapshot=util.snapshot),
        )
        with (
            patch.object(reconciliation.source_scope, "snapshot", side_effect=ValueError("bad source contract")),
            self.assertRaisesRegex(ValueError, "bad source contract"),
        ):
            reconciliation.prepare(runner, self.state, self.workspace)

    def test_incomplete_snapshot_never_offers_or_accepts_source_recovery(self):
        runner = SimpleNamespace(
            native_calls=None,
            assert_stage_stopped=lambda _active: None,
            support=SimpleNamespace(snapshot=util.snapshot),
        )
        with (
            patch.object(reconciliation.source_scope, "snapshot", return_value={"revision": "incomplete"}),
            patch.object(reconciliation, "catalog_problem", return_value=None),
            patch.object(
                status_observation,
                "_observe",
                side_effect=lambda op, payload: status_worker._worker({"operation": op, "payload": payload}),
            ),
        ):
            self.assertIsNone(self.view()["next_command"])
            with self.assertRaises(util.Paused) as held:
                reconciliation.prepare(runner, self.state, self.workspace)
        self.assertEqual("PAUSED_SOURCE_UNAVAILABLE", held.exception.status)

    def test_a_readable_workspace_under_blocked_source_discovery_is_not_offered_as_readable(self):
        # Discovery under the shared source roots is the other cause of this stop; resuming while it still
        # blocks stops at the same place again, so the view must not offer the continuation.
        before = copy.deepcopy(self.state)
        with (
            patch("autocode_repo_catalog.blocking", return_value=[{"code": "symlink_escape", "path": "/r/link"}]),
            patch.object(
                status_observation,
                "_observe",
                side_effect=lambda op, payload: status_worker._worker({"operation": op, "payload": payload}),
            ),
        ):
            view = self.view()
        self.assertIsNone(view["needs"])
        self.assertIsNone(view["next_command"])
        self.assertEqual("blocked", view["recovery"]["category"])
        self.assertEqual(before, self.state)

    def test_status_observation_timeout_holds_until_a_later_fresh_read(self):
        with patch.object(status_observation.captured, "run", side_effect=subprocess.TimeoutExpired("worker", 15)):
            view = self.view()
        self.assertIsNone(view["next_command"])
        self.assertEqual("blocked", view["recovery"]["category"])
        self.assertEqual("continue", self.view()["needs"]["kind"])

    def test_a_live_controller_reconciling_a_saved_failure_is_not_shown_as_blocked(self):
        for status in ("PAUSED_SOURCE_UNAVAILABLE", "PAUSED_TOOL_CONTAINMENT", "PAUSED_VERIFICATION_UNCERTAIN"):
            with self.subTest(status=status):
                self.state["status"] = status
                before = copy.deepcopy(self.state)
                view = run_view.view(
                    self.state,
                    workspace=self.workspace,
                    run_dir=self.run,
                    liveness=self.fresh,
                    controller={"kind": "running", "checked": True, "alive": True},
                )
                self.assertEqual("working", summary.project(view)["state"])
                self.assertIsNone(
                    view["recovery"], "The retained failure is history while its controller reconciles it"
                )
                self.assertIsNone(view["needs"])
                self.assertIsNone(view["next_command"])
                self.assertEqual(before, self.state)

    def test_unknown_live_or_unclean_supervision_cannot_continue(self):
        for fault in ("owner", "keeper", "provider", "unknown", "unclean", "stop_pending", "native"):
            with self.subTest(fault=fault):
                fresh, state = copy.deepcopy(self.fresh), copy.deepcopy(self.state)
                if fault in ("owner", "keeper", "provider"):
                    fresh[fault]["alive"] = True
                elif fault == "unknown":
                    fresh["keeper"] = {"checked": False, "alive": None}
                elif fault == "unclean":
                    fresh["receipt"]["cleanup_error"] = "family not confirmed stopped"
                elif fault == "native":
                    state["ui_run"] = "/original/native-handoff"
                view = run_view.view(
                    state,
                    workspace=self.workspace,
                    run_dir=self.run,
                    liveness=fresh,
                    controller={"kind": "ended", "checked": True, "alive": False},
                    stop_pending=fault == "stop_pending",
                )
                self.assertIsNone(view["next_command"])

    def test_a_prelaunch_source_read_can_recover_without_an_attempt(self):
        self.state.pop("active_stage")
        view = self.view()
        self.assertEqual("continue", view["needs"]["kind"])
        self.assertIn("--resume-paused", view["next_command"])

    def test_a_separate_cleanup_hold_cannot_be_bypassed_by_readable_source(self):
        failure = {"title": "Cleanup unconfirmed", "category": "blocked", "what_happened": "Cleanup is unconfirmed"}
        with patch.object(run_view.operational_block, "presentation", return_value=failure):
            view = self.view()
        self.assertIsNone(view["next_command"])
        self.assertEqual("Cleanup unconfirmed", view["recovery"]["title"])

    def test_source_recovery_does_not_renew_resolver_or_report_allowances(self):
        import autocode as runner
        import autocode_action_recovery as recovery

        args = SimpleNamespace(
            resume_paused=True,
            abandon_stage=None,
            accept_source_edit=False,
            grant_recovery=None,
            retry_report=None,
            retry_failed_stage=False,
            diagnose_failed_stage=False,
            _resumed_pause={"status": self.state["status"]},
        )
        self.state.update(
            active_seconds=125,
            automatic_recoveries_since_resume=2,
            pending_report_repair={"attempts": 2},
        )
        before = copy.deepcopy(self.state)
        decided = runner.run_actions.Decided()
        with ExitStack() as patches:
            for name in (
                "assert_stage_stopped",
                "prepare_abandoned_completion_revalidation",
                "prepare_timeout_archived_completion",
                "prepare_planning_retry",
                "prepare_exhausted_execution_report_retry",
                "archive_stale_report_repair",
                "repeated_failure_resume_guard",
                "reconcile_active",
                "write_json",
            ):
                patches.enter_context(patch.object(runner, name, return_value=None))
            patches.enter_context(patch.object(runner.report_revalidation, "revalidate", return_value=False))
            patches.enter_context(patch.object(recovery.operational_admission, "guard", return_value=None))
            patches.enter_context(
                patch.object(runner.resolver_runtime, "reset_for_resume", side_effect=AssertionError("no renewal"))
            )
            patches.enter_context(
                patch.object(runner, "reset_report_repair_for_resume", side_effect=AssertionError("no renewal"))
            )
            recovery.resume(runner, args, None, self.state, self.run / "state.json", self.run, self.workspace, decided)
            runner.reconcile_active.assert_called_once()
        self.assertTrue(decided.evidence_reconciled)
        self.assertIsNone(args._resumed_pause, "Source recovery is a runner action, not a person's resume")
        note = self.state.pop("reconciliation_notes")[0]
        self.assertEqual(
            ("runner", False, before["stop_reason"]), (note["actor"], note["allowances_renewed"], note["reason"])
        )
        self.state.pop("recovery_context")  # Existing resume's empty context, not additional retry credit.
        self.assertEqual(before, self.state)
