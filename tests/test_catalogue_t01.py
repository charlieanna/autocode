"""T01 — Approval and requirement preservation catalogue scenarios (APP-01..APP-12).

Independent ApprovalOracle: recomputes the contract seal and approval token
from fixture facts (canonical-json SHA-256, `r<revision>:<hash>` format) and
decides expected approval validity without calling autocode_goals decision
functions.  Existing regressions are cited per case and rerun by the suite.
"""
# path bootstrap: runtime in tools/, fakes in tests/fakes/
import sys as _sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[2] if 'fakes' in _Path(__file__).parts else _Path(__file__).resolve().parents[1]
_TOOLS = _ROOT / 'tools'
_FAKES = _ROOT / 'tests' / 'fakes'
for _p in (_ROOT, _TOOLS, _ROOT / 'tests', _FAKES):
    _s = str(_p)
    if _s not in _sys.path:
        _sys.path.insert(0, _s)
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

_ROOT = _Path(__file__).resolve().parents[1] if _Path(__file__).name != 'live_trial.py' else _Path(__file__).resolve().parent.parent
for _p in (_ROOT, _ROOT / 'tools', _ROOT / 'tests', _ROOT / 'tests' / 'fakes'):
    _s = str(_p)
    if _s not in _sys.path:
        _sys.path.insert(0, _s)
import autopilot_testkit as kit
import autocode as runner
import autocode_goals as goals
import autocode_support as support
from goal_fixtures import body, envelope


def oracle_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class ApprovalOracle:
    """Expected approval validity from first principles: a sealed contract,
    an exact `r<rev>:<seal>` token recorded as a user_cli event, and no open
    blocking questions."""

    @staticmethod
    def seal(contract):
        return oracle_digest({k: contract[k] for k in ("task_id", "revision", "body")})

    @staticmethod
    def token(contract):
        return f"r{contract['revision']}:{ApprovalOracle.seal(contract)}"

    @staticmethod
    def valid(state):
        contract = state.get("goal_contract") or {}
        if not contract:
            return False
        approval = contract.get("approval_event") or {}
        return (contract.get("hash") == ApprovalOracle.seal(contract)
                and contract.get("approval_status") == "approved"
                and approval.get("token") == ApprovalOracle.token(contract)
                and approval.get("actor") == "user_cli"
                and approval in state.get("user_events", [])
                and not contract["body"].get("open_blocking_questions"))


class ApprovalCase(kit.CatalogueCase):
    def setUp(self):
        kit.CatalogueCase.setUp(self)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        env = patch.dict(os.environ, {"AUTOCODE_HOME": str(self.root / "registry-home")})
        env.start()
        self.addCleanup(env.stop)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=F", "-c", "user.email=f@t",
                        "commit", "--allow-empty", "-qm", "fixture"], check=True)
        self.run = self.root / ".autocode/runs/fixture"
        self.run.mkdir(parents=True)
        self.local = {"auth_mode": "fixture"}
        self.state = {"version": 2, "workspace": str(self.root), "task": "Make a greeting tool",
                      "status": "RUNNING", "iteration": 1, "sessions": {}, "stages": [], "history": [],
                      "next_stage": "terra", "acceptance_criteria": [],
                      "settings": {"roles": {r: {"model": r, "reasoning_effort": "high"}
                                             for r in ("astra", "terra", "sol")},
                                   "transport_identity": self.local, "headroom": {"enabled": False},
                                   "context_soft_tokens": 10000,
                                   "limits": {"iteration_ceiling": 5, "max_seconds": None,
                                              "max_reported_tokens": None, "no_progress_batches": 3}}}
        goals.migrate(self.state)

    def draft(self, **kwargs):
        goals.install_draft(self.state, body(**kwargs), origin="test")

    def approve_now(self, **kwargs):
        self.draft(**kwargs)
        goals.present(self.state)
        goals.approve(self.state, goals.token(self.state["goal_contract"]))

    def decision(self, status="CONTINUE"):
        criteria = [{**c, "status": "verified", "evidence": "event:check"}
                    for c in self.state["acceptance_criteria"]]
        return {**envelope(self.state), "status": status, "acceptance_criteria": criteria,
                "next_objective": "Implement greeting",
                "next_task": {"kind": "implement", "milestone_id": "M1",
                              "requirements": ["Greet valid names; reject empty names"],
                              "acceptance_criteria": ["C1"], "validation_plan": ["Execute both CLI cases"]},
                "agreed_limitations": [], "blocker": "", "evidence": ["event:check"],
                "plan": ["Greeting and checks"], "affected_paths": ["greet.py"]}

    def invoke(self, *args, role=None):
        support.atomic_json(self.run / "state.json", self.state)
        argv = ["autocode", "--workspace", str(self.root), "--run-dir", str(self.run), *args]
        with patch.object(sys, "argv", argv), patch.object(support, "assert_no_legacy_process"), \
                patch.object(support, "local_settings", return_value=self.local), \
                patch.object(runner, "run_role", side_effect=role or AssertionError("No agent may launch")), \
                io.StringIO(), __import__("contextlib").redirect_stdout(io.StringIO()), \
                __import__("contextlib").redirect_stderr(io.StringIO()):
            try:
                code = runner.main()
            except SystemExit as exit_code:
                code = exit_code.code
        self.state = support.read(self.run / "state.json")
        return code

    def compare_with_oracle(self, label="approval_matches_oracle"):
        self.check(label, self.oracle_expectation(), goals.approved(self.state))

    def oracle_expectation(self):
        return ApprovalOracle.valid(self.state)

    def source_clean(self):
        listing = subprocess.run(["git", "-C", str(self.root), "status", "--porcelain",
                                  "--", ".", ":(exclude).autocode", ":(exclude)registry-home"],
                                 capture_output=True, text=True).stdout.strip()
        return not listing


