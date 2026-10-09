"""Regressions from the owner's review of PR #75 (head f1b22f2): state and
provenance transitions across the investigation pass, obligations, delegation
and stale displays. One case per finding, numbered as in the review."""
import copy
import unittest

import autocode_goals as goals
import autocode_goal_lifecycle as lifecycle
from goal_fixtures import body
from .test_autoplanner_step2 import EpisodeCase, clarification_only, discovery, question, requirements
from .test_autoplanner_step3 import ObligationCase, decision_question, plan


def shown(state):
    lifecycle.human.evaluate(state)
    lifecycle.present(state)
    return goals.token(state["goal_contract"])


class InvestigationReviewTests(EpisodeCase):
    def apply(self, stage, value, output="report.json"):
        candidate = copy.deepcopy(self.state)
        super_apply = EpisodeCase.apply
        self.state, keep = candidate, self.state
        try:
            super_apply(self, stage, value, output)
        except Exception:
            self.state = keep
            raise
        keep.clear()
        keep.update(candidate)
        self.state = keep

    def test_1_investigation_cannot_drop_an_unrelated_decision(self):
        self.apply("requirements_gather", requirements([question("Q1"), question("Q2", "decision", "cost")]))
        handoff_hash = self.state["investigation_request"]["handoff_hash"]
        with self.assertRaisesRegex(ValueError, "dropped questions without a machine_resolution: Q2"):
            self.apply("requirements_gather", requirements([], machine_resolutions=[{
                "question_id": "Q1", "resolution": "Set in config.py", "source_refs": ["config.py:1"],
                "handoff_hash": handoff_hash}]))

    def test_5_answering_another_question_keeps_a_settled_workspace_fact(self):
        self.apply("requirements_gather", requirements([question("Q1", "decision"), question("Q2", "decision")]))
        self.apply("astra_discovery", discovery(clarification_only([question("Q1"), question("Q2", "decision")])))
        handoff_hash = self.state["investigation_request"]["handoff_hash"]
        self.apply("astra_discovery", discovery(clarification_only([question("Q2", "decision")]),
                   machine_resolutions=[{"question_id": "Q1", "resolution": "Set in config.py",
                                         "source_refs": ["config.py:1"], "handoff_hash": handoff_hash}]))
        lifecycle.human.evaluate(self.state)  # the runner's writer boundary publishes the clarification
        self.assertEqual(["Q2"], [q["id"] for q in self.state["pending_questions"]])
        goals.answer(self.state, "Q2", "Use the default route")
        self.apply("astra_discovery", discovery(body()))
        self.assertEqual("astra_challenge", self.state["next_stage"])

    def test_5_a_refreshed_handoff_does_not_inherit_an_old_resolution(self):
        self.test_5_answering_another_question_keeps_a_settled_workspace_fact()
        self.state["requirements_handoff"]["report"]["summary"] = "Refreshed"
        self.state["requirements_handoff"]["report"]["open_questions"] = [question("Q1", "decision")]
        with self.assertRaisesRegex(ValueError, "dropped unresolved requirements questions: Q1"):
            self.apply("astra_discovery", discovery(body()))


class ObligationReviewTests(ObligationCase):
    def test_2_mentioning_an_obligation_in_feedback_does_not_resolve_it(self):
        oid = self.reject(category="cost")
        self.apply("astra_discovery", self.discovery(plan([decision_question(oid)])))
        self.publish()
        goals.feedback(self.state, f"{oid} is still unresolved. Investigate another approach.")
        self.assertEqual("open", self.obligation(oid)["status"])

    def test_3_delegating_an_obligation_question_is_refused_before_any_change(self):
        oid = self.reject(category="cost")
        question = {**decision_question(oid), "proposed_default": "Prefer the paid provider"}
        self.apply("astra_discovery", self.discovery(plan([question])))
        self.publish()
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "cannot be delegated"):
            goals.answer(self.state, oid, "accept default", delegated=True)
        self.assertEqual(before, self.state)
        goals.answer(self.state, oid, "Use the free provider")
        self.assertEqual("resolved", self.obligation(oid)["status"])

    def test_6_new_intent_invalidates_a_carried_remediation(self):
        oid = self.reject()
        self.apply("astra_discovery", self.discovery(plan([decision_question("Q2")]), [self.record(oid)]))
        self.publish()
        old_hash = self.obligation(oid)["remediation_hash"]
        self.assertEqual("pending_review", self.obligation(oid)["status"])
        goals.answer(self.state, "Q2", "stdin, not argv")
        self.assertEqual("open", self.obligation(oid)["status"])
        self.assertIsNone(self.obligation(oid)["remediation"])
        self.apply("astra_discovery", self.discovery())
        with self.assertRaisesRegex(ValueError, "does not name a remediation awaiting review"):
            self.apply("astra_challenge", self.challenge([self.decide(oid, remediation_hash=old_hash)]))

    def test_6_discharge_rechecks_the_record_episode(self):
        oid = self.reject()
        self.apply("astra_discovery", self.discovery(records=[self.record(oid)]))
        self.obligation(oid)["remediation"]["episode_id"] = "episode-old"
        with self.assertRaisesRegex(ValueError, "previous clarification episode"):
            self.apply("astra_challenge", self.challenge([self.decide(oid)]))

    def test_7_resolved_rejection_still_blocks_reinstating_the_assumption(self):
        from .test_autoplanner_step3 import assumption, requirements as reqs
        oid = self.reject()
        self.apply("astra_discovery", self.discovery(records=[self.record(oid)]))
        self.apply("astra_challenge", self.challenge([self.decide(oid)]))
        self.assertEqual("resolved", self.obligation(oid)["status"])
        with self.assertRaisesRegex(ValueError, "cannot reappear"):
            self.apply("requirements_gather", reqs([assumption()]))


