"""A program's correction of a child's displayed plan is the program's guidance, never the user's input (#866)."""

import copy
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_brief_obligations as brief_obligations
import autocode_contract_revision as contract_revision
import autocode_goal_lifecycle as lifecycle
import autocode_program_guidance as guidance
import autocode_requirement_cues as requirement_cues
from goal_fixtures import body
from units import autoplanner as planner

# The correction autocode_program sends when a child's draft drops an inherited requirement.
TEXT = (
    "The program agreement rejects this plan: it drops inherited requirement(s) C2. Keep each inherited "
    "requirement's id, exact criterion definition and human_review obligation, and every inherited constraint "
    "and permission boundary. Copy their text from the brief; do not weaken or paraphrase it."
)


class ProgramGuidanceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.name=F", "-c", "user.email=f@example.test"]
            + ["commit", "--allow-empty", "-qm", "fixture"],
            check=True,
        )
        self.state = {
            "version": 2,
            "workspace": str(self.root),
            "task": "Make a greeting tool",
            "status": "RUNNING",
            "iteration": 1,
            "sessions": {},
            "stages": [],
            "history": [],
            "next_stage": "terra",
            "acceptance_criteria": [],
            "settings": {
                "roles": {r: {"model": r, "reasoning_effort": "high"} for r in ("astra", "terra", "sol")},
                "transport_identity": {"auth_mode": "fixture"},
                "headroom": {"enabled": False},
                "context_soft_tokens": 10000,
                "limits": {"iteration_ceiling": 5, "max_seconds": None, "no_progress_batches": 3},
            },
        }
        lifecycle.migrate(self.state)
        lifecycle.install_draft(self.state, body(), origin="test")
        lifecycle.human.evaluate(self.state)
        lifecycle.present(self.state)
        self.assertEqual("AWAITING_GOAL_APPROVAL", self.state["status"])
        self.token = self.state["displayed_goal"]

    def test_it_is_saved_as_the_programs_and_never_as_user_input(self):
        episode = copy.deepcopy(self.state.get("clarification_episode"))
        user_events = copy.deepcopy(self.state.get("user_events", []))
        event = guidance.apply(self.state, TEXT, self.token)
        self.assertEqual(("program_guidance", "program", TEXT), (event["kind"], event["actor"], event["text"]))
        self.assertEqual([event], self.state["program_guidance"])
        self.assertEqual(user_events, self.state.get("user_events", []))
        self.assertEqual([], self.state.get("brief_feedback", []))
        # No clarification episode starts, so the investigation allowance is not renewed.
        self.assertEqual(episode, self.state.get("clarification_episode"))

    def test_it_is_no_requirement_source_declaration_or_decision_basis(self):
        event = guidance.apply(self.state, TEXT, self.token)
        # The Requirements check owed every requirement-like sentence of a scanned text a quote (#866).
        self.assertNotIn(TEXT, requirement_cues.scan_texts(self.state))
        self.assertNotIn(TEXT, requirement_cues.source_texts(self.state))
        self.assertNotIn(TEXT, [record["text"] for record in brief_obligations.sources(self.state)])
        self.assertFalse(contract_revision.saved_user_basis(self.state, "user_feedback", event["id"]))

    def test_it_sends_the_displayed_plan_back_to_planning_for_a_new_approval(self):
        guidance.apply(self.state, TEXT, self.token)
        self.assertEqual(
            ("RUNNING", "DISCOVERING", "astra_discovery"),
            (self.state["status"], self.state["phase"], self.state["next_stage"]),
        )
        self.assertNotIn("displayed_goal", self.state)
        with self.assertRaises(ValueError):
            lifecycle.approve(self.state, self.token)

    def test_it_refuses_a_stale_token_empty_text_and_an_approved_plan(self):
        before = copy.deepcopy(self.state)
        for text, token in ((TEXT, "r1:stale"), (TEXT, None), ("  ", self.token)):
            with self.subTest(text=text, token=token), self.assertRaises(ValueError):
                guidance.apply(self.state, text, token)
            self.assertEqual(before, self.state)
        lifecycle.approve(self.state, self.token)
        with self.assertRaises(ValueError):
            guidance.apply(self.state, TEXT, self.token)

    def test_the_planning_stages_read_this_turns_guidance_as_the_programs(self):
        prompt, _ = planner.context(self.state, "astra_discovery", self.root / "state.json")
        self.assertNotIn("PROGRAM GUIDANCE:", prompt)
        event = guidance.apply(self.state, TEXT, self.token)
        self.assertEqual([{"id": event["id"], "text": TEXT}], guidance.current(self.state))
        prompt, _ = planner.context(self.state, "astra_discovery", self.root / "state.json")
        self.assertIn("PROGRAM GUIDANCE:", prompt)
        self.assertIn('"program_guidance"', prompt)
        # A later turn's plans are not this correction's.
        self.state.setdefault("turns", []).append({"at": "9999-01-01T00:00:00Z"})
        self.assertEqual([], guidance.current(self.state))


if __name__ == "__main__":
    unittest.main()
