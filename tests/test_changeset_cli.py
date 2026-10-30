"""`autocode changeset link` starts a linked run in another registered project (#2066, step 4).

Drives the real, offline CLI: the source run through the task-run interface, the link through
the `autocode changeset` command, and both runs' status views.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_changeset as changeset
import autocode_taskrun as taskrun

from .test_agent_step import fixture_workspace
from .test_scope_question_cli import ELSEWHERE, PART
from .test_taskrun import BRIEF, FIXTURE_OPTIONS

ROOT = Path(__file__).resolve().parents[1]
# The linked brief's own instruction, which the greeting fixture's report does not quote.
MEMBER_ONLY = "This run owns only greet-history's side."
OPTIONS = (*FIXTURE_OPTIONS, "--joint-planning", "--no-adaptive-planning")


def register_project(home: Path, workspace: Path) -> None:
    """Onboard the fresh checkout through the same public command a person uses."""
    subprocess.run(
        [sys.executable, str(ROOT / "tools/autocode.py"), "registry", "add", str(workspace), "--json"],
        env={**os.environ, "AUTOCODE_HOME": str(home)},
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )


class ChangesetCliTests(unittest.TestCase):
    def setUp(self):
        self.source, env = fixture_workspace(self)
        self.member, _ = fixture_workspace(self)
        self.home = Path(env["AUTOCODE_HOME"])
        self.env = {**env, "AUTOCODE_FIXTURE_EXTERNAL_PART": PART}
        self.run = taskrun.TaskRun.start(
            self.source, f"{BRIEF} {ELSEWHERE}", options=OPTIONS, env=self.env, timeout=180
        )
        view = self.run.advance_until_input()
        self.scope_question = view["needs"]["questions"][0]["id"]
        self.assertTrue(self.scope_question.startswith("workspace-scope-"), view["stop_reason"])

    def changeset(self, *args):
        env = {**os.environ, **self.env}
        env.pop("AUTOCODE_FIXTURE_EXTERNAL_PART")  # the linked run's own request spans nothing
        env["AUTOCODE_FIXTURE_IGNORED_STATEMENTS"] = MEMBER_ONLY
        return subprocess.run(
            [sys.executable, str(ROOT / "tools" / "autocode.py"), "changeset", *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )

    def link(self, target="greet-history", workspace=None):
        return self.changeset(
            "link",
            "--run-dir",
            str(self.run.run_dir),
            "--target",
            target,
            "--workspace",
            str(workspace or self.member),
            "--json",
            "--",
            *OPTIONS,
        )

    def test_a_linked_run_starts_in_the_registered_project_with_the_ready_brief(self):
        register_project(self.home, self.member)
        brief = next(
            row["brief"] for row in self.run.status()["scope"]["briefs"] if row["repository"] == "greet-history"
        )
        proc = self.link()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        summary = json.loads(proc.stdout)
        source, member = summary["runs"]
        self.assertEqual(("source", str(self.run.run_dir.resolve())), (source["role"], source["run_dir"]))
        self.assertEqual(("member", "greet-history"), (member["role"], member["target"]))
        self.assertEqual(str(self.member.resolve()), member["workspace"])
        [member_run] = taskrun.TaskRun.runs_in(self.member)
        self.assertEqual(str(member_run.resolve()), member["run_dir"])
        self.assertNotIn(member["status"], ("STARTING", "UNREADABLE"), member)

        linked = taskrun.TaskRun(self.member.resolve(), member_run, env=self.env)
        self.assertEqual(brief, json.loads((member_run / "state.json").read_text())["task"])
        [back] = linked.status()["scope"]["linked"]
        self.assertEqual(("source", str(self.run.run_dir.resolve())), (back["role"], back["run_dir"]))
        [there] = self.run.status()["scope"]["linked"]
        self.assertEqual(
            (summary["changeset"], "greet-history", member["run_dir"]),
            (there["changeset"], there["target"], there["run_dir"]),
        )

        again = self.link()
        self.assertEqual(1, again.returncode)
        self.assertIn("already linked", json.loads(again.stdout)["error"])
        self.assertEqual(1, len(taskrun.TaskRun.runs_in(self.member)), "a refused link starts no run")
        shown = self.changeset("status", "--run-dir", member["run_dir"], "--json")
        self.assertEqual(summary["changeset"], json.loads(shown.stdout)["changeset"], shown.stderr)

    def test_only_a_registered_project_and_a_found_target_can_be_linked(self):
        refused = self.link()
        self.assertEqual(1, refused.returncode)
        self.assertIn("is not a registered project", json.loads(refused.stdout)["error"])
        register_project(self.home, self.member)
        refused = self.link(target="billing")
        self.assertEqual(1, refused.returncode)
        self.assertIn("is not work this run found in another repository", json.loads(refused.stdout)["error"])
        self.assertEqual([], taskrun.TaskRun.runs_in(self.member), "nothing started for a refused link")
        listed = self.changeset("list", "--json")
        self.assertEqual({"changesets": []}, json.loads(listed.stdout))

    def test_one_set_token_approves_every_shown_plan_and_a_revision_withdraws_it(self):
        register_project(self.home, self.member)
        linked = self.link()
        self.assertEqual(0, linked.returncode, linked.stdout + linked.stderr)
        member_dir = Path(json.loads(linked.stdout)["runs"][1]["run_dir"])
        member = taskrun.TaskRun(
            self.member.resolve(), member_dir, env={**self.env, "AUTOCODE_FIXTURE_IGNORED_STATEMENTS": MEMBER_ONLY}
        )
        member.env.pop("AUTOCODE_FIXTURE_EXTERNAL_PART")
        member.advance_until_input()
        shown = json.loads(self.changeset("status", "--run-dir", str(self.run.run_dir), "--json").stdout)
        self.assertIsNone(shown["approval"]["set_token"], "the source run has not planned yet")
        self.assertEqual([Path(self.source).name], shown["approval"]["waiting_on"], shown["runs"][1])

        self.run.answer(self.scope_question, "This repository only")
        self.assertEqual("AWAITING_GOAL_APPROVAL", self.run.advance_until_input()["status"])
        shown = json.loads(self.changeset("status", "--run-dir", str(self.run.run_dir), "--json").stdout)
        token = shown["approval"]["set_token"]
        self.assertTrue(token, shown)
        self.assertEqual(token, shown["approval"]["next_command"][-1])
        self.assertTrue(all(row["plan"]["acceptance_criteria"] for row in shown["runs"]), "each plan is shown")

        member.feedback("Also log the greeting time.")
        self.assertEqual("AWAITING_GOAL_APPROVAL", member.advance_until_input()["status"])
        stale = self.changeset("approve", "--run-dir", str(self.run.run_dir), "--set-token", token, "--json")
        self.assertEqual(1, stale.returncode)
        self.assertIn("changed since it was shown", json.loads(stale.stdout)["error"])
        self.assertIsNone(self.run.status().get("approved_contract"), "a stale set token approves nothing")

        fresh = json.loads(self.changeset("status", "--run-dir", str(member_dir), "--json").stdout)
        self.env[changeset.SET_TOKEN_ENV] = fresh["approval"]["set_token"]
        self.assertNotEqual(token, self.env[changeset.SET_TOKEN_ENV])
        done = self.changeset("approve", "--run-dir", str(member_dir), "--set-token", "-", "--json")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        for run in (self.run, member):
            approved = run.status().get("approved_contract")
            self.assertIsNotNone(approved)
        self.assertIsNone(json.loads(done.stdout)["approval"]["set_token"])

    def test_partial_set_approval_blocks_dispatch_and_can_retry_or_release_one_run(self):
        register_project(self.home, self.member)
        linked = self.link()
        self.assertEqual(0, linked.returncode, linked.stdout + linked.stderr)
        member_dir = Path(json.loads(linked.stdout)["runs"][1]["run_dir"])
        environment = {**self.env, "AUTOCODE_FIXTURE_IGNORED_STATEMENTS": MEMBER_ONLY}
        environment.pop("AUTOCODE_FIXTURE_EXTERNAL_PART")
        member = taskrun.TaskRun(self.member.resolve(), member_dir, env=environment)
        member.advance_until_input()
        self.run.answer(self.scope_question, "This repository only")
        self.assertEqual("AWAITING_GOAL_APPROVAL", self.run.advance_until_input()["status"])
        shown = json.loads(self.changeset("status", "--run-dir", str(self.run.run_dir), "--json").stdout)
        token = shown["approval"]["set_token"]
        approve_plan = taskrun.TaskRun.approve_plan

        def fail_second(run, plan_token, **kwargs):
            if run.run_dir == member_dir:
                raise taskrun.TaskRunError("injected lost member approval")
            return approve_plan(run, plan_token, **kwargs)

        with patch.dict(os.environ, environment), patch.object(taskrun.TaskRun, "approve_plan", fail_second):
            os.environ.pop("AUTOCODE_FIXTURE_EXTERNAL_PART", None)
            with self.assertRaisesRegex(changeset.LinkRefused, "after approving"):
                changeset.approve(self.run.run_dir, token)
        before = self.run.status()
        self.assertEqual("approve_plan", before["needs"]["kind"])
        self.assertTrue(before["needs"]["changeset_held"])
        held = self.run.advance()  # even a direct resume cannot dispatch the partially approved Builder
        self.assertEqual("PAUSED_CHANGESET_APPROVAL", held["status"], held.get("stop_reason"))
        self.assertIsNone(held["interaction_timing"]["first_builder_at"])
        self.assertEqual(before["usage"]["accounting"]["attempts"], held["usage"]["accounting"]["attempts"])
        fresh = json.loads(self.changeset("status", "--run-dir", str(member_dir), "--json").stdout)
        self.assertTrue(fresh["approval"]["set_token"], fresh)
        retry = self.changeset(
            "approve", "--run-dir", str(member_dir), "--set-token", fresh["approval"]["set_token"], "--json"
        )
        self.assertEqual(0, retry.returncode, retry.stdout + retry.stderr)
        self.assertEqual("continue", self.run.status()["needs"]["kind"])
        self.assertEqual("continue", member.status()["needs"]["kind"])
        queued = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools" / "autocode.py"),
                "intervention",
                "submit",
                "--workspace",
                str(member.workspace),
                "--run-dir",
                str(member.run_dir),
                "--request-id",
                "member-revision",
                "--kind",
                "feedback",
                "--text",
                "Also log the greeting time.",
                "--json",
            ],
            env={**os.environ, **environment},
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, queued.returncode, queued.stdout + queued.stderr)
        revised = member.advance()
        self.assertIsNone(revised["interaction_timing"]["first_builder_at"])
        source = self.run.status()
        self.assertTrue(source["needs"]["changeset_held"], "a member revision withdraws the shared release")
        self.run.approve_plan(source["needs"]["token"])
        self.assertEqual(
            "continue", self.run.status()["needs"]["kind"], "explicit standalone approval releases this run"
        )
        self.assertIsNone(member.status().get("approved_contract"))


class StatusRefusalTests(unittest.TestCase):
    def test_a_missing_run_directory_is_named_not_called_a_run_in_no_change_set(self):
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "runs" / "missing"
            with self.assertRaisesRegex(changeset.LinkRefused, f"No such run directory: {missing}"):
                changeset.status(missing)


class SetTokenTests(unittest.TestCase):
    def test_the_set_token_needs_every_plan_and_follows_each_one(self):
        runs = [{"run_dir": "/a", "plan_token": "t1"}, {"run_dir": "/b", "plan_token": "t2"}]
        token = changeset.set_token(runs)
        self.assertEqual(token, changeset.set_token(list(reversed(runs))))
        self.assertNotEqual(token, changeset.set_token([runs[0], {"run_dir": "/b", "plan_token": "t3"}]))
        self.assertIsNone(changeset.set_token([runs[0], {"run_dir": "/b", "plan_token": None}]))
        self.assertIsNone(changeset.set_token([]))

    def test_completed_run_reuses_only_its_authenticated_displayed_plan(self):
        plan = {
            "token": "r1:abc",
            "acceptance_criteria": [
                {"id": "fix", "criterion": "Fix the result", "verification_method": "unit tests", "human_review": False}
            ],
            "constraints": [],
            "permission_boundaries": [],
            "scope_exclusions": [],
        }
        view = {
            "done": True,
            "needs": {"kind": "continue"},
            "displayed_plan": plan,
            "approved_contract": {
                "token": "r1:abc",
                "body": {
                    "acceptance_criteria": plan["acceptance_criteria"],
                    "constraints": [],
                    "permission_boundaries": [],
                    "scope_exclusions": [],
                },
            },
        }
        self.assertEqual({"plan": plan, "already_approved": True}, changeset.plan_for_set(view))
        view["approved_contract"]["token"] = "r1:old"
        self.assertIsNone(changeset.plan_for_set(view))

    def test_revised_aggregate_approval_does_not_reapprove_an_unchanged_completed_member(self):
        runs = [
            {
                "role": "source",
                "target": "api",
                "workspace": "/api",
                "run_dir": "/api/run",
                "plan_token": "old-plan",
                "already_approved": True,
            },
            {
                "role": "joint",
                "target": "Joint regression",
                "workspace": "/joint",
                "run_dir": "/joint/run",
                "plan_token": "new-plan",
                "already_approved": False,
            },
        ]
        summary = {"runs": runs, "approval": {"set_token": "new-set"}}
        run = unittest.mock.Mock()
        with (
            patch.object(changeset, "status", return_value=summary),
            patch.object(changeset.store, "begin_approval"),
            patch.object(changeset.store, "release_approval"),
            patch.object(changeset.taskrun, "TaskRun", return_value=run) as task_run,
        ):
            changeset.approve("/api/run", "new-set")
        task_run.assert_called_once()
        run.approve_plan.assert_called_once_with("new-plan", changeset_token="new-set")


if __name__ == "__main__":
    unittest.main()
