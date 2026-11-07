"""Internal defaults reach a reviewed plan through the real, offline CLI."""

import json
import os
import sys
import textwrap
import unittest
from pathlib import Path

import autocode_taskrun as taskrun

from .test_agent_step import fixture_workspace
from .test_taskrun import BRIEF, FIXTURE_OPTIONS


class TechnicalQuestionsCliTests(unittest.TestCase):
    def run_question(self, stage, category, *, repeat=False):
        workspace, env = fixture_workspace(self)
        provider = Path(env["PATH"].split(os.pathsep)[0]) / "codex"
        marker = '    output = Path(sys.argv[sys.argv.index("-o") + 1])'
        amendment = textwrap.dedent("""
            asked = Path(os.environ["QUESTION_ASKED"])
            count = int(asked.read_text()) if asked.exists() else 0
            limit = 2 if os.environ["QUESTION_REPEAT"] == "1" else 1
            if stage == os.environ["QUESTION_STAGE"] and not repairing and count < limit:
                asked.write_text(str(count + 1))
                report["contract"]["open_blocking_questions"] = [{
                    "id": "internal-proof", "question": "May AC4 use the executable fixture?",
                    "why": "The example needs a complete input and exact Python invocation.",
                    "options": [], "proposed_default": "Use the complete input and Python command.",
                    "kind": "decision", "category": os.environ["QUESTION_CATEGORY"], "delegable": False}]
                if count:
                    report["contract"]["open_blocking_questions"][0].update(
                        question="Can the previously selected executable fixture verify AC4?",
                        why="The input and Python invocation are already available.",
                        proposed_default="Keep the complete input and the same Python invocation.")
                report["contract"].update(technical_approach=[], milestones=[])
                if "initial_task" in report["contract"]:
                    report["contract"]["initial_task"] = {
                        "objective": "", "affected_paths": [], "kind": "none", "milestone_id": "",
                        "requirements": [], "acceptance_criteria": [], "validation_plan": []}
        """)
        source = provider.read_text()
        self.assertEqual(1, source.count(marker))
        provider.write_text(source.replace(marker, textwrap.indent(amendment, "    ") + "\n" + marker))
        env.update(
            QUESTION_ASKED=str(provider.parent / "asked"),
            QUESTION_STAGE=stage,
            QUESTION_CATEGORY=category,
            QUESTION_REPEAT="1" if repeat else "0",
        )
        env["PATH"] = f"{provider.parent}{os.pathsep}{Path(sys.executable).parent}{os.pathsep}{env['PATH']}"
        self.run = taskrun.TaskRun.start(
            workspace,
            BRIEF,
            options=(*FIXTURE_OPTIONS, "--joint-planning", "--no-adaptive-planning"),
            env=env,
            timeout=180,
        )
        return self.run.advance_until_input()

    def test_internal_discovery_and_revision_questions_need_no_human_answer(self):
        for stage in ("astra_discovery", "glm_revise"):
            with self.subTest(stage=stage):
                view = self.run_question(stage, "technical")
                self.assertEqual("approve_plan", view["needs"]["kind"], (view["status"], view["stop_reason"]))
                self.assertEqual("AWAITING_GOAL_APPROVAL", view["status"])
                self.assertTrue(view.get("displayed_plan"))
                self.assertFalse(any("AC4" in row["text"] for row in view["chat"]["messages"]))
                state = json.loads((self.run.run_dir / "state.json").read_text())
                reviews = [
                    row["stage"] for row in state["stages"] if row.get("stage") in ("astra_challenge", "astra_finalize")
                ]
                if stage == "glm_revise":
                    # The revision's question was AutoCode's to answer, so the redraft continues the reviewed
                    # cycle: the saved first review and one final review, not a second review from scratch.
                    self.assertEqual(1, len(state["planning_history"]))
                    self.assertEqual(["astra_challenge", "astra_finalize"], reviews)
                    self.assertEqual(2, state["planning"]["astra_calls"])
                    self.assertEqual("astra_finalize", state["planning"]["inherited_review"]["next_stage"])
                else:
                    self.assertEqual([], state.get("planning_history", []))
                    self.assertEqual(["astra_challenge", "astra_finalize"], reviews)

    def test_a_behavior_choice_still_requires_the_human_even_with_a_default(self):
        view = self.run_question("astra_discovery", "behavior")
        self.assertEqual("answer", view["needs"]["kind"], (view["status"], view["stop_reason"]))
        self.assertEqual("WAITING_FOR_USER", view["status"])
        self.assertEqual(["internal-proof"], [q["id"] for q in view["needs"]["questions"]])

    def test_rephrased_internal_question_repairs_the_report_without_another_full_planner_call(self):
        view = self.run_question("astra_discovery", "technical", repeat=True)
        self.assertEqual("approve_plan", view["needs"]["kind"], (view["status"], view["stop_reason"]))
        attempts = view["usage"]["accounting"]["attempts"]
        drafts = [row for row in attempts if row["stage"] == "astra_discovery" and not row["report_only"]]
        self.assertEqual(2, len(drafts), "A paraphrase must not start a third full Planner pass")
        repairs = [row for row in attempts if row["report_only"]]
        self.assertEqual(1, len(repairs))
        self.assertEqual("astra_discovery", repairs[0]["original_stage"])
        self.assertTrue(any(row["rejected"] for row in drafts), "Retain the rejected repeated question")
        self.assertNotIn("terra", [row["stage"] for row in attempts], "A technical default never approves a plan")
        reopened = self.run.status()
        self.assertEqual(view["displayed_plan"], reopened["displayed_plan"])
        self.assertEqual(attempts, reopened["usage"]["accounting"]["attempts"])


if __name__ == "__main__":
    unittest.main()
