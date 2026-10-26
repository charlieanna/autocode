"""A requirement proven only on a stand-in is marked proxied and settled by the person (#2068).

When the user-visible result is rendered by code outside this checkout, a plan may only prove an internal value
the result is believed to depend on. The trace says so (disposition ``proxied`` with a ``proxy_reason``), the plan
asks the person a decision question until a saved answer accepts the stand-in, and the status view and the
approval screen list the row instead of showing it as full coverage.
"""

import copy
import unittest
from pathlib import Path

import autocode_approval_view as approval_view
import autocode_goal_lifecycle as lifecycle
import autocode_goals as goals
import autocode_run_view as run_view
import autocode_trace_coverage as trace_coverage
import autocode_util as util
from goal_fixtures import body
from units import autoplanner as planner

REQUIREMENT = {
    "id": "R1",
    "text": "The domain list no longer renders as `()`",
    "source_quote": "Users see the domain list render as `()`.",
}
REASON = "Users see `()` from RegManager, outside this checkout; C1 proves Check_Domain returns a non-empty operation"


def state():
    current = {
        "version": 3,
        "task_id": "task-1",
        "task": "Users see the domain list render as `()`. Fix it.",
        "workspace": "/absent-workspace",
        "settings": {"joint_planning": True, "roles": {"plan_reviewer": {}}},
        "answers": {},
        "user_events": [],
        "acceptance_criteria": [],
        "requirements_handoff": {"report": {"requirements": [copy.deepcopy(REQUIREMENT)]}},
    }
    return current


def contract(*questions):
    draft = body()
    draft["acceptance_criteria"][0]["criterion"] = "Check_Domain returns a non-empty operation"
    draft["required_behaviors"].append("Users no longer see `()` for the domain list")
    draft["constraints"] = ["RegManager, which renders the domain list, is outside this checkout"]
    draft["open_blocking_questions"] = list(questions)
    return draft


def question(text, kind="decision"):
    return {
        "id": "Q1",
        "question": text,
        "why": "Only you can say whether the stand-in is enough.",
        "options": ["It is enough", "It is not enough"],
        "proposed_default": "It is not enough",
        "kind": kind,
        "category": "requested_outcome",
        "delegable": False,
    }


def proxied(**changes):
    row = {"requirement_id": "R1", "disposition": "proxied", "evidence": "C1", "proxy_reason": REASON}
    row.update(changes)
    return {"requirement_trace": [row]}


