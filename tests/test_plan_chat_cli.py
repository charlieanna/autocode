"""Plan discussion preserves approval through the offline public CLI."""

import os
import textwrap
import unittest
from pathlib import Path

import autocode_taskrun as taskrun

from .test_agent_step import fixture_workspace
from .test_taskrun import BRIEF, FIXTURE_OPTIONS

WHY = "Why did you choose this approach?"
MIXED = "Why this approach? Keep the same scope and standard-library-only constraint."
REPLY = "The plan uses the standard library to keep the greeting CLI dependency-free."


class PlanChatCliTests(unittest.TestCase):
    def make_plan(self):
        workspace, env = fixture_workspace(self)
        provider = Path(env["PATH"].split(os.pathsep)[0]) / "codex"
        marker = '    output = Path(sys.argv[sys.argv.index("-o") + 1])'
        amendment = textwrap.dedent(f"""
            if stage == "requirements_gather" and not data.get("message"):
                for event in data.get("brief_feedback") or []:
                    if event.get("text") == {MIXED!r}:
                        report["requirements"].append({{"id": "R5", "text": "Keep the existing scope and standard-library-only constraint",
                                                       "source_quote": event["text"]}})
            if stage == "requirements_gather" and data.get("message"):
                report = {{"message_id": data["message"]["id"],
                          "contract_token": data["message"]["contract_token"],
                          "intent": "change" if data["message"]["text"] == {MIXED!r} else "question", "assistant_reply": {REPLY!r}}}
        """)
        provider.write_text(provider.read_text().replace(marker, textwrap.indent(amendment, "    ") + "\n" + marker))
        run = taskrun.TaskRun.start(
            workspace, BRIEF, options=(*FIXTURE_OPTIONS, "--spec-first", "--no-adaptive-planning"), env=env, timeout=300
        )
        gathered = run.advance_until_input()
        self.assertEqual("answer", gathered["needs"]["kind"], gathered)
        question = gathered["needs"]["questions"][-1]
        run.answer(question["id"], "plan", resolver_token=gathered["needs"]["resolver_token"])
        reviewed = run.advance_until_input()
        self.assertEqual("approve_plan", reviewed["needs"]["kind"], reviewed)
        return run, workspace, reviewed

    def test_mixed_corrections_need_a_new_review_and_the_old_token_cannot_approve(self):
        run, workspace, reviewed = self.make_plan()
        token = reviewed["needs"]["token"]
        run.feedback(MIXED)
        refreshed = run.advance_until_input()
        self.assertEqual("approve_plan", refreshed["needs"]["kind"], (refreshed["status"], refreshed["stop_reason"]))
        self.assertNotEqual(token, refreshed["needs"]["token"])
        self.assertGreater(refreshed["displayed_plan"]["revision"], reviewed["displayed_plan"]["revision"])
        with self.assertRaises(taskrun.TaskRunError):
            run.approve_plan(token)
        texts = [row["text"] for row in refreshed["chat"]["messages"]]
        self.assertEqual(1, texts.count(MIXED))
        self.assertFalse((workspace / "greet.py").exists())

    def test_questions_preserve_the_reviewed_plan_and_explicit_approval_still_works(self):
        run, workspace, reviewed = self.make_plan()
        token, plan = reviewed["needs"]["token"], reviewed["displayed_plan"]
        for message in (WHY, "Does that keep dependencies unchanged?"):
            run.feedback(message)
            queued = run.status()
            self.assertEqual(plan, queued.get("displayed_plan"), "Discussion must preserve the displayed plan")
            answered = run.advance_until_input()
            self.assertEqual("approve_plan", answered["needs"]["kind"], (answered["status"], answered["stop_reason"]))
            self.assertEqual(token, answered["needs"]["token"])
            self.assertEqual(plan, answered["displayed_plan"])
            texts = [row["text"] for row in answered["chat"]["messages"]]
            self.assertEqual([message, REPLY], texts[-2:])
            self.assertFalse((workspace / "greet.py").exists(), "A question cannot authorize Builder")
        run.approve_plan(token)
        self.assertEqual("TASK_COMPLETE", run.advance_until_input()["status"])


if __name__ == "__main__":
    unittest.main()
