"""A lost supervised checkpoint has a safe, bound public reconciliation action."""

import copy
import unittest
from pathlib import Path

import autocode_client_actions as client
import autocode_run_view as run_view

from .test_liveness import inspection, metadata


def checkpoint():
    return {
        "status": "RUNNING",
        "workspace": "/w",
        "run_dir": "/r",
        "settings": {"engine": "opencode", "provider": "kilocode", "limits": {"max_cost_usd": 0.75}},
        "active_stage": {
            "stage": "review_change",
            "role": "sol",
            "iteration": 1,
            "output": "/r/iterations/001/review-change-01.json",
            "events": "/r/iterations/001/review-change-01.jsonl",
            "supervision": metadata(),
        },
    }


class CheckpointReconciliationTests(unittest.TestCase):
    def test_confirmed_dead_supervision_names_a_private_model_free_action(self):
        state = checkpoint()
        before = copy.deepcopy(state)
        fresh = inspection(state["active_stage"]["supervision"], owner=False, keeper=False, provider=False)
        view = run_view.view(state, run_dir=Path("/r"), workspace=Path("/w"), liveness=fresh)
        command = client.named(view, Path("/w"), Path("/r"))
        self.assertEqual(["--reconcile-only", "-"], command.argv)
        self.assertTrue(command.env.get("AUTOCODE_RECONCILE_ONLY_TOKEN"))
        self.assertEqual("continue", view["needs"]["kind"])
        self.assertEqual(before, state, "The status projection must remain read-only")

    def test_live_unknown_or_finished_supervision_does_not_offer_reconciliation(self):
        for fault in (
            "live_owner",
            "live_keeper",
            "live_provider",
            "unknown",
            "finished",
            "runner_check",
            "visual",
            "durable",
        ):
            with self.subTest(fault=fault):
                state = checkpoint()
                fresh = inspection(state["active_stage"]["supervision"], owner=False, keeper=False, provider=False)
                if fault.startswith("live_"):
                    fresh[fault.removeprefix("live_")]["alive"] = True
                elif fault == "unknown":
                    fresh["owner"] = {"checked": False, "alive": None}
                elif fault == "finished":
                    state["active_stage"]["finished_at"] = "2026-10-27T17:35:00Z"
                elif fault == "durable":
                    state["execution"] = {"mode": "durable_local"}
                elif fault == "visual":
                    state["ui_run"] = "/original/native-handoff"
                else:
                    state["active_runner_check"] = {"stage": "check"}
                view = run_view.view(state, run_dir=Path("/r"), workspace=Path("/w"), liveness=fresh)
                self.assertNotIn("--reconcile-only", view.get("next_command") or "")

    def test_controller_liveness_and_changed_checkpoint_reject_stale_authority(self):
        import autocode_checkpoint_reconciliation as reconciliation

        state = checkpoint()
        fresh = inspection(state["active_stage"]["supervision"], owner=False, keeper=False, provider=False)
        for kind in ("running", "busy", "unknown", "ready"):
            self.assertIsNone(reconciliation.offer(state, fresh, controller={"kind": kind}))
        self.assertIsNotNone(reconciliation.offer(state, fresh, controller={"kind": "ended"}))
        expected = reconciliation.token(state)
        for key, value in (
            ("settings", {}),
            ("active_seconds", 1),
            ("source_revision", "changed"),
            ("goal_contract", {"changed": True}),
        ):
            changed = copy.deepcopy(state)
            changed[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "checkpoint changed"):
                reconciliation.decision(changed, expected, fresh)


