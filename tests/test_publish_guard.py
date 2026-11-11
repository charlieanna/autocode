"""No stage changes GitHub or pushes, on any engine (autocode_publish_guard).

The commands are the ones a live Requirements stage ran on AutoCode's own repository (run
20261111-235533-vscode-ddd0e8, Kilo): it followed the repository's rules for agents taking an issue, labeled
#3118 and commented on it before any plan existed.
"""

import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_publish_guard as guard
import autocode_unit_request as unit_request
from providers import claude
from providers import kilocode_policy as kilo
from providers import opencode as oc
from units.model_request import ModelRequest

LABEL = 'gh issue edit 3118 --repo charlieanna/autocode --add-label "in progress"'
COMMENT = (
    'gh issue comment 3118 --repo charlieanna/autocode --body "Taking this. Working on it: AutoCode agent session"'
)
READS = ("gh issue view 3118 --repo charlieanna/autocode", "gh issue list --state open --limit 50", "git status")


def native_action(bash, command):
    """OpenCode's and Kilo's bash decision: the last pattern matching the whole command wins."""
    found = None
    for pattern, action in bash.items() if isinstance(bash, dict) else [("*", bash)]:
        if re.fullmatch(re.escape(pattern).replace(r"\*", ".*"), command, re.DOTALL):
            found = action
    return found


def claude_denies(rules, command):
    """Claude Code's prefix rule ``Bash(prefix:*)``: the command starts with the prefix."""
    return any(
        rule.startswith("Bash(") and rule.endswith(":*)") and (command + " ").startswith(rule[5:-3] + " ")
        for rule in rules
    )


class PublishGuardTests(unittest.TestCase):
    def test_kilo_stage_shell_denies_the_label_and_comment_the_run_posted(self):
        rules = [{"permission": "*", "pattern": "*", "action": "allow"}]

        def debug(argv, **_kwargs):
            answer = {"default_agent": "code"} if argv[2:] == ["config"] else {"permission": rules}
            return subprocess.CompletedProcess(argv, 0, json.dumps(answer), "")

        child = {"PATH": os.environ["PATH"], "KILO_CONFIG_CONTENT": "{}"}
        argv = [sys.executable, "run", "--model", "xiaomi-token-plan-sgp/mimo-v2.6-pro"]
        for options in ({"portable_shell": True}, {"write_stage": True}):
            with self.subTest(**options), patch.object(kilo.subprocess, "run", side_effect=debug):
                _, env = kilo.restrict(argv, dict(child), Path.cwd(), role="requirements", **options)
            (agent,) = json.loads(env["KILO_CONFIG_CONTENT"])["agent"].values()
            bash = agent["permission"]["bash"]
            self.assertEqual("deny", native_action(bash, LABEL))
            self.assertEqual("deny", native_action(bash, COMMENT))
            self.assertEqual("deny", native_action(bash, "git push origin HEAD"))
            for command in READS:
                self.assertEqual("allow", native_action(bash, command), command)

    def test_claude_stage_disallows_the_same_commands(self):
        argv = claude.DENY_GIT + claude.DENY_PUBLISH
        self.assertTrue(claude_denies(argv, LABEL))
        self.assertTrue(claude_denies(argv, COMMENT))
        self.assertFalse(any(claude_denies(argv, command) for command in READS))
        self.assertEqual(len(argv), len(set(argv)))
        self.assertEqual(claude.DENY_PUBLISH, claude.POLICY["deny_publish"])

    def test_opencode_agent_shell_denies_them_and_keeps_the_operators_rules(self):
        for policy, other in (
            (None, None),  # no rule of its own: every other command falls through to the agent's wider rule
            ({"bash": "ask"}, "ask"),
            ({"bash": {"*": "allow", "rm *": "deny"}}, "allow"),
        ):
            config = {"agent": {"autocode_tester": {"permission": policy}}} if policy else {}
            with self.subTest(policy=policy), patch.dict(os.environ, {"OPENCODE_CONFIG_CONTENT": json.dumps(config)}):
                _, env, _ = oc.launch("tester", Path("/workspace"), Path("/run"), None, "test/model", None, False)
                bash = json.loads(env["OPENCODE_CONFIG_CONTENT"])["agent"]["autocode_tester"]["permission"]["bash"]
                self.assertEqual("deny", native_action(bash, LABEL))
                self.assertEqual("deny", native_action(bash, COMMENT))
                self.assertEqual(other, native_action(bash, "ls tools"))
                if policy and isinstance(policy["bash"], dict):
                    self.assertEqual("deny", native_action(bash, "rm -rf build"))

    def test_the_denial_never_allows_more_than_the_shell_already_had(self):
        for existing in ("ask", "deny", {"*": "deny"}, {"*": "allow", "git push*": "ask"}, None):
            with self.subTest(existing=existing):
                bash = guard.bash_permission(existing)
                for command in (*READS, "make test", "git push --dry-run origin"):
                    before = native_action(existing, command) if existing is not None else None
                    after = native_action(bash, command)
                    self.assertTrue(after == before or after == "deny", (command, before, after))
                self.assertEqual("deny", native_action(bash, LABEL))
        self.assertEqual({"*": "deny"}, guard.bash_permission("deny"))
        with self.assertRaises(ValueError):
            guard.bash_permission(["allow"])

    def test_every_stage_prompt_says_it_publishes_nothing_before_its_handoff(self):
        request = ModelRequest(
            "planner", "planner", 'Gather requirements.\nCURRENT HANDOFF DATA\n{"task": 1}', {}, {}, False
        )

        class Unit:
            @staticmethod
            def prepare(*_args, **_kwargs):
                return request

        state = {"status": "RUNNING"}
        with patch.object(unit_request.stuck, "with_guidance", side_effect=lambda _s, _t, value: value):
            prompt = unit_request.prepare(state, "requirements_gather", None, None, select_unit=lambda _: Unit).prompt
        before, marker, data = prompt.partition(guard.MARKER)
        self.assertTrue(marker)
        self.assertIn("Do not label, comment on", before)
        self.assertIn("not for this stage", before)
        self.assertEqual('{"task": 1}', data)
        self.assertEqual(prompt, guard.with_rule(prompt))
        self.assertTrue(guard.with_rule("No handoff.").endswith(guard.RULE))


if __name__ == "__main__":
    unittest.main()
