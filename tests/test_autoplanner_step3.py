"""AutoPlanner step 3 (issue #62, rule E8): rejected assumptions as obligations,
their remediation, Plan Reviewer decisions bound to the reviewed record, and the
discovery, finalize and approval gates."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

import autocode_goal_lifecycle as lifecycle
import autocode_goals as goals
import autocode_planning_clarification as clarification
import autopilot
from goal_fixtures import body
from units import autoplanner as planner

TASK = "Greet the user by name."
REQUIREMENT = {"id": "R1", "text": "Greet by name", "source_quote": TASK}
TRACE = [{"requirement_id": "R1", "disposition": "covered", "evidence": "C1"}]


def assumption(aid="A1", category="technical"):
    return {
        "id": aid,
        "text": f"{aid}: a CLI is enough",
        "kind": "inferable" if category == "technical" else "decision",
        "category": category,
        "convention_ref": "config.py:1",
        "rationale": "Existing CLI",
        "supports": ["R1"],
    }


def requirements(assumptions=(), questions=()):
    return {
        "summary": "Requirements",
        "intended_outcome": "Greeting",
        "required_behaviors": ["Greet"],
        "constraints": [],
        "acceptance_tests": ["Run it"],
        "source_refs": ["config.py:1"],
        "proposed_assumptions": list(assumptions),
        "open_questions": list(questions),
        "requirements": [REQUIREMENT],
        "ignored_statements": [],
        "conflicts": [],
        "proposed_reframes": [],
        "ignored_requirements": [],
        "machine_resolutions": [],
        "access_blockers": [],
    }


def decision_question(qid):
    return {
        "id": qid,
        "question": "Which approach replaces the rejected assumption?",
        "why": "It was rejected",
        "options": [],
        "proposed_default": "",
        "kind": "decision",
        "category": "cost",
        "delegable": False,
    }


def plan(questions=(), initial=None):
    contract = body()
    contract["open_blocking_questions"] = list(questions)
    if questions:
        contract.update(technical_approach=[], milestones=[])
    if initial is not None:
        contract["initial_task"] = initial
    return contract


REAL_TASK = {
    "objective": "Implement greeting",
    "affected_paths": ["greet.py"],
    "kind": "implement",
    "milestone_id": "M1",
    "requirements": ["Greet"],
    "acceptance_criteria": ["C1"],
    "validation_plan": ["Run it"],
}
NO_TASK = {
    "objective": "",
    "affected_paths": [],
    "kind": "none",
    "milestone_id": "",
    "requirements": [],
    "acceptance_criteria": [],
    "validation_plan": [],
}


class ObligationCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name)
        (self.workspace / "config.py").write_text("INTERFACE = 'cli'\nPROVIDER = 'opencode'\n")
        self.state = {
            "version": 3,
            "task_id": "task-1",
            "task": TASK,
            "workspace": str(self.workspace),
            "answers": {},
            "user_events": [],
            "acceptance_criteria": [],
            "status": "RUNNING",
            "next_stage": "requirements_gather",
            "settings": {"joint_planning": True, "roles": {"requirements": {}, "glm": {}, "plan_reviewer": {}}},
        }

    def apply(self, stage, value):
        # As the runner does: apply to a copy and commit only if every gate passes.
        candidate = copy.deepcopy(self.state)
        autopilot.apply_planning(candidate, stage, value, {"output": f"{stage}.json"})
        self.state.clear()
        self.state.update(candidate)

    def reject(self, category="technical"):
        """Requirements with one assumption, then the user rejects it at a checkpoint."""
        self.apply("requirements_gather", requirements([assumption(category=category)]))
        self.apply("astra_discovery", self.discovery(plan([decision_question("Q0")])))
        self.publish()
        self.assertEqual("WAITING_FOR_USER", self.state["status"])
        lifecycle.present(self.state)
        obligation = goals.reject_assumption(self.state, "A1", goals.token(self.state["goal_contract"]))
        self.apply("requirements_gather", requirements())
        return obligation["id"]

    def publish(self):
        """The runner's writer boundary: a queued human request becomes visible
        (status, pending_questions, resolver token) only when AutoResolver publishes it."""
        self.assertEqual("RESOLVER_PENDING", self.state["status"])
        lifecycle.human.evaluate(self.state)

    def obligation(self, obligation_id):
        return next(ob for ob in self.state["deferred_obligations"] if ob["id"] == obligation_id)

    def record(self, obligation_id, approach="Keep the CLI and read the name from argv", **overrides):
        record = {
            "obligation_id": obligation_id,
            "assumption_id": "A1",
            "approach": approach,
            "evidence_refs": ["config.py:1"],
            "covered_requirements": ["R1"],
            "episode_id": goals.clarification_episode(self.state)["id"],
        }
        record.update(overrides)
        return record

    def discovery(self, contract=None, records=()):
        return {
            "contract": contract or plan(),
            "summary": "Draft",
            "code_refs": ["config.py:1"],
            "alternatives": [],
            "uncertainties": [],
            "contract_changes": [],
            "requirement_trace": TRACE,
            "machine_resolutions": [],
            "access_blockers": [],
            "remediation_records": list(records),
        }

    def challenge(self, decisions=(), concerns=()):
        return {"summary": "Review", "concerns": list(concerns), "obligation_decisions": list(decisions)}

    def revise(self, records=(), concerns=()):
        responses = [
            {
                "concern_id": c["id"],
                "response": "Addressed",
                "evidence_refs": ["config.py:1"],
                "change": "Changed",
                "acceptance_test": "t",
            }
            for c in concerns
        ]
        return {
            "contract": plan(),
            "summary": "Revised",
            "code_refs": ["config.py:1"],
            "responses": responses,
            "contract_changes": [],
            "requirement_trace": TRACE,
            "machine_resolutions": [],
            "access_blockers": [],
            "remediation_records": list(records),
        }

    def finalize(self, decisions=(), questions=(), initial=REAL_TASK, concerns=()):
        concern_decisions = [
            {
                "concern_id": c["id"],
                "decision": "Handled",
                "rationale": "Checked",
                "acceptance_test": c["acceptance_test"],
                "resolved": True,
            }
            for c in concerns
        ]
        return {
            "contract": plan(questions, initial),
            "summary": "Final",
            "decisions": concern_decisions,
            "contract_changes": [],
            "requirement_trace": TRACE,
            "obligation_decisions": list(decisions),
        }

    def decide(self, obligation_id, resolved=True, remediation_hash=None):
        return {
            "obligation_id": obligation_id,
            "remediation_hash": remediation_hash or self.obligation(obligation_id)["remediation_hash"],
            "resolved": resolved,
            "rationale": "Checked against config.py",
            "evidence_refs": ["config.py:1"],
        }


class RemediationTransitionTests(ObligationCase):
    def test_discovery_remediation_reaches_challenge_and_is_resolved_without_a_user_event(self):
        oid = self.reject()
        self.apply("astra_discovery", self.discovery(records=[self.record(oid)]))
        self.assertEqual("pending_review", self.obligation(oid)["status"])
        self.assertEqual("astra_challenge", self.state["next_stage"])
        prompt, _ = planner.context(self.state, "astra_challenge", Path("/tmp/state.json"))
        self.assertIn(oid, prompt)
        self.assertIn("REJECTED ASSUMPTIONS", prompt)
        with self.assertRaisesRegex(ValueError, "needs one obligation_decisions entry"):
            self.apply("astra_challenge", self.challenge())
        events = len(self.state["user_events"])
        self.apply("astra_challenge", self.challenge([self.decide(oid)]))
        self.assertEqual("resolved", self.obligation(oid)["status"])
        self.assertTrue(self.obligation(oid)["resolved_by"].startswith("astra_challenge:"))
        self.assertEqual(events, len(self.state["user_events"]))
        self.apply("glm_revise", self.revise())
        self.apply("astra_finalize", self.finalize())
        # Finalize queues the approval request; AutoResolver publishes it at the
        # writer boundary once final-plan evidence matches the current source.
        self.assertEqual("RESOLVER_PENDING", self.state["status"])
        self.assertEqual("goal_approval", self.state[lifecycle.human.PRIVATE]["scope"])
        self.assertEqual("AWAITING_GOAL_APPROVAL", self.state[lifecycle.human.PRIVATE]["status"])

    def test_revise_emitted_remediation_must_be_decided_at_finalize(self):
        oid = self.reject()
        self.apply("astra_discovery", self.discovery())
        self.assertEqual("open", self.obligation(oid)["status"])
        self.apply("astra_challenge", self.challenge())
        self.apply("glm_revise", self.revise([self.record(oid)]))
        with self.assertRaisesRegex(ValueError, "needs one obligation_decisions entry"):
            self.apply("astra_finalize", self.finalize())
        self.apply("astra_finalize", self.finalize([self.decide(oid)]))
        self.assertEqual("resolved", self.obligation(oid)["status"])
        self.assertEqual("implement", self.state["goal_contract"]["body"]["initial_task"]["kind"])

    def test_rejected_approach_is_repaired_and_the_old_decision_cannot_discharge_it(self):
        oid = self.reject()
        self.apply("astra_discovery", self.discovery(records=[self.record(oid)]))
        first_hash = self.obligation(oid)["remediation_hash"]
        concern = {
            "id": "P1",
            "concern": "The approach ignores empty names",
            "evidence_refs": [oid],
            "requested_change": "Handle empty names",
            "acceptance_test": "Empty name exits 2",
            "blocking": True,
        }
        with self.assertRaisesRegex(ValueError, "needs a blocking concern citing it"):
            self.apply("astra_challenge", self.challenge([self.decide(oid, resolved=False)]))
        self.apply("astra_challenge", self.challenge([self.decide(oid, resolved=False)], [concern]))
        self.assertEqual("open", self.obligation(oid)["status"])
        self.apply("glm_revise", self.revise([self.record(oid, "Read argv and reject empty names")], [concern]))
        self.assertEqual("pending_review", self.obligation(oid)["status"])
        self.assertNotEqual(first_hash, self.obligation(oid)["remediation_hash"])
        with self.assertRaisesRegex(ValueError, "superseded remediation"):
            self.apply(
                "astra_finalize", self.finalize([self.decide(oid, remediation_hash=first_hash)], concerns=[concern])
            )
        self.apply("astra_finalize", self.finalize([self.decide(oid)], concerns=[concern]))
        self.assertEqual("resolved", self.obligation(oid)["status"])

    def test_finalize_cannot_silently_drop_an_unresolved_remediation(self):
        oid = self.reject()
        self.apply("astra_discovery", self.discovery())
        self.apply("astra_challenge", self.challenge())
        self.apply("glm_revise", self.revise([self.record(oid)]))
        self.assertEqual("pending_review", self.obligation(oid)["status"])
        with self.assertRaisesRegex(ValueError, "must return to the user"):
            self.apply("astra_finalize", self.finalize([self.decide(oid, resolved=False)], initial=NO_TASK))
        with self.assertRaisesRegex(ValueError, "block an executable initial_task"):
            self.apply("astra_finalize", self.finalize([self.decide(oid, resolved=False)], [decision_question(oid)]))
        self.apply(
            "astra_finalize", self.finalize([self.decide(oid, resolved=False)], [decision_question(oid)], NO_TASK)
        )
        self.publish()
        self.assertEqual("WAITING_FOR_USER", self.state["status"])
        self.assertEqual("open", self.obligation(oid)["status"])
        goals.answer(self.state, oid, "Keep the CLI; reject empty names")
        self.assertEqual("resolved", self.obligation(oid)["status"])
        self.assertEqual("answer:" + oid, self.obligation(oid)["resolved_by"])


class UncitedSentenceFlowTests(ObligationCase):
    """The runner names the brief sentences a draft leaves uncited, and a revision may not re-leave them (#1630)."""

    SECOND = "The tool lives in greet.py."

    def setUp(self):
        super().setUp()
        self.state["task"] = TASK + " " + self.SECOND
        self.apply("requirements_gather", requirements())

    def cited(self, *quotes, contract=None):
        contract = contract or plan()
        contract["acceptance_criteria"][0]["sources"] = [{"source_id": "task:0", "quote": quote} for quote in quotes]
        return contract

    def uncited(self):
        return [row["sentence"] for row in self.state["planning"]["uncited_sentences"]]

    def test_a_draft_that_cites_part_of_the_brief_names_the_rest_in_the_next_handoffs(self):
        self.apply("astra_discovery", self.discovery(self.cited(TASK)))
        self.assertEqual([self.SECOND], self.uncited())
        for stage in ("astra_challenge", "glm_revise", "astra_finalize"):
            with self.subTest(stage=stage):
                prompt, _ = planner.context(self.state, stage, Path("/tmp/state.json"))
                packet = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
                self.assertEqual([{"source_id": "task:0", "sentence": self.SECOND}], packet["uncited_source_sentences"])
                self.assertNotIn("uncited_sentences", packet["planning"])
                self.assertIn("UNCITED SOURCE SENTENCES", prompt)
        revise, _ = planner.context(self.state, "glm_revise", Path("/tmp/state.json"))
        self.assertIn("refused before review", revise)
        review, _ = planner.context(self.state, "astra_challenge", Path("/tmp/state.json"))
        self.assertNotIn("refused before review", review)

    def test_a_draft_that_cites_everything_adds_nothing_to_the_handoff(self):
        self.apply("astra_discovery", self.discovery(self.cited(TASK, self.SECOND)))
        self.assertEqual([], self.uncited())
        prompt, _ = planner.context(self.state, "astra_challenge", Path("/tmp/state.json"))
        self.assertNotIn("uncited_source_sentences", prompt)
        self.assertNotIn("UNCITED SOURCE SENTENCES", prompt)

    def test_a_first_draft_is_never_refused_for_an_uncited_sentence(self):
        self.apply("astra_discovery", self.discovery(self.cited(TASK)))
        self.assertEqual("astra_challenge", self.state["next_stage"])

    def test_a_revision_that_leaves_the_sentence_uncited_is_refused_before_review(self):
        self.apply("astra_discovery", self.discovery(self.cited(TASK)))
        self.apply("astra_challenge", self.challenge())
        revision = self.revise()
        revision["contract"] = self.cited(TASK)
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "still leaves source sentences uncited.*The tool lives in greet.py"):
            self.apply("glm_revise", revision)
        self.assertEqual(before, self.state)
        revision["contract"] = self.cited(TASK, self.SECOND)
        self.apply("glm_revise", revision)
        self.assertEqual([], self.uncited())

    def test_a_sentence_the_review_placed_out_of_scope_may_stay_uncited(self):
        self.apply("astra_discovery", self.discovery(self.cited(TASK)))
        review = self.challenge()
        review["source_trace"] = {
            "out_of_scope": [{"source_id": "task:0", "quote": self.SECOND, "reason": "Deferred"}],
            "negation_waivers": [],
        }
        self.apply("astra_challenge", review)
        revision = self.revise()
        revision["contract"] = self.cited(TASK)
        self.apply("glm_revise", revision)
        self.assertEqual([self.SECOND], self.uncited())

    def test_a_contract_that_cites_nothing_is_never_listed_or_refused(self):
        self.apply("astra_discovery", self.discovery())
        self.assertEqual([], self.uncited())
        self.apply("astra_challenge", self.challenge())
        self.apply("glm_revise", self.revise())


