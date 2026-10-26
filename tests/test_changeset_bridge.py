"""The channel bridge relays a change set's plans and takes its named set approval (#2066, step 7).

Drives the real, offline CLI: two linked fixture runs wait for plan approval, and the bridge worker
reads `autocode changeset status --json`, relays the plans, and takes `approve set` only with the set
token the issue was shown.
"""

import json
import os
import unittest
from pathlib import Path

import autocode_bridge_relay as relay
import autocode_bridge_worker as worker
import autocode_taskrun as taskrun

from . import test_changeset_cli as base
from .test_changeset_cli import MEMBER_ONLY, register_project


class ChangesetBridgeTests(base.ChangesetCliTests):
    def test_the_bridge_approves_the_set_only_with_the_token_it_relayed(self):
        register_project(self.home, self.member)
        linked = self.link()
        self.assertEqual(0, linked.returncode, linked.stdout + linked.stderr)
        env = {**self.env, "AUTOCODE_FIXTURE_IGNORED_STATEMENTS": MEMBER_ONLY}
        env.pop("AUTOCODE_FIXTURE_EXTERNAL_PART")
        member_dir = Path(json.loads(linked.stdout)["runs"][1]["run_dir"])
        member = taskrun.TaskRun(self.member.resolve(), member_dir, env=dict(env))
        member.advance_until_input()
        self.run.answer("workspace-scope-1", "This repository only")
        self.assertEqual("AWAITING_GOAL_APPROVAL", self.run.advance_until_input()["status"])
        source = taskrun.TaskRun(self.run.workspace, self.run.run_dir, env={**os.environ, **env})

        view = source.status()
        summary = worker.changeset_summary(source, view)
        relayed = relay.relay(view, {"gate": {}}, summary)
        self.assertIn("Reply `approve set`", relayed.messages[1])
        self.assertIn("**greet-history's plan**", relayed.messages[1])
        stale = relay.interpret({"gate": {**relayed.gate, "set_token": "set-old"}}, view, "approve set")
        with self.assertRaisesRegex(worker.BridgeError, "matches no change set shown here"):
            worker.take(source, view, stale)
        self.assertIsNone(source.status().get("approved_contract"), "a stale set token approves nothing")

        action = relay.interpret({"gate": relayed.gate}, view, "approve set")
        self.assertIn("Every plan of the change set is approved", worker.take(source, view, action))
        for run in (source, member):
            self.assertIsNotNone(run.status().get("approved_contract"))

    test_a_linked_run_starts_in_the_registered_project_with_the_ready_brief = None
    test_only_a_registered_project_and_a_found_target_can_be_linked = None
    test_one_set_token_approves_every_shown_plan_and_a_revision_withdraws_it = None


if __name__ == "__main__":
    unittest.main()
