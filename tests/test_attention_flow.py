"""Durable attention through real public commands and a supervised offline provider."""

import json
import os
import subprocess
import sys
import unittest

import autocode_taskrun as taskrun

from tests.test_agent_step import ask_one_question_once, fixture_workspace
from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS


@unittest.skipUnless(sys.platform == "linux", "supervised offline providers require supported Linux")
class AttentionFlow(unittest.TestCase):
    def inventory(self, run):
        result = subprocess.run(
            [*run.command, "runs", "--all", "--json"],
            env=run.env,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        return next(row for row in json.loads(result.stdout)["runs"] if row["run_dir"] == str(run.run_dir))

    def test_detached_question_restores_and_another_viewers_answer_refuses_the_stale_token(self):
        workspace, fixture = fixture_workspace(self)
        ask_one_question_once(self, fixture)
        env = {**os.environ, **fixture, "AUTOCODE_ATTENTION_HOME": str(workspace.parent / "attention")}
        run = taskrun.TaskRun.start(workspace, BRIEF, options=FIXTURE_OPTIONS, env=env, timeout=300)
        view = run.status()
        self.assertEqual("answer", view["needs"]["kind"], (view["status"], view["stop_reason"]))
        self.assertEqual("clarification", view["attention"]["item"]["kind"])
        self.assertEqual(view["attention"], self.inventory(run)["attention"])
        detached = taskrun.TaskRun(workspace, run.run_dir, env=env, timeout=300)
        self.assertEqual(view["attention"], detached.status()["attention"])
        question = view["needs"]["questions"][0]
        token = view["needs"]["resolver_token"]
        resolved = detached.answer(question["id"], question["options"][0], resolver_token=token)
        self.assertIsNone(resolved["attention"]["item"])
        self.assertIsNone(self.inventory(run)["attention"]["item"])
        with self.assertRaises(taskrun.TaskRunError):
            run.answer(question["id"], question["options"][0], resolver_token=token)
        self.assertEqual(
            view["usage"], run.status()["usage"], "stale notification cannot dispatch or duplicate an answer"
        )
        with self.assertRaises(taskrun.TaskRunError):
            run.acknowledge_attention(view)
        self.assertFalse((workspace / "greet.py").exists())

    def test_restart_shared_ack_stale_plan_and_successive_completion(self):
        workspace, fixture = fixture_workspace(self)
        env = {**os.environ, **fixture, "AUTOCODE_ATTENTION_HOME": str(workspace.parent / "attention")}
        run = taskrun.TaskRun.start(workspace, BRIEF, options=FIXTURE_OPTIONS, env=env, timeout=300)
        view = run.status()
        self.assertEqual("approve_plan", view["needs"]["kind"], (view["status"], view["stop_reason"]))
        old_plan = view["needs"]["token"]
        self.assertEqual("unread", view["attention"]["acknowledgement"]["state"])
        before = (run.run_dir / "state.json").read_bytes()
        advance = run.last_advance
        first = run.acknowledge_attention(view)
        self.assertEqual(before, (run.run_dir / "state.json").read_bytes(), "ack cannot change the task checkpoint")
        self.assertIs(advance, run.last_advance)

        viewer = taskrun.TaskRun(workspace, run.run_dir, env=env, timeout=300)
        restored = viewer.status()
        self.assertEqual(restored["attention"], self.inventory(run)["attention"])
        self.assertEqual(view["attention"]["item"]["id"], restored["attention"]["item"]["id"])
        self.assertEqual("acknowledged", restored["attention"]["acknowledgement"]["state"])
        # Simulate losing the first response: the same named acknowledgement returns its original receipt.
        repeated = viewer.acknowledge_attention(view)
        self.assertTrue(repeated["reconciled"])
        self.assertEqual(first["receipt"], repeated["receipt"])
        self.assertEqual(view["usage"], restored["usage"])
        self.assertEqual(view["needs"], restored["needs"], "read acknowledgement cannot approve a plan")

        run.approve_plan(old_plan)
        complete = run.advance_until_input()
        self.assertTrue(complete["done"], (complete["status"], complete["stop_reason"]))
        self.assertEqual("completion", complete["attention"]["item"]["kind"])
        run.acknowledge_attention(complete)
        original_outcome = complete["attention"]["item"]["id"]
        run.follow_up(BRIEF)
        revised = run.advance_until_input()
        self.assertEqual("approve_plan", revised["needs"]["kind"], (revised["status"], revised["stop_reason"]))
        self.assertNotEqual(old_plan, revised["needs"]["token"])
        self.assertNotEqual(view["attention"]["item"]["id"], revised["attention"]["item"]["id"])
        self.assertEqual("unread", revised["attention"]["acknowledgement"]["state"])
        with self.assertRaises(taskrun.TaskRunError):
            viewer.approve_plan(old_plan)
        self.assertEqual(revised["needs"], run.status()["needs"])
        # Acknowledgement retry remains safe even after the decision was superseded.
        self.assertEqual(first["receipt"], viewer.acknowledge_attention(view)["receipt"])

        run.approve_plan(revised["needs"]["token"])
        next_outcome = run.advance_until_input()
        self.assertTrue(next_outcome["done"], (next_outcome["status"], next_outcome["stop_reason"]))
        self.assertNotEqual(original_outcome, next_outcome["attention"]["item"]["id"])
        self.assertEqual("unread", next_outcome["attention"]["acknowledgement"]["state"])
