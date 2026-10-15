"""`autocode --goal-file PATH`: reading a plan file, the planner-draft checks it gets, and what the approval
stop says about it (tools/autocode_goal_file.py, #720). The CLI behavior is in test_goal_file_cli.
"""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

import autocode_brief_literals as brief_literals
import autocode_goal_file as goal_file
import autocode_goals as goals
import autocode_plan_file as plan_file
from goal_fixtures import body as fixture_body
from units import autoplanner

BRIEF = "Build `todo.py list`, which prints `ID TEXT [open|done]` for each to-do."
REVIEW = {"stage": "astra_challenge", "events": "/run/a/plan-challenge-01.jsonl", "events_sha256": "1" * 64}
QUESTION = {
    "id": "Q-format",
    "question": "Which columns does `todo.py list` print?",
    "why": "The output format decides every test of the command",
    "options": ["ID and text", "ID, text and state"],
    "proposed_default": "ID, text and state",
}


def plan_body(**changes) -> dict:
    """A plan that keeps both literals of BRIEF; ``changes`` replace its parts."""
    body = fixture_body()
    body["open_blocking_questions"] = []
    body["required_behaviors"] = ["`todo.py list` prints `ID TEXT [open|done]` for each to-do"]
    body.update(changes)
    return body


def brief_state(body=None, **extra) -> dict:
    return {
        "task": BRIEF,
        "answers": {},
        "brief_feedback": [],
        "user_events": [],
        "goal_contract": {"revision": 2, "body": body or plan_body()},
        **extra,
    }


class CheckTests(unittest.TestCase):
    def test_a_plan_that_drops_a_brief_literal_gets_the_message_a_planner_draft_gets(self):
        body = plan_body(required_behaviors=["`todo.py list` prints one line for each to-do"])
        state = brief_state()
        with self.assertRaises(ValueError) as planner:
            goals.check_requirement_trace(state, {}, body)
        with self.assertRaises(ValueError) as loaded:
            goal_file.check(state, body, coverage=True)
        self.assertEqual(str(planner.exception), str(loaded.exception))
        self.assertEqual(brief_literals.error(["ID TEXT [open|done]"]), str(loaded.exception))

    def test_a_plan_that_keeps_them_passes(self):
        goal_file.check(brief_state(), plan_body(), coverage=autoplanner.traces_coverage(plan_body()))

    def test_a_plan_with_an_open_blocking_question_is_not_held_to_the_brief_literals(self):
        # As for a planner's clarification-only draft (units.autoplanner.traces_coverage), which is how the
        # --goal-file action step decides ``coverage``; the CLI test drives that step.
        dropped = plan_body(required_behaviors=["Prints to-dos"])
        with self.assertRaises(ValueError):
            goal_file.check(brief_state(), dropped, coverage=autoplanner.traces_coverage(dropped))
        asking = plan_body(required_behaviors=["Prints to-dos"], open_blocking_questions=[QUESTION])
        goal_file.check(brief_state(), asking, coverage=autoplanner.traces_coverage(asking))

    def test_runner_records_must_be_the_runs_own_and_absent_ones_are_kept(self):
        record = {"manifest": {"observations": []}, "review": REVIEW, "amendments": []}
        state = brief_state(plan_body(brief_acceptance=record))
        goal_file.check(state, plan_body(brief_acceptance=record), coverage=True)
        goal_file.check(state, plan_body(), coverage=True)  # install_draft keeps the run's own
        other = copy.deepcopy(record)
        other["review"]["events_sha256"] = "2" * 64
        with self.assertRaisesRegex(ValueError, r"brief_acceptance is not this run's.*delete brief_acceptance"):
            goal_file.check(state, plan_body(brief_acceptance=other), coverage=False)
        with self.assertRaisesRegex(ValueError, r"source_trace is not this run's"):
            goal_file.check(state, plan_body(source_trace={"version": 1, "review": REVIEW}), coverage=True)


class ReadTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="goal-file-")
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "plan.md"

    def test_an_exported_file_reads_back_its_plan(self):
        self.path.write_text(plan_file.export(plan_body(), revision=2, approved=False))
        plan = goal_file.read(self.path)
        self.assertEqual(plan_body(), plan.body)
        self.assertEqual(plan_file.goal_hash(plan_body()), plan.goal_hash)

    def test_a_file_whose_text_no_longer_matches_its_front_matter_is_refused(self):
        text = plan_file.export(plan_body(), revision=2, approved=False)
        self.path.write_text(text.replace('"Web service"', '"Web service or API"', 1))
        with self.assertRaisesRegex(ValueError, r"no longer matches it \(line \d+\).*autocode plan --render"):
            goal_file.read(self.path)

    def test_a_missing_file_and_a_file_without_a_plan_are_refused(self):
        with self.assertRaisesRegex(ValueError, "Cannot read the plan file"):
            goal_file.read(self.path)
        self.path.write_text("# Plan\n")
        with self.assertRaisesRegex(ValueError, "not a plan file AutoCode can load: A plan file starts with"):
            goal_file.read(self.path)


