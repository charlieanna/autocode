"""AutoPlanner step 3 (issue #62, rule E8): rejected assumptions as obligations,
their remediation, Plan Reviewer decisions bound to the reviewed record, and the
discovery, finalize and approval gates."""

import copy
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
