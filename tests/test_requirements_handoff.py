"""Which Requirements reports the runner accepts as the handoff (autocode_requirements_handoff.accept)."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_changeset_store as sets
import autocode_requirements_handoff as handoff

QUESTION = {
    "id": "Q1",
    "question": "What would you like to build or change?",
    "why": "No software outcome has been named yet.",
    "options": [],
    "proposed_default": "",
    "kind": "decision",
    "category": "requested_outcome",
    "delegable": False,
}


def report(**change):
    value = {
        "summary": "The person asked my name.",
        "intended_outcome": "",
        "required_behaviors": [],
        "constraints": [],
        "acceptance_tests": [],
        "source_refs": [],
        "proposed_assumptions": [],
        "open_questions": [QUESTION],
        "requirements": [],
        "ignored_statements": [],
        "conflicts": [],
        "proposed_reframes": [],
    }
    value.update(change)
    return value


class RequirementsHandoffTests(unittest.TestCase):
    def accept(self, value, settings=None, **saved):
        settings = {"spec_first": True} if settings is None else settings
        state = {"task": "What is your name?", "settings": settings, "stages": [], **saved}
        queued = []
        handoff.accept(
            state,
            value,
            {"output": None},
            check_code_refs=lambda *_: self.fail("An outcome question makes no code claim"),
            queue=lambda *args, **kwargs: queued.append(kwargs),
        )
        return state, queued

    def test_a_chat_reply_that_only_asks_for_the_outcome_needs_no_outcome_yet(self):
        state, queued = self.accept(report())
        self.assertEqual("", state["requirements_handoff"]["report"]["intended_outcome"])
        self.assertEqual([[QUESTION]], [row["questions"] for row in queued])

    def test_the_saved_handoff_keeps_only_the_external_parts_the_request_grounds(self):
        # #2066: the runner, not the model, decides what counts as another repository's work.
        scope = {
            "spans_repositories": True,
            "external_parts": [
                {"target": "other-repo", "work": "Store it", "source_quote": "store it in other-repo", "evidence": ""},
                {
                    "target": "guessed-repo",
                    "work": "Bill it",
                    "source_quote": "bill it in guessed-repo",
                    "evidence": "",
                },
            ],
        }
        state, _ = self.accept(report(workspace_scope=scope), task="Show the field here and store it in other-repo.")
        saved = state["requirements_handoff"]["report"]["workspace_scope"]
        self.assertTrue(saved["spans_repositories"])
        self.assertEqual(["other-repo"], [part["target"] for part in saved["external_parts"]])
        self.assertEqual(["guessed-repo"], state["requirements_handoff"]["dropped_external_parts"])
        self.assertNotIn("dropped", saved, "the saved report stays valid against its schema")

    def test_a_handoff_without_a_workspace_scope_is_saved_unchanged(self):
        state, _ = self.accept(report())
        self.assertNotIn("workspace_scope", state["requirements_handoff"]["report"])

    def test_a_handoff_that_claims_work_still_needs_its_outcome(self):
        for value, settings in (
            (report(required_behaviors=["Greet"], acceptance_tests=["Prints hello"]), None),
            (report(), {}),
            (report(open_questions=[]), None),
        ):
            with self.subTest(value=value, settings=settings), self.assertRaisesRegex(ValueError, "needs an outcome"):
                self.accept(value, settings)

    def test_a_reply_that_names_no_goal_and_asks_nothing_waits_for_the_person(self):
        # Live, Kilo GLM 5.3 in idlecampus: "hi", then "Nothing yet, just saying hello." The honest
        # reply was refused, repaired twice and sent to the Investigator.
        reply = report(
            open_questions=[],
            assistant_reply="Understood. Whenever something comes to mind, just describe it.",
            summary="The user answered Q1 with 'Nothing yet, just saying hello.'",
        )
        state, queued = self.accept(reply, answers={"Q1": {"text": "Nothing yet, just saying hello."}})
        [[question]] = [row["questions"] for row in queued]
        self.assertEqual("requested_outcome", question["category"])
        self.assertEqual(handoff.OUTCOME_QUESTION, question["question"])
        self.assertTrue(question["id"].startswith("outcome-"))
        self.assertNotIn(question["id"], state["answers"], "a new id: an answered question cannot be asked again")
        self.assertEqual("", state["requirements_handoff"]["report"]["intended_outcome"])

    def test_a_handoff_that_only_asks_waits_for_the_answers_on_the_default_flow(self):
        # Live (program plan, "build an adaptive learning engine", 2026-10-20): Requirements asked three good
        # questions with the acceptance tests still empty, as its prompt asks while clarifying, and the default
        # flow refused the handoff as invalid output until a person resumed the run.
        task = "Build an adaptive learning engine."
        question = {**QUESTION, "id": "Q-form", "question": "What form should the engine take, and who uses it?"}
        value = report(
            intended_outcome=task,
            requirements=[{"id": "R1", "text": task, "source_quote": task}],
            open_questions=[question],
        )
        state, queued = self.accept(value, {}, task=task)
        self.assertEqual([[question]], [row["questions"] for row in queued], "its own questions, no review question")
        self.assertEqual(value, state["requirements_handoff"]["report"])
        self.assertNotIn("goal_contract", state)

    def test_without_an_open_question_a_handoff_still_needs_behaviors_and_acceptance_tests(self):
        for value in (
            report(intended_outcome="Add export", open_questions=[]),
            report(intended_outcome="Add export", required_behaviors=["Export CSV"], open_questions=[]),
        ):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "needs an outcome"):
                self.accept(value, {})

    def test_without_spec_first_or_with_claimed_work_a_goalless_reply_is_still_refused(self):
        chat = {"open_questions": [], "assistant_reply": "Understood."}
        for value, settings in ((report(**chat), {}), (report(**chat, required_behaviors=["Greet"]), None)):
            with self.subTest(settings=settings), self.assertRaisesRegex(ValueError, "needs an outcome"):
                self.accept(value, settings)

    def test_clarifying_a_stated_requirement_does_not_require_inspecting_source(self):
        task = "Make chat respond quickly while keeping all safety checks."
        question = {**QUESTION, "question": "What response time would be quick enough?"}
        value = report(
            intended_outcome=task,
            requirements=[{"id": "R1", "text": task, "source_quote": task}],
            constraints=["Keep all safety checks"],
            open_questions=[question],
        )
        state, queued = self.accept(value, task=task)
        self.assertEqual([[question]], [row["questions"] for row in queued])
        self.assertNotIn("goal_contract", state)

    def test_a_coordinated_member_role_label_is_refused_before_any_state_write(self):
        # #2420: the live attempt-5 tax Requirements could have carried the same
        # role labels; the refusal names the canonical members for report repair.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with mock.patch.dict(os.environ, {"AUTOCODE_HOME": str(root / "registry")}):
                anchor = root / "anchor" / ".autocode" / "runs" / "chat"
                anchor.mkdir(parents=True)
                spaces = {name: root / name for name in ("checkout", "history", "tax")}
                for space in spaces.values():
                    space.mkdir()
                record = sets.reserve(
                    source_workspace=spaces["checkout"],
                    source_run_dir=anchor,
                    target="history",
                    workspace=spaces["history"],
                    brief="Fix the total",
                )
                sets.reserve(
                    source_workspace=spaces["checkout"],
                    source_run_dir=anchor,
                    target="tax",
                    workspace=spaces["tax"],
                    brief="Fix the total",
                )
                run_dir = spaces["tax"] / ".autocode" / "runs" / "fix"
                sets.bind(record["id"], "tax", run_dir)
                sets.set_fields(record["id"], {"automatic_anchor": str(root / "coordination")})
                state = {
                    "task": "Fix checkout showing '100.0 USD' for a 100 USD subtotal.",
                    "settings": {"spec_first": True},
                    "stages": [],
                    "run_dir": str(run_dir),
                    "workspace": str(spaces["tax"]),
                }
                scope = {
                    "spans_repositories": True,
                    "external_parts": [
                        {
                            "target": "Checkout display member",
                            "work": "Produce the final '$110.00' display",
                            "source_quote": "Fix checkout showing '100.0 USD' for a 100 USD subtotal.",
                            "evidence": "",
                        }
                    ],
                }
                with self.assertRaisesRegex(ValueError, "role label.*coordinated members are: checkout, history, tax"):
                    handoff.accept(
                        state,
                        report(workspace_scope=scope),
                        {"output": None},
                        check_code_refs=lambda *_: self.fail("No code claim is made"),
                        queue=lambda *args, **kwargs: None,
                    )
                self.assertNotIn("requirements_handoff", state)
                self.assertNotIn("requirements_history", state)
                canonical = {
                    "spans_repositories": True,
                    "external_parts": [
                        {
                            "target": "checkout",
                            "work": "Produce the final '$110.00' display",
                            "source_quote": "Fix checkout showing '100.0 USD' for a 100 USD subtotal.",
                            "evidence": "",
                        }
                    ],
                }
                handoff.accept(
                    state,
                    report(workspace_scope=canonical),
                    {"output": None},
                    check_code_refs=lambda *_: None,
                    queue=lambda *args, **kwargs: None,
                )
                self.assertEqual(
                    ["checkout"],
                    [
                        part["target"]
                        for part in state["requirements_handoff"]["report"]["workspace_scope"]["external_parts"]
                    ],
                )
        task = "Make chat respond quickly while keeping all safety checks."
        question = {**QUESTION, "question": "What response time would be quick enough?"}
        value = report(
            intended_outcome=task,
            requirements=[{"id": "R1", "text": task, "source_quote": task}],
            constraints=["Keep all safety checks"],
            open_questions=[question],
        )
        state, queued = self.accept(value, task=task)
        self.assertEqual([[question]], [row["questions"] for row in queued])
        self.assertNotIn("goal_contract", state)

    def test_completed_requirements_and_explicit_source_refs_still_check_source(self):
        for value in (
            report(intended_outcome="Add export", source_refs=["export.py"]),
            report(intended_outcome="Add export", required_behaviors=["Export CSV"]),
            report(
                intended_outcome="Add export",
                required_behaviors=["Export CSV"],
                acceptance_tests=["The export is valid CSV"],
                open_questions=[],
            ),
        ):
            with self.subTest(value=value):
                check = mock.Mock(side_effect=ValueError("source check required"))
                with self.assertRaisesRegex(ValueError, "source check required"):
                    handoff.accept(
                        {"task": "Add export", "settings": {"spec_first": True}, "stages": []},
                        value,
                        {"output": None},
                        check_code_refs=check,
                        queue=mock.Mock(),
                    )
                check.assert_called_once()


if __name__ == "__main__":
    unittest.main()
