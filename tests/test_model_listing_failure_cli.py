"""A real saved-run CLI admission retains a typed, model-free roster failure.

The provider is the actual CommandProvider, with NAME ``kilocode`` and the exact
plain ``["kilo", "models"]`` producer. Only its model/report executables are
offline fixtures. The fault narrows those two actual listing subprocesses to a
zero-second fixture timeout; the producer still requests its unchanged 30/60
allowances. subprocess.run really kills and reaps each direct child. This proves
neither descendant cleanup nor a successful stage, so the outcome stays Blocked.

All source/config/marker/report paths are allocated below this test's fresh root.
No live credentials, provider, model, frozen fixture or private-state mutation.
The supervised healthy planning setup requires supported Linux. A UI fixture can
reuse this exact provider/roster arrangement, arm only its own marker after its
actual public plan approval, and take one public Continue through the extension.
"""

import copy
import json
import os
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_client_actions as client_actions
import autocode_providers as providers
import autocode_source_snapshot as source
import autocode_taskrun as taskrun
import inprocess_cli
from providers import command

from tests import test_command_flow


class ModelListingFailureCLI(unittest.TestCase):
    def setUp(self):
        # Compose setup only: inheriting ConfigToolFlow would collect its tests.
        self.fixture = test_command_flow.ConfigToolFlow(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.configure_fixture(track_invocations=True)
        self.root, self.project = self.fixture.root, self.fixture.project
        self.tools = Path(__file__).resolve().parents[1] / "tools"
        self.marker = self.root / "config/kilo/roster-timeout-enabled.json"
        self.marker.parent.mkdir()
        self.counter = 0
        self.fixture.env.update(
            HOME=str(self.root / "home"),
            XDG_DATA_HOME=str(self.root / "data"),
            PATH=os.pathsep.join((str(self.root / "bin"), str(Path(sys.executable).parent), os.environ["PATH"])),
        )
        (self.root / "home").mkdir()
        (self.root / "data").mkdir()
        original = self.root / "config/autocode/providers/fixturetool.toml"
        text = original.read_text().replace('name = "fixturetool"', 'name = "kilocode"')
        models = tomllib.loads(text)["models"]
        text = (
            "\n".join(
                'models_command = ["kilo", "models"]' if line.strip().startswith("models =") else line
                for line in text.splitlines()
            )
            + "\n"
        )
        config = self.root / "config/autocode/providers/offline-kilo.toml"
        config.write_text(text)
        self.provider = command.CommandProvider(tomllib.loads(text), config)
        roster = self.root / "bin/kilo"
        roster.write_text(
            "#!/usr/bin/env python3\n"
            "import signal, sys\nfrom pathlib import Path\n"
            "if sys.argv[1:] != ['models']: raise SystemExit(2)\n"
            f"if Path({str(self.marker)!r}).exists(): signal.pause()\n"
            f"print('\\n'.join({models!r}))\n"
        )
        roster.chmod(0o755)
        resolve = providers.resolve
        self.enterContext(
            patch.object(
                providers, "resolve", side_effect=lambda name: self.provider if name == "kilocode" else resolve(name)
            )
        )
        self.enterContext(patch.object(taskrun, "run_process", side_effect=self.recorded_cli))

    def recorded_cli(self, argv, **kwargs):
        result = inprocess_cli.run(argv, **kwargs)
        self.counter += 1
        (self.root / f"public-cli-{self.counter:02d}.json").write_text(
            json.dumps(
                {"argv": list(argv), "exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr},
                indent=2,
            )
            + "\n"
        )
        return result

    def public(self, *words, codes=(0,)):
        result = self.recorded_cli(
            [sys.executable, str(self.tools / "autocode.py"), *words],
            cwd=self.root,
            env=self.fixture.env,
        )
        self.assertIn(result.returncode, codes, result.stdout + result.stderr)
        return result

    def document(self, run):
        return json.loads(
            self.public("--workspace", str(self.project), "--run-dir", str(run.run_dir), "--status").stdout
        )

    def planned_run(self):
        run = taskrun.TaskRun.start(
            self.project,
            "Build a greeting tool",
            command=(sys.executable, str(self.tools / "autoplanner.py")),
            options=(
                "--provider",
                "kilocode",
                "--engine",
                "opencode",
                "--max-seconds",
                "600",
                "--max-stage-seconds",
                "120",
                "--max-cost-usd",
                "0",
                "--max-planning-calls",
                "20",
            ),
            env=self.fixture.env,
            cwd=self.root,
        )
        for _ in range(8):
            view = run.status()
            need = view.get("needs") or {}
            if need.get("kind") == "approve_plan":
                break
            if need.get("kind") == "answer":
                self.assertEqual(["Q1"], [row["id"] for row in need["questions"]])
                run.answer("Q1", "CLI", resolver_token=need.get("resolver_token"))
            elif need.get("kind") == "continue":
                run.advance()
            else:
                self.fail(f"Unexpected healthy fixture gate: {need}")
        else:
            self.fail("The offline planning fixture did not reach approval")
        plan = client_actions.plan_for_approval(view)
        self.assertIsNotNone(plan)
        self.assertIn("Provide a deterministic greeting CLI", json.dumps(plan))
        self.assertEqual(["C1"], [row["id"] for row in plan["acceptance_criteria"]])
        token = view["needs"]["token"]
        exported = self.root / "reviewed-plan.md"
        run.export_plan(exported)
        checked = json.loads(self.public("plan", "--check", str(exported), "--json").stdout)
        self.assertTrue(checked["matches"])
        (self.root / "agent-plan-review.json").write_text(
            json.dumps(
                {
                    "actor": "Codex agent review under owner delegation in AGENTS.md",
                    "token": token,
                    "whole_displayed_plan": plan,
                    "exported_plan": exported.read_text(),
                },
                indent=2,
            )
            + "\n"
        )
        run.approve_plan(token)
        return taskrun.TaskRun(self.project, run.run_dir, env=self.fixture.env, cwd=self.root)

    def assert_blocked(self, document):
        view = document["view"]
        self.assertEqual("PAUSED_TRANSPORT_UNVERIFIED", document["status"])
        self.assertFalse(view["done"])
        self.assertIsNone(view["needs"])
        self.assertIsNone(view["next_command"])
        self.assertEqual("blocked", view["recovery"]["category"])
        self.assertEqual("blocked", view["summary"]["state"])
        self.assertEqual(["inspect"], [row["kind"] for row in view["recovery"]["actions"]])
        self.assertEqual(
            "AutoCode could not confirm that its startup check has stopped. It kept the saved work and will not start another step.",
            view["recovery"]["what_happened"],
        )
        self.assertNotIn("resume", json.dumps(view["explanation"]).lower())
        self.assertEqual(0, view["usage"]["accounting"]["active_attempts"])

    def test_roster_timeout_blocks_saved_run_and_cannot_be_released_by_resume_or_settings(self):
        run = self.planned_run()
        before = self.document(run)
        source_before = source.snapshot(self.project)
        calls = (run.run_dir / "invocations.txt").read_bytes()
        self.assertTrue(before["view"]["usage"]["accounting"]["attempts"])
        self.assertIsNotNone(before["view"]["approved_contract"])
        self.assertEqual("kilocode", self.provider.NAME)
        self.assertEqual(["kilo", "models"], self.provider._config["models_command"])
        # The existing cache keys the listing on this owned tool-config
        # inventory. Arming the fixture invalidates the healthy cached roster
        # through its real identity rule, without deleting cache/private state.
        self.marker.write_text('{"fixture_timeout": true}\n')
        requested_bounds = []
        real_run = subprocess.run

        def roster_timeout(argv, *args, **kwargs):
            if argv == ["kilo", "models"]:
                requested_bounds.append(kwargs["timeout"])
                # Keep the public producer's configured30/60 bounds unchanged.
                # Only this exact owned blocking fixture child gets an immediate
                # actual timeout, followed by subprocess.run's kill/wait cleanup.
                return real_run(argv, *args, **{**kwargs, "timeout": 0})
            return real_run(argv, *args, **kwargs)

        identity = ["--workspace", str(self.project), "--run-dir", str(run.run_dir)]
        with patch.object(subprocess, "run", side_effect=roster_timeout):
            failed = self.public(*identity, "--no-chat", "--json", codes=(2,))
            self.assertIn("AutoCode is blocked", failed.stdout + failed.stderr)
            held = self.document(run)
            self.assert_blocked(held)
            self.assertEqual([30, 60], requested_bounds)
            self.assertEqual(before["settings"], held["settings"])
            self.assertEqual(before["view"]["routes"], held["view"]["routes"])
            self.assertEqual(before["view"]["approvals"], held["view"]["approvals"])
            self.assertEqual(before["contract_token"], held["contract_token"])
            self.assertEqual(before["next_stage"], held["next_stage"])
            self.assertEqual(before["view"]["approved_contract"], held["view"]["approved_contract"])
            old_attempts = before["view"]["usage"]["accounting"]["attempts"]
            held_attempts = held["view"]["usage"]["accounting"]["attempts"]
            for old in old_attempts:
                self.assertIn(old, held_attempts)
            self.assertEqual(
                before["view"]["usage"]["accounting"]["provider_requests"],
                held["view"]["usage"]["accounting"]["provider_requests"],
            )
            self.public(*identity, "--resume-paused", "--no-chat", "--json", codes=(2,))
            self.assert_blocked(self.document(run))
            self.public(*identity, "--resume-paused", "--max-seconds", "601", "--no-chat", "--json", codes=(2,))
            after = self.document(run)
        self.assert_blocked(after)
        expected = copy.deepcopy(before["settings"])
        expected["limits"]["max_seconds"] = 601
        self.assertEqual(expected, after["settings"])
        self.assertEqual(before["view"]["routes"], after["view"]["routes"])
        self.assertEqual(before["view"]["approvals"], after["view"]["approvals"])
        self.assertEqual(before["contract_token"], after["contract_token"])
        self.assertEqual(before["next_stage"], after["next_stage"])
        self.assertEqual(before["view"]["approved_contract"], after["view"]["approved_contract"])
        for old in old_attempts:
            self.assertIn(old, after["view"]["usage"]["accounting"]["attempts"])
        self.assertEqual(
            before["view"]["usage"]["accounting"]["provider_requests"],
            after["view"]["usage"]["accounting"]["provider_requests"],
        )
        self.assertEqual([30, 60], requested_bounds, "Held resumes must not relist or run a provider")
        self.assertEqual(calls, (run.run_dir / "invocations.txt").read_bytes())
        self.assertEqual(source_before, source.snapshot(self.project))