class ApprovalLinesTests(unittest.TestCase):
    def loaded_run(self, current: dict | None = None) -> dict:
        """A run whose revision 3 was loaded from a file; ``current`` is revision 4's body, else 3 is current."""
        body = plan_body(brief_acceptance={"manifest": {}, "review": REVIEW, "amendments": []})
        loaded = {"revision": 3, "body": body, "approval_status": "draft", goal_file.FIELD: {"path": "/p.md"}}
        loaded[goal_file.FIELD]["goal_hash"] = plan_file.goal_hash(body)
        history = [{"revision": 2, "body": plan_body(), "approval_status": "draft"}]
        if current is None:
            return {"contract_history": history, "goal_contract": loaded}
        return {
            "contract_history": [*history, loaded],
            "goal_contract": {"revision": 4, "body": current, "approval_status": "draft"},
        }

    def test_a_run_without_a_plan_file_says_nothing(self):
        self.assertEqual([], goal_file.approval_lines(brief_state()))

    def test_the_loaded_revision_itself_is_approved_exactly(self):
        run = self.loaded_run()
        lines = goal_file.approval_lines(run)
        goal = plan_file.goal_hash(run["goal_contract"]["body"])
        self.assertEqual(["", f"Plan file: /p.md (goal hash {goal}), loaded as revision 3."], lines[:2])
        self.assertIn("is the plan in that file, unchanged: the approval token approves exactly it", lines[2])

    def test_a_new_review_of_the_same_plan_changes_only_the_runners_records(self):
        state = self.loaded_run()
        reviewed = copy.deepcopy(state["goal_contract"]["body"])
        reviewed["brief_acceptance"]["review"] = dict(REVIEW, events="/run/a/plan-challenge-02.jsonl")
        lines = goal_file.approval_lines(self.loaded_run(reviewed))
        self.assertIn("only the runner's records of the plan's review differ", lines[2])
        self.assertIn(plan_file.goal_hash(reviewed), lines[2])

    def changed(self) -> dict:
        """The loaded plan with a constraint added and a runner record's observations changed."""
        changed = copy.deepcopy(self.loaded_run()["goal_contract"]["body"])
        changed["constraints"].append("Keep todo.py short")
        changed["brief_acceptance"]["manifest"] = {"observations": ["another"]}
        return changed

    def test_a_changed_plan_names_what_changed_and_a_later_approval_ends_the_note(self):
        changed = self.changed()
        lines = goal_file.approval_lines(self.loaded_run(changed))
        self.assertIn("differs from the plan loaded from that file in brief_acceptance, constraints", lines[2])
        run = self.loaded_run(changed)
        run["contract_history"].append({"revision": 4, "body": changed, "approval_status": "approved"})
        run["goal_contract"] = {"revision": 5, "body": changed, "approval_status": "draft"}
        self.assertEqual([], goal_file.approval_lines(run))

    def test_an_approval_of_the_loaded_revision_itself_ends_the_note(self):
        # Without joint planning the loaded revision is the one approved, in place
        # (autocode_goal_lifecycle.approve), and the next draft copies it, plan_file included, into the history.
        run = self.loaded_run(self.changed())
        self.assertIn("loaded as revision 3", goal_file.approval_lines(run)[1])
        loaded = run["contract_history"][-1]
        loaded.update(hash="3" * 64, approval_status="approved")
        loaded["approval_event"] = {"kind": "goal_approval", "actor": "user_cli", "token": f"r3:{'3' * 64}"}
        run["user_events"] = [loaded["approval_event"]]
        self.assertEqual([], goal_file.approval_lines(run))

    def test_an_approval_reset_to_draft_since_the_load_still_ends_the_note(self):
        # Feedback, an answer or a rejected assumption resets an approved revision to draft in place
        # (autocode_goals.apply_intervention_feedback); its saved goal_approval event stays.
        changed = self.changed()
        run = self.loaded_run(changed)
        run["contract_history"].append(
            {"revision": 4, "hash": "4" * 64, "body": changed, "approval_status": "draft", "approval_event": None}
        )
        run["goal_contract"] = {"revision": 5, "body": changed, "approval_status": "draft"}
        self.assertIn("loaded as revision 3", goal_file.approval_lines(run)[1])
        run["user_events"] = [{"kind": "goal_approval", "actor": "user_cli", "token": f"r4:{'4' * 64}"}]
        self.assertEqual([], goal_file.approval_lines(run))


if __name__ == "__main__":
    unittest.main()
