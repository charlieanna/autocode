"""Saved code checkpoints attributed to the approved plan's criteria through their milestones (#730)."""

import copy
import subprocess
import tempfile
import unittest

import autocode_criterion_changes as criterion_changes
import autocode_goals as goals
import autocode_run_view as run_view
import goal_fixtures

BODY = {
    "acceptance_criteria": [{"id": "C1"}, {"id": "C2"}, {"id": "C3"}],
    "milestones": [
        {"id": "M1", "acceptance_criteria": ["C1", "C3"]},
        {"id": "M2", "acceptance_criteria": ["C2", "C3"]},
    ],
}


def row(ident, milestone, files):
    return {"id": ident, "milestone_id": milestone, "changed_files": files, "commit": "a" * 40}


class AttributeTests(unittest.TestCase):
    def test_no_approved_plan_has_no_attribution(self):
        self.assertIsNone(criterion_changes.attribute(None, [row("code-1", "M1", ["a.py"])]))
        self.assertIsNone(criterion_changes.attribute({"acceptance_criteria": "broken"}, []))

    def test_each_criterion_names_its_milestones_checkpoints_and_files(self):
        rows = [
            row("code-1", "M1", ["app.py", "greet.py"]),
            row("code-2", "M2", ["cli.py"]),
            row("code-3", "M1", ["greet.py", "util.py"]),
        ]
        before = copy.deepcopy(rows)
        result = criterion_changes.attribute(BODY, rows)
        self.assertIn("not proof", result["meaning"])
        by_id = {item["id"]: item for item in result["criteria"]}
        self.assertEqual(["C1", "C2", "C3"], [item["id"] for item in result["criteria"]])
        self.assertEqual(
            {
                "id": "C1",
                "milestones": ["M1"],
                "checkpoints": ["code-1", "code-3"],
                "files": ["app.py", "greet.py", "util.py"],
                "overlapping": False,
            },
            by_id["C1"],
        )
        self.assertEqual(["code-2"], by_id["C2"]["checkpoints"])
        self.assertEqual(["M1", "M2"], by_id["C3"]["milestones"])
        self.assertEqual(["code-1", "code-2", "code-3"], by_id["C3"]["checkpoints"])
        self.assertEqual([], result["unattributed"])
        self.assertEqual(before, rows)

    def test_a_criterion_with_no_saved_step_yet_is_listed_empty(self):
        result = criterion_changes.attribute(BODY, [row("code-1", "M1", ["a.py"])])
        c2 = next(item for item in result["criteria"] if item["id"] == "C2")
        self.assertEqual(
            {"checkpoints": [], "files": [], "overlapping": False},
            {k: c2[k] for k in ("checkpoints", "files", "overlapping")},
        )

    def test_a_file_another_milestone_also_changed_is_overlapping(self):
        rows = [row("code-1", "M1", ["shared.py"]), row("code-2", "M2", ["shared.py", "cli.py"])]
        by_id = {item["id"]: item for item in criterion_changes.attribute(BODY, rows)["criteria"]}
        self.assertTrue(by_id["C1"]["overlapping"])
        self.assertTrue(by_id["C2"]["overlapping"])
        # C3 covers both milestones, so no change outside its own attribution touched its files.
        self.assertFalse(by_id["C3"]["overlapping"])

    def test_steps_outside_any_plan_milestone_are_unattributed_and_can_overlap(self):
        rows = [row("code-1", "M1", ["greet.py"]), row("code-2", None, ["greet.py"]), row("code-3", "M9", ["x.py"])]
        result = criterion_changes.attribute(BODY, rows)
        self.assertEqual(["code-2", "code-3"], result["unattributed"])
        c1 = next(item for item in result["criteria"] if item["id"] == "C1")
        self.assertEqual(["code-1"], c1["checkpoints"])
        self.assertTrue(c1["overlapping"])

    def test_a_parallel_batch_step_gives_each_member_milestone_only_its_own_files(self):
        batch = {
            "id": "code-2",
            "milestone_id": None,
            "changed_files": ["cli.py", "greet.py"],
            "members": [
                {"milestone_id": "M1", "changed_files": ["greet.py"]},
                {"milestone_id": "M2", "changed_files": ["cli.py"]},
            ],
            "commit": "b" * 40,
        }
        result = criterion_changes.attribute(BODY, [row("code-1", "M1", ["app.py"]), batch])
        by_id = {item["id"]: item for item in result["criteria"]}
        self.assertEqual(
            {"checkpoints": ["code-1", "code-2"], "files": ["app.py", "greet.py"], "overlapping": False},
            {k: by_id["C1"][k] for k in ("checkpoints", "files", "overlapping")},
        )
        self.assertEqual(
            {"checkpoints": ["code-2"], "files": ["cli.py"], "overlapping": False},
            {k: by_id["C2"][k] for k in ("checkpoints", "files", "overlapping")},
        )
        # C3 belongs to both members, and the shared step is listed once.
        self.assertEqual(["code-1", "code-2"], by_id["C3"]["checkpoints"])
        self.assertEqual(["app.py", "cli.py", "greet.py"], by_id["C3"]["files"])
        self.assertEqual([], result["unattributed"])

    def test_a_batch_member_outside_the_plan_is_unattributed_once(self):
        batch = {
            "id": "code-1",
            "milestone_id": None,
            "changed_files": ["greet.py", "x.py", "y.py"],
            "members": [
                {"milestone_id": "M1", "changed_files": ["greet.py"]},
                {"milestone_id": "M9", "changed_files": ["x.py"]},
                {"milestone_id": "M8", "changed_files": ["y.py"]},
                "broken",
            ],
        }
        result = criterion_changes.attribute(BODY, [batch])
        self.assertEqual(["code-1"], result["unattributed"])
        c1 = next(item for item in result["criteria"] if item["id"] == "C1")
        self.assertEqual((["code-1"], ["greet.py"], False), (c1["checkpoints"], c1["files"], c1["overlapping"]))

    def test_malformed_rows_and_milestones_are_skipped(self):
        body = {
            "acceptance_criteria": [{"id": "C1"}, "C2", {"id": 3}],
            "milestones": [
                {"id": "M1", "acceptance_criteria": ["C1"]},
                "M2",
                {"id": "M3", "acceptance_criteria": "C1"},
            ],
        }
        rows = [
            "code-0",
            row("code-1", "M1", ["ok.py", 7, "", "ok.py"]),
            {"id": "code-2", "milestone_id": "M1", "changed_files": "bad.py"},
            {"milestone_id": "M1", "changed_files": ["noid.py"]},
        ]
        result = criterion_changes.attribute(body, rows)
        self.assertEqual(
            [
                {
                    "id": "C1",
                    "milestones": ["M1"],
                    "checkpoints": ["code-1", "code-2"],
                    "files": ["ok.py"],
                    "overlapping": False,
                }
            ],
            result["criteria"],
        )
        self.assertEqual([], result["unattributed"])
        self.assertEqual(result, criterion_changes.attribute(body, rows + [None]))
        self.assertEqual([], criterion_changes.attribute(body, "broken")["criteria"][0]["checkpoints"])


