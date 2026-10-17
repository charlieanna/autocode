"""Internal defaults reach a reviewed plan through the real, offline CLI."""

import os
import textwrap
import unittest
from pathlib import Path

import autocode_taskrun as taskrun

from .test_agent_step import fixture_workspace
from .test_taskrun import BRIEF, FIXTURE_OPTIONS


class TechnicalQuestionsCliTests(unittest.TestCase):
    def run_question(self, stage, category):
        workspace, env = fixture_workspace(self)
        provider = Path(env["PATH"].split(os.pathsep)[0]) / "codex"
        marker = '    output = Path(sys.argv[sys.argv.index("-o") + 1])'
        amendment = textwrap.dedent("""
            asked = Path(os.environ["QUESTION_ASKED"])
            if stage == os.environ["QUESTION_STAGE"] and not asked.exists():
                asked.write_text("asked")
                report["contract"]["open_blocking_questions"] = [{
                    "id": "internal-proof", "question": "May AC4 use the executable fixture?",
                    "why": "The example needs a complete input and exact Python invocation.",
                    "options": [], "proposed_default": "Use the complete input and Python command.",
                    "kind": "decision", "category": os.environ["QUESTION_CATEGORY"], "delegable": False}]
                report["contract"].update(technical_approach=[], milestones=[])
                if "initial_task" in report["contract"]:
                    report["contract"]["initial_task"] = {
                        "objective": "", "affected_paths": [], "kind": "none", "milestone_id": "",
                        "requirements": [], "acceptance_criteria": [], "validation_plan": []}
        """)
        source = provider.read_text()
        self.assertEqual(1, source.count(marker))
        provider.write_text(source.replace(marker, textwrap.indent(amendment, "    ") + "\n" + marker))
        env.update(QUESTION_ASKED=str(provider.parent / "asked"), QUESTION_STAGE=stage, QUESTION_CATEGORY=category)
        run = taskrun.TaskRun.start(
            workspace,
            BRIEF,
            options=(*FIXTURE_OPTIONS, "--joint-planning", "--no-adaptive-planning"),
            env=env,
            timeout=180,
        )
        return run.advance_until_input()

    def test_internal_discovery_and_revision_questions_need_no_human_answer(self):
        for stage in ("astra_discovery", "glm_revise"):
            with self.subTest(stage=stage):
                view = self.run_question(stage, "technical")
                self.assertEqual("approve_plan", view["needs"]["kind"], (view["status"], view["stop_reason"]))
                self.assertEqual("AWAITING_GOAL_APPROVAL", view["status"])
                self.assertTrue(view.get("displayed_plan"))
                self.assertFalse(any("AC4" in row["text"] for row in view["chat"]["messages"]))

    def test_a_behavior_choice_still_requires_the_human_even_with_a_default(self):
        view = self.run_question("astra_discovery", "behavior")
        self.assertEqual("answer", view["needs"]["kind"], (view["status"], view["stop_reason"]))
        self.assertEqual("WAITING_FOR_USER", view["status"])
        self.assertEqual(["internal-proof"], [q["id"] for q in view["needs"]["questions"]])


if __name__ == "__main__":
    unittest.main()
