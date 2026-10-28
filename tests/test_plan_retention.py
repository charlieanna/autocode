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
        expanded = retention.expand(state, "glm_revise", report)
        expected = [old[0], changed, *old[3:], added]
        self.assertEqual(expected, expanded["contract"]["acceptance_criteria"])
        self.assertLess(len(json.dumps(report)), len(json.dumps(retention.inventory(state))))
        self.assertEqual(before, (state, report))
        expanded["contract"]["acceptance_criteria"][0]["criterion"] = "changed"
        self.assertEqual(before, (state, report))

    def test_unchanged_criteria_need_one_contract_identity_and_no_row_hashes(self):
        state = self.state()
        report = {"contract": {**state["goal_contract"]["body"], "acceptance_criteria": self.delta(state)}}
        expanded = retention.expand(state, "astra_discovery", report)
        self.assertEqual(state["goal_contract"]["body"], expanded["contract"])
        revision.revision_guard(state, expanded["contract"], [], "astra_discovery")
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
                retention.expand(state, "glm_revise", {"contract": {"acceptance_criteria": bad}})
        for stage in ("astra_finalize", "terra", "plan_revise"):
            with self.subTest(stage=stage), self.assertRaisesRegex(ValueError, "criteria delta"):
                retention.expand(state, stage, {"contract": {"acceptance_criteria": good}})
        # The same IDs with a new contract revision cannot borrow the earlier binding.
        state["goal_contract"]["revision"] += 1
        with self.assertRaisesRegex(ValueError, "criteria delta"):
            retention.expand(state, "glm_revise", {"contract": {"acceptance_criteria": good}})

    def test_delta_still_requires_authority_for_weakened_proof_or_removal(self):
        for action in ("weaken", "remove"):
            state = self.state()
            row = {**state["goal_contract"]["body"]["acceptance_criteria"][0], "verification_method": "echo PASS"}
            change = self.delta(state, upsert=[row]) if action == "weaken" else self.delta(state, remove=["AC8"])
            report = {"contract": {**state["goal_contract"]["body"], "acceptance_criteria": change}}
            expanded = retention.expand(state, "glm_revise", report)
            with self.subTest(action=action), self.assertRaises(ValueError):
                revision.revision_guard(state, expanded["contract"], [], "glm_revise")

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
        expanded = retention.expand(state, "glm_revise", report)
        expected = copy.deepcopy(state["goal_contract"]["body"])
        expected["acceptance_criteria"][0]["verification_method"] = proof["verification_method"]
        self.assertEqual(expected, expanded["contract"])
        self.assertEqual(before, state)
        revision.revision_guard(state, expanded["contract"], [], "glm_revise")
        state["goal_contract"]["approval_status"] = "approved"
        with self.assertRaises(ValueError):
            revision.revision_guard(state, expanded["contract"], [], "glm_revise")

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
                retention.expand(state, "glm_revise", {"contract": {"acceptance_criteria": delta}})

    def test_pinned_old_delta_schema_does_not_offer_the_new_proof_field(self):
        state = self.state()
        schema = retention.model_schema(planner.SCHEMAS["astra_discovery"], state, "astra_discovery")
        self.assertIn("PROOF-ONLY DELTA", retention.delta_rule(schema))
        branch = schema["properties"]["contract"]["properties"]["acceptance_criteria"]["anyOf"][1]
        branch["properties"].pop("proofs")
        self.assertNotIn("PROOF-ONLY DELTA", retention.delta_rule(schema))

    def test_author_schema_offers_delta_with_full_rows_but_keeps_legacy_array(self):
        state = self.state()
        source = copy.deepcopy(planner.SCHEMAS["glm_revise"])
        criteria = source["properties"]["contract"]["properties"]["acceptance_criteria"]
        for stage in ("astra_discovery", "glm_revise"):
            schema = retention.model_schema(source, state, stage)
            contract = schema["properties"]["contract"]
            if stage == "glm_revise":
                contract = contract["anyOf"][1]
            options = contract["properties"]["acceptance_criteria"]["anyOf"]
            delta = options[1]
            self.assertEqual(criteria["items"], delta["properties"]["upsert"]["items"])
            self.assertEqual({"retain_from", "upsert", "remove"}, set(delta["required"]))
            self.assertEqual("array", options[0]["type"])
        self.assertEqual(planner.SCHEMAS["glm_revise"], source)

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
        schema = retention.model_schema({"properties": {"contract": body}}, state, "astra_finalize")
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
        result = retention.expand(state, "astra_finalize", report)
        expected = copy.deepcopy(state["goal_contract"]["body"])
        expected["acceptance_criteria"] = [
            {**old[0], "verification_method": proof["verification_method"]},
            reworded,
            *old[3:],
            added,
        ]
        self.assertEqual(expected, result["contract"])
        self.assertEqual(
            state["planning"]["reports"]["glm_revise"]["report"]["requirement_trace"], result["requirement_trace"]
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
            "astra_finalize",
            {
                "contract": {**ref, "acceptance_criteria": self.final_delta(state, proofs=[proof])},
                "contract_changes": [],
            },
        )
        revision.revision_guard(state, corrected["contract"], [], "astra_finalize")
        dropped = retention.expand(
            state,
            "astra_finalize",
            {"contract": {**ref, "acceptance_criteria": self.final_delta(state, remove=["AC8"])}},
        )
        self.assertEqual([], dropped["contract"]["acceptance_criteria"])
        with self.assertRaises(ValueError):
            revision.revision_guard(state, dropped["contract"], [], "astra_finalize")
        receipt = [{"item": "AC8", "change": "removed", "basis": "user_answer", "answer_id": "Q1"}]
        state["answers"] = {"Q1": {"text": "Drop AC8"}}
        backed = retention.expand(
            state,
            "astra_finalize",
            {
                "contract": {**ref, "acceptance_criteria": self.final_delta(state, remove=["AC8"])},
                "contract_changes": receipt,
            },
        )
        self.assertEqual(
            receipt, revision.revision_guard(state, backed["contract"], backed["contract_changes"], "astra_finalize")
        )
        state["goal_contract"]["approval_status"] = "approved"
        with self.assertRaises(ValueError):
            revision.revision_guard(state, corrected["contract"], [], "astra_finalize")

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
                retention.expand(state, "astra_finalize", report)
        # The empty delta is the reference's ordinary form: it retains every row and the validated trace.
        result = retention.expand(state, "astra_finalize", {"contract": {**ref, "acceptance_criteria": empty}})
        self.assertEqual(state["goal_contract"]["body"], result["contract"])

    def test_a_corrected_criterion_set_may_bring_its_own_trace_but_an_unchanged_one_may_not(self):
        state = self.state()
        ref = {"retain_contract": retention.token(state)}
        validated = state["planning"]["reports"]["glm_revise"]["report"]["requirement_trace"]
        own = [{"requirement_id": "R1", "disposition": "covered", "evidence": "AC9"}]
        added = {**copy.deepcopy(state["goal_contract"]["body"]["acceptance_criteria"][0]), "id": "AC9"}
        corrected = {
            "contract": {**ref, "acceptance_criteria": self.final_delta(state, upsert=[added])},
            "requirement_trace": own,
        }
        self.assertEqual(own, retention.expand(state, "astra_finalize", corrected)["requirement_trace"])
        corrected["requirement_trace"] = []
        self.assertEqual(validated, retention.expand(state, "astra_finalize", corrected)["requirement_trace"])
        unchanged = {"contract": {**ref, "acceptance_criteria": self.final_delta(state)}, "requirement_trace": own}
        with self.assertRaisesRegex(ValueError, "validated trace"):
            retention.expand(state, "astra_finalize", unchanged)

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

    def test_a_question_the_contract_already_holds_is_not_duplicated(self):
        state = self.state()
        held = {"id": "Q1", "question": "Which behavior?"}
        state["goal_contract"]["body"]["open_blocking_questions"] = [held]
        new = {"id": "Q2", "question": "Which exit code?"}
        report = {"contract": {"retain_contract": retention.token(state), "open_blocking_questions": [dict(held), new]}}
        result = retention.expand(state, "astra_finalize", report)
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