class RequirementCorrectionContextTests(ObligationCase):
    def test_first_requirements_handoff_gets_mapped_saved_correction_sources(self):
        previous = {
            "id": "R2",
            "text": "Preserve existing behavior for a zero total and malformed receipts.",
            "source_quote": "Preserve existing behavior for a zero total and malformed receipts.",
        }
        feedback = {"id": "feedback-1", "actor": "user_cli", "text": "Zero totals no longer matter."}
        self.state["requirements_handoff"] = {"report": {"requirements": [previous]}}
        self.state["brief_feedback"] = [feedback]
        self.state["user_events"] = [feedback]

        prompt, _ = planner.context(self.state, "requirements_gather", Path("/tmp/state.json"))
        packet = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])

        self.assertEqual([previous], packet["previous_requirements"])
        self.assertEqual(["R2"], packet["requirements_correction_sources"][0]["previous_requirement_ids"])
        self.assertIn(
            "candidate links based on shared wording, not authority",
            " ".join(prompt.split()),
        )


class SentenceIdCitationFlowTests(ObligationCase):
    """A report may cite whole sentences by id; the saved contract and every check see verbatim quotes (#1630)."""

    SECOND = "The tool lives in greet.py."

    def setUp(self):
        super().setUp()
        self.state["task"] = TASK + " " + self.SECOND
        self.apply("requirements_gather", requirements())

    def by_id(self, *numbers):
        contract = plan()
        contract["acceptance_criteria"][0]["sources"] = [
            {"source_id": "task:0", "quote": f"task:0#{number}"} for number in numbers
        ]
        return contract

    def packet(self, stage):
        prompt, _ = planner.context(self.state, stage, Path("/tmp/state.json"))
        return prompt, json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])

    def test_drafting_stages_get_numbered_sentences_and_the_rule_but_reviews_do_not(self):
        self.apply("astra_discovery", self.discovery(self.by_id(1, 2)))
        for stage in ("astra_discovery", "glm_revise"):
            with self.subTest(stage=stage):
                prompt, packet = self.packet(stage)
                self.assertEqual(["task:0#1", "task:0#2"], [row["id"] for row in packet["source_sentences"]])
                self.assertEqual([TASK, self.SECOND], [row["text"] for row in packet["source_sentences"]])
                self.assertEqual([["R1"], []], [row["requirement_ids"] for row in packet["source_sentences"]])
                self.assertIn("SOURCE SENTENCE IDS", prompt)
                self.assertIn(self.SECOND, packet["source_inventory"][0]["text"])  # the inventory stays whole
        for stage in ("astra_challenge", "astra_finalize"):
            with self.subTest(stage=stage):
                prompt, packet = self.packet(stage)
                self.assertNotIn("source_sentences", packet)
                self.assertNotIn("SOURCE SENTENCE IDS", prompt)
                self.assertEqual(
                    ["C1"],
                    packet["review_source_sentences"][0]["criterion_ids"],
                )
                self.assertEqual(
                    {"source_id", "sentence", "criterion_ids"},
                    set(packet["review_source_sentences"][0]),
                )
                self.assertIn("review_source_sentences", prompt)

    def test_plan_reviewer_report_repair_gets_sentence_to_criterion_mapping(self):
        self.apply("astra_discovery", self.discovery(self.by_id(1, 2)))

        packet = planner.repair_citation_context(self.state, "astra_challenge", Path("/workspace"))

        self.assertEqual(
            {
                "source_id": "task:0",
                "sentence": TASK,
                "criterion_ids": ["C1"],
            },
            packet["review_source_sentences"][0],
        )

    def test_ids_are_saved_as_the_verbatim_sentences(self):
        self.apply("astra_discovery", self.discovery(self.by_id(1, 2)))
        saved = self.state["goal_contract"]["body"]["acceptance_criteria"][0]["sources"]
        self.assertEqual([TASK, self.SECOND], [row["quote"] for row in saved])
        self.assertEqual([], self.state["planning"]["uncited_sentences"])

    def test_a_mix_of_ids_and_verbatim_quotes_covers_the_brief(self):
        contract = self.by_id(1)
        contract["acceptance_criteria"][0]["sources"].append({"source_id": "task:0", "quote": self.SECOND})
        self.apply("astra_discovery", self.discovery(contract))
        self.assertEqual([], self.state["planning"]["uncited_sentences"])

    def test_an_id_for_a_missing_sentence_is_refused_and_changes_nothing(self):
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, r"without quoting it verbatim \(no such sentence id\)"):
            self.apply("astra_discovery", self.discovery(self.by_id(1, 7)))
        self.assertEqual(before, self.state)

    def test_a_revision_citing_by_id_satisfies_the_uncited_sentence_rule(self):
        self.apply("astra_discovery", self.discovery(self.by_id(1)))
        self.assertEqual([self.SECOND], [row["sentence"] for row in self.state["planning"]["uncited_sentences"]])
        self.apply("astra_challenge", self.challenge())
        revision = self.revise()
        revision["contract"] = self.by_id(1)
        with self.assertRaisesRegex(ValueError, "still leaves source sentences uncited"):
            self.apply("glm_revise", revision)
        revision["contract"] = self.by_id(1, 2)
        self.apply("glm_revise", revision)
        self.assertEqual([], self.state["planning"]["uncited_sentences"])


