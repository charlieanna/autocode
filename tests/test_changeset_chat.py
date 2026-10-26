"""The terminal chat shows a change set and takes only the actions `changeset status` names (#2066, step 7).

Drives the real, offline CLI: two linked fixture runs wait for plan approval, and the chat client's
/changeset reads `autocode changeset status --json`, approves the set the person was shown, and later
runs the set's integration check. `runs --all --json` and /runs group the two chats.
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

import autocode_chat_changeset as chat_changeset
import autocode_chat_client as chat
import autocode_chat_panel as panel
import autocode_client_actions as actions
import autocode_taskrun as taskrun

from . import test_changeset_cli as base
from .test_changeset_cli import MEMBER_ONLY, ROOT, register_project

AUTOCODE = (sys.executable, str(ROOT / "tools" / "autocode.py"))


class ChangesetChatTests(base.ChangesetCliTests):
    def client(self, said):
        env = {**os.environ, **self.env, "AUTOCODE_FIXTURE_IGNORED_STATEMENTS": MEMBER_ONLY}
        env.pop("AUTOCODE_FIXTURE_EXTERNAL_PART")
        client = chat.ChatClient(self.source, self.run.run_dir, command=AUTOCODE, env=env, say=said.append)
        self.addCleanup(client.close)
        return client

    def test_the_chat_approves_the_shown_set_and_runs_its_check(self):
        register_project(self.home, self.member)
        linked = self.link()
        self.assertEqual(0, linked.returncode, linked.stdout + linked.stderr)
        summary = json.loads(linked.stdout)
        member_dir = Path(summary["runs"][1]["run_dir"])
        member = taskrun.TaskRun(
            self.member.resolve(), member_dir, env={**self.env, "AUTOCODE_FIXTURE_IGNORED_STATEMENTS": MEMBER_ONLY}
        )
        member.env.pop("AUTOCODE_FIXTURE_EXTERNAL_PART")
        member.advance_until_input()
        said: list[str] = []
        client = self.client(said)

        waiting = client.changeset()
        self.assertIsInstance(waiting, str, "no set approval before the source run has a plan")
        self.assertIn("A set approval waits for a complete plan from", waiting)
        view = self.run.status()
        sections = {section.title: section for section in panel.sections(view)}
        self.assertEqual("with 1 other run", sections["Change set"].summary)
        self.assertIn("greet-history · linked run", [row.text for row in sections["Change set"].rows])

        self.run.answer(self.scope_question, "This repository only")
        self.assertEqual("AWAITING_GOAL_APPROVAL", self.run.advance_until_input()["status"])
        offered = client.changeset()
        self.assertIsInstance(offered, panel.Confirm)
        self.assertEqual(chat_changeset.APPROVE, offered.question)
        self.assertEqual(2, offered.text.count("Plan to approve:"), "every run's plan is shown before approving")
        with self.assertRaises(chat.SetApproved):
            offered.then()
        for run in (self.run, member):
            self.assertIsNotNone(run.status().get("approved_contract"))
            self.assertEqual("TASK_COMPLETE", run.advance_until_input()["status"])

        check = self.changeset(
            "check-command",
            "--run-dir",
            str(self.run.run_dir),
            "--in",
            "greet-history",
            "--",
            sys.executable,
            "-c",
            "print('ok')",
        )
        self.assertEqual(0, check.returncode, check.stdout + check.stderr)
        offered = client.changeset()
        self.assertEqual(chat_changeset.VERIFY, offered.question)
        self.assertIn("the set's check is due in greet-history", offered.text)
        offered.then()
        done = client.changeset()
        self.assertIn("Checked together: done.", done)

        listed = subprocess.run(
            [*AUTOCODE, "runs", "--all", "--json"], env=client.env, capture_output=True, text=True, timeout=120
        )
        rows = json.loads(listed.stdout)["runs"]
        sets = {row["run_dir"]: row.get("changeset") for row in rows}
        self.assertEqual(summary["changeset"], sets[str(self.run.run_dir.resolve())], listed.stderr)
        self.assertEqual(summary["changeset"], sets[str(member_dir.resolve())])
        shown = panel.runs_text(panel.run_rows(rows))
        self.assertIn(f"Change set {summary['changeset']}: chats 1 and 2 are one request.", shown)

    test_a_linked_run_starts_in_the_registered_project_with_the_ready_brief = None
    test_only_a_registered_project_and_a_found_target_can_be_linked = None
    test_one_set_token_approves_every_shown_plan_and_a_revision_withdraws_it = None
    test_partial_set_approval_blocks_dispatch_and_can_retry_or_release_one_run = None


class ChangesetActionTests(unittest.TestCase):
    SUMMARY = {
        "runs": [{"run_dir": "/w/.autocode/runs/a"}],
        "approval": {
            "set_token": "set-1",
            "next_command": [
                "autocode",
                "changeset",
                "approve",
                "--run-dir",
                "/w/.autocode/runs/a",
                "--set-token",
                "set-1",
            ],
        },
        "verification": {"next_command": None},
    }

    def test_only_the_named_set_action_is_taken(self):
        self.assertEqual(
            ["changeset", "approve", "--run-dir", "/w/.autocode/runs/a", "--set-token", "set-1"],
            actions.changeset_action(self.SUMMARY, "approval"),
        )
        with self.assertRaisesRegex(actions.ClientGap, "no joint check"):
            actions.changeset_action(self.SUMMARY, "verification")
        moved = {**self.SUMMARY, "approval": {**self.SUMMARY["approval"], "set_token": "set-2"}}
        with self.assertRaisesRegex(actions.ClientGap, "Unsupported"):
            actions.changeset_action(moved, "approval")  # the command no longer carries the shown token
        self.assertIsNone(actions.changeset_status({"scope": {"linked": []}}, Path("/w"), Path("/r")))
        self.assertEqual(
            ["changeset", "status", "--run-dir", "/r", "--json"],
            actions.changeset_status({"scope": {"linked": [{"target": "b"}]}}, Path("/w"), Path("/r")),
        )

    def test_runs_group_only_sets_with_two_listed_chats(self):
        rows = [{"changeset": "changeset-1"}, {"changeset": None}, {"changeset": "changeset-1"}, {"changeset": "x"}]
        self.assertEqual(["Change set changeset-1: chats 1 and 3 are one request."], chat_changeset.groups(rows))
        self.assertIsNone(chat_changeset.compact({"scope": {"linked": []}}))
        self.assertIsNone(chat_changeset.approval_hint({}))


if __name__ == "__main__":
    unittest.main()