class ApprovalScenarios(ApprovalCase):

    def test_app01_clarify_without_implementing(self):
        """APP-01. Existing: test_goals.test_vague_task_starts_read_only_discovery_and_waits."""
        def planner(**kwargs):
            self.bundle.operation("planner_launch", allow_write=kwargs["allow_write"],
                                  sandbox=kwargs["sandbox"])
            return {"contract": body(questions=True), "summary": "Need interface"}, {
                "output": str(self.run / "draft.json"), "duration_seconds": 0.01}
        self.state.update(next_stage="astra_discovery", phase="PLANNING", status="RUNNING")
        code = self.invoke(role=planner)
        self.check("awaits_user", "WAITING_FOR_USER", self.state["status"])
        self.check("exit_signalled_waiting", 2, code)
        self.check("approval_not_inferred", False, goals.approved(self.state))
        self.compare_with_oracle()
        self.check("planner_was_read_only", (False, "read-only"),
                   (self.bundle.operations[0]["allow_write"], self.bundle.operations[0]["sandbox"]))
        self.check("no_source_mutation", True, self.source_clean())
        self.check("draft_contract_saved", True, bool(self.state.get("goal_contract")))
        self.check("one_understandable_request", True,
                   bool(self.state.get("pending_questions") or self.state.get("displayed_goal")))
        # Recovery: answering and approving the exact plan starts the build path.
        self.assertEqual(0, self.invoke("--answer", "Q1=CLI"))
        self.draft()  # answered question incorporated; no open blockers remain
        goals.present(self.state)
        goals.approve(self.state, goals.token(self.state["goal_contract"]))
        self.check_true("recovery_approval_opens_execution", goals.approved(self.state))
        self.compare_with_oracle()
        self.finish(summary="AWAITING_APPROVAL: read-only clarification, no writer, approval never inferred")

    def test_app02_refuse_build_without_approval(self):
        """APP-02. Existing: partial (guard inside other tests); explicit both-path case new."""
        self.draft()
        self.bundle.state("before", {"approved": goals.approved(self.state),
                                     "revision": self.state["goal_contract"]["revision"]})
        before = copy.deepcopy(self.state)
        self.expect_raises("apply_refused_without_approval", support.Paused,
                           runner.apply_result, self.state, "terra",
                           {**envelope(self.state), "summary": "build", "evidence_refs": []},
                           {"output": "x", "changed_files": []}, self.root, self.run)
        self.check("state_unchanged_by_refusal", before, self.state)
        self.assertEqual(2, self.invoke("--resume-paused"))
        self.check("resume_still_asks_approval", "AWAITING_GOAL_APPROVAL", self.state["status"])
        self.check("resume_launched_nothing", [], [op for op in self.bundle.operations
                                                   if op["kind"] == "provider_launch"])
        self.check("no_source_mutation", True, self.source_clean())
        self.compare_with_oracle()
        # Recovery: authentic approval admits exactly one build path.
        goals.present(self.state)
        goals.approve(self.state, goals.token(self.state["goal_contract"]))
        try:
            goals.execution_guard(self.state)
            self.check("recovered_execution_authorized", True, True)
        except support.Paused as error:
            self.check("recovered_execution_authorized", "authorized execution", str(error))
        self.finish(summary="PAUSED_SAFE: unapproved contract refuses writers on both paths; draft preserved")

    def test_app03_approve_exact_displayed_plan_once(self):
        """APP-03. Existing: test_goals.test_approval_requires_displayed_exact_revision."""
        self.draft()
        goals.present(self.state)
        selected = goals.token(self.state["goal_contract"])
        goals.approve(self.state, selected)
        self.compare_with_oracle()
        revision = self.state["goal_contract"]["revision"]
        self.check("approval_binds_current_revision", f"r{revision}:",
                   self.state["goal_contract"]["approval_event"]["token"][:len(f"r{revision}:")])
        goals.assign_task(self.state, self.decision(), support.snapshot(self.root))
        first_task = self.state["current_task"]["id"]
        self.check("one_build_assignment", True, bool(first_task))
        self.check("no_stage_dispatch_yet", 0, len(self.state["stages"]))
        self.check("approval_event_recorded_once", 1,
                   sum(1 for e in self.state["user_events"] if e.get("kind") == "goal_approval"))
        self.finish(summary="READY_BUILD: exact-token approval and one saved build assignment")

    def test_app04_reject_stale_plan_approval(self):
        """APP-04. Existing: test_goals.test_approval_requires_displayed_exact_revision (stale branch)."""
        self.approve_now()
        stale = goals.token(self.state["goal_contract"])
        revised = body()
        revised["required_behaviors"].append("Support Unicode names")
        goals.install_draft(self.state, revised, origin="user_edit")
        goals.present(self.state)
        self.expect_raises("stale_token_rejected", ValueError, goals.approve, self.state, stale)
        self.check("r2_remains_unapproved", False, goals.approved(self.state))
        self.compare_with_oracle()
        self.expect_raises("no_task_admitted_by_stale_event", support.Paused,
                           goals.execution_guard, self.state)
        # Recovery: approving the displayed R2 token admits work again.
        goals.approve(self.state, goals.token(self.state["goal_contract"]))
        self.check_true("r2_approval_recovers", goals.approved(self.state))
        self.compare_with_oracle()
        self.finish(summary="AWAITING_APPROVAL: stale R1 token cannot approve R2")

    def test_app05_duplicate_approval_delivery_is_harmless(self):
        """APP-05. New: repeated identical approval keeps one effective authorization."""
        self.approve_now()
        selected = goals.token(self.state["goal_contract"])
        events_after_first = len(self.state["user_events"])
        before_duplicate = copy.deepcopy(self.state)
        # A re-delivered approval is explicitly refused (not silently re-applied):
        # an equivalent trace with unchanged authorization state.
        self.expect_raises("duplicate_delivery_refused", ValueError,
                           goals.approve, self.state, selected)
        self.check("duplicate_left_state_unchanged", before_duplicate, self.state)
        self.check("still_approved", True, goals.approved(self.state))
        self.compare_with_oracle()
        self.check("single_effective_approval_event", events_after_first, len(self.state["user_events"]))
        goals.assign_task(self.state, self.decision(), support.snapshot(self.root))
        self.check("one_assignment_only", True, bool(self.state["current_task"]["id"]))
        self.check("no_writer_duplicated", 0, len(self.state["stages"]))
        self.finish(summary="READY_BUILD: duplicate approval delivery is a no-op")

    def test_app06_answers_recovery_and_resume_are_not_approval(self):
        """APP-06. Existing: test_goals.test_answer_is_never_approval_and_resume_does_not_bypass..."""
        draft = body(questions=True)
        draft["open_blocking_questions"].append({**draft["open_blocking_questions"][0], "id": "Q2"})
        goals.install_draft(self.state, draft, origin="test")
        self.assertEqual(0, self.invoke("--answer", "Q1=CLI"))
        self.check("answer_saved_once", "CLI", self.state["answers"]["Q1"]["text"])
        self.check("answer_is_not_approval", False, goals.approved(self.state))
        self.compare_with_oracle()
        self.assertEqual(2, self.invoke("--resume-paused"))
        self.check("resume_keeps_remaining_question", ["Q2"],
                   [q["id"] for q in self.state["pending_questions"]])
        self.check("still_unapproved_after_resume", False, goals.approved(self.state))
        self.finish(summary="AWAITING_APPROVAL: clarification, recovery and Resume never approve")

    def test_app07_reject_contract_altered_after_approval(self):
        """APP-07. Existing: test_goals.test_in_place_tampering_and_model_approval_claim..."""
        self.approve_now()
        sealed_contract = copy.deepcopy(self.state["goal_contract"])
        self.state["goal_contract"]["body"]["scope_exclusions"] = []
        self.check("tampered_seal_fails", False, goals.approved(self.state))
        self.compare_with_oracle()
        self.expect_raises("no_dispatch_from_tampered_contract", support.Paused,
                           goals.execution_guard, self.state)
        self.check("definitions_not_silently_replaced", sealed_contract["body"]["required_behaviors"],
                   body()["required_behaviors"])
        # Recovery: a legitimate revision through the supported path re-approves.
        self.state["goal_contract"] = sealed_contract
        self.check_true("restored_seal_valid_again", goals.approved(self.state))
        self.finish(summary="PAUSED_SAFE: post-approval tampering breaks the seal and refuses dispatch")

    def test_app08_reject_fabricated_approval_receipt(self):
        """APP-08. Existing: test_goals.test_in_place_tampering... and test_user_decisions_cannot_be_invented."""
        self.draft()
        self.state["goal_contract"]["approval_status"] = "approved"
        self.check("shaped_field_is_not_authorization", False, goals.approved(self.state))
        self.state["goal_contract"]["approval_event"] = {"token": goals.token(self.state["goal_contract"]),
                                                         "actor": "user_cli"}
        self.check("actor_text_without_user_event_is_not_authorization", False, goals.approved(self.state))
        self.compare_with_oracle()
        forged = body()
        forged["delegated_decisions"] = [{"text": "Use web", "basis": "delegated", "answer_id": "invented"}]
        self.expect_raises("agent_forged_answer_id_rejected", ValueError,
                           goals.install_draft, self.state, forged, origin="agent")
        self.finish(summary="PAUSED_SAFE: fabricated receipts and forged answer ids rejected")

    def test_app09_scope_revision_requires_new_approval(self):
        """APP-09. Existing: test_goals.test_goal_change_answer_requires_new_revision_and_approval
        and test_edit_invalidates_approval_validation_and_human_review."""
        self.approve_now()
        old_token = goals.token(self.state["goal_contract"])
        goals.wait_for_user(self.state, {"kind": "goal_change", "decision_needed": "Allow Unicode?",
                                         "impact": "Changes scope", "discovered": "Non-ASCII names",
                                         "options": ["Yes", "No"], "proposed_delta": "Accept Unicode"})
        goals.answer(self.state, self.state["pending_questions"][0]["id"], "Yes")
        revised = body()
        revised["required_behaviors"].append("Accept Unicode")
        prior_revision = self.state["goal_contract"]["revision"]
        goals.install_draft(self.state, revised, origin="astra_discovery")
        self.check("r2_versioned", prior_revision + 1, self.state["goal_contract"]["revision"])
        self.check("old_approval_does_not_cover_r2", False, goals.approved(self.state))
        self.expect_raises("old_token_cannot_approve_r2", ValueError, goals.approve, self.state, old_token)
        self.check("history_explains_change", True, bool(self.state.get("contract_history")))
        goals.present(self.state)
        goals.approve(self.state, goals.token(self.state["goal_contract"]))
        self.check_true("r2_approval_recovers", goals.approved(self.state))
        self.compare_with_oracle()
        self.finish(summary="AWAITING_APPROVAL: authorized scope change produces versioned R2 needing approval")

    def test_app10_denied_and_qualified_permission(self):
        """APP-10. Existing: test_goals.test_exact_permission_reuses_real_answer_including_a_denial
        and test_permission_reuse_never_expands_scope_or_trusts_missing_provenance."""
        self.approve_now()
        request = {"kind": "permission", "decision_needed": "Repair the fallback test?",
                   "impact": "The exact test is excluded", "options": ["Repair", "Keep excluded"],
                   "discovered": "An assertion races navigation", "proposed_delta": "Only the fallback test"}
        goals.wait_for_user(self.state, request)
        qid = self.state["pending_questions"][0]["id"]
        goals.resolve_permission(self.state, qid, "No, leave it excluded")  # denial
        self.check("denial_recorded", "No, leave it excluded", self.state["answers"][qid]["text"])
        goals.wait_for_user(self.state, copy.deepcopy(request))
        self.check("denial_reused_not_escalated", "RUNNING", self.state["status"])
        self.check("denial_answer_bound", "No, leave it excluded",
                   self.state["permission_reuse_context"]["answer"])
        original_contract = copy.deepcopy(self.state["goal_contract"])
        wider = copy.deepcopy(request)
        wider["proposed_delta"] = "Change production navigation too"
        goals.wait_for_user(self.state, wider)
        self.check("wider_scope_not_authorized_by_qualification", "WAITING_FOR_USER", self.state["status"])
        self.check("contract_untouched_by_permission_flow", original_contract,
                   self.state["goal_contract"])
        self.finish(summary="WAITING_USER_OR_SCOPED_PROGRESS: denials stay denials; scope never widens")

    def test_app11_reuse_answered_question_without_looping(self):
        """APP-11. Existing: test_goals.test_existing_answers_persist_and_cannot_be_asked_again."""
        self.draft(questions=True)
        self.assertEqual(0, self.invoke("--answer", "Q1=CLI"))
        # Asking the already-answered question again is explicitly refused.
        self.expect_raises("same_question_rejected", ValueError,
                           self.draft, questions=True)
        self.check("prior_answer_retained", "CLI", self.state["answers"]["Q1"]["text"])
        draft = body(questions=True)
        draft["open_blocking_questions"][0].update(id="Q9", question="A materially different question?")
        goals.install_draft(self.state, draft, origin="test")
        self.check("distinct_new_question_allowed", ["Q9"],
                   [q["id"] for q in self.state["pending_questions"]])
        self.finish(summary="SCOPED_PROGRESS: exact answers reused; genuinely new questions still asked")

    def test_app12_tracked_requirement_survives_handoff(self):
        """APP-12. Existing: test_goals.test_stale_role_result_and_criterion_weakening..."""
        self.approve_now()
        goals.assign_task(self.state, self.decision(), support.snapshot(self.root))
        tracked = {c["id"] for c in self.state["acceptance_criteria"]}
        self.check("criteria_tracked", {"C1"}, tracked)
        for label, mutate in (
            ("dropped", lambda d: d["acceptance_criteria"].clear()),
            ("redefined", lambda d: d["acceptance_criteria"][0].update(criterion="Weaker goal")),
        ):
            decision = self.decision()
            mutate(decision)
            before = copy.deepcopy(self.state)
            with self.subTest(variant=label):
                self.expect_raises(f"[{label}] requirement_loss_rejected", support.Paused,
                                   runner.apply_result, self.state, "astra_review", decision,
                                   {"output": "review.json"}, self.root, self.run)
            self.check(f"[{label}] contract_unchanged", before, self.state)
        self.check("original_requirement_present", tracked,
                   {c["id"] for c in self.state["acceptance_criteria"]})
        self.finish(summary="PAUSED_SAFE: dropped or redefined criteria rejected at the handoff")


if __name__ == "__main__":
    unittest.main()
