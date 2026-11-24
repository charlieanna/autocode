"""Each guard's example is checked against the original code before the plan is approved.

A guard says behavior already works and names the test that shows it. The regression proof checks that only
after the Builder ran, so a guard naming an existing test that fails on the original code cost a whole repair
loop. That test is now run on the original commit when the draft is installed, and such a guard goes back to
the Planner as a plan correction, before approval.
"""

import copy
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_goal_lifecycle as lifecycle
import autocode_plan_base_guards as base_guards
import autocode_plan_defects as plan_defects
from goal_fixtures import body

PRODUCT = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a + b  # the bug the plan fixes\n"
TESTS = (
    "import unittest\n\nfrom calc import add, sub\n\n\n"
    "class CalcTest(unittest.TestCase):\n"
    "    def test_add(self):\n        self.assertEqual(3, add(1, 2))\n\n"
    "    def test_sub(self):\n        self.assertEqual(2, sub(3, 1))\n"
)


def plan(*methods):
    value = body()
    rows = [
        {"id": f"C{number}", "criterion": f"Check {number} holds", "verification_method": method, "human_review": False}
        for number, method in enumerate(methods, 1)
    ]
    value["acceptance_criteria"] = rows
    value["milestones"][0]["acceptance_criteria"] = [row["id"] for row in rows]
    return value


class WorkspaceCase(unittest.TestCase):
    def setUp(self):
        base_guards.clear()
        self.addCleanup(base_guards.clear)
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        (self.root / "tests").mkdir()
        (self.root / "calc.py").write_text(PRODUCT)
        (self.root / "tests" / "__init__.py").write_text("")
        (self.root / "tests" / "test_calc.py").write_text(TESTS)
        git = ["git", "-C", str(self.root), "-c", "user.email=a@example.com", "-c", "user.name=a"]
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run([*git, "add", "-A"], check=True)
        subprocess.run([*git, "commit", "-qm", "base"], check=True)
        self.base = subprocess.run(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip()

    def state(self):
        return {
            "version": 3,
            "task_id": "task-guards",
            "task": "Fix subtraction",
            "workspace": str(self.root),
            "base_commit": self.base,
            "status": "RUNNING",
            "next_stage": "draft_plan",
            "settings": {"joint_planning": True},
            "answers": {},
            "user_events": [],
        }


class ObserveTests(WorkspaceCase):
    def test_a_guard_whose_existing_test_fails_on_the_original_code_is_found(self):
        found = base_guards.observe(
            self.state(),
            plan(
                "guard: test_add",  # exists and passes: right
                "guard: test_sub",  # exists and fails on the original code
                "guard: test_subtraction_is_commutative_enough",  # a new test the Builder writes
                "guard: test_c4_subtraction_keeps_working",  # a new test named after its criterion
                "test: test_sub",  # a test: criterion is new behavior; nothing to check today
            ),
        )
        self.assertEqual(["C2"], [row["id"] for row in found])
        [failing] = found
        self.assertEqual("test_sub", failing["test"])
        self.assertTrue(failing["failed"] and all("test_sub" in test for test in failing["failed"]))
        self.assertIn(self.base[:12], failing["detail"])
        # The output and the command-supervision receipts are retained beside each other.
        output = Path(failing["detail"].rsplit("(output ", 1)[1].rstrip(")"))
        self.assertTrue(output.is_file())
        self.addCleanup(shutil.rmtree, output.parent, True)
        self.assertTrue(list((output.parent / "command-supervision").glob("*/supervision.json")))

    def test_nothing_is_observed_without_an_original_commit_or_guards(self):
        no_base = {**self.state(), "base_commit": None}
        self.assertEqual([], base_guards.observe(no_base, plan("guard: test_sub")))
        self.assertEqual([], base_guards.observe(self.state(), plan("test: test_c1_subtracts")))
        self.assertIsNone(base_guards.defect(self.state(), plan("guard: test_add")))

    def test_a_run_that_cannot_report_results_decides_nothing(self):
        # The original test file does not load: no per-test result can refute the guard, so the proof decides.
        (self.root / "tests" / "test_calc.py").write_text(TESTS + "\ndef broken(:\n")
        git = ["git", "-C", str(self.root), "-c", "user.email=a@example.com", "-c", "user.name=a"]
        subprocess.run([*git, "commit", "-qam", "broken"], check=True)
        state = {**self.state(), "base_commit": "HEAD"}
        self.assertEqual([], base_guards.observe(state, plan("guard: test_sub")))

    def test_the_question_names_each_criterion_and_what_the_original_code_showed(self):
        draft = plan("guard: test_sub", "guard: test_add")
        question = base_guards.defect(self.state(), draft)
        self.assertEqual((base_guards.QUESTION_ID, plan_defects.KIND), (question["id"], question["kind"]))
        self.assertEqual(["C1"], plan_defects.named_parts(question, draft))
        self.assertIn("test_sub", question["why"])
        self.assertIn("fails", question["why"])


class InstallTests(WorkspaceCase):
    def corrections(self, current):
        return [e for e in current["user_events"] if e.get("settled_as") == plan_defects.KIND]

    def test_a_guard_the_original_code_contradicts_goes_back_to_the_planner_before_approval(self):
        current = self.state()
        lifecycle.install_draft(current, plan("guard: test_add", "guard: test_sub"), origin="planner_draft")
        self.assertEqual(("RUNNING", "draft_plan"), (current["status"], current["next_stage"]))
        self.assertEqual("none", lifecycle.human.evaluate(current))
        [event] = self.corrections(current)
        self.assertEqual((base_guards.QUESTION_ID, ["C2"]), (event["question_id"], event["plan_parts"]))
        self.assertIn("test_sub", event["text"])
        self.assertIn("fails", event["question"]["why"])
        self.assertEqual([], current["goal_contract"]["body"]["open_blocking_questions"])

    def test_guards_the_original_code_supports_reach_approval_unchanged(self):
        current = self.state()
        draft = plan("guard: test_add", "guard: test_subtraction_of_zero", "test: test_c3_subtracts")
        lifecycle.install_draft(current, copy.deepcopy(draft), origin="planner_draft")
        self.assertEqual([], self.corrections(current))


if __name__ == "__main__":
    unittest.main()
