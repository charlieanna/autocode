"""Planner gates: protected revisions, requirement trace, source refs, milestone order."""
# path bootstrap: runtime in tools/, fakes in tests/fakes/
import sys as _sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[2] if 'fakes' in _Path(__file__).parts else _Path(__file__).resolve().parents[1]
_TOOLS = _ROOT / 'tools'
_FAKES = _ROOT / 'tests' / 'fakes'
for _p in (_ROOT, _TOOLS, _ROOT / 'tests', _FAKES):
    _s = str(_p)
    if _s not in _sys.path:
        _sys.path.insert(0, _s)
import copy
from pathlib import Path
import tempfile
import unittest

from . import autocode_planning as planning
from . import test_autocode as base
from goal_fixtures import body

goals = base.goals


def state(task="Build a greeting CLI"):
    return {"version": 3, "task_id": "task-1", "task": task, "workspace": "/absent-workspace",
            "settings": {"joint_planning": True, "roles": {"plan_reviewer": {}}},
            "answers": {}, "user_events": [], "acceptance_criteria": []}


class RevisionGuardTests(unittest.TestCase):
    def test_revision_cannot_drop_a_behavior_or_exclusion_or_widen_permissions(self):
        current = state("Print Hello, NAME. Don't touch auth.")
        goals.install_draft(current, body(), origin="glm_draft")
        dropped = body()
        dropped["required_behaviors"] = ["Print a greeting"]
        with self.assertRaisesRegex(ValueError, "without a user-backed"):
            goals.install_draft(current, dropped, origin="glm_revise")
        excluded = body()
        excluded["scope_exclusions"] = ["Web service"]
        with self.assertRaisesRegex(ValueError, "Do not touch auth|without a user-backed"):
            goals.install_draft(current, excluded, origin="glm_revise")
        wider = body()
        wider["permission_boundaries"] = ["May write outside the workspace and call external services"]
        with self.assertRaisesRegex(ValueError, "without a user-backed"):
            goals.install_draft(current, wider, origin="glm_revise")
        self.assertEqual(["Print Hello, NAME for a nonempty name"], current["goal_contract"]["body"]["required_behaviors"])

    def test_user_edit_and_backed_reword_are_allowed(self):
        current = state()
        goals.install_draft(current, body(), origin="glm_draft")
        edited = body()
        edited["required_behaviors"] = ["Print a greeting"]
        goals.install_draft(current, edited, origin="user_cli_edit")
        self.assertEqual(["Print a greeting"], current["goal_contract"]["body"]["required_behaviors"])
        current = state()
        goals.install_draft(current, body(), origin="glm_draft")
        reworded = body()
        reworded["required_behaviors"] = ["Print Hello, NAME"]
        with self.assertRaisesRegex(ValueError, "saved user answer"):
            goals.install_draft(current, reworded, origin="glm_revise", changes=[{
                "item": "Print Hello, NAME for a nonempty name", "change": "reworded",
                "basis": "agent_proposed", "answer_id": "", "replacement": "Print Hello, NAME"}])
        current["answers"]["Q1"] = {"id": "Q1", "text": "Use the shorter wording"}
        goals.install_draft(current, reworded, origin="glm_revise", changes=[{
            "item": "Print Hello, NAME for a nonempty name", "change": "reworded",
            "basis": "user_answer", "answer_id": "Q1", "replacement": "Print Hello, NAME"}])
        self.assertIn("reworded", current["goal_contract"]["declared_changes"][0]["change"])
        self.assertIn("Declared contract changes", goals.render(current))

    def test_plan_review_changes_plan_without_rewriting_protected_requirements(self):
        current = state()
        original = body()
        goals.install_draft(current, original, origin="glm_draft")
        revised = copy.deepcopy(original)
        revised["technical_approach"] = ["Use a smaller parser and run its tests"]
        goals.install_draft(current, revised, origin="glm_revise", changes=[])
        self.assertEqual(original["required_behaviors"], current["goal_contract"]["body"]["required_behaviors"])
        self.assertEqual(original["acceptance_criteria"], current["goal_contract"]["body"]["acceptance_criteria"])
        current["answers"]["Q1"] = {"id": "Q1", "text": "Use the shorter wording"}
        with self.assertRaisesRegex(ValueError, "not changed"):
            goals.install_draft(current, revised, origin="glm_revise", changes=[{
                "item": original["required_behaviors"][0], "change": "reworded",
                "basis": "user_answer", "answer_id": "Q1", "replacement": "Print Hello, NAME"}])


