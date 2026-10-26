"""Explicit total caps rebind a failed job without launching or granting a retry."""

import json
import os
import subprocess

from autocode_taskrun import TaskRun, TaskRunError

from .test_job_failure_recovery import JobHarness


class JobCapChangeTests(JobHarness):
    def command(self, run, *flags, token=None):
        return subprocess.run(
            [*run.command, "--run-dir", str(run.run_dir), "--no-chat", *flags],
            env={**os.environ, **run.env, "AUTOCODE_JOB_RETRY_TOKEN": token or ""},
            cwd=self.workspace,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def snapshot(self, run):
        response = self.command(run, "--status", "--json")
        self.assertEqual(0, response.returncode, response.stderr)
        return json.loads(response.stdout)

    def change(self, run, token, *extra):
        return self.command(
            run, "--job-retry-token", "-", "--max-seconds", "1800", "--max-cost-usd", "5", *extra, token=token
        )

    def test_explicit_total_caps_preserve_usage_and_require_a_fresh_separate_retry(self):
        self.options += ("--max-cost-usd", "3")
        run = self.start()
        need = self.paused(run)
        before = self.snapshot(run)
        response = self.change(run, need["job_retry_token"])
        self.assertEqual(0, response.returncode, response.stdout + response.stderr)
        after = self.snapshot(run)
        self.assertEqual("PAUSED_JOB_FAILURE", after["status"])
        self.assertEqual(before["view"]["usage"], after["view"]["usage"])
        expected = dict(before["settings"]["limits"], max_seconds=1800, max_cost_usd=5)
        self.assertEqual(expected, after["settings"]["limits"])
        self.assertEqual(before["settings"]["roles"], after["settings"]["roles"])
        self.assertEqual(before.get("goal_contract"), after.get("goal_contract"))
        fresh = after["view"]["needs"]["job_retry_token"]
        self.assertNotEqual(need["job_retry_token"], fresh)
        self.assertEqual(1, self.count(), "saving caps must launch no provider")
        self.command(run, "--resume-paused")
        self.assertEqual("PAUSED_JOB_FAILURE", run.status()["status"])
        self.assertEqual(1, self.count(), "saving caps must not authorize ordinary Resume")
        retry = TaskRun(self.workspace, run.run_dir, command=run.command, env={**run.env, "JOB_MODE": "success"})
        with self.assertRaisesRegex(TaskRunError, "token"):
            retry.retry_job(need["job_retry_token"])
        self.assertEqual(1, self.count())
        completed = retry.retry_job(fresh)
        self.assertEqual("TASK_COMPLETE", completed["status"])
        self.assertEqual(2, self.count())
        self.assertTrue(list(run.run_dir.rglob("archived-review-change-*")))
        self.assertGreater(
            completed["usage"]["tokens"]["input_tokens"], before["view"]["usage"]["tokens"]["input_tokens"]
        )

    def test_stale_source_or_token_and_mixed_settings_are_atomic_refusals(self):
        run = self.start()
        need = self.paused(run)
        before = self.snapshot(run)
        for extra in (
            ("--max-stage-seconds", "901"),
            ("--max-idle-seconds", "1"),
            ("--max-tool-seconds", "1"),
            ("--sol-model", "another-model"),
            ("--planning-review-call-limit", "3"),
            ("--grant-recovery", "1"),
            ("--feedback", "Change the plan"),
            ("--resume-paused", "--retry-failed-stage"),
        ):
            with self.subTest(extra=extra):
                response = self.change(run, need["job_retry_token"], *extra)
                self.assertNotEqual(0, response.returncode)
                after = self.snapshot(run)
                self.assertEqual(before["settings"], after["settings"])
                self.assertEqual(before["view"]["usage"], after["view"]["usage"])
                self.assertEqual(need["job_retry_token"], after["view"]["needs"]["job_retry_token"])
                self.assertEqual(1, self.count())
        response = self.change(run, "jr:stale")
        self.assertNotEqual(0, response.returncode)
        self.assertIn("token", response.stderr)
        source = self.workspace / "calc.py"
        source.write_text(source.read_text() + "# later user edit\n")
        response = self.change(run, need["job_retry_token"])
        self.assertNotEqual(0, response.returncode)
        self.assertIn("Source changed", response.stderr)
        self.assertEqual(before["settings"], self.snapshot(run)["settings"])
        self.assertEqual(1, self.count())

    def test_total_caps_without_a_retry_token_still_cannot_change_the_failed_job(self):
        run = self.start()
        self.paused(run)
        before = self.snapshot(run)
        response = self.command(run, "--max-seconds", "1800", "--max-cost-usd", "5")
        self.assertNotEqual(0, response.returncode)
        self.assertEqual(before["settings"], self.snapshot(run)["settings"])
        self.assertEqual(1, self.count())

    def test_single_cap_changes_preserve_the_other_cap_and_reject_transport_drift(self):
        self.env["JOB_TRANSPORT"] = "original"
        self.options += ("--max-cost-usd", "3")
        run = self.start()
        self.paused(run)
        for flag, value, key, untouched, expected in (
            ("--max-seconds", "1800", "max_seconds", "max_cost_usd", 3),
            ("--max-cost-usd", "5", "max_cost_usd", "max_seconds", 1800),
        ):
            before = self.snapshot(run)
            token = before["view"]["needs"]["job_retry_token"]
            run.env["JOB_TRANSPORT"] = "different"
            refused = self.command(run, "--job-retry-token", "-", flag, value, token=token)
            self.assertNotEqual(0, refused.returncode)
            self.assertEqual(before["settings"], self.snapshot(run)["settings"])
            run.env["JOB_TRANSPORT"] = "original"
            accepted = self.command(run, "--job-retry-token", "-", flag, value, token=token)
            self.assertEqual(0, accepted.returncode, accepted.stdout + accepted.stderr)
            after = self.snapshot(run)
            self.assertEqual(int(value), after["settings"]["limits"][key])
            self.assertEqual(expected, after["settings"]["limits"][untouched])
            self.assertEqual(before["view"]["usage"], after["view"]["usage"])
            self.assertEqual(1, self.count())
