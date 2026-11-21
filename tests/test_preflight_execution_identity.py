"""#3867: a metered model access probe records the same launch facts as an ordinary stage.

Ordinary stages get a schema-1 ``execution_identity`` from ``autocode_route_evidence.launch_identity``;
a probe attempt had none, so public ``usage.accounting.attempts`` rows showed ``{}``, and a Kilo
probe named provider ``opencode``. Only the transport and native process observation are fake:
no model is called, and receipts and usage are real.
"""

import contextlib
import datetime as dt
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_model_preflight as preflight
import autocode_preflight_attempts as attempts
import autocode_process as processes
import autocode_route_evidence as route_evidence
import autocode_run_records as records
import autocode_support as support
import autocode_usage as usage
import autocode_util as util

MODEL = "fixture/access-model"


def adapters():
    """(case, adapter, transport identity, the provider its receipt names)."""
    auth = {"auth": {}}

    def login(*args):
        return None

    return (
        (
            "opencode",
            SimpleNamespace(NAME="opencode", _config=auth, check_subscription_routes=login),
            {"engine": "opencode", "identity_version": 1, "config_sha256": "a" * 64},
            "opencode",
        ),
        (
            "kilocode adapter name",
            SimpleNamespace(NAME="kilocode", _config=auth, check_subscription_routes=login),
            {"engine": "kilocode", "identity_version": 1, "config_sha256": "b" * 64},
            "kilocode",
        ),
        (
            "kilocode transport engine",
            SimpleNamespace(_config=auth, check_subscription_routes=login),
            {"engine": "kilocode", "identity_version": 1, "config_sha256": "c" * 64},
            "kilocode",
        ),
    )


class PreflightExecutionIdentityTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.seconds = 0.0
        self.epoch = dt.datetime(2026, 10, 15, tzinfo=dt.UTC)
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

    def fixture(self, adapter, identity):
        temporary = tempfile.TemporaryDirectory(prefix="preflight-identity-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.run_dir = self.root / "run"
        self.run_dir.mkdir()
        self.state_path = self.run_dir / "state.json"
        self.seconds = 0.0
        self.runner = SimpleNamespace(opencode=adapter, account_stage=records.account_stage)
        self.state = {
            "settings": {
                "engine": "opencode",
                "roles": {"tester": {"model": MODEL, "reasoning_effort": "high"}},
                "effort_origins": {"tester": "user_explicit"},
                "transport_identities": {"opencode": identity},
            },
            "stages": [],
        }

    def now(self):
        return (self.epoch + dt.timedelta(seconds=self.seconds)).isoformat()

    def checkpoint(self):
        util.atomic_json(self.state_path, self.state)

    def check(self):
        return preflight.check(self.runner, self.state, self.run_dir, self.root, checkpoint=self.checkpoint)

    def write_events(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        rows = [
            {"type": "step_start", "sessionID": "probe", "part": {"id": "start"}},
            {
                "type": "step_finish",
                "sessionID": "probe",
                "part": {
                    "id": "finish",
                    "reason": "stop",
                    "cost": 0.03,
                    "tokens": {"input": 100, "output": 5, "reasoning": 0, "cache": {"read": 0, "write": 0}},
                },
            },
        ]
        (directory / "events.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))

    def ownership(self, directory, *, phase="stopped"):
        directory.mkdir(parents=True, exist_ok=True)
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
            "observed_at": (self.epoch + dt.timedelta(seconds=self.seconds + 2)).isoformat(),
            "processes": [metadata["provider"]],
        }
        util.atomic_json(Path(metadata["receipt"]), receipt)
        return metadata

    def answered(self, **kwargs):
        self.efforts.append(kwargs["effort"])
        directory = kwargs["directory"]
        self.write_events(directory)
        kwargs["checkpoint"](self.ownership(directory))
        self.seconds += 2
        return {"exit_code": 0, "timed_out": False, "metrics": support.event_metrics(directory / "events.jsonl")}

    def expected(self, provider):
        return route_evidence.launch_identity(
            role="model_preflight",
            engine="opencode",
            provider=provider,
            model=MODEL,
            effort="high",
            runtime_commit=util.runner_identity().get("commit"),
            settings=self.state["settings"],
        )

    def assert_identity(self, row, provider):
        identity = row["execution_identity"]
        self.assertTrue(route_evidence.valid_execution_identity(identity), identity)
        self.assertEqual(self.expected(provider), identity)
        self.assertEqual(
            ("model_preflight", "opencode", provider, MODEL, "high"),
            (
                identity["role"],
                identity["engine"],
                identity["provider"],
                identity["model"],
                identity["reasoning_effort"],
            ),
        )
        self.assertEqual(
            (None, "unrecorded", None), (identity["billing_kind"], identity["billing_basis"], identity["endpoint"])
        )

    def launch_route(self, provider, effort="high"):
        return {"engine": "opencode", "provider": provider, "model": MODEL, "effort": effort}

    def preflight_rows(self):
        rows = usage.summary(self.state)["accounting"]["attempts"]
        return [row for row in rows if row["stage"] == "model_preflight"]

    def test_ac4_finished_preflight_attempt_has_valid_execution_identity(self):
        for case, adapter, identity, provider in adapters():
            with self.subTest(case=case):
                self.fixture(adapter, identity)
                self.efforts = []
                with patch.object(preflight.transport, "execute", side_effect=self.answered):
                    self.assertTrue(self.check())
                self.assertEqual(["high"], self.efforts)
                self.state = json.loads(self.state_path.read_text())
                [record] = self.state["stages"]
                self.assertEqual("ok", record["preflight_outcome"]["status"])
                self.assertEqual(provider, record["launch_route"]["provider"])
                self.assertEqual("opencode", record["engine"])
                self.assertEqual(self.launch_route(provider), record["launch_route"])
                [row] = self.preflight_rows()
                self.assert_identity(row, provider)

    def test_ac5_interrupted_preflight_attempt_keeps_valid_execution_identity(self):
        for case, adapter, identity, provider in adapters():
            with self.subTest(case=case, path="interrupted"):
                self.fixture(adapter, identity)

                def interrupted(**kwargs):
                    self.write_events(kwargs["directory"])
                    kwargs["checkpoint"](self.ownership(kwargs["directory"]))
                    self.seconds += 2
                    raise KeyboardInterrupt("SIGTERM")

                with patch.object(preflight.transport, "execute", side_effect=interrupted):
                    with self.assertRaises(support.Paused) as stopped:
                        self.check()
                self.assertEqual("PAUSED_INTERRUPTED", stopped.exception.status)
                self.state = json.loads(self.state_path.read_text())
                [record] = self.state["stages"]
                self.assertTrue(record["interrupted"])
                self.assertEqual(provider, record["launch_route"]["provider"])
                self.assertEqual(self.launch_route(provider), record["launch_route"])
                self.assertEqual("opencode", record["engine"])
                [row] = self.preflight_rows()
                self.assertTrue(row["interrupted"])
                self.assert_identity(row, provider)

            with self.subTest(case=case, path="recovered"):
                self.fixture(adapter, identity)
                self.efforts = []

                def cleanup_pending(**kwargs):
                    self.write_events(kwargs["directory"])
                    kwargs["checkpoint"](self.ownership(kwargs["directory"], phase="armed"))
                    self.seconds += 2
                    raise processes.ProcessError("provider cleanup remains unknown")

                with patch.object(preflight.transport, "execute", side_effect=cleanup_pending):
                    with self.assertRaises(support.Paused) as stopped:
                        self.check()
                self.assertEqual("PAUSED_PROCESS_CLEANUP", stopped.exception.status)
                self.state = json.loads(self.state_path.read_text())
                [pending] = self.state["stages"]
                self.assertFalse(pending["preflight_complete"])
                # Cleanup is now confirmed; the next check settles the left probe before probing again.
                self.ownership(Path(pending["events"]).parent)
                with patch.object(preflight.transport, "execute", side_effect=self.answered):
                    self.assertTrue(self.check())
                self.state = json.loads(self.state_path.read_text())
                settled, fresh = self.state["stages"]
                self.assertTrue(settled["preflight_complete"])
                self.assertTrue(settled["interrupted"])
                self.assertEqual("PAUSED_PROVIDER_UNCERTAIN", settled["preflight_outcome"]["status"])
                self.assertEqual(provider, settled["launch_route"]["provider"])
                self.assertEqual(self.launch_route(provider), settled["launch_route"])
                recovered, answered = self.preflight_rows()
                self.assertTrue(recovered["interrupted"])
                self.assert_identity(recovered, provider)
                self.assert_identity(answered, provider)

    def test_a_probe_interrupted_before_admission_records_no_model_launch(self):
        for case, adapter, identity, _provider in adapters():
            with self.subTest(case=case):
                self.fixture(adapter, identity)

                def refused_before_exec(**kwargs):
                    # Interrupted before the admission callback: nothing launched.
                    raise KeyboardInterrupt("SIGTERM")

                with patch.object(preflight.transport, "execute", side_effect=refused_before_exec):
                    with self.assertRaises(support.Paused) as stopped:
                        self.check()
                self.assertEqual("PAUSED_INTERRUPTED", stopped.exception.status)
                self.state = json.loads(self.state_path.read_text())
                [record] = self.state["stages"]
                self.assertTrue(record["interrupted"])
                self.assertEqual("runner", record["engine"])
                self.assertNotIn("execution_identity", record)
                [row] = self.preflight_rows()
                self.assertEqual({}, row["execution_identity"])
                self.assertEqual("runner", row["engine"])

    def test_an_attempt_built_without_its_level_or_engine_keeps_its_receipt_level_on_opencode(self):
        self.fixture(SimpleNamespace(NAME="opencode"), {"engine": "opencode"})
        for preflight_identity, effort in (
            ({"provider": "opencode", "reasoning_effort": "medium"}, "medium"),
            ({"provider": "opencode"}, "low"),
            (None, "low"),
        ):
            with self.subTest(preflight_identity=preflight_identity):
                attempt = attempts.Attempt(
                    self.runner,
                    self.state,
                    self.run_dir,
                    SimpleNamespace(),
                    MODEL,
                    self.checkpoint,
                    lambda: self.seconds,
                    preflight_identity=preflight_identity,
                )
                record = attempt.record
                self.assertEqual("opencode", record["engine"])
                self.assertEqual(self.launch_route("opencode", effort), record["launch_route"])
                identity = record["execution_identity"]
                self.assertTrue(route_evidence.valid_execution_identity(identity), identity)
                self.assertEqual(
                    ("opencode", "opencode", effort),
                    (identity["engine"], identity["provider"], identity["reasoning_effort"]),
                )

    def test_an_attempt_given_no_level_records_none_not_its_receipt_level(self):
        # check() passes effort=None when the probed route selects no level (_selected_efforts);
        # that explicit None is recorded as it is, never replaced by the receipt level or "low".
        self.fixture(SimpleNamespace(NAME="opencode"), {"engine": "opencode"})
        attempt = attempts.Attempt(
            self.runner,
            self.state,
            self.run_dir,
            SimpleNamespace(),
            MODEL,
            self.checkpoint,
            lambda: self.seconds,
            preflight_identity={"provider": "opencode", "reasoning_effort": "medium"},
            effort=None,
        )
        record = attempt.record
        self.assertEqual(self.launch_route("opencode", None), record["launch_route"])
        self.assertIsNone(record["execution_identity"]["reasoning_effort"])


if __name__ == "__main__":
    unittest.main()
