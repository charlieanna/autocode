"""The Plan Reviewer's request reaches the plan through the real, offline CLI.

A live vague-brief run (2026-10-20) wrote its milestone file split into a constraint and a permission boundary;
the Plan Reviewer's blocking concern asked to rebalance the milestones, and the revision that did so was refused
for changing protected text. The run then spent a repair, a question AutoCode answered itself, a redraft and a
second review from scratch, and reached its time cap with no plan.
"""

import json
import os
import textwrap
import unittest
from pathlib import Path

import autocode_goal_lifecycle as lifecycle
import autocode_taskrun as taskrun

from .test_agent_step import fixture_workspace
from .test_taskrun import BRIEF, FIXTURE_OPTIONS

PARTITION = "M1 adds only greet.py and test_greet.py; README.md is written by a later milestone"
REBALANCED = "M1 delivers greet.py, test_greet.py and README.md together"
BOUNDARY = "Edit only greet.py and test_greet.py"
REPLACEMENT = "Read and edit only this fixture Git workspace"  # the fixture plan's own boundary


class ReviewerRequestedRevisionCliTests(unittest.TestCase):
    def reviewed_revision(self, *, quote_brief=False):
        """The Planner's draft puts its file split into protected lists; the review blocks it; the revision
        applies the review and declares the changes as its own. With ``quote_brief`` the constraint it changes
        repeats a sentence of the brief instead, which only the person may change."""
        workspace, env = fixture_workspace(self)
        provider = Path(env["PATH"].split(os.pathsep)[0]) / "codex"
        marker = '    output = Path(sys.argv[sys.argv.index("-o") + 1])'
        changed = "Python standard library only" if quote_brief else PARTITION
        amendment = textwrap.dedent(f"""
            if stage == "astra_discovery":
                if {PARTITION!r} not in report["contract"]["constraints"]:
                    report["contract"]["constraints"].append({PARTITION!r})
                report["contract"]["permission_boundaries"] = [{BOUNDARY!r}]
            elif stage == "astra_challenge":
                report["concerns"] = [{{
                    "id": "R1", "concern": "README.md is a deliverable no milestone writes",
                    "evidence_refs": ["goal_contract.body.constraints"],
                    "requested_change": "Rebalance the milestone so M1 writes every deliverable",
                    "acceptance_test": "M1's constraint names README.md", "blocking": True}}]
            elif stage == "glm_revise":
                report["contract"]["constraints"] = [
                    line for line in report["contract"]["constraints"] if line != {changed!r}] + [{REBALANCED!r}]
                report["contract_changes"] = [
                    {{"item": {changed!r}, "change": "reworded", "basis": "agent_proposed", "answer_id": "",
                     "replacement": {REBALANCED!r}, "example_correction": None}},
                    {{"item": {BOUNDARY!r}, "change": "permission_changed", "basis": "agent_proposed",
                     "answer_id": "", "replacement": {REPLACEMENT!r}, "example_correction": None}}]
                report["responses"] = [{{
                    "concern_id": "R1", "response": "M1 now writes all three deliverables",
                    "evidence_refs": ["goal_contract.body.milestones"],
                    "change": "Reworded the milestone constraint and its boundary",
                    "acceptance_test": "M1's constraint names README.md"}}]
            elif stage == "astra_finalize":
                report["decisions"] = [{{
                    "concern_id": "R1", "decision": "M1 writes every deliverable, as the revision states",
                    "rationale": "The reworded constraint and boundary cover README.md",
                    "acceptance_test": "M1's constraint names README.md", "resolved": True}}]
        """)
        source = provider.read_text()
        self.assertEqual(1, source.count(marker))
        provider.write_text(source.replace(marker, textwrap.indent(amendment, "    ") + "\n" + marker))
        run = taskrun.TaskRun.start(workspace, BRIEF, options=FIXTURE_OPTIONS, env=env, timeout=180)
        return run, run.advance_until_input()

    def test_the_revision_applies_the_reviewers_request_to_its_own_lines_without_a_person(self):
        run, view = self.reviewed_revision()
        self.assertEqual("approve_plan", view["needs"]["kind"], (view["status"], view["stop_reason"]))
        self.assertEqual("AWAITING_GOAL_APPROVAL", view["status"])
        plan = view["displayed_plan"]
        self.assertIn(REBALANCED, plan["constraints"])
        self.assertNotIn(PARTITION, plan["constraints"])
        self.assertEqual([REPLACEMENT], plan["permission_boundaries"])
        state = json.loads((run.run_dir / "state.json").read_text())
        self.assertEqual([], state.get("planning_history", []), "one planning cycle: no repair, question or redraft")
        declared = [
            (row["change"], row["item"], tuple(row.get("review_concerns") or ()))
            for revision in [*state["contract_history"], state["goal_contract"]]
            for row in revision.get("declared_changes") or []
        ]
        self.assertEqual({("reworded", PARTITION, ("R1",)), ("permission_changed", BOUNDARY, ("R1",))}, set(declared))
        shown = lifecycle.render(state)  # what approval prints: the revision's own changes, kept by the final review
        self.assertIn(
            f"reworded: {PARTITION} -> {REBALANCED} (basis: agent_proposed; Plan Reviewer concern R1; revision 2)",
            shown,
        )
        self.assertIn(
            f"permission_changed: {BOUNDARY} -> {REPLACEMENT} (basis: agent_proposed; Plan Reviewer concern R1; revision 2)",
            shown,
        )

    def test_a_constraint_that_repeats_the_brief_still_needs_the_person(self):
        run, view = self.reviewed_revision(quote_brief=True)
        self.assertNotEqual("approve_plan", view["needs"]["kind"], "the brief's own words are not the Planner's")
        state = json.loads((run.run_dir / "state.json").read_text())
        self.assertTrue(
            any(
                "repeats the person's own words" in json.dumps(row)
                for row in state["stages"]
                if row.get("stage") == "glm_revise"
            ),
            "the refusal says why the allowance did not apply",
        )


if __name__ == "__main__":
    unittest.main()
