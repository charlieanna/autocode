"""Retained owners prove a continuation; selection never supplies its authority."""

import copy
import json
import unittest
from unittest.mock import patch

import autocode as runner
import autocode_action_retained_recovery as owners
import autocode_args
import autocode_operational_admission as admission
import autocode_operational_disposition as disposition
import autocode_resolver_human as human
import autocode_run_actions as actions
import autocode_stop as stop

from . import test_repaired_acceptance_no_progress as fixtures
from .operational_fixtures import legacy_publication


class RetainedOwnerTests(unittest.TestCase):
    setUp = fixtures.RepairedAcceptanceNoProgressUnitTests.setUp

    def retained(self, *, proof=True):
        if proof:
            fixtures._seed_acceptance_proof(self.state, self.workspace, self.run_dir)
        self.state["stop_reason"] = "Repeated unchanged implementation batches require review"
        binding = {**human._binding(self.state), "runtime_sources": "old-runtime"}
        with patch.object(human, "_binding", return_value=binding):
            legacy_publication(self.state, self.run_dir, pause="PAUSED_NO_PROGRESS")
        # The fixture preserves old bytes. All operations below use real owners.
        (self.run_dir / "state.json").write_text(json.dumps(self.state))

    def advance(self, *, resume=False):
        parser = autocode_args.build_parser(None, autocode_args.DEFAULT_ROLE_MODELS)
        args = parser.parse_args(
            ["--run-dir", str(self.run_dir), "--no-chat", *(["--resume-paused"] if resume else [])]
        )
        decided = actions.Decided()
        values = (runner, args, parser, self.state, self.run_dir / "state.json", self.run_dir, self.workspace, decided)
        code = admission.guard(*values)
        if code is None and decided.owner_lane:
            code = admission.apply_owned(*values)
        return code, values

    def test_plain_and_explicit_continuations_recheck_same_proof_without_granting_an_attempt(self):
        self.retained()
        before = copy.deepcopy(self.state)
        code, values = self.advance()
        self.assertIsNone(code)
        self.assertEqual("RUNNING", self.state["status"])
        self.assertIsNone(admission.work_guard(*values, final=True))
        for key in ("settings", "goal_contract", "no_progress_batches"):
            self.assertEqual(before[key], self.state[key], key)
        self.assertEqual(before["stages"], self.state["stages"][: len(before["stages"])])
        self.assertTrue(all(row.get("runner_owned") for row in self.state["stages"][len(before["stages"]) :]))
        checked = disposition.checked_recovery(self.state, disposition.held(self.state))
        self.assertEqual("retained_acceptance", checked["selection"])
        receipts = copy.deepcopy(self.state["resolver"]["operational_receipts"])
        code, _ = self.advance(resume=True)
        self.assertIsNone(code)
        self.assertEqual(
            receipts, self.state["resolver"]["operational_receipts"], "saved proof is checked, not replayed"
        )

    def test_changed_source_invalidates_the_checked_owner_before_work(self):
        self.retained()
        code, values = self.advance()
        self.assertIsNone(code)
        (self.workspace / "tests" / "test_c1.py").write_text("assert False\n")
        self.assertEqual(2, admission.work_guard(*values, final=True))

    def test_changed_settings_or_contract_invalidates_saved_owner_answer(self):
        self.retained()
        self.assertIsNone(self.advance()[0])
        baseline = copy.deepcopy(self.state)
        for key, value in (("settings", {"limits": {"max_cost_usd": 0}}), ("current_task", {"id": "other"})):
            with self.subTest(key=key):
                self.state = copy.deepcopy(baseline)
                self.state[key] = value
                self.assertEqual(2, self.advance()[0])

    def test_unproven_default_hold_settles_once_without_a_provider(self):
        self.retained(proof=False)
        self.assertEqual(2, self.advance()[0])
        self.assertEqual("PAUSED_INTERVENTION", self.state["status"])
        self.assertEqual("could_not_finish", stop.outcome(self.state)["result"])
        receipts = copy.deepcopy(self.state["resolver"]["operational_receipts"])
        self.assertEqual(2, self.advance()[0])
        self.assertEqual(receipts, self.state["resolver"]["operational_receipts"])

    def test_explicit_limit_and_uncertain_work_are_not_automatic_recovery_authority(self):
        for mutation in (
            lambda state: state["settings"]["budget_origins"].update(no_progress_batches="user_cli"),
            lambda state: state.update(active_runner_check={"status": "uncertain"}),
            lambda state: state.update(pending_report_repair={"stage": "builder"}),
        ):
            with self.subTest(mutation=mutation):
                baseline = copy.deepcopy(self.state)
                mutation(self.state)
                self.retained()
                before = copy.deepcopy(self.state)
                code, values = self.advance()
                self.assertIn(code, (None, 2))
                self.assertFalse(values[-1].owner_lane)
                self.assertFalse(values[-1].operational_recovery_checked)
                self.assertEqual(before, self.state)
                self.state = baseline

    def test_accounting_owner_rechecks_ledger_and_source_after_correction(self):
        from .test_recovery_progress import RecoveryProgressTests

        state = RecoveryProgressTests().state()
        state.update(goal_contract=self.contract, user_events=[self.approval])
        self.assertTrue(
            owners.progress.reconcile(
                state,
                issued={"request_id": "request", "scope": "operational_exhaustion"},
                approved=True,
                supersede=lambda *_: True,
                now=lambda: "now",
            )
        )
        proof = {"event": state["user_events"][-1], "source_revision": "revision"}
        with patch.object(owners, "_revision", return_value="revision"):
            observed = {"result": proof}
            self.assertIsNotNone(
                owners.validate_accounting(
                    runner, None, state, self.run_dir, self.workspace, None, None, observed, final=True
                )
            )
            state["stages"].append({"stage": "builder"})
            self.assertIsNone(
                owners.validate_accounting(
                    runner, None, state, self.run_dir, self.workspace, None, None, observed, final=True
                )
            )