class ProxiedTraceTests(unittest.TestCase):
    def test_a_proxied_row_with_a_decision_question_naming_it_is_accepted(self):
        asked = contract(question("R1 is proven on Check_Domain only. Does that change the `()` users see?"))
        goals.check_requirement_trace(state(), proxied(), asked)

    def test_the_question_may_name_the_visible_literal_instead_of_the_id(self):
        asked = contract(question("Does a non-empty Check_Domain change the `()` users see?"))
        goals.check_requirement_trace(state(), proxied(), asked)

    def test_a_proxied_row_nobody_was_asked_about_is_refused(self):
        with self.assertRaisesRegex(ValueError, "R1 is proxied without the person's confirmation"):
            goals.check_requirement_trace(state(), proxied(), contract())

    def test_a_question_that_is_not_a_decision_does_not_settle_it(self):
        asked = contract(question("Where does R1's `()` come from?", kind="discoverable"))
        with self.assertRaisesRegex(ValueError, "without the person's confirmation"):
            goals.check_requirement_trace(state(), proxied(), asked)

    def test_a_question_about_something_else_does_not_settle_it(self):
        asked = contract(question("Should the CLI also print a count?"))
        with self.assertRaisesRegex(ValueError, "without the person's confirmation"):
            goals.check_requirement_trace(state(), proxied(), asked)

    def test_a_saved_answer_cited_in_the_reason_settles_it_without_a_question(self):
        current = state()
        current["answers"]["Q1"] = {"id": "Q1", "text": "Yes, a non-empty Check_Domain is enough."}
        goals.check_requirement_trace(current, proxied(proxy_reason=REASON + "; accepted in Q1"), contract())

    def test_a_proxied_row_needs_a_reason(self):
        asked = contract(question("Is R1's stand-in enough?"))
        for reason in (None, "", "  "):
            with (
                self.subTest(reason=reason),
                self.assertRaisesRegex(ValueError, "R1 is proxied without a proxy_reason"),
            ):
                goals.check_requirement_trace(state(), proxied(proxy_reason=reason), asked)

    def test_a_proxied_row_still_cites_the_criteria_that_prove_the_stand_in(self):
        asked = contract(question("Is R1's stand-in enough?"))
        with self.assertRaisesRegex(ValueError, "R1 is proxied but cites no criterion"):
            goals.check_requirement_trace(state(), proxied(evidence="C99 proves it"), asked)

    def test_a_proxied_row_is_not_a_proxy_gap_for_the_plan_reviewer(self):
        draft = contract(question("Is R1's stand-in enough?"))
        self.assertEqual([], trace_coverage.proxy_gaps([REQUIREMENT], proxied()["requirement_trace"], draft))
        covered = [{"requirement_id": "R1", "disposition": "covered", "evidence": "C1"}]
        self.assertEqual(
            ["R1"], [gap["requirement_id"] for gap in trace_coverage.proxy_gaps([REQUIREMENT], covered, draft)]
        )

    def test_the_planner_prompt_explains_proxied_and_its_question(self):
        prompt, _ = planner.context(state(), "glm_revise", Path("/run/state.json"))
        self.assertIn("disposition proxied is for a user-visible requirement", prompt)
        self.assertIn("open_blocking_questions entry of kind decision", prompt)

    def test_the_planner_schema_offers_proxied_and_a_nullable_reason(self):
        schema = planner.TRACE
        self.assertIn("proxied", schema["properties"]["disposition"]["enum"])
        strict = util.model_output_schema(schema)
        self.assertIn("proxy_reason", strict["required"])
        self.assertEqual(["string", "null"], strict["properties"]["proxy_reason"]["type"])
        # A row written before the field existed still validates against the saved-report schema.
        util.validate_schema({"requirement_id": "R1", "disposition": "covered", "evidence": "C1"}, schema)

    def test_a_report_that_leaves_out_proxy_reason_still_validates_against_the_strict_schema(self):
        import autocode_report_schema as report_schema

        schema = util.model_output_schema(
            {"type": "object", "properties": {"requirement_trace": {"type": "array", "items": planner.TRACE}}}
        )
        older = {"requirement_trace": [{"requirement_id": "R1", "disposition": "covered", "evidence": "C1"}]}
        with self.assertRaisesRegex(ValueError, "proxy_reason"):
            util.validate_schema(older, schema)
        normalized = report_schema.normalize_absent_authority(older, schema, {"stage": "astra_discovery"})
        util.validate_schema(normalized, schema)
        self.assertIsNone(normalized["requirement_trace"][0]["proxy_reason"])
        self.assertNotIn("proxy_reason", older["requirement_trace"][0])


class ProxiedDisplayTests(unittest.TestCase):
    def planned(self):
        current = {
            "version": 2,
            "workspace": "/absent-workspace",
            "task": "Users see the domain list render as `()`. Fix it.",
            "status": "RUNNING",
            "iteration": 1,
            "sessions": {},
            "stages": [],
            "history": [],
            "next_stage": "astra_challenge",
            "acceptance_criteria": [],
            "settings": {},
        }
        lifecycle.migrate(current)
        draft = contract()
        lifecycle.install_draft(current, draft, origin="astra_discovery")
        report = {"contract": draft, "requirement_trace": proxied()["requirement_trace"]}
        current.setdefault("planning", {}).setdefault("reports", {})["astra_discovery"] = {"report": report}
        return current

    def test_the_status_view_lists_the_proxied_requirements(self):
        self.assertEqual(
            [{"requirement_id": "R1", "evidence": "C1", "proxy_reason": REASON}],
            run_view.view(self.planned())["proxied_requirements"],
        )

    def test_the_status_view_lists_none_without_a_plan_or_proxy(self):
        current = self.planned()
        report = current["planning"]["reports"]["astra_discovery"]["report"]
        report["requirement_trace"] = [{"requirement_id": "R1", "disposition": "covered", "evidence": "C1"}]
        self.assertEqual([], run_view.view(current)["proxied_requirements"])
        current.pop("goal_contract")
        self.assertEqual([], run_view.view(current)["proxied_requirements"])

    def test_the_approval_summary_lists_the_stand_in(self):
        lines = approval_view.summary(
            contract(), 1, [], proxied=trace_coverage.proxied_rows(proxied()["requirement_trace"])
        )
        at = lines.index("Proven only on a stand-in (what you see is rendered outside this checkout):")
        self.assertEqual(["  - R1: C1", "    Why: " + REASON], lines[at + 1 : at + 3])

    def test_the_approval_summary_is_unchanged_without_a_stand_in(self):
        self.assertEqual(approval_view.summary(contract(), 1, []), approval_view.summary(contract(), 1, [], proxied=[]))
        self.assertFalse(any("stand-in" in line for line in approval_view.summary(contract(), 1, [])))


if __name__ == "__main__":
    unittest.main()
