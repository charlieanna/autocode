"""The private authorization wire contract, independent of runner state."""

import argparse
import io
import json
import os
import unittest
from contextlib import redirect_stderr

from autocode_authorization_transport import (
    FLAG,
    OPTIONS,
    SELECTOR,
    bind,
    check_command_prefix,
    decode,
    guidance,
    input_stream,
    invocation,
    prepare,
    replay_hint,
)


def envelope(tokens):
    return json.dumps({"schema": 1, "tokens": tokens}).encode()


class AuthorizationTransportTests(unittest.TestCase):
    def test_every_token_option_keeps_the_exact_value_out_of_command_arguments(self):
        for option, field in OPTIONS.items():
            with self.subTest(option=option):
                context = (
                    "checkpoint" if field == "expected_token" else "program_approve" if field == "token" else "task"
                )
                command = ["checkpoint", "--restore", "checkpoint-1"] if context == "checkpoint" else []
                token = "exact:" + field + ":α"
                safe, data = prepare([*command, option, token])
                self.assertNotIn(token, safe)
                with invocation(safe, context=context, stream=io.BytesIO(data)) as argv:
                    parser = argparse.ArgumentParser()
                    parser.add_argument(option)
                    args = parser.parse_args(argv[len(command) :])
                    bind(args, parser)
                    self.assertEqual(getattr(args, field), token)

    def test_one_recovery_envelope_preserves_both_required_tokens(self):
        argv, data = prepare(
            ["--resume-paused", "--expected-recovery-token", "pause:one", "--job-retry-token=retry:two"]
        )
        self.assertEqual(decode(data), {"expected_recovery_token": "pause:one", "job_retry_token": "retry:two"})
        self.assertNotIn("pause:one", " ".join(argv))
        self.assertNotIn("retry:two", " ".join(argv))

    def test_duplicate_unknown_and_malformed_envelopes_fail_without_echoing_values(self):
        secret = "DO_NOT_ECHO_THIS_TOKEN"
        invalid = [
            b'{"schema":1,"schema":1,"tokens":{"approve_goal":"' + secret.encode() + b'"}}',
            b'{"schema":1,"tokens":{"approve_goal":"one","approve_goal":"two"}}',
            envelope({"unknown": secret}),
            envelope({"approve_goal": ""}),
            envelope({"approve_goal": "x" * 4097}),
            json.dumps({"schema": True, "tokens": {"approve_goal": secret}}).encode(),
            envelope({"approve_goal": secret}) + b"{}",
            b"\xff",
            b'{"schema":1,"tokens":{"approve_goal":"\\ud800"}}',
            b"[" * 2000 + b"0" + b"]" * 2000,
        ]
        for data in invalid:
            with self.subTest(data_length=len(data)):
                with self.assertRaises(ValueError) as caught:
                    decode(data)
                self.assertNotIn(secret, str(caught.exception))

    def test_selectors_must_match_payload_and_command_before_dispatch(self):
        cases = [
            (["--approve-goal", SELECTOR, FLAG], {"review_token": "review"}, "task"),
            (["--approve-goal", "literal", FLAG], {"approve_goal": "literal"}, "task"),
            (["--approve-goal", SELECTOR, "--approve-goal", SELECTOR, FLAG], {"approve_goal": "goal"}, "task"),
            (["program", "run", FLAG, "--token", SELECTOR], {"token": "agreement"}, "task"),
            (
                ["checkpoint", "--compare", "C1", "--expected-token", SELECTOR, FLAG],
                {"expected_token": "checkpoint"},
                "task",
            ),
        ]
        for argv, tokens, context in cases:
            with self.subTest(argv=argv):
                with self.assertRaises(ValueError):
                    with invocation(argv, context=context, stream=io.BytesIO(envelope(tokens))):
                        self.fail("invalid input must not reach dispatch")

    def test_private_option_and_payload_are_not_reinterpreted_as_task_text(self):
        argv = ["--approve-goal", "goal:one", "--", FLAG, "--review-token", "ordinary task words"]
        safe, data = prepare(argv)
        self.assertEqual(safe[safe.index("--") + 1 :], argv[argv.index("--") + 1 :])
        self.assertLess(safe.index(FLAG), safe.index("--"))
        self.assertEqual(decode(data), {"approve_goal": "goal:one"})
        with invocation(["--", FLAG], stream=io.BytesIO(b"not JSON")) as literal:
            self.assertEqual(literal, ["--", FLAG])

    def test_nested_dispatch_reuses_input_and_later_invocation_cannot_reuse_it(self):
        data = envelope({"token": "agreement:one"})
        outer = ["program", "approve", "--token", SELECTOR, FLAG]
        with invocation(outer, stream=io.BytesIO(data)) as safe:
            with invocation(safe[1:], context="program", stream=io.BytesIO(b"must not be read")):
                args = argparse.Namespace(token=SELECTOR)
                bind(args, argparse.ArgumentParser())
                self.assertEqual(args.token, "agreement:one")
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            bind(argparse.Namespace(token=SELECTOR), argparse.ArgumentParser())

    def test_internal_transport_refuses_duplicate_selectors_or_a_secret_fallback(self):
        for command in (
            ["--approve-goal", "one", "--approve-goal", "two"],
            ["--approve-goal", SELECTOR],
            ["--approve-g", "one"],
            [FLAG],
        ):
            with self.subTest(command=command), self.assertRaises(ValueError):
                prepare(command)

    def test_anonymous_input_is_non_inheritable_except_when_explicitly_given_as_stdin(self):
        with input_stream(envelope({"approve_goal": "goal:one"})) as stream:
            self.assertFalse(os.get_inheritable(stream.fileno()))
            self.assertEqual(os.fstat(stream.fileno()).st_mode & 0o777, 0o600)
            self.assertEqual(decode(stream.read()), {"approve_goal": "goal:one"})

    def test_command_wrappers_allow_a_delimiter_but_refuse_authorization_abbreviations(self):
        check_command_prefix(["env", "--", "python", "autocode.py"])
        for option in [*OPTIONS, FLAG, "--approve-g", "--authorization-s", "--t"]:
            with self.subTest(option=option), self.assertRaises(ValueError):
                check_command_prefix(["python", "autocode.py", option, "DO_NOT_ECHO"])

    def test_guidance_keeps_exact_tokens_in_a_separate_document(self):
        shown = guidance(["autocode", "--run-dir", "/path with spaces/run", "--approve-goal", "exact-token"])
        command, label, payload = shown.splitlines()
        self.assertNotIn("exact-token", command)
        self.assertIn("--approve-goal @stdin --authorization-stdin <", command)
        self.assertIn("save separately", label)
        self.assertEqual({"approve_goal": "exact-token"}, decode(payload.encode()))

    def test_run_selection_hint_preserves_private_admission_without_bound_values(self):
        safe, data = prepare(["--approve-goal", "exact-token"])
        with invocation(safe, stream=io.BytesIO(data)) as flags:
            hint = replay_hint(flags)
            self.assertNotIn("exact-token", hint)
            self.assertIn("--approve-goal @stdin --authorization-stdin <", hint)
        self.assertEqual("--status", replay_hint(["--status"]))


if __name__ == "__main__":
    unittest.main()
