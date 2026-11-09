"""A legacy validation-only misroute can request review, never more writer authority."""

import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import autocode_contract_identity as contracts
import autocode_resolver_human as human
import autocode_run_view as run_view
import autocode_util as util
import autocode_validation_scope_recovery as recovery


def retain_misroute(state, folder, revision, *, scope="operational_exhaustion", pause="PAUSED_NO_PROGRESS"):
    """Write a synthetic pre-upgrade checkpoint; callers observe recovery publicly."""
    contract = state["goal_contract"]
    state.update(status="PAUSED_NO_PROGRESS", next_stage="terra", iteration=5, no_progress_batches=3)
    state.setdefault("settings", {}).setdefault("milestone_checkpoints", {})["enabled"] = True
    state["settings"].setdefault("limits", {})["no_progress_batches"] = 3
    state["current_task"] = {
        "id": "validation-M1",
        "kind": "validate",
        "milestone_id": "M1",
        "objective": "Validate retained work; write nothing.",
        "requirements": ["Write nothing; preserve M2 and every acceptance criterion."],
        "contract_hash": contract["hash"],
        "contract_revision": contract["revision"],
    }
    assessment = {"status": "UNVERIFIED", "source_revision": revision, "reason": "One changed line lacks a test"}
    proof_path, report = folder / "proof.json", folder / "review.json"
    proof_path.write_text("retained proof bytes")
    report.write_text("retained independent review bytes")
    state["regression_proof"] = {
        "verdict": "PASS",
        "source_revision": revision,
        "path": str(proof_path),
        "changed_code_evidence": assessment,
    }
    state["validation"] = {
        "verdict": "PASS",
        "output": str(report),
        "source_revision": revision,
        "reviewer_role": "sol",
        "task_id": "validation-M1",
        "contract_hash": contract["hash"],
        "contract_revision": contract["revision"],
        "criteria_revision": state.get("criteria_revision"),
        "evidence_hashes": {str(p): util.file_hash(p) for p in (proof_path, report)},
    }
    state.setdefault("stages", []).append(
        {
            "stage": "sol_report_repair",
            "original_stage": "sol",
            "role": "sol",
            "exit_code": 0,
            "output": str(report),
            "task_id": "validation-M1",
            "contract_hash": contract["hash"],
            "source_revision": revision,
            "changed_files": [],
        }
    )
    state["regression_proofs"] = [
        {
            "kind": "changed_code_rework",
            "action": "builder",
            "round": 2,
            "round_limit": 2,
            "task_id": "validation-M1",
            "contract_hash": contract["hash"],
            "contract_revision": contract["revision"],
            "source_revision": revision,
            "review_output": str(report),
            "assessment": deepcopy(assessment),
        }
    ]
    binding = {
        **{key: state.get(key) for key in ("workspace", "run_dir", "task_id", "conversation_id")},
        "runtime_sources": "previous-runtime",
        "current_task": util.digest(state["current_task"]),
        "settings": util.digest(state["settings"]),
        "validation": util.digest(state["validation"]),
        "contract": {key: contract[key] for key in ("hash", "revision", "approval_event")},
        "accounting": {"no_progress_batches": 3},
        "observed_source": {"revision": revision},
        "interruptions": {},
    }
    human.queue(
        state,
        scope,
        {"stage": "terra", "pause_status": pause},
        request={
            "kind": "blocker",
            "decision_needed": "Raise the no-progress limit",
            "impact": "Old validation misroute",
        },
    )
    with (
        patch.object(human, "_binding", return_value=binding),
        patch.object(human, "_decision", return_value=("escalate", "Retained old misroute")),
    ):
        human.evaluate(state)


