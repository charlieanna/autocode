"""Fake-clock preparation measurements: no providers, waits or benchmarks."""

import contextlib
import importlib
import io
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autocode_efficiency as efficiency
import autocode_phase_timing as phase_timing
import autocode_publish_guard as publish_guard
import autocode_regression as regression
import autocode_support as support
import autocode_unit_request as unit_request
from units.model_request import ModelRequest

from .test_efficiency import attempt


class PhaseTimingTests(unittest.TestCase):
    def test_setup_attempts_reach_public_efficiency_evidence_without_double_counting_time(self):
        for module in (phase_timing, importlib.import_module("tools.autocode_phase_timing")):
            prefix = "tools." if module.__name__.startswith("tools.") else ""
            probe_module = importlib.import_module(prefix + "providers.kilocode_probe")
            state = {}
            clock = [0.0]

            def run(seconds):
                clock[0] += seconds
                raise probe_module.subprocess.TimeoutExpired(["kilo", "models", "--verbose"], seconds)

            with (
                patch.object(module.time, "monotonic", lambda: clock[0]),
                patch.object(module.util, "now", side_effect=[0, 90]),
                self.assertRaises(probe_module.KiloProbeTimeout),
            ):
                with module.measure(state, "source_launcher_preparation", stage="builder"):
                    probe_module.probe(["kilo", "models", "--verbose"], run)
            result = efficiency.summary(state)
            checks = result["observations"][0]["provenance"]["model_free_checks"]
            self.assertEqual([1, 2], [row["attempt"] for row in checks])
            self.assertEqual(["timed_out", "timed_out"], [row["outcome"] for row in checks])
            self.assertEqual(90, result["time"]["known_summed_execution_seconds"])

    def measured(self, state, action=lambda: None):
        with (
            patch.object(
                phase_timing.util, "now", side_effect=["2026-11-07T00:00:00+00:00", "2026-11-07T00:00:05+00:00"]
            ),
            patch.object(phase_timing.time, "monotonic", side_effect=[30, 32]),
        ):
            with phase_timing.measure(state, "prompt_write", stage="builder", attempt=2):
                action()

    def test_records_once_and_keeps_wall_and_monotonic_measurements_distinct(self):
        state = {"iteration": 4}
        self.measured(state)
        self.assertEqual(1, len(state["efficiency_observations"]))
        row = state["efficiency_observations"][0]
        self.assertEqual("2026-11-07T00:00:00+00:00", row["started_at"])
        self.assertEqual("2026-11-07T00:00:05+00:00", row["finished_at"])
        self.assertEqual("activity", row["kind"])
        self.assertEqual("deterministic_setup", row["category"])
        self.assertIsNone(row["attempt_id"])
        self.assertEqual(
            {
                "phase": "prompt_write",
                "stage": "builder",
                "iteration": 4,
                "attempt": 2,
                "outcome": "completed",
                "duration_seconds": 2,
            },
            row["provenance"],
        )
        self.measured(state)
        self.assertEqual(2, len({row["event_id"] for row in state["efficiency_observations"]}))

    def test_refusals_exceptions_and_interrupts_retain_one_measurement_and_propagate(self):
        for error, outcome in (
            (support.Paused("PAUSED_TEST", "private diagnostic"), "refused"),
            (ValueError("private diagnostic"), "exception"),
            (KeyboardInterrupt(), "exception"),
        ):
            with self.subTest(error=type(error).__name__):
                state = {}

                def fail():
                    raise error

                with self.assertRaises(type(error)) as caught:
                    self.measured(state, fail)
                self.assertIs(error, caught.exception)
                self.assertEqual(1, len(state["efficiency_observations"]))
                metadata = state["efficiency_observations"][0]["provenance"]
                self.assertEqual(outcome, metadata["outcome"])
                self.assertEqual(type(error).__name__, metadata["exception_type"])
                self.assertNotIn("private diagnostic", str(metadata))
                self.assertEqual(getattr(error, "status", None), metadata.get("refusal_status"))

    def test_request_scope_includes_guidance_and_records_a_preparation_refusal(self):
        for error in (None, support.Paused("PAUSED_REQUEST", "refused")):
            with self.subTest(refused=error is not None):
                state = {"next_stage": "resolve", "iteration": 1}
                request = ModelRequest("task_planner", "resolver", "request", {}, {}, False)
                guided = replace(request, prompt="guided")
                prepare = Mock(return_value=request)
                guidance = Mock(return_value=guided, side_effect=error)
                with patch.object(unit_request.stuck, "with_guidance", guidance):
                    if error:
                        with self.assertRaises(support.Paused):
                            unit_request.prepare(
                                state,
                                "resolve",
                                "state",
                                "schemas",
                                select_unit=lambda _: SimpleNamespace(prepare=prepare),
                                retry_authorization="grant",
                            )
                    else:
                        result = unit_request.prepare(
                            state,
                            "resolve",
                            "state",
                            "schemas",
                            select_unit=lambda _: SimpleNamespace(prepare=prepare),
                            retry_authorization="grant",
                        )
                        # The guided request, told it publishes nothing (autocode_publish_guard).
                        self.assertEqual(replace(guided, prompt=publish_guard.with_rule("guided")), result)
                prepare.assert_called_once_with(state, "resolve", "state", "schemas", retry_authorization="grant")
                guidance.assert_called_once_with(state, "resolve", request)
                row = state["efficiency_observations"][0]
                self.assertEqual("request_assembly", row["provenance"]["phase"])
                self.assertEqual("refused" if error else "completed", row["provenance"]["outcome"])

    def test_setup_does_not_relabel_or_double_count_the_provider_interval(self):
        state = {"stages": [attempt(start=10, end=20)]}
        with (
            patch.object(phase_timing.util, "now", side_effect=[0, 5]),
            patch.object(phase_timing.time, "monotonic", side_effect=[0, 5]),
        ):
            with phase_timing.measure(state, "source_launcher_preparation", stage="builder", attempt=1):
                pass
        result = efficiency.summary(state)
        self.assertEqual(1, result["by_category"]["build"]["attempts"])
        self.assertEqual(0, result["by_category"]["deterministic_setup"]["attempts"])
        timing = result["time"]
        self.assertEqual(15, timing["observed_wall_union_seconds"])
        self.assertEqual(15, timing["known_summed_execution_seconds"])
        self.assertEqual(5, timing["exclusive_wall_seconds_by_category"]["deterministic_setup"])
        self.assertEqual(0, timing["exclusive_wall_seconds_by_category"]["parallel_overlap"])


