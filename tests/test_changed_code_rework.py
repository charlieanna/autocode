"""A narrowed batch repair cannot silently authorize another component's tests."""

import copy
import unittest

import autocode_changed_code_rework as rework
import autocode_findings as findings
import autocode_recovery_history as history

from .test_findings import astra, resolved, sol


class ChangedCodeRepairScopeTests(unittest.TestCase):
    def fixture(self):
        state = {
            "status": "RUNNING",
            "iteration": 9,
            "current_task": {
                "id": "repair-store",
                "milestone_id": "D",
                "milestone_ids": ["D", "E"],
                "affected_paths": ["tests/store/test_store.py"],
                "requirements": ["Repair only the storage tests; do not modify other files."],
            },
            "goal_contract": {
                "hash": "plan-one",
                "revision": 3,
                "body": {
                    "milestones": [
                        {"id": "D", "affected_paths": ["app/store/", "tests/store/"]},
                        {"id": "E", "affected_paths": ["app/evaluate/", "tests/evaluate/"]},
                    ]
                },
            },
            "regression_proof": {
                "verdict": "PASS",
                "changed_code_evidence": {
                    "source_revision": "source-one",
                    "status": "UNVERIFIED",
                    "reason": "Comparison branches have no proving assertions",
                },
            },
        }
        record = {"stage": "tester", "role": "tester", "source_revision": "source-one", "output": "/run/tester.json"}
        rejected = [{"path": "app/evaluate/core.py", "line": 136, "reason": "Assert unequal values for <=."}]
        return state, record, rejected

    def test_validation_only_task_returns_to_review_with_evidence_and_counters_intact(self):
        for stage, role in (("tester", "tester"), ("review_checkpoint", "task_planner")):
            with self.subTest(stage=stage):
                state, record, _ = self.fixture()
                state["current_task"] = {"id": "validate-retained", "kind": "validate", "milestone_id": "D"}
                state["no_progress_batches"] = 3
                record.update(stage=stage, role=role)
                preserved = copy.deepcopy(state)
                self.assertTrue(rework.route(state, record, investigation_enabled=True))
                self.assertEqual("review_completion", state["next_stage"])
                self.assertIn("validation-only", state["next_action"])
                self.assertIn("within the approved plan", state["next_action"])
                self.assertIn(
                    "retain the unverified lines and all outstanding milestone obligations", state["next_action"]
                )
                for key in ("current_task", "goal_contract", "regression_proof", "iteration", "no_progress_batches"):
                    self.assertEqual(preserved[key], state[key], key)
                event = state["regression_proofs"][-1]
                self.assertEqual("reviewer", event["action"])
                self.assertNotIn("round", event)
                self.assertFalse(rework.queued(state, record))
                self.assertTrue(rework.route(state, record, investigation_enabled=True))
                self.assertEqual(1, len(state["regression_proofs"]))

    def test_validation_only_scope_review_still_requires_independent_current_failed_line_evidence(self):
        for variant in ("builder", "failed_proof", "settled_lines", "stale_source"):
            with self.subTest(variant=variant):
                state, record, _ = self.fixture()
                state["current_task"]["kind"] = "validate"
                if variant == "builder":
                    record.update(stage="builder", role="builder")
                elif variant == "failed_proof":
                    state["regression_proof"]["verdict"] = "FAIL"
                elif variant == "settled_lines":
                    state["regression_proof"]["changed_code_evidence"]["status"] = "PASS"
                else:
                    record["source_revision"] = "stale-source"
                before = copy.deepcopy(state)
                if variant == "stale_source":
                    with self.assertRaisesRegex(ValueError, "exact source"):
                        rework.route(state, record, investigation_enabled=True)
                else:
                    self.assertFalse(rework.route(state, record, investigation_enabled=True))
                self.assertEqual(before, state)

    def test_scope_handoff_leaves_a_tester_finding_for_its_fresh_owner_review(self):
        state, record, _ = self.fixture()
        state["current_task"] = {"id": "validate-retained", "kind": "validate"}
        state["goal_contract"]["body"] = {"acceptance_criteria": [{"id": "C1"}]}
        findings.record_validation(state, sol("Lower return has no assertion"), {"output": "tester-first"})
        ident = findings.open_entries(state, "tester")[0]["id"]
        rework.route(state, record, investigation_enabled=True)
        findings.record_decision(state, astra("CONTINUE", dispositions=[resolved(ident)]), {"output": "scope-review"})
        self.assertEqual([ident], [row["id"] for row in findings.open_entries(state, "tester")])
        repaired = sol(dispositions=[resolved(ident)])
        with self.assertRaisesRegex(ValueError, "report-only repair"):
            findings.record_validation(state, repaired, {"output": "tester-repair", "report_repaired": True})
        self.assertEqual([ident], [row["id"] for row in findings.open_entries(state, "tester")])
        repaired.update(
            verdict="PASS", criterion_results=[{"id": "C1", "status": "PASS", "evidence_refs": ["event:fresh"]}]
        )
        findings.record_validation(state, repaired, {"output": "tester-fresh"})
        self.assertEqual([], findings.open_entries(state, "tester"))

    def test_narrowed_batch_assignment_returns_to_review_without_spending_builder_round(self):
        state, record, rejected = self.fixture()
        original_task = copy.deepcopy(state["current_task"])
        original_contract = copy.deepcopy(state["goal_contract"])
        self.assertTrue(rework.route(state, record, investigation_enabled=True, rejected=rejected))
        self.assertEqual("review_completion", state["next_stage"])
        self.assertEqual(9, state["iteration"])
        self.assertEqual(original_task, state["current_task"])
        self.assertEqual(original_contract, state["goal_contract"])
        event = state["regression_proofs"][-1]
        self.assertEqual("reviewer", event["action"])
        self.assertIn("E", event["scope_reason"])
        self.assertIn("app/evaluate/core.py:136", state["next_action"])
        self.assertIn("reassign", state["next_action"].lower())
        self.assertEqual("UNVERIFIED", state["regression_proof"]["changed_code_evidence"]["status"])
        # Reconciliation is idempotent; a combined checkpoint must still apply its review decision.
        self.assertTrue(rework.route(state, record, investigation_enabled=True, rejected=rejected))
        self.assertEqual(1, len(state["regression_proofs"]))
        self.assertFalse(rework.queued(state, record))

    def test_scope_review_does_not_consume_the_later_authorized_builder_allowance(self):
        state, record, rejected = self.fixture()
        rework.route(state, record, investigation_enabled=True, rejected=rejected)
        rework.route(
            state, {**record, "output": "/run/second-review.json"}, investigation_enabled=True, rejected=rejected
        )
        state["current_task"]["affected_paths"].append("tests/evaluate/")
        rework.route(
            state, {**record, "output": "/run/next-tester.json"}, investigation_enabled=True, rejected=rejected
        )
        self.assertEqual(
            ("builder", "builder", 10),
            (state["next_stage"], state["regression_proofs"][-1]["action"], state["iteration"]),
        )

    def test_test_only_assignment_for_the_same_component_keeps_direct_repair(self):
        state, record, rejected = self.fixture()
        rejected[0]["path"] = "app/store/core.py"
        rework.route(state, record, investigation_enabled=False, rejected=rejected)
        self.assertEqual("builder", state["next_stage"])
        self.assertEqual("builder", state["regression_proofs"][-1]["action"])

    def test_combined_checkpoint_can_apply_its_review_instead_of_repeating_the_tester(self):
        state, record, rejected = self.fixture()
        record.update(stage="review_checkpoint", role="task_planner")
        rework.route(state, record, investigation_enabled=False, rejected=rejected)
        self.assertEqual("review_completion", state["next_stage"])
        self.assertFalse(rework.queued(state, record))

    def test_full_batch_and_legacy_assignments_keep_existing_routing(self):
        for variant in ("full_batch", "parent_path", "single", "no_paths", "unknown_owner", "prefix_sibling"):
            with self.subTest(variant=variant):
                state, record, rejected = self.fixture()
                if variant == "full_batch":
                    state["current_task"]["affected_paths"].append("tests/evaluate/test_comparisons.py")
                elif variant == "parent_path":
                    state["current_task"]["affected_paths"] = ["tests/"]
                elif variant == "single":
                    state["current_task"].pop("milestone_ids")
                elif variant == "no_paths":
                    state["current_task"].pop("affected_paths")
                elif variant == "unknown_owner":
                    rejected[0]["path"] = "another/core.py"
                else:
                    rejected[0]["path"] = "app/evaluate_extra/core.py"
                rework.route(state, record, investigation_enabled=False, rejected=rejected)
                self.assertEqual("builder", state["next_stage"])

    def test_a_builder_round_is_recorded_with_the_rounds_the_gate_allows(self):
        # The status view says "round 1 of 2" from the record alone; a scope review spends no round.
        state, record, rejected = self.fixture()
        state["current_task"]["affected_paths"].append("tests/evaluate/test_comparisons.py")
        rework.route(state, record, investigation_enabled=False, rejected=rejected)
        event = state["regression_proofs"][-1]
        self.assertEqual(("builder", 1, rework.REPAIR_ROUNDS), (event["action"], event["round"], event["round_limit"]))
        self.assertIn("(round 1 of 2)", history.project(state)[-1]["headline"])
        again = {**record, "output": "/run/tester-2.json"}
        rework.route(state, again, investigation_enabled=False, rejected=rejected)
        self.assertEqual(
            ("builder", 2), (state["regression_proofs"][-1]["action"], state["regression_proofs"][-1]["round"])
        )
        third = {**record, "output": "/run/tester-3.json"}
        rework.route(state, third, investigation_enabled=False, rejected=rejected)
        self.assertEqual(
            ("investigator", 3), (state["regression_proofs"][-1]["action"], state["regression_proofs"][-1]["round"])
        )
        scoped_state, scoped_record, scoped_rejected = self.fixture()
        rework.route(scoped_state, scoped_record, investigation_enabled=True, rejected=scoped_rejected)
        self.assertNotIn("round", scoped_state["regression_proofs"][-1])

    def test_recovery_view_names_reassignment_instead_of_a_builder_attempt(self):
        state, record, rejected = self.fixture()
        rework.route(state, record, investigation_enabled=False, rejected=rejected)
        rows = history.project(state)
        self.assertTrue(rows)
        self.assertIn("reassign", str(rows).lower())
        self.assertNotIn("Builder sent back for untested lines", str(rows))

    def test_builder_round_reminds_builder_to_stay_within_current_task_affected_paths(self):
        state, record, rejected = self.fixture()
        rejected[0]["path"] = "app/store/core.py"
        state["current_task"]["affected_paths"] = ["app/store/", "tests/store/test_store.py"]
        rework.route(state, record, investigation_enabled=False, rejected=rejected)
        self.assertEqual("builder", state["next_stage"])
        self.assertIn("current_task.affected_paths (app/store/, tests/store/test_store.py)", state["next_action"])
        self.assertIn("only inside those assigned paths", state["next_action"])