class TraceTests(unittest.TestCase):
    def test_covered_trace_accepts_whole_id_citations_and_rejects_unknown_ids(self):
        current = state()
        current["requirements_handoff"] = {"report": {"requirements": [{"id": "R1", "text": "Greet", "source_quote": "Greet"}]}}
        draft = body()
        for evidence in ("C1", draft["required_behaviors"][0], "C1 verifies the requested behavior"):
            goals.check_requirement_trace(current, {"requirement_trace": [
                {"requirement_id": "R1", "disposition": "covered", "evidence": evidence}]}, draft)
        for evidence in ("C99 verifies it", "C10 verifies it", "XC1 verifies it",
                         "c1 verifies it", "C1 and C99 verify it", "It is covered"):
            with self.subTest(evidence=evidence), self.assertRaisesRegex(ValueError, "not covered"):
                goals.check_requirement_trace(current, {"requirement_trace": [
                    {"requirement_id": "R1", "disposition": "covered", "evidence": evidence}]}, draft)
        prompt, _ = planning.context(current, "glm_revise", Path("/run/state.json"))
        self.assertIn("AC1 verifies this", prompt)
        self.assertIn("copy required_behaviors", prompt)
        self.assertIn("Reviewer\nconcerns and agent proposals are not saved user authorization", prompt)

    def test_buried_sentence_must_be_quoted_or_ignored(self):
        task = ("Intro. " * 5) + "Reject a name that is only whitespace, exit 2. " + ("Closing. " * 3)
        current = state(task)
        report = {"summary": "s", "intended_outcome": "o", "required_behaviors": ["Greet"],
                  "constraints": [], "acceptance_tests": ["run"], "source_refs": ["task"],
                  "proposed_assumptions": [], "open_questions": [], "requirements": [],
                  "ignored_statements": [], "conflicts": []}
        with self.assertRaisesRegex(ValueError, "only whitespace"):
            planning.apply(current, "requirements_gather", report, {"output": "req.json"})
        report["requirements"] = [{"id": "R1", "text": "Reject whitespace names",
                                   "source_quote": "Reject a name that is only whitespace, exit 2."}]
        planning.apply(current, "requirements_gather", report, {"output": "req.json"})
        draft = body()
        with self.assertRaisesRegex(ValueError, "no trace"):
            planning.apply(current, "astra_discovery", {
                "contract": draft, "summary": "plan", "code_refs": [], "alternatives": [],
                "uncertainties": [], "contract_changes": [], "requirement_trace": []}, {"output": "draft.json"})
        planning.apply(current, "astra_discovery", {
            "contract": draft, "summary": "plan", "code_refs": [], "alternatives": [], "uncertainties": [],
            "contract_changes": [], "requirement_trace": [
                {"requirement_id": "R1", "disposition": "covered", "evidence": "C1"}]}, {"output": "draft.json"})
        self.assertEqual("R1", current["requirements_handoff"]["report"]["requirements"][0]["id"])

    def test_conflict_blocks_until_a_question_or_user_event(self):
        current = state("Use Redis. Actually no additional infrastructure.")
        report = {"summary": "s", "intended_outcome": "o", "required_behaviors": ["Store sessions"],
                  "constraints": ["Use Redis.", "No additional infrastructure."],
                  "acceptance_tests": ["run"], "source_refs": ["task"], "proposed_assumptions": [],
                  "open_questions": [], "ignored_statements": [], "conflicts": [
                      {"requirement_ids": ["R1", "R2"], "description": "Redis contradicts no extra infrastructure"}],
                  "requirements": [
                      {"id": "R1", "text": "Use Redis", "source_quote": "Use Redis."},
                      {"id": "R2", "text": "No extra infrastructure", "source_quote": "Actually no additional infrastructure."}]}
        planning.apply(current, "requirements_gather", report, {"output": "req.json"})
        draft = body()
        draft["constraints"] = ["Use Redis.", "No additional infrastructure."]
        payload = {"contract": draft, "summary": "plan", "code_refs": [], "alternatives": [],
                   "uncertainties": [], "contract_changes": [], "requirement_trace": [
                       {"requirement_id": "R1", "disposition": "covered", "evidence": draft["required_behaviors"][0]},
                       {"requirement_id": "R2", "disposition": "covered", "evidence": "C1"}]}
        with self.assertRaisesRegex(ValueError, "blocking question"):
            planning.apply(current, "astra_discovery", payload, {"output": "draft.json"})
        asked = copy.deepcopy(payload)
        asked["contract"] = body(questions=True)
        asked["contract"]["milestones"] = []
        asked["contract"]["technical_approach"] = []
        asked["contract"]["constraints"] = draft["constraints"]
        planning.apply(current, "astra_discovery", asked, {"output": "draft.json"})
        self.assertTrue(current["goal_contract"]["body"]["open_blocking_questions"])


class PlanEvidenceTests(unittest.TestCase):
    def test_existing_source_must_be_cited_and_overlap_needs_an_edge(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "api.py").write_text("def route():\n    return 1\n")
            current = state("Keep the current modular layout")
            current["workspace"] = str(root)
            draft = body()
            payload = {"contract": draft, "summary": "replatform", "code_refs": [], "alternatives": [],
                       "uncertainties": [], "contract_changes": [], "requirement_trace": []}
            with self.assertRaisesRegex(ValueError, "code_refs"):
                planning.apply(current, "astra_discovery", payload, {"output": "draft.json"})
            payload["code_refs"] = ["api.py:1"]
            planning.apply(current, "astra_discovery", payload, {"output": "draft.json"})
        hidden = body()
        hidden["milestones"] = [
            {"id": "M1", "objective": "Parser", "acceptance_criteria": ["C1"], "depends_on": [], "affected_paths": ["shared.py"]},
            {"id": "M2", "objective": "Caller", "acceptance_criteria": ["C1"], "depends_on": [], "affected_paths": ["shared.py"]}]
        # Overlap stays a valid plan. Dispatch runs those milestones one at a time
        # instead of treating missing order as a planning error.
        goals.validate_body(state(), hidden)


if __name__ == "__main__":
    unittest.main()