class RegressionStartTests(unittest.TestCase):
    def test_fresh_unverified_proof_records_its_actual_start_without_backfilling_history(self):
        legacy = {"stage": "regression_proof", "finished_at": "legacy-finish", "duration_seconds": 9}
        state = {"settings": {}, "iteration": 1, "stages": [legacy.copy()], "history": [legacy.copy()]}
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(regression.source_scope, "snapshot", return_value={"revision": "current"}),
            patch.object(regression, "base_commit", return_value=None),
            patch.object(regression.verify, "detect_framework", return_value=None),
            patch.object(regression.verify, "python_for", return_value="python"),
            patch.object(regression.runner_check, "track", return_value=contextlib.nullcontext(None)),
            patch.object(regression.util, "now", side_effect=["fresh-start", "proved-at", "record-finish"]),
            patch.object(regression.time, "monotonic", side_effect=[100, 106.5]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            proof = regression.prove(state, Path(temp), Path(temp) / "run")
        self.assertEqual("UNVERIFIED", proof["verdict"])
        self.assertIn("started_at", state["stages"][-1])
        self.assertNotIn("started_at", proof)
        self.assertEqual("proved-at", proof["proved_at"])
        self.assertEqual(6.5, proof["duration_seconds"])
        self.assertNotIn("started_at", state["regression_proofs"][-1])
        self.assertEqual("fresh-start", state["stages"][-1]["started_at"])
        self.assertEqual("record-finish", state["stages"][-1]["finished_at"])
        self.assertEqual(legacy, state["stages"][0])
        self.assertEqual(legacy, state["history"][0])