class BuilderEvidenceTests(unittest.TestCase):
    def test_a_builder_gets_open_lines_whole_and_settled_lines_by_number(self):
        def row(path, line, status):
            return {
                "gap_id": f"changed-code:{path}:{line}",
                "path": path,
                "line": line,
                "status": status,
                "evidence": ["/proof/mutant.log"],
                "mutation_outcomes": ["survived"],
                "reason": "r" * 400,
            }

        assessment = {
            "schema": 1,
            "source_revision": "rev",
            "proof_context": "ctx",
            "status": "UNVERIFIED",
            "reason": "Changed product lines still need behavioral evidence: a.py:3",
            "fallback": ["mutation_only"],
            "obligations": [
                row("a.py", 1, "tested"),
                row("a.py", 2, "excepted"),
                row("a.py", 3, "reason_required"),
                row("b.py", 7, "tested"),
                row("b.py", 9, "reason_required"),
            ],
        }
        original = copy.deepcopy(assessment)
        view = rework.builder_evidence(assessment)
        self.assertEqual(original, assessment)
        self.assertEqual([original["obligations"][2], original["obligations"][4]], view["obligations"])
        self.assertEqual({"tested": {"a.py": [1], "b.py": [7]}, "excepted": {"a.py": [2]}}, view["settled_lines"])
        self.assertEqual(
            {key: value for key, value in original.items() if key != "obligations"},
            {key: value for key, value in view.items() if key not in ("obligations", "settled_lines")},
        )

    def test_an_open_line_nothing_observed_is_listed_by_number(self):
        # A proof that did not pass runs no mutant, so every changed line is open with nothing to read.
        def row(line, **observed):
            return {"path": "a.py", "line": line, "status": "reason_required", "mutation_outcomes": [], **observed}

        rows = [
            row(1),
            row(2, mutation_outcomes=["survived"]),
            row(3, evidence=["/proof/m.log"]),
            row(4, executed=True),
        ]
        view = rework.builder_evidence({"status": "UNVERIFIED", "obligations": rows})
        self.assertEqual(rows[1:], view["obligations"])
        self.assertEqual({"a.py": [1]}, view["unmeasured_lines"])
        self.assertNotIn("settled_lines", view)

    def test_evidence_with_nothing_settled_or_no_rows_keeps_its_shape(self):
        open_only = {
            "status": "UNVERIFIED",
            "obligations": [
                {"path": "a.py", "line": 1, "status": "reason_required", "mutation_outcomes": ["survived"]}
            ],
        }
        self.assertEqual(open_only, rework.builder_evidence(open_only))
        self.assertEqual({"status": "UNVERIFIED"}, rework.builder_evidence({"status": "UNVERIFIED"}))
