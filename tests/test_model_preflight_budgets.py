"""Automatic access probes obey the public run's budgets and stop requests."""

import contextlib
import datetime as dt
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode as runner
import autocode_model_preflight as preflight
import autocode_support as support
from providers.command import CommandProvider


class ModelPreflightBudgetCliTests(unittest.TestCase):
    """Real in-process CLI; only provider discovery and model traffic are simulated."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="preflight-budget-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.workspace),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.test",
                "commit",
                "--allow-empty",
                "-qm",
                "fixture",
            ],
            check=True,
        )
        self.run_dir = self.workspace / ".autocode/runs/access-probe"
        models = {role: "fixture/reviewer" for role in ("astra", "terra", "sol", "completion", "glm", "plan_reviewer")}
        models["terra"] = models["glm"] = "fixture/builder"
        config = {
            "name": "budgetfixture",
            "command": [sys.executable, "-c", "raise AssertionError('no provider may spawn')"],
            "roles": {role: {"model": model, "effort": "medium"} for role, model in models.items()},
            "auth": {"command": ["fixture", "auth", "list"]},
        }
        self.adapter = CommandProvider(config, self.root / "provider.toml")
        self.probes = []
        self.stages = []
        self.clock = 100.0
        self.duration = 2.0
        self.probe_cost = 0.02
        self.on_probe = None
        self.stop_receipts = []
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(
            patch.dict(
                os.environ,
                {
                    "AUTOCODE_HOME": str(self.root / "registry"),
                    "XDG_CONFIG_HOME": str(self.root / "config"),
                    "CODEX_HOME": str(self.root / "codex-home"),
                    "AUTOCODE_PROVIDER": "budgetfixture",
                },
            )
        )
        self.stack.enter_context(patch.object(runner.autocode_providers, "resolve", return_value=self.adapter))
        self.stack.enter_context(patch.object(self.adapter, "local_settings", return_value={"engine": "budgetfixture"}))
        self.stack.enter_context(patch.object(self.adapter, "available_models", return_value=set(models.values())))
        self.stack.enter_context(patch.object(self.adapter, "check_models"))
        self.stack.enter_context(patch.object(self.adapter, "check_subscription_routes"))
        self.stack.enter_context(patch.object(preflight.transport, "execute", side_effect=self.probe))
        self.stack.enter_context(
            patch.object(
                preflight,
                "time",
                SimpleNamespace(monotonic=lambda: self.clock, gmtime=time.gmtime, strftime=time.strftime),
            )
        )
        self.stack.enter_context(patch.object(runner, "run_role", side_effect=self.stage))

    def probe(self, **kwargs):
        self.probes.append(kwargs["model"])
        directory = kwargs["directory"]
        directory.mkdir(parents=True, exist_ok=True)
        session = f"probe-{len(self.probes)}"
        events = [
            {"type": "step_start", "sessionID": session, "part": {"id": "start", "sessionID": session}},
            {
                "type": "step_finish",
                "sessionID": session,
                "part": {
                    "id": "finish",
                    "sessionID": session,
                    "reason": "stop",
                    "cost": self.probe_cost,
                    "tokens": {"input": 100, "output": 5, "reasoning": 0, "cache": {"read": 0, "write": 0}},
                },
            },
        ]
        events_path = directory / "events.jsonl"
        events_path.write_text("".join(json.dumps(row) + "\n" for row in events))
        self.clock += self.duration
        if self.on_probe and len(self.probes) == 1:
            self.on_probe()
        return {
            "exit_code": 0,
            "timed_out": False,
            "report": {"ok": True},
            "rows": events,
            "report_error": None,
            "metrics": support.event_metrics(events_path),
        }

    def stage(self, *args, **kwargs):
        self.stages.append(args)
        raise support.Paused("PAUSED_FIXTURE_STAGE", "Ordinary stage was reached")

    def invoke(self, *arguments):
        with (
            patch.object(sys, "argv", ["autocode", "--workspace", str(self.workspace), *arguments]),
            contextlib.redirect_stdout(io.StringIO()) as stdout,
            contextlib.redirect_stderr(io.StringIO()) as stderr,
        ):
            code = runner.main()
        return code, stdout.getvalue(), stderr.getvalue()

    def start(self, *limits):
        return self.invoke(
            "--brief",
            "Build a greeting CLI",
            "--in-place",
            "--new-run-dir",
            str(self.run_dir),
            "--provider",
            "budgetfixture",
            "--no-chat",
            "--evidence-provenance",
            "fake",
            *limits,
        )

    def status(self):
        code, stdout, stderr = self.invoke("--run-dir", str(self.run_dir), "--status")
        self.assertEqual(0, code, stderr)
        return json.loads(stdout)["view"]

    def submit_stop(self):
        with (
            patch.object(
                sys,
                "argv",
                [
                    "autocode",
                    "intervention",
                    "submit",
                    "--workspace",
                    str(self.workspace),
                    "--run-dir",
                    str(self.run_dir),
                    "--request-id",
                    "stop-during-probe",
                    "--kind",
                    "stop",
                    "--json",
                ],
            ),
            contextlib.redirect_stdout(io.StringIO()) as stdout,
            contextlib.redirect_stderr(io.StringIO()) as stderr,
        ):
            code = runner.main()
        self.assertEqual(0, code, stderr.getvalue())
        receipt = json.loads(stdout.getvalue())
        self.assertTrue(receipt["accepted"])
        self.stop_receipts.append(receipt)
        return receipt

    def pending_interventions(self):
        with (
            patch.object(
                sys,
                "argv",
                [
                    "autocode",
                    "intervention",
                    "inspect",
                    "--workspace",
                    str(self.workspace),
                    "--run-dir",
                    str(self.run_dir),
                    "--json",
                ],
            ),
            contextlib.redirect_stdout(io.StringIO()) as stdout,
            contextlib.redirect_stderr(io.StringIO()) as stderr,
        ):
            code = runner.main()
        self.assertEqual(0, code, stderr.getvalue())
        return json.loads(stdout.getvalue())

    def assert_one_charged_probe(self, view):
        attempts = [row for row in view["usage"]["accounting"]["attempts"] if not row["runner_owned"]]
        self.assertEqual(1, len(attempts))
        self.assertAlmostEqual(self.duration, attempts[0]["duration_seconds"])
        self.assertEqual(100, attempts[0]["tokens"]["input_tokens"])
        self.assertIsNotNone(view["cost"]["reported_usd"], "Native reported probe cost must remain priced")
        self.assertIsNotNone(view["cost"]["spent_usd"])
        self.assertAlmostEqual(self.probe_cost, view["cost"]["reported_usd"])
        self.assertAlmostEqual(self.probe_cost, view["cost"]["spent_usd"])
        self.assertEqual(0, view["cost"]["unpriced_stages"])

    def test_storage_failure_stops_before_any_paid_access_probe(self):
        import autocode_storage_budget as storage

        with patch.object(storage, "dispatch_refusal", return_value="temporary filesystem is full"):
            code, stdout, stderr = self.start("--max-cost-usd", "1")
        self.assertEqual([], self.probes)
        self.assertEqual([], self.stages)
        self.assertEqual("PAUSED_TASK_PREFLIGHT", self.status()["status"], (code, stdout, stderr))

    def test_reported_probe_cost_stops_before_another_request_and_is_public(self):
        code, stdout, stderr = self.start("--max-cost-usd", "0.01")
        self.assertEqual(2, code, stdout + stderr)
        self.assertEqual(1, len(self.probes), "The first probe already exceeded the saved money cap")
        self.assertEqual([], self.stages, "No ordinary stage may spend after the probe reached the cap")
        view = self.status()
        self.assertEqual("PAUSED_COST_LIMIT", view["needs"].get("pause_origin") or view["status"])
        self.assert_one_charged_probe(view)

    def test_elapsed_probe_time_stops_before_another_request_and_is_public(self):
        code, stdout, stderr = self.start("--max-seconds", "1")
        self.assertEqual(2, code, stdout + stderr)
        self.assertEqual(1, len(self.probes), "Probe time already exceeded the saved active-time budget")
        self.assertEqual([], self.stages)
        view = self.status()
        self.assertEqual("PAUSED_TIME_LIMIT", view["needs"].get("pause_origin") or view["status"])
        self.assert_one_charged_probe(view)

    def test_plain_resume_does_not_reprobe_or_double_charge_a_cost_pause(self):
        self.start("--max-cost-usd", "0.01")
        before = self.status()
        code, stdout, stderr = self.invoke("--run-dir", str(self.run_dir), "--resume-paused")
        self.assertEqual(2, code, stdout + stderr)
        self.assertEqual(1, len(self.probes))
        self.assertEqual([], self.stages)
        after = self.status()
        self.assert_one_charged_probe(after)
        self.assertEqual(before["cost"], after["cost"])

    def test_available_budget_admits_all_probes_then_the_ordinary_stage(self):
        code, stdout, stderr = self.start("--max-cost-usd", "1", "--max-seconds", "10")
        self.assertEqual(2, code, stdout + stderr)
        self.assertEqual(2, len(self.probes))
        self.assertEqual(1, len(self.stages), "Available budget must still admit ordinary work")
        view = self.status()
        self.assertEqual("PAUSED_FIXTURE_STAGE", view["needs"].get("pause_origin") or view["status"])
        self.assertIsNotNone(view["cost"]["spent_usd"], "Both completed native probes reported their cost")
        self.assertAlmostEqual(2 * self.probe_cost, view["cost"]["spent_usd"])
        attempts = [row for row in view["usage"]["accounting"]["attempts"] if not row["runner_owned"]]
        self.assertEqual(2, len(attempts))
        self.assertAlmostEqual(2 * self.duration, sum(row["duration_seconds"] for row in attempts))

    def test_stop_accepted_during_a_probe_prevents_the_next_request(self):
        self.on_probe = self.submit_stop
        code, stdout, stderr = self.start()
        self.assertEqual(2, code, stdout + stderr)
        self.assertEqual(1, len(self.probes), "An accepted Stop must prevent the next automatic probe")
        self.assertEqual([], self.stages)
        view = self.status()
        self.assertEqual("PAUSED_INTERVENTION", view["status"])
        self.assertIsNone(view["next_command"], "Stop is terminal, with no resume command")
        self.assert_one_charged_probe(view)
        duplicate = self.submit_stop()
        self.assertTrue(duplicate["idempotent"])
        self.assertEqual("already_applied", duplicate["consumer"])
        self.assertEqual(self.stop_receipts[0]["receipt"], duplicate["receipt"])
        self.invoke("--run-dir", str(self.run_dir), "--resume-paused")
        self.assertEqual(1, len(self.probes))
        self.assertEqual([], self.stages)
        self.assert_one_charged_probe(self.status())

    def test_pending_stop_waits_for_failed_probe_cleanup_across_invocations(self):
        admitted = {}

        def uncertain_transport(**kwargs):
            directory = kwargs["directory"]
            directory.mkdir(parents=True, exist_ok=True)
            metadata = {
                "schema": 1,
                "nonce": "d" * 32,
                "owner": {"pid": 99999991, "birth_identity": 1},
                "keeper": {"pid": 99999992, "birth_identity": 2},
                "provider": {"pid": 99999993, "birth_identity": 3},
                "receipt": str(directory / "supervision.json"),
            }
            receipt = {
                **metadata,
                "phase": "armed",
                "cause": None,
                "cleanup_error": None,
                "observed_at": support.now(),
                "processes": [metadata["provider"]],
            }
            support.atomic_json(Path(metadata["receipt"]), receipt)
            kwargs["checkpoint"](metadata)
            admitted.update(metadata=metadata, receipt=receipt)
            self.probe(**kwargs)
            self.submit_stop()
            raise preflight.processes.ProcessError("Fixture provider cleanup is still unknown")

        with (
            patch.object(preflight.transport, "execute", side_effect=uncertain_transport),
            patch.object(preflight.attempts.processes, "live_processes", return_value=[]) as native_absence,
        ):
            code, stdout, stderr = self.start()
            self.assertEqual(2, code, stdout + stderr)

            def assert_cleanup_hold():
                view = self.status()
                self.assertEqual("PAUSED_PROCESS_CLEANUP", view["needs"].get("pause_origin") or view["status"])
                inbox = self.pending_interventions()
                self.assertEqual(1, inbox["pending_count"], "Stop must remain pending until cleanup is verified")
                self.assertEqual("stop-during-probe", inbox["requests"][0]["id"])
                self.assertEqual(1, len(self.probes))
                self.assertEqual([], self.stages)
                self.assertEqual("pending_only", self.submit_stop()["consumer"])

            assert_cleanup_hold()
            for flags in ((), ("--resume-paused",)):
                code, stdout, stderr = self.invoke("--run-dir", str(self.run_dir), *flags)
                self.assertEqual(2, code, stdout + stderr)
                assert_cleanup_hold()
            native_absence.assert_not_called()

            receipt = {
                **admitted["receipt"],
                "phase": "stopped",
                "cause": "provider_stopped",
                "observed_at": (dt.datetime.now(dt.UTC) + dt.timedelta(seconds=3)).isoformat(),
            }
            support.atomic_json(Path(admitted["metadata"]["receipt"]), receipt)
            native_absence.side_effect = preflight.processes.ProcessError("Fixture native absence is unverified")
            code, stdout, stderr = self.invoke("--run-dir", str(self.run_dir), "--resume-paused")
            self.assertEqual(2, code, stdout + stderr)
            assert_cleanup_hold()
            native_absence.assert_called()

            native_absence.side_effect = None
            code, stdout, stderr = self.invoke("--run-dir", str(self.run_dir), "--resume-paused")
            self.assertEqual(2, code, stdout + stderr)
            view = self.status()
            self.assertEqual("PAUSED_INTERVENTION", view["status"])
            self.assertIsNone(view["next_command"])
            self.assertEqual(0, self.pending_interventions()["pending_count"])
            self.assertEqual("already_applied", self.submit_stop()["consumer"])
            self.assertEqual(1, len(self.probes))
            self.assertEqual([], self.stages)
            self.assertAlmostEqual(self.probe_cost, view["cost"]["spent_usd"])
            attempts = [row for row in view["usage"]["accounting"]["attempts"] if not row["runner_owned"]]
            self.assertEqual(1, len(attempts), "Reconciliation must retain one charged provider request")
            self.invoke("--run-dir", str(self.run_dir), "--resume-paused")
            self.assertEqual(1, len(self.probes))
            self.assertEqual(view["cost"], self.status()["cost"])


if __name__ == "__main__":
    unittest.main()
