"""A larger stage deadline preserves the inspected failure and requires a fresh retry."""

import contextlib
import copy
import io
import json
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autocode_args as arguments
import autocode_job_args as job_args
import autocode_job_failure as failure
from autocode_taskrun import TaskRun, TaskRunError

from . import test_acceptance_writer_route as writer_fixture


class StageLimitPolicyTests(unittest.TestCase):
    def test_standalone_stage_limit_is_a_metadata_action(self):
        parser = arguments.build_parser(None, arguments.DEFAULT_ROLE_MODELS)
        args = parser.parse_args(
            ["--run-dir", "/run", "--job-retry-token", "jr:current", "--max-stage-seconds", "1800"]
        )
        job_args.validate(args, parser, {"run_dir", "job_retry_token", "max_stage_seconds"})
        self.assertTrue(args._job_cap_change)

    def test_stage_limit_cannot_mix_with_other_settings_or_retry(self):
        for extra in (
            ("--max-cost-usd", "0"),
            ("--max-seconds", "9000"),
            ("--sol-model", "other"),
            ("--resume-paused", "--retry-failed-stage"),
        ):
            with self.subTest(extra=extra), contextlib.redirect_stderr(io.StringIO()):
                parser = arguments.build_parser(None, arguments.DEFAULT_ROLE_MODELS)
                args = parser.parse_args(
                    ["--run-dir", "/run", "--job-retry-token", "jr:current", "--max-stage-seconds", "1800", *extra]
                )
                with self.assertRaises(SystemExit):
                    job_args.validate(
                        args,
                        parser,
                        {"run_dir", "job_retry_token", "max_stage_seconds", extra[0][2:].replace("-", "_")},
                    )

    def test_the_model_answer_refusal_precedes_the_stage_deadline_refusal(self):
        # A model answer with the retry it cannot make (#463) must get the model-owning refusal
        # even when the mix also carries a stage deadline; the deadline rule never preempts it.
        for flags in (("--answer", "route-sol=gpt-6-luna"), ("--delegate", "route-sol")):
            with self.subTest(flags=flags), contextlib.redirect_stderr(io.StringIO()) as stderr:
                parser = arguments.build_parser(None, arguments.DEFAULT_ROLE_MODELS)
                args = parser.parse_args(
                    [
                        "--run-dir",
                        "/run",
                        *flags,
                        "--resume-paused",
                        "--retry-failed-stage",
                        "--max-stage-seconds",
                        "60",
                        "--job-retry-token",
                        "T",
                    ]
                )
                with self.assertRaises(SystemExit):
                    job_args.validate(
                        args,
                        parser,
                        {
                            "run_dir",
                            "job_retry_token",
                            "resume_paused",
                            "retry_failed_stage",
                            "max_stage_seconds",
                            flags[0][2:].replace("-", "_"),
                        },
                    )
                message = stderr.getvalue()
                self.assertIn("names a stopped job's model on its own", message)
                self.assertNotIn("Save the stopped job's stage deadline", message)

    def state(self):
        return {
            "settings": {
                "limits": {"stage_timeout_seconds": 900, "max_seconds": 5000, "max_cost_usd": 12},
                "roles": {"acceptance_tester": {"model": "selected", "reasoning_effort": "high"}},
            },
            "job_failure": {
                "kind": "timeout",
                "attempt_id": "001/acceptance-tests-01",
                "stage": "write_acceptance_tests",
                "source_identity": "original",
                "job_retry_token": "jr:current",
            },
            "recovery_context": {
                "timeout_kind": "stage",
                "attempt_id": "001/acceptance-tests-01",
                "stage": "write_acceptance_tests",
            },
            "status": "PAUSED_JOB_FAILURE",
            "user_events": [],
        }

    def change(self, state, seconds):
        expected = copy.deepcopy(state["settings"])
        expected["limits"]["stage_timeout_seconds"] = seconds
        expected["budget_origins"] = {"stage_timeout_seconds": "user_explicit"}
        args = SimpleNamespace(
            job_retry_token="jr:current",
            max_seconds=None,
            max_cost_usd=None,
            max_stage_seconds=seconds,
            _job_cap_settings=expected,
        )
        runtime = Mock()
        with patch.object(failure, "_validate_retry", return_value=state["job_failure"]):
            failure.change_total_caps(runtime, state, Path("/run"), Path("/workspace"), args)
        return runtime

    def test_deadline_change_rebinds_only_the_limit_and_keeps_the_job_paused(self):
        state = self.state()
        before = copy.deepcopy(state)
        runtime = self.change(state, 1800)
        self.assertEqual(1800, state["settings"]["limits"]["stage_timeout_seconds"])
        self.assertEqual(before["settings"]["roles"], state["settings"]["roles"])
        self.assertEqual("PAUSED_JOB_FAILURE", state["status"])
        self.assertNotEqual("jr:current", state["job_failure"]["job_retry_token"])
        self.assertNotIn("job_retry_authorization", state)
        self.assertEqual({"stage_timeout_seconds": {"from": 900, "to": 1800}}, state["user_events"][-1]["changes"])
        runtime.write_json.assert_called_once()

    def test_only_an_increase_for_the_exact_hard_stage_timeout_is_admitted(self):
        for seconds, kind, timeout, attempt in (
            (0, "timeout", "stage", "001/acceptance-tests-01"),
            (900, "timeout", "stage", "001/acceptance-tests-01"),
            (1800, "exit", "stage", "001/acceptance-tests-01"),
            (1800, "timeout", "idle", "001/acceptance-tests-01"),
            (1800, "timeout", "stage", "other"),
        ):
            with self.subTest(seconds=seconds, kind=kind, timeout=timeout, attempt=attempt):
                state = self.state()
                state["job_failure"]["kind"] = kind
                state["recovery_context"].update(timeout_kind=timeout, attempt_id=attempt)
                before = copy.deepcopy(state)
                with self.assertRaises(ValueError):
                    self.change(state, seconds)
                self.assertEqual(before, state)


