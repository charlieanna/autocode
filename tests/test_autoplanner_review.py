"""Regressions from the owner's review of PR #75 (head f1b22f2): state and
provenance transitions across the investigation pass, obligations, delegation
and stale displays. One case per finding, numbered as in the review."""

import copy
import unittest
from pathlib import Path

import autocode_goal_lifecycle as lifecycle
import autocode_goals as goals
from goal_fixtures import body
from units import autoplanner as planner

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
            self.apply(
                "requirements_gather",
                requirements(
                    [],
                    machine_resolutions=[
                        {
                            "question_id": "Q1",
                            "resolution": "Set in config.py",
                            "source_refs": ["config.py:1"],
                            "handoff_hash": handoff_hash,
                        }
                    ],
                ),
            )

    def test_5_answering_another_question_keeps_a_settled_workspace_fact(self):
        self.apply("requirements_gather", requirements([question("Q1", "decision"), question("Q2", "decision")]))
        self.apply("astra_discovery", discovery(clarification_only([question("Q1"), question("Q2", "decision")])))
        handoff_hash = self.state["investigation_request"]["handoff_hash"]
        self.apply(
            "astra_discovery",
            discovery(
                clarification_only([question("Q2", "decision")]),
                machine_resolutions=[
                    {
                        "question_id": "Q1",
                        "resolution": "Set in config.py",
                        "source_refs": ["config.py:1"],
                        "handoff_hash": handoff_hash,
                    }
                ],
            ),
        )
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
        from .test_autoplanner_step3 import assumption
        from .test_autoplanner_step3 import requirements as reqs

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
                self.draft(
                    {**decision_question("Q0"), "category": "behavior", "proposed_default": "CLI", "delegable": True}
                )
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
        from .test_autoplanner_step3 import assumption
        from .test_autoplanner_step3 import requirements as reqs

        self.apply("requirements_gather", reqs([assumption()]))
        self.draft(decision_question("Q9"))
        displayed = shown(self.state)
        self.state["requirements_handoff"]["report"]["summary"] = "Refreshed after display"
        with self.assertRaisesRegex(ValueError, "displayed revision"):
            goals.reject_assumption(self.state, "A1", displayed)
        self.assertEqual([], self.state.get("deferred_obligations", []))
        goals.reject_assumption(self.state, "A1", shown(self.state))
        self.assertEqual(1, len(self.state["deferred_obligations"]))


class ProxyConcernTests(ObligationCase):
    """A covered criterion proven on an internal value while the plan places the user-visible result
    outside this checkout is stored as a blocking concern for the person (#2068)."""

    def setUp(self):
        super().setUp()
        self.state["task"] = "The board shows the empty domain list as `--`."

    def handoff(self):
        from .test_autoplanner_step3 import requirements as reqs

        report = reqs()
        report["requirements"] = [
            {
                "id": "R1",
                "text": "The board shows the empty domain list as `--`",
                "source_quote": "The board shows the empty domain list as `--`.",
            }
        ]
        return report

    def draft(self, criterion):
        contract = plan()
        contract["required_behaviors"] = ["Show the empty domain list as `--`"]
        contract["constraints"] = ["The board is rendered by BoardHost, which is outside this checkout"]
        contract["acceptance_criteria"] = [
            {"id": "C1", "criterion": criterion, "verification_method": "Run the board check", "human_review": False}
        ]
        return contract

    def reviewed(self, criterion):
        self.apply("requirements_gather", self.handoff())
        self.apply("astra_discovery", self.discovery(self.draft(criterion)))
        self.apply("astra_challenge", self.challenge())
        return self.state["planning"]["reports"]["astra_challenge"]["report"]["concerns"]

    def test_a_proxy_draft_is_stored_with_one_blocking_proxy_concern(self):
        concerns = self.reviewed("list_domains returns a non-empty response")
        self.assertEqual(["proxy-R1"], [row["id"] for row in concerns])
        concern = concerns[0]
        self.assertTrue(concern["blocking"])
        self.assertIn("outside this checkout", concern["concern"])
        self.assertIn("`--`", concern["concern"])
        self.assertIn("the user sees", concern["concern"])
        self.assertIn("confirm", concern["concern"])
        self.assertTrue(concern["requested_change"].strip())
        self.assertTrue(concern["acceptance_test"].strip())

    def test_a_draft_whose_criterion_names_the_visible_literal_gets_no_proxy_concern(self):
        self.assertEqual([], self.reviewed("The empty domain list renders as `--`"))

    def test_a_second_challenge_on_the_same_draft_does_not_add_a_second_proxy_concern(self):
        self.apply("requirements_gather", self.handoff())
        self.apply("astra_discovery", self.discovery(self.draft("list_domains returns a non-empty response")))
        self.apply("astra_challenge", self.challenge())
        self.assertEqual(
            ["proxy-R1"],
            [row["id"] for row in self.state["planning"]["reports"]["astra_challenge"]["report"]["concerns"]],
        )
        for _ in range(2):  # a later round is not re-blocked either, however many follow
            self.apply("astra_challenge", self.challenge())
            self.assertEqual([], self.state["planning"]["reports"]["astra_challenge"]["report"]["concerns"])

    def test_a_gap_with_no_cited_criterion_says_no_criterion_cited(self):
        contract = self.draft("list_domains returns a non-empty response")
        contract["acceptance_criteria"] = [{}]
        state = {
            "goal_contract": {"origin": "astra_discovery", "body": contract},
            "planning": {"reports": {"astra_discovery": {"report": self.discovery(contract)}}},
            "requirements_handoff": {"report": self.handoff()},
        }
        value = {"concerns": []}
        planner.proxy_concerns(state, value)
        self.assertEqual(["proxy-R1"], [row["id"] for row in value["concerns"]])
        concern = value["concerns"][0]["concern"]
        self.assertIn("no criterion cited", concern)
        self.assertNotIn("no cited criterion (the cited criteria)", concern)

    def test_the_astra_challenge_prompt_carries_the_out_of_checkout_proxy_rule(self):
        self.apply("requirements_gather", self.handoff())
        self.apply("astra_discovery", self.discovery(self.draft("list_domains returns a non-empty response")))
        prompt, _ = planner.context(self.state, "astra_challenge", Path("/tmp/state.json"))
        self.assertIn("OUT-OF-CHECKOUT PROXY", prompt)
        self.assertIn("this proves X, the user sees Y", prompt)


if __name__ == "__main__":
    unittest.main()
