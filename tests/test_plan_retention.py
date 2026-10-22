"""Exact inheritance avoids model copying without relaxing contract protection."""

import copy
import json
import unittest

import autocode_contract_revision as revision
import autocode_plan_retention as retention
import autocode_planning_cycle as cycle
from units import autoplanner as planner


class PlanRetentionTests(unittest.TestCase):
    def state(self):
        row = {
            "id": "AC8",
            "criterion": "Given todos.json containing ID 1, bad usage leaves the bytes unchanged.",
            "verification_method": "guard: test_bad_usage",
            "human_review": False,
            "sources": [{"source_id": "task:0", "quote": "Preserve bytes."}],
        }
        body = {
            "acceptance_criteria": [row],
            "permission_boundaries": ["No network"],
            "required_behaviors": ["Preserve bytes."],
            "scope_exclusions": [],
            "constraints": [],
            "important_failure_cases": [],
        }
        return {
            "goal_contract": {
                "revision": 4,
                "hash": "saved-hash",
                "body": body,
                "approval_status": "approved",
                "origin": "glm_revise",
            },
            "planning": {
                "reports": {
                    "glm_revise": {
                        "report": {
                            "contract": body,
                            "requirement_trace": [
                                {"requirement_id": "R1", "disposition": "covered", "evidence": "AC8"}
                            ],
                        }
                    }
                }
            },
        }

    def test_inherited_row_is_exact_and_still_passes_protected_revision_guard(self):
        state = self.state()
        before = copy.deepcopy(state)
        value = {"contract": {**state["goal_contract"]["body"], "acceptance_criteria": retention.inventory(state)}}
        expanded = retention.expand(state, "astra_discovery", value)
        revision.revision_guard(state, expanded["contract"], [], "astra_discovery")
        self.assertEqual(before, state)
        self.assertEqual(state["goal_contract"]["body"], expanded["contract"])
        expanded["contract"]["acceptance_criteria"][0]["criterion"] = "corrupted"
        self.assertEqual(before, state)

    def test_real_behavior_and_proof_changes_are_not_normalized_away(self):
        for field, replacement in (
            ("criterion", "Bad usage may rewrite the bytes."),
            ("verification_method", "Run echo PASS"),
            ("human_review", True),
        ):
            state = self.state()
            value = {"contract": copy.deepcopy(state["goal_contract"]["body"])}
            value["contract"]["acceptance_criteria"][0][field] = replacement
            expanded = retention.expand(state, "glm_revise", value)
            with self.subTest(field=field), self.assertRaises(ValueError):
                revision.revision_guard(state, expanded["contract"], [], "glm_revise")

    def test_stale_unknown_and_overridden_criterion_references_are_refused(self):
        state = self.state()
        ref = retention.inventory(state)[0]
        for bad in (
            {**ref, "sha256": "stale"},
            {**ref, "retain_criterion": "AC999"},
            {**ref, "criterion": "weakened"},
            {**ref, "human_review": False},
        ):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "criterion reference"):
                retention.expand(state, "glm_revise", {"contract": {"acceptance_criteria": [bad]}})

    def test_final_review_retains_exact_plan_and_validated_trace_without_approval(self):
        state = self.state()
        before = copy.deepcopy(state)
        report = {
            "contract": {"retain_contract": retention.token(state)},
            "requirement_trace": [],
            "contract_changes": [],
            "conflict_resolutions": [],
            "decisions": [{"concern_id": "C1", "resolved": True}],
        }
        result = retention.expand(state, "astra_finalize", report)
        self.assertEqual(state["goal_contract"]["body"], result["contract"])
        self.assertEqual(
            state["planning"]["reports"]["glm_revise"]["report"]["requirement_trace"], result["requirement_trace"]
        )
        self.assertEqual(report["decisions"], result["decisions"])
        self.assertEqual(before, state)
        self.assertLess(len(json.dumps(report)), len(json.dumps(result)))

    def test_final_reference_cannot_change_trace_permissions_or_use_stale_identity(self):
        state = self.state()
        ref = {"retain_contract": retention.token(state)}
        cases = [
            {"contract": {**ref, "permission_boundaries": []}},
            {"contract": {"retain_contract": "r3:old"}},
            {"contract": ref, "requirement_trace": [{"requirement_id": "forged"}]},
            {"contract": ref, "contract_changes": [{"item": "AC8"}]},
        ]
        for report in cases:
            with self.subTest(report=report), self.assertRaises(ValueError):
                retention.expand(state, "astra_finalize", report)
        with self.assertRaises(ValueError):
            retention.expand(state, "glm_revise", {"contract": ref})

    def test_unrelated_prior_report_cannot_lend_trace_to_current_contract(self):
        state = self.state()
        state["planning"]["reports"]["glm_revise"]["report"]["contract"] = {"acceptance_criteria": []}
        trace = [{"requirement_id": "R1", "disposition": "covered", "evidence": "AC8"}]
        report = {"contract": {"retain_contract": retention.token(state)}, "requirement_trace": trace}
        self.assertIsNone(retention.validated_trace(state))
        self.assertEqual(trace, retention.expand(state, "astra_finalize", report)["requirement_trace"])
        # No inherited trace is fabricated: ordinary coverage validation sees an empty report.
        report["requirement_trace"] = []
        self.assertEqual([], retention.expand(state, "astra_finalize", report)["requirement_trace"])

    def test_final_generation_is_a_verdict_not_another_contract_author(self):
        state = self.state()
        body = {
            "type": "object",
            "properties": {
                "acceptance_criteria": {"type": "array", "items": {}},
                "initial_task": {"type": "object"},
                "open_blocking_questions": {"type": "array", "items": {}},
            },
        }
        schema = retention.model_schema({"properties": {"contract": body}}, state, "astra_finalize")
        self.assertEqual(
            {"retain_contract", "initial_task", "open_blocking_questions"},
            set(schema["properties"]["contract"]["properties"]),
        )
        self.assertNotIn("acceptance_criteria", schema["properties"]["contract"]["properties"])

    def test_v2_generation_keeps_its_separate_artifact_protocol(self):
        schema = {"properties": {"contract": {"properties": {"acceptance_criteria": {"items": {}}}}}}
        for stage in ("plan", "plan_revise", "plan_finalize"):
            self.assertEqual(schema, retention.model_schema(schema, self.state(), stage))

    def test_blocking_verdict_keeps_obligations_and_cannot_dispatch(self):
        state = self.state()
        state["goal_contract"]["body"]["initial_task"] = {
            "kind": "implement",
            "objective": "fix",
            "affected_paths": ["todo.py"],
        }
        question = {"id": "Q1", "question": "Which behavior?"}
        report = {"contract": {"retain_contract": retention.token(state), "open_blocking_questions": [question]}}
        result = retention.expand(state, "astra_finalize", report)
        self.assertEqual([question], result["contract"]["open_blocking_questions"])
        self.assertEqual("none", result["contract"]["initial_task"]["kind"])
        self.assertEqual(
            state["goal_contract"]["body"]["acceptance_criteria"], result["contract"]["acceptance_criteria"]
        )
        with self.assertRaises(ValueError):
            retention.expand(
                state,
                "astra_finalize",
                {"contract": {"retain_contract": retention.token(state), "initial_task": {"kind": "implement"}}},
            )

    def test_compaction_retains_all_requirements_conflicts_and_human_authority(self):
        packet = {
            "requirements_handoff": {
                "output": "requirements.json",
                "report": {
                    "requirements": [{"id": "R1", "text": "Preserve bytes"}],
                    "conflicts": [{"description": "unresolved"}],
                    "open_questions": [{"id": "Q1"}],
                    "summary": "Repeated prose",
                },
            },
            "user_events": [
                {
                    "kind": "brief_feedback",
                    "id": "F1",
                    "actor": "user_cli",
                    "text": "Preserve bytes",
                    "contract_token": "r1:h",
                    "displayed_plan": "duplicate",
                }
            ],
        }
        retention.compact_final_packet(packet)
        self.assertEqual(
            [{"id": "R1", "text": "Preserve bytes"}], packet["requirements_handoff"]["report"]["requirements"]
        )
        self.assertEqual([{"description": "unresolved"}], packet["requirements_handoff"]["report"]["conflicts"])
        self.assertEqual("Preserve bytes", packet["user_events"][0]["text"])
        self.assertNotIn("displayed_plan", packet["user_events"][0])


