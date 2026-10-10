"""The literals a brief states survive into the goal contract (tools/autocode_brief_literals.py)."""

import unittest
from pathlib import Path

import autocode_brief_literals as literals
import autocode_goals as goals
import autocode_planning as planning
from goal_fixtures import body

TODO_BRIEF = (Path(__file__).resolve().parents[1] / "scenarios/catalog/greenfield-todo-cli/brief.md").read_text()


def todo_contract(listed="1 buy milk [open]"):
    contract = body()
    contract["deliverables"] = ["todo.py", "test_todo.py", "README.md with the command summary"]
    contract["acceptance_criteria"] = [
        {
            "id": "C1",
            "criterion": "Given an empty store; when `todo.py add buy milk` runs; then it exits 0",
            "verification_method": "test: test_c1_add",
            "human_review": False,
        },
        {
            "id": "C2",
            "criterion": f"Given one to-do; when `todo.py list` runs; then stdout is exactly `{listed}`",
            "verification_method": "test: test_c2_list",
            "human_review": False,
        },
        {
            "id": "C3",
            "criterion": "Given one to-do; when `todo.py complete 1` runs; then `todo.py list` shows it done",
            "verification_method": "test: test_c3_complete",
            "human_review": False,
        },
    ]
    return contract


class LiteralsTests(unittest.TestCase):
    def test_inline_spans_in_order_without_repeats_or_fenced_blocks(self):
        text = "Run `a  b`, then `c` and `a b`.\n```\n`not a literal`\n```\nAlso `d`.\n```unterminated `e`"
        self.assertEqual(["a b", "c", "d"], literals.literals([text]))

    def test_the_todo_brief_states_four_literals(self):
        self.assertEqual(
            ["todo.py add TEXT", "todo.py list", "ID TEXT [open|done]", "todo.py complete ID"],
            literals.literals([TODO_BRIEF]),
        )

    def test_placeholders_and_alternations_make_a_template_file_names_and_values_do_not(self):
        self.assertIsNone(literals.template("todo.py list"))
        self.assertIsNone(literals.template("README.md"))
        self.assertIsNone(literals.template("2024-W54"))
        self.assertTrue(literals.template("ID TEXT [open|done]").search("7 buy milk [done]"))
        self.assertFalse(literals.template("ID TEXT [open|done]").search("7 buy milk done"))
        self.assertTrue(literals.template("--name=NAME").search("run --name=Ada"))


class MissingTests(unittest.TestCase):
    def setUp(self):
        self.found = literals.literals([TODO_BRIEF])

    def test_the_live_false_completion_is_caught(self):
        # 2026-10-01: the examples wrote the brief's `ID TEXT [open|done]` as `1 buy milk open`.
        self.assertEqual(["ID TEXT [open|done]"], literals.missing(self.found, todo_contract("1 buy milk open")))

    def test_filled_in_examples_keep_every_literal(self):
        self.assertEqual([], literals.missing(self.found, todo_contract()))

    def test_quoting_the_template_itself_keeps_it(self):
        self.assertEqual([], literals.missing(self.found, todo_contract("ID TEXT [open|done]")))

    def test_a_format_quoted_verbatim_anywhere_is_kept(self):
        # Whether the examples then agree with it is the Plan Reviewer's check (BRIEF_TRACE_RULE).
        contract = todo_contract("1 buy milk open")
        contract["required_behaviors"].append("list prints every to-do as `ID TEXT [open|done]`")
        self.assertEqual([], literals.missing(self.found, contract))

    def test_a_template_filled_in_outside_the_criteria_is_not_kept(self):
        contract = todo_contract("1 buy milk open")
        contract["required_behaviors"].append("list prints lines such as 1 buy milk [open]")
        self.assertEqual(["ID TEXT [open|done]"], literals.missing(self.found, contract))

    def test_a_plain_literal_may_be_anywhere_in_the_contract(self):
        self.assertEqual([], literals.missing(["README.md"], todo_contract()))
        self.assertEqual(["todos.json"], literals.missing(["todos.json"], todo_contract()))

    def test_the_error_names_every_dropped_literal(self):
        message = literals.error(["ID TEXT [open|done]", "todos.json"])
        self.assertIn("`ID TEXT [open|done]`", message)
        self.assertIn("`todos.json`", message)


class PlannerDraftTests(unittest.TestCase):
    """Through goals.check_requirement_trace, which the runner calls on every planner draft."""

    def state(self, **extra):
        return {"task": TODO_BRIEF, "answers": {}, "brief_feedback": [], **extra}

    def test_a_draft_that_drops_a_brief_literal_goes_back_to_the_planner(self):
        with self.assertRaisesRegex(ValueError, r"drops literals.*`ID TEXT \[open\|done\]`"):
            goals.check_requirement_trace(self.state(), {}, todo_contract("1 buy milk open"))

    def test_a_draft_that_keeps_them_passes(self):
        goals.check_requirement_trace(self.state(), {}, todo_contract())

    def test_a_clarification_only_draft_is_not_checked(self):
        goals.check_requirement_trace(self.state(), {}, todo_contract("1 buy milk open"), coverage=False)

    def test_literals_in_the_users_answers_count_and_a_delegated_default_does_not(self):
        answers = {"Q1": {"text": "Store it in `todos.json`", "kind": "user"}}
        with self.assertRaisesRegex(ValueError, "`todos.json`"):
            goals.check_requirement_trace(self.state(answers=answers), {}, todo_contract())
        answers = {"Q1": {"text": "Store it in `todos.json`", "kind": "delegated"}}
        goals.check_requirement_trace(self.state(answers=answers), {}, todo_contract())


class PlannerPromptTests(unittest.TestCase):
    """The drafting stages are told which literals the runner will check, so a draft need not be sent back.
    In the 2026-10-04 live runs every bug-fix draft first dropped `2024-W54`, the wrong output its report quotes."""

    def prompt(self, stage):
        state = {
            "version": 3,
            "task_id": "task-1",
            "task": TODO_BRIEF,
            "workspace": "/absent-workspace",
            "settings": {"joint_planning": True, "roles": {"plan_reviewer": {}}},
            "answers": {},
            "user_events": [],
            "acceptance_criteria": [],
        }
        return planning.context(state, stage, Path("/run/state.json"))[0]

    def test_each_drafting_stage_names_every_literal(self):
        for stage in ("astra_discovery", "glm_revise", "astra_finalize"):
            text = self.prompt(stage)
            with self.subTest(stage=stage):
                self.assertIn("BRIEF LITERALS", text)
                for literal in literals.literals([TODO_BRIEF]):
                    self.assertIn(f"`{literal}`", text.split("BRIEF LITERALS", 1)[1].split("\n", 1)[0])

    def test_a_stage_whose_draft_is_not_checked_gets_no_list(self):
        self.assertNotIn("BRIEF LITERALS", self.prompt("requirements_gather"))


if __name__ == "__main__":
    unittest.main()