class ValidationScopeRecoveryTests(unittest.TestCase):
    def fixture(self, **options):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        folder = Path(temp.name)
        contract = {"task_id": "root-task", "revision": 6, "body": {"open_blocking_questions": []}}
        contract["hash"] = util.digest(contract)
        approval = {"kind": "goal_approval", "actor": "user_cli", "token": contracts.token(contract)}
        contract.update(approval_status="approved", approval_event=approval)
        state = {
            "goal_contract": contract,
            "criteria_revision": 6,
            "user_events": [approval],
            "settings": {"roles": {"sol": {"model": "saved", "reasoning_effort": "high"}}},
            "findings_ledger": [{"id": "F1", "source": "sol", "status": "open", "blocking": True}],
            "no_progress_reports": [{"output": "earlier-implementation"}],
        }
        retain_misroute(state, folder, "rev", **options)
        return state

    def test_public_offer_replaces_only_the_machinery_question_and_is_read_only(self):
        state = self.fixture()
        before = deepcopy(state)
        with patch.object(util, "file_hash", side_effect=AssertionError("offer must read no files")):
            self.assertIsNotNone(recovery.pending(state))
        view = run_view.view(state)
        self.assertEqual("continue", view["needs"]["kind"])
        self.assertIn("scope review", view["stop_reason"])
        self.assertIsNone(view["chat"]["gate"])
        self.assertIsNone(view["chat"]["input"])
        self.assertFalse(view["done"])
        self.assertEqual(before, state)

    def test_settlement_queues_review_without_resetting_counters_or_accepting_evidence(self):
        state = self.fixture()
        before = deepcopy(state)
        self.assertTrue(recovery.settle(state, "rev", at="now"))
        self.assertEqual(("RUNNING", "astra_review"), (state["status"], state["next_stage"]))
        for key in (
            "current_task",
            "goal_contract",
            "settings",
            "validation",
            "regression_proof",
            "stages",
            "iteration",
            "no_progress_batches",
            "no_progress_reports",
            "findings_ledger",
        ):
            self.assertEqual(before[key], state[key], key)
        self.assertEqual(before["regression_proofs"], state["regression_proofs"][:-1])
        self.assertEqual("reviewer", state["regression_proofs"][-1]["action"])
        self.assertNotIn("round", state["regression_proofs"][-1])
        self.assertIn("existing exact-plan review and approval", state["next_action"])
        self.assertEqual("runner", state["user_events"][-1]["actor"])
        after = deepcopy(state)
        self.assertFalse(recovery.settle(state, "rev", at="again"))
        self.assertEqual(after, state)

    def test_unbound_or_other_gates_never_receive_scope_recovery(self):
        for variant in (
            "permission",
            "time",
            "task",
            "settings",
            "approval",
            "count",
            "proof",
            "assessment",
            "review",
            "rejected",
            "active",
            "nonmilestone",
        ):
            with self.subTest(variant=variant):
                state = self.fixture(
                    **(
                        {"scope": "permission"}
                        if variant == "permission"
                        else {"pause": "PAUSED_TIME_LIMIT"}
                        if variant == "time"
                        else {}
                    )
                )
                if variant == "task":
                    state["current_task"]["requirements"].append("Different assignment")
                elif variant == "settings":
                    state["settings"]["limits"]["no_progress_batches"] = 4
                elif variant == "approval":
                    state["goal_contract"]["approval_status"] = "draft"
                elif variant == "count":
                    state["no_progress_batches"] = 2
                elif variant == "proof":
                    state["regression_proof"]["source_revision"] = "stale"
                elif variant == "assessment":
                    state["regression_proof"]["changed_code_evidence"]["reason"] = "changed"
                elif variant == "review":
                    state["regression_proofs"][-1]["review_output"] = "another"
                elif variant == "rejected":
                    state["stages"][-1]["rejected"] = True
                elif variant == "active":
                    state["active_runner_check"] = {"running": True}
                elif variant == "nonmilestone":
                    state["settings"]["milestone_checkpoints"]["enabled"] = False
                before = deepcopy(state)
                self.assertIsNone(recovery.pending(state))
                self.assertFalse(recovery.settle(state, "rev", at="now"))
                self.assertEqual(before, state)

    def test_source_or_pin_drift_and_pending_interruptions_prevent_settlement(self):
        for variant in ("source", "pin", "pause"):
            with self.subTest(variant=variant):
                state = self.fixture()
                if variant == "pin":
                    Path(state["validation"]["output"]).write_text("changed bytes")
                before = deepcopy(state)
                with patch.object(human, "pending_interruptions", return_value={"pause": variant == "pause"}):
                    self.assertFalse(recovery.settle(state, "other" if variant == "source" else "rev", at="now"))
                self.assertEqual(before, state)
