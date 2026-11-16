"""The CLI must show a resumed run doing local checks before it calls a model."""

import copy
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_command_receipt as command_receipt
import autocode_command_supervision as command_supervision
import autocode_process as processes
import autocode_regression as regression
import autocode_run_view as run_view
import autocode_runner_check as runner_check
import autocode_status as status
import autocode_uncertain_cleanup as uncertain_cleanup
import autocode_util as util
import autocode_verification_recovery as verification_recovery

from tests.test_verify import REFERENCE, Project, isolated_python


class RunnerCheckTests(unittest.TestCase):
    def setUp(self):
        self.python = isolated_python(self)
        self.project = Project()
        self.addCleanup(self.project.close)
        self.run = self.project.root / ".autocode" / "runs" / "fixture"
        self.run.mkdir(parents=True)
        self.path = self.run / "state.json"
        self.state = {
            "version": 3,
            "task": "Check the change",
            "workspace": str(self.project.root),
            "run_dir": str(self.run),
            "status": "RUNNING",
            "iteration": 1,
            "sessions": {},
            "stages": [],
            "next_stage": "tester",
            "base_commit": self.project.base,
            "settings": {"roles": {"tester": {"model": "glm-5.3", "reasoning_effort": "high"}}},
        }
        # Resume has updated memory, but no provider has saved its launch yet.
        self.path.write_text(json.dumps({**self.state, "status": "PAUSED_INTERVENTION"}))

    def cli_status(self):
        before = self.path.read_bytes()
        command = [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "tools" / "autocode.py"),
            "--workspace",
            str(self.project.root),
            "--run-dir",
            str(self.run),
            "--status",
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        self.assertEqual(before, self.path.read_bytes(), "status must remain read-only")
        return json.loads(result.stdout)

    def test_status_during_baseline_and_comparison_then_clean_handoff(self):
        self.project.write(REFERENCE)
        self.state["settings"]["regression"] = {"python": self.python}
        route = copy.deepcopy(self.state["settings"])
        observed = []
        actual_baseline, actual_verify = regression.verify.baseline, regression.verify.verify

        def observe(label):
            view = self.cli_status()
            observed.append(view["view"]["runner_check"]["summary"])
            self.assertIn(label, observed[-1])
            self.assertEqual("RUNNING", view["status"])
            self.assertEqual("continue", view["view"]["needs"]["kind"])
            self.assertEqual("tester", view["next_stage"])
            self.assertIsNone(view["active_stage"])
            self.assertIsNone(view["active_stage_workers"])
            self.assertFalse(view["stale"])
            self.assertTrue(view["runner_check_workers"]["alive"])
            self.assertEqual([os.getpid()], view["runner_check_workers"]["live_pids"])
            self.assertEqual(route, view["settings"])
            saved = json.loads(self.path.read_text())
            self.assertIn("no model is active", saved["progress_messages"][-1]["text"])

        def baseline(*args, **kwargs):
            observe("original code")
            return actual_baseline(*args, **kwargs)

        def verify(*args, **kwargs):
            observe("candidate suite")
            return actual_verify(*args, **kwargs)

        with (
            patch.object(regression, "required", return_value=True),
            patch.object(regression.verify, "baseline", side_effect=baseline),
            patch.object(regression.verify, "verify", side_effect=verify),
        ):
            regression.before_review(self.state, "tester", self.project.root, self.run)

        self.assertEqual(2, len(observed))
        view = self.cli_status()
        self.assertIsNone(view["view"]["runner_check"])
        self.assertIsNone(view["runner_check_workers"])
        self.assertEqual("PASS", view["view"]["evidence"]["regression_proof"]["verdict"])
        self.assertEqual("tester", view["next_stage"])
        # A cached proof must not publish another active check or repeat the suite.
        with patch.object(regression.verify, "verify") as run_suite:
            regression.prove(self.state, self.project.root, self.run)
        run_suite.assert_not_called()

    def test_exception_clears_activity_without_fabricating_a_proof(self):
        with (
            patch.object(regression, "required", return_value=True),
            patch.object(regression.verify, "baseline", side_effect=RuntimeError("suite failed to start")),
        ):
            with self.assertRaisesRegex(RuntimeError, "suite failed to start"):
                regression.before_review(self.state, "tester", self.project.root, self.run)
        view = self.cli_status()
        self.assertIsNone(view["view"]["runner_check"])
        self.assertIsNone(view["view"]["evidence"]["regression_proof"])

    def test_reused_pid_is_a_stale_check_not_a_live_provider(self):
        with runner_check.track(self.state, self.run, "regression_proof", "Testing the change", status.persist):
            # The PID exists, but its birth identity no longer matches the saved owner.
            saved = json.loads(self.path.read_text())
            owner = saved["active_runner_check"]["processes"][0]
            owner["birth_identity"] -= 1
            self.path.write_text(json.dumps(saved))
            view = self.cli_status()
            self.assertTrue(view["stale"])
            self.assertFalse(view["runner_check_workers"]["alive"])
            self.assertIn("runner check", view["next_action"])
            self.assertIsNone(view["attempt_id"])

    def _hold(self, *, phase="uncertain", cleanup_error="keeper failed", cause="keeper_failure"):
        # An armed receipt admits no cause or cleanup error (liveness._receipt).
        if phase == "armed":
            cause, cleanup_error = None, None
        path = self.run / "supervision.json"
        metadata = {
            "schema": 1,
            "nonce": "c" * 32,
            "receipt": str(path.resolve()),
            "owner": {"pid": 301, "birth_identity": 1},
            "keeper": {"pid": 302, "birth_identity": 2},
            "provider": {"pid": 303, "birth_identity": 3},
        }
        util.atomic_json(
            path,
            {
                **metadata,
                "phase": phase,
                "cause": cause,
                "cleanup_error": cleanup_error,
                "observed_at": "2026-10-07T03:10:23Z",
                "processes": [metadata["provider"]],
            },
        )
        self.state["active_runner_check"] = {
            "stage": "regression_proof",
            "summary": "baseline",
            "supervision": metadata,
        }
        return metadata

    def _persist(self, path, state):
        path.write_text(json.dumps(state))

    def test_a_dead_uncertain_inventory_is_retired_without_becoming_proof(self):
        self._hold()
        with patch.object(command_supervision.processes, "live_processes", return_value=[]):
            runner_check.clear(self.state, self.run, self._persist)
        self.assertNotIn("active_runner_check", self.state)
        self.assertNotIn("regression_proof", self.state)
        self.assertEqual("runner_check_retired", self.state["user_events"][-1]["kind"])
        saved = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.assertNotIn("active_runner_check", saved)

    def _automatic_hold(self, *, proof=None, log_parts=()):
        metadata = self._hold(
            phase="uncertain", cleanup_error="SessionScanIncomplete: changing PID scan", cause="controller_finished"
        )
        pid = os.getpid()
        metadata["owner"] = processes.identity(processes.process_table({pid})[pid])
        metadata["provider"].update(group=metadata["provider"]["pid"], session=metadata["provider"]["pid"])
        receipt = json.loads(Path(metadata["receipt"]).read_text())
        proof = proof or self.run / "proof"
        log_dir = proof.joinpath(*log_parts)
        directory = log_dir / "command-supervision" / ("a" * 32)
        directory.mkdir(parents=True)
        (proof / "scratch" / "candidate").mkdir(parents=True)
        output = log_dir / "captured.log"
        output.write_text("partial output")
        path = directory / "supervision.json"
        metadata["receipt"] = str(path)
        receipt.update(owner=metadata["owner"], provider=metadata["provider"], receipt=str(path))
        check = self.state["active_runner_check"]
        check.update(command="check command", output=str(output), processes=[metadata["owner"]])
        util.atomic_json(path, receipt)
        util.atomic_json(
            directory / "admission.json",
            {
                "schema": 1,
                "command": check["command"],
                "output": str(output),
                "cwd": str(proof / "scratch" / "candidate"),
                "supervision": metadata,
            },
        )
        return metadata, path

    def test_automatic_cleanup_binds_nested_evidence_to_its_own_proof_scratch(self):
        def settle(tree, **_):
            tree.groups.clear()
            tree.sessions.clear()
            tree.session_admissions.clear()
            return []

        for index, parts in enumerate((("mutation-check",), ("reports", "commands"))):
            with self.subTest(log_parts=parts):
                proof = self.run / "regression" / f"proof-{index}"
                _, receipt = self._automatic_hold(proof=proof, log_parts=parts)
                saved = receipt.read_bytes()
                revision = f"revision-{index}"
                with patch.object(processes, "settled_sample", side_effect=settle):
                    self.assertTrue(
                        runner_check.auto_recover(
                            self.state,
                            self.run,
                            self._persist,
                            source_revision=revision,
                            source_current=lambda: revision,
                        )
                    )
                self.assertNotIn("active_runner_check", self.state)
                self.assertNotIn("regression_proof", self.state, "interrupted cleanup is not passing proof")
                self.assertEqual(saved, receipt.read_bytes())
                self.assertEqual("runner_check_auto_recovered", self.state["user_events"][-1]["kind"])

    def test_automatic_cleanup_refuses_scratch_from_a_different_proof(self):
        proof = self.run / "regression" / "proof-a"
        _, receipt = self._automatic_hold(proof=proof, log_parts=("reports",))
        other = self.run / "regression" / "proof-b" / "scratch" / "candidate"
        other.mkdir(parents=True)
        admission_path = receipt.parent / "admission.json"
        admission = util.read(admission_path)
        admission["cwd"] = str(other)
        util.atomic_json(admission_path, admission)
        before = copy.deepcopy(self.state)
        with patch.object(processes, "settled_sample") as scan:
            self.assertFalse(
                runner_check.auto_recover(
                    self.state, self.run, self._persist, source_revision="revision", source_current=lambda: "revision"
                )
            )
        self.assertEqual(before, self.state)
        scan.assert_not_called()

    def test_root_level_regression_log_keeps_its_existing_scratch_binding(self):
        self._automatic_hold(proof=self.run / "regression")

        def settle(tree, **_):
            tree.groups.clear()
            tree.sessions.clear()
            tree.session_admissions.clear()
            return []

        with patch.object(processes, "settled_sample", side_effect=settle):
            self.assertTrue(
                runner_check.auto_recover(
                    self.state, self.run, self._persist, source_revision="revision", source_current=lambda: "revision"
                )
            )
        self.assertNotIn("regression_proof", self.state)
        self.assertNotIn("active_runner_check", self.state)

    def test_command_binding_refuses_an_unrelated_scratch_root(self):
        proof = self.run / "regression" / "proof-a"
        self._automatic_hold(proof=proof, log_parts=("reports",))
        other = self.run / "regression" / "proof-b" / "scratch"
        other.mkdir(parents=True)
        check = self.state["active_runner_check"]
        with self.assertRaises(command_receipt.OwnershipUncertain):
            verification_recovery.bind_command(Path(check["output"]).parent, check, scratch_root=other)

    def test_unsettled_cleanup_is_reported_as_blocked_without_a_machinery_question(self):
        self._automatic_hold()
        self.state["status"] = "PAUSED_VERIFICATION_UNCERTAIN"
        view = run_view.view(self.state, run_dir=self.run, workspace=self.project.root)
        self.assertFalse(view["done"])
        self.assertIsNone(view["needs"])
        self.assertIsNone(view["next_command"])
        self.assertIn("active_runner_check", self.state)
        self.assertEqual("blocked", view["recovery"]["category"])
        self.assertIsNone(view["chat"]["input"])
        self.assertNotIn("inspect the pause", str(view["chat"]).lower())
        self.assertIn("could not confirm", view["recovery"]["what_happened"])

    def test_uncertain_cleanup_is_rechecked_and_retried_without_counting_as_proof(self):
        self.project.write(REFERENCE)
        self.state["settings"]["regression"] = {"python": self.python}
        actual = regression._prove
        calls = []
        saved_receipt = []

        def first_uncertain(*args, **kwargs):
            calls.append(1)
            if len(calls) != 1:
                return actual(*args, **kwargs)
            metadata, path = self._automatic_hold()
            saved_receipt.append(path.read_bytes())
            raise command_receipt.OwnershipUncertain("injected uncertain cleanup")

        def settle(tree, **_):
            tree.groups.clear()
            tree.sessions.clear()
            tree.session_admissions.clear()
            return []

        with (
            patch.object(regression, "required", return_value=True),
            patch.object(regression, "_prove", side_effect=first_uncertain),
            patch.object(processes, "settled_sample", side_effect=settle),
        ):
            regression.before_review(self.state, "tester", self.project.root, self.run)
        self.assertEqual(2, len(calls))
        self.assertEqual("PASS", self.state["regression_proof"]["verdict"])
        event = next(row for row in self.state["user_events"] if row["kind"] == "runner_check_auto_recovered")
        self.assertEqual("runner", event["actor"])
        self.assertEqual(saved_receipt[0], Path(event["receipt"]).read_bytes())
        self.assertNotIn("active_runner_check", self.state)

    def test_automatic_cleanup_scan_retries_only_transient_churn_at_a_fixed_bound(self):
        result = {"receipt": "verified"}
        with (
            patch.object(
                uncertain_cleanup, "settle", side_effect=[processes.SessionScanIncomplete("churn"), result]
            ) as scan,
            patch.object(uncertain_cleanup.util, "sleep") as sleep,
        ):
            self.assertEqual(result, uncertain_cleanup.settle_with_retry({}))
        self.assertEqual(2, scan.call_count)
        sleep.assert_called_once_with(0.2)
        with (
            patch.object(uncertain_cleanup, "settle", side_effect=processes.SessionScanIncomplete("churn")) as scan,
            patch.object(uncertain_cleanup.util, "sleep") as sleep,
            self.assertRaises(processes.SessionScanIncomplete),
        ):
            uncertain_cleanup.settle_with_retry({})
        self.assertEqual(3, scan.call_count)
        self.assertEqual(2, sleep.call_count)
        with (
            patch.object(uncertain_cleanup, "settle", side_effect=processes.ProcessError("denied")) as scan,
            patch.object(uncertain_cleanup.util, "sleep") as sleep,
            self.assertRaises(processes.ProcessError),
        ):
            uncertain_cleanup.settle_with_retry({})
        scan.assert_called_once()
        sleep.assert_not_called()

    def test_cancelled_uncertain_check_is_not_automatically_retried(self):
        self.project.write(REFERENCE)
        self.state["settings"]["regression"] = {"python": self.python}
        error = command_receipt.OwnershipUncertain("cancelled with unknown cleanup")
        error.check_cancelled = True
        with (
            patch.object(regression, "required", return_value=True),
            patch.object(regression, "_prove", side_effect=error),
            patch.object(runner_check, "auto_recover") as recover,
            self.assertRaises(command_receipt.OwnershipUncertain),
        ):
            regression.before_review(self.state, "tester", self.project.root, self.run)
        recover.assert_not_called()
        self.assertIn("active_runner_check", self.state)

    def test_automatic_recovery_keeps_unknown_or_changed_ownership_held(self):
        metadata, path = self._automatic_hold()
        original = copy.deepcopy(self.state)
        original_receipt = path.read_bytes()

        def attempt(source=lambda: "revision"):
            return runner_check.auto_recover(
                self.state, self.run, self._persist, source_revision="revision", source_current=source
            )

        with patch.object(processes, "live_processes", return_value=[metadata["provider"]]):
            self.assertFalse(attempt())
        with patch.object(processes, "settled_sample", side_effect=processes.SessionScanIncomplete("busy")):
            self.assertFalse(attempt())
        with patch.object(processes, "settled_sample", return_value=[]):
            self.assertFalse(attempt(), "an unretired group or session cannot prove cleanup")
        self.assertFalse(attempt(source=lambda: "different"))
        with patch.object(runner_check.interventions, "pending", return_value=[{"kind": "pause"}]):
            self.assertFalse(attempt())
        pending = self.run / "check-replay" / "obligations" / "pending.json"
        pending.parent.mkdir(parents=True)
        pending.write_text("{}")
        self.assertFalse(attempt(), "a separately owned scheduled obligation must not be bypassed")
        pending.unlink()
        admission = path.parent / "admission.json"
        original_admission = admission.read_bytes()
        admission.write_text("{}")
        self.assertFalse(attempt(), "an unbound command admission cannot be retired")
        admission.write_bytes(original_admission)
        self.assertEqual(original, self.state)
        self.assertEqual(original_receipt, path.read_bytes())

    def test_new_intervention_or_in_place_check_change_keeps_cleanup_held(self):
        self._automatic_hold()
        for mode in ("intervention", "check_changed"):
            with self.subTest(mode=mode):
                original = copy.deepcopy(self.state)
                calls = []

                def settle(tree, **_):
                    calls.append(1)
                    tree.groups.clear()
                    tree.sessions.clear()
                    tree.session_admissions.clear()
                    if len(calls) == 2 and mode == "check_changed":
                        self.state["active_runner_check"]["command"] = "changed"
                    return []

                def pending(_):
                    return [{"kind": "pause"}] if len(calls) == 2 and mode == "intervention" else []

                with (
                    patch.object(processes, "settled_sample", side_effect=settle),
                    patch.object(runner_check.interventions, "pending", side_effect=pending),
                ):
                    self.assertFalse(
                        runner_check.auto_recover(
                            self.state,
                            self.run,
                            self._persist,
                            source_revision="revision",
                            source_current=lambda: "revision",
                        )
                    )
                self.assertIn("active_runner_check", self.state)
                self.assertFalse(self.state.get("user_events"))
                self.state = original

    def test_automatic_recovery_rechecks_receipt_and_is_bounded_per_source(self):
        _metadata, path = self._automatic_hold()
        original = copy.deepcopy(self.state)
        original_receipt = path.read_bytes()

        def changed_during_scan(tree, **_):
            tree.groups.clear()
            tree.sessions.clear()
            tree.session_admissions.clear()
            path.write_text("{}")
            return []

        with patch.object(processes, "settled_sample", side_effect=changed_during_scan):
            self.assertFalse(
                runner_check.auto_recover(
                    self.state, self.run, self._persist, source_revision="revision", source_current=lambda: "revision"
                )
            )
        self.assertEqual(original, self.state)
        self.assertIn("active_runner_check", self.state)
        path.write_bytes(original_receipt)

        def safely_empty(tree, **_):
            tree.groups.clear()
            tree.sessions.clear()
            tree.session_admissions.clear()
            return []

        with patch.object(processes, "settled_sample", side_effect=safely_empty):
            self.assertTrue(
                runner_check.auto_recover(
                    self.state, self.run, self._persist, source_revision="revision", source_current=lambda: "revision"
                )
            )
            self.state["active_runner_check"] = original["active_runner_check"]
            self.assertFalse(
                runner_check.auto_recover(
                    self.state, self.run, self._persist, source_revision="revision", source_current=lambda: "revision"
                )
            )
        self.assertEqual(1, len(self.state["user_events"]))
        self.assertIn("active_runner_check", self.state)

    def test_a_live_or_unreadable_inventory_keeps_the_hold(self):
        self._hold()
        with patch.object(command_supervision.processes, "live_processes", return_value=[{"pid": 303}]):
            with self.assertRaises(command_receipt.OwnershipUncertain):
                runner_check.clear(self.state, self.run, self._persist)
        self.assertIn("active_runner_check", self.state)
        self.assertNotIn("user_events", self.state)
        (self.run / "supervision.json").write_bytes(b"x" * (command_receipt.MAX_RECEIPT_BYTES + 1))
        with self.assertRaises(command_receipt.OwnershipUncertain):
            runner_check.clear(self.state, self.run, self._persist)
        self.assertIn("active_runner_check", self.state)

    def test_a_dead_armed_inventory_of_a_lost_keeper_is_retired(self):
        """#2718: an armed receipt whose keeper died can never finish; a fully dead
        recorded tree with an empty provider session and group releases the hold."""
        self._hold(phase="armed", cleanup_error=None)
        with (
            patch.object(command_supervision.processes, "live_processes", return_value=[]),
            patch.object(command_supervision.processes, "process_ids", return_value=[]),
        ):
            runner_check.clear(self.state, self.run, self._persist)
        self.assertNotIn("active_runner_check", self.state)
        self.assertEqual("runner_check_retired", self.state["user_events"][-1]["kind"])

    def test_an_armed_inventory_with_a_live_provider_session_member_keeps_the_hold(self):
        self._hold(phase="armed", cleanup_error=None)
        member = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
        self.addCleanup(member.wait, timeout=10)
        self.addCleanup(member.kill)
        metadata = self.state["active_runner_check"]["supervision"]
        metadata["provider"] = {**metadata["provider"], "session": member.pid, "group": member.pid}
        with patch.object(command_supervision.processes, "live_processes", return_value=[]):
            with self.assertRaises(command_receipt.OwnershipUncertain):
                runner_check.clear(self.state, self.run, self._persist)
        self.assertIn("active_runner_check", self.state)
        self.assertNotIn("user_events", self.state)

    def test_unknown_process_liveness_does_not_claim_a_dead_check(self):
        with runner_check.track(self.state, self.run, "regression_proof", "Testing the change", status.persist):
            with patch.object(processes, "process_table", side_effect=processes.ProcessError("access denied")):
                worker = processes.recorded_worker_state(self.state["active_runner_check"])
            self.assertFalse(worker["checked"])
            self.assertIsNone(worker["alive"])