class HumanDecisionTests(ObligationCase):
    def test_policy_rejection_blocks_a_real_discovery_contract_until_the_user_answers(self):
        oid = self.reject(category="cost")
        self.assertEqual("human_decision", self.obligation(oid)["kind"])
        with self.assertRaisesRegex(ValueError, "does not name an open remediation obligation"):
            self.apply("astra_discovery", self.discovery(records=[self.record(oid)]))
        with self.assertRaisesRegex(ValueError, "policy weight"):
            self.apply("astra_discovery", self.discovery())
        with self.assertRaisesRegex(ValueError, "must return to the user"):
            self.apply("astra_discovery", self.discovery(plan([decision_question("Q9")])))
        self.apply("astra_discovery", self.discovery(plan([decision_question(oid)])))
        self.publish()
        self.assertEqual("WAITING_FOR_USER", self.state["status"])
        goals.answer(self.state, oid, "Quality matters more than cost here")
        self.assertEqual("resolved", self.obligation(oid)["status"])
        self.apply("astra_discovery", self.discovery())
        self.assertEqual("astra_challenge", self.state["next_stage"])

    def test_cap_full_defers_presentation_but_not_the_obligation(self):
        oid = self.reject(category="cost")
        others = [decision_question(q) for q in ("Q1", "Q2", "Q3")]
        self.apply("astra_discovery", self.discovery(plan(others)))
        self.publish()
        self.assertEqual("open", self.obligation(oid)["status"])
        self.assertEqual(["Q1", "Q2", "Q3"], [q["id"] for q in self.state["pending_questions"]])


