"""Progressive proposal wiring: schema acceptance, generated disclosure, the
candidate record, and delegation sealing at ordinary approval."""
import copy
import tempfile
import unittest
from pathlib import Path

import autocode_goal_lifecycle as lifecycle
import autocode_goals as goals
import autocode_progressive_plan as rules
import autocode_progressive_state as progressive_state
import autocode_resolver_human as human
import autocode_support as support
import goal_fixtures
from units import autoplanner


def check(cid="K1", method="python -m pytest tests/test_journey.py -q", relation="contributes_to", criteria=("C1",)):
    return {"id": cid, "method": method, "relation": relation, "criterion_ids": list(criteria)}


def slice_row(sid="S1", criteria=("C1",), checks=None, tentative=False, depends_on=(), paths=("greet.py",)):
    return {"id": sid, "intended_result": f"{sid} delivers one useful end-to-end result",
            "criterion_ids": list(criteria), "paths": list(paths), "depends_on": list(depends_on),
            "checks": checks if checks is not None else [check()], "tentative": tentative}


def proposal():
    return {"version": 1,
            "needed_because": "the product only succeeds as several verified slices",
            "shared_decisions": ["progress persists per student"],
            "outstanding_criteria": [],
            "done_slices": [],
            "slices": [slice_row(),
                       slice_row("S2", criteria=("C1",), checks=[check("K2", "python -m pytest tests/test_recs.py -q",
                                                                       "fully_verify", ("C1",))],
                                 tentative=True, depends_on=("S1",), paths=("greet.py",))]}


def report(body=None, with_proposal=True):
    value = {"contract": body if body is not None else goal_fixtures.body(), "summary": "Plan the slices",
             "code_refs": [], "alternatives": [], "uncertainties": [], "contract_changes": [],
             "conflict_resolutions": [], "requirement_trace": []}
    if with_proposal:
        value["progressive_proposal"] = proposal()
    return value


class SchemaTests(unittest.TestCase):
    def test_reports_accept_an_optional_progressive_proposal(self):
        support.validate_schema(report(with_proposal=False), autoplanner.SCHEMAS["astra_discovery"])
        support.validate_schema(report(), autoplanner.SCHEMAS["astra_discovery"])
        final_body = goal_fixtures.body()
        final_body["initial_task"] = {"objective": "Deliver S1", "affected_paths": ["app/"], "kind": "implement",
                                      "milestone_id": "M1", "requirements": ["R1"],
                                      "acceptance_criteria": ["C1"], "validation_plan": ["python -m pytest"]}
        final = {"contract": final_body, "summary": "Plan the slices", "decisions": [],
                 "contract_changes": [], "conflict_resolutions": [], "requirement_trace": [],
                 "progressive_proposal": proposal()}
        support.validate_schema(final, autoplanner.SCHEMAS["astra_finalize"])
        with self.assertRaises(Exception):
            support.validate_schema({**report(), "progressive_proposal": {"version": 1}},
                                    autoplanner.SCHEMAS["astra_discovery"])


