"""Explicit operator recovery grants through the public TaskRun CLI client."""

import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from autocode_taskrun import TaskRun, TaskRunError


class StageDeadlineRecoveryTests(unittest.TestCase):
    def test_saved_deadline_replaces_the_same_handles_launch_option(self):
        for deadline in (("--max-stage-seconds", "900"), ("--max-stage-seconds=900",)):
            with self.subTest(deadline=deadline):
                run = TaskRun(Path("/repo"), Path("/run"), options=(*deadline, "--engine", "codex"))
                saved = {"seconds": 900, "token": "old"}
                calls = []

                def invoke(name, *args, **kwargs):
                    calls.append((name, args, kwargs))
                    if name == "save stage deadline":
                        saved.update(seconds=int(args[args.index("--max-stage-seconds") + 1]), token="fresh")
                    return subprocess.CompletedProcess(
                        [],
                        0,
                        json.dumps(
                            {
                                "settings": {"limits": {"stage_timeout_seconds": saved["seconds"]}},
                                "view": {"needs": {"job_retry_token": saved["token"]}},
                            }
                        ),
                        "",
                    )

                with patch.object(run, "_invoke", side_effect=invoke):
                    fresh = run.save_stage_deadline("old", 1800)
                    run.retry_job(fresh["token"])
                self.assertEqual(1800, saved["seconds"])
                retry = next(call for call in calls if call[0] == "retry job")
                self.assertEqual(
                    (
                        "--resume-paused",
                        "--retry-failed-stage",
                        "--job-retry-token",
                        "-",
                        "--no-chat",
                        "--engine",
                        "codex",
                    ),
                    retry[1],
                )
                self.assertEqual({"AUTOCODE_JOB_RETRY_TOKEN": "fresh"}, retry[2]["secret_env"])

    def test_failed_or_unconfirmed_save_retains_launch_options(self):
        for rejected in (True, False):
            with self.subTest(rejected=rejected):
                options = ("--max-stage-seconds", "900", "--engine", "codex")
                run = TaskRun(Path("/repo"), Path("/run"), options=options)
                with (
                    patch.object(run, "_invoke", side_effect=TaskRunError("rejected") if rejected else None),
                    patch.object(run, "status", return_value={"needs": {}}),
                    self.assertRaises(TaskRunError),
                ):
                    run.save_stage_deadline("old", 1800)
                self.assertEqual(options, run.options)


class RecoveryGrantTests(unittest.TestCase):
    def test_advance_until_input_takes_the_exact_named_deterministic_recovery(self):
        run = TaskRun(Path("/repo"), Path("/repo/.autocode/runs/task"))
        attempt = "001/requirements-gather-01"
        recovery = {
            "status": "PAUSED_CONTROLLER_EXIT",
            "done": False,
            "next_stage": "requirements_gather",
            "iteration": 1,
            "phase": "requirements",
            "needs": {
                "kind": "continue",
                "abandon_stage": attempt,
                "action": f"--abandon-stage {attempt} then --resume-paused",
            },
            "next_command": (
                f"autocode --run-dir /repo/.autocode/runs/task --workspace /repo --abandon-stage {attempt}"
            ),
        }
        stopped = {
            "status": "PAUSED_STAGE_ABANDONED",
            "done": False,
            "next_stage": "requirements_gather",
            "iteration": 1,
            "phase": "requirements",
            "needs": {"kind": "resume"},
            "next_command": "autocode --run-dir /repo/.autocode/runs/task --workspace /repo --resume-paused",
        }
        with (
            patch.object(run, "status", side_effect=[recovery, stopped]),
            patch.object(run, "_invoke") as invoke,
        ):
            self.assertEqual(stopped, run.advance_until_input())
        invoke.assert_called_once_with("advance", "--abandon-stage", attempt, advancing=True, secret_env={})

    def test_grant_resumes_with_exact_positive_allowance_and_returns_public_view(self):
        run = TaskRun(
            Path("/repo"), Path("/repo/.autocode/runs/task"), command=("autocode",), options=("--engine", "opencode")
        )
        view = {"status": "RUNNING", "done": False, "needs": {"kind": "continue"}}
        with (
            patch("autocode_taskrun.run_captured", return_value=subprocess.CompletedProcess([], 0, "", "")) as execute,
            patch(
                "autocode_taskrun.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, json.dumps({"view": view}), ""),
            ),
        ):
            self.assertEqual(view, run.grant_recovery(1))
        self.assertEqual(
            [
                "autocode",
                "--resume-paused",
                "--grant-recovery",
                "1",
                "--no-chat",
                "--engine",
                "opencode",
                "--workspace",
                "/repo",
                "--run-dir",
                "/repo/.autocode/runs/task",
            ],
            execute.call_args.args[0],
        )
        self.assertNotIn("--grant-recovery", run.options)

    def test_invalid_allowance_never_invokes_cli(self):
        run = TaskRun(Path("/repo"), Path("/repo/.autocode/runs/task"))
        for amount in (0, -1, True, 1.5, "1", None):
            with self.subTest(amount=amount), patch("autocode_taskrun.run_captured") as execute:
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    run.grant_recovery(amount)
                execute.assert_not_called()

    def test_prefixed_cli_rejection_is_not_reported_as_an_accepted_pause(self):
        run = TaskRun(Path("/repo"), Path("/repo/.autocode/runs/task"))
        for output in ("stdout", "stderr"):
            with self.subTest(output=output):
                streams = {"stdout": "", "stderr": ""}
                streams[output] = (
                    "AutoResolver: internal checkpoint updated\nInput rejected: current recovery request required\n"
                )
                result = subprocess.CompletedProcess([], 2, **streams)
                with (
                    patch("autocode_taskrun.run_captured", return_value=result) as execute,
                    self.assertRaisesRegex(TaskRunError, "current recovery request required"),
                ):
                    run.grant_recovery(1)
                self.assertEqual(1, execute.call_count)
