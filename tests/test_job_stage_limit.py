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

import autocode_action_recovery as action_recovery
import autocode_args as arguments
import autocode_job_args as job_args
import autocode_job_failure as failure
import autocode_job_time_view as job_time_view
import autocode_run_view as run_view
import autocode_util as util
from autocode_taskrun import TaskRun, TaskRunError

from . import test_acceptance_writer_route as writer_fixture


class StageLimitPolicyTests(unittest.TestCase):
    def approval_state(self):
        contract = {"task_id": "task-1", "revision": 1, "body": {"requirements": []}}
        contract["hash"] = util.digest({key: contract[key] for key in ("task_id", "revision", "body")})
        from autocode_contract_identity import token

        shown = token(contract)
        return {
            "status": "AWAITING_GOAL_APPROVAL",
            "goal_contract": contract,
            "displayed_goal": shown,
            "active_seconds": 900,
            "settings": {"limits": {"max_seconds": 5000}, "budget_origins": {"max_seconds": "user_explicit"}},
            "user_events": [],
            "configuration_changes": [],
        }

    def test_plan_approval_offers_exact_plan_bound_save_only_time_control(self):
        state = self.approval_state()
        view = run_view.view(state)
        row = view["needs"]["job_time_controls"][0]
        self.assertEqual(
            ("max_seconds", 5000, 5001, "approval"), (row["id"], row["current"], row["minimum"], row["purpose"])
        )
        self.assertEqual(state["displayed_goal"], row["binding"])
        self.assertIn("--extend-plan-time-limit --max-seconds N", row["command"])
        self.assertIn("AUTOCODE_EXPECTED_GOAL_TOKEN=", row["command"])
        self.assertIn("--expected-goal-token -", row["command"])

    def test_plan_time_save_keeps_exact_approval_pending_and_refuses_stale_or_busy_state(self):
        import autocode_plan_time as plan_time

        state = self.approval_state()
        before_contract = copy.deepcopy(state["goal_contract"])
        args = SimpleNamespace(
            expected_goal_token=state["displayed_goal"],
            _approval_time_settings={
                "limits": {"max_seconds": 6000},
                "budget_origins": {"max_seconds": "user_explicit"},
            },
        )
        runtime = Mock()
        plan_time.change(runtime, state, Path("/run/state.json"), args)
        self.assertEqual("AWAITING_GOAL_APPROVAL", state["status"])
        self.assertEqual(before_contract, state["goal_contract"])
        self.assertEqual(args.expected_goal_token, state["displayed_goal"])
        self.assertEqual(6000, state["settings"]["limits"]["max_seconds"])
        self.assertEqual("plan_time_limit_extended", state["user_events"][-1]["kind"])
        runtime.write_json.assert_called_once()
        for key, change in (
            ("stale_token", {"expected_goal_token": "r0:stale"}),
            ("status", {"status": "RUNNING"}),
            ("active_stage", {"active_stage": {"stage": "builder"}}),
        ):
            with self.subTest(key=key):
                candidate = self.approval_state()
                candidate.update({k: v for k, v in change.items() if k != "expected_goal_token"})
                stale_args = copy.deepcopy(args)
                if "expected_goal_token" in change:
                    stale_args.expected_goal_token = change["expected_goal_token"]
                with self.assertRaises(ValueError):
                    plan_time.change(runtime, candidate, Path("/run/state.json"), stale_args)

    def test_plan_time_cli_selector_requires_the_exact_boundary_and_larger_limit(self):
        argv = [
            "--run-dir",
            "/run",
            "--extend-plan-time-limit",
            "--max-seconds",
            "6000",
            "--expected-goal-token",
            "-",
        ]
        with patch.dict("os.environ", {"AUTOCODE_EXPECTED_GOAL_TOKEN": "r0:shown"}):
            args, _ = arguments.parse(None, argv, arguments.DEFAULT_ROLE_MODELS)
        self.assertEqual("r0:shown", args.expected_goal_token)
        self.assertTrue(args._approval_time_change)
        self.assertEqual(6000, args.max_seconds)
        for extra in (("--resume-paused",), ("--approve-goal", "r0:shown"), ("--max-cost-usd", "0")):
            with self.subTest(extra=extra), contextlib.redirect_stderr(io.StringIO()):
                with patch.dict("os.environ", {"AUTOCODE_EXPECTED_GOAL_TOKEN": "r0:shown"}):
                    with self.assertRaises(SystemExit):
                        arguments.parse(None, [*argv, *extra], arguments.DEFAULT_ROLE_MODELS)

    def test_approval_time_recovery_dispatch_saves_without_approving_or_launching(self):
        state = self.approval_state()
        runtime = Mock()
        runtime.now.return_value = "2026-11-16T12:00:00Z"
        args = SimpleNamespace(
            _approval_time_change=True,
            _approval_time_settings={
                "limits": {"max_seconds": 6000},
                "budget_origins": {"max_seconds": "user_explicit"},
            },
            expected_goal_token=state["displayed_goal"],
        )
        with contextlib.redirect_stdout(io.StringIO()) as output:
            result = action_recovery.gate_failures(
                runtime,
                args,
                Mock(),
                state,
                Path("/run/state.json"),
                Path("/run"),
                Path("/workspace"),
                False,
            )
        self.assertEqual(0, result)
        self.assertIn("Plan approval is still pending; no provider launched", output.getvalue())
        self.assertEqual("AWAITING_GOAL_APPROVAL", state["status"])
        runtime.write_json.assert_called_once()

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
            ("--tester-model", "other"),
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
        for flags in (("--answer", "route-tester=gpt-6-luna"), ("--delegate", "route-tester")):
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
                "reason": "Stage timed out",
                "archive": "/run/archive",
                "write_diagnosis": {},
                "unrestored": [],
            },
            "recovery_context": {
                "timeout_kind": "stage",
                "attempt_id": "001/acceptance-tests-01",
                "stage": "write_acceptance_tests",
            },
            "status": "PAUSED_JOB_FAILURE",
            "active_seconds": 1000,
            "user_events": [],
        }

    def test_failed_job_offers_separate_token_bound_time_controls(self):
        state = self.state()
        view = run_view.action(state, run_dir=Path("/run"), workspace=Path("/workspace"))
        controls = view["needs"]["job_time_controls"]
        self.assertEqual(["max_seconds", "max_stage_seconds"], [row["id"] for row in controls])
        for row, flag, current in zip(controls, ("--max-seconds", "--max-stage-seconds"), (5000, 900), strict=True):
            self.assertEqual((current, current + 1), (row["current"], row["minimum"]))
            self.assertEqual(flag, row["flag"])
            self.assertIn("AUTOCODE_JOB_RETRY_TOKEN=jr:current", row["command"])
            self.assertIn(f"{flag} N --job-retry-token -", row["command"])
            self.assertNotIn("--resume-paused", row["command"])
        state["recovery_context"]["timeout_kind"] = "idle"
        controls = run_view.action(state, run_dir=Path("/run"), workspace=Path("/workspace"))["needs"][
            "job_time_controls"
        ]
        self.assertEqual(["max_seconds"], [row["id"] for row in controls])

    def test_time_controls_only_describe_actionable_finite_limits(self):
        state = self.state()
        need = run_view.needs(state)
        for key in ("active_stage", "active_runner_check", "pending_report_repair", "uncertain_artifacts"):
            with self.subTest(busy=key):
                busy = copy.deepcopy(state)
                busy[key] = {"in_progress": True}
                self.assertEqual([], job_time_view.offers(busy, need, run_dir="/run", workspace="/workspace"))
        for value in (None, 0):
            with self.subTest(total=value):
                uncapped = copy.deepcopy(state)
                uncapped["settings"]["limits"]["max_seconds"] = value
                self.assertEqual(
                    ["max_stage_seconds"],
                    [row["id"] for row in job_time_view.offers(uncapped, need, run_dir="/run", workspace="/workspace")],
                )
        for change in ({"kind": "recover_source"}, {"job_retry_token": None}):
            self.assertEqual(
                [], job_time_view.offers(state, {**need, **change}, run_dir="/run", workspace="/workspace")
            )

    def test_invalid_total_time_or_usage_never_produces_a_time_control(self):
        state = self.state()
        need = run_view.needs(state)
        for limit, used in ((True, 0), (-1, 0), (9007199254740991, 0), (6000, -1), (6000, float("nan"))):
            with self.subTest(limit=limit, used=used):
                changed = copy.deepcopy(state)
                changed["settings"]["limits"]["max_seconds"] = limit
                changed["active_seconds"] = used
                controls = job_time_view.offers(changed, need, run_dir="/run", workspace="/workspace")
                self.assertNotIn("max_seconds", [row["id"] for row in controls])

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

    def test_same_handle_saves_a_deadline_then_retries_only_the_fresh_token(self):
        run, held = self.start()
        before = self.snapshot(run)
        old_seconds = before["settings"]["limits"]["stage_timeout_seconds"]
        run.options = ("--max-stage-seconds", str(old_seconds))
        old_token = held["needs"]["job_retry_token"]
        calls = self.calls.read_bytes()
        saved = run.save_stage_deadline(old_token, old_seconds + 900)
        self.assertNotEqual(old_token, saved["token"])
        self.assertEqual("PAUSED_JOB_FAILURE", saved["view"]["status"])
        self.assertEqual(calls, self.calls.read_bytes())
        for key in ("usage", "cost", "approved_contract"):
            self.assertEqual(held[key], saved["view"][key], key)
        with self.assertRaises(TaskRunError):
            run.retry_job(old_token)
        self.assertEqual(calls, self.calls.read_bytes())
        run.env.pop("WRITER_TIMEOUT")
        completed = run.retry_job(saved["token"])
        self.assertTrue(completed["done"], completed.get("needs"))
        self.assertEqual(old_seconds + 900, self.snapshot(run)["settings"]["limits"]["stage_timeout_seconds"])
        self.assertEqual(before["settings"]["roles"], self.snapshot(run)["settings"]["roles"])
        self.assertEqual(4, len(self.calls.read_text().splitlines()))

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
