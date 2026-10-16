"""`autocode --goal-file PATH` through the CLI, on fake-provider runs (#720).

Drives the greenfield-todo-cli catalog scenario to the plan it asks a person to approve, exports it with
`autocode plan --export`, and loads edited copies of the file. A copy that drops a literal of the brief,
names git status, plans a command the shell cannot parse, or whose text no longer matches its front
matter is refused and the run is left as it was; the copy that drops the literal loads once it also asks a
blocking question, and only as the invocation's one action. A reviewed copy becomes the next revision, goes
through the Plan Reviewer and is approved with its own token, not the one shown before it was loaded, and the
completed run's evidence report cites it by path and goal hash. The pure checks are in test_goal_file.
"""

from __future__ import annotations

import copy
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import autocode_brief_literals as brief_literals
import autocode_goal_file as goal_file
import autocode_goals as goals
import autocode_plan_file as plan_file
from harness import catalog
from harness.driver import Driver, default_autocode, fake_setup
from harness.project import materialize

SCENARIO = "greenfield-todo-cli"


class GoalFileCliTests(unittest.TestCase):
    """Through the CLI on a real fake-provider run waiting for plan approval."""

    @classmethod
    def setUpClass(cls):
        directory = tempfile.TemporaryDirectory(prefix="goal-file-cli-")
        cls.addClassCleanup(directory.cleanup)
        cls.base = Path(directory.name)
        with ThreadPoolExecutor(max_workers=2) as pool:
            cls.refused, cls.loaded = pool.map(cls.drive, ("refused", "loaded"))

    @classmethod
    def drive(cls, name: str) -> Driver:
        scenario = catalog.load(SCENARIO)
        root = cls.base / name
        root.mkdir()
        project = materialize(scenario.seed, root / "project", npm_setup=scenario.npm_setup)
        flags, env = fake_setup(scenario, root, scenario.reference)
        driver = Driver(
            project,
            root,
            flags,
            env,
            autocode=default_autocode(),
            max_steps=scenario.max_steps,
            timeout_seconds=600,
            explicit_answers=scenario.fake_answers,
            acknowledge=scenario.fake_acknowledge,
        )
        driver.call("start", task=scenario.brief)
        driver.run_dir = next((project / ".autocode" / "runs").glob("*/state.json")).parent
        driver.until_stopped(say_at="needs:approve_plan")
        return driver

    def autocode(self, driver: Driver, *argv: str) -> subprocess.CompletedProcess:
        where = ["--workspace", str(driver.project), "--run-dir", str(driver.run_dir)]
        return subprocess.run(
            [*default_autocode(), *where, *argv], capture_output=True, text=True, env=driver.env, timeout=300
        )

    def plan(self, driver: Driver, *argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [*default_autocode(), "plan", *argv], capture_output=True, text=True, env=driver.env, timeout=120
        )

    def exported(self, driver: Driver) -> str:
        """The run's plan as `autocode plan --export` writes it."""
        path = driver.root / "exported.md"
        result = self.plan(driver, "--export", str(path), "--run-dir", str(driver.run_dir))
        self.assertEqual(0, result.returncode, result.stderr)
        return path.read_text()

    def edited(self, driver: Driver, exported: str, name: str, edit) -> tuple[Path, dict]:
        """``exported`` with ``edit`` applied to its front matter and the text regenerated (`autocode plan --render`)."""
        plan = plan_file.read(exported)
        body = copy.deepcopy(plan.body)
        edit(body)
        front = {"autocode_plan": plan_file.FORMAT, "goal_hash": plan.exported_hash, "revision": plan.revision}
        path = driver.root / name
        path.write_text(plan_file.document({**front, "approval_status": plan.approval_status, "contract": body}))
        return path, body

    def assert_refused(self, driver: Driver, path: Path, expected: str) -> None:
        before = driver.state()
        result = self.autocode(driver, "--goal-file", str(path))
        self.assertEqual(2, result.returncode, result.stdout[-2000:] + result.stderr[-2000:])
        self.assertIn("Input rejected: " + expected, result.stderr)
        self.assertEqual(before, driver.state(), "a refused plan file leaves the run as it was")

    def test_a_hand_edited_file_is_refused_where_a_planner_draft_would_be_sent_back(self):
        driver = self.refused
        self.assertEqual("AWAITING_GOAL_APPROVAL", driver.state()["status"])
        exported = self.exported(driver)

        def drop_literal(body):
            # A reviewer renames a command the brief spells out; the brief's own words still say it.
            before = list(body["required_behaviors"])
            body["required_behaviors"] = [text.replace("`todo.py complete ID`", "`todo.py done ID`") for text in before]
            self.assertNotEqual(before, body["required_behaviors"])

        path, body = self.edited(driver, exported, "dropped.md", drop_literal)
        # Acceptance 2 (#720): the planner's own message for this contract.
        with self.assertRaises(ValueError) as planner:
            goals.check_requirement_trace(driver.state(), {}, body)
        self.assertEqual(brief_literals.error(["todo.py complete ID"]), str(planner.exception))
        self.assert_refused(driver, path, str(planner.exception))

        # A reviewer edits the front matter and forgets to regenerate the text under it.
        stale = driver.root / "stale.md"
        stale.write_text(exported.replace('"Python standard library only', '"Python 3 standard library only'))
        self.assertNotEqual(exported, stale.read_text())
        self.assert_refused(driver, stale, f"{stale}: the text under the front matter no longer matches it (line ")

        def method(text):
            return lambda body: body["acceptance_criteria"][0].update(verification_method=text)

        git_status, _ = self.edited(driver, exported, "git-status.md", method("git status --porcelain"))
        self.assert_refused(driver, git_status, "Acceptance criterion C1's verification_method `git status")
        unparsable, _ = self.edited(driver, exported, "unparsable.md", method('sh -c "echo ("'))
        self.assert_refused(driver, unparsable, 'Acceptance criterion C1\'s verification_method `sh -c "echo ("` (')

        def ask(body):
            # The renamed command again, now with a blocking question about it: a planner's draft with an open
            # question is not held to the brief's literals until the answer (units.autoplanner.traces_coverage).
            drop_literal(body)
            question = {"id": "Q-file-done", "question": "Which name completes a to-do?", "why": "Reviewers disagree"}
            body["open_blocking_questions"] = [{**question, "options": ["yes", "no"], "proposed_default": "no"}]

        asking, body = self.edited(driver, exported, "asking.md", ask)
        # One action per invocation, as for every other: the same file alone loads below.
        before = driver.state()
        both = self.autocode(driver, "--goal-file", str(asking), "--approve-goal", before["displayed_goal"])
        self.assertEqual(2, both.returncode, both.stdout[-2000:] + both.stderr[-2000:])
        self.assertIn("Choose one action per invocation", both.stderr)
        self.assertEqual(before, driver.state())
        result = self.autocode(driver, "--goal-file", str(asking))
        self.assertEqual(0, result.returncode, result.stdout[-2000:] + result.stderr[-2000:])
        self.assertEqual(body, driver.state()["goal_contract"]["body"])

    def test_a_reviewed_file_becomes_the_next_revision_and_is_approved_as_usual(self):
        driver = self.loaded
        shown = driver.state()
        path, body = self.edited(
            driver, self.exported(driver), "reviewed.md", lambda body: body["constraints"].append("Keep it short")
        )
        goal = plan_file.goal_hash(body)
        result = self.autocode(driver, "--goal-file", str(path))
        self.assertEqual(0, result.returncode, result.stderr[-2000:])
        revision = shown["goal_contract"]["revision"] + 1
        self.assertIn(
            f"Loaded the plan in {path} (goal hash {goal}) as plan revision {revision}; it is not approved.",
            result.stdout,
        )
        contract = driver.state()["goal_contract"]
        self.assertEqual(
            (revision, "user_cli_edit", "draft"),
            (contract["revision"], contract["origin"], contract["approval_status"]),
        )
        self.assertEqual(body, contract["body"])
        self.assertEqual({"path": str(path.resolve()), "goal_hash": goal}, contract[goal_file.FIELD])

        # Ordinary approval: the Plan Reviewer reviews the loaded plan, then the person approves its token.
        need = driver.until_stopped(say_at="needs:approve_plan")["needs"]
        self.assertEqual("approve_plan", need["kind"])
        screen = self.autocode(driver, "--show-goal").stdout
        self.assertIn(f"Plan file: {path.resolve()} (goal hash {goal}), loaded as revision {revision}.", screen)
        reviewed = driver.state()["goal_contract"]
        self.assertEqual([], goal_file.changed(body, reviewed["body"]))
        self.assertIn(
            "only the runner's records of the plan's review differ from the file, so its goal hash is "
            + plan_file.goal_hash(reviewed["body"]),
            screen,
        )
        # The plan shown before the load is replaced: at this stop, where a plan is up for approval, its token
        # is refused as no longer the one displayed, and the run is left as it was.
        self.assertNotEqual(shown["displayed_goal"], need["token"])
        before = driver.state()
        stale = self.autocode(driver, "--approve-goal", shown["displayed_goal"])
        self.assertEqual(2, stale.returncode, stale.stdout[-2000:] + stale.stderr[-2000:])
        self.assertIn("Approve only the current displayed draft token", stale.stderr)
        self.assertEqual(before, driver.state())
        driver.serve(need)
        approved = driver.state()["goal_contract"]
        self.assertEqual(
            ("approved", need["token"]), (approved["approval_status"], approved["approval_event"]["token"])
        )
        self.assertEqual([], goal_file.changed(body, approved["body"]))

        # Step 4: the completed run's evidence report, which `autocode-issue pr` wraps into the PR body, cites the
        # approved plan by goal hash and the file it was loaded from, and says how the two differ.
        view = driver.until_stopped()
        self.assertEqual("TASK_COMPLETE", view["status"], view["needs"])
        cited = {"path": str(path.resolve()), "goal_hash": goal, "revision": revision, "changed": []}
        self.assertEqual(cited, view["approved_contract"]["plan_file"])
        approved_goal = plan_file.goal_hash(approved["body"])
        self.assertEqual(approved_goal, view["approved_contract"]["goal_hash"])
        report = (driver.run_dir / "evidence.md").read_text()
        self.assertIn(f"\nPlan goal hash: {approved_goal} (what `autocode plan --check FILE` prints", report)
        self.assertIn(
            f"\nPlan file: `{path.resolve()}` (goal hash {goal}), loaded as plan revision {revision}; the approved "
            "plan differs from that file's plan only in the runner's records of the plan's review.\n",
            report,
        )


if __name__ == "__main__":
    unittest.main()