class RemediationValidityTests(ObligationCase):
    def test_invalid_records_are_rejected(self):
        oid = self.reject()
        cases = {
            "unknown": self.record("obligation-nope"),
            "different assumption": self.record(oid, assumption_id="A7"),
            "coverage": self.record(oid, covered_requirements=[]),
            "previous episode": self.record(oid, episode_id="episode-old"),
            "evidence": self.record(oid, evidence_refs=["missing.py"]),
        }
        for name, record in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                self.apply("astra_discovery", self.discovery(records=[record]))

    def test_record_cannot_claim_a_requirement_the_trace_does_not_cover(self):
        oid = self.reject()
        value = self.discovery(records=[self.record(oid)])
        value["requirement_trace"] = [{"requirement_id": "R1", "disposition": "excluded", "evidence": "Web service"}]
        with self.assertRaisesRegex(ValueError, "does not cover"):
            self.apply("astra_discovery", value)

    def test_resolved_obligation_accepts_no_new_record(self):
        oid = self.reject()
        self.apply("astra_discovery", self.discovery(records=[self.record(oid)]))
        self.apply("astra_challenge", self.challenge([self.decide(oid)]))
        with self.assertRaisesRegex(ValueError, "does not name an open remediation obligation"):
            self.apply("glm_revise", self.revise([self.record(oid)]))

    def test_rejected_assumption_cannot_reappear_in_a_refreshed_handoff(self):
        self.reject()
        with self.assertRaisesRegex(ValueError, "cannot reappear"):
            self.apply("requirements_gather", requirements([assumption()]))


class ApprovalGateTests(ObligationCase):
    def test_approval_refuses_while_an_obligation_is_open(self):
        self.state["workspace"] = "/absent-workspace"
        self.state["settings"]["joint_planning"] = False
        lifecycle.install_draft(self.state, body(), origin="user_cli_edit")
        self.publish()
        self.assertEqual("AWAITING_GOAL_APPROVAL", self.state["status"])
        self.assertEqual("goal_approval", self.state["resolver_human_request"]["scope"])
        self.state["deferred_obligations"] = [{"id": "obligation-1", "kind": "remediation", "status": "open"}]
        lifecycle.present(self.state)
        with self.assertRaisesRegex(ValueError, "unresolved obligations"):
            lifecycle.approve(self.state, goals.token(self.state["goal_contract"]))

    def test_legacy_run_without_obligations_is_unaffected(self):
        self.assertEqual([], goals.open_obligations(self.state))
        before = copy.deepcopy(self.state)
        clarification.apply_obligations(
            self.state, "astra_challenge", self.challenge(), check_code_refs=autopilot._check_code_refs
        )
        self.assertEqual(before, self.state)


if __name__ == "__main__":
    unittest.main()
