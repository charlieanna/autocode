"""`autocode-issue plan`'s commit and pull request body (autocode_issue_plan, #720). See docs/issues.md."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_issue_plan as issue_plan
import autocode_plan_file as plan_file

IDENTITY = {
    "GIT_AUTHOR_NAME": "T",
    "GIT_AUTHOR_EMAIL": "t@example.test",
    "GIT_COMMITTER_NAME": "T",
    "GIT_COMMITTER_EMAIL": "t@example.test",
}
BODY = {"intended_outcome": "Greet by name", "acceptance_criteria": [{"id": "C1", "criterion": "prints Hello"}]}


class _Run:
    """The issue's TaskRun as prepare() uses it: export_plan writes the plan waiting for approval."""

    def __init__(self, body, revision):
        self.text = plan_file.export(body, revision=revision, approved=False)

    def export_plan(self, path):
        Path(path).write_text(self.text, encoding="utf-8", newline="")
        return f"Wrote plan to {path}"


class PrepareTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="issue-plan-")
        self.addCleanup(temp.cleanup)
        environment = patch.dict(os.environ, IDENTITY)
        environment.start()
        self.addCleanup(environment.stop)
        self.project = Path(temp.name) / "project"
        self.git("init", "-q", "-b", "main", str(self.project), cwd=Path(temp.name))
        (self.project / "README.md").write_text("widgets\n")
        self.git("add", "README.md")
        self.git("commit", "-q", "-m", "base")
        self.base = self.git("rev-parse", "HEAD")
        self.record = {
            "owner": "acme",
            "repo": "widgets",
            "number": 7,
            "title": "Add a greeting CLI",
            "branch": "autocode/issue-7-add-a-greeting-cli",
            "base_commit": self.base,
            "run_dir": "/p/.autocode/runs/run-1",
        }

    def git(self, *args, cwd=None):
        return subprocess.run(
            ["git", "-C", str(cwd or self.project), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    def blob(self, commit):
        return subprocess.run(
            ["git", "-C", str(self.project), "cat-file", "blob", f"{commit}:plans/issue-7.md"],
            check=True,
            capture_output=True,
        ).stdout.decode()

    def prepare(self, run, path="plans/issue-7.md"):
        entry = issue_plan.prepare(self.project, self.record, run, path)
        self.record[issue_plan.FIELD] = entry
        return entry

    def test_the_plan_is_committed_on_a_branch_of_its_own_without_touching_the_checkout(self):
        # The checkout moved past the run's base commit since the run started; the plan still goes on the base.
        (self.project / "later.txt").write_text("later\n")
        self.git("add", "later.txt")
        self.git("commit", "-q", "-m", "later work")
        (self.project / "staged.txt").write_text("staged\n")
        self.git("add", "staged.txt")
        before = (self.git("rev-parse", "HEAD"), self.git("status", "--porcelain"))
        self.assertNotEqual(self.base, before[0])
        run = _Run(BODY, 2)
        entry = self.prepare(run)
        branch = "autocode/issue-7-add-a-greeting-cli-plan"
        self.assertEqual((branch, entry["commit"]), (entry["branch"], self.git("rev-parse", branch)))
        self.assertEqual(self.base, self.git("rev-parse", f"{branch}^"))
        self.assertEqual("A\tplans/issue-7.md", self.git("diff", "--name-status", self.base, branch))
        self.assertEqual(run.text, self.blob(branch))
        goal = plan_file.goal_hash(BODY)
        self.assertEqual(
            (2, goal, goal, None), (entry["revision"], entry["goal_hash"], entry["run_goal_hash"], entry["url"])
        )
        self.assertEqual(before, (self.git("rev-parse", "HEAD"), self.git("status", "--porcelain")))

    def test_an_unchanged_plan_reuses_its_commit_and_a_revised_one_goes_on_top(self):
        first = self.prepare(_Run(BODY, 2))
        self.record[issue_plan.FIELD]["url"] = "https://github.example/acme/widgets/pull/1"
        self.assertEqual(first["commit"], self.prepare(_Run(BODY, 2))["commit"])
        revised = {**BODY, "intended_outcome": "Greet by full name"}
        second = self.prepare(_Run(revised, 3))
        self.assertEqual(first["commit"], self.git("rev-parse", f"{second['commit']}^"))
        self.assertEqual(second["commit"], self.git("rev-parse", second["branch"]))
        self.assertEqual((3, plan_file.goal_hash(revised)), (second["revision"], second["goal_hash"]))
        self.assertEqual("https://github.example/acme/widgets/pull/1", second["url"])
        self.assertEqual(plan_file.export(revised, revision=3, approved=False), self.blob(second["branch"]))

    def test_a_branch_moved_since_is_refused_and_left_as_it_is(self):
        entry = self.prepare(_Run(BODY, 2))
        self.git("branch", "-f", entry["branch"], self.base)
        with self.assertRaisesRegex(ValueError, "no longer points at .*never replaces commits it did not make"):
            self.prepare(_Run({**BODY, "intended_outcome": "Other"}, 3))
        self.assertEqual(self.base, self.git("rev-parse", entry["branch"]))

    def test_a_deleted_branch_is_made_again_where_it_was_left(self):
        """A branch deleted here (`git branch -D`, `gh pr merge --delete-branch`) holds nothing to replace."""
        entry = self.prepare(_Run(BODY, 2))
        self.git("branch", "-D", entry["branch"])
        self.assertEqual(entry["commit"], self.prepare(_Run(BODY, 2))["commit"])
        self.assertEqual(entry["commit"], self.git("rev-parse", entry["branch"]))
        self.git("branch", "-D", entry["branch"])
        second = self.prepare(_Run({**BODY, "intended_outcome": "Greet by full name"}, 3))
        self.assertEqual(entry["commit"], self.git("rev-parse", f"{second['commit']}^"))
        self.assertEqual(second["commit"], self.git("rev-parse", entry["branch"]))

    def test_an_existing_branch_is_not_taken_over(self):
        self.git("branch", "autocode/issue-7-add-a-greeting-cli-plan", self.base)
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.prepare(_Run(BODY, 2))
        self.assertEqual(self.base, self.git("rev-parse", "autocode/issue-7-add-a-greeting-cli-plan"))

    def test_a_base_that_already_holds_the_plan_has_nothing_to_propose(self):
        run = _Run(BODY, 2)
        (self.project / "plans").mkdir()
        (self.project / "plans/issue-7.md").write_text(run.text)
        self.git("add", "plans/issue-7.md")
        self.git("commit", "-q", "-m", "plan")
        self.record["base_commit"] = self.git("rev-parse", "HEAD")
        with self.assertRaisesRegex(ValueError, "already holds this plan; there is nothing to propose"):
            self.prepare(run)
        self.assertEqual("", self.git("branch", "--list", "*-plan"))

    def test_a_merged_pull_request_shows_in_the_base_whatever_the_merge_method(self):
        entry = self.prepare(_Run(BODY, 2))
        bases = ("refs/remotes/origin/main", "refs/heads/main")  # a missing ref holds nothing
        self.assertIsNone(issue_plan.merged_into(self.project, self.record, bases))
        self.git("commit", "-q", "--allow-empty", "-m", "unrelated work on main")
        self.assertIsNone(issue_plan.merged_into(self.project, self.record, bases))
        self.git("branch", "squashed")
        self.git("merge", "-q", "--no-edit", entry["branch"])
        self.assertEqual(self.git("rev-parse", "main"), issue_plan.merged_into(self.project, self.record, bases))
        # A squash merge of a reviewer's edit: the plan commit is not in the base, but its file changed there.
        self.git("checkout", "-q", "squashed")
        (self.project / "plans").mkdir(exist_ok=True)
        (self.project / "plans/issue-7.md").write_text("edited by a reviewer\n")
        self.git("add", "plans/issue-7.md")
        self.git("commit", "-q", "-m", "Plan (#1)")
        self.assertEqual(
            self.git("rev-parse", "squashed"),
            issue_plan.merged_into(self.project, self.record, ("refs/heads/squashed",)),
        )

    def test_after_a_merge_a_new_pull_request_starts_from_the_base_on_the_next_branch(self):
        first = self.prepare(_Run(BODY, 2))
        self.record[issue_plan.FIELD]["url"] = "https://github.example/acme/widgets/pull/1"
        self.git("merge", "-q", "--ff-only", first["branch"])
        merged = issue_plan.merged_into(self.project, self.record, ("refs/heads/main",))
        self.assertTrue(issue_plan.carried(first, "r2:abc"))
        self.assertFalse(issue_plan.carried(first, "r4:abc"))
        with self.assertRaisesRegex(ValueError, "already holds this plan; there is nothing to propose"):
            issue_plan.prepare(self.project, self.record, _Run(BODY, 2), "plans/issue-7.md", start=merged)
        revised = {**BODY, "intended_outcome": "Greet by full name"}
        second = issue_plan.prepare(self.project, self.record, _Run(revised, 4), "plans/issue-7.md", start=merged)
        self.assertEqual(
            ("autocode/issue-7-add-a-greeting-cli-plan-2", 2, merged, None),
            (second["branch"], second["round"], second["parent"], second["url"]),
        )
        self.assertEqual(merged, self.git("rev-parse", f"{second['branch']}^"))
        self.assertEqual("M\tplans/issue-7.md", self.git("diff", "--name-status", merged, second["branch"]))
        self.assertEqual(first["commit"], self.git("rev-parse", first["branch"]))  # the merged one's branch stays
        self.record[issue_plan.FIELD] = second
        self.assertIsNone(issue_plan.merged_into(self.project, self.record, ("refs/heads/main",)))
        self.assertEqual(second["commit"], self.prepare(_Run(revised, 4))["commit"])

    def test_another_path_than_the_one_the_pull_request_carries_is_refused(self):
        self.prepare(_Run(BODY, 2))
        with self.assertRaisesRegex(ValueError, "carries plans/issue-7.md; pass --path plans/issue-7.md"):
            self.prepare(_Run(BODY, 2), "docs/plan.md")


class PathTests(unittest.TestCase):
    def test_a_path_must_stay_inside_the_repository_and_out_of_run_data(self):
        self.assertEqual("plans/issue-7.md", issue_plan.repository_path("./plans//issue-7.md"))
        for value in ("", " ", "/plans/x.md", "../x.md", "plans/../../x.md", ".autocode/x.md", ".git/x", "a\\b.md"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                issue_plan.repository_path(value)


class BodyTests(unittest.TestCase):
    RECORD = {"owner": "acme", "repo": "widgets", "number": 7, "run_dir": "/p/.autocode/runs/run-1"}

    def entry(self, run_goal_hash="a" * 64):
        return {"path": "plans/issue-7.md", "revision": 2, "goal_hash": "a" * 64, "run_goal_hash": run_goal_hash}

    def test_the_body_says_what_the_file_is_how_to_review_it_and_that_merging_approves_nothing(self):
        body = issue_plan.body(self.RECORD, self.entry())
        self.assertTrue(body.startswith("The plan AutoCode drafted for acme/widgets#7, for review before anything"))
        self.assertIn("`plans/issue-7.md` is plan revision 2 of AutoCode run `run-1`, waiting for approval.", body)
        self.assertIn(f"Its goal hash is `{'a' * 64}`, what `autocode plan --check plans/issue-7.md` prints.", body)
        self.assertIn("`autocode plan --render plans/issue-7.md`", body)
        self.assertIn("Merging this pull request approves nothing.", body)
        self.assertIn("`autocode --goal-file plans/issue-7.md`", body)
        self.assertIn("A new run cannot be started from the file yet.", body)
        self.assertNotIn("[REDACTED]", body)
        self.assertNotIn("Resolves", body)

    def test_a_masked_file_says_its_goal_hash_differs_from_the_runs_plan(self):
        body = issue_plan.body(self.RECORD, self.entry(run_goal_hash="b" * 64))
        self.assertIn(
            f"masked as `[REDACTED]` in this file, so its goal hash differs from the run's plan (`{'b' * 64}`)", body
        )


if __name__ == "__main__":
    unittest.main()
