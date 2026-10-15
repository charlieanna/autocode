"""Access-probe evidence survives interrupted publication and bounded cleanup."""

import contextlib
import datetime as dt
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_cost as cost
import autocode_model_preflight as preflight
import autocode_preflight_attempts as attempts
import autocode_process as processes
import autocode_run_records as records
import autocode_support as support
import autocode_usage as usage
import autocode_util as util


class ModelPreflightAttemptTests(unittest.TestCase):
    """Only transport and native observation are fake; receipts and usage are real."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="preflight-attempt-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.run_dir = self.root / "run"
        self.run_dir.mkdir()
        self.state_path = self.run_dir / "state.json"
        self.model = "fixture/access-model"
        self.adapter = SimpleNamespace(
            NAME="fixture",
            _config={"auth": {}},
            check_subscription_routes=lambda *args: None,
        )
        self.runner = SimpleNamespace(opencode=self.adapter, account_stage=records.account_stage)
        self.identity = {
            "engine": "fixture",
            "identity_version": 1,
            "config_sha256": "a" * 64,
        }
        self.state = {
            "settings": {
                "engine": "opencode",
                "roles": {"sol": {"model": self.model}},
                "transport_identities": {"opencode": self.identity},
            },
            "stages": [],
        }
        self.seconds = 0.0
        self.epoch = dt.datetime(2026, 10, 15, tzinfo=dt.UTC)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(util, "now", side_effect=self.now))
        self.stack.enter_context(
            patch.object(
                preflight,
                "time",
                SimpleNamespace(monotonic=lambda: self.seconds, gmtime=time.gmtime, strftime=time.strftime),
            )
        )
        self.live = self.stack.enter_context(patch.object(processes, "live_processes", return_value=[]))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def now(self):
        return (self.epoch + dt.timedelta(seconds=self.seconds)).isoformat()

    def checkpoint(self):
        util.atomic_json(self.state_path, self.state)

    def check(self):
        return preflight.check(
            self.runner,
            self.state,
            self.run_dir,
            self.root,
            checkpoint=self.checkpoint,
        )

    def write_events(self, directory, *, amount=0.03, partial=False):
        directory.mkdir(parents=True, exist_ok=True)
        rows = [
            {"type": "step_start", "sessionID": "probe", "part": {"id": "start"}},
            {
                "type": "step_finish",
                "sessionID": "probe",
                "part": {
                    "id": "finish",
                    "reason": "stop",
                    "cost": amount,
                    "tokens": {"input": 100, "output": 5, "reasoning": 0, "cache": {"read": 0, "write": 0}},
                },
            },
        ]
        if partial:
            rows.append({"type": "step_start", "sessionID": "probe", "part": {"id": "unfinished"}})
        path = directory / "events.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        return rows

    def ownership(self, directory, *, phase="stopped", observed=None):
        directory.mkdir(parents=True, exist_ok=True)
        observed = self.seconds + 2 if observed is None else observed
        metadata = {
            "schema": 1,
            "nonce": "a" * 32,
            "receipt": str(directory / "supervision.json"),
            "owner": {"pid": 101, "birth_identity": 1},
            "keeper": {"pid": 102, "birth_identity": 2},
            "provider": {"pid": 103, "birth_identity": 3},
        }
        receipt = {
            **metadata,
            "phase": phase,
            "cause": "owner_lost" if phase == "stopped" else None,
            "cleanup_error": None,
            "observed_at": (self.epoch + dt.timedelta(seconds=observed)).isoformat(),
            "processes": [metadata["provider"]],
        }
        util.atomic_json(Path(metadata["receipt"]), receipt)
        return metadata

    def successful_transport(self, **kwargs):
        directory = kwargs["directory"]
        self.write_events(directory)
        kwargs["checkpoint"](self.ownership(directory))
        self.seconds += 2
        return {
            "exit_code": 0,
            "timed_out": False,
            "metrics": support.event_metrics(directory / "events.jsonl"),
        }

    def pending_attempt(self, *, phase="stopped", observed=5):
        attempt = attempts.Attempt(
            self.runner,
            self.state,
            self.run_dir,
            self.adapter,
            self.model,
            self.checkpoint,
            lambda: self.seconds,
            preflight_identity=preflight._receipt_identity(self.adapter, self.identity),
        )
        self.write_events(attempt.directory)
        metadata = self.ownership(attempt.directory, phase=phase, observed=observed)
        attempt.admit(metadata)
        return attempt

    def assert_spend(self, amount, seconds, count=1):
        spent = cost.view(self.state)["spent_usd"]
        self.assertIsNotNone(spent, "Native provider-reported cost must remain visible to the run cap")
        self.assertAlmostEqual(amount, spent)
        self.assertAlmostEqual(seconds, self.state["active_seconds"])
        public = usage.accounting(self.state)["attempts"]
        self.assertEqual(count, len(public))
        self.assertEqual(count, len({row["identity"] for row in public}))
        self.assertAlmostEqual(seconds, sum(row["duration_seconds"] for row in public))
        return public

    def test_lost_success_marker_uses_saved_attempt_without_reprobe_or_double_charge(self):
        with patch.object(preflight.transport, "execute", side_effect=self.successful_transport) as execute:
            self.assertTrue(self.check())
            (self.run_dir / "model-preflight.json").unlink()
            self.state = json.loads(self.state_path.read_text())
            self.assertFalse(self.check())
            self.assertEqual(1, execute.call_count)
        self.assert_spend(0.03, 2)

    def test_failed_marker_publication_keeps_the_charged_success_durable(self):
        with (
            patch.object(preflight.transport, "execute", side_effect=self.successful_transport) as execute,
            patch.object(preflight, "atomic_json", side_effect=OSError("marker unavailable")),
        ):
            with self.assertRaisesRegex(OSError, "marker unavailable"):
                self.check()
        self.state = json.loads(self.state_path.read_text())
        with patch.object(preflight.transport, "execute") as resumed:
            self.assertFalse(self.check())
            resumed.assert_not_called()
        self.assertEqual(1, execute.call_count)
        self.assert_spend(0.03, 2)

    def test_interruption_retains_partial_cost_and_time_before_pausing(self):
        def interrupted(**kwargs):
            self.write_events(kwargs["directory"], partial=True)
            kwargs["checkpoint"](self.ownership(kwargs["directory"]))
            self.seconds += 2
            raise KeyboardInterrupt("SIGTERM")

        with patch.object(preflight.transport, "execute", side_effect=interrupted):
            with self.assertRaises(support.Paused) as stopped:
                self.check()
        self.assertEqual("PAUSED_INTERRUPTED", stopped.exception.status)
        self.state = json.loads(self.state_path.read_text())
        public = self.assert_spend(0.03, 2)
        self.assertTrue(public[0]["interrupted"])
        self.assertFalse(cost.view(self.state)["complete"])

    def test_failed_transport_preserves_reported_cost_and_elapsed_time(self):
        def failed(**kwargs):
            self.write_events(kwargs["directory"])
            kwargs["checkpoint"](self.ownership(kwargs["directory"]))
            self.seconds += 2
            raise processes.ProcessError("provider cleanup failed")

        with patch.object(preflight.transport, "execute", side_effect=failed):
            with self.assertRaises(support.Paused) as stopped:
                self.check()
        self.assertEqual("PAUSED_PROCESS_CLEANUP", stopped.exception.status)
        self.state = json.loads(self.state_path.read_text())
        self.assert_spend(0.03, 2)

    def test_stop_arriving_with_verified_clean_failure_is_consumed_after_charging(self):
        pending_stop = False

        def failed(**kwargs):
            nonlocal pending_stop
            self.write_events(kwargs["directory"])
            kwargs["checkpoint"](self.ownership(kwargs["directory"]))
            self.seconds += 2
            pending_stop = True
            raise processes.ProcessError("provider interrupted after verified cleanup")

        def boundary():
            if pending_stop:
                raise support.Paused("PAUSED_INTERVENTION", "Durable stop applied")

        with patch.object(preflight.transport, "execute", side_effect=failed):
            with self.assertRaises(support.Paused) as stopped:
                preflight.check(
                    self.runner,
                    self.state,
                    self.run_dir,
                    self.root,
                    checkpoint=self.checkpoint,
                    before_probe=boundary,
                )
        self.assertEqual("PAUSED_INTERVENTION", stopped.exception.status)
        self.state = json.loads(self.state_path.read_text())
        self.assert_spend(0.03, 2)

    def test_stop_arriving_with_unknown_cleanup_is_withheld_until_ownership_settles(self):
        pending_stop = False
        stop_applied = False

        def failed(**kwargs):
            nonlocal pending_stop
            self.write_events(kwargs["directory"])
            kwargs["checkpoint"](self.ownership(kwargs["directory"], phase="armed"))
            self.seconds += 2
            pending_stop = True
            raise processes.ProcessError("provider cleanup remains unknown")

        def boundary():
            nonlocal stop_applied
            if pending_stop:
                stop_applied = True
                raise support.Paused("PAUSED_INTERVENTION", "Durable stop applied")

        with patch.object(preflight.transport, "execute", side_effect=failed):
            with self.assertRaises(support.Paused) as stopped:
                preflight.check(
                    self.runner,
                    self.state,
                    self.run_dir,
                    self.root,
                    checkpoint=self.checkpoint,
                    before_probe=boundary,
                )
        self.assertEqual("PAUSED_PROCESS_CLEANUP", stopped.exception.status)
        self.assertFalse(stop_applied, "Unknown provider ownership must retain its cleanup hold")
        self.state = json.loads(self.state_path.read_text())
        self.assert_spend(0.03, 2)

    def test_failed_admission_is_known_zero_spend_with_retained_timing(self):
        def refused(**kwargs):
            self.seconds += 0.5
            raise processes.ProcessError("session boundary unavailable; no provider launched")

        with patch.object(preflight.transport, "execute", side_effect=refused):
            with self.assertRaises(support.Paused):
                self.check()
        self.state = json.loads(self.state_path.read_text())
        self.assert_spend(0, 0.5)
        self.assertEqual(0, cost.view(self.state)["unpriced_stages"])

    def test_pending_attempt_with_unknown_or_live_native_ownership_blocks_retry(self):
        self.pending_attempt()
        for observation in (processes.ProcessError("native inspection denied"), ["live provider"]):
            with self.subTest(observation=observation):
                self.live.side_effect = observation if isinstance(observation, Exception) else None
                self.live.return_value = observation
                with patch.object(preflight.transport, "execute") as execute:
                    with self.assertRaises(support.Paused) as stopped:
                        self.check()
                    self.assertEqual("PAUSED_PROCESS_CLEANUP", stopped.exception.status)
                    execute.assert_not_called()

    def test_verified_pending_attempt_settles_once_and_event_prefix_never_caches_pass(self):
        self.pending_attempt(observed=5)
        self.seconds = 5
        with patch.object(preflight.transport, "execute", side_effect=self.successful_transport) as execute:
            self.assertTrue(self.check(), "Interrupted event prefix must require a fresh access response")
            self.assertEqual(1, execute.call_count)
            self.state = json.loads(self.state_path.read_text())
            self.assertFalse(self.check())
            self.assertEqual(1, execute.call_count)
        public = self.assert_spend(0.06, 7, count=2)
        self.assertTrue(public[0]["interrupted"])
        self.assertFalse(public[1]["interrupted"])

    def test_delayed_cleanup_charges_only_the_remaining_elapsed_time_once(self):
        attempt = self.pending_attempt(phase="armed", observed=3)
        self.seconds = 3
        attempt.finish(error=processes.ProcessError("cleanup pending"))
        self.assert_spend(0.03, 3)
        self.ownership(attempt.directory, observed=5)
        self.seconds = 5
        attempts.reconcile(self.runner, self.state, self.run_dir, self.checkpoint)
        self.assert_spend(0.03, 5)
        self.state = json.loads(self.state_path.read_text())
        attempts.reconcile(self.runner, self.state, self.run_dir, self.checkpoint)
        self.assert_spend(0.03, 5)

    @contextlib.contextmanager
    def transport_preparation(self, seconds):
        def launch(*args, **kwargs):
            self.seconds += seconds
            return ["fixture-provider"], kwargs["env"], {}

        adapter = SimpleNamespace(launch=launch, final_report=lambda *args, **kwargs: {"ok": True})
        transport = preflight.transport
        with (
            patch.object(transport, "time", SimpleNamespace(monotonic=lambda: self.seconds), create=True),
            patch.object(transport.env_prep, "resolve_executable", return_value=sys.executable),
            patch.object(transport.processes, "interruption_handler", contextlib.nullcontext),
            patch.object(
                transport, "_provider_child", return_value=contextlib.nullcontext(SimpleNamespace(returncode=0))
            ) as child,
            patch.object(transport.processes, "wait_for_stage", return_value=(0, False)) as wait,
        ):
            yield adapter, child, wait

    def execute_until(self, adapter, deadline):
        return preflight.transport.execute(
            adapter=adapter,
            workspace=self.root,
            directory=self.run_dir / "transport-preparation",
            model=self.model,
            effort="low",
            session=None,
            allow_write=False,
            prompt=preflight.PROMPT,
            schema=preflight.SCHEMA,
            env=dict(os.environ),
            timeout=90,
            supervised=True,
            deadline=deadline,
        )

    def test_preparation_that_exhausts_the_deadline_refuses_provider_launch(self):
        with self.transport_preparation(2) as (adapter, child, wait):
            with self.assertRaises(support.Paused) as stopped:
                self.execute_until(adapter, deadline=1)
            self.assertEqual("PAUSED_TIME_LIMIT", stopped.exception.status)
            child.assert_not_called()
            wait.assert_not_called()

    def test_preparation_deducts_elapsed_time_from_owned_provider_timeout(self):
        with self.transport_preparation(2) as (adapter, child, wait):
            result = self.execute_until(adapter, deadline=5)
            self.assertEqual(0, result["exit_code"])
            self.assertEqual(3, child.call_args.args[2])
            self.assertEqual(3, wait.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