class StageLimitCLI(unittest.TestCase):
    setUp = writer_fixture.AcceptanceWriterRouteCLI.setUp
    start = writer_fixture.AcceptanceWriterRouteCLI.start
    snapshot = writer_fixture.AcceptanceWriterRouteCLI.snapshot

    def test_timed_out_tester_keeps_model_plan_usage_and_reopens_before_one_retry(self):
        run, held = self.start()
        self.assertEqual("PAUSED_JOB_FAILURE", held["status"])
        writer_fixture.AcceptanceWriterRouteCLI.assert_deadline_extended_before_pause(self, run)
        before = self.snapshot(run)
        token = held["needs"]["job_retry_token"]
        original = self.calls.read_bytes()
        old = before["settings"]["limits"]["stage_timeout_seconds"]
        response = subprocess.run(
            [
                *run.command,
                "--run-dir",
                str(run.run_dir),
                "--no-chat",
                "--job-retry-token",
                token,
                "--max-stage-seconds",
                str(old + 900),
            ],
            env=run.env,
            cwd=run.cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, response.returncode, response.stdout + response.stderr)
        self.assertIn("Limits saved; job remains paused", response.stdout)
        after = self.snapshot(run)
        self.assertEqual("PAUSED_JOB_FAILURE", after["status"])
        self.assertEqual(before["settings"]["roles"], after["settings"]["roles"])
        self.assertEqual(
            dict(before["settings"]["limits"], stage_timeout_seconds=old + 900), after["settings"]["limits"]
        )
        for key in ("usage", "cost", "approved_contract"):
            self.assertEqual(held[key], run.status()[key], key)
        self.assertEqual(original, self.calls.read_bytes())
        fresh = run.status()["needs"]["job_retry_token"]
        self.assertNotEqual(token, fresh)
        run.env.pop("WRITER_TIMEOUT")
        reopened = TaskRun(run.workspace, run.run_dir, command=run.command, env=run.env, cwd=run.cwd)
        with self.assertRaises(TaskRunError):
            reopened.retry_job(token)
        self.assertEqual(original, self.calls.read_bytes())
        completed = reopened.retry_job(fresh)
        self.assertTrue(completed["done"], completed.get("needs"))
        rows = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertEqual(4, len(rows))
        self.assertEqual(1, len({row["model"] for row in rows}))
        self.assertTrue(list(run.run_dir.rglob("archived-acceptance-tests-*")))