class AcceptProposalTests(unittest.TestCase):
    def setUp(self):
        self.state = {"version": 3, "task_id": "task-1", "task": "Build the platform", "answers": {},
                      "user_events": [], "acceptance_criteria": [], "status": "RUNNING",
                      "settings": {}}

    def test_installs_generated_disclosure_and_records_the_candidate(self):
        value = report()
        progressive_state.accept_proposal(self.state, value, origin="glm_draft")
        body = value["contract"]
        generated = rules.disclosure(proposal(), ["C1"])
        self.assertEqual([line for line in body["constraints"] if line.startswith("Progressive ")],
                         [generated["constraints"][0]])
        self.assertEqual(generated["technical_approach"], body["technical_approach"][:2])
        self.assertIn("A standard-library Python CLI using sys.argv", body["technical_approach"])
        self.assertIn("Python standard library only", body["constraints"])
        candidate = self.state["progressive"]["candidate"]
        self.assertEqual(rules.plan_identity(proposal()), candidate["plan_hash"])
        self.assertEqual(["C1"], candidate["criteria"])
        rules.check_disclosure(body, proposal(), ["C1"])

    def test_rejects_hand_written_delegation_without_a_proposal(self):
        body = goal_fixtures.body()
        body["constraints"] = ["Progressive delegation: unlimited"]
        with self.assertRaisesRegex(ValueError, "without a progressive_proposal"):
            progressive_state.accept_proposal(self.state, {"contract": body}, origin="glm_draft")

    def test_rejects_mismatched_hand_written_disclosure(self):
        body = goal_fixtures.body()
        body["technical_approach"] = ["Progressive slice: S9 (forged): nothing"]
        with self.assertRaisesRegex(ValueError, "does not generate"):
            progressive_state.accept_proposal(self.state, report(body), origin="glm_draft")

    def test_rejects_a_prose_check(self):
        value = report()
        value["progressive_proposal"]["slices"][0]["checks"] = [check(method="the validator reads the diff")]
        with self.assertRaisesRegex(ValueError, "extracts no executable command"):
            progressive_state.accept_proposal(self.state, value, origin="glm_draft")

    def test_rejects_a_new_check_that_names_git_status_but_reads_a_saved_one(self):
        # The quoted script is one operand, so the operand checks let it through; clean replay refuses every
        # Validator report that runs it (#589, #185). A saved plan's check still reads as before.
        method = "sh -c 'test -z \"$(git status --porcelain app)\"'"
        value = report()
        value["progressive_proposal"]["slices"][1]["checks"][0]["method"] = method
        with self.assertRaisesRegex(ValueError, r"^check K2 method `sh -c .*` names git status\. Never name"):
            progressive_state.accept_proposal(self.state, value, origin="glm_draft")
        self.assertNotIn("progressive", self.state)
        self.assertEqual([method], rules.check_commands(check("K2", method)))

    def test_s3_and_milestone_errors_are_distinct_unchanged_guards(self):
        value = report()
        value["progressive_proposal"]["slices"].append(slice_row(
            "S3", checks=[check("K3")], depends_on=("S2",), tentative=False))
        value["contract"]["milestones"].append({
            **copy.deepcopy(value["contract"]["milestones"][0]), "id": "M2", "depends_on": ["M1"]})
        support.validate_schema(value, autoplanner.SCHEMAS["astra_discovery"])
        with self.assertRaisesRegex(ValueError, "slice S3 is future work"):
            progressive_state.accept_proposal(self.state, copy.deepcopy(value), origin="glm_draft")
        value["progressive_proposal"]["slices"][2]["tentative"] = True
        with self.assertRaisesRegex(ValueError, "requires one whole-product milestone"):
            progressive_state.accept_proposal(self.state, copy.deepcopy(value), origin="glm_draft")
        value["contract"]["milestones"] = value["contract"]["milestones"][:1]
        progressive_state.accept_proposal(self.state, value, origin="glm_draft")
        rules.check_disclosure(value["contract"], value["progressive_proposal"], ["C1"])

    def test_without_a_proposal_the_ordinary_path_stays_untouched(self):
        value = report(with_proposal=False)
        progressive_state.accept_proposal(self.state, value, origin="glm_draft")
        self.assertNotIn("progressive", self.state)
        self.assertEqual(["Python standard library only"], value["contract"]["constraints"])

    def test_the_generation_schema_placeholder_means_no_proposal(self):
        placeholder = {"version": 0, "needed_because": "", "shared_decisions": [],
                       "outstanding_criteria": [], "done_slices": [], "slices": []}
        value = report(with_proposal=False)
        value["progressive_proposal"] = placeholder
        self.assertFalse(rules.declares(placeholder))
        progressive_state.accept_proposal(self.state, value, origin="glm_draft")
        self.assertNotIn("progressive", self.state)
        broken = {**placeholder, "version": 2, "needed_because": "a real attempt", "slices": [slice_row()]}
        value["progressive_proposal"] = broken
        with self.assertRaisesRegex(ValueError, "version must be 1"):
            progressive_state.accept_proposal(self.state, value, origin="glm_draft")

    def test_rebind_without_a_proposal_clears_the_candidate(self):
        progressive_state.accept_proposal(self.state, report(), origin="glm_draft")
        self.assertIn("candidate", self.state["progressive"])
        progressive_state.accept_proposal(self.state, report(with_proposal=False), origin="glm_revise")
        self.assertNotIn("candidate", self.state["progressive"])
        self.assertNotIn("delegation", self.state["progressive"])


