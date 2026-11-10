"""Exact inheritance avoids model copying without relaxing contract protection."""

import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_contract_revision as revision
import autocode_plan_retention as retention
import autocode_planning_cycle as cycle
from autocode_util import digest
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
                "origin": "revise_plan",
            },
            "planning": {
                "reports": {
                    "revise_plan": {
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

    def reviewed_state(self, *, added_guard=False):
        state = self.state()
        draft = state["goal_contract"]
        draft.update(task_id="small-fix", revision=1, approval_status="draft", origin="glm_draft")
        body = draft["body"]
        body.update(
            task_kind="bugfix",
            milestones=[
                {
                    "id": "M1",
                    "objective": "Fix the bug",
                    "acceptance_criteria": ["AC8", "AC9"],
                    "affected_paths": ["todos.py"],
                    "depends_on": [],
                }
            ],
            initial_task={
                "milestone_id": "M1",
                "objective": "Fix the bug",
                "acceptance_criteria": ["AC8", "AC9"],
                "affected_paths": ["todos.py"],
                "validation_plan": ["python3 -m unittest"],
            },
            technical_approach=["Fix the bug and test it"],
        )
        body["acceptance_criteria"].append(
            {**body["acceptance_criteria"][0], "id": "AC9", "current_behavior": "Old count: 3"}
        )
        draft["hash"] = digest({k: draft[k] for k in ("task_id", "revision", "body")})
        revised = copy.deepcopy(draft)
        revised.update(revision=2, origin="revise_plan")
        revised["body"]["acceptance_criteria"][0]["verification_method"] = "guard: test_ac8_bad_usage"
        if added_guard:
            revised_body = revised["body"]
            revised_body["acceptance_criteria"].append(
                {
                    **body["acceptance_criteria"][0],
                    "id": "AC10",
                    "criterion": "Given an empty list, print an empty report.",
                    "verification_method": "guard: test_empty",
                }
            )
            for binding in (revised_body["initial_task"], revised_body["milestones"][0]):
                binding["acceptance_criteria"].append("AC10")
                binding["objective"] += " and retain the empty report test"
            revised_body["technical_approach"] = ["Fix the bug, test it, and retain the empty report test"]
        else:
            revised["body"]["acceptance_criteria"][1]["current_behavior"] = "Old count: 4"
        revised["hash"] = digest({k: revised[k] for k in ("task_id", "revision", "body")})
        state.update(goal_contract=revised, contract_history=[draft], iteration=1)
        state["planning"]["reports"]["review_plan"] = {
            "output": "accepted-review.json",
            "report": {"summary": "AC8: run with ID 1, output bytes unchanged."},
        }
        state["stages"] = [
            {
                "stage": "review_plan",
                "iteration": 1,
                "contract_hash": draft["hash"],
                "source_revision": "source-1",
                "route_role": "plan_reviewer",
                "changed_files": [],
                "exit_code": 0,
                "finished_at": "2026-11-06T00:00:00Z",
                "output": "accepted-review.json",
            }
        ]
        return state

    def test_final_example_context_reuses_only_unchanged_examples_on_the_same_source(self):
        state = self.reviewed_state()
        before = copy.deepcopy(state)
        result = retention.prior_review_examples(state, "plan_reviewer", lambda: "source-1")
        self.assertEqual(["AC8"], result["unchanged_example_ids"])
        self.assertEqual("AC8: run with ID 1, output bytes unchanged.", result["summary"])
        self.assertEqual("source-1", result["source_revision"])
        self.assertEqual(before, state)
        self.assertIsNone(retention.prior_review_examples(state, "plan_reviewer", lambda: "source-2"))
        self.assertIsNone(retention.prior_review_examples(state, "different_route", lambda: "source-1"))

    def test_a_stale_or_rejected_review_cannot_supply_reusable_examples(self):
        for field, value in (
            ("contract_hash", "earlier-draft"),
            ("output", "rejected-review.json"),
            ("iteration", 2),
            ("rejected", True),
            ("changed_files", ["todos.py"]),
        ):
            with self.subTest(field=field):
                state = self.reviewed_state()
                state["stages"][0][field] = value
                self.assertIsNone(retention.prior_review_examples(state, "plan_reviewer", lambda: "source-1"))
        state = self.reviewed_state()
        state["goal_contract"]["body"]["constraints"] = ["New rule"]
        draft = state["goal_contract"]
        draft["hash"] = digest({k: draft[k] for k in ("task_id", "revision", "body")})
        self.assertIsNone(retention.prior_review_examples(state, "plan_reviewer", lambda: "source-1"))

    def test_adding_a_guard_keeps_only_earlier_examples_eligible_for_reuse(self):
        state = self.reviewed_state(added_guard=True)
        result = retention.prior_review_examples(state, "plan_reviewer", lambda: "source-1")
        self.assertEqual(["AC8", "AC9"], result["unchanged_example_ids"])

    def test_guard_reuse_refuses_a_change_to_scope_binding_or_test_kind(self):
        for kind in ("path", "command", "removed_binding", "fail_first"):
            state = self.reviewed_state(added_guard=True)
            draft = state["goal_contract"]
            body = draft["body"]
            if kind == "path":
                body["milestones"][0]["affected_paths"].append("unrelated.py")
            elif kind == "command":
                body["initial_task"]["validation_plan"] = ["echo passed"]
            elif kind == "removed_binding":
                body["initial_task"]["acceptance_criteria"].remove("AC8")
            else:
                body["acceptance_criteria"][-1]["verification_method"] = "test: test_empty"
            draft["hash"] = digest({k: draft[k] for k in ("task_id", "revision", "body")})
            with self.subTest(kind=kind):
                self.assertIsNone(retention.prior_review_examples(state, "plan_reviewer", lambda: "source-1"))

    def test_final_prompt_carries_current_observations_and_keeps_the_full_review(self):
        state = self.reviewed_state()
        state.update(
            task="Fix the week",
            workspace="/tmp",
            version=2,
            settings={"joint_planning": True, "roles": {"plan_reviewer": {"model": "review-model", "engine": "codex"}}},
            acceptance_criteria=[],
        )
        for source_revision in ("source-1", "stale"):
            with patch.object(
                planner.stage_context.source_scope, "snapshot", return_value={"revision": source_revision}
            ):
                prompt, _ = planner.context(state, "finalize_plan", Path("/tmp/run/state.json"))
            packet = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
            self.assertIn("CHECK EVERY WORKED EXAMPLE", prompt)
            self.assertEqual(source_revision == "source-1", "prior_review_examples" in packet)
            self.assertEqual(source_revision == "source-1", "Recompute changed examples" in prompt)
            if source_revision == "source-1":
                self.assertEqual(["AC8"], packet["prior_review_examples"]["unchanged_example_ids"])
                self.assertNotIn("summary", packet["planning"]["reports"]["review_plan"]["report"])

    def test_inherited_row_is_exact_and_still_passes_protected_revision_guard(self):
        state = self.state()
        before = copy.deepcopy(state)
        value = {"contract": {**state["goal_contract"]["body"], "acceptance_criteria": retention.inventory(state)}}
        expanded = retention.expand(state, "draft_plan", value)
        revision.revision_guard(state, expanded["contract"], [], "draft_plan")
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
            expanded = retention.expand(state, "revise_plan", value)
            with self.subTest(field=field), self.assertRaises(ValueError):
                revision.revision_guard(state, expanded["contract"], [], "revise_plan")

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
                retention.expand(state, "revise_plan", {"contract": {"acceptance_criteria": [bad]}})

    def delta(self, state, *, upsert=(), remove=()):
        return {"retain_from": retention.token(state), "upsert": list(upsert), "remove": list(remove)}

    def test_revision_emits_only_changed_rows_and_retains_all_other_bytes(self):
        state = self.state()
        old = state["goal_contract"]["body"]["acceptance_criteria"]
        old.extend({**copy.deepcopy(old[0]), "id": f"AC{n}"} for n in range(9, 30))
        changed = {**old[1], "sources": [*old[1]["sources"], {"source_id": "answer:Q1", "quote": "Keep bytes."}]}
        added = {**copy.deepcopy(old[0]), "id": "AC30"}
        report = {"contract": {"acceptance_criteria": self.delta(state, upsert=[changed, added], remove=["AC10"])}}
        before = copy.deepcopy((state, report))
        expanded = retention.expand(state, "revise_plan", report)
        expected = [old[0], changed, *old[3:], added]
        self.assertEqual(expected, expanded["contract"]["acceptance_criteria"])
        self.assertLess(len(json.dumps(report)), len(json.dumps(retention.inventory(state))))
        self.assertEqual(before, (state, report))
        expanded["contract"]["acceptance_criteria"][0]["criterion"] = "changed"
        self.assertEqual(before, (state, report))

    def test_unchanged_criteria_need_one_contract_identity_and_no_row_hashes(self):
        state = self.state()
        report = {"contract": {**state["goal_contract"]["body"], "acceptance_criteria": self.delta(state)}}
        expanded = retention.expand(state, "draft_plan", report)
        self.assertEqual(state["goal_contract"]["body"], expanded["contract"])
        revision.revision_guard(state, expanded["contract"], [], "draft_plan")
        self.assertNotIn("sha256", json.dumps(report))

    def test_criteria_delta_rejects_stale_and_ambiguous_inputs(self):
        state = self.state()
        row = copy.deepcopy(state["goal_contract"]["body"]["acceptance_criteria"][0])
        good = self.delta(state)
        for bad in (
            {**good, "retain_from": "r3:old"},
            {**good, "upsert": [row, row]},
            {**good, "upsert": [row], "remove": [row["id"]]},
            {**good, "remove": [row["id"], row["id"]]},
            {**good, "remove": ["AC999"]},
            {**good, "upsert": [{"retain_criterion": "AC8", "sha256": "wrong"}]},
            {**good, "upsert": [{"id": []}]},
            {**good, "remove": [None]},
            {**good, "upsert": {}},
            {**good, "extra": True},
            {k: v for k, v in good.items() if k != "remove"},
        ):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "criteria delta"):
                retention.expand(state, "revise_plan", {"contract": {"acceptance_criteria": bad}})
        for stage in ("finalize_plan", "builder", "plan_revise"):
            with self.subTest(stage=stage), self.assertRaisesRegex(ValueError, "criteria delta"):
                retention.expand(state, stage, {"contract": {"acceptance_criteria": good}})
        # The same IDs with a new contract revision cannot borrow the earlier binding.
        state["goal_contract"]["revision"] += 1
        with self.assertRaisesRegex(ValueError, "criteria delta"):
            retention.expand(state, "revise_plan", {"contract": {"acceptance_criteria": good}})

    def test_delta_still_requires_authority_for_weakened_proof_or_removal(self):
        for action in ("weaken", "remove"):
            state = self.state()
            row = {**state["goal_contract"]["body"]["acceptance_criteria"][0], "verification_method": "echo PASS"}
            change = self.delta(state, upsert=[row]) if action == "weaken" else self.delta(state, remove=["AC8"])
            report = {"contract": {**state["goal_contract"]["body"], "acceptance_criteria": change}}
            expanded = retention.expand(state, "revise_plan", report)
            with self.subTest(action=action), self.assertRaises(ValueError):
                revision.revision_guard(state, expanded["contract"], [], "revise_plan")

    def test_proof_delta_copies_every_other_criterion_field_exactly(self):
        state = self.state()
        state["goal_contract"]["approval_status"] = "draft"
        before = copy.deepcopy(state)
        proof = {"id": "AC8", "verification_method": "guard: test_all_bad_usage"}
        report = {
            "contract": {
                **state["goal_contract"]["body"],
                "acceptance_criteria": {**self.delta(state), "proofs": [proof]},
            }
        }
        expanded = retention.expand(state, "revise_plan", report)
        expected = copy.deepcopy(state["goal_contract"]["body"])
        expected["acceptance_criteria"][0]["verification_method"] = proof["verification_method"]
        self.assertEqual(expected, expanded["contract"])
        self.assertEqual(before, state)
        revision.revision_guard(state, expanded["contract"], [], "revise_plan")
        state["goal_contract"]["approval_status"] = "approved"
        with self.assertRaises(ValueError):
            revision.revision_guard(state, expanded["contract"], [], "revise_plan")

    def test_proof_delta_rejects_ambiguous_or_hidden_changes(self):
        state = self.state()
        proof = {"id": "AC8", "verification_method": "guard: test_all_bad_usage"}
        for proofs, extras in (
            ([proof, proof], {}),
            ([{**proof, "id": "missing"}], {}),
            ([{**proof, "criterion": "different behavior"}], {}),
            ([{**proof, "verification_method": " "}], {}),
            ([proof], {"remove": ["AC8"]}),
            ([proof], {"upsert": [copy.deepcopy(state["goal_contract"]["body"]["acceptance_criteria"][0])]}),
            ({}, {}),
        ):
            delta = {**self.delta(state), "proofs": proofs, **extras}
            with self.subTest(delta=delta), self.assertRaisesRegex(ValueError, "criteria delta"):
                retention.expand(state, "revise_plan", {"contract": {"acceptance_criteria": delta}})

    def test_pinned_old_delta_schema_does_not_offer_the_new_proof_field(self):
        state = self.state()
        schema = retention.model_schema(planner.SCHEMAS["draft_plan"], state, "draft_plan")
        self.assertIn("PROOF-ONLY DELTA", retention.delta_rule(schema))
        branch = schema["properties"]["contract"]["properties"]["acceptance_criteria"]["anyOf"][1]
        branch["properties"].pop("proofs")
        self.assertNotIn("PROOF-ONLY DELTA", retention.delta_rule(schema))

    def test_author_schema_offers_delta_with_full_rows_but_keeps_legacy_array(self):
        state = self.state()
        source = copy.deepcopy(planner.SCHEMAS["revise_plan"])
        criteria = source["properties"]["contract"]["properties"]["acceptance_criteria"]
        for stage in ("draft_plan", "revise_plan"):
            schema = retention.model_schema(source, state, stage)
            contract = schema["properties"]["contract"]
            if stage == "revise_plan":
                contract = contract["anyOf"][1]
            options = contract["properties"]["acceptance_criteria"]["anyOf"]
            delta = options[1]
            self.assertEqual(criteria["items"], delta["properties"]["upsert"]["items"])
            self.assertEqual({"retain_from", "upsert", "remove"}, set(delta["required"]))
            self.assertEqual("array", options[0]["type"])
            self.assertEqual(stage == "draft_plan", retention.allows_row_references(schema))
            if stage == "revise_plan":
                self.assertEqual(criteria["items"], options[0]["items"])
                self.assertNotIn("retain_criterion", json.dumps(schema))
            else:
                self.assertIn("retain_criterion", json.dumps(options[0]["items"]))
        self.assertEqual(planner.SCHEMAS["revise_plan"], source)

    def test_planner_revision_prompt_copies_one_contract_reference_instead_of_row_hashes(self):
        state = self.state()
        state.update(
            task="Fix the report",
            workspace="/tmp",
            version=2,
            settings={"joint_planning": True, "roles": {"planner": {"model": "fixture", "engine": "codex"}}},
        )
        prompt, _ = planner.context(state, "revise_plan", Path("/tmp/run/state.json"))
        packet = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
        self.assertEqual(retention.token(state), packet["retained_contract"])
        self.assertNotIn("retained_criteria", packet)
        self.assertNotIn(retention.RULE, prompt)
        self.assertIn(retention.DELTA_RULE, prompt)

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
        result = retention.expand(state, "finalize_plan", report)
        self.assertEqual(state["goal_contract"]["body"], result["contract"])
        self.assertEqual(
            state["planning"]["reports"]["revise_plan"]["report"]["requirement_trace"], result["requirement_trace"]
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
                retention.expand(state, "finalize_plan", report)
        with self.assertRaises(ValueError):
            retention.expand(state, "revise_plan", {"contract": ref})

    def test_unrelated_prior_report_cannot_lend_trace_to_current_contract(self):
        state = self.state()
        state["planning"]["reports"]["revise_plan"]["report"]["contract"] = {"acceptance_criteria": []}
        trace = [{"requirement_id": "R1", "disposition": "covered", "evidence": "AC8"}]
        report = {"contract": {"retain_contract": retention.token(state)}, "requirement_trace": trace}
        self.assertIsNone(retention.validated_trace(state))
        self.assertEqual(trace, retention.expand(state, "finalize_plan", report)["requirement_trace"])
        # No inherited trace is fabricated: ordinary coverage validation sees an empty report.
        report["requirement_trace"] = []
        self.assertEqual([], retention.expand(state, "finalize_plan", report)["requirement_trace"])

    def test_final_generation_is_a_verdict_with_a_criteria_delta_not_another_contract_author(self):
        state = self.state()
        body = {
            "type": "object",
            "properties": {
                "acceptance_criteria": {"type": "array", "items": {"type": "object"}},
                "initial_task": {"type": "object"},
                "open_blocking_questions": {"type": "array", "items": {}},
                "milestones": {"type": "array", "items": {}},
            },
        }
        schema = retention.model_schema({"properties": {"contract": body}}, state, "finalize_plan")
        reference = schema["properties"]["contract"]
        self.assertEqual(
            {"retain_contract", "initial_task", "open_blocking_questions", "acceptance_criteria"},
            set(reference["properties"]),
        )
        # Criteria only, and only as a delta on the retained rows (#1991): never a full array again.
        delta = reference["properties"]["acceptance_criteria"]
        self.assertEqual("object", delta["type"])
        self.assertEqual([retention.token(state)], delta["properties"]["retain_from"]["enum"])
        self.assertEqual({"type": "object"}, delta["properties"]["upsert"]["items"])
        self.assertTrue(retention.allows_delta(schema))
        self.assertIn(retention.FINAL_DELTA_RULE, retention.final_rule(schema))
        self.assertIn(retention.PROOF_RULE, retention.final_rule(schema))
        # An older pinned reference schema without the delta is never told to emit one.
        del reference["properties"]["acceptance_criteria"]
        self.assertFalse(retention.allows_delta(schema))
        self.assertEqual(retention.FINAL_RULE, retention.final_rule(schema))

    def final_delta(self, state, **operations):
        return {"retain_from": retention.token(state), "upsert": [], "remove": [], **operations}

    def test_final_reference_corrects_criteria_in_place_and_keeps_every_other_byte(self):
        state = self.state()
        old = state["goal_contract"]["body"]["acceptance_criteria"]
        old.extend({**copy.deepcopy(old[0]), "id": f"AC{n}"} for n in range(9, 14))
        before = copy.deepcopy(state)
        reworded = {**old[1], "criterion": "Given todos.json containing ID 1, bad usage leaves every byte unchanged."}
        added = {**copy.deepcopy(old[0]), "id": "AC14"}
        proof = {"id": "AC8", "verification_method": "guard: test_all_bad_usage"}
        report = {
            "contract": {
                "retain_contract": retention.token(state),
                "open_blocking_questions": [],
                "acceptance_criteria": self.final_delta(
                    state, upsert=[reworded, added], remove=["AC10"], proofs=[proof]
                ),
            },
            "requirement_trace": [],
            "contract_changes": [],
            "conflict_resolutions": [],
        }
        result = retention.expand(state, "finalize_plan", report)
        expected = copy.deepcopy(state["goal_contract"]["body"])
        expected["acceptance_criteria"] = [
            {**old[0], "verification_method": proof["verification_method"]},
            reworded,
            *old[3:],
            added,
        ]
        self.assertEqual(expected, result["contract"])
        self.assertEqual(
            state["planning"]["reports"]["revise_plan"]["report"]["requirement_trace"], result["requirement_trace"]
        )
        self.assertEqual(before, state)

    def test_final_correction_passes_the_same_guards_as_a_planner_revision(self):
        # A draft's proof correction needs no receipt; dropping a criterion needs the person's saved answer,
        # and an approved contract protects its proofs from the final reviewer as from the Planner.
        state = self.state()
        state["goal_contract"]["approval_status"] = "draft"
        ref = {"retain_contract": retention.token(state)}
        proof = {"id": "AC8", "verification_method": "guard: test_all_bad_usage"}
        corrected = retention.expand(
            state,
            "finalize_plan",
            {
                "contract": {**ref, "acceptance_criteria": self.final_delta(state, proofs=[proof])},
                "contract_changes": [],
            },
        )
        revision.revision_guard(state, corrected["contract"], [], "finalize_plan")
        dropped = retention.expand(
            state,
            "finalize_plan",
            {"contract": {**ref, "acceptance_criteria": self.final_delta(state, remove=["AC8"])}},
        )
        self.assertEqual([], dropped["contract"]["acceptance_criteria"])
        with self.assertRaises(ValueError):
            revision.revision_guard(state, dropped["contract"], [], "finalize_plan")
        receipt = [{"item": "AC8", "change": "removed", "basis": "user_answer", "answer_id": "Q1"}]
        state["answers"] = {"Q1": {"text": "Drop AC8"}}
        backed = retention.expand(
            state,
            "finalize_plan",
            {
                "contract": {**ref, "acceptance_criteria": self.final_delta(state, remove=["AC8"])},
                "contract_changes": receipt,
            },
        )
        self.assertEqual(
            receipt, revision.revision_guard(state, backed["contract"], backed["contract_changes"], "finalize_plan")
        )
        state["goal_contract"]["approval_status"] = "approved"
        with self.assertRaises(ValueError):
            revision.revision_guard(state, corrected["contract"], [], "finalize_plan")

    def test_final_reference_refuses_a_full_array_a_stale_delta_and_receipts_for_no_change(self):
        state = self.state()
        ref = {"retain_contract": retention.token(state)}
        row = copy.deepcopy(state["goal_contract"]["body"]["acceptance_criteria"][0])
        empty = self.final_delta(state)
        for report, message in (
            ({"contract": {**ref, "acceptance_criteria": [row]}}, "criteria delta"),
            ({"contract": {**ref, "acceptance_criteria": {**empty, "retain_from": "r3:old"}}}, "criteria delta"),
            ({"contract": {**ref, "acceptance_criteria": {**empty, "remove": ["AC999"]}}}, "criteria delta"),
            ({"contract": {**ref, "acceptance_criteria": empty}, "contract_changes": [{"item": "AC8"}]}, "declare"),
            (
                {
                    "contract": {**ref, "acceptance_criteria": self.final_delta(state, remove=["AC8"])},
                    "conflict_resolutions": [{"requirement_ids": ["R1"]}],
                },
                "declare",
            ),
            ({"contract": {**ref, "acceptance_criteria": empty, "milestones": []}}, "final review"),
        ):
            with self.subTest(report=report), self.assertRaisesRegex(ValueError, message):
                retention.expand(state, "finalize_plan", report)
        # The empty delta is the reference's ordinary form: it retains every row and the validated trace.
        result = retention.expand(state, "finalize_plan", {"contract": {**ref, "acceptance_criteria": empty}})
        self.assertEqual(state["goal_contract"]["body"], result["contract"])

    def test_a_corrected_criterion_set_may_bring_its_own_trace_but_an_unchanged_one_may_not(self):
        state = self.state()
        ref = {"retain_contract": retention.token(state)}
        validated = state["planning"]["reports"]["revise_plan"]["report"]["requirement_trace"]
        own = [{"requirement_id": "R1", "disposition": "covered", "evidence": "AC9"}]
        added = {**copy.deepcopy(state["goal_contract"]["body"]["acceptance_criteria"][0]), "id": "AC9"}
        corrected = {
            "contract": {**ref, "acceptance_criteria": self.final_delta(state, upsert=[added])},
            "requirement_trace": own,
        }
        self.assertEqual(own, retention.expand(state, "finalize_plan", corrected)["requirement_trace"])
        corrected["requirement_trace"] = []
        self.assertEqual(validated, retention.expand(state, "finalize_plan", corrected)["requirement_trace"])
        unchanged = {"contract": {**ref, "acceptance_criteria": self.final_delta(state)}, "requirement_trace": own}
        with self.assertRaisesRegex(ValueError, "validated trace"):
            retention.expand(state, "finalize_plan", unchanged)

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
        result = retention.expand(state, "finalize_plan", report)
        self.assertEqual([question], result["contract"]["open_blocking_questions"])
        self.assertEqual("none", result["contract"]["initial_task"]["kind"])
        self.assertEqual(
            state["goal_contract"]["body"]["acceptance_criteria"], result["contract"]["acceptance_criteria"]
        )
        with self.assertRaises(ValueError):
            retention.expand(
                state,
                "finalize_plan",
                {"contract": {"retain_contract": retention.token(state), "initial_task": {"kind": "implement"}}},
            )

    def test_a_question_the_contract_already_holds_is_not_duplicated(self):
        state = self.state()
        held = {"id": "Q1", "question": "Which behavior?"}
        state["goal_contract"]["body"]["open_blocking_questions"] = [held]
        new = {"id": "Q2", "question": "Which exit code?"}
        report = {"contract": {"retain_contract": retention.token(state), "open_blocking_questions": [dict(held), new]}}
        result = retention.expand(state, "finalize_plan", report)
        self.assertEqual([held, new], result["contract"]["open_blocking_questions"])

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
                "reports": {"finalize_plan": {"output": "old.json"}},
                "review_depth_reduced": True,
                "review_call_limit": 2,
                **({"review_call_limit_origin": "user_explicit"} if explicit else {}),
            },
            "stages": [{"stage": "finalize_plan", "duration_seconds": 5}],
            "cost_ledger": {"spent": 1},
        }

    def test_new_human_turn_renews_default_only_and_preserves_usage_history(self):
        state = self.state()
        stages = copy.deepcopy(state["stages"])
        ledger = copy.deepcopy(state["cost_ledger"])
        state["turns"] = [{"event_id": "human-followup-1"}]
        state["workflow"] = {"kind": "build", "clarity": "clear", "brief_size": "small"}
        state["next_stage"] = "review_plan"
        cycle.new_cycle(state, new_turn=True)
        self.assertEqual(0, state["planning"]["astra_calls"])
        planner.charge(state, "review_plan")
        self.assertEqual(1, state["planning"]["astra_calls"])
        self.assertEqual(2, state["planning_history"][0]["astra_calls"])
        self.assertEqual(stages, state["stages"])
        self.assertEqual(ledger, state["cost_ledger"])

    def test_a_config_toml_allowance_is_a_default_not_an_explicit_bound(self):
        # planning_review_call_limit_origin names where a default came from (#1565); it is not a run-wide total.
        state = self.state()
        state["settings"] = {
            "planning_review_call_limit": 2,
            "planning_review_call_limit_origin": "plan_review_call_limit in ~/.config/autocode/config.toml",
        }
        del state["planning"]["review_call_limit"]
        state["turns"] = [{"event_id": "human-followup-1"}]
        cycle.new_cycle(state, new_turn=True)
        self.assertEqual(0, state["planning"]["astra_calls"])
        self.assertNotEqual("user_explicit", state["planning"].get("review_call_limit_origin"))
        self.assertEqual(2, state["planning_history"][0]["astra_calls"])

    def test_internal_restart_and_explicit_human_bound_never_mint_reviews(self):
        for new_turn, explicit in ((False, False), (True, True)):
            state = self.state(explicit)
            cycle.new_cycle(state, new_turn=new_turn)
            self.assertEqual(2, state["planning"]["astra_calls"])
            if explicit:
                self.assertEqual(2, state["planning"]["review_call_limit"])
