"""Completed-turn receipts admit unchanged old tests without weakening fresh proof."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_acceptance_provenance as provenance
import autocode_acceptance_recovery as recovery
import autocode_acceptance_tests as acceptance
import autocode_acceptance_tests_job as job
import autocode_contract_identity as identity
import autocode_planning_cycle as planning_cycle
import autocode_run_view as run_view
import autocode_util as util


def seal(body, revision, events):
    contract = {"task_id": "task", "revision": revision, "body": copy.deepcopy(body)}
    contract["hash"] = util.digest(contract)
    event = {"kind": "goal_approval", "actor": "user_cli", "token": identity.token(contract)}
    events.append(event)
    contract.update(approval_status="approved", approval_event=event, origin="astra_finalize")
    return contract


class AcceptanceFixture:
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.archive = self.root / "old-tests"
        self.old_code = b"def test_t1_negative():\n    assert total([2, -1]) == 2\n"
        self.archive.write_bytes(self.old_code)
        (self.workspace / "test_totals.py").write_bytes(
            self.old_code + b"\ndef test_s1_start():\n    assert total([], start=1) == 1\n"
        )
        old_row = {
            "id": "AC1",
            "criterion": "Negative input is excluded",
            "verification_method": "test: test_t1_negative",
            "human_review": False,
            "sources": [{"source_id": "task:0", "quote": "Exclude negatives"}],
        }
        events = []
        self.old = seal({"acceptance_criteria": [old_row], "open_blocking_questions": []}, 1, events)
        feedback = {
            "kind": "brief_feedback",
            "actor": "user_cli",
            "id": "feedback-turn",
            "text": "Add start",
            "contract_token": identity.token(self.old),
        }
        events.append(feedback)
        body = copy.deepcopy(self.old["body"])
        body["acceptance_criteria"].append(
            {"id": "AC2", "criterion": "Start is added", "verification_method": "test: test_s1_start"}
        )
        current = seal(body, 2, events)
        self.state = {
            "settings": {"spec_first": True},
            "task": "Add start",
            "workspace": str(self.workspace),
            "goal_contract": current,
            "contract_history": [self.old],
            "user_events": events,
            "brief_feedback": [feedback],
            "turns": [
                {
                    "event_id": feedback["id"],
                    "say": "Add start",
                    "stage_index": 1,
                    "previous": {"status": "TASK_COMPLETE"},
                }
            ],
            "stages": [{"stage": "astra_review"}],
            "next_stage": "terra",
            "acceptance_tests": {
                "receipts": {
                    "old": {
                        "contract_hash": self.old["hash"],
                        "case_ids": ["AC1"],
                        "results": {"AC1": "fails"},
                        "source_revision": "before-the-first-builder",
                        "files": {
                            "test_totals.py": {"archive": str(self.archive), "sha256": util.file_hash(self.archive)}
                        },
                    }
                }
            },
        }
        self.reports = [
            {"case_id": "AC1", "file": "test_totals.py", "test_name": "test_t1_negative"},
            {"case_id": "AC2", "file": "test_totals.py", "test_name": "test_s1_start"},
        ]

    def reseal(self):
        old_event = self.state["goal_contract"]["approval_event"]
        self.state["user_events"].remove(old_event)
        self.state["goal_contract"] = seal(self.state["goal_contract"]["body"], 2, self.state["user_events"])


class AcceptanceProvenanceTests(AcceptanceFixture, unittest.TestCase):
    def test_completed_turn_resolves_its_exact_approved_contract_without_mutation(self):
        before = copy.deepcopy(self.state)
        self.assertEqual(self.old, provenance.prior_contract(self.state))
        self.assertEqual(
            {"AC1"}, provenance.inherited_cases(self.state, reports=self.reports, workspace=self.workspace)
        )
        self.assertEqual(before, self.state)

    def test_identical_restored_history_copies_share_one_identity_but_conflicts_refuse(self):
        self.state["contract_history"].append(copy.deepcopy(self.old))
        self.assertEqual(self.old, provenance.prior_contract(self.state))
        self.assertEqual(
            {"AC1"}, provenance.inherited_cases(self.state, reports=self.reports, workspace=self.workspace)
        )
        self.state["contract_history"][-1]["origin"] = "conflicting-origin"
        self.assertIsNone(provenance.prior_contract(self.state))

    def test_incomplete_or_ambiguous_turn_and_approval_provenance_stays_closed(self):
        controls = [
            lambda s: s["turns"][-1]["previous"].update(status="RUNNING"),
            lambda s: s["turns"][-1].update(stage_index=99),
            lambda s: s["turns"][-1].update(stage_index=True),
            lambda s: s["brief_feedback"].clear(),
            lambda s: s["user_events"].pop(0),
            lambda s: s["user_events"].append(copy.deepcopy(s["brief_feedback"][0])),
            lambda s: s["contract_history"].clear(),
            lambda s: s["contract_history"][0]["body"].update(open_blocking_questions=[{}]),
        ]
        for change in controls:
            with self.subTest(change=change):
                state = copy.deepcopy(self.state)
                change(state)
                self.assertIsNone(provenance.prior_contract(state))
                self.assertEqual(set(), provenance.inherited_cases(state))

    def test_changed_criterion_selector_source_or_human_authority_is_new_work(self):
        for field, value in (
            ("criterion", "A different behavior"),
            ("verification_method", "test: test_new"),
            ("human_review", True),
            ("sources", []),
        ):
            with self.subTest(field=field):
                original = copy.deepcopy(self.state)
                self.state["goal_contract"]["body"]["acceptance_criteria"][0][field] = value
                self.reseal()
                self.assertEqual(
                    set(), provenance.inherited_cases(self.state, reports=self.reports, workspace=self.workspace)
                )
                self.state = original

    def test_bad_or_missing_generation_receipt_never_admits_inheritance(self):
        controls = [
            lambda r: r.update(contract_hash="another-plan"),
            lambda r: r.update(case_ids=[]),
            lambda r: r.update(results={"AC1": "passes"}),
            lambda r: r.update(source_revision=""),
            lambda r: r["files"]["test_totals.py"].update(sha256="damaged"),
            lambda r: r["files"]["test_totals.py"].update(archive=str(self.root / "missing")),
        ]
        for change in controls:
            with self.subTest(change=change):
                state = copy.deepcopy(self.state)
                change(state["acceptance_tests"]["receipts"]["old"])
                self.assertEqual(
                    set(), provenance.inherited_cases(state, reports=self.reports, workspace=self.workspace)
                )

    def test_changed_file_or_reported_binding_does_not_inherit(self):
        for field, value in (("file", "other.py"), ("test_name", "test_new")):
            reports = copy.deepcopy(self.reports)
            reports[0][field] = value
            self.assertEqual(set(), provenance.inherited_cases(self.state, reports=reports, workspace=self.workspace))
        (self.workspace / "test_totals.py").write_bytes(self.old_code.replace(b"== 2", b"== 1"))
        self.assertEqual(set(), provenance.inherited_cases(self.state, reports=self.reports, workspace=self.workspace))

    def test_context_distinguishes_delivered_tests_from_new_current_baseline_cases(self):
        acceptance.route(self.state)
        packet = job.packet(self.state)
        kinds = {row["id"]: row["kind"] for row in packet["cases"]}
        self.assertIn("already delivered", kinds["AC1"])
        self.assertIn("fresh original-reference fail-first proof remains required", kinds["AC1"])
        self.assertEqual("test: must fail today", kinds["AC2"])
        self.state["acceptance_tests"]["receipts"].clear()
        self.assertEqual("test: must fail today", job.packet(self.state)["cases"][0]["kind"])

    def apply(self, *, new_passes=False):
        acceptance.route(self.state)
        value = {
            "tests": self.reports,
            "cannot_write": [],
            "already_passing": [{"case_id": "AC1", "evidence": "old test passes current fixed code"}],
        }
        if new_passes:
            value["already_passing"].append({"case_id": "AC2", "evidence": "new behavior already exists"})
        sent = []
        results = {
            "passed": ["test_t1_negative"] + (["test_s1_start"] if new_passes else []),
            "failed": [] if new_passes else ["test_s1_start"],
            "complete": True,
        }
        job.apply(
            self.state,
            value,
            {
                "output": str(self.root / "new-report.json"),
                "changed_files": ["test_totals.py"],
                "source_revision": "second-generation",
            },
            self.workspace,
            run_tests=lambda files: {"command": "pytest", "results": results},
            evidence_dir=self.root,
            send_back=lambda body, changes: sent.append((body, changes)),
        )
        return sent

    def test_old_current_green_case_keeps_approved_test_marker_and_proceeds_to_builder(self):
        approved = copy.deepcopy(self.state["goal_contract"])
        self.assertEqual([], self.apply())
        self.assertEqual("terra", self.state["next_stage"])
        self.assertEqual(approved, self.state["goal_contract"])
        receipt = next(
            row for row in acceptance.receipts(self.state).values() if row["contract_hash"] == approved["hash"]
        )
        self.assertEqual({"AC1": "already passes", "AC2": "fails"}, receipt["results"])

    def test_new_current_green_case_still_requires_a_reviewed_correction(self):
        sent = self.apply(new_passes=True)
        self.assertEqual(1, len(sent))
        methods = {row["id"]: row["verification_method"] for row in sent[0][0]["acceptance_criteria"]}
        self.assertEqual("test: test_t1_negative", methods["AC1"])
        self.assertEqual("guard: test_s1_start", methods["AC2"])

    def third_turn(self):
        self.apply()
        completed = copy.deepcopy(self.state["goal_contract"])
        event = {
            "kind": "brief_feedback",
            "actor": "user_cli",
            "id": "feedback-third",
            "text": "Continue again",
            "contract_token": identity.token(completed),
        }
        self.state["user_events"].append(event)
        self.state["brief_feedback"].append(event)
        self.state["stages"].append({"stage": "astra_review"})
        self.state["turns"].append(
            {"event_id": event["id"], "say": event["text"], "stage_index": 2, "previous": {"status": "TASK_COMPLETE"}}
        )
        self.state["contract_history"].append(completed)
        self.state["goal_contract"] = seal(completed["body"], 3, self.state["user_events"])
        return completed

    def test_third_turn_follows_intact_completed_fail_first_origin_without_normalizing_results(self):
        completed = self.third_turn()
        before = copy.deepcopy(self.state)
        self.assertIn("AC1", provenance.inherited_cases(self.state, reports=self.reports, workspace=self.workspace))
        current_receipt = next(
            row for row in acceptance.receipts(self.state).values() if row["contract_hash"] == completed["hash"]
        )
        self.assertEqual("already passes", current_receipt["results"]["AC1"])
        self.assertEqual(before, self.state)

    def test_third_turn_requires_the_exact_authenticated_completed_fail_first_origin(self):
        self.third_turn()
        original = copy.deepcopy(self.state)
        controls = [
            lambda s: s["acceptance_tests"]["receipts"].pop("old"),
            lambda s: s["turns"][0]["previous"].update(status="RUNNING"),
            lambda s: s["contract_history"][0]["body"]["acceptance_criteria"][0].update(criterion="Other behavior"),
            lambda s: s["user_events"].pop(0),
            lambda s: s["acceptance_tests"]["receipts"]["old"]["files"]["test_totals.py"].update(sha256="changed"),
        ]
        for change in controls:
            with self.subTest(change=change):
                state = copy.deepcopy(original)
                change(state)
                self.assertNotIn(
                    "AC1", provenance.inherited_cases(state, reports=self.reports, workspace=self.workspace)
                )


class AcceptanceRecoveryFixture(AcceptanceFixture):
    def setUp(self):
        super().setUp()
        spec = {
            "kind": "implement",
            "milestone_id": "M1",
            "objective": "Add start",
            "affected_paths": ["totals.py", "test_totals.py"],
            "requirements": ["Add start"],
            "acceptance_criteria": ["AC1", "AC2"],
            "validation_plan": ["pytest"],
        }
        self.state["goal_contract"]["body"]["initial_task"] = spec
        self.reseal()
        self.approved = copy.deepcopy(self.state["goal_contract"])
        acceptance.route(self.state)
        self.value = {
            "summary": "One inherited test and one new test",
            "tests": self.reports,
            "cannot_write": [],
            "already_passing": [{"case_id": "AC1", "evidence": "old test passes"}],
            "tests_run": ["pytest"],
        }
        output, events = self.root / "report.json", self.root / "report.jsonl"
        output.write_text(json.dumps(self.value))
        rows = []
        for kind, extra in (
            ("step_start", {}),
            ("text", {"text": json.dumps(self.value)}),
            (
                "step_finish",
                {
                    "reason": "stop",
                    "tokens": {"input": 1, "output": 1, "reasoning": 0, "cache": {"read": 0, "write": 0}},
                },
            ),
        ):
            rows.append(
                {
                    "type": kind,
                    "sessionID": "ses_fixture",
                    "part": {"id": "prt_" + kind, "sessionID": "ses_fixture", "messageID": "msg_fixture", **extra},
                }
            )
        events.write_text("\n".join(json.dumps(row) for row in rows))
        record = {
            "stage": job.STAGE,
            "contract_hash": self.approved["hash"],
            "exit_code": 0,
            "accounted": True,
            "output": str(output),
            "events": str(events),
            "engine": "opencode",
            "output_mode": "opencode_events",
            "source_revision": "after-generation",
            "task_id": "task-current",
            "metrics": {
                "request_context": {
                    "accounting": {"event_file": {"path": str(events), "sha256": util.file_hash(events)}}
                }
            },
        }
        self.state["stages"].append(record)
        self.state["task_archive"] = [
            {**spec, "id": "task-current", "contract_hash": self.approved["hash"], "contract_revision": 2}
        ]
        acceptance.record_send_back(self.state, ["AC1"], str(output))
        names = {"AC1": "test_t1_negative"}
        changes = [
            {
                "item": "AC1",
                "change": "reworded",
                "basis": "runner_proof",
                "answer_id": "",
                "replacement": "guard: test_t1_negative (the runner saw it pass on today's code: old test passes)",
            }
        ]
        contract = {"task_id": "task", "revision": 3, "body": job.corrected_body(self.state, names)}
        contract["hash"] = util.digest(contract)
        contract.update(origin=job.ORIGIN, approval_status="draft", approval_event=None, declared_changes=changes)
        self.state["contract_history"].append(copy.deepcopy(self.approved))
        self.state["goal_contract"] = contract
        self.old_planning = {
            "astra_calls": 3,
            "review_depth_reduced": True,
            "review_calls_before_policy": 1,
            "review_call_limit": 3,
            "review_call_limit_origin": "runner_default",
            "revision_review_renewals": 1,
            "turn_event_id": "feedback-turn",
            "final_token": identity.token(self.approved),
            "reports": {},
        }
        self.state["planning"] = copy.deepcopy(self.old_planning)
        planning_cycle.new_cycle(self.state)
        self.state.update(status="PAUSED_PLANNING_BUDGET", next_stage="astra_challenge", current_task=None)
        self.enterContext(
            patch.object(recovery.source_scope, "snapshot", return_value={"revision": "after-generation"})
        )


class AcceptanceRecoveryTests(AcceptanceRecoveryFixture, unittest.TestCase):
    def test_saved_runner_only_draft_names_pure_resume_and_restores_exact_approval(self):
        before = copy.deepcopy(self.state)
        self.assertTrue(recovery.can_resume(self.state))
        action = run_view.action(self.state)
        self.assertEqual("resume", action["needs"]["kind"])
        self.assertEqual("--resume-paused", action["needs"]["action"])
        self.assertNotIn("command_inputs", action["needs"])
        self.assertEqual(before, self.state)
        self.assertTrue(
            recovery.reconcile(
                self.state,
                self.workspace,
                at="now",
                evidence_dir=self.root / "recovery",
                run_tests=lambda files: {"results": {"passed": ["test_t1_negative"], "failed": ["test_s1_start"]}},
            )
        )
        self.assertEqual(self.approved, self.state["goal_contract"])
        self.assertEqual(self.old_planning, self.state["planning"])
        self.assertEqual(before["contract_history"], self.state["contract_history"][:-1])
        self.assertEqual(before["goal_contract"], self.state["contract_history"][-1])
        for key in ("stages", "user_events", "task_archive"):
            self.assertEqual(before[key], self.state[key])
        self.assertEqual("terra", self.state["next_stage"])
        self.assertFalse(recovery.can_resume(self.state))

    def test_user_edits_new_authority_source_changes_and_new_charges_forbid_recovery(self):
        controls = [
            lambda s: s["user_events"].append({"kind": "planning_budget_change", "limit": 2}),
            lambda s: s["planning"].update(astra_calls=4),
            lambda s: s["planning"].update(review_call_limit=2, review_call_limit_origin="user_explicit"),
            lambda s: s["goal_contract"].update(origin="user_cli_edit"),
            lambda s: s["goal_contract"]["body"].update(permission_boundaries=["new permission"]),
            lambda s: s["stages"].append({"stage": "astra_challenge", "exit_code": 0}),
            lambda s: s["stages"][-1].update(source_revision="another-source"),
            lambda s: s["task_archive"].clear(),
            lambda s: s["task_archive"][0].update(affected_paths=["another-file.py"]),
            lambda s: s["task_archive"][0].update(contract_revision=1),
            lambda s: s["task_archive"][0].update(objective="Different work"),
            lambda s: s["goal_contract"].update(approval_status="approved"),
            lambda s: s["stages"][-1].update(output_mode="report_file"),
            lambda s: s["stages"][-1].update(engine="codex"),
        ]
        for change in controls:
            with self.subTest(change=change):
                state = copy.deepcopy(self.state)
                change(state)
                before = copy.deepcopy(state)
                self.assertFalse(recovery.can_resume(state))
                self.assertEqual(before, state)

    def test_changed_saved_report_or_provider_events_stays_closed(self):
        record = self.state["stages"][-1]
        Path(record["output"]).write_text(json.dumps({**self.value, "summary": "changed"}))
        self.assertFalse(recovery.can_resume(self.state))
        Path(record["output"]).write_text(json.dumps(self.value))
        Path(record["events"]).write_text("changed events")
        self.assertFalse(recovery.can_resume(self.state))

    def test_historical_missing_runner_default_bound_recovers_without_changing_spend(self):
        self.state["planning"].pop("review_call_limit")
        self.state["planning"].pop("review_call_limit_origin")
        self.assertTrue(recovery.can_resume(self.state))

    def test_stale_build_token_or_pending_human_boundary_is_rejected_before_test_execution(self):
        original = copy.deepcopy(self.state)
        for condition in ("stale-token", "question", "request"):
            with self.subTest(condition=condition):
                candidate = copy.deepcopy(original)
                if condition == "question":
                    candidate["pending_questions"] = [{"id": "human", "question": "Decide scope"}]
                if condition == "request":
                    candidate["user_request"] = {"kind": "permission", "decision_needed": "Allow more files"}
                executed = []

                def run_tests(files):
                    recovery.require_boundary(candidate, "r999:stale" if condition == "stale-token" else None)
                    executed.append(files)

                with self.assertRaises(ValueError):
                    recovery.reconcile(candidate, self.workspace, at="now", run_tests=run_tests)
                self.assertEqual([], executed)
                self.assertEqual(original, self.state)


class RetainedReviewAllowanceTests(unittest.TestCase):
    def test_same_turn_restart_carries_bound_with_spent_calls_and_new_turn_keeps_fresh_default(self):
        for origin in ("runner_default", "user_explicit"):
            with self.subTest(origin=origin):
                prior = {
                    "astra_calls": 3,
                    "review_depth_reduced": True,
                    "review_calls_before_policy": 1,
                    "review_call_limit": 3,
                    "review_call_limit_origin": origin,
                    "revision_review_renewals": 1,
                    "reports": {},
                }
                state = {"planning": copy.deepcopy(prior), "settings": {}}
                planning_cycle.new_cycle(state)
                self.assertEqual(
                    (3, 3, origin),
                    (
                        state["planning"]["astra_calls"],
                        state["planning"]["review_call_limit"],
                        state["planning"]["review_call_limit_origin"],
                    ),
                )
                fresh = {"planning": copy.deepcopy(prior), "settings": {}}
                planning_cycle.new_cycle(fresh, new_turn=True)
                if origin == "runner_default":
                    self.assertEqual(0, fresh["planning"]["astra_calls"])
                    self.assertNotIn("review_call_limit", fresh["planning"])
                else:
                    self.assertEqual(
                        (3, 3, origin),
                        (
                            fresh["planning"]["astra_calls"],
                            fresh["planning"]["review_call_limit"],
                            fresh["planning"]["review_call_limit_origin"],
                        ),
                    )
