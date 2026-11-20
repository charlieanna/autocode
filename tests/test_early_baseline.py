"""Tests for starting the base-suite baseline check early (#3348)."""

from __future__ import annotations

import contextlib
import os
import shlex
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import autocode_command_supervision as command_supervision
import autocode_early_baseline as early_baseline
import autocode_regression as regression
import autocode_util as util
import autocode_verify as verify

from .test_verify import Project, isolated_python


class EarlyBaselineTests(unittest.TestCase):
    def setUp(self):
        self.command = f"{shlex.quote(isolated_python(self))} -m unittest -v"

    def _receipt(self, base: str, run_dir: Path, *, timed_out: bool = False, exit_code: int = 0) -> dict:
        directory = Path(run_dir) / "baseline"
        directory.mkdir(parents=True, exist_ok=True)
        log = directory / "suite-on-base.log"
        log.write_text("OK\n" if not timed_out else "TIMEOUT\n")
        return {
            "base": base,
            "command": self.command,
            "receipt": {
                "command": self.command,
                "exit_code": exit_code if not timed_out else None,
                "timed_out": timed_out,
                "error": "" if not timed_out else "Command timed out",
                "output": str(log),
                "output_sha256": util.file_hash(log),
                "results": {
                    "passed": ["test_sample"] if not timed_out else [],
                    "failed": [],
                    "skipped": [],
                    "collection_errors": [],
                    "complete": not timed_out,
                    "total": 1 if not timed_out else 0,
                },
            },
            "health": "passing" if not timed_out else "timeout",
        }

    def test_is_enabled_default_and_project_configuration(self):
        # Default is True
        self.assertTrue(early_baseline.is_enabled({}))
        self.assertTrue(early_baseline.is_enabled({"settings": {}}))

        # Explicitly disabled in top-level settings
        self.assertFalse(early_baseline.is_enabled({"settings": {"early_baseline": False}}))

        # Explicitly disabled in regression settings
        self.assertFalse(early_baseline.is_enabled({"settings": {"regression": {"early_baseline": False}}}))

        # Explicitly enabled
        self.assertTrue(early_baseline.is_enabled({"settings": {"early_baseline": True}}))

    def test_early_baseline_starts_and_proof_reuses_matching_baseline(self):
        project = Project({"app.py": "x = 1\n", "test_app.py": "import unittest\n"})
        self.addCleanup(project.close)
        run_dir = project.root / ".autocode" / "run"
        run_dir.mkdir(parents=True, exist_ok=True)

        state = {
            "base_commit": "base-rev-1",
            "settings": {
                "regression": {"test_command": self.command},
                "early_baseline": True,
            },
        }

        baseline_calls: list[str] = []

        def fake_baseline(workspace, base, run_dir, **kwargs):
            baseline_calls.append(base)
            return self._receipt(base, run_dir)

        # 1. Start early baseline
        with mock.patch.object(verify, "baseline", side_effect=fake_baseline):
            handle = early_baseline.maybe_start(state, project.root, run_dir)
            self.assertIsNotNone(handle)
            self.assertIn("active_runner_check", state)
            self.assertEqual("regression_baseline", state["active_runner_check"]["stage"])
            self.assertTrue(state["active_runner_check"]["started_early"])

            # Wait for background thread to finish
            handle.join(timeout=5.0)
            self.assertTrue(handle.completed.is_set())

            # 2. First proof requests baseline via _baseline
            framework = verify.command_framework(self.command)
            result = regression._baseline(
                state,
                project.root,
                run_dir,
                "base-rev-1",
                framework,
                self.command,
                project.root,
            )

        # verify.baseline was only called ONCE (during early baseline, not again in _baseline)
        self.assertEqual(["base-rev-1"], baseline_calls)
        self.assertEqual("passing", result["health"])

        # 3. Shown and accounted: runner-owned stage row added
        stages = state.get("stages", [])
        early_stage = next((s for s in stages if s.get("stage") == "regression_baseline"), None)
        self.assertIsNotNone(early_stage)
        self.assertTrue(early_stage.get("started_early"))
        self.assertTrue(early_stage.get("runner_owned"))
        self.assertEqual("runner", early_stage.get("engine"))
        self.assertEqual(0, early_stage["metrics"]["provider_tokens"]["input_tokens"])

        # 4. active_runner_check cleared after reaping
        self.assertNotIn("active_runner_check", state)

    def test_early_baseline_waits_if_still_running(self):
        project = Project({"app.py": "x = 1\n", "test_app.py": "import unittest\n"})
        self.addCleanup(project.close)
        run_dir = project.root / ".autocode" / "run"
        run_dir.mkdir(parents=True, exist_ok=True)

        state = {
            "base_commit": "base-rev-wait",
            "settings": {
                "regression": {"test_command": self.command},
            },
        }

        import threading

        in_flight = threading.Event()
        release_baseline = threading.Event()

        def slow_baseline(workspace, base, run_dir, **kwargs):
            in_flight.set()
            release_baseline.wait(timeout=5.0)
            return self._receipt(base, run_dir)

        progress_calls = []

        def fake_progress(msg, **kwargs):
            progress_calls.append(msg)
            release_baseline.set()

        with mock.patch.object(verify, "baseline", side_effect=slow_baseline):
            handle = early_baseline.maybe_start(state, project.root, run_dir)
            self.assertIsNotNone(handle)
            in_flight.wait(timeout=5.0)

            # Call _baseline while handle is guaranteed to be in-flight
            framework = verify.command_framework(self.command)
            result = regression._baseline(
                state,
                project.root,
                run_dir,
                "base-rev-wait",
                framework,
                self.command,
                project.root,
                progress=fake_progress,
            )

        self.assertEqual("passing", result["health"])
        self.assertTrue(handle.completed.is_set())
        self.assertTrue(any("background" in msg.lower() for msg in progress_calls))

    def test_early_baseline_binding_mismatch_discards_and_recomputes(self):
        project = Project({"app.py": "x = 1\n", "test_app.py": "import unittest\n"})
        self.addCleanup(project.close)
        run_dir = project.root / ".autocode" / "run"
        run_dir.mkdir(parents=True, exist_ok=True)

        state = {
            "base_commit": "base-rev-a",
            "settings": {
                "regression": {"test_command": self.command},
            },
        }

        baseline_calls = []

        def fake_baseline(workspace, base, run_dir, **kwargs):
            baseline_calls.append(base)
            return self._receipt(base, run_dir)

        with mock.patch.object(verify, "baseline", side_effect=fake_baseline):
            handle = early_baseline.maybe_start(state, project.root, run_dir)
            self.assertIsNotNone(handle)
            handle.join(timeout=5.0)

            # Now call _baseline with a DIFFERENT base commit (binding mismatch)
            framework = verify.command_framework(self.command)
            result = regression._baseline(
                state,
                project.root,
                run_dir,
                "base-rev-b",  # Changed base commit!
                framework,
                self.command,
                project.root,
            )

        # Should have called fake_baseline twice: once for early (base-rev-a), once fresh (base-rev-b)
        self.assertEqual(["base-rev-a", "base-rev-b"], baseline_calls)
        self.assertEqual("passing", result["health"])

        # Discard reason recorded in user_events
        events = state.get("user_events", [])
        discarded_event = next((e for e in events if e.get("kind") == "early_baseline_discarded"), None)
        self.assertIsNotNone(discarded_event)
        self.assertIn("base", discarded_event.get("reason", ""))

    def test_early_baseline_not_started_when_reuse_not_supported(self):
        project = Project({"app.py": "x = 1\n"})
        self.addCleanup(project.close)
        run_dir = project.root / ".autocode" / "run"

        state = {
            "base_commit": "base-rev-junit",
            "settings": {"regression": {"test_command": "mvn test"}},
        }

        # Mock baseline_identity to return reuse_supported=False (like JUnit)
        with mock.patch.object(verify, "baseline_identity", return_value={"reuse_supported": False}):
            handle = early_baseline.maybe_start(state, project.root, run_dir)
            self.assertIsNone(handle)
            self.assertNotIn("active_runner_check", state)

    def test_early_baseline_timed_out_is_not_reused(self):
        project = Project({"app.py": "x = 1\n", "test_app.py": "import unittest\n"})
        self.addCleanup(project.close)
        run_dir = project.root / ".autocode" / "run"
        run_dir.mkdir(parents=True, exist_ok=True)

        state = {
            "base_commit": "base-rev-timeout",
            "settings": {
                "regression": {"test_command": self.command},
            },
        }

        calls = []

        def timeout_then_pass(workspace, base, run_dir, **kwargs):
            calls.append(len(calls))
            if len(calls) == 1:
                return self._receipt(base, run_dir, timed_out=True)
            return self._receipt(base, run_dir, timed_out=False)

        with mock.patch.object(verify, "baseline", side_effect=timeout_then_pass):
            handle = early_baseline.maybe_start(state, project.root, run_dir)
            self.assertIsNotNone(handle)
            handle.join(timeout=5.0)

            # First proof calls _baseline - timed-out early run must not be reused as passing baseline
            framework = verify.command_framework(self.command)
            result = regression._baseline(
                state,
                project.root,
                run_dir,
                "base-rev-timeout",
                framework,
                self.command,
                project.root,
                settled_timeout=False,
            )

        self.assertEqual(2, len(calls), "Timed-out early baseline must not be reused as passing evidence")
        self.assertEqual("passing", result["health"])

    def test_reconcile_at_boundary_stops_the_running_command_and_its_thread(self):
        # #3859: the pause boundary only marked the check stopped. Its thread and command kept
        # writing into the run directory after the run paused, and a test's temporary directory
        # cleanup then failed with "Directory not empty". The boundary now ends both.
        project = Project({"app.py": "x = 1\n", "test_app.py": "import unittest\n"})
        self.addCleanup(project.close)
        run_dir = project.root / ".autocode" / "run"
        run_dir.mkdir(parents=True, exist_ok=True)

        state = {
            "base_commit": "base-rev-pause",
            "settings": {
                "regression": {"test_command": self.command},
            },
        }
        started = threading.Event()

        def long_suite(workspace, base, run_dir, **kwargs):
            # The real base suite runs through command supervision; a long one must stop at the boundary.
            log = Path(run_dir) / "baseline" / "suite-on-base.log"
            with mock.patch.object(command_supervision.supervision, "launch_command", launch(started)):
                command_supervision.run("sleep 60", workspace, log, timeout=120, env=dict(os.environ))
            return self._receipt(base, run_dir)

        with mock.patch.object(verify, "baseline", side_effect=long_suite):
            handle = early_baseline.maybe_start(state, project.root, run_dir)
            self.assertIsNotNone(handle)
            self.assertIn("active_runner_check", state)
            self.assertTrue(started.wait(30), "the supervised command never started")

            began = time.monotonic()
            early_baseline.reconcile_at_boundary(state, run_dir)

        self.assertTrue(handle.stopped)
        self.assertFalse(handle.is_alive(), "the early baseline thread outlived the pause boundary")
        self.assertLess(time.monotonic() - began, 30)
        self.assertIsInstance(handle.error, command_supervision.CheckCancelled)
        self.assertIsNone(handle.result)
        self.assertNotIn("active_runner_check", state)


def launch(started):
    """Wrap command supervision's launch so the test knows the command is running."""
    original = command_supervision.supervision.launch_command

    @contextlib.contextmanager
    def wrapped(*args, **kwargs):
        with original(*args, **kwargs) as process:
            started.set()
            yield process

    return wrapped


if __name__ == "__main__":
    unittest.main()
