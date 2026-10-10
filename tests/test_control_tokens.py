"""Controller tokens stay private without changing explicit approval selection."""

import argparse
import contextlib
import io
import unittest

import autocode_agent_env as agent_env
import autocode_control_tokens as tokens


class ControlTokenTests(unittest.TestCase):
    def test_all_nine_transport_and_resolve_without_mutating_the_command(self):
        cases = [
            ([], tokens.TOKEN_ENV_VARS),
            (["checkpoint"], tokens.CHECKPOINT_TOKEN_ENV_VARS),
            (["program", "approve"], tokens.PROGRAM_TOKEN_ENV_VARS),
        ]
        for prefix, fields in cases:
            for attribute, variable in fields.items():
                flag = "--" + attribute.replace("_", "-")
                for option in ([flag, "exact-token"], [flag + "=exact-token"]):
                    with self.subTest(flag=flag, option=option):
                        original = ["python", "autocode.py", *prefix, *option]
                        safe, private = tokens.private_command(original, argument_offset=2)
                        self.assertNotIn("exact-token", " ".join(safe))
                        self.assertIn("exact-token", " ".join(original))
                        self.assertEqual({variable: "exact-token"}, private)
                        args = argparse.Namespace(**{attribute: "-"})
                        tokens.resolve_placeholders(args, argparse.ArgumentParser(), private, fields=fields)
                        self.assertEqual("exact-token", getattr(args, attribute))

    def test_all_nine_are_withheld_even_when_explicitly_passed(self):
        names = {
            "AUTOCODE_RESOLVER_TOKEN",
            "AUTOCODE_JOB_RETRY_TOKEN",
            "AUTOCODE_RECOVER_JOB_REPORT",
            "AUTOCODE_APPROVE_GOAL_TOKEN",
            "AUTOCODE_REVIEW_TOKEN",
            "AUTOCODE_EXPECTED_GOAL_TOKEN",
            "AUTOCODE_EXPECTED_RECOVERY_TOKEN",
            "AUTOCODE_CHECKPOINT_EXPECTED_TOKEN",
            "AUTOCODE_PROGRAM_APPROVAL_TOKEN",
        }
        env = {name: "inert-canary" for name in names}
        env.update({"AUTOCODE_PASS_ENV": ",".join(names), "AUTOCODE_CAPTURE_CONTEXT": "{}", "PATH": "/bin"})
        original = dict(env)
        self.assertEqual(sorted(names), agent_env.withheld(env))
        self.assertEqual(
            {key: env[key] for key in ("AUTOCODE_PASS_ENV", "AUTOCODE_CAPTURE_CONTEXT", "PATH")},
            agent_env.scrubbed(env),
        )
        self.assertEqual(original, env)
        self.assertEqual({}, agent_env.scrubbed({name.lower(): "inert-canary" for name in names}))

    def test_ambient_variables_never_select_an_action_or_replace_a_literal(self):
        args = argparse.Namespace(approve_goal=None, review_token="literal")
        tokens.resolve_placeholders(
            args,
            argparse.ArgumentParser(),
            {"AUTOCODE_APPROVE_GOAL_TOKEN": "ambient", "AUTOCODE_REVIEW_TOKEN": "ambient"},
        )
        self.assertIsNone(args.approve_goal)
        self.assertEqual("literal", args.review_token)

    def test_missing_or_empty_selector_is_refused(self):
        for value in (None, ""):
            with self.subTest(value=value), contextlib.redirect_stderr(io.StringIO()):
                env = {} if value is None else {"AUTOCODE_APPROVE_GOAL_TOKEN": value}
                with self.assertRaises(SystemExit):
                    tokens.resolve_placeholders(argparse.Namespace(approve_goal="-"), argparse.ArgumentParser(), env)

    def test_only_actual_subcommands_use_generic_token_options(self):
        for cmd in (
            ["program", "run", "--token", "text"],
            ["--feedback", "program", "--token", "text"],
            ["--feedback", "checkpoint", "--expected-token", "text"],
            ["Document --token text"],
            ["--", "--approve-goal", "task-text"],
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual((cmd, {}), tokens.private_command(cmd))

    def test_duplicate_options_preserve_the_last_selector(self):
        variable = "AUTOCODE_APPROVE_GOAL_TOKEN"
        safe, private = tokens.private_command(["--approve-goal", "first", "--approve-goal=last"])
        self.assertEqual(["--approve-goal", "-", "--approve-goal=-"], safe)
        self.assertEqual({variable: "last"}, private)
        safe, private = tokens.private_command(["--approve-goal=first", "--approve-goal", "-"])
        self.assertEqual(["--approve-goal=-", "--approve-goal", "-"], safe)
        self.assertEqual({}, private)

    def test_malformed_options_remain_for_the_parser_to_refuse(self):
        for cmd in (
            ["--approve-goal"],
            ["--approve-goal", "--status"],
            ["--approve-goal", ""],
            ["--approve-goal", "-invalid"],
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual((cmd, {}), tokens.private_command(cmd))

    def test_internal_abbreviations_fail_before_a_command_can_expose_a_token(self):
        for cmd in (
            ["--approve-goa", "private-value"],
            ["--expected-recovery-t=private-value"],
            ["checkpoint", "--expected-t", "private-value"],
            ["program", "approve", "--tok", "private-value"],
        ):
            with self.subTest(cmd=cmd), self.assertRaises(ValueError) as caught:
                tokens.private_command(cmd)
            self.assertNotIn("private-value", str(caught.exception))
