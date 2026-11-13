"""Automatic access probes obey the public run's budgets and stop requests."""

import contextlib
import datetime as dt
import io
import json
import os
import shlex
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
import autocode_tool_containment as containment
import autocode_toolchain as toolchain
from providers import kilocode_policy
from providers.command import CommandProvider
from providers.kilocode import KiloProvider


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
        models = {
            role: "fixture/reviewer"
            for role in ("task_planner", "builder", "tester", "completion", "planner", "plan_reviewer")
        }
        models["builder"] = models["planner"] = "fixture/builder"
        config = {
            "name": "budgetfixture",
            "command": [sys.executable, "-c", "raise AssertionError('no provider may spawn')"],
            "roles": {role: {"model": model, "effort": "medium"} for role, model in models.items()},
            "auth": {"command": ["fixture", "auth", "list"]},
        }
        self.adapter = CommandProvider(config, self.root / "provider.toml")
        self.probes = []
        self.probe_efforts = []
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
        self.real_transport = preflight.transport.execute
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
        self.probe_efforts.append(kwargs["effort"])
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

    def test_policy_startup_timeout_is_a_recorded_pause_without_model_launch(self):
        def timeout(*args, **kwargs):
            self.clock += 30
            raise subprocess.TimeoutExpired(["kilo", "debug", "config"], 30)

        with (
            patch.object(preflight.transport, "execute", side_effect=self.real_transport),
            patch.object(self.adapter, "launch", side_effect=timeout) as prepare,
            patch.object(preflight.transport, "_provider_child") as child,
        ):
            code, stdout, stderr = self.start("--max-cost-usd", "0")
        self.assertEqual(2, code, stdout + stderr)
        self.assertEqual(1, prepare.call_count, "a startup timeout must not silently retry")
        child.assert_not_called()
        self.assertEqual([], self.stages)
        view = self.status()
        self.assertEqual("PAUSED_TRANSPORT_UNVERIFIED", view["needs"].get("pause_origin") or view["status"])
        self.assertIn("before model access", view["stop_reason"])
        self.assertIn("debug', 'config", view["stop_reason"])
        self.assertFalse(view["done"])
        self.assertFalse((self.run_dir / "model-preflight.json").exists(), "no successful access receipt")
        attempts = view["usage"]["accounting"]["attempts"]
        self.assertEqual(1, len(attempts))
        self.assertTrue(attempts[0]["runner_owned"])
        self.assertEqual(30, attempts[0]["duration_seconds"])
        self.assertEqual(0, view["cost"]["spent_usd"])

    def test_reported_probe_cost_stops_before_another_request_and_is_public(self):
        code, stdout, stderr = self.start("--max-cost-usd", "0.01")
        self.assertEqual(2, code, stdout + stderr)
        self.assertEqual(1, len(self.probes), "The first probe already exceeded the saved money cap")
        self.assertEqual([], self.stages, "No ordinary stage may spend after the probe reached the cap")
        view = self.status()
        self.assertEqual("PAUSED_COST_LIMIT", view["needs"].get("pause_origin") or view["status"])
        self.assert_one_charged_probe(view)

    def fresh_joint_start(self, *limits):
        identity = {
            "engine": "budgetfixture",
            "identity_version": 1,
            "executable": sys.executable,
            "config_sha256": "a" * 64,
        }
        with patch.object(self.adapter, "local_settings", return_value=identity):
            return self.start(
                "--joint-planning",
                "--no-adaptive-planning",
                "--no-auto-select",
                "--requirements-model",
                "fixture/reviewer",
                *limits,
            )

    def use_kilo_selected_python(self):
        """Use the concrete readonly Kilo launch policy, with all native/model traffic fake."""
        config = {
            **self.adapter._config,
            "command": [sys.executable, "run", "--agent", "fixture", "--model", "{model}"],
        }
        self.adapter = KiloProvider(config, self.adapter._path)
        runner.autocode_providers.resolve.return_value = self.adapter

        # Readiness resolves native metadata from the configured command, not
        # the mocked model launch below. Keep its real capture-policy check,
        # while scripting a permitted agent so these cases reach ELF admission.
        def effective_agent(argv, *_args, **_kwargs):
            self.assertEqual([sys.executable, "run", "--agent", "fixture", "--model"], argv[:5])
            return argv, {"permission": [{"permission": "bash", "pattern": "*", "action": "allow"}]}

        self.stack.enter_context(patch.object(kilocode_policy, "effective_agent", side_effect=effective_agent))
        self.tools_readiness = self.stack.enter_context(
            patch.object(self.adapter, "check_tools", wraps=self.adapter.check_tools)
        )
        for name, value in (
            (
                "local_settings",
                {
                    "engine": "budgetfixture",
                    "identity_version": 1,
                    "executable": sys.executable,
                    "config_sha256": "a" * 64,
                },
            ),
            ("available_models", {"fixture/reviewer", "fixture/builder"}),
            ("check_models", None),
            ("check_subscription_routes", None),
        ):
            self.stack.enter_context(patch.object(self.adapter, name, return_value=value))
        binary = self.workspace / ".venv/bin"
        binary.mkdir(parents=True)
        self.selected_python = binary / "python"
        self.wrapper_marker = self.root / "wrapper-executed"
        self.selected_python.write_text("#!/bin/sh\ntouch " + shlex.quote(str(self.wrapper_marker)) + "\n")
        self.selected_python.chmod(0o755)
        self.stack.enter_context(patch.dict(os.environ, {"PATH": str(binary) + os.pathsep + os.environ["PATH"]}))
        self.native_observations = []

        class SelectedToolsReady(Exception):
            pass

        def version(command, **_kwargs):
            self.native_observations.append("version")
            return subprocess.CompletedProcess(command, 0, "7.8.3", "")

        def policy():
            self.native_observations.append("policy")
            return {"permission": [{"permission": "bash", "pattern": "*", "action": "allow"}]}

        def startup(*_args, **_kwargs):
            self.native_observations.append("startup")
            raise SelectedToolsReady

        def launch(_role, workspace, _run_dir, _session, _model, _effort, _write, **kwargs):
            # Keep actual late discovery and the public failure translator. The
            # deterministic native observations stop before any startup process.
            command = [sys.executable, "run", "--agent", "fixture", "--model", _model]
            child = {**kwargs["env"], "KILO_CONFIG_CONTENT": json.dumps({"agent": {"fixture": {}}})}
            with (
                patch.object(containment, "backend", return_value=containment.BUBBLEWRAP),
                patch.object(containment.subprocess, "run", side_effect=version),
                patch.object(containment, "_native_debug", return_value=policy),
                patch.object(containment, "prepare", side_effect=startup),
            ):
                try:
                    containment.configure(
                        command,
                        child,
                        workspace,
                        allow_write=False,
                        request=kwargs["containment"],
                        transport="kilocode",
                    )
                except SelectedToolsReady:
                    return command, child, {}
            raise AssertionError("The native startup stub was not reached")

        self.stack.enter_context(patch.object(self.adapter, "launch", side_effect=launch))

        def stage(**kwargs):
            self.stages.append(kwargs)
            runner.provider_launch.prepare(
                engine="opencode",
                adapter=self.adapter,
                role="requirements",
                route_role="requirements",
                workspace=self.workspace,
                run_dir=self.run_dir,
                session=None,
                model=kwargs["model"],
                effort="high",
                allow_write=False,
                planning=True,
                report=None,
                schema=None,
                prompt_file=None,
                sandbox="read-only",
                transport_args=[],
                chatgpt=True,
                provider="budgetfixture",
                settings=kwargs["state"]["settings"],
            )
            raise support.Paused("PAUSED_FIXTURE_STAGE", "Requirements tool discovery reached")

        runner.run_role.side_effect = stage

    def selected_python_start(self, *arguments):
        exact = tuple(
            argument
            for role in (
                "task_planner",
                "builder",
                "tester",
                "planner",
                "plan_reviewer",
                "requirements",
                "completion",
                "resolver",
            )
            for argument in (f"--{role.replace('_', '-')}-reasoning-effort", "high")
        )
        return self.fresh_joint_start("--max-cost-usd", "0", *exact, *arguments)

    @unittest.skipUnless(sys.platform == "linux", "selected ELF admission requires supported Linux")
    def test_selected_python_wrapper_pauses_before_probe_and_same_run_repairs(self):
        self.use_kilo_selected_python()
        with patch.object(toolchain, "discover", wraps=toolchain.discover) as discover:
            code, stdout, stderr = self.selected_python_start()
            self.tools_readiness.assert_called_once()
            self.assertEqual([], self.probes, stdout + stderr)
            self.assertEqual([], self.stages)
            view = self.status()
            self.assertEqual("PAUSED_TOOL_CONTAINMENT", view["needs"].get("pause_origin") or view["status"])
            self.assertIn("not a native ELF executable", view["stop_reason"])
            self.assertEqual([], view["usage"]["accounting"]["attempts"])
            self.assertEqual(1, discover.call_count)
            self.assertEqual([], self.native_observations)
            self.assertFalse(self.wrapper_marker.exists())
            self.assertFalse((self.run_dir / "model-preflight.json").exists())
            self.selected_python.unlink()
            self.selected_python.symlink_to(Path(sys.executable).resolve())
            code, stdout, stderr = self.invoke("--run-dir", str(self.run_dir), "--resume-paused", "--no-chat")
            self.assertEqual(2, code, stdout + stderr)
            self.assertEqual(2, self.tools_readiness.call_count, "Resume inspects the native policy again")
            self.assertEqual(["fixture/builder"], self.probes)
            self.assertEqual(1, len(self.stages))
            self.assertEqual(3, discover.call_count, "Resume checks early and again at the actual launch")
            self.assertEqual(["version", "policy", "startup"], self.native_observations)
            repaired = self.status()
            self.assertEqual("PAUSED_FIXTURE_STAGE", repaired["needs"].get("pause_origin") or repaired["status"])
            self.assertEqual(["high"], self.probe_efforts)
            self.assertFalse(self.wrapper_marker.exists())

    @unittest.skipUnless(sys.platform == "linux", "selected ELF admission requires supported Linux")
    def test_late_discovery_rejects_python_replaced_after_early_check(self):
        self.use_kilo_selected_python()
        wrapper = self.selected_python.read_text()
        self.selected_python.unlink()
        self.selected_python.symlink_to(Path(sys.executable).resolve())

        def replace():
            self.selected_python.unlink()
            self.selected_python.write_text(wrapper)
            self.selected_python.chmod(0o755)

        self.on_probe = replace
        with patch.object(toolchain, "discover", wraps=toolchain.discover) as discover:
            code, stdout, stderr = self.selected_python_start()
        self.tools_readiness.assert_called_once()
        self.assertEqual(2, code, stdout + stderr)
        self.assertEqual(["fixture/builder"], self.probes)
        self.assertEqual(1, len(self.stages))
        view = self.status()
        self.assertEqual("PAUSED_TOOL_CONTAINMENT", view["needs"].get("pause_origin") or view["status"])
        self.assertIn("not a native ELF executable", view["stop_reason"])
        self.assertEqual(2, discover.call_count, "The real launch must rediscover changed tool bytes")
        self.assert_one_charged_probe(view)
        self.assertFalse(self.wrapper_marker.exists())

    @unittest.skipUnless(sys.platform == "linux", "selected ELF admission requires supported Linux")
    def test_local_billing_refusal_precedes_selected_tool_discovery(self):
        self.use_kilo_selected_python()

        def auth(*_args, **_kwargs):
            if (self.run_dir / "state.json").is_file():
                raise RuntimeError("fixture login unavailable")

        self.adapter.check_subscription_routes.side_effect = auth
        with patch.object(toolchain, "discover", wraps=toolchain.discover) as discover:
            code, stdout, stderr = self.selected_python_start()
        self.tools_readiness.assert_called_once()
        self.assertEqual(2, code, stdout + stderr)
        discover.assert_not_called()
        self.assertEqual([], self.probes)
        view = self.status()
        self.assertEqual("PAUSED_BILLING_ROUTE", view["needs"].get("pause_origin") or view["status"])
        self.assertIn("fixture login unavailable", view["stop_reason"])

    @unittest.skipUnless(sys.platform == "linux", "selected ELF admission requires supported Linux")
    def test_budget_refusal_precedes_selected_tool_discovery(self):
        self.use_kilo_selected_python()
        with (
            patch.object(preflight, "_allowance", side_effect=support.Paused("PAUSED_COST_LIMIT", "fixture saved cap")),
            patch.object(toolchain, "discover", wraps=toolchain.discover) as discover,
        ):
            code, stdout, stderr = self.selected_python_start()
        self.tools_readiness.assert_called_once()
        self.assertEqual(2, code, stdout + stderr)
        discover.assert_not_called()
        self.assertEqual([], self.probes)
        view = self.status()
        self.assertEqual("PAUSED_COST_LIMIT", view["needs"].get("pause_origin") or view["status"])

    @unittest.skipUnless(sys.platform == "linux", "selected ELF admission requires supported Linux")
    def test_stop_at_selected_tool_pause_precedes_resume_discovery(self):
        self.use_kilo_selected_python()
        self.selected_python_start()
        self.tools_readiness.assert_called_once()
        self.submit_stop()
        with patch.object(toolchain, "discover", wraps=toolchain.discover) as discover:
            code, stdout, stderr = self.invoke("--run-dir", str(self.run_dir), "--resume-paused", "--no-chat")
        self.assertEqual(2, code, stdout + stderr)
        self.assertEqual(1, self.tools_readiness.call_count, "A saved stop takes precedence over readiness on resume")
        discover.assert_not_called()
        self.assertEqual([], self.probes)
        self.assertEqual("PAUSED_INTERVENTION", self.status()["status"])

    def test_remaining_probe_cost_prevents_the_deferred_real_requirements_request(self):
        code, stdout, stderr = self.fresh_joint_start("--max-cost-usd", "0.01")
        self.assertEqual(2, code, stdout + stderr)
        self.assertEqual(["fixture/builder"], self.probes)
        self.assertEqual([], self.stages)
        view = self.status()
        self.assertEqual("PAUSED_COST_LIMIT", view["needs"].get("pause_origin") or view["status"])
        self.assert_one_charged_probe(view)

    def test_stop_after_remaining_probe_prevents_the_deferred_real_requirements_request(self):
        self.on_probe = self.submit_stop
        code, stdout, stderr = self.fresh_joint_start("--max-cost-usd", "0")
        self.assertEqual(2, code, stdout + stderr)
        self.assertEqual(["fixture/builder"], self.probes)
        self.assertEqual([], self.stages)
        view = self.status()
        self.assertEqual("PAUSED_INTERVENTION", view["status"])
        self.assertIsNone(view["next_command"])
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