class CompletedTurnAllowanceTests(unittest.TestCase):
    def state(self, explicit=False):
        return {
            "settings": {},
            "planning": {
                "astra_calls": 2,
                "reports": {"astra_finalize": {"output": "old.json"}},
                "review_depth_reduced": True,
                "review_call_limit": 2,
                **({"review_call_limit_origin": "user_explicit"} if explicit else {}),
            },
            "stages": [{"stage": "astra_finalize", "duration_seconds": 5}],
            "cost_ledger": {"spent": 1},
        }

    def test_new_human_turn_renews_default_only_and_preserves_usage_history(self):
        state = self.state()
        stages = copy.deepcopy(state["stages"])
        ledger = copy.deepcopy(state["cost_ledger"])
        state["turns"] = [{"event_id": "human-followup-1"}]
        state["workflow"] = {"kind": "build", "clarity": "clear", "brief_size": "small"}
        state["next_stage"] = "astra_challenge"
        cycle.new_cycle(state, new_turn=True)
        self.assertEqual(0, state["planning"]["astra_calls"])
        planner.charge(state, "astra_challenge")
        self.assertEqual(1, state["planning"]["astra_calls"])
        self.assertEqual(2, state["planning_history"][0]["astra_calls"])
        self.assertEqual(stages, state["stages"])
        self.assertEqual(ledger, state["cost_ledger"])

    def test_internal_restart_and_explicit_human_bound_never_mint_reviews(self):
        for new_turn, explicit in ((False, False), (True, True)):
            state = self.state(explicit)
            cycle.new_cycle(state, new_turn=new_turn)
            self.assertEqual(2, state["planning"]["astra_calls"])
            if explicit:
                self.assertEqual(2, state["planning"]["review_call_limit"])
