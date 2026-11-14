"""A collected pre-provider Git failure is AutoCode's outcome, never a human gate."""

import copy
import subprocess
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autocode_verification_copy as copies


class AllocationFailureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name).resolve()
        self.run = self.workspace / ".autocode/runs/example"
        self.run.mkdir(parents=True)
        self.original = subprocess.CalledProcessError(
            128, ["/usr/bin/git", "fast-import"], stderr=b"fatal: fixture failure"
        )

    def fail(self, *args, **kwargs):
        context = kwargs.get("_preparation_context")
        if context is not None:
            context.update(source_revision="source-bytes", source_paths=[])
        raise self.original

    def test_collected_git_failure_preserves_cause_and_confirms_owned_cleanup(self):
        with patch.object(copies, "create", side_effect=self.fail):
            with self.assertRaises(subprocess.CalledProcessError) as raised:
                copies.allocate(self.workspace, self.run)
        failure = raised.exception
        self.assertTrue(getattr(failure, "cleanup_confirmed", False))
        self.assertEqual(failure.stderr, self.original.stderr)
        self.assertEqual(failure.cmd, self.original.cmd)
        self.assertEqual(failure.source_revision, "source-bytes")
        self.assertEqual(failure.workspace, str(self.workspace))
        self.assertEqual(failure.run_dir, str(self.run))
        self.assertFalse(list(self.run.iterdir()))

    def test_cleanup_error_keeps_original_git_failure_and_owned_copy(self):
        with (
            patch.object(copies, "create", side_effect=self.fail),
            patch.object(copies.shutil, "rmtree", side_effect=OSError("cleanup uncertain")),
        ):
            with self.assertRaises(subprocess.CalledProcessError) as raised:
                copies.allocate(self.workspace, self.run)
        failure = raised.exception
        self.assertFalse(failure.cleanup_confirmed)
        self.assertIn("cleanup uncertain", failure.cleanup_error)
        self.assertEqual(failure.stderr, self.original.stderr)
        self.assertEqual(len(list(self.run.iterdir())), 1)

    def test_replaced_control_is_not_removed_or_reported_clean(self):
        moved = self.run / "retained-original"

        def replace(*args, **kwargs):
            control = Path(args[1]).parent
            control.rename(moved)
            control.symlink_to(moved, target_is_directory=True)
            raise self.original

        with patch.object(copies, "create", side_effect=replace):
            with self.assertRaises(subprocess.CalledProcessError) as raised:
                copies.allocate(self.workspace, self.run)
        self.assertFalse(raised.exception.cleanup_confirmed)
        self.assertTrue(moved.is_dir())
        self.assertTrue(Path(raised.exception.control).is_symlink())

    def test_source_drift_is_not_a_git_prelaunch_failure(self):
        with patch.object(copies, "create", side_effect=ValueError("Source changed while copying")):
            with self.assertRaisesRegex(ValueError, "Source changed"):
                copies.allocate(self.workspace, self.run)


class PreparationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name).resolve()
        self.run = self.workspace / ".autocode/runs/example"
        self.run.mkdir(parents=True)
        self.state = {
            "workspace": str(self.workspace),
            "run_dir": str(self.run),
            "task_id": "task",
            "current_task": {"id": "T1"},
            "goal_contract": {"body": {"outcome": "approved"}},
            "status": "RUNNING",
            "next_stage": "tester",
            "iteration": 1,
            "stages": [],
            "history": [],
        }
        self.saved = []
        self.runtime = SimpleNamespace(write_json=lambda path, state: self.saved.append(copy.deepcopy(state)))
        current_source = patch(
            "autocode_verification_failure_recovery.source.snapshot", return_value={"revision": "source-bytes"}
        )
        self.source_snapshot = current_source.start()
        self.addCleanup(current_source.stop)

    def error(self, *, clean=True, revision="source-bytes", run=None):
        import autocode_verification_failure as failure

        original = subprocess.CalledProcessError(128, ["/usr/bin/git", "fast-import"], stderr=b"fatal: fixture failure")
        fact = failure.GitPreparationFailed(
            original,
            workspace=self.workspace,
            run_dir=run or self.run,
            control=self.run / ("tool-containment-" + "1" * 32),
            source_revision=revision,
            source_paths=[],
            cleanup_confirmed=clean,
            cleanup_error=None if clean else "unknown cleanup",
        )
        return failure.PrelaunchFailure("Verification copy was not prepared: fatal: fixture failure", fact)

    def recover(self, error):
        import autocode_verification_failure_recovery as recovery

        return recovery.recover(self.runtime, self.state, self.run, self.workspace, error)

    def test_quiet_confirmed_cleanup_ends_without_retry_and_preserves_approval(self):
        import autocode_stop as stop
        import autocode_util as util

        approved = copy.deepcopy(self.state["goal_contract"])
        with self.assertRaises(util.Paused):
            self.recover(self.error())
        self.assertEqual(stop.outcome(self.state)["result"], "could_not_finish")
        self.assertIn("fatal: fixture failure", self.state["stop_reason"])
        self.assertEqual(self.state["goal_contract"], approved)
        self.assertEqual(len(self.state["stages"]), 1)
        self.assertEqual(self.state["stages"][0]["runner_calls"], 0)
        self.assertEqual(self.state["stages"][0]["receipt"]["evidence"]["binding"]["current_task"], {"id": "T1"})
        self.assertEqual(len(self.saved), 1)

    def test_unknown_cleanup_source_and_every_unsettled_owner_stay_blocked(self):
        import autocode_recovery_limits as limits
        import autocode_stop as stop
        import autocode_util as util
        import autocode_verification_failure as recovery

        for reason in ("cleanup", "source", *limits.UNSETTLED):
            with self.subTest(reason=reason):
                self.state.pop("applied_interventions", None)
                self.state["status"] = "RUNNING"
                for key in limits.UNSETTLED:
                    self.state.pop(key, None)
                if reason in limits.UNSETTLED:
                    self.state[reason] = {"owned": "retained"}
                error = self.error(clean=reason != "cleanup", revision=None if reason == "source" else "source-bytes")
                with self.assertRaises(util.Paused):
                    self.recover(error)
                self.assertIsNone(stop.outcome(self.state))
                self.assertIsNotNone(recovery.blocked(self.state))
                if reason in limits.UNSETTLED:
                    self.assertEqual(self.state[reason], {"owned": "retained"})

    def test_changed_or_unreadable_source_after_collection_is_blocked(self):
        import autocode_stop as stop
        import autocode_util as util
        import autocode_verification_failure as failure

        for changed in (True, False):
            with self.subTest(changed=changed):
                self.source_snapshot.side_effect = None if changed else OSError("source no longer readable")
                self.source_snapshot.return_value = {"revision": "later-source"}
                with self.assertRaises(util.Paused):
                    self.recover(self.error())
                self.assertIsNone(stop.outcome(self.state))
                recorded = failure.blocked(self.state)["receipt"]["evidence"]
                self.assertEqual(recorded["binding"]["source_revision"], "source-bytes")
                self.assertEqual(recorded["current_source_revision"], "later-source" if changed else None)
                self.assertEqual(bool(recorded["source_check_error"]), not changed)

    def test_untyped_pause_and_foreign_run_never_get_terminal_authority(self):
        import autocode_stop as stop
        import autocode_util as util

        self.assertFalse(self.recover(util.Paused("PAUSED_STALE_VALIDATION", "Verification copy was not prepared")))
        with self.assertRaises(util.Paused):
            self.recover(self.error(run=self.run.parent / "other"))
        self.assertIsNone(stop.outcome(self.state))

    def test_public_failure_and_blocked_hold_never_publish_a_person_action(self):
        import autocode_attention as attention
        import autocode_run_view as view
        import autocode_util as util

        for clean in (False, True):
            with self.subTest(clean=clean):
                self.state.pop("applied_interventions", None)
                self.state["status"] = "RUNNING"
                with self.assertRaises(util.Paused):
                    self.recover(self.error(clean=clean))
                public = view.view(self.state)
                self.assertFalse(public["done"])
                self.assertIsNone(public["needs"])
                self.assertIsNone(public["next_command"])
                self.assertEqual(public["recovery"]["category"], "stopped" if clean else "blocked")
                self.assertIsNone(
                    attention.project(
                        self.state, public, workspace=self.workspace, run_dir=self.run, context="context"
                    )["item"]
                )
                self.assertNotIn("person", public["explanation"]["what_it_means"])

    def test_status_and_settings_changes_cannot_clear_unconfirmed_cleanup(self):
        import autocode_action_recovery as action
        import autocode_run_view as view
        import autocode_util as util
        import autocode_verification_failure as failure

        with self.assertRaises(util.Paused):
            self.recover(self.error(clean=False))
        self.state.update(
            status="RUNNING", phase="DISCOVERING", next_stage="requirements_gather", settings={"joint_planning": True}
        )
        self.state.pop("stop_reason", None)  # The enabling-joint settings path does this too.
        retained = copy.deepcopy(self.state)
        self.assertIsNotNone(failure.blocked(self.state))
        public = view.view(self.state)
        self.assertIsNone(public["needs"])
        self.assertIsNone(public["next_command"])
        self.assertFalse(public["done"])
        self.assertEqual(public["recovery"]["category"], "blocked")
        self.assertEqual([row["kind"] for row in public["recovery"]["actions"]], ["inspect"])
        result = action.resume(
            self.runtime,
            SimpleNamespace(abandon_stage=None, resume_paused=True),
            None,
            self.state,
            self.run / "state.json",
            self.run,
            self.workspace,
            SimpleNamespace(gate=None, information_admitted=False),
        )
        self.assertEqual(result, 2)
        self.assertEqual(self.state["status"], "PAUSED_VERIFICATION_PREPARATION")
        self.assertEqual(self.state["phase"], "PAUSED_OR_BLOCKED")
        self.assertEqual(self.saved[-1], self.state)
        self.assertEqual(self.state["stop_reason"], failure.blocked(self.state)["summary"])
        self.assertEqual(
            {key: value for key, value in self.state.items() if key not in ("status", "phase", "stop_reason")},
            {key: value for key, value in retained.items() if key not in ("status", "phase", "stop_reason")},
        )
        self.source_snapshot.assert_not_called()

    def test_failure_decisions_are_shown_as_words_without_internal_action_names(self):
        import autocode_run_view as view
        import autocode_util as util

        with self.assertRaises(util.Paused):
            self.recover(self.error(clean=False))
        text = str(view.view(self.state)["recoveries"])
        self.assertIn("remain blocked on verification preparation", text)
        with self.assertRaises(util.Paused):
            self.recover(self.error())
        text = str(view.view(self.state)["recoveries"])
        self.assertIn("end the task without completion", text)
        self.assertNotIn("could_not_finish", text)

    def test_manual_resume_cannot_clear_a_retained_unknown_cleanup_hold(self):
        import autocode_action_recovery as action
        import autocode_util as util

        with self.assertRaises(util.Paused):
            self.recover(self.error(clean=False))
        before = copy.deepcopy(self.state)
        args = SimpleNamespace(abandon_stage=None, resume_paused=True)
        result = action.resume(
            SimpleNamespace(),
            args,
            None,
            self.state,
            self.run / "state.json",
            self.run,
            self.workspace,
            SimpleNamespace(gate=None, information_admitted=False),
        )
        self.assertEqual(result, 2)
        self.assertEqual(self.state, before)

    def test_blocked_public_owner_is_retained_and_summary_is_not_terminal(self):
        import autocode_progress_view as progress
        import autocode_run_view as view
        import autocode_status_summary as summary
        import autocode_util as util
        import autocode_verification_failure as failure

        self.state["active_stage"] = {"stage": "builder", "engine": "codex", "iteration": 1}
        self.state["orchestration_batch"] = {"workers": [{"status": "RUNNING", "pid": 1234}]}
        owned = copy.deepcopy((self.state["active_stage"], self.state["orchestration_batch"]))
        with self.assertRaises(util.Paused):
            self.recover(self.error())
        public = view.view(self.state)
        self.assertIsNone(public["needs"])
        self.assertIsNone(public["next_command"])
        self.assertIsNone(public["recovery"]["outcome"])
        self.assertEqual([row["kind"] for row in public["recovery"]["actions"]], ["inspect"])
        projected = summary.project(public, stale=True)
        self.assertEqual((projected["state"], projected["label"]), ("blocked", "Blocked"))
        self.assertNotIn("nothing supervises", projected["sentence"])
        observed = progress.progress(
            self.state, accepted=(), stage="Builder", needs=None, internal_failure=failure.presentation(self.state)
        )
        self.assertEqual(observed["headline"], "AutoCode is blocked on verification preparation")
        self.assertEqual(observed["needs_you"], "nothing needed from you")
        self.assertEqual((self.state["active_stage"], self.state["orchestration_batch"]), owned)

    def test_untyped_matching_text_and_model_record_do_not_change_existing_gates(self):
        import autocode_run_view as view

        self.state.update(status="PAUSED_STALE_VALIDATION", stop_reason="Verification copy was not prepared")
        self.assertEqual(view.needs(self.state)["kind"], "resume")
        self.state.update(
            status="PAUSED_VERIFICATION_PREPARATION",
            resolver={
                "operational_diagnostics": ["model"],
                "operational_receipts": {
                    "model": {
                        "runner_owned": False,
                        "engine": "model",
                        "decision": {"action": "blocked"},
                        "receipt": {"scope": "verification_preparation"},
                    }
                },
            },
        )
        self.assertEqual(view.needs(self.state)["kind"], "resume")

    def native_dispatch(self, error):
        import autocode_build_loop as loop

        class LoopExit(Exception):
            pass

        request = SimpleNamespace(
            role="tester",
            route_role="tester",
            prompt="review",
            metrics={"estimated_prompt_tokens": 0, "soft_budget_tokens": 10},
            schema={},
            allow_write=False,
        )
        self.state["settings"] = {"roles": {"tester": {"model": "saved-model"}}}
        self.runtime.native_calls = Mock()
        self.runtime.orchestrator = SimpleNamespace(
            LoopExit=LoopExit, drive=lambda current, dispatch, **kwargs: dispatch(current, "tester")
        )
        self.runtime.SCHEMA_DIR = self.run / "schemas"
        self.runtime.rotate_if_needed = Mock()
        self.runtime.run_role = Mock(side_effect=error)
        self.runtime.ReportRepairQueued = type("ReportRepairQueued", (Exception,), {})
        args = SimpleNamespace(unit=None, chat=False, pause_after_stage=False)
        with ExitStack() as stack:
            for module, name in (
                (loop.model_preflight, "check"),
                (loop.progressive, "guard_dispatch"),
                (loop.autopilot.builder_failure, "dispatch_guard"),
                (loop.workflow, "dispatch_guard"),
                (loop.autopilot, "admit_validation"),
                (loop.completion_result, "before_review"),
                (loop.launch_inputs, "guard"),
                (loop.climb_cap, "guard"),
            ):
                stack.enter_context(patch.object(module, name))
            stack.enter_context(patch.object(loop.storage_budget, "conversation_only", return_value=True))
            stack.enter_context(patch.object(loop.planning, "is_planning", return_value=False))
            stack.enter_context(patch.object(loop.autopilot, "prepare_request", return_value=request))
            general = stack.enter_context(patch.object(loop.recovery_dispatch, "recover"))
            try:
                loop.run(self.runtime, args, self.state, self.run / "state.json", self.run, self.workspace)
            finally:
                general.assert_not_called()
                self.assertEqual(self.runtime.run_role.call_count, 1)

    def test_native_dispatch_records_typed_failure_without_retry(self):
        import autocode_stop as stop
        import autocode_util as util

        with self.assertRaises(util.Paused):
            self.native_dispatch(self.error())
        self.assertEqual(stop.outcome(self.state)["result"], "could_not_finish")

    def test_other_native_pause_keeps_its_original_hold_without_recovery(self):
        import autocode_stop as stop
        import autocode_util as util

        error = util.Paused("PAUSED_PROVIDER_UNCERTAIN", "retained native attempt")
        with self.assertRaises(util.Paused) as raised:
            self.native_dispatch(error)
        self.assertIs(raised.exception, error)
        self.assertIsNone(stop.outcome(self.state))
        self.assertNotIn("resolver", self.state)