class DelegationReviewTests(ObligationCase):
    def draft(self, *questions):
        self.state["workspace"] = "/absent-workspace"
        self.state["settings"]["joint_planning"] = False
        contract = body()
        contract["open_blocking_questions"] = list(questions)
        lifecycle.install_draft(self.state, contract, origin="glm_draft")

    def test_4_bulk_delegation_refuses_protected_or_unclassified_categories(self):
        for category in ("cost", "quota", "permission", "external_side_effect", "requested_outcome", None):
            with self.subTest(category=category):
                protected = {**decision_question("Q1"), "proposed_default": "Paid provider", "delegable": True}
                if category:
                    protected["category"] = category
                else:
                    protected.pop("category")
                self.setUp()
                self.draft({**decision_question("Q0"), "category": "behavior", "proposed_default": "CLI",
                            "delegable": True})
                displayed = shown(self.state)
                # A protected question slipped into the pending list (validation refuses it in
                # a draft, see below); bulk delegation must still refuse the whole call.
                self.state["pending_questions"].append(protected)
                with self.assertRaisesRegex(ValueError, "Q1"):
                    goals.delegate_all(self.state, displayed)
                self.assertEqual({}, self.state["answers"])

    def test_4_validation_rejects_delegable_protected_questions(self):
        protected = {**decision_question("Q1"), "proposed_default": "Paid provider", "delegable": True}
        with self.assertRaisesRegex(ValueError, "cannot be delegable"):
            self.draft(protected)
        unclassified = {key: value for key, value in protected.items() if key != "category"}
        with self.assertRaisesRegex(ValueError, "cannot be delegable"):
            self.draft(unclassified)
        from .test_autoplanner_step3 import requirements as reqs
        with self.assertRaisesRegex(ValueError, "cannot be delegable"):
            self.apply("requirements_gather", reqs(questions=[protected]))

    def test_8_bulk_delegation_is_bound_to_the_displayed_revision(self):
        safe = {**decision_question("Q1"), "category": "technical", "proposed_default": "SQLite", "delegable": True}
        self.draft(safe)
        displayed = shown(self.state)
        self.draft({**safe, "proposed_default": "PostgreSQL"})
        with self.assertRaisesRegex(ValueError, "displayed revision"):
            goals.delegate_all(self.state, displayed)
        self.assertEqual({}, self.state["answers"])
        goals.delegate_all(self.state, shown(self.state))
        self.assertEqual("PostgreSQL", self.state["answers"]["Q1"]["text"])

    def test_8_rejection_is_bound_to_the_displayed_revision_and_handoff(self):
        from .test_autoplanner_step3 import assumption, requirements as reqs
        self.apply("requirements_gather", reqs([assumption()]))
        self.draft(decision_question("Q9"))
        displayed = shown(self.state)
        self.state["requirements_handoff"]["report"]["summary"] = "Refreshed after display"
        with self.assertRaisesRegex(ValueError, "displayed revision"):
            goals.reject_assumption(self.state, "A1", displayed)
        self.assertEqual([], self.state.get("deferred_obligations", []))
        goals.reject_assumption(self.state, "A1", shown(self.state))
        self.assertEqual(1, len(self.state["deferred_obligations"]))


if __name__ == "__main__":
    unittest.main()