class CheckpointCliTests(unittest.TestCase):
    """Real CLI control calls, with no installed provider required or model launched."""

    def setUp(self):
        import json
        import subprocess
        import tempfile

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name).resolve()
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        self.run = self.workspace / ".autocode/runs/interrupted"
        self.run.mkdir(parents=True)
        self.path = self.run / "state.json"
        self.state = checkpoint()
        self.state.update(
            version=3,
            task="Review the existing patch",
            workspace=str(self.workspace),
            run_dir=str(self.run),
            iteration=1,
            sessions={},
            stages=[],
            active_seconds=123.0,
            automatic_recoveries_since_resume=2,
            automatic_timeout_recoveries=[{"attempt": "retained", "seconds": 30}],
            recovery_grants=[{"count": 1, "reason": "prior owner grant"}],
        )
        self.state["settings"]["provider"] = "provider-that-is-not-installed"
        self.state["active_stage"]["output"] = str(self.run / "iterations/001/review-change-01.json")
        self.state["active_stage"]["events"] = str(self.run / "iterations/001/review-change-01.jsonl")
        saved = self.state["active_stage"]["supervision"]
        saved["receipt"] = str(self.run / "missing-receipt.json")
        for role in ("owner", "keeper", "provider"):
            saved[role]["pid"] = 2**22 - 1
        self.path.write_text(json.dumps(self.state))
        self.source = self.workspace / "source.txt"
        self.source.write_text("retained partial work\n")

    def invoke(self, *args, env=None):
        import os
        import subprocess
        import sys

        return subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "tools/autocode.py"),
                "--workspace",
                str(self.workspace),
                "--run-dir",
                str(self.run),
                *args,
            ],
            env={**os.environ, **(env or {})},
            text=True,
            capture_output=True,
            timeout=30,
        )

    def test_named_control_pauses_without_provider_configuration_or_usage_reset(self):
        import json

        status = self.invoke("--status", "--json")
        self.assertEqual(0, status.returncode, status.stderr)
        data = json.loads(status.stdout)
        view = data["view"]
        command = client.named(view, self.workspace, self.run)
        self.assertEqual(["--reconcile-only", "-"], command.argv)
        result = self.invoke(*command.argv, env=command.env)
        self.assertEqual(2, result.returncode, result.stderr + result.stdout)
        current = json.loads(self.path.read_text())
        self.assertEqual("PAUSED_UNCERTAIN_STAGE", current["status"], result.stdout + result.stderr)
        for key in (
            "settings",
            "active_stage",
            "stages",
            "active_seconds",
            "automatic_recoveries_since_resume",
            "automatic_timeout_recoveries",
            "recovery_grants",
        ):
            self.assertEqual(self.state[key], current[key], key)
        self.assertEqual("retained partial work\n", self.source.read_text())
        self.assertFalse((self.run / "iterations").exists(), "No provider or report artifact may be created")

    def test_stale_token_and_combined_settings_refuse_without_saving(self):
        import autocode_checkpoint_reconciliation as reconciliation

        token = reconciliation.token(self.state)
        original = self.path.read_bytes()
        for args in (
            ("--reconcile-only", "stale"),
            ("--reconcile-only", token, "--max-cost-usd", "100"),
            ("--reconcile-only", token, "--resume-paused"),
        ):
            with self.subTest(args=args[:1]):
                result = self.invoke(*args)
                self.assertEqual(2, result.returncode)
                self.assertEqual(original, self.path.read_bytes(), result.stderr + result.stdout)

    def test_process_revived_since_view_refuses_without_replay(self):
        import json
        import os

        import autocode_checkpoint_reconciliation as reconciliation
        import autocode_process as processes

        # A stale view cannot authorize classification if a role is live now.
        live = processes.identity(processes.process_table({os.getpid()})[os.getpid()])
        self.state["active_stage"]["supervision"]["provider"] = live
        self.path.write_text(json.dumps(self.state))
        original = self.path.read_bytes()
        result = self.invoke("--reconcile-only", reconciliation.token(self.state))
        self.assertEqual(2, result.returncode)
        current = json.loads(self.path.read_text())
        self.assertEqual("RUNNING", current["status"], result.stderr + result.stdout)
        self.assertEqual(original, self.path.read_bytes())

    def test_pending_stop_is_preserved_and_refuses_reconciliation(self):
        import autocode_checkpoint_reconciliation as reconciliation
        import autocode_interventions as interventions

        interventions.submit(self.workspace, self.run, request_id="owner-stop", kind="stop", text="")
        original = self.path.read_bytes()
        result = self.invoke("--reconcile-only", reconciliation.token(self.state))
        self.assertEqual(2, result.returncode)
        self.assertIn("stop is pending", result.stderr.lower())
        self.assertEqual(original, self.path.read_bytes())
        self.assertEqual("owner-stop", interventions.pending(self.run)[0]["id"])