class ScenarioNoActionTests(unittest.TestCase):
    def test_no_action_failed_or_blocked_view_is_retained_without_serving_or_completion(self):
        from scenarios.harness.driver import Driver

        for status in ("PAUSED_INTERVENTION", "PAUSED_VERIFICATION_PREPARATION"):
            with self.subTest(status=status):
                observed = {"status": status, "done": False, "needs": None, "next_command": None}
                driver = object.__new__(Driver)
                driver.view = Mock(return_value=observed)
                driver.call = Mock()
                driver.serve = Mock()
                driver.pause_to_acknowledge = Mock(side_effect=AssertionError("no acknowledgement of internal failure"))
                self.assertIs(driver.until_stopped(), observed)
                self.assertFalse(observed["done"])
                driver.call.assert_not_called()
                driver.serve.assert_not_called()


class TerminalNoActionTests(unittest.TestCase):
    def test_no_action_failure_is_read_only_in_frozen_terminal_chat(self):
        from tests.test_chat_product_journeys import CompletedRunReopenTests

        for category in ("blocked", "stopped"):
            with self.subTest(category=category):
                title = (
                    "AutoCode is blocked on verification preparation"
                    if category == "blocked"
                    else "AutoCode could not finish this task"
                )
                public = {
                    "status": "PAUSED_VERIFICATION_PREPARATION" if category == "blocked" else "PAUSED_INTERVENTION",
                    "done": False,
                    "needs": None,
                    "next_command": None,
                    "pause_category_label": "AutoCode",
                    "recovery": {
                        "category": category,
                        "title": title,
                        "what_happened": "Verification preparation failed before a provider started.",
                        "retained": "Saved work and approvals remain available.",
                    },
                    "chat": {"messages": [], "input": None},
                }
                client = CompletedRunReopenTests.client_for(public)
                self.addCleanup(client.close)
                self.assertEqual(client.step(), 2)
                shown = "\n".join(client.output)
                self.assertIn(title, shown)
                self.assertIn("Saved work and approvals remain available", shown)
                for absent in ("needs you", "start a new request", "resume", "Done.", "This task is complete"):
                    self.assertNotIn(absent, shown)
                self.assertEqual(client.prompts, [])
                self.assertEqual(client.dispatched, [])

    def test_no_action_missing_recovery_uses_only_public_summary(self):
        from tests.test_chat_product_journeys import CompletedRunReopenTests

        public = {
            "status": "PAUSED_VERIFICATION_PREPARATION",
            "done": False,
            "needs": None,
            "next_command": None,
            "pause_category_label": "AutoCode",
            "summary": {"label": "Blocked", "sentence": "Ownership remains unconfirmed."},
            "chat": {"messages": [], "input": None},
        }
        client = CompletedRunReopenTests.client_for(public)
        self.addCleanup(client.close)
        self.assertFalse(client.hand_off(public))
        shown = "\n".join(client.output)
        self.assertIn("Blocked", shown)
        self.assertIn("Ownership remains unconfirmed", shown)
        self.assertNotIn("needs you", shown)
        self.assertEqual(client.prompts, [])
        self.assertEqual(client.dispatched, [])