class StatusViewTests(unittest.TestCase):
    def state(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        subprocess.run(["git", "init", "-q", temp.name], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                temp.name,
                "-c",
                "user.name=T",
                "-c",
                "user.email=t@example.invalid",
                "commit",
                "-q",
                "--allow-empty",
                "-m",
                "base",
            ],
            check=True,
        )
        return {"status": "RUNNING", "workspace": temp.name, "task": "Greeting", "current_task": {"id": "t1"}}

    def test_the_view_attributes_saved_checkpoints_to_the_approved_plan(self):
        state = self.state()
        goal_fixtures.approve_fixture(state, goals)
        state["code_checkpoints"] = [row("code-1", "M1", ["greet.py"]), row("code-2", None, ["notes.md"])]
        shown = run_view.view(state)["changes_by_criterion"]
        self.assertEqual(
            [
                {
                    "id": "C1",
                    "milestones": ["M1"],
                    "checkpoints": ["code-1"],
                    "files": ["greet.py"],
                    "overlapping": False,
                }
            ],
            shown["criteria"],
        )
        self.assertEqual(["code-2"], shown["unattributed"])

    def test_without_an_approved_plan_the_field_is_none(self):
        self.assertIsNone(run_view.view({"status": "RUNNING"})["changes_by_criterion"])
        state = self.state()
        state["code_checkpoints"] = [row("code-1", "M1", ["greet.py"])]
        self.assertIsNone(run_view.view(state)["changes_by_criterion"])


if __name__ == "__main__":
    unittest.main()
