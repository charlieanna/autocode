"""Authorization tokens travel by environment, not argv (issue #818).

/proc/<pid>/cmdline is world-readable on Linux; /proc/<pid>/environ is not. The
token options therefore accept the value ``-`` meaning "read the paired
AUTOCODE_* variable", and TaskRun hands its tokens to the child through the
environment with the sentinel in argv. A literal token value keeps working, and
an ambient variable alone never acts as authorization.
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import autocode as runner
import autocode_args
import autocode_taskrun as taskrun

PARSER = autocode_args.build_parser(None, runner.DEFAULT_ROLE_MODELS)


def parse(argv, environ=None):
    args = PARSER.parse_args(argv)
    with contextlib.redirect_stderr(io.StringIO()):
        autocode_args.resolve_token_env_placeholders(args, PARSER, environ or {})
    return args


class SentinelResolutionTests(unittest.TestCase):
    def test_dash_reads_the_paired_variable(self):
        args = parse(
            ["--resume-paused", "--retry-failed-stage", "--job-retry-token", "-"],
            {"AUTOCODE_JOB_RETRY_TOKEN": "secret-1"},
        )
        self.assertEqual("secret-1", args.job_retry_token)

    def test_every_token_option_has_a_pairing(self):
        cases = (
            (
                ["--resume-paused", "--answer", "route-terra=zai/glm", "--resolver-token", "-"],
                "resolver_token",
                "AUTOCODE_RESOLVER_TOKEN",
            ),
            (["--recover-job-report", "-"], "recover_job_report", "AUTOCODE_RECOVER_JOB_REPORT"),
            (["--approve-goal", "-"], "approve_goal", "AUTOCODE_APPROVE_GOAL_TOKEN"),
            (["--review-token", "-"], "review_token", "AUTOCODE_REVIEW_TOKEN"),
            (["--expected-goal-token", "-"], "expected_goal_token", "AUTOCODE_EXPECTED_GOAL_TOKEN"),
            (["--expected-recovery-token", "-"], "expected_recovery_token", "AUTOCODE_EXPECTED_RECOVERY_TOKEN"),
        )
        for argv, attribute, variable in cases:
            with self.subTest(variable=variable):
                args = parse(argv, {variable: "secret"})
                self.assertEqual("secret", getattr(args, attribute))

    def test_missing_variable_is_a_usage_error(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            parse(["--approve-goal", "-"])

    def test_resolver_sentinel_without_a_consumer_is_a_usage_error(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            parse(["--resolver-token", "-"], {"AUTOCODE_RESOLVER_TOKEN": "ambient"})

    def test_resolver_sentinel_with_an_operational_request_resolves(self):
        args = parse(
            ["--resolver-request", "request-1", "--resolver-token", "-"],
            {"AUTOCODE_RESOLVER_TOKEN": "secret"},
        )
        self.assertEqual("secret", args.resolver_token)

    def test_resolver_sentinel_with_delegation_resolves(self):
        args = parse(["--delegate", "Q1", "--resolver-token", "-"], {"AUTOCODE_RESOLVER_TOKEN": "secret"})
        self.assertEqual("secret", args.resolver_token)

    def test_literal_token_still_works_and_ambient_variable_is_ignored(self):
        args = parse(["--approve-goal", "literal-token"], {"AUTOCODE_APPROVE_GOAL_TOKEN": "ambient"})
        self.assertEqual("literal-token", args.approve_goal)

    def test_ambient_variable_alone_never_authorizes(self):
        args = PARSER.parse_args([])
        autocode_args.resolve_token_env_placeholders(
            args,
            PARSER,
            {
                "AUTOCODE_REVIEW_TOKEN": "ambient",
                "AUTOCODE_APPROVE_GOAL_TOKEN": "ambient",
                "AUTOCODE_RESOLVER_TOKEN": "ambient",
            },
        )
        actions = autocode_args.user_actions(args)
        self.assertFalse([flag for flag, given in actions.items() if given], actions)


class TaskRunSecretEnvTests(unittest.TestCase):
    def test_retry_job_passes_the_token_by_environment(self):
        run = taskrun.TaskRun(Path("."), Path("."), command=("echo",))
        with (
            patch.object(run, "_invoke", return_value=Mock(stdout="{}")) as invoke,
            patch.object(type(run), "status", return_value={}),
        ):
            run.retry_job("secret-2")
        call = invoke.call_args
        self.assertTrue(call.kwargs.get("advancing"))
        argv = [item for item in call.args if item != "retry job"]
        self.assertIn("-", argv)
        self.assertNotIn("secret-2", argv)
        self.assertEqual({"AUTOCODE_JOB_RETRY_TOKEN": "secret-2"}, call.kwargs["secret_env"])

    def test_answer_passes_the_resolver_token_by_environment(self):
        run = taskrun.TaskRun(Path("."), Path("."), command=("echo",))
        with (
            patch.object(run, "_invoke", return_value=Mock(stdout="{}")) as invoke,
            patch.object(type(run), "status", return_value={}),
        ):
            run.answer("q1", "an answer", resolver_token="secret-3")
        argv = [item for item in invoke.call_args.args if item != "answer"]
        self.assertIn("-", argv)
        self.assertNotIn("secret-3", argv)
        self.assertEqual({"AUTOCODE_RESOLVER_TOKEN": "secret-3"}, invoke.call_args.kwargs["secret_env"])

    def test_child_sees_the_token_in_its_environment_not_in_argv(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "spy.json"
            spy = Path(directory) / "spy.py"
            spy.write_text(
                "import json, os, sys\n"
                f"json.dump([sys.argv[1:], os.environ.get('AUTOCODE_JOB_RETRY_TOKEN')], open({str(out)!r}, 'w'))\n",
            )
            run = taskrun.TaskRun(Path(directory), Path(directory), command=(sys.executable, str(spy)))
            with patch.object(type(run), "status", return_value={}):
                run.retry_job("secret-4")
            argv, token = json.loads(out.read_text())
            self.assertIn("-", argv)
            self.assertNotIn("secret-4", argv)
            self.assertEqual("secret-4", token)


if __name__ == "__main__":
    unittest.main()