class SealTests(unittest.TestCase):
    def setUp(self):
        self.state = {"version": 3, "task_id": "task-1", "task": "Build the platform", "answers": {},
                      "user_events": [], "acceptance_criteria": [], "status": "RUNNING",
                      "settings": {}}
        self.value = report()
        progressive_state.accept_proposal(self.state, self.value, origin="glm_draft")

    def with_contract(self):
        self.state["goal_contract"] = {"body": copy.deepcopy(self.value["contract"])}

    def test_displayed_token_without_independent_review_cannot_seal(self):
        self.with_contract()
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "planning report lacks an accepted read-only witness"):
            progressive_state.seal(self.state, "r1:tok")
        self.assertEqual(before, self.state)

    def test_failed_seal_does_not_reset_spent_planning_usage(self):
        self.with_contract()
        self.state["planning"] = {"astra_calls": 2}
        self.state["active_seconds"] = 350
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "planning report lacks an accepted read-only witness"):
            progressive_state.seal(self.state, "r1:tok")
        self.assertEqual(before, self.state)

    def test_seal_without_a_candidate_keeps_the_ordinary_path(self):
        self.state["progressive"].pop("candidate", None)
        self.assertIsNone(progressive_state.seal(self.state, "r1:tok"))
        self.state.pop("progressive", None)
        self.assertIsNone(progressive_state.seal(self.state, "r1:tok"))

    def test_seal_refuses_a_card_that_no_longer_matches_the_proposal(self):
        self.with_contract()
        self.state["goal_contract"]["body"]["technical_approach"] = ["something else"]
        with self.assertRaisesRegex(ValueError, "does not show the generated disclosure"):
            progressive_state.seal(self.state, "r1:tok")

    def test_seal_refuses_changed_product_criteria(self):
        self.with_contract()
        self.state["goal_contract"]["body"]["acceptance_criteria"] = [
            {"id": "C9", "criterion": "New", "verification_method": "read", "human_review": False}]
        with self.assertRaisesRegex(ValueError, "product criteria changed"):
            progressive_state.seal(self.state, "r1:tok")


class ApprovalIntegrationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name)
        (self.workspace / "config.py").write_text("PROVIDER = 'opencode'\n")
        self.state = {"version": 3, "task_id": "task-1", "task": "Build the platform",
                      "workspace": str(self.workspace), "answers": {}, "user_events": [],
                      "acceptance_criteria": [], "status": "RUNNING", "next_stage": "astra_discovery",
                      "settings": {}}

    def approve(self, value):
        lifecycle.migrate(self.state)
        progressive_state.accept_proposal(self.state, value, origin="glm_draft")
        lifecycle.install_draft(self.state, value["contract"], origin="glm_draft", record={})
        human.evaluate(self.state)
        lifecycle.present(self.state)
        selected = goals.token(self.state["goal_contract"])
        lifecycle.approve(self.state, selected)
        return selected

    def test_ordinary_approval_grants_no_delegation(self):
        selected = self.approve(report(with_proposal=False))
        self.assertEqual(selected, goals.token(self.state["goal_contract"]))
        self.assertNotIn("progressive", self.state)
        self.assertEqual("approved", self.state["goal_contract"]["approval_status"])

    def test_unreviewed_card_cannot_grant_delegation_through_ordinary_approval(self):
        with self.assertRaisesRegex(ValueError, "planning report lacks an accepted read-only witness"):
            self.approve(report())
        self.assertFalse(goals.approved(self.state))
        self.assertNotIn("delegation", self.state["progressive"])

    def test_tampered_candidate_does_not_partially_approve(self):
        value = report()
        progressive_state.accept_proposal(self.state, value, origin="glm_draft")
        lifecycle.install_draft(self.state, value["contract"], origin="glm_draft", record={})
        human.evaluate(self.state)
        lifecycle.present(self.state)
        self.state["progressive"]["candidate"]["plan_hash"] = "tampered"
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "plan identity"):
            lifecycle.approve(self.state, goals.token(self.state["goal_contract"]))
        self.assertEqual(before, self.state)
        self.assertFalse(goals.approved(self.state))

    def test_approval_alone_cannot_dispatch_an_unreviewed_slice(self):
        with self.assertRaisesRegex(ValueError, "planning report lacks an accepted read-only witness"):
            self.approve(report())
        with self.assertRaises(support.Paused):
            progressive_state.guard_dispatch(self.state, "terra")

    def test_missing_progressive_ledger_does_not_downgrade_execution(self):
        self.approve(report(with_proposal=False))
        self.state["goal_contract"]["body"]["constraints"] += rules.disclosure(proposal(), ["C1"])["constraints"]
        self.assertTrue(progressive_state.enabled(self.state))
        with self.assertRaises(support.Paused):
            progressive_state.guard_dispatch(self.state, "terra")

    def test_a_stale_token_never_seals(self):
        lifecycle.migrate(self.state)
        value = report()
        progressive_state.accept_proposal(self.state, value, origin="glm_draft")
        lifecycle.install_draft(self.state, value["contract"], origin="glm_draft", record={})
        human.evaluate(self.state)
        lifecycle.present(self.state)
        stale = "r0:stale"
        with self.assertRaises(ValueError):
            lifecycle.approve(self.state, stale)
        self.assertNotIn("delegation", self.state.get("progressive", {}))
        self.assertIn("candidate", self.state["progressive"])


if __name__ == "__main__":
    unittest.main()
