"""Source access is AutoCode's recovery, never a question about Git machinery."""

import copy
import subprocess
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_client_actions as client
import autocode_run_view as run_view
import autocode_status_summary as summary
import autocode_util as util

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
        with patch(
            "autocode_source_scope.snapshot", side_effect=util.Paused("PAUSED_SOURCE_UNAVAILABLE", "unreadable")
        ):
            view = self.view()
        self.assertIsNone(view["needs"])
        self.assertIsNone(view["next_command"])
        self.assertEqual("blocked", view["recovery"]["category"])
        self.assertNotIn("resume", view["recovery"]["what_happened"].lower())
        self.assertEqual(before, self.state)

    def test_a_readable_workspace_under_blocked_source_discovery_is_not_offered_as_readable(self):
        # Discovery under the shared source roots is the other cause of this stop; resuming while it still
        # blocks stops at the same place again, so the view must not offer the continuation.
        before = copy.deepcopy(self.state)
        with patch("autocode_repo_catalog.blocking", return_value=[{"code": "symlink_escape", "path": "/r/link"}]):
            view = self.view()
        self.assertIsNone(view["needs"])
        self.assertIsNone(view["next_command"])
        self.assertEqual("blocked", view["recovery"]["category"])
        self.assertEqual(before, self.state)

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
