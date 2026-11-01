"""Tests for the scenario harness and catalog.  python3 -m unittest scenarios/test_harness.py

Proves every oracle (seed fails, reference passes, broken variants fail), then
proves the full run path with the scripted model: a correct solution is judged
PASS and a plausible wrong one is judged FALSE_COMPLETE.
"""

import argparse
import ast
import contextlib
import dataclasses
import hashlib
import importlib.util
import io
import json
import os
import re
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run  # noqa: E402
from harness import (  # noqa: E402
    api_cost,
    approval_provenance,
    attempts,
    baseline,
    build_compare,
    catalog,
    compare,
    hybrid,
    oracle,
    plan_compare,
    processes,
    profiles,
    routing,
    stats,
    verdict,
)
from harness.driver import (  # noqa: E402
    PLANNING_BUDGET_FEEDBACK,
    DriveError,
    Driver,
    InterruptedDrive,
    TurnNotReached,
    changed_between,
    leaves_for_person,
    metrics,
    model_routes,
    split_by_turn,
    turn_state,
    with_flag_values,
    workspace_files,
)
from harness.project import PERSON_EMAIL, PERSON_NAME, materialize  # noqa: E402


class PhaseCatalogTests(unittest.TestCase):
    def test_reused_sequence_base_cannot_inherit_credentials_or_overwrite_evidence(self):
        from harness.phase_env import PhaseSequence, write_synthetic_credentials

        with tempfile.TemporaryDirectory() as root:
            base = Path(root) / "sequence"
            first = PhaseSequence("first", base)
            phase = first.phase("stats")
            write_synthetic_credentials(phase.credential_root, "prior-token")
            phase.requests_log.write_text('{"phase":"stats"}\n')
            first.finish()
            previous = {p.relative_to(base): p.read_bytes() for p in base.rglob("*") if p.is_file()}
            with self.assertRaisesRegex(ValueError, "fresh|owned"):
                PhaseSequence("second", base)
            self.assertEqual(previous, {p.relative_to(base): p.read_bytes() for p in base.rglob("*") if p.is_file()})

    def test_duplicate_phase_names_cannot_reuse_roots_or_refusal_logs(self):
        from harness.phase_env import PhaseSequence, write_synthetic_credentials

        with tempfile.TemporaryDirectory() as root:
            sequence = PhaseSequence("acceptance", Path(root) / "fresh")
            first = sequence.phase("compat")
            credential = write_synthetic_credentials(first.credential_root, "first-phase-token")
            first.requests_log.write_text('{"phase":"compat"}\n')
            previous = (credential.read_bytes(), first.requests_log.read_bytes())
            for name in ("compat", "COMPAT", "compat/.", "compat/../compat"):
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, "duplicate|single"):
                    sequence.phase(name)
            self.assertEqual([first], sequence.phases)
            self.assertEqual(previous, (credential.read_bytes(), first.requests_log.read_bytes()))

    def test_phase_catalog_seed_reference_and_controls(self):
        rows = run.self_test(catalog.load("acceptance-phase-isolation"))
        self.assertEqual(
            {
                "seed",
                "reference",
                "broken/contamination",
                "broken/swallowed-call",
                "broken/swallowed-teardown",
                "broken/swallowed-teardown-cleanup",
            },
            {name for name, _, _ in rows},
        )
        self.assertTrue(all(ok for _, ok, _ in rows), rows)

    def _score(self, overlay):
        from harness.project import materialize

        scenario = catalog.load("acceptance-phase-isolation")
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        project = materialize(scenario.seed, Path(temporary.name) / "project", overlay)
        evidence = (project.parent / ".phase-evidence").resolve()
        evidence.mkdir()
        sentinel = evidence / "prior.json"
        sentinel.write_text('{"outcome":"ERROR"}')
        before = sentinel.read_bytes()
        result = verdict.evaluate(scenario, project)
        self.assertEqual("", result.error)
        record = json.loads(next(c.detail for c in result.checks if c.name == "phase_records"))
        self.assertEqual(before, sentinel.read_bytes())
        base = Path(record["base"])
        self.assertNotEqual(evidence, base)
        self.assertTrue(base.is_relative_to(evidence))
        for phase in record["phases"]:
            for key in ("credential_root", "config_root", "state_root", "cache_root", "requests_log"):
                self.assertTrue(Path(phase[key]).is_relative_to(base), (key, phase))
        self.assertTrue(Path(record["record_path"]).is_relative_to(base))
        return result, record

    def test_reference_real_loopback_lifecycle_and_parent_oauth(self):
        from harness.phase_env import GREEN

        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            oauth = home / ".config/provider/oauth.json"
            oauth.parent.mkdir(parents=True)
            oauth.write_text('{"parent":"oauth"}')
            with patch.dict(os.environ, {"HOME": str(home)}):
                parent = dict(os.environ)
                result, record = self._score(catalog.load("acceptance-phase-isolation").reference)
                self.assertEqual(parent, dict(os.environ))
                self.assertEqual('{"parent":"oauth"}', oauth.read_text())
                self.assertFalse(Path(record["base"]).is_relative_to(home))
        self.assertTrue(result.passed, result.summary)
        self.assertEqual(GREEN, record["outcome"])
        self.assertTrue(all(not p["unexpected_requests"] for p in record["phases"]))
        life = record["stats_lifecycle"]
        self.assertEqual(("127.0.0.1", 200, {"status": "ok"}), (life["host"], life["status"], life["body"]))
        self.assertTrue(life["started"] and life["polled"] and life["stopped"])

    def test_swallowed_call_and_teardown_remain_error_with_green_children(self):
        from harness.phase_env import ERROR

        scenario = catalog.load("acceptance-phase-isolation")
        for name in ("swallowed-call", "swallowed-teardown", "swallowed-teardown-cleanup"):
            with self.subTest(control=name):
                result, record = self._score(scenario.dir / "broken" / name)
                self.assertFalse(result.passed)
                self.assertEqual(ERROR, record["outcome"])
                self.assertTrue(all(c["ok"] for c in record["checks"] if c["name"].endswith("subprocess 1 exit")))
                self.assertEqual(1, sum(len(p["unexpected_requests"]) for p in record["phases"]))

    def test_oracle_does_not_write_delivered_project(self):
        from harness.project import materialize

        scenario = catalog.load("acceptance-phase-isolation")
        with tempfile.TemporaryDirectory() as root:
            project = materialize(scenario.seed, Path(root) / "project", scenario.reference)
            before = {
                p.relative_to(project): p.read_bytes()
                for p in project.rglob("*")
                if p.is_file() and ".git" not in p.parts
            }
            result = verdict.evaluate(scenario, project)
            self.assertTrue(result.passed, result.summary)
            after = {
                p.relative_to(project): p.read_bytes()
                for p in project.rglob("*")
                if p.is_file() and ".git" not in p.parts
            }
            self.assertEqual(before, after)

    def test_phase_root_escape_rejected_before_prior_evidence_write(self):
        from harness.phase_env import PhaseSequence

        with tempfile.TemporaryDirectory() as root:
            previous = Path(root) / "prior-evidence"
            previous.mkdir()
            sentinel = previous / ".credentials.json"
            sentinel.write_text('{"token":"prior-evidence"}')
            sequence = PhaseSequence("acceptance", Path(root) / "fresh")
            with self.assertRaisesRegex(ValueError, "inside"):
                sequence.phase("stats", credential_root=previous)
            self.assertEqual('{"token":"prior-evidence"}', sentinel.read_text())
            self.assertEqual([], sequence.phases)


class OracleCommandTests(unittest.TestCase):
    def test_non_executable_deliveries_are_failed_checks_not_oracle_errors(self):
        with tempfile.TemporaryDirectory() as root:
            project = Path(root)
            for mode, contents in ((0o644, b"!<arch>\n"), (0o755, b"not an executable\n")):
                with self.subTest(mode=oct(mode)):
                    binary = project / "policy.bin"
                    binary.write_bytes(contents)
                    binary.chmod(mode)
                    result = oracle.run(["./policy.bin"], project)
                    self.assertEqual(126, result.returncode)
                    self.assertEqual("", result.stdout)
                    self.assertIn("policy.bin", result.stderr)

    def test_missing_executable_remains_exit_127(self):
        with tempfile.TemporaryDirectory() as root:
            result = oracle.run(["./missing"], Path(root))
            self.assertEqual(127, result.returncode)
            self.assertIn("missing", result.stderr)


class EvidenceDirectoryTests(unittest.TestCase):
    def test_simultaneous_runs_with_the_same_timestamp_have_separate_evidence(self):
        with tempfile.TemporaryDirectory() as root, patch.object(run, "datetime") as clock:
            clock.now.return_value.strftime.return_value = "20260929T003743Z"
            parent = (Path(root) / "results").resolve()
            with ThreadPoolExecutor(max_workers=8) as pool:
                rows = list(pool.map(lambda _: run.evidence_directory(parent, "review-live"), range(16)))
            self.assertEqual(16, len({path for _, path in rows}))
            for index, (stamp, path) in enumerate(rows):
                self.assertEqual("20260929T003743Z", stamp)
                self.assertEqual(parent, path.parent)
                self.assertTrue(path.name.startswith(stamp + "-review-live-"))
                (path / "result.json").write_text(str(index))
            for index, (_, path) in enumerate(rows):
                self.assertEqual(str(index), (path / "result.json").read_text())


class DesignOrderingGroundingTests(unittest.TestCase):
    def test_user_questions_are_optional_when_the_existing_ordering_contract_is_grounded(self):
        from harness.project import materialize

        scenario = catalog.load("design-review-planted")
        original = json.loads((scenario.reference / "review/design-review.json").read_text())
        with tempfile.TemporaryDirectory() as root:
            project = materialize(scenario.seed, Path(root) / "project", scenario.reference)
            for questions in (
                [],
                [
                    {
                        "question": "Must every consumer be idempotent or only billing?",
                        "options": ["billing only", "all consumers"],
                    }
                ],
            ):
                with self.subTest(questions=questions):
                    (project / "review/design-review.json").write_text(json.dumps({**original, "questions": questions}))
                    result = verdict.evaluate(scenario, project)
                    self.assertEqual("", result.error)
                    self.assertTrue(
                        all(check.ok for check in result.checks),
                        [check.name for check in result.checks if not check.ok],
                    )


class ProcessDependencyTests(unittest.TestCase):
    def test_catalog_list_imports_without_psutil(self):
        script = """
import builtins, runpy, sys
original_import = builtins.__import__
def without_psutil(name, *args, **kwargs):
    if name == "psutil":
        raise ModuleNotFoundError("No module named 'psutil'", name="psutil")
    return original_import(name, *args, **kwargs)
builtins.__import__ = without_psutil
sys.argv = [sys.argv[1], "list"]
runpy.run_path(sys.argv[0], run_name="__main__")
"""
        result = subprocess.run(
            [sys.executable, "-c", script, str(Path(run.__file__).resolve())],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("ladder-17-transaction-ledger", result.stdout)

    def test_custom_cli_without_psutil_fails_before_spawning(self):
        driver = Driver(Path.cwd(), Path.cwd(), [], {}, autocode=["custom-autocode"], max_steps=1, timeout_seconds=60)
        with patch.object(processes, "psutil", None), patch.object(processes.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(DriveError, "supervision requires psutil.*including with --autocode"):
                driver.call("start", task="Build")
        launch.assert_not_called()
        self.assertEqual([], driver.steps)

    def test_default_cli_keeps_friendly_missing_dependency_diagnostic(self):
        args = argparse.Namespace(fake=True, profile=None, autocode=None)
        with patch.object(run.importlib.util, "find_spec", return_value=None):
            with self.assertRaisesRegex(SystemExit, "supervision needs psutil.*virtualenv"):
                run.require_mode(args)


class DriverFlagTests(unittest.TestCase):
    def test_new_run_only_flags_go_to_the_first_call_and_never_to_a_saved_run(self):
        flags = [
            "--provider",
            "claude",
            "--workflow",
            "build",
            "--builder-strong-model",
            "claude-sonnet-5-5",
            "--builder-strong-model=claude-opus-5-5",
            "--workflow=build",
            "--terra-model",
            "claude-haiku",
            "--package",
            "packages/greet/",
        ]
        done = subprocess.CompletedProcess([], 0, "", "")
        with tempfile.TemporaryDirectory() as root, patch("harness.driver.run_cli", return_value=done) as launch:
            driver = Driver(Path(root), Path(root), flags, {}, autocode=["cli"], max_steps=5, timeout_seconds=60)
            driver.call("start", task="Build")
            driver.run_dir = Path(root) / "saved-run"
            driver.call("resume", "--resume-paused")
        first, then = (call.args[0] for call in launch.call_args_list)
        self.assertEqual(flags, first[first.index("--no-chat") + 1 :])
        # The CLI refuses --builder-strong-model, --workflow and --package on a saved run, so a resume never
        # repeats them.
        self.assertEqual(
            ["--provider", "claude", "--terra-model", "claude-haiku", "--resume-paused"],
            then[then.index("--no-chat") + 1 :],
        )
        self.assertEqual(flags, driver.flags)


class DriverTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.parent = self.process(100)
        self.child = Mock(pid=100, returncode=0)
        self.child.communicate.return_value = ("output", "error")
        for name, value in (("Popen", self.child),):
            patcher = patch.object(processes.subprocess, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(processes.psutil, "Process", return_value=self.parent)
        self.lookup = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(processes.psutil, "wait_procs", return_value=([], []))
        self.wait = patcher.start()
        self.addCleanup(patcher.stop)

    def process(self, pid, *, children=(), stubborn=False):
        process = Mock(pid=pid)
        process.is_running.return_value = True
        process.status.return_value = processes.psutil.STATUS_RUNNING
        process.children.side_effect = lambda **kwargs: self.events.append((pid, "capture")) or list(children)

        def signal(method):
            self.events.append((pid, method))
            if not stubborn:
                process.is_running.return_value = False

        process.terminate.side_effect = lambda: signal("terminate")
        process.kill.side_effect = lambda: signal("kill")
        return process

    def timed_out(self, graceful=("partial", "")):
        self.child.communicate.side_effect = [subprocess.TimeoutExpired(["cli"], 10), graceful, ("partial", "")]

    def run_cli(self):
        return processes.run_cli(["cli"], env={"SAFE": "value"}, cwd=Path.cwd(), timeout=10)

    def test_normal_exit_preserves_completed_process_and_does_not_signal(self):
        self.child.returncode = 2
        result = self.run_cli()
        self.assertEqual(
            (["cli"], 2, "output", "error"), (result.args, result.returncode, result.stdout, result.stderr)
        )
        self.child.communicate.assert_called_once_with(timeout=10)
        self.parent.terminate.assert_not_called()
        self.parent.kill.assert_not_called()
        self.wait.assert_not_called()

    def test_timeout_captures_detached_provider_before_parent_exit_and_kills_only_owned(self):
        provider = self.process(101)
        provider.session_id = 101  # Separate provider session, still a child of the CLI.
        unrelated = self.process(999)
        self.parent.children.side_effect = lambda **kwargs: self.events.append((100, "capture")) or [provider]
        self.timed_out()
        with self.assertRaises(processes.CallTimeout) as caught:
            self.run_cli()
        self.assertEqual([], caught.exception.cleanup_errors)
        self.assertLess(self.events.index((100, "capture")), self.events.index((100, "terminate")))
        provider.kill.assert_called_once_with()
        self.parent.kill.assert_not_called()  # Gracefully exited, so no second signal.
        unrelated.terminate.assert_not_called()
        unrelated.kill.assert_not_called()
        self.lookup.assert_called_once_with(100)
        self.assertEqual([self.parent, provider], self.wait.call_args.args[0])

    def test_cleanup_captures_late_grandchildren_and_skips_reused_identity(self):
        late = self.process(103)
        provider = self.process(101, children=[late])
        reused = self.process(102)
        self.parent.children.side_effect = lambda **kwargs: [provider, reused]

        def graceful(*args, **kwargs):
            reused.is_running.return_value = False  # Captured PID now belongs to a different process.
            return "partial", ""

        def communicate(*, timeout):
            if timeout == 10:
                raise subprocess.TimeoutExpired(["cli"], 10)
            if timeout == 5:
                return graceful()
            return "", ""

        self.child.communicate.side_effect = communicate
        with self.assertRaises(processes.CallTimeout):
            self.run_cli()
        late.kill.assert_called_once_with()
        provider.kill.assert_called_once_with()
        reused.kill.assert_not_called()

    def test_cleanup_failures_are_retained_in_timeout(self):
        self.parent.children.side_effect = processes.psutil.AccessDenied(100)
        self.timed_out()
        with self.assertRaises(processes.CallTimeout) as caught:
            self.run_cli()
        self.assertTrue(any("cannot capture descendants" in item for item in caught.exception.cleanup_errors))
        self.parent.terminate.assert_called_once_with()

    def test_timeout_retains_complete_output_collected_during_cleanup(self):
        self.child.communicate.side_effect = [
            subprocess.TimeoutExpired(["cli"], 10, output=b"partial", stderr=b"early error"),
            ("partial and graceful", "early error and graceful"),
            ("partial and graceful and final", "early error and graceful and final"),
        ]
        with self.assertRaises(processes.CallTimeout) as caught:
            self.run_cli()
        self.assertEqual("partial and graceful and final", caught.exception.stdout)
        self.assertEqual("early error and graceful and final", caught.exception.stderr)
        self.assertEqual([], caught.exception.cleanup_errors)

    def test_open_pipes_timeout_retains_latest_partial_output_and_cleanup_errors(self):
        self.child.communicate.side_effect = [
            subprocess.TimeoutExpired(["cli"], 10, output=b"initial", stderr=b"initial error"),
            subprocess.TimeoutExpired(["cli"], 5, output=b"initial graceful", stderr=b"initial error graceful"),
            subprocess.TimeoutExpired(
                ["cli"], 2, output=b"initial graceful final", stderr=b"initial error graceful final"
            ),
        ]
        with self.assertRaises(processes.CallTimeout) as caught:
            self.run_cli()
        self.assertEqual(b"initial graceful final", caught.exception.stdout)
        self.assertEqual(b"initial error graceful final", caught.exception.stderr)
        self.assertTrue(any("output pipes remain open" in item for item in caught.exception.cleanup_errors))

    def test_cleanup_decode_errors_do_not_discard_original_timeout_output(self):
        decode_error = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid byte")
        self.child.communicate.side_effect = [
            subprocess.TimeoutExpired(["cli"], 10, output=b"original", stderr=b"original error"),
            decode_error,
            decode_error,
        ]
        with self.assertRaises(processes.CallTimeout) as caught:
            self.run_cli()
        self.assertEqual(b"original", caught.exception.stdout)
        self.assertEqual(b"original error", caught.exception.stderr)
        self.assertTrue(any("UnicodeDecodeError" in item for item in caught.exception.cleanup_errors))

    def test_missing_identity_stops_direct_child_and_reports_incomplete_ownership(self):
        self.lookup.side_effect = processes.psutil.AccessDenied(100)
        self.timed_out()
        with self.assertRaises(processes.CallTimeout) as caught:
            self.run_cli()
        self.assertTrue(any("cannot capture CLI PID 100 identity" in item for item in caught.exception.cleanup_errors))
        self.child.terminate.assert_called_once_with()
        self.child.kill.assert_called_once_with()

    def test_interruption_stops_owned_workers_and_preserves_interrupt(self):
        provider = self.process(101)
        self.parent.children.side_effect = lambda **kwargs: [provider]
        self.child.communicate.side_effect = [KeyboardInterrupt(), ("", ""), ("", "")]
        with self.assertRaises(KeyboardInterrupt):
            self.run_cli()
        provider.kill.assert_called_once_with()

    def test_direct_child_exit_race_does_not_prevent_cleanup(self):
        self.lookup.side_effect = processes.psutil.NoSuchProcess(100)
        self.child.terminate.side_effect = ProcessLookupError()
        self.child.kill.side_effect = ProcessLookupError()
        self.timed_out()
        with self.assertRaises(processes.CallTimeout) as caught:
            self.run_cli()
        self.assertEqual(1, len(caught.exception.cleanup_errors))
        self.assertIn("cannot capture CLI PID 100 identity", caught.exception.cleanup_errors[0])

    def test_repeated_decode_error_still_kills_captured_workers_and_preserves_error(self):
        provider = self.process(101)
        self.parent.children.side_effect = lambda **kwargs: [provider]
        error = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid byte")
        self.child.communicate.side_effect = error
        with self.assertRaises(UnicodeDecodeError) as caught:
            self.run_cli()
        self.assertIs(error, caught.exception)
        provider.kill.assert_called_once_with()
        self.assertIn("UnicodeDecodeError", " ".join(caught.exception.__notes__))
        self.child.stdout.close.assert_called_once_with()

    def test_second_interrupt_during_cleanup_does_not_skip_owned_workers(self):
        provider = self.process(101)
        self.parent.children.side_effect = lambda **kwargs: [provider]
        self.child.communicate.side_effect = [subprocess.TimeoutExpired(["cli"], 10), KeyboardInterrupt(), ("", "")]
        with self.assertRaises(processes.CallTimeout) as caught:
            self.run_cli()
        provider.kill.assert_called_once_with()
        self.assertTrue(any("KeyboardInterrupt" in item for item in caught.exception.cleanup_errors))

    def test_guarded_cleanup_waits_for_nested_keeper_after_cli_streams_close(self):
        keeper = self.process(101)
        sentinel = self.process(999)
        self.parent.children.return_value = [keeper]
        self.parent.children.side_effect = None
        clock = [0.0]

        def advance(seconds):
            clock[0] += seconds
            if clock[0] >= 14:
                keeper.is_running.return_value = False

        self.child.communicate.return_value = ("pause retained", "")
        with (
            patch.object(processes.time, "monotonic", side_effect=lambda: clock[0]),
            patch.object(processes.time, "sleep", side_effect=advance),
        ):
            errors = processes._stop(self.child, self.parent, "", {}, cleanup_seconds=processes.CLI_SETTLE_SECONDS)
        self.assertEqual([], errors)
        self.assertGreaterEqual(clock[0], 14)
        keeper.kill.assert_not_called()
        sentinel.terminate.assert_not_called()
        sentinel.kill.assert_not_called()

    def test_guarded_cleanup_fallback_stays_bounded_for_a_stuck_nested_keeper(self):
        keeper = self.process(101, stubborn=True)
        self.parent.children.return_value = [keeper]
        self.parent.children.side_effect = None
        clock = [0.0]
        killed_at = []
        keeper.kill.side_effect = lambda: killed_at.append(clock[0])

        def advance(seconds):
            clock[0] += seconds

        with (
            patch.object(processes.time, "monotonic", side_effect=lambda: clock[0]),
            patch.object(processes.time, "sleep", side_effect=advance),
        ):
            errors = processes._stop(self.child, self.parent, "", {}, cleanup_seconds=processes.CLI_SETTLE_SECONDS)
        self.assertEqual([], errors)
        self.assertEqual([processes.CLI_SETTLE_SECONDS], killed_at)
        self.wait.assert_called_once_with([self.parent, keeper], timeout=2)

    def test_second_interrupt_during_guarded_settlement_still_stops_owned_workers(self):
        keeper = self.process(101)
        self.parent.children.return_value = [keeper]
        self.parent.children.side_effect = None
        with patch.object(processes.time, "sleep", side_effect=KeyboardInterrupt):
            errors = processes._stop(self.child, self.parent, "", {}, cleanup_seconds=processes.CLI_SETTLE_SECONDS)
        keeper.kill.assert_called_once_with()
        self.assertTrue(any("KeyboardInterrupt" in error for error in errors))

    def test_signal_denial_is_not_reported_as_successful_cleanup(self):
        provider = self.process(101)
        provider.kill.side_effect = processes.psutil.AccessDenied(101)
        self.parent.children.side_effect = lambda **kwargs: [provider]
        self.timed_out()
        with self.assertRaises(processes.CallTimeout) as caught:
            self.run_cli()
        self.assertTrue(any("cannot kill owned PID 101" in item for item in caught.exception.cleanup_errors))

    def test_survivors_and_unclosed_pipes_are_reported(self):
        provider = self.process(101, stubborn=True)
        self.parent.children.side_effect = lambda **kwargs: [provider]
        self.child.communicate.side_effect = subprocess.TimeoutExpired(["cli"], 10)
        self.wait.return_value = ([], [provider])
        with self.assertRaises(processes.CallTimeout) as caught:
            self.run_cli()
        self.assertTrue(any("PID 101 remains alive" in item for item in caught.exception.cleanup_errors))
        self.assertTrue(any("output pipes remain open" in item for item in caught.exception.cleanup_errors))
        self.child.stdout.close.assert_called_once_with()
        self.child.stderr.close.assert_called_once_with()

    def test_driver_does_not_hide_cleanup_failure(self):
        driver = Driver(Path.cwd(), Path.cwd(), [], {}, autocode=["cli"], max_steps=1, timeout_seconds=60)
        with patch(
            "harness.driver.run_cli", side_effect=processes.CallTimeout(["cli"], 10, ["owned PID 101 remains alive"])
        ):
            with self.assertRaisesRegex(DriveError, "cleanup incomplete: owned PID 101 remains alive"):
                driver.call("start", task="Build")
        self.assertEqual([], driver.steps)

    def budgeted_driver(self, root, runs):
        """A Driver with a 300 s budget on a fake clock whose CLI calls take ``runs``: (seconds, returns)."""
        now = [1000.0]
        calls = iter(runs)

        def cli(command, **_):
            seconds, returns = next(calls)
            now[0] += seconds
            if not returns:
                raise processes.CallTimeout(command, 1, [])
            return subprocess.CompletedProcess(command, 0, "{}", "")

        for patcher in (
            patch("harness.driver.time", Mock(monotonic=lambda: now[0])),
            patch("harness.driver.run_cli", cli),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        return Driver(root, root, [], {}, autocode=["cli"], max_steps=30, timeout_seconds=300), now

    def test_a_cut_off_call_says_where_the_time_budget_went(self):
        """#967: a CLI test's retry ran out of its one 300 s budget, and the test's fixture was removed with
        the steps.jsonl that timed each call. The error itself now accounts for the budget."""
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        runs = [(8.5, True), (1.0, True), (40.0, True), (1.5, True), (20.0, True), (2.0, True), (240.0, False)]
        driver, now = self.budgeted_driver(root, runs)
        driver.call("start", task="Build")
        for kind in ("status", "resume", "status", "plain-resume", "status"):
            now[0] += 0.5  # the harness's own work between calls
            driver.call(kind, action=True, record=kind != "status")
        now[0] += 0.5
        with self.assertRaises(InterruptedDrive) as caught:
            driver.call("authorized-retry", "--resume-paused", "--retry-failed-stage")
        self.assertEqual(
            "authorized-retry was still running when the time budget ran out; captured workers stopped. "
            "Of the 300 s budget: start 8.5 s, resume 40.0 s, plain-resume 20.0 s, 3 status calls 4.5 s, "
            "3.0 s between calls; authorized-retry started with 224.0 s left and was stopped after 240.0 s",
            str(caught.exception),
        )
        # The cut-off call is still no step.
        self.assertEqual(["start", "resume", "plain-resume"], [step["kind"] for step in driver.steps])

    def test_a_call_refused_after_the_deadline_says_where_the_time_budget_went(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        driver, _ = self.budgeted_driver(root, [(100.0, True), (2.0, True), (199.0, True)])
        driver.call("start", task="Build")
        driver.call("status", action=True, record=False)
        driver.call("resume")  # returns 1 s after the deadline
        with self.assertRaises(DriveError) as caught:
            driver.call("status", action=True, record=False)
        self.assertNotIsInstance(caught.exception, InterruptedDrive)
        self.assertEqual(
            "time budget used up after 2 CLI calls. Of the 300 s budget: start 100.0 s, resume 199.0 s, "
            "1 status call 2.0 s, 0.0 s between calls",
            str(caught.exception),
        )

    def test_status_reads_during_a_watched_call_are_not_counted_as_no_time_between_calls(self):
        """A conformance drive reads the status from another thread while the call it watches runs."""
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        driver, now = self.budgeted_driver(root, [(5.0, True)])
        driver.call("start", task="Build")

        def watched(command, **_):
            if "--status" in command:
                now[0] += 1.0
                return subprocess.CompletedProcess(command, 0, "{}", "")
            for _ in range(3):
                now[0] += 50.0
                driver.call("status", "--status", action=True, record=False)
            now[0] += 140.0
            raise processes.CallTimeout(command, 1, [])

        with patch("harness.driver.run_cli", watched), self.assertRaises(InterruptedDrive) as caught:
            driver.call("continue")
        self.assertEqual(
            "continue was still running when the time budget ran out; captured workers stopped. "
            "Of the 300 s budget: start 5.0 s, 3 status calls 3.0 s, calls overlapped; "
            "continue started with 295.0 s left and was stopped after 293.0 s",
            str(caught.exception),
        )


class HarnessNativeIdentityTests(unittest.TestCase):
    """The harness compares receipts with the identity AutoCode records, which on Linux is psutil's start time
    since boot and not the wall-clock create_time() (#1117). A copy of the old rule made every owner-loss and
    orphan scenario fail on Linux CI while passing on macOS."""

    def process(self):
        return Mock(pid=4242, _ident=(4242, 1376.02), create_time=Mock(return_value=1791869719.02))

    def test_linux_and_macos_record_the_start_time_that_a_clock_change_does_not_move(self):
        for flags in ({"OSX": False, "LINUX": True}, {"OSX": True, "LINUX": False}):
            with self.subTest(flags=flags), patch.multiple(attempts.psutil, **flags):
                self.assertEqual(1376.02, attempts.native_birth(self.process()))

    def test_other_platforms_keep_create_time(self):
        with patch.multiple(attempts.psutil, OSX=False, LINUX=False):
            self.assertEqual(1791869719.02, attempts.native_birth(self.process()))

    def test_a_receipt_recorded_with_the_linux_identity_is_alive_to_the_harness(self):
        process = Mock(pid=4242, _ident=(4242, 1376.02), create_time=Mock(return_value=1791869719.02))
        process.is_running.return_value = True
        process.status.return_value = "running"
        with (
            patch.multiple(attempts.psutil, OSX=False, LINUX=True),
            patch.object(attempts.psutil, "Process", return_value=process),
        ):
            self.assertIs(True, attempts.owner_alive({"pid": 4242, "birth_identity": 1376.02}))
            self.assertIs(False, attempts.owner_alive({"pid": 4242, "birth_identity": 1791869719.02}))


class HarnessAttemptTests(unittest.TestCase):
    def admission(self, root, **extra):
        return attempts.admit(
            root,
            {"scenario": "case", "mode": "fake", "started_at": "1", **extra},
            {"timeout_seconds": 15, "max_steps": 2},
        )

    def test_cli_admission_is_durable_before_launch_and_only_reader_is_inherited(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.admission(root)
            kept = []
            child = Mock(pid=os.getpid(), returncode=2)

            def launch(command, **options):
                (record,) = (root / "cli-calls").glob("*.json")
                self.assertEqual("pending", attempts.read(record)["phase"])
                (fd,) = options["pass_fds"]
                self.assertEqual(["--owner-lifeline-fd", str(fd)], command[-2:])
                self.assertTrue(options["close_fds"])
                packet = json.loads(os.read(fd, 4096))
                self.assertEqual(attempts.identity(), packet["owner"])
                self.assertEqual(processes.CLI_KILL_AFTER_SECONDS, packet["kill_after_seconds"])
                self.assertEqual(33, packet["kill_after_seconds"])
                self.assertEqual(
                    str((root / "cli-calls" / (packet["nonce"] + "-supervision.json")).resolve()), packet["receipt"]
                )
                # No EOF yet: the harness alone retains the writer during work.
                self.assertEqual([], select.select([fd], [], [], 0)[0])
                kept.append(os.dup(fd))
                child.communicate.return_value = ("output", "")
                return child

            try:
                with patch.object(processes.subprocess, "Popen", side_effect=launch):
                    result = processes.run_cli(
                        ["cli"],
                        env={},
                        cwd=root,
                        timeout=15,
                        lifeline={"root": root / "cli-calls", "kind": "start", "deadline": time.monotonic() + 15},
                    )
                self.assertEqual((["cli"], 2, "output"), (result.args, result.returncode, result.stdout))
                self.assertEqual(b"", os.read(kept[0], 1))
                (record,) = (root / "cli-calls").glob("*.json")
                self.assertEqual(("returned", 2), tuple(attempts.read(record)[key] for key in ("phase", "exit")))
            finally:
                for fd in kept:
                    os.close(fd)

    def test_launch_failure_closes_the_only_writer_and_retains_interruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.admission(root)
            kept = []

            def launch(command, **options):
                (fd,) = options["pass_fds"]
                os.read(fd, 4096)
                kept.append(os.dup(fd))
                raise OSError("fixture launch denied")

            try:
                with patch.object(processes.subprocess, "Popen", side_effect=launch), self.assertRaises(OSError):
                    processes.run_cli(
                        ["cli"],
                        env={},
                        cwd=root,
                        timeout=15,
                        lifeline={"root": root / "cli-calls", "kind": "start", "deadline": time.monotonic() + 15},
                    )
                self.assertEqual(b"", os.read(kept[0], 1))
                self.assertEqual(verdict.INTERRUPTED_UNGRADED, attempts.unfinished(root)["verdict"])
            finally:
                for fd in kept:
                    os.close(fd)

    def test_interrupt_during_birth_capture_stops_direct_child_and_closes_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.admission(root)
            kept = []
            child = Mock(pid=os.getpid(), returncode=-15)
            child.communicate.return_value = ("", "")
            lookup = patch.object(processes.psutil, "Process", side_effect=KeyboardInterrupt())

            def launch(command, **options):
                (fd,) = options["pass_fds"]
                os.read(fd, 4096)
                kept.append(os.dup(fd))
                lookup.start()  # Interrupt after Popen and before birth capture.
                return child

            try:
                with (
                    patch.object(processes.subprocess, "Popen", side_effect=launch),
                    self.assertRaises(KeyboardInterrupt),
                ):
                    processes.run_cli(
                        ["cli"],
                        env={},
                        cwd=root,
                        timeout=15,
                        lifeline={"root": root / "cli-calls", "kind": "start", "deadline": time.monotonic() + 15},
                    )
                self.assertEqual(b"", os.read(kept[0], 1))
                child.terminate.assert_called_once_with()
                child.kill.assert_called_once_with()
            finally:
                lookup.stop()
                for fd in kept:
                    os.close(fd)

    # A CLI that sleeps past the budget; `armed` also writes the receipt the keeper would have, first.
    SLOW_CLI = (
        "import json, os, sys, time\n"
        "fd = int(sys.argv[sys.argv.index('--owner-lifeline-fd') + 1])\n"
        "packet = json.loads(os.read(fd, 4096))\n"
        "if sys.argv[1] == 'armed':\n"
        "    open(packet['receipt'], 'w').write(json.dumps({'phase': 'armed'}))\n"
        "time.sleep(30)\n"
    )

    def slow_cli(self, root, variant, budget):
        """Run a CLI that never arms (or arms, then sleeps) past the budget; its admission record."""
        script = root / "slow_cli.py"
        script.write_text(self.SLOW_CLI)
        started = time.monotonic()
        with self.assertRaises(processes.CallTimeout) as caught:
            processes.run_cli(
                [sys.executable, str(script), variant],
                env={"PATH": os.environ.get("PATH", "")},
                cwd=root,
                timeout=budget,
                lifeline={"root": root / "cli-calls", "kind": "start", "deadline": time.monotonic() + 15},
            )
        self.assertLess(time.monotonic() - started, 15, "the budget, not the lifeline deadline, stopped the CLI")
        self.assertEqual([], caught.exception.cleanup_errors)
        (call,) = [
            record
            for path in (root / "cli-calls").glob("*.json")
            if (record := attempts.read(path)) and record.get("kind") == "cli_call"
        ]
        return call

    def test_a_cli_stopped_before_its_keeper_armed_records_no_receipt(self):
        """#454 observation 2: the admission names a receipt path before Popen, but the keeper the CLI spawns
        writes it only after the CLI's imports and guard. A CLI killed by a sub-second budget before that must
        not leave a dangling path that reads as a receipt nobody wrote."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.admission(root)
            call = self.slow_cli(root, "unarmed", budget=0.6)  # sub-second, as in #454: before any keeper wrote
            self.assertEqual(
                ("interrupted", "TimeoutExpired", -signal.SIGTERM, False, None),
                tuple(call[key] for key in ("phase", "interruption", "exit", "keeper_armed", "receipt")),
                call,
            )
            self.assertEqual([], list(root.glob("cli-calls/*-supervision.json")))
            unfinished = attempts.unfinished(root)
            self.assertEqual((verdict.INTERRUPTED_UNGRADED, 1), (unfinished["verdict"], unfinished["cli_calls"]))
            self.assertEqual("CLI exited on a signal", unfinished["summary"])

    def test_a_cli_stopped_after_its_keeper_armed_keeps_the_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.admission(root)
            # Long enough for even a loaded host to start the interpreter and write the receipt first.
            call = self.slow_cli(root, "armed", budget=5)
            self.assertEqual(("interrupted", True), (call["phase"], call["keeper_armed"]))
            self.assertEqual({"phase": "armed"}, attempts.read(call["receipt"]))
            self.assertEqual(verdict.INTERRUPTED_UNGRADED, attempts.unfinished(root)["verdict"])

    def test_dead_owner_missing_result_counts_as_ungraded_and_preserves_reported_usage_so_far(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "attempt"
            self.admission(root)
            usage = {
                "accounting": {"tokens": {"output": {"known": 123, "total": None}}},
                "cost_usd": {"reported": 0.5, "complete": False},
            }
            attempts.observe(root, {"status": "BUILDER_RUNNING", "usage": usage})
            with patch.object(attempts, "owner_alive", return_value=False):
                (saved,) = stats.load_results(Path(tmp))
            self.assertEqual(
                (verdict.INTERRUPTED_UNGRADED, None, "unknown", usage),
                (saved["verdict"], saved["oracle_passed"], saved["usage_status"], saved["usage_snapshot"]),
            )
            (row,) = stats.summarize([saved])
            self.assertEqual(
                (1, 0, 0, 1, 1, None),
                tuple(
                    row[key]
                    for key in ("runs", "passes", "streak", "interrupted", "usage_unknown", "median_model_minutes")
                ),
            )
            self.assertFalse((root / "result.json").exists())  # Reading stats never finalizes or resumes.

    def test_a_long_run_s_usage_rows_never_outgrow_the_attempt_record(self):
        # A three-turn build reported 40 attempt rows (150 KB) and stopped its harness mid-run.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "attempt"
            self.admission(root)
            row = {"stage": "terra", "tokens": {"input_tokens": {"known": 1, "total": 1}}, "note": "x" * 4000}
            usage = {
                "cost_usd": {"reported": 9.3, "complete": True},
                "accounting": {
                    "schema": 1,
                    "attempts": [row] * 200,
                    "issues": ["late"] * 50,
                    "tokens": {"output_tokens": {"known": 7, "total": 7}},
                    "complete": True,
                },
            }
            attempts.observe(root, {"status": "BUILDER_RUNNING", "usage": usage})
            saved = attempts.read(root / "attempt.json")["usage_snapshot"]
            self.assertEqual(
                {
                    "schema": 1,
                    "attempts_rows": 200,
                    "issues_rows": 50,
                    "complete": True,
                    "tokens": {"output_tokens": {"known": 7, "total": 7}},
                },
                saved["accounting"],
            )
            self.assertEqual(usage["cost_usd"], saved["cost_usd"])

    def test_live_owner_is_pending_and_reused_birth_is_dead(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            value = self.admission(root)
            self.assertEqual(verdict.PENDING_UNGRADED, attempts.unfinished(root)["verdict"])
            value["owner"]["birth_identity"] -= 100
            self.assertFalse(attempts.owner_alive(value["owner"]))
            attempts.atomic_json(root / "attempt.json", value)
            self.assertEqual(verdict.INTERRUPTED_UNGRADED, attempts.unfinished(root)["verdict"])

    def test_final_result_wins_after_owner_death_and_is_not_counted_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "attempt"
            self.admission(root)
            run.finish(root, {"scenario": "case", "mode": "fake", "started_at": "1"}, verdict.PASS, "oracle passed")
            with patch.object(attempts, "owner_alive", return_value=False):
                rows = stats.load_results(Path(tmp))
            self.assertEqual([verdict.PASS], [row["verdict"] for row in rows])
            self.assertEqual("finished", attempts.read(root / "attempt.json")["phase"])

    def test_signal_exit_retains_negative_receipt_and_never_grades_or_queries_private_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(
                fake=True,
                fake_solution="reference",
                profile=None,
                provider=None,
                hybrid=False,
                i_authorize_live_model_spend=False,
                out=Path(tmp),
                autocode=None,
                max_steps=None,
                timeout_minutes=1,
            )
            with (
                patch.object(Driver, "drive", side_effect=InterruptedDrive("start exited on signal 9")),
                patch.object(Driver, "state") as private_state,
                patch.object(verdict, "evaluate") as grade,
            ):
                result = run.run_one(catalog.load("greenfield-greeting-cli"), args)
            private_state.assert_not_called()
            grade.assert_not_called()
            self.assertEqual(
                (verdict.INTERRUPTED_UNGRADED, None, "unknown"),
                (result["verdict"], result["oracle_passed"], result["usage_status"]),
            )
            self.assertTrue((Path(result["evidence"]) / "attempt.json").is_file())

    def test_interrupted_final_status_cannot_be_swallowed_and_graded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = argparse.Namespace(
                fake=True,
                fake_solution="reference",
                profile=None,
                provider=None,
                hybrid=False,
                i_authorize_live_model_spend=False,
                out=root,
                autocode=None,
                max_steps=None,
                timeout_minutes=1,
            )
            driver = Mock(run_dir=root / "run", steps=[], answers=[], turn_marks=[])
            driver.state.return_value = {}
            driver.view.side_effect = InterruptedDrive("final status exited on signal 9")
            with (
                patch.object(run, "Driver", return_value=driver),
                patch.object(verdict, "evaluate") as grade,
                patch.object(verdict, "diagnose") as diagnose,
            ):
                result = run.run_one(catalog.load("greenfield-greeting-cli"), args)
            driver.view.assert_called_once_with()
            grade.assert_not_called()
            diagnose.assert_not_called()
            self.assertEqual(
                (verdict.INTERRUPTED_UNGRADED, None, "unknown"),
                (result["verdict"], result["oracle_passed"], result["usage_status"]),
            )

    def test_comparison_rebuild_retains_missing_outer_arm_as_unknown_without_relaunch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "protocol.json").write_text(
                json.dumps({"fake": False, "profile_name": "test", "pairs": build_compare.schedule(["case"], 1)})
            )
            self.admission(root / "pair-case-1" / "scenario-run", repeat=1, variant="fixed")
            with patch.object(attempts, "owner_alive", return_value=False), patch.object(run, "run_one") as launch:
                result = build_compare.rebuild(root)
            launch.assert_not_called()
            fixed = result["summary"]["fixed"]
            self.assertEqual(
                (1, 0, None, None, None, 1),
                (
                    fixed["attempts"],
                    fixed["passes"],
                    fixed["api_usd"],
                    fixed["model_calls"],
                    fixed["rejected_model_calls"],
                    len(result["missing"]),
                ),
            )
            self.assertFalse(result["all_passed"])


class HarnessOwnerLossTests(unittest.TestCase):
    """Kill the real harness/CLI at a fake provider event, never a timed sleep."""

    def live(self, process):
        try:
            return process.is_running() and process.status() != processes.psutil.STATUS_ZOMBIE
        except processes.psutil.NoSuchProcess:
            return False

    def cleanup(self, harness, owned, sentinel):
        if harness.poll() is None:
            with contextlib.suppress(processes.psutil.NoSuchProcess):
                owned.extend(processes.psutil.Process(harness.pid).children(recursive=True))
            harness.kill()
        harness.wait(timeout=10)
        for process in reversed(owned):
            if self.live(process):
                process.kill()  # Process retains birth identity; no PID/group fallback.
        if sentinel.poll() is None:
            sentinel.kill()
        sentinel.wait(timeout=10)
        for child in (harness, sentinel):
            for stream in (child.stdin, child.stdout, child.stderr):
                if stream:
                    stream.close()

    def fault(self, target, *, barrier="settings"):
        # The fake provider blocks at its first call that is not `--version`.
        # A fresh run's first such call is the unsupervised `codex login status`
        # settings check, a plain child of the CLI. Answering it moves the
        # barrier into the first provider stage, behind its pre-exec keeper.
        # Settings have no nested workers to settle. Declare a short fixture
        # grace there, while provider-stage faults retain the production grace.
        kill_after = 0.25 if barrier == "settings" else processes.CLI_KILL_AFTER_SECONDS
        with tempfile.TemporaryDirectory(prefix="harness-owner-loss-") as tmp:
            root = Path(tmp)
            ready = root / "provider-ready"
            os.mkfifo(ready)
            reader = os.open(ready, os.O_RDONLY | os.O_NONBLOCK)
            self.addCleanup(os.close, reader)
            login = (
                "if sys.argv[1:]==['login','status']: print('Logged in using ChatGPT'); raise SystemExit(0)\n"
                "if sys.argv[1:2]!=['exec']: raise SystemExit(2)\n"
                if barrier == "stage"
                else ""
            )
            provider = (
                f"#!{sys.executable}\nimport os,json,signal,sys\n"
                "if '--version' in sys.argv: print('codex-cli 0.92.0'); raise SystemExit(0)\n"
                f"{login}"
                "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
                "fd=os.open(os.environ['OWNER_READY'],os.O_WRONLY)\n"
                "os.write(fd,(json.dumps({'provider':os.getpid(),'cli':os.getppid(),'call':sys.argv[1]})+'\\n').encode()); os.close(fd)\n"
                "signal.pause()\n"
            )
            script = f"""import argparse,json,sys
from pathlib import Path
sys.path.insert(0,{str(Path(__file__).resolve().parent)!r})
import run
from harness import catalog,processes
processes.CLI_KILL_AFTER_SECONDS={kill_after!r}
processes.CLI_SETTLE_SECONDS=processes.CLI_KILL_AFTER_SECONDS+5
original=run.fake_setup
def setup(scenario,root,solution):
    flags,env=original(scenario,root,solution)
    binary=root/'bin'/'codex'
    binary.write_text({provider!r}); binary.chmod(0o755)
    return flags,{{**env,'OWNER_READY':{str(ready)!r}}}
run.fake_setup=setup
args=argparse.Namespace(fake=True,fake_solution='reference',profile=None,provider=None,hybrid=False,
    i_authorize_live_model_spend=False,out=Path({str(root / "results")!r}),autocode=None,
    max_steps=None,timeout_minutes=1,max_seconds=30,max_stage_seconds=15,max_iterations=None)
result=run.run_one(catalog.load('greenfield-greeting-cli'),args)
print(json.dumps({{'verdict':result['verdict']}}),flush=True)
"""
            sentinel = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE)
            sentinel_birth = processes.psutil.Process(sentinel.pid)
            harness = subprocess.Popen(
                [sys.executable, "-u", "-c", script],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            owned = []
            try:
                self.assertTrue(select.select([reader], [], [], 45)[0], "Fake provider never reached its event barrier")
                event = json.loads(os.read(reader, 4096))
                self.assertEqual({"settings": "login", "stage": "exec"}[barrier], event["call"])
                owned.extend(processes.psutil.Process(harness.pid).children(recursive=True))
                cli = next(process for process in owned if process.pid == event["cli"])
                provider_process = next(process for process in owned if process.pid == event["provider"])
                (evidence,) = (root / "results").iterdir()
                admission = attempts.read(evidence / "attempt.json")
                self.assertEqual(harness.pid, admission["owner"]["pid"])
                (call_path,) = [
                    path
                    for path in (evidence / "cli-calls").glob("*.json")
                    if attempts.read(path).get("kind") == "cli_call"
                ]
                call = attempts.read(call_path)
                self.assertEqual(cli.pid, call["cli"]["pid"])
                self.assertEqual("running", call["phase"])
                self.assertEqual(kill_after, call["kill_after_seconds"])
                # Retain the enclosing keeper's provider-discovery barrier (#555)
                # for both settings and stage faults. The original startup-probe
                # fixture could fault before its first sample. Stage faults also
                # require an armed inner receipt and fresh native identities below.
                deadline = time.monotonic() + 10
                while True:
                    recorded = attempts.read(call["receipt"])
                    if any(row["pid"] == event["provider"] for row in recorded.get("processes", ())):
                        break
                    if time.monotonic() > deadline:
                        self.fail(f"Keeper never recorded the provider process: {recorded}")
                    time.sleep(0.02)
                if barrier == "stage":
                    stage_receipts = [attempts.read(path) for path in evidence.rglob("*.supervision.json")]
                    (stage_receipt,) = [
                        receipt
                        for receipt in stage_receipts
                        if receipt.get("provider", {}).get("pid") == event["provider"]
                    ]
                    self.assertEqual("armed", stage_receipt["phase"], stage_receipt)
                    self.assertEqual(cli.pid, stage_receipt["owner"]["pid"], stage_receipt)
                    self.assertIn(stage_receipt["keeper"]["pid"], [process.pid for process in owned])
                    identities = [stage_receipt[role] for role in ("owner", "keeper", "provider")]
                    self.assertTrue(
                        all(attempts.owner_alive(identity) is True for identity in identities), stage_receipt
                    )
                if target == "harness":
                    harness.kill()
                else:
                    cli.kill()
                output, errors = harness.communicate(timeout=15)
                processes.psutil.wait_procs(owned, timeout=call["kill_after_seconds"] + 5)
                survivors = []
                for process in owned:
                    if self.live(process):
                        try:
                            survivors.append(
                                f"pid={process.pid} ppid={process.ppid()} status={process.status()}"
                                f" cmdline={process.cmdline()}"
                            )
                        except processes.psutil.Error as error:
                            survivors.append(f"pid={process.pid} inspect failed: {error}")
                self.assertFalse(survivors, (errors or "") + "; survivors: " + "; ".join(survivors))
                self.assertFalse(self.live(provider_process))
                self.assertTrue(self.live(sentinel_birth), "An unrelated sentinel was signalled")
                if target == "harness":
                    self.assertEqual(-signal.SIGKILL, harness.returncode)
                    self.assertFalse((evidence / "result.json").exists())
                    receipt = attempts.read(call["receipt"])
                    self.assertEqual(
                        ("stopped", "owner_lost", None),
                        tuple(receipt[key] for key in ("phase", "cause", "cleanup_error")),
                        receipt,
                    )
                else:
                    self.assertEqual(0, harness.returncode, errors)
                    self.assertEqual(verdict.INTERRUPTED_UNGRADED, json.loads(output)["verdict"])
                    self.assertEqual(-signal.SIGKILL, attempts.read(call_path)["exit"])
                (row,) = stats.load_results(root / "results")
                self.assertEqual(
                    (verdict.INTERRUPTED_UNGRADED, "unknown", None),
                    (row["verdict"], row["usage_status"], row["oracle_passed"]),
                )
                (summary,) = stats.summarize([row])
                self.assertEqual(
                    (1, 0, 1, 1), tuple(summary[key] for key in ("runs", "passes", "interrupted", "usage_unknown"))
                )
            finally:
                self.cleanup(harness, owned, sentinel)

    def test_harness_sigkill_closes_lifeline_stops_cli_provider_and_retains_ungraded_attempt(self):
        self.fault("harness")

    def test_cli_sigkill_finishes_harness_as_ungraded_and_preserves_negative_exit(self):
        self.fault("cli")

    def test_harness_sigkill_in_a_provider_stage_stops_cli_and_provider(self):
        self.fault("harness", barrier="stage")

    def test_cli_sigkill_in_a_provider_stage_stops_provider_and_finishes_ungraded(self):
        self.fault("cli", barrier="stage")


class DriverStatusObservationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.driver = Driver(root, root, [], {}, autocode=["autocode"], max_steps=6, timeout_seconds=60)
        self.driver.run_dir = root / "run"

    @staticmethod
    def snapshot(status, need, *, iteration=1):
        return {
            "done": need is None,
            "needs": need,
            "status": status,
            "next_stage": "sol",
            "iteration": iteration,
            "phase": "build",
            "usage": {"tokens": iteration},
        }

    def exercise(self, responses, *, say_at=None, error=None, expire_after_status=None):
        pending = list(responses)
        observed_views = [view for kind, view in pending if kind == "status"]

        def invoke(command, **kwargs):
            kind = (
                "status"
                if "--status" in command
                else "answer"
                if "--answer" in command
                else "approve-plan"
                if "--approve-goal" in command
                else "resume-paused"
                if "--resume-paused" in command
                else "resume"
            )
            self.assertTrue(pending, f"unexpected {kind} CLI call after the observed stop")
            expected, view = pending.pop(0)
            self.assertEqual(expected, kind, "a fresh gate must be served before another observation")
            if view is expire_after_status and expire_after_status is not None:
                self.driver.deadline = -1
            output = json.dumps({"view": view}) if kind == "status" else ""
            return subprocess.CompletedProcess(command, 0, output, "")

        with patch("harness.driver.run_cli", side_effect=invoke), patch("harness.driver.attempts.observe") as observe:
            if error:
                with self.assertRaisesRegex(DriveError, error):
                    self.driver.until_stopped(say_at)
                result = None
            else:
                result = self.driver.until_stopped(say_at)
        self.assertEqual([], pending, "every prescribed public CLI action and observation must occur")
        self.assertEqual(observed_views, [call.args[1] for call in observe.call_args_list])
        if result is not None:
            self.assertIs(result, observe.call_args_list[-1].args[1], "return the actual public observation")
        return result

    def test_completed_post_resume_observation_is_returned_without_another_status_call(self):
        before = self.snapshot("RUNNING", {"kind": "continue"})
        after = self.snapshot("TASK_COMPLETE", None, iteration=2)
        self.assertEqual(after, self.exercise([("status", before), ("resume", None), ("status", after)]))
        self.assertEqual(["resume"], [step["kind"] for step in self.driver.steps])

    def test_multiple_progressing_continues_each_use_one_new_public_observation(self):
        before = self.snapshot("RUNNING", {"kind": "continue"})
        next_step = self.snapshot("RUNNING", {"kind": "continue"}, iteration=2)
        after = self.snapshot("TASK_COMPLETE", None, iteration=3)
        result = self.exercise(
            [
                ("status", before),
                ("resume", None),
                ("status", next_step),
                ("resume", None),
                ("status", after),
            ]
        )
        self.assertEqual(after, result)
        self.assertEqual(["resume", "resume"], [step["kind"] for step in self.driver.steps])

    def test_post_resume_person_stop_is_returned_without_automatic_recovery(self):
        for kind in ("resume", "retry_job", "recover_source"):
            with self.subTest(kind=kind):
                before = self.snapshot("RUNNING", {"kind": "continue"})
                after = self.snapshot("PAUSED_JOB_FAILURE", {"kind": kind}, iteration=2)
                self.assertEqual(after, self.exercise([("status", before), ("resume", None), ("status", after)]))
        self.assertEqual(["resume"] * 3, [step["kind"] for step in self.driver.steps])

    def test_post_resume_approval_is_served_then_fresh_completion_is_observed(self):
        before = self.snapshot("RUNNING", {"kind": "continue"})
        approval = self.snapshot("AWAITING_GOAL_APPROVAL", {"kind": "approve_plan", "token": "reviewed-plan"})
        after = self.snapshot("TASK_COMPLETE", None, iteration=2)
        result = self.exercise(
            [
                ("status", before),
                ("resume", None),
                ("status", approval),
                ("approve-plan", None),
                ("status", after),
            ]
        )
        self.assertEqual(after, result)
        self.assertEqual(["resume", "approve-plan"], [step["kind"] for step in self.driver.steps])
        self.assertEqual(["--approve-goal", "reviewed-plan"], self.driver.steps[-1]["args"])

    def test_explicit_answer_and_resume_mutations_each_require_a_new_observation(self):
        self.driver.explicit_answers = {"route-sol": "gpt-6-sol"}
        before = self.snapshot("RUNNING", {"kind": "continue"})
        question = self.snapshot(
            "AWAITING_RESOLVER",
            {
                "kind": "answer",
                "resolver_scope": "operational_exhaustion",
                "resolver_token": "request",
                "questions": [{"id": "route-sol", "proposed_default": "No default."}],
            },
        )
        paused = self.snapshot("PAUSED_INTERVENTION", {"kind": "resume"}, iteration=2)
        after = self.snapshot("TASK_COMPLETE", None, iteration=2)
        result = self.exercise(
            [
                ("status", before),
                ("resume", None),
                ("status", question),
                ("answer", None),
                ("status", paused),
                ("resume-paused", None),
                ("status", after),
            ]
        )
        self.assertEqual(after, result)
        self.assertEqual(["resume", "answer", "resume"], [step["kind"] for step in self.driver.steps])
        self.assertEqual(["--resume-paused"], self.driver.steps[-1]["args"])
        self.assertTrue(self.driver.answers[0]["explicit"])
        self.assertFalse(self.driver.resume_after_explicit)
        self.assertEqual(paused, self.exercise([("status", paused)]))

    def test_say_at_uses_observed_post_resume_gate_without_serving_it(self):
        before = self.snapshot("RUNNING", {"kind": "continue"})
        approval = self.snapshot("AWAITING_GOAL_APPROVAL", {"kind": "approve_plan", "token": "reviewed-plan"})
        result = self.exercise(
            [("status", before), ("resume", None), ("status", approval)], say_at="needs:approve_plan"
        )
        self.assertEqual(approval, result)
        self.assertEqual(["resume"], [step["kind"] for step in self.driver.steps])

    def test_unchanged_post_resume_view_still_refuses_no_progress(self):
        before = self.snapshot("RUNNING", {"kind": "continue"})
        self.exercise([("status", before), ("resume", None), ("status", dict(before))], error="no progress")
        self.assertEqual(["resume"], [step["kind"] for step in self.driver.steps])

    def test_observed_continue_cannot_start_another_action_after_shared_deadline(self):
        before = self.snapshot("RUNNING", {"kind": "continue"})
        after = self.snapshot("RUNNING", {"kind": "continue"}, iteration=2)
        self.exercise(
            [("status", before), ("resume", None), ("status", after)],
            error="time budget used up",
            expire_after_status=after,
        )
        self.assertEqual(["resume"], [step["kind"] for step in self.driver.steps])


class DriverAnswerTests(unittest.TestCase):
    def setUp(self):
        self.driver = Driver(Path.cwd(), Path.cwd(), [], {}, autocode=[], max_steps=1, timeout_seconds=60)
        call_patch = patch.object(self.driver, "call")
        self.call = call_patch.start()
        self.addCleanup(call_patch.stop)

    def test_explicit_no_default_uses_the_offered_rounding_rule(self):
        selected = "Round to nearest with ties to even, producing `-273.2 C`."
        question = {
            "id": "Q1",
            "question": "Which one-decimal rounding rule?",
            "proposed_default": "No default; this is a requested-output decision that requires user selection.",
            "options": [
                selected,
                "Round to nearest with ties away from zero, producing `-273.2 C`.",
                "Specify another rounding rule and the required literal output.",
            ],
        }
        self.driver.serve({"kind": "answer", "questions": [question], "resolver_token": "token"})
        self.call.assert_called_once_with(
            "answer", "--answer", f"Q1={selected}", "--resolver-token", "token", action=True
        )
        self.assertEqual([selected], [answer["answer"] for answer in self.driver.answers])

    QUOTA_NEED = {
        "kind": "answer",
        "resolver_scope": "operational_exhaustion",
        "resolver_token": "token",
        "questions": [{"id": "route-sol", "proposed_default": "", "options": []}],
        "route": {"question_id": "route-sol", "role": "sol"},
    }

    def views(self, *needs):
        return [
            {"done": need is None, "needs": need, "status": "S", "next_stage": "sol", "iteration": 1, "phase": "P"}
            for need in needs
        ]

    def test_a_person_only_question_is_left_for_the_person_without_an_explicit_answer(self):
        with patch.object(self.driver, "view", side_effect=self.views(self.QUOTA_NEED)):
            self.assertEqual(self.QUOTA_NEED, self.driver.until_stopped()["needs"])
        self.call.assert_not_called()

    def test_an_unanswered_question_returns_the_public_stop_without_submitting_the_batch(self):
        for default in (None, "", "   ", "No default; user selection required."):
            with self.subTest(default=default):
                stopped = {
                    "done": False,
                    "status": "WAITING_FOR_USER",
                    "needs": {
                        "kind": "answer",
                        "questions": [
                            {"id": "Q1", "proposed_default": "Keep current behavior."},
                            {
                                "id": "Q2",
                                "proposed_default": default,
                                "options": ["Other", "Specify another result.", "Ask the user."],
                            },
                        ],
                    },
                }
                with patch.object(self.driver, "view", return_value=stopped):
                    self.assertIs(stopped, self.driver.until_stopped())
                self.call.assert_not_called()
                self.assertEqual([], self.driver.answers)

    def test_an_unanswered_question_is_judged_against_the_expected_ending(self):
        stopped = {
            "done": False,
            "status": "WAITING_FOR_USER",
            "needs": {"kind": "answer", "questions": [{"id": "Q1", "proposed_default": "No default."}]},
        }
        with patch.object(self.driver, "view", return_value=stopped):
            final = self.driver.until_stopped()
        accepted = verdict.OracleResult([oracle.Check("unchanged behavior", True)])
        for expected in ("any", "stop"):
            with self.subTest(expected=expected):
                self.assertEqual(verdict.PASS, verdict.judge(final["status"], accepted, expected)[0])
        self.assertEqual(verdict.HONEST_BLOCKER, verdict.judge(final["status"], accepted, "complete")[0])
        rejected = verdict.OracleResult([oracle.Check("required behavior", False)])
        self.assertEqual(verdict.HONEST_BLOCKER, verdict.judge(final["status"], rejected, "any")[0])
        self.assertFalse(final["done"])
        self.call.assert_not_called()

    def test_an_explicit_answer_to_a_no_default_question_still_continues(self):
        need = {"kind": "answer", "questions": [{"id": "Q1", "proposed_default": "No default."}]}
        self.driver.explicit_answers = {"Q1": "The failing input is a CSV row containing None."}
        with patch.object(self.driver, "view", side_effect=self.views(need, None)):
            self.assertTrue(self.driver.until_stopped()["done"])
        self.call.assert_called_once_with(
            "answer", "--answer", "Q1=The failing input is a CSV row containing None.", action=True
        )
        self.assertTrue(self.driver.answers[0]["explicit"])

    def test_genuine_answer_driver_errors_still_propagate(self):
        stopped = {
            "done": False,
            "status": "WAITING_FOR_USER",
            "needs": {"kind": "answer", "questions": [{"id": "Q1", "proposed_default": "Keep data."}]},
        }
        with (
            patch.object(self.driver, "view", return_value=stopped),
            patch.object(self.driver, "serve", side_effect=DriveError("answer CLI rejected the gate")),
            self.assertRaisesRegex(DriveError, "answer CLI rejected"),
        ):
            self.driver.until_stopped()

    def test_a_malformed_question_is_not_an_unanswered_product_stop(self):
        stopped = {
            "done": False,
            "status": "WAITING_FOR_USER",
            "needs": {"kind": "answer", "questions": [{"proposed_default": "No default."}]},
        }
        with patch.object(self.driver, "view", return_value=stopped), self.assertRaises(KeyError):
            self.driver.until_stopped()
        self.call.assert_not_called()

    def test_explicit_answer_is_given_then_its_pause_resumed_once(self):
        driver = Driver(
            Path.cwd(),
            Path.cwd(),
            ["--sol-model", "gpt-5.6-sol"],
            {},
            autocode=[],
            max_steps=5,
            timeout_seconds=60,
            explicit_answers=(("route-sol", "gpt-6-luna"),),
        )
        resume = {"kind": "resume", "reason": "Partial work retained"}
        with (
            patch.object(driver, "call") as call,
            patch.object(driver, "view", side_effect=self.views(self.QUOTA_NEED, resume, None)),
        ):
            self.assertTrue(driver.until_stopped()["done"])
        self.assertEqual(
            [
                (("answer", "--answer", "route-sol=gpt-6-luna", "--resolver-token", "token"), {"action": True}),
                (("resume", "--resume-paused"), {}),
            ],
            [(c.args, c.kwargs) for c in call.call_args_list],
        )
        self.assertEqual(["--sol-model", "gpt-6-luna"], driver.flags, "the old model is never passed back")
        self.assertTrue(driver.answers[0]["explicit"])
        with patch.object(driver, "call") as call, patch.object(driver, "view", side_effect=self.views(resume)):
            self.assertEqual(resume, driver.until_stopped()["needs"])
        call.assert_not_called()

    def test_substantive_default_is_preserved_even_when_it_is_not_an_option(self):
        for default in (
            "Keep the existing behavior.",
            "No default value should be persisted; reject absent keys.",
            "  Deny external writes.  ",
        ):
            with self.subTest(default=default):
                self.call.reset_mock()
                self.driver.serve(
                    {
                        "kind": "answer",
                        "questions": [{"id": "Q1", "proposed_default": default, "options": ["Use another behavior."]}],
                    }
                )
                self.call.assert_called_once_with("answer", "--answer", f"Q1={default}", action=True)

    def test_placeholder_options_are_skipped_for_a_concrete_choice(self):
        for default in (
            "No default",
            " NO DEFAULT: user selection required.",
            "No proposed default is available.",
            None,
            "",
            " \t ",
        ):
            with self.subTest(default=default):
                self.call.reset_mock()
                self.driver.serve(
                    {
                        "kind": "answer",
                        "questions": [
                            {
                                "id": "Q1",
                                "proposed_default": default,
                                "options": [
                                    "",
                                    "  ",
                                    "No default; ask the user.",
                                    "Other (please specify)",
                                    "Specify another rounding rule.",
                                    "Use ties to even.",
                                ],
                            }
                        ],
                    }
                )
                self.call.assert_called_once_with("answer", "--answer", "Q1=Use ties to even.", action=True)

    def test_no_concrete_choice_refuses_the_batch_without_recording_unsent_answers(self):
        for default in (None, "", " \t ", "No default; user selection required."):
            for options in (None, [], ["Other", "Specify another result.", "Ask the user.", "No default."]):
                with self.subTest(default=default, options=options):
                    self.call.reset_mock()
                    self.driver.answers.clear()
                    question = {"id": "Q2"}
                    if default is not None:
                        question["proposed_default"] = default
                    if options is not None:
                        question["options"] = options
                    need = {
                        "kind": "answer",
                        "resolver_token": "token",
                        "questions": [{"id": "Q1", "proposed_default": "Keep current behavior."}, question],
                    }
                    with self.assertRaisesRegex(DriveError, "Q2.*no concrete option"):
                        self.driver.serve(need)
                    self.call.assert_not_called()
                    self.assertEqual([], self.driver.answers)

    def test_multiple_answers_are_submitted_with_one_resolver_token(self):
        self.driver.serve(
            {
                "kind": "answer",
                "resolver_token": "shared",
                "questions": [
                    {"id": "Q1", "proposed_default": "Preserve records."},
                    {"id": "Q2", "proposed_default": "No default.", "options": ["Use ties to even."]},
                ],
            }
        )
        self.call.assert_called_once_with(
            "answer",
            "--answer",
            "Q1=Preserve records.",
            "--answer",
            "Q2=Use ties to even.",
            "--resolver-token",
            "shared",
            action=True,
        )
        self.assertEqual(["Q1", "Q2"], [answer["id"] for answer in self.driver.answers])

    def test_missing_default_uses_a_concrete_offered_option(self):
        self.driver.serve({"kind": "answer", "questions": [{"id": "Q1", "options": ["Keep data.", "Remove data."]}]})
        self.call.assert_called_once_with("answer", "--answer", "Q1=Keep data.", action=True)

    def test_explicit_answer_supplies_freeform_input_without_a_default(self):
        answer = "5 failures within 60 seconds"
        self.driver.explicit_answers = {"Q1": answer}
        self.driver.serve(
            {
                "kind": "answer",
                "resolver_token": "token",
                "questions": [
                    {"id": "Q1", "question": "What threshold and window?", "proposed_default": "", "options": []}
                ],
            }
        )
        self.call.assert_called_once_with(
            "answer", "--answer", f"Q1={answer}", "--resolver-token", "token", action=True
        )
        self.assertTrue(self.driver.answers[0]["explicit"])
        self.assertEqual(answer, self.driver.answers[0]["answer"])

    def test_each_decision_records_who_made_it(self):
        """#455: a served default or an approval is the harness deciding, never zero-decision autonomy,
        so result.json says which answers were the person's own and which gates the harness approved."""
        self.driver.explicit_answers = {"Q2": "The person's own answer."}
        self.driver.serve({"kind": "approve_plan", "token": "plan:1"})
        self.driver.serve(
            {
                "kind": "answer",
                "questions": [
                    {"id": "Q1", "proposed_default": "Keep data."},
                    {"id": "Q2", "proposed_default": "No default."},
                ],
            }
        )
        self.driver.serve({"kind": "review", "criteria": ["C1", "C2"], "token": "review:1", "question": "Accept?"})
        self.assertEqual(
            [("Q1", "proposed_default"), ("Q2", "person"), ("C1", "approval"), ("C2", "approval")],
            [(row["id"], row["source"]) for row in self.driver.answers],
        )
        self.assertEqual(
            [
                {"gate": "approve_plan", "token": "plan:1"},
                {"gate": "review", "criterion": "C1", "token": "review:1"},
                {"gate": "review", "criterion": "C2", "token": "review:1"},
            ],
            self.driver.approvals,
        )
        self.assertEqual(
            {"person_answers": 1, "default_answers": 1, "approvals": 3},
            stats.decisions([{"answers": self.driver.answers, "approvals": self.driver.approvals}]),
        )

    def test_the_planning_budget_feedback_is_recorded_as_a_gate_the_harness_passed(self):
        """The canned feedback that grants more planning review calls is the harness deciding for the person:
        counted with the approvals, and not an `answers` row, which oracles count as questions asked."""
        self.driver.serve({"kind": "planning_budget", "reason": "two calls"})
        self.call.assert_called_once_with("feedback", "--feedback", PLANNING_BUDGET_FEEDBACK, action=True)
        self.assertEqual([{"gate": "planning_budget", "feedback": PLANNING_BUDGET_FEEDBACK}], self.driver.approvals)
        self.assertEqual([], self.driver.answers)
        self.assertEqual(
            {"person_answers": 0, "default_answers": 0, "approvals": 1},
            stats.decisions([{"answers": self.driver.answers, "approvals": self.driver.approvals}]),
        )

    # AutoResolver's request for a person at a cost pause (#721), and the same pause once that request is consumed.
    COST_REQUEST = {
        "kind": "answer",
        "resolver_scope": "operational_exhaustion",
        "resolver_token": "token",
        "pause_origin": "PAUSED_COST_LIMIT",
        "questions": [{"id": "q1", "proposed_default": "", "options": []}],
    }
    COST_PAUSE = {"kind": "resume", "reason": "Spend so far $0.18 reached the $0.10 cost cap at stage boundary"}

    def cost_driver(self, flags=("--engine", "codex", "--max-cost-usd", "0.1"), acknowledge=None):
        return Driver(
            Path.cwd(),
            Path.cwd(),
            list(flags),
            {},
            autocode=[],
            max_steps=5,
            timeout_seconds=60,
            acknowledge=(("PAUSED_COST_LIMIT", ("--max-cost-usd", "100")),) if acknowledge is None else acknowledge,
        )

    def test_a_budget_pause_is_continued_once_with_the_person_s_own_flags(self):
        """[fake] acknowledge: the person raises their cost cap with --resume-paused, whether AutoResolver's
        request is still open or consumed, and later calls pass the raised cap, never the scenario's first one."""
        done = {"done": True, "needs": None, "status": "TASK_COMPLETE"}
        for status, need in (("WAITING_FOR_USER", self.COST_REQUEST), ("PAUSED_COST_LIMIT", self.COST_PAUSE)):
            with self.subTest(status=status):
                driver = self.cost_driver()
                paused = {"done": False, "needs": need, "status": status}
                with (
                    patch.object(driver, "call") as call,
                    patch.object(driver, "view", side_effect=[paused, done]),
                ):
                    self.assertTrue(driver.until_stopped()["done"])
                self.assertEqual(
                    [(("acknowledge", "--resume-paused", "--max-cost-usd", "100"), {})],
                    [(c.args, c.kwargs) for c in call.call_args_list],
                )
                self.assertEqual(["--engine", "codex", "--max-cost-usd", "100"], driver.flags)
                self.assertEqual(
                    [{"gate": "acknowledge", "pause": "PAUSED_COST_LIMIT", "flags": ["--max-cost-usd", "100"]}],
                    driver.approvals,
                )
                self.assertEqual([], driver.answers, "an acknowledgement is not an answer to a question")

    def test_the_same_pause_is_never_acknowledged_twice(self):
        driver = self.cost_driver()
        again = {"done": False, "needs": self.COST_REQUEST, "status": "WAITING_FOR_USER"}
        with patch.object(driver, "call") as call, patch.object(driver, "view", side_effect=[again, again]):
            self.assertIs(again, driver.until_stopped())
        self.assertEqual(["acknowledge"], [c.args[0] for c in call.call_args_list])
        with patch.object(driver, "call") as call, patch.object(driver, "view", return_value=again):
            self.assertIs(again, driver.until_stopped())
        call.assert_not_called()

    def test_a_pause_the_person_did_not_acknowledge_is_left_for_them(self):
        for acknowledge, status, need in (
            ((), "WAITING_FOR_USER", self.COST_REQUEST),
            ((), "PAUSED_COST_LIMIT", self.COST_PAUSE),
            (None, "WAITING_FOR_USER", {**self.COST_REQUEST, "pause_origin": "PAUSED_TIME_LIMIT"}),
            (None, "PAUSED_TIME_LIMIT", {"kind": "resume", "reason": "Saved active-time limit reached"}),
        ):
            with self.subTest(acknowledge=acknowledge, status=status, origin=need.get("pause_origin")):
                driver = self.cost_driver(acknowledge=acknowledge)
                paused = {"done": False, "needs": need, "status": status}
                with patch.object(driver, "call") as call, patch.object(driver, "view", return_value=paused):
                    self.assertIs(paused, driver.until_stopped())
                call.assert_not_called()
                self.assertEqual(["--engine", "codex", "--max-cost-usd", "0.1"], driver.flags)

    def test_acknowledged_flags_replace_the_scenario_s_value_in_either_form(self):
        given = ["--max-cost-usd", "100"]
        self.assertEqual(
            ["--x", "--max-cost-usd", "100", "--max-seconds", "60"],
            with_flag_values(["--x", "--max-cost-usd", "0.1", "--max-seconds", "60"], given),
        )
        self.assertEqual(["--max-cost-usd=100"], with_flag_values(["--max-cost-usd=0.1"], given))
        self.assertEqual(["--max-cost-usd", "100"], with_flag_values(["--max-cost-usd", "0.1"], ["--max-cost-usd=100"]))
        # An option the scenario does not pass is left to the saved run, which keeps what the person set.
        self.assertEqual(["--engine", "codex"], with_flag_values(["--engine", "codex"], given))


# Where each variant must fail for its own stated reason, the oracle summary of every variant (#59, plan B1).
CONTROL_SUMMARIES = {
    "feature-stock-refusals": {
        "seed": "3/6 checks; failing: hidden_tests_pass, new_command_tests_fail_on_original_code, "
        "readme_documents_move_and_remove",
        "reference": "6/6 checks",
        "broken/refusal-writes-store": "5/6 checks; failing: hidden_tests_pass",
        "broken/vacuous-refusal-tests": "5/6 checks; failing: new_command_tests_fail_on_original_code",
    },
    "feature-monorepo-package": {
        "seed": "4/7 checks; failing: hidden_tests_pass, new_tests_fail_on_original_code, readme_documents_option",
        "reference": "7/7 checks",
        "broken/edits-farewell-package": "6/7 checks; failing: nothing_changed_outside_the_package",
    },
    "bugfix-monorepo-package": {
        "seed": "4/6 checks; failing: hidden_tests_pass, new_tests_fail_on_original_code",
        "reference": "6/6 checks",
        "broken/fixes-search-copy-too": "5/6 checks; failing: nothing_changed_outside_the_package",
    },
}


def oracle_controls(scenario_id):
    """The oracle rejects the seed, accepts the reference and rejects every broken variant."""

    def test(self):
        scenario = catalog.load(scenario_id)
        if scenario.missing_tools():
            self.skipTest("requires " + ", ".join(scenario.missing_tools()))
        self.assertIsNotNone(scenario.reference, "every scenario needs a reference solution")
        rows = run.self_test(scenario)
        for name, ok, summary in rows:
            self.assertTrue(ok, f"{name}: {summary}")
        if scenario_id in CONTROL_SUMMARIES:
            self.assertEqual(CONTROL_SUMMARIES[scenario_id], {name: summary for name, _, summary in rows})

    return test


class OracleControlTests(unittest.TestCase):
    """One test per catalog scenario (filled in below), so the controls can run side by side."""


for _scenario in catalog.load_all():
    setattr(OracleControlTests, "test_" + _scenario.id.replace("-", "_"), oracle_controls(_scenario.id))


class CatalogTests(unittest.TestCase):
    def test_fake_starting_review_allowance_is_finite_and_explicit(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(catalog, "CATALOG", Path(directory)):
            root = Path(directory) / "review-budget"
            root.mkdir()
            base = 'title = "t"\ncategory = "greenfield"\nbrief = "Build it."\n[fake]\n'
            for value in ("true", "0", "1", "-1", "2.5", '"6"'):
                (root / "scenario.toml").write_text(base + f"plan_review_call_limit = {value}\n")
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, "finite integer of at least 2"):
                    catalog.load(root.name)
            for value in (2, 6):
                (root / "scenario.toml").write_text(base + f"plan_review_call_limit = {value}\n")
                self.assertEqual(value, catalog.load(root.name).fake_plan_review_call_limit)
            (root / "scenario.toml").write_text(base)
            self.assertIsNone(catalog.load(root.name).fake_plan_review_call_limit)

    def test_fake_review_allowance_uses_an_isolated_public_config_without_pause_flags(self):
        scenario = catalog.load("conformance-lifecycle")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ambient = root / "ambient" / "autocode" / "config.toml"
            ambient.parent.mkdir(parents=True)
            ambient.write_text("plan_review_call_limit = 19\n")
            for limit in (None, 6):
                case = root / str(limit)
                case.mkdir()
                fixture = dataclasses.replace(scenario, fake_plan_review_call_limit=limit)
                with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(ambient.parent.parent)}):
                    flags, environment = run.fake_setup(fixture, case, fixture.reference)
                provider_config = json.loads(Path(environment["SCENARIO_FAKE_CONFIG"]).read_text())
                self.assertEqual(fixture.fake_check, provider_config["check"])
                self.assertNotIn("--planning-review-call-limit", flags)
                self.assertNotIn("--plan-review-full", flags)
                # Every fake run has its own config home, so the ambient one never applies (#1938).
                config_home = Path(environment["XDG_CONFIG_HOME"])
                self.assertTrue(config_home.is_relative_to(case))
                user_config = config_home / "autocode" / "config.toml"
                if limit is None:
                    self.assertFalse(user_config.exists())
                else:
                    self.assertEqual({"plan_review_call_limit": 6}, tomllib.loads(user_config.read_text()))
                self.assertEqual("plan_review_call_limit = 19\n", ambient.read_text())

    def test_nothing_here_imports_autocode(self):
        """Oracles and the harness judge AutoCode from outside; importing it would let its bugs hide."""
        here = Path(__file__).resolve().parent
        autocode_modules = {path.stem for path in (here.parent / "tools").glob("*.py")} | {"tools", "autocode_cli"}
        autocode_modules -= {"__init__", "__main__"}
        for path in here.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                names = (
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module]
                    if isinstance(node, ast.ImportFrom) and node.module and not node.level
                    else []
                )
                for name in names:
                    self.assertNotIn(name.split(".")[0], autocode_modules, f"{path.relative_to(here)} imports {name}")

    def test_briefs_are_plain_text(self):
        for scenario in catalog.load_all():
            with self.subTest(scenario=scenario.id):
                self.assertTrue(scenario.brief)
                self.assertNotIn("\n#", scenario.brief, "headings would become part of the task text")

    def test_known_failures_say_why(self):
        for scenario in catalog.load_all():
            with self.subTest(scenario=scenario.id):
                self.assertIsInstance(scenario.known_failure, str)
                if scenario.known_failure:
                    self.assertGreater(len(scenario.known_failure), 20, "a known failure names what is missing")
                self.assertIn(scenario.expected, catalog.EXPECTED)

    def test_acknowledge_names_paused_statuses_and_the_flags_a_person_types(self):
        self.assertEqual((), catalog.load("greenfield-greeting-cli").fake_acknowledge)
        with tempfile.TemporaryDirectory() as root:
            original = catalog.CATALOG
            catalog.CATALOG = Path(root)
            self.addCleanup(setattr, catalog, "CATALOG", original)
            cases = {
                "good": ("greenfield", '{ PAUSED_COST_LIMIT = ["--max-cost-usd", "100"] }', None),
                "status": ("greenfield", '{ WAITING_FOR_USER = ["--max-cost-usd", "100"] }', "PAUSED_* status"),
                "empty": ("greenfield", "{ PAUSED_COST_LIMIT = [] }", "PAUSED_* status"),
                "text": ("greenfield", '{ PAUSED_COST_LIMIT = "--max-cost-usd 100" }', "PAUSED_* status"),
                "resume": ("greenfield", '{ PAUSED_COST_LIMIT = ["--resume-paused"] }', "PAUSED_* status"),
                "table": ("greenfield", '["--max-cost-usd", "100"]', "PAUSED_* status"),
                "conformance": ("conformance", '{ PAUSED_COST_LIMIT = ["--max-cost-usd", "100"] }', "single run"),
            }
            for name, (category, table, error) in cases.items():
                case = Path(root) / name
                case.mkdir()
                (case / "brief.md").write_text("Do it.")
                (case / "scenario.toml").write_text(
                    f'title = "t"\ncategory = "{category}"\n[fake]\ncheck = "true"\nacknowledge = {table}\n'
                )
                with self.subTest(name):
                    if error is None:
                        self.assertEqual(
                            (("PAUSED_COST_LIMIT", ("--max-cost-usd", "100")),), catalog.load(name).fake_acknowledge
                        )
                    else:
                        with self.assertRaisesRegex(ValueError, re.escape(error)):
                            catalog.load(name)

    def test_routing_table_loads_and_names_known_workflows(self):
        table = routing.load()
        catalog.load(table["seed"])
        self.assertGreaterEqual(len(table["prompts"]), 10)
        self.assertEqual(
            set(routing.WORKFLOWS),
            {p["workflow"] for p in table["prompts"]},
            "every workflow needs at least one prompt",
        )


class JudgeTests(unittest.TestCase):
    passing = verdict.OracleResult([verdict.Check("a", True)])
    failing = verdict.OracleResult([verdict.Check("a", False)])

    def test_verdicts(self):
        self.assertEqual(verdict.PASS, verdict.judge("TASK_COMPLETE", self.passing)[0])
        self.assertEqual(verdict.FALSE_COMPLETE, verdict.judge("TASK_COMPLETE", self.failing)[0])
        self.assertEqual(verdict.HONEST_BLOCKER, verdict.judge("PAUSED_BUDGET", self.passing)[0])
        self.assertEqual(verdict.ERROR, verdict.judge("RUNNING", self.passing)[0])
        self.assertEqual(verdict.ERROR, verdict.judge("TASK_COMPLETE", verdict.OracleResult(error="boom"))[0])

    def test_a_scenario_that_expects_a_stop(self):
        self.assertEqual(verdict.PASS, verdict.judge("PAUSED_HUMAN", self.passing, "stop")[0])
        self.assertEqual(verdict.HONEST_BLOCKER, verdict.judge("PAUSED_HUMAN", self.failing, "stop")[0])
        self.assertEqual(verdict.FALSE_COMPLETE, verdict.judge("TASK_COMPLETE", self.passing, "stop")[0])
        self.assertEqual(verdict.PASS, verdict.judge("WAITING_FOR_USER", self.passing, "any")[0])
        self.assertEqual(verdict.PASS, verdict.judge("TASK_COMPLETE", self.passing, "any")[0])

    def test_resolver_waiting_for_manual_resume_is_an_honest_blocker(self):
        for result in (self.passing, self.failing):
            with self.subTest(passed=result.passed):
                self.assertEqual(verdict.HONEST_BLOCKER, verdict.judge("RESOLVER_PENDING", result)[0])
        self.assertEqual(verdict.ERROR, verdict.judge("RESOLVER_PENDING", verdict.OracleResult(error="boom"))[0])
        self.assertEqual(verdict.ERROR, verdict.judge("RESOLVER_PENDING_UNKNOWN", self.passing)[0])

    def test_an_oracle_with_no_checks_does_not_pass(self):
        self.assertFalse(verdict.OracleResult([]).passed)


class ApprovalProvenanceTests(unittest.TestCase):
    """#722: a completed run's `--status --json` lists each approval with who made it (harness.approval_provenance)."""

    token = "r2:" + "a" * 64

    def row(self, kind="plan", actor="user_cli", pause_status=None, **provenance):
        recorded = {
            "git_user_name": PERSON_NAME,
            "git_user_email": PERSON_EMAIL,
            "host": socket.gethostname() or None,
            "command": "autocode --workspace --run-dir --approve-goal",
            "claimed_as": None,
            **provenance,
        }
        return {
            "kind": kind,
            "at": "2026-10-16T08:00:00Z",
            "actor": actor,
            "contract_token": self.token if kind == "plan" else None,
            "criterion": "C1" if kind == "review" else None,
            "pause_status": pause_status,
            "provenance": recorded,
        }

    def record(self, rows, gates=(), status="TASK_COMPLETE"):
        return {"report_expected": True, "view": {"status": status, "approvals": rows}, "approvals": list(gates)}

    def failures(self, record):
        return [check.detail for check in approval_provenance.checks(record) if not check.ok]

    def test_approvals_that_name_the_person_pass(self):
        policy = {**self.row(actor="workflow_policy"), "provenance": None}
        review = self.row("review", command="autocode --workspace --run-dir --approve-review --review-token")
        resume = self.row(
            "resume", "user_cli", "PAUSED_COST_LIMIT", command="autocode resume --run-dir --as", claimed_as="Ana"
        )
        gates = [
            {"gate": "approve_plan", "token": self.token},
            {"gate": "review", "criterion": "C1", "token": "review-token"},
            {"gate": "planning_budget", "feedback": "more"},
            {"gate": "acknowledge", "pause": "PAUSED_COST_LIMIT", "flags": ["--max-cost-usd", "100"]},
        ]
        (check,) = approval_provenance.checks(self.record([self.row(), review, resume, policy], gates))
        self.assertEqual("approval provenance: task", check.name)
        self.assertTrue(check.ok, check.detail)
        self.assertIn("4 recorded (plan, review, resume, plan)", check.detail)
        self.assertTrue(approval_provenance.checks(self.record([]))[0].ok)  # a review or discussion approves nothing

    def test_an_approval_without_the_person_s_provenance_fails(self):
        for name, row, said in (
            ("no provenance", {**self.row(), "provenance": None}, "row 1 (plan) records no provenance"),
            ("no Git name", self.row(git_user_name=None), "git_user_name is None, not 'Scenario Person'"),
            ("the seed's author", self.row(git_user_email="scenario@example.test"), "git_user_email"),
            ("another host", self.row(host="elsewhere.invalid"), "host is 'elsewhere.invalid'"),
            ("a token value", self.row(command="autocode --approve-goal " + self.token), "is not autocode and option"),
            ("no command", self.row(command=None), "command None"),
            ("no actor", self.row(actor="runner"), "neither a person nor the workflow policy"),
            ("policy naming a person", self.row(actor="workflow_policy"), "under the workflow policy but names"),
        ):
            with self.subTest(name):
                (failure,) = self.failures(self.record([row]))
                self.assertIn(said, failure)

    def test_each_approval_the_harness_gave_must_be_listed(self):
        plan = {"gate": "approve_plan", "token": self.token}
        review = {"gate": "review", "criterion": "C1", "token": "review-token"}
        policy = {**self.row(actor="workflow_policy"), "provenance": None}
        for name, rows, gates, said in (
            ("nothing listed", [], [plan], f"the harness approved plan {self.token}, which no plan row lists"),
            ("another plan", [self.row()], [{**plan, "token": "r3:b"}], "approved plan r3:b"),
            ("no review row", [self.row()], [plan, review], "the review of C1, which no review row lists"),
            ("another criterion", [self.row(), self.row("review")], [plan, {**review, "criterion": "C2"}], "of C2"),
            ("only the policy's", [policy], [plan], "which no plan row lists"),
        ):
            with self.subTest(name):
                (failure,) = self.failures(self.record(rows, gates))
                self.assertIn(said, failure)

    def test_each_budget_pause_the_person_acknowledged_must_have_its_resume_row(self):
        """[fake] acknowledge (Driver.acknowledge_pause) is matched to the resume row naming that pause."""
        acknowledged = {"gate": "acknowledge", "pause": "PAUSED_COST_LIMIT", "flags": ["--max-cost-usd", "100"]}
        command = "autocode --workspace --run-dir --resume-paused --max-cost-usd"
        resumed = self.row("resume", "user_cli", "PAUSED_COST_LIMIT", command=command)
        self.assertEqual([], self.failures(self.record([self.row(), resumed], [acknowledged])))
        said = "the harness acknowledged PAUSED_COST_LIMIT, which no resume row lists"
        for name, rows in (
            ("nothing listed", []),
            ("no resume row", [self.row()]),
            ("another pause resumed", [self.row("resume", "user_cli", "PAUSED_TIME_LIMIT", command=command)]),
            ("a resume naming no pause", [self.row("resume", command=command)]),
            ("a plan row naming the pause", [self.row("plan", "user_cli", "PAUSED_COST_LIMIT")]),
            (
                "the pause resumed by no person",
                [{**self.row("resume", "workflow_policy", "PAUSED_COST_LIMIT"), "provenance": None}],
            ),
        ):
            with self.subTest(name):
                self.assertEqual([said], self.failures(self.record(rows, [acknowledged])))

    def test_the_driver_s_acknowledgement_reaches_the_check(self):
        """Driver.acknowledge_pause records the gate run.run_record hands to the check, which then needs its row."""
        driver = self.driver_at_a_cost_pause()
        with patch.object(driver, "call"):
            driver.acknowledge_pause("PAUSED_COST_LIMIT")
        view = {"status": "TASK_COMPLETE", "approvals": []}
        driver.view, driver.run_dir, driver.turn_marks = (lambda: view), Path("r1"), []
        record = run.run_record(driver, {"stages": []})
        self.assertEqual(
            ["the harness acknowledged PAUSED_COST_LIMIT, which no resume row lists"], self.failures(record)
        )
        view["approvals"] = [self.row("resume", "user_cli", "PAUSED_COST_LIMIT", command="autocode --resume-paused")]
        self.assertEqual([], self.failures(run.run_record(driver, {"stages": []})))

    def driver_at_a_cost_pause(self):
        return Driver(
            Path.cwd(),
            Path.cwd(),
            ["--max-cost-usd", "0.1"],
            {},
            autocode=[],
            max_steps=5,
            timeout_seconds=60,
            acknowledge=(("PAUSED_COST_LIMIT", ("--max-cost-usd", "100")),),
        )

    def test_the_run_record_carries_the_harness_s_approvals_to_the_check(self):
        """run.run_record hands the gates the harness passed to the check; without them nothing is matched."""
        gates = [{"gate": "approve_plan", "token": self.token}]
        view = {"status": "TASK_COMPLETE", "approvals": []}
        driver = argparse.Namespace(
            run_dir=Path("r1"), view=lambda: view, answers=[], approvals=gates, steps=[], turn_marks=[]
        )
        record = run.run_record(driver, {"stages": []})
        self.assertEqual(gates, record.get("approvals"))
        self.assertEqual([f"the harness approved plan {self.token}, which no plan row lists"], self.failures(record))

    def test_only_a_completed_executed_run_is_judged(self):
        bad = [{**self.row(), "provenance": None}]
        self.assertEqual([], approval_provenance.checks(None))
        self.assertEqual([], approval_provenance.checks({**self.record(bad), "report_expected": False}))
        self.assertEqual([], approval_provenance.checks(self.record(bad, status="PAUSED_INVALID_OUTPUT")))
        missing = {"report_expected": True, "view": {"status": "TASK_COMPLETE"}}
        self.assertEqual(["the status view has no approvals list"], self.failures(missing))

    def test_each_completed_component_run_is_judged(self):
        good, bad = [self.row()], [{**self.row(), "provenance": None}]
        components = {
            "report_expected": True,
            "components": {
                "components": {
                    "api": {"view": {"status": "TASK_COMPLETE", "approvals": bad}},
                    "web": {"view": {"status": "PAUSED", "approvals": bad}},
                }
            },
            "architecture_view": {"status": "TASK_COMPLETE", "approvals": good},
        }
        # An architecture run that has not completed is not judged.
        waiting = {"status": "AWAITING_GOAL_APPROVAL", "approvals": bad}
        component = ("approval provenance: api", False)
        for name, record, expected in (
            ("components", components, [("approval provenance: architecture", True), component]),
            ("architecture waiting", {**components, "architecture_view": waiting}, [component]),
        ):
            with self.subTest(name):
                self.assertEqual(expected, [(row.name, row.ok) for row in approval_provenance.checks(record)])

    def test_each_task_run_of_a_components_run_must_list_the_gates_given_to_it(self):
        """One approvals list holds the gates the harness gave all of a components run's task runs; each names
        the run it was given to (driver.attribute) and must be listed among that run's own rows."""
        s_token, t_token = "r2:" + "5" * 64, "r2:" + "7" * 64

        def listing(token):
            return {"status": "TASK_COMPLETE", "approvals": [{**self.row(), "contract_token": token}]}

        def plan(token, run_dir):
            return {"gate": "approve_plan", "token": token, "run_dir": run_dir}

        components = {
            "report_expected": True,
            "architecture_run_dir": "/runs/architecture",
            "architecture_view": listing(self.token),
            "components": {
                "components": {
                    "api": {"run_dir": "/runs/api", "view": listing(s_token)},
                    "web": {"run_dir": "/runs/web", "view": listing(t_token)},
                }
            },
            "approvals": [
                plan(self.token, "/runs/architecture"),
                {"gate": "auto_approve", "call": "components"},
                plan(s_token, "/runs/api"),
                plan(t_token, "/runs/web"),
            ],
        }

        def judged(record):
            return [(check.name.removeprefix("approval provenance: "), check.detail) for check in self.failing(record)]

        # Each run passes, and says it lists the gates given to it.
        listed = "; each gate the harness passed for this run is listed (approve_plan)"
        checks = approval_provenance.checks(components)
        self.assertTrue(all(check.ok for check in checks), [check.detail for check in checks])
        self.assertEqual(
            [("architecture", True), ("api", True), ("web", True)],
            [(check.name.removeprefix("approval provenance: "), check.detail.endswith(listed)) for check in checks],
        )
        api_gate, web_gate = components["approvals"][2], components["approvals"][3]
        missing = "the harness approved plan {}, which no plan row lists"
        for name, record, failures in (
            (
                "api's gate attributed to web",
                {
                    **components,
                    "approvals": [*components["approvals"][:2], {**api_gate, "run_dir": "/runs/web"}, web_gate],
                },
                [("web", missing.format(s_token))],
            ),
            (
                "api lists web's approval and web lists api's",
                {
                    **components,
                    "components": {
                        "components": {
                            "api": {"run_dir": "/runs/api", "view": listing(t_token)},
                            "web": {"run_dir": "/runs/web", "view": listing(s_token)},
                        }
                    },
                },
                [("api", missing.format(s_token)), ("web", missing.format(t_token))],
            ),
            (
                "the architecture's gate attributed to a component",
                {**components, "approvals": [plan(self.token, "/runs/api"), api_gate, web_gate]},
                [("api", missing.format(self.token))],
            ),
            (
                "the architecture run lists nothing",
                {**components, "architecture_view": {"status": "TASK_COMPLETE", "approvals": []}},
                [("architecture", missing.format(self.token))],
            ),
        ):
            with self.subTest(name):
                self.assertEqual(failures, judged(record))

    def failing(self, record):
        return [check for check in approval_provenance.checks(record) if not check.ok]

    def test_every_executed_run_s_oracle_result_holds_the_check(self):
        scenario = argparse.Namespace(oracle=lambda: lambda project, scenario: [verdict.Check("delivered", True)])
        for row, ok in ((self.row(), True), ({**self.row(), "provenance": None}, False)):
            with self.subTest(ok=ok):
                result = verdict.evaluate(scenario, Path("."), {**self.record([row]), "run_dir": "."})
                self.assertEqual(ok, {check.name: check.ok for check in result.checks}["approval provenance: task"])
                self.assertFalse(result.error, result.error)

    def test_a_scenario_project_names_the_person_who_is_not_the_seed_s_author(self):
        with tempfile.TemporaryDirectory() as root:
            project = materialize(catalog.load("bugfix-trivial").seed, Path(root) / "project")

            def git(*args):
                return subprocess.run(
                    ["git", "-C", str(project), *args], capture_output=True, text=True, check=True
                ).stdout.strip()

            self.assertEqual(
                [PERSON_NAME, PERSON_EMAIL], [git("config", "--get", f"user.{key}") for key in ("name", "email")]
            )
            self.assertEqual("Scenario <scenario@example.test>", git("log", "-1", "--format=%an <%ae>"))


class PlanRoundTripTests(unittest.TestCase):
    """#720 acceptance 1: before the harness approves a catalog run's plan, it exports the plan and reads the file
    back with `autocode plan --check`, and the file must reproduce the plan's goal hash (harness.plan_round_trip).
    Judged through verdict.evaluate, beside the other postconditions."""

    token = "r2:" + "b" * 64
    goal = "c" * 64

    def trip(self, export=None, check=None, changed=()):
        exported = {
            "exit": 0,
            "path": "plan-files/01.md",
            "revision": 2,
            "approved": False,
            "goal_hash": self.goal,
            "written_goal_hash": self.goal,
            "masked": False,
            "message": "Wrote plan revision 2",
        }
        checked = {
            "exit": 0,
            "path": "plan-files/01.md",
            "goal_hash": self.goal,
            "exported_hash": self.goal,
            "revision": 2,
            "matches": True,
            "stale_line": None,
            "message": "plan-files/01.md: goal hash",
        }
        return {
            "file": "plan-files/01.md",
            "export": {**exported, **(export or {})},
            "check": {**checked, **(check or {})},
            "run_changed": list(changed),
            "seconds": 3.1,
        }

    def judged(self, trips, approved=None, **record):
        """The plan round trip checks verdict.evaluate gives a completed run whose plan approvals recorded ``trips``."""
        gates = [{"gate": "approve_plan", "token": self.token, "plan_file": trip} for trip in trips]
        view = {"status": "TASK_COMPLETE", "approvals": []}
        if approved is not None:
            view["approved_contract"] = {"token": self.token, "goal_hash": approved}
        run = {"report_expected": True, "run_dir": ".", "view": view, "approvals": gates, **record}
        scenario = argparse.Namespace(oracle=lambda: lambda project, scenario: [verdict.Check("delivered", True)])
        result = verdict.evaluate(scenario, Path("."), run)
        self.assertFalse(result.error, result.error)
        return [check for check in result.checks if check.name == "plan round trip"]

    def test_a_plan_file_that_reads_back_with_the_plan_s_goal_hash_passes(self):
        (check,) = self.judged([self.trip()], approved=self.goal)
        self.assertTrue(check.ok, check.detail)
        self.assertIn("1 plan(s) exported at the approval stop and read back by `autocode plan --check`", check.detail)
        self.assertIn(f"revision 2 goal hash {self.goal}, approved", check.detail)

    def test_a_round_trip_that_does_not_reproduce_the_goal_hash_fails(self):
        other = "e" * 64
        error = {"error": {"code": "plan_error", "message": "has no plan yet"}}
        for name, trip, approved, said in (
            ("export failed", self.trip({"exit": 2, **error}), None, "`autocode plan --export` exited 2: has no plan"),
            (
                "stale file",
                self.trip(check={"exit": 2, "error": {"code": "stale_plan_text", "message": "line 9"}}),
                None,
                "`autocode plan --check` exited 2: line 9",
            ),
            ("read back otherwise", self.trip(check={"goal_hash": other}), None, f"as goal hash {other}, not the"),
            (
                # What the CLI reports for any file written with another goal hash: `autocode plan --export` sets
                # `masked` whenever the hashes differ, so a mask that took plan text for a credential (the
                # `risk-<sha>` ids #1307 fixed) looks exactly like this, and `--goal-file` refuses the file.
                "masked on export",
                self.trip({"written_goal_hash": other, "masked": True}, {"goal_hash": other, "matches": False}),
                None,
                f"reads back as goal hash {other}, not the run's plan's {self.goal} (the export reported text masked",
            ),
            ("front matter otherwise", self.trip(check={"matches": False}), None, "not the run's plan's"),
            ("another revision", self.trip(check={"revision": 1}), None, "names revision 1, not the exported 2"),
            ("run changed", self.trip(changed=["state.json"]), None, "changed the run: state.json"),
            ("approved otherwise", self.trip(), other, f"approved plan's goal hash as {other}, not the exported"),
        ):
            with self.subTest(name):
                (check,) = self.judged([trip], approved=approved)
                self.assertFalse(check.ok)
                self.assertIn(f"plan {self.token}: ", check.detail)
                self.assertIn(said, check.detail)

    def test_only_an_executed_run_that_reached_a_plan_approval_is_judged(self):
        self.assertEqual([], self.judged([]))
        self.assertEqual([], self.judged([self.trip()], report_expected=False))
        bare = {"report_expected": True, "approvals": [{"gate": "approve_plan", "token": self.token}]}
        scenario = argparse.Namespace(oracle=lambda: lambda project, scenario: [verdict.Check("delivered", True)])
        self.assertNotIn(
            "plan round trip", [check.name for check in verdict.evaluate(scenario, Path("."), bare).checks]
        )
        # A run that stopped after the approval is judged on what was exported all the same.
        (check,) = self.judged([self.trip(changed=["state.json"])], view={"status": "PAUSED_BUDGET"})
        self.assertFalse(check.ok)

    def test_a_components_scenario_judges_its_architecture_run_s_plan(self):
        """run.run_components judges the plan file round trip of the architecture run's plan approval, which
        components_driver's architecture Driver records, beside the --auto-approve rows of the components calls."""
        root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        scenario = argparse.Namespace(
            oracle=lambda: lambda project, scenario: [verdict.Check("delivered", True)], expected="complete"
        )
        for name, trip, outcome in (
            ("reproduced", self.trip(), verdict.PASS),
            ("run changed", self.trip(changed=["state.json"]), verdict.FALSE_COMPLETE),
        ):
            with self.subTest(name):
                out = root / name
                out.mkdir()
                architecture = Mock(
                    run_dir=out / "architecture-run",
                    answers=[],
                    approvals=[
                        {"gate": "approve_plan", "token": self.token, "plan_file": trip},
                        {"gate": run.components_driver.AUTO_APPROVE_GATE, "call": "components"},
                    ],
                    steps=[],
                )
                # Not complete, so neither its evidence report nor its provenance is checked: the round trip alone.
                architecture.view.return_value = {"approved_contract": {"token": self.token, "goal_hash": self.goal}}

                def drive(scenario, args, out, project, flags, env, decisions, architecture=architecture):
                    decisions.update(answers=architecture.answers, approvals=architecture.approvals)
                    return architecture, out / "product", {"exit_code": 0, "components": {}}, []

                with patch.object(run.components_driver, "drive", drive):
                    result = run.run_components(
                        scenario, argparse.Namespace(local_docker=False), out, {}, out / "project", [], {}
                    )
                self.assertEqual(outcome, result["verdict"], result["summary"])
                (check,) = [check for check in result["checks"] if check["name"] == "plan round trip"]
                self.assertEqual(outcome == verdict.PASS, check["ok"], check["detail"])
                self.assertIn(f"goal hash {self.goal}, approved" if check["ok"] else "changed the run", check["detail"])

    def plan_cli(self, root: Path, export=None):
        """Stands in for `autocode`: `plan --export` (``export`` changes what it does), `plan --check`, approval."""
        commands = []

        def cli(command, **_):
            args = list(command[1:])
            commands.append(args)
            if args[:2] == ["plan", "--export"]:
                if export:
                    return export(args)
                Path(args[2]).write_text("---\n")
                payload = {"revision": 2, "goal_hash": self.goal, "written_goal_hash": self.goal, "masked": False}
                return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
            if args[:2] == ["plan", "--check"]:
                payload = {"goal_hash": self.goal, "exported_hash": self.goal, "revision": 2, "matches": True}
                return subprocess.CompletedProcess(command, 0, json.dumps(payload, indent=2), "")
            return subprocess.CompletedProcess(command, 0, "", "")

        return cli, commands

    def driver(self, root: Path, **options) -> Driver:
        driver = Driver(
            root / "project", root, [], {}, autocode=["autocode"], max_steps=5, timeout_seconds=60, **options
        )
        driver.run_dir = root / "run"
        driver.run_dir.mkdir(parents=True)
        (driver.run_dir / "state.json").write_text("{}")
        return driver

    def test_the_driver_exports_and_reads_back_each_plan_before_approving_it(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        driver = self.driver(root, round_trip_plans=True)
        cli, commands = self.plan_cli(root)
        with patch("harness.driver.run_cli", cli):
            driver.serve({"kind": "approve_plan", "token": self.token})
        path = root / "plan-files" / "01.md"
        where = ["--workspace", str(root / "project"), "--run-dir", str(driver.run_dir)]
        self.assertEqual(
            [
                ["plan", "--export", str(path), *where, "--json"],
                ["plan", "--check", str(path), "--json"],
                [*where, "--approve-goal", self.token],
            ],
            commands,
        )
        self.assertTrue(path.is_file())
        # The export and the check are the harness's own: no step, so neither the step budget nor an oracle's
        # count of CLI calls sees them; the time budget does.
        self.assertEqual(["approve-plan"], [step["kind"] for step in driver.steps])
        self.assertEqual({"plan-export", "plan-check"}, set(driver.unrecorded))
        (gate,) = driver.approvals
        self.assertEqual(
            (self.token, str(path), []), (gate["token"], gate["plan_file"]["file"], gate["plan_file"]["run_changed"])
        )
        self.assertEqual(
            (0, self.goal, 0, self.goal),
            (
                gate["plan_file"]["export"]["exit"],
                gate["plan_file"]["export"]["goal_hash"],
                gate["plan_file"]["check"]["exit"],
                gate["plan_file"]["check"]["goal_hash"],
            ),
        )
        (check,) = self.judged([gate["plan_file"]])
        self.assertTrue(check.ok, check.detail)
        # A driver outside a catalog run approves without exporting, as before.
        plain = self.driver(root / "plain")
        cli, commands = self.plan_cli(root / "plain")
        with patch("harness.driver.run_cli", cli):
            plain.serve({"kind": "approve_plan", "token": self.token})
        self.assertEqual(
            [
                [
                    "--workspace",
                    str(root / "plain/project"),
                    "--run-dir",
                    str(plain.run_dir),
                    "--approve-goal",
                    self.token,
                ]
            ],
            commands,
        )
        self.assertEqual([{"gate": "approve_plan", "token": self.token}], plain.approvals)

    def test_a_failed_export_or_a_changed_run_is_recorded_and_the_plan_still_approved(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        for name, export, said in (
            (
                "refused",
                lambda args: subprocess.CompletedProcess(
                    args, 2, "", '{"error": {"code": "plan_error", "message": "no plan yet"}}\n'
                ),
                "`autocode plan --export` exited 2: no plan yet",
            ),
            ("writes into the run", None, "changed the run: state.json"),
        ):
            with self.subTest(name):
                driver = self.driver(root / name, round_trip_plans=True)

                def writes(args, run_dir=driver.run_dir):
                    (run_dir / "state.json").write_text('{"changed": true}')
                    Path(args[2]).write_text("---\n")
                    payload = {"revision": 2, "goal_hash": self.goal, "written_goal_hash": self.goal}
                    return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

                cli, commands = self.plan_cli(root / name, export or writes)
                with patch("harness.driver.run_cli", cli):
                    driver.serve({"kind": "approve_plan", "token": self.token})
                self.assertIn("--approve-goal", commands[-1])
                # A refused export leaves nothing to read back.
                self.assertEqual(3 if export is None else 2, len(commands))
                (check,) = self.judged([driver.approvals[0]["plan_file"]])
                self.assertFalse(check.ok)
                self.assertIn(said, check.detail)


class RunChecksTests(unittest.TestCase):
    """Run-level checks judge how AutoCode worked; without a run there is nothing to judge."""

    def test_no_run_means_no_checks(self):
        self.assertEqual([], oracle.run_checks(None, workflow="review", no_build=True))

    def test_todays_build_pipeline_fails_a_review(self):
        run_record = {
            "view": {"workflow": None},
            "cli_calls": ["start", "approve-plan", "resume"],
            "stages": ["requirements_gather", "astra_discovery", "terra", "sol"],
            "answers": [],
        }
        failed = {
            c.name
            for c in oracle.run_checks(run_record, workflow="review", no_build=True, after_requirements="review_change")
            if not c.ok
        }
        self.assertEqual(
            {
                "workflow_recognized",
                "no_builder_dispatched",
                "no_build_plan_approval_requested",
                "job_starts_after_requirements",
            },
            failed,
        )

    def test_a_recognized_read_only_review_passes(self):
        run_record = {
            "view": {"workflow": "review"},
            "cli_calls": ["start", "resume"],
            "stages": ["requirements_gather", "review_change", "sol"],
            "answers": [],
        }
        self.assertTrue(
            all(
                c.ok
                for c in oracle.run_checks(
                    run_record, workflow="review", no_build=True, after_requirements="review_change", max_questions=0
                )
            )
        )

    def test_requirements_runs_once_and_first(self):
        """Every flow starts at Requirements, which names the job (owner decision 2026-10-22, #1860)."""
        for stages, ok in (
            (["requirements_gather", "investigate_bug", "astra_discovery"], True),
            (["requirements_gather", "requirements_gather_report_repair", "investigate_bug", "orchestrator"], True),
            (["recognize_workflow", "requirements_gather", "investigate_bug"], False),
            (["investigate_bug", "astra_discovery"], False),
            (["requirements_gather", "investigate_bug", "requirements_gather", "astra_discovery"], False),
        ):
            with self.subTest(stages=stages):
                run_record = {"view": {"workflow": "bugfix"}, "stages": stages, "answers": []}
                checks = oracle.run_checks(run_record, workflow="bugfix", after_requirements="investigate_bug")
                self.assertEqual(ok, all(c.ok for c in checks), [c for c in checks if not c.ok])

    def test_a_planned_fix_needs_plan_review_and_the_users_approval(self):
        planned = {
            "view": {"workflow": "bugfix"},
            "cli_calls": ["start", "approve-plan", "resume"],
            "steps": [{"kind": "approve-plan", "exit": 0}],
            "stages": ["investigate_bug", "astra_discovery", "astra_challenge", "terra"],
            "answers": [],
        }
        self.assertTrue(all(c.ok for c in oracle.run_checks(planned, workflow="bugfix", plan_approved=True)))
        small = {**planned, "cli_calls": ["start", "resume"], "steps": [], "stages": ["investigate_bug", "terra"]}
        failed = {c.name for c in oracle.run_checks(small, workflow="bugfix", plan_approved=True) if not c.ok}
        self.assertEqual({"plan_reviewed", "plan_approved_by_user"}, failed)

    def test_attempted_or_rejected_plan_approval_is_not_accepted(self):
        for steps in ([], [{"kind": "approve-plan", "exit": 2}], [{"kind": "resume", "exit": 0}]):
            with self.subTest(steps=steps):
                run = {
                    "view": {"workflow": "build"},
                    "cli_calls": ["start", "approve-plan"],
                    "steps": steps,
                    "stages": ["astra_challenge", "glm_revise"],
                }
                checks = {row.name: row for row in oracle.run_checks(run, workflow="build", plan_approved=True)}
                self.assertFalse(checks["plan_approved_by_user"].ok)

    def test_successful_approval_remains_evidence_after_completion_or_archival(self):
        run = {
            "view": {"workflow": "build", "done": True, "status": "COMPLETE"},
            "cli_calls": ["start", "approve-plan", "resume"],
            "steps": [{"kind": "approve-plan", "exit": 0}],
            "stages": ["astra_challenge", "glm_revise", "terra"],
        }
        checks = oracle.run_checks(run, workflow="build", plan_approved=True)
        self.assertTrue(all(row.ok for row in checks))

    def test_the_stage_budget_counts_only_model_stages(self):
        run_record = {
            "view": {"workflow": "bugfix"},
            "cli_calls": ["start"],
            "answers": [],
            "stages": [
                "requirements_gather",
                "investigate_bug",
                "orchestrator",
                "terra",
                "regression_proof",
                "sol",
                "astra_review",
            ],
            "model_stages": ["requirements_gather", "investigate_bug", "terra", "sol", "astra_review"],
        }
        (check,) = [
            c for c in oracle.run_checks(run_record, workflow="bugfix", max_model_stages=5) if c.name == "stage_budget"
        ]
        self.assertTrue(check.ok, check.detail)
        del run_record["model_stages"]  # older records: everything but orchestration counts
        (check,) = [
            c for c in oracle.run_checks(run_record, workflow="bugfix", max_model_stages=5) if c.name == "stage_budget"
        ]
        self.assertFalse(check.ok)


class MonorepoPackageOracleTests(unittest.TestCase):
    """feature-monorepo-package judges the run's own report of changes outside its package (#711): every one
    named in an open blocking runner finding, none invented, and nothing to judge in a run without --package."""

    FINDING = "Files outside the package packages/greet/ changed: "

    def score(self, overlay, findings, package="packages/greet"):
        from harness.project import materialize

        scenario = catalog.load("feature-monorepo-package")
        with tempfile.TemporaryDirectory(prefix="monorepo-oracle-test-") as root:
            project = materialize(scenario.seed, Path(root) / "project", scenario.dir / overlay)
            record = {
                "status": "RESOLVER_PENDING",
                "view": {"workflow": "build", "package": package, "evidence": {"findings": findings}},
                "stages": ["astra_challenge", "terra", "sol", "astra_review"],
                "steps": [{"kind": "approve-plan", "exit": 0}],
            }
            result = verdict.evaluate(scenario, project, record)
        self.assertEqual("", result.error)
        return {check.name: check for check in result.checks}

    def finding(self, text, status="open"):
        return {"id": "F-1", "source": "runner", "status": status, "blocking": True, "finding": self.FINDING + text}

    def other_runner_finding(self, text):
        # The run's other runner finding (autocode_risk_findings) is not the package check's report.
        return {**self.finding(""), "finding": "Runner lifecycle replay failed after " + text}

    def test_every_change_outside_the_package_must_be_named_in_the_open_blocking_package_finding(self):
        both = "packages/farewell/farewell.py (modified), packages/farewell/test_farewell_formal.py (added). Restore"
        one = "packages/farewell/farewell.py (modified). Restore"
        for findings, ok in (
            ([self.finding(both)], True),
            ([self.finding(one)], False),
            ([self.finding(both, status="closed")], False),
            ([{**self.finding(both), "source": "reviewer"}], False),
            ([{**self.finding(both), "blocking": False}], False),
            ([self.other_runner_finding(both)], False),
            ([], False),
        ):
            with self.subTest(findings=findings):
                checks = self.score("broken/edits-farewell-package", findings)
                self.assertEqual(ok, checks["outside_changes_reported"].ok, checks["outside_changes_reported"])
                self.assertFalse(checks["nothing_changed_outside_the_package"].ok)

    def test_a_report_with_nothing_outside_is_invented_and_a_run_without_the_package_is_not_judged(self):
        checks = self.score("reference", [self.finding("README.md (modified). Restore")])
        self.assertFalse(checks["outside_changes_reported"].ok)
        self.assertTrue(self.score("reference", [])["outside_changes_reported"].ok)
        other = [self.other_runner_finding("packages/greet/greet.py (modified)")]
        self.assertTrue(self.score("reference", other)["outside_changes_reported"].ok)
        # A live profile is not started with --package: only the delivered files are judged.
        self.assertNotIn("outside_changes_reported", self.score("broken/edits-farewell-package", [], package=None))


class BugfixMonorepoPackageOracleTests(unittest.TestCase):
    """bugfix-monorepo-package (#711): a --package bug fix investigated first, judged as feature-monorepo-package
    judges its package. The runner's diagnosis note (the path the run's saved investigation names) is the
    workflow's own file, not a change outside the package; any other file under docs/bugs/ is one."""

    FINDING = "Files outside the package packages/invoices/ changed: "
    NOTE = "docs/bugs/scripted-diagnosis.json"
    STAGES = ["requirements_gather", "investigate_bug", "astra_discovery", "astra_challenge", "terra", "sol"]

    def score(self, overlay, findings, stages=STAGES, package="packages/invoices", model_stages=None, extra=()):
        from harness.project import materialize

        scenario = catalog.load("bugfix-monorepo-package")
        with tempfile.TemporaryDirectory(prefix="bugfix-monorepo-oracle-test-") as root:
            project = materialize(scenario.seed, Path(root) / "project", scenario.dir / overlay)
            for path in (self.NOTE, *extra):
                (project / path).parent.mkdir(parents=True, exist_ok=True)
                (project / path).write_text('{"reproduced": true}\n')
            run_dir = Path(root) / "run"
            run_dir.mkdir()
            (run_dir / "state.json").write_text(json.dumps({"investigation": {"note_path": self.NOTE}}))
            record = {
                "status": "RESOLVER_PENDING",
                "view": {"workflow": "bugfix", "package": package, "evidence": {"findings": findings}},
                "stages": stages,
                "run_dir": str(run_dir),
                "steps": [{"kind": "approve-plan", "exit": 0}],
                "answers": [],
                **({"model_stages": model_stages} if model_stages is not None else {}),
            }
            result = verdict.evaluate(scenario, project, record)
        self.assertEqual("", result.error)
        return {check.name: check for check in result.checks}

    def finding(self, text, status="open"):
        return {"id": "F-1", "source": "runner", "status": status, "blocking": True, "finding": self.FINDING + text}

    def test_the_other_packages_copy_must_be_named_and_the_diagnosis_note_is_not_outside(self):
        search = "packages/search/pager.py (modified). A --package run may change only files inside packages/invoices/"
        for findings, ok in (
            ([self.finding(search)], True),
            ([self.finding(search, status="closed")], False),
            ([{**self.finding(search), "blocking": False}], False),
            ([], False),
            # The finding must name the file, and come from the runner.
            ([self.finding("packages/search/test_pager.py (modified). A --package run may change only")], False),
            ([{**self.finding(search), "source": "sol"}], False),
        ):
            with self.subTest(findings=findings):
                checks = self.score("broken/fixes-search-copy-too", findings)
                self.assertEqual(ok, checks["outside_changes_reported"].ok, checks["outside_changes_reported"])
                self.assertEqual(
                    "changed outside packages/invoices/: ['packages/search/pager.py']",
                    checks["nothing_changed_outside_the_package"].detail,
                )
        reference = self.score("reference", [])
        self.assertTrue(reference["nothing_changed_outside_the_package"].ok)
        self.assertTrue(reference["outside_changes_reported"].ok)
        # Another runner finding (a lifecycle observation) is not a report of changes outside the package.
        lifecycle = {**self.finding(""), "finding": "Runner lifecycle observation failed: a claimed lease was lost"}
        self.assertTrue(self.score("reference", [lifecycle])["outside_changes_reported"].ok)
        # Reporting the runner's own note as a change outside the package (as the proof did before #1415) is invented.
        note = [self.finding("docs/bugs/scripted-diagnosis.json (added). A --package run may change only files")]
        self.assertFalse(self.score("reference", note)["outside_changes_reported"].ok)
        self.assertNotIn("outside_changes_reported", self.score("reference", [], package=None))
        # Only the runner's note is left out: another file under docs/bugs/ is a change outside the package.
        other = self.score("reference", [], extra=["docs/bugs/search-pager.json"])
        self.assertEqual(
            "changed outside packages/invoices/: ['docs/bugs/search-pager.json']",
            other["nothing_changed_outside_the_package"].detail,
        )
        self.assertFalse(other["nothing_changed_outside_the_package"].ok)

    def test_the_investigator_runs_before_any_other_model_stage_but_requirements(self):
        for stages, model_stages, ok in (
            (self.STAGES, None, True),
            (self.STAGES[1:], None, True),
            (["requirements_gather", "requirements_gather_report_repair", *self.STAGES[1:]], None, True),
            # A runner-owned record is not a model stage.
            (["requirements_gather", "model_preflight", *self.STAGES[1:]], self.STAGES, True),
            (["requirements_gather", "astra_discovery", "investigate_bug", "terra"], None, False),
            (self.STAGES, ["requirements_gather", "astra_discovery", "investigate_bug"], False),
            (["requirements_gather", "astra_discovery", "terra"], None, False),
        ):
            with self.subTest(stages=stages, model_stages=model_stages):
                checks = self.score("reference", [], stages=stages, model_stages=model_stages)
                self.assertEqual(ok, checks["investigated_first"].ok, checks["investigated_first"])


class ProgressiveLearningOracleTests(unittest.TestCase):
    def setUp(self):
        from harness.project import materialize

        self.scenario = catalog.load("progressive-learning-journey")
        temporary = tempfile.TemporaryDirectory(prefix="learning-oracle-test-")
        self.addCleanup(temporary.cleanup)
        self.project = materialize(self.scenario.seed, Path(temporary.name) / "project", self.scenario.reference)

    def score(self, demonstrated, proof):
        record = {
            "status": "TASK_COMPLETE",
            "view": {
                "workflow": "build",
                "progressive": {"demonstrated_slices": demonstrated, "current_whole_product_proof": proof},
            },
            "stages": ["astra_challenge", "glm_revise", "terra", "sol", "astra_review"],
            "cli_calls": ["start", "approve-plan", "resume"],
            "steps": [{"kind": "approve-plan", "exit": 0}],
        }
        result = verdict.evaluate(self.scenario, self.project, record)
        self.assertEqual("", result.error)
        return result

    def test_distinct_slice_names_are_not_part_of_the_product_contract(self):
        for ids in (("S1", "S2"), ("journey-persist", "recommendations-finish")):
            with self.subTest(ids=ids):
                result = self.score(
                    [{"slice_id": id_} for id_ in ids],
                    {"verified": True, "status": "current", "source_revision": "current-source"},
                )
                self.assertEqual(verdict.PASS, verdict.judge("TASK_COMPLETE", result)[0], result.summary)

    def test_exactly_two_nonempty_distinct_slice_ids_are_required(self):
        cases = [
            None,
            [],
            [{"slice_id": "one"}],
            [{"slice_id": id_} for id_ in ("one", "two", "three")],
            [{"slice_id": "one"}, {"slice_id": "one"}],
            [{}, {"slice_id": "two"}],
            [{"id": "one"}, {"slice_id": "two"}],
            ["one", "two"],
            {"one": {}, "two": {}},
        ]
        cases.extend([[{"slice_id": id_}, {"slice_id": "two"}] for id_ in (None, "", " \t ", 1, True, [], {})])
        for demonstrated in cases:
            with self.subTest(demonstrated=demonstrated):
                result = self.score(
                    demonstrated, {"verified": True, "status": "current", "source_revision": "current-source"}
                )
                checks = {check.name: check.ok for check in result.checks}
                self.assertFalse(checks["two_independently_verified_slices"])
                self.assertEqual(verdict.FALSE_COMPLETE, verdict.judge("TASK_COMPLETE", result)[0])

    def test_checkpoint_claims_need_current_cli_completion_proof(self):
        claims = [
            {
                "slice_id": id_,
                "status": "PASS",
                "verified": True,
                "artifact": {"path": "invented-checkpoint.json", "sha256": "0" * 64},
            }
            for id_ in ("journey-persist", "recommendations-finish")
        ]
        for proof in (
            None,
            {},
            True,
            {"verified": 1},
            {"verified": False, "status": "not_established"},
            {"verified": False, "status": "stale_or_incomplete"},
        ):
            with self.subTest(proof=proof):
                result = self.score(claims, proof)
                checks = {check.name: check.ok for check in result.checks}
                self.assertFalse(checks["two_independently_verified_slices"])
                self.assertFalse(checks["current_whole_product_proof"])
                self.assertEqual(verdict.FALSE_COMPLETE, verdict.judge("TASK_COMPLETE", result)[0])


class ExercisedTests(unittest.TestCase):
    """`[run] requires_stages` (issue #59): a run that never reached the stage under test proves nothing about it."""

    def test_a_good_ending_without_the_stage_is_not_exercised(self):
        outcome, summary = verdict.exercised(verdict.PASS, "fine", ("astra_resolve",), ["terra", "sol"])
        self.assertEqual(verdict.NOT_EXERCISED, outcome)
        self.assertIn("astra_resolve", summary)
        self.assertEqual(
            verdict.PASS,
            verdict.exercised(verdict.PASS, "fine", ("astra_resolve",), ["terra", "sol", "astra_resolve", "terra"])[0],
        )

    def test_a_false_completion_is_never_hidden_behind_not_exercised(self):
        self.assertEqual(
            verdict.FALSE_COMPLETE, verdict.exercised(verdict.FALSE_COMPLETE, "bad", ("astra_resolve",), [])[0]
        )

    def test_the_oracles_reason_makes_a_good_ending_not_exercised(self):
        # The stage ran, but the oracle's diagnosis found it never ran on the failure the scenario plants.
        for ending in (verdict.PASS, verdict.HONEST_BLOCKER):
            outcome, summary = verdict.exercised(
                ending, "fine", ("astra_resolve",), ["astra_resolve"], "unrelated rework"
            )
            self.assertEqual(verdict.NOT_EXERCISED, outcome)
            self.assertIn("unrelated rework", summary)
        for kept in (verdict.FALSE_COMPLETE, verdict.ERROR):
            self.assertEqual(kept, verdict.exercised(kept, "bad", (), [], "unrelated rework")[0])
        self.assertEqual(verdict.PASS, verdict.exercised(verdict.PASS, "fine", (), [], "")[0])


class DiagnosisBlockTests(unittest.TestCase):
    """An oracle's optional diagnosis(project, run), kept apart from the run verdict (issue #59)."""

    def stand_in(self, diagnosis):
        return Mock(diagnosis=Mock(return_value=diagnosis))

    def test_no_diagnosis_function_means_no_block(self):
        self.assertIsNone(verdict.diagnose(catalog.load("greenfield-greeting-cli"), Path("."), {}))

    def test_checks_become_plain_values_for_result_json(self):
        block = verdict.diagnose(
            self.stand_in(
                lambda project, run: {
                    "verdict": verdict.CORRECT,
                    "reason": "",
                    "checks": [oracle.Check("a", True)],
                    "trap_calls": [{"checks": [oracle.Check("b", False, "x")], "output": Path("/r.json")}],
                }
            ),
            Path("."),
            {},
        )
        self.assertEqual([{"name": "a", "ok": True, "detail": ""}], block["checks"])
        self.assertEqual(
            {"checks": [{"name": "b", "ok": False, "detail": "x"}], "output": "/r.json"}, block["trap_calls"][0]
        )
        json.dumps(block)

    def test_a_skipped_run_saves_a_null_diagnosis(self):
        with tempfile.TemporaryDirectory(prefix="scenario-test-") as out:
            args = argparse.Namespace(
                fake=True,
                profile=None,
                fake_solution="reference",
                out=Path(out),
                autocode=None,
                max_steps=None,
                timeout_minutes=1,
            )
            result = run.run_one(catalog.load("stuck-planner-citation"), args)
            saved = json.loads((Path(result["evidence"]) / "result.json").read_text())
        self.assertEqual(verdict.SKIPPED, saved["verdict"])
        self.assertIn("diagnosis", saved)
        self.assertIsNone(saved["diagnosis"])

    def test_a_crash_or_unknown_verdict_is_a_diagnosis_error_not_a_run_error(self):
        def crash(project, run):
            raise KeyError("stages")

        for scorer in (crash, lambda project, run: {"verdict": "PASS"}):
            block = verdict.diagnose(self.stand_in(scorer), Path("."), {})
            self.assertEqual(verdict.ERROR, block["verdict"])
            self.assertIn("diagnosis error", block["reason"])


class TurnTests(unittest.TestCase):
    """Follow-up turns (issue #51): parsed from scenario.toml, matched to run states, and split afterwards."""

    def test_turns_load_in_order(self):
        scenario = catalog.load("review-then-fix")
        self.assertEqual(["complete"], [turn.after for turn in scenario.turns])
        self.assertTrue(scenario.turns[0].say.startswith("Fix them."))
        self.assertEqual((), catalog.load("review-planted-defects").turns)

    def test_a_turn_must_say_something_after_a_known_state(self):
        with tempfile.TemporaryDirectory() as root:
            original = catalog.CATALOG
            catalog.CATALOG = Path(root)
            self.addCleanup(setattr, catalog, "CATALOG", original)
            # --follow-up continues only a finished run, so a turn after a stop or a need could never be said.
            only_finished = 'after must be "complete": a follow-up continues only a finished run'
            for turn, message in (
                ('after = "later"\nsay = "x"', "after must be"),
                ('after = "complete"', "exactly"),
                ('after = "stop"\nsay = "x"', only_finished),
                ('after = "needs:answer"\nsay = "x"', only_finished),
            ):
                scenario = Path(root) / "bad"
                scenario.mkdir(exist_ok=True)
                (scenario / "brief.md").write_text("Do it.")
                (scenario / "scenario.toml").write_text(f'title = "t"\ncategory = "conversation"\n[[turn]]\n{turn}\n')
                with self.assertRaisesRegex(ValueError, message):
                    catalog.load("bad")

    def test_a_run_that_stops_before_a_turn_is_judged_but_never_passes(self):
        with tempfile.TemporaryDirectory() as root:
            project = Path(root)
            (project / ".autocode" / "runs" / "r1").mkdir(parents=True)
            (project / ".autocode" / "runs" / "r1" / "state.json").write_text("{}")
            driver = Driver(project, project, [], {}, autocode=[], max_steps=5, timeout_seconds=60)
            stopped = {"done": False, "needs": {"kind": "resume", "reason": "paused"}, "status": "PAUSED_X"}
            with (
                patch.object(driver, "call") as call,
                patch.object(driver, "view", return_value=stopped),
                self.assertRaisesRegex(TurnNotReached, "stopped before turn 2") as raised,
            ):
                driver.drive("Do it.", (catalog.Turn("complete", "Go on."),))
        self.assertEqual(2, raised.exception.turn)
        self.assertEqual(["start"], [c.args[0] for c in call.call_args_list], "the turn was never said")
        # An expected stop the oracle agrees with would PASS; a conversation that never finished cannot.
        self.assertEqual(
            (verdict.HONEST_BLOCKER, "stopped before turn 2: AutoCode stopped"),
            verdict.turn_not_reached(verdict.PASS, "AutoCode stopped", 2),
        )
        for kept in (verdict.HONEST_BLOCKER, verdict.FALSE_COMPLETE, verdict.ERROR):
            self.assertEqual(kept, verdict.turn_not_reached(kept, "s", 3)[0])

    def test_turn_state_names_what_a_turn_may_follow(self):
        self.assertEqual(["complete"], turn_state({"done": True, "needs": {"kind": "none"}}))
        self.assertEqual(["stop", "needs:answer"], turn_state({"done": False, "needs": {"kind": "answer"}}))

    def test_the_driver_leaves_an_autoresolver_escalation_for_a_person(self):
        # A run that reached its token cap asks a person, not a requirements
        # question; answering it with a default turned an honest pause into an ERROR.
        question = {"kind": "answer", "questions": [{"id": "q1", "proposed_default": "yes"}]}
        for scope in ("operational_exhaustion", "blocker"):
            self.assertTrue(leaves_for_person({**question, "resolver_token": "t", "resolver_scope": scope}))
        self.assertTrue(leaves_for_person({"kind": "resume", "reason": "paused"}))
        for need in (
            question,
            {**question, "resolver_token": "t", "resolver_scope": "clarification"},
            {"kind": "approve_plan", "token": "g"},
        ):
            self.assertFalse(leaves_for_person(need))

    def test_job_recovery_is_an_inspection_stop_not_an_automatic_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            driver = Driver(root, root, [], {}, autocode=[], max_steps=5, timeout_seconds=60)
            for kind in ("retry_job", "recover_source"):
                stopped = {
                    "done": False,
                    "status": "PAUSED_JOB_FAILURE",
                    "needs": {"kind": kind, "job_retry_token": "owned-attempt"},
                }
                with (
                    self.subTest(kind=kind),
                    patch.object(driver, "view", return_value=stopped),
                    patch.object(driver, "call") as call,
                ):
                    self.assertEqual(stopped, driver.until_stopped())
                    call.assert_not_called()
            unknown = {"done": False, "needs": {"kind": "unrecognized_protocol_gate"}}
            with (
                patch.object(driver, "view", return_value=unknown),
                self.assertRaisesRegex(DriveError, "no way to serve"),
            ):
                driver.until_stopped()

    def test_stages_are_split_at_the_moment_each_follow_up_was_said(self):
        state = {
            "stages": [
                {"stage": "review_change", "finished_at": "2026-09-28T10:00:01+00:00"},
                {"stage": "orchestrator", "started_at": None, "finished_at": "2026-09-28T10:05:00+00:00"},
                {
                    "stage": "terra",
                    "started_at": "2026-09-28T10:05:01+00:00",
                    "finished_at": "2026-09-28T10:06:00+00:00",
                },
            ]
        }
        first, second = split_by_turn(state, [{"said_at": "2026-09-28T10:04:00+00:00"}])
        self.assertEqual(["review_change"], [stage["stage"] for stage in first])
        self.assertEqual(["orchestrator", "terra"], [stage["stage"] for stage in second])

    def test_run_and_turn_records_preserve_successful_and_rejected_approval_exits(self):
        driver = argparse.Namespace(
            run_dir=None,
            answers=[],
            steps=[
                {"kind": "start", "exit": 2},
                {"kind": "approve-plan", "exit": 0},
                {"kind": "follow-up", "exit": 2},
                {"kind": "approve-plan", "exit": 2},
            ],
            turn_marks=[
                {
                    "steps": 2,
                    "answers": 0,
                    "say": "Change the goal",
                    "said_at": "2026-09-28T10:04:00+00:00",
                    "view": {"workflow": "build"},
                }
            ],
        )
        record = run.run_record(driver, {"stages": []})
        self.assertEqual(driver.steps, record["steps"])
        approved = lambda row: next(
            check.ok
            for check in oracle.run_checks(row, workflow="build", plan_approved=True)
            if check.name == "plan_approved_by_user"
        )
        self.assertTrue(approved(record))
        self.assertTrue(approved(record["turns"][0]))
        self.assertFalse(approved(record["turns"][1]))

    def test_turn_paths_split_a_conversation_s_solution_one_list_per_turn(self):
        scenario = catalog.load("discuss-then-design-then-build")
        self.assertEqual(len(scenario.turns) + 1, len(scenario.fake_turn_paths))
        with tempfile.TemporaryDirectory() as root:
            original = catalog.CATALOG
            catalog.CATALOG = Path(root)
            self.addCleanup(setattr, catalog, "CATALOG", original)
            cases = {
                "too-few": ('[["a/"]]', ["x"], "one list of relative path prefixes per turn"),
                "outside": ('[["a/"], ["../b/"]]', ["x"], "one list of relative path prefixes per turn"),
                "prefix": (
                    '[["a/"], ["b/"], ["c/"]]',
                    ["Go on.", "Go on. Now."],
                    "no turn's message may begin another's",
                ),
                # Identical messages begin each other: both follow-ups would be served as the last.
                "duplicate": (
                    '[["a/"], ["b/"], ["c/"]]',
                    ["Go on.", "Go on."],
                    "no turn's message may begin another's",
                ),
                "brief": ('[["a/"], ["b/"]]', ["Do"], "no turn's message may begin another's or the brief"),
            }
            for name, (paths, says, error) in cases.items():
                bad = Path(root) / name
                bad.mkdir()
                (bad / "brief.md").write_text("Do it.")
                turns = "".join(f'[[turn]]\nafter = "complete"\nsay = "{say}"\n' for say in says)
                (bad / "scenario.toml").write_text(
                    f'title = "t"\ncategory = "conversation"\n[fake]\ncheck = "true"\nturn_paths = {paths}\n{turns}'
                )
                with self.subTest(name), self.assertRaisesRegex(ValueError, re.escape(error)):
                    catalog.load(name)

    def test_no_turn_s_message_may_begin_another_s_even_without_turn_paths(self):
        # The scripted model also serves per-turn reports (.fake-turns) by the message a task starts with.
        with tempfile.TemporaryDirectory() as root:
            bad = Path(root) / "bad"
            bad.mkdir()
            (bad / "brief.md").write_text("Do it.")
            (bad / "scenario.toml").write_text(
                'title = "t"\ncategory = "conversation"\n'
                + "".join(f'[[turn]]\nafter = "complete"\nsay = "{say}"\n' for say in ("Go on.", "Go on. Now."))
            )
            with (
                patch.object(catalog, "CATALOG", Path(root)),
                self.assertRaisesRegex(ValueError, "no turn's message may begin another's"),
            ):
                catalog.load("bad")

    def test_a_solution_s_scripted_turn_reports_are_never_part_of_the_project(self):
        from harness.project import materialize, overlay_paths

        with tempfile.TemporaryDirectory() as root:
            solution = Path(root) / "solution"
            (solution / ".fake-turns" / "0").mkdir(parents=True)
            (solution / ".fake-turns" / "0" / "review_design.json").write_text("{}")
            (solution / "review").mkdir()
            (solution / "review" / "design-review.json").write_text("{}")
            self.assertEqual(["review/design-review.json"], overlay_paths(solution))
            project = materialize(Path(root) / "no-seed", Path(root) / "project", solution)
            self.assertEqual(["review/design-review.json"], sorted(workspace_files(project)))

    def test_each_turn_keeps_the_files_it_changed_as_it_left_them(self):
        """A report revised in place by the next turn is still there, as each turn left it."""
        with tempfile.TemporaryDirectory() as root:
            project, out = Path(root) / "project", Path(root) / "out"
            (project / ".autocode" / "runs" / "r1").mkdir(parents=True)
            (project / ".autocode" / "runs" / "r1" / "state.json").write_text("{}")
            out.mkdir()
            driver = Driver(project, out, [], {}, autocode=[], max_steps=5, timeout_seconds=60)
            reports = iter(["revision 1", "revision 2"])

            def call(kind, *args, **kwargs):
                (project / "review").mkdir(exist_ok=True)
                (project / "review" / "design-review.json").write_text(next(reports))

            done = {"done": True, "needs": {"kind": "none"}, "status": "TASK_COMPLETE"}
            with patch.object(driver, "call", side_effect=call), patch.object(driver, "view", return_value=done):
                driver.drive("Do it.", (catalog.Turn("complete", "Go on."),))
                record = run.run_record(driver, {"stages": []})
            self.assertEqual(
                ["revision 1", "revision 2"],
                [(Path(turn["kept_files"]) / "review" / "design-review.json").read_text() for turn in record["turns"]],
            )
            self.assertEqual([["review/design-review.json"]] * 2, [turn["changed_files"] for turn in record["turns"]])

    def test_the_design_turn_may_also_add_its_design_to_the_folder_s_index(self):
        design_document = catalog.load("discuss-then-design-then-build").oracle().__globals__["design_document"]
        written = {"changed_files": ["docs/design/README.md", "docs/design/metadata-cache.md"]}
        self.assertEqual("docs/design/metadata-cache.md", design_document(None, {"turns": [{}, written, {}]}))

    def test_a_build_turn_that_changed_no_code_does_not_follow_the_design(self):
        scenario = catalog.load("discuss-then-design-then-build")
        from harness.project import materialize

        with tempfile.TemporaryDirectory() as root:
            project = materialize(scenario.seed, Path(root) / "project", scenario.reference)
            turns = lambda built: [
                {"changed_files": ["docs/decisions/metadata-cache.json"]},
                {"changed_files": ["docs/design/metadata-cache.md"]},
                {"changed_files": built},
            ]
            follows = lambda built: next(
                check
                for check in scenario.oracle()(project, scenario, {"turns": turns(built)})
                if check.name == "build_follows_design"
            )
            self.assertFalse(follows([]).ok, "a build turn that wrote nothing cannot follow the design")
            self.assertIn("changed nothing under app/", follows([]).detail)
            self.assertTrue(
                follows(["app/shared_cache.py", "tests/test_shared_cache.py"]).ok,
                follows(["app/shared_cache.py"]).detail,
            )

    def test_freshness_predicates_require_every_decision_outside_rejected_options(self):
        policy = catalog.load("discuss-then-design-then-build").oracle().__globals__["freshness_policy"]
        clauses = {
            "ttl": "TTL governs freshness.",
            "caller_configurable": "The caller controls TTL per lookup.",
            "default_3600": "The default is 3600 seconds.",
            "injectable_clock": "Use an injectable clock.",
            "expiry_at_equality": "An entry expires when age >= TTL.",
            "no_stale": "Never serve stale metadata.",
            "single_refresh": "The owner invokes `fetch(tld)` once.",
            "atomic_publication": "Publish using os.replace.",
            "worker_reuse": "Other workers reuse the new record.",
            "upstream_errors": "Propagate upstream errors.",
        }
        full = "\n".join(clauses.values())
        self.assertTrue(all(policy(full).values()), policy(full))
        for name in clauses:
            if name == "ttl":  # Other clauses still explicitly name TTL.
                continue
            without = "\n".join(text for key, text in clauses.items() if key != name)
            with self.subTest(decision=name):
                self.assertFalse(policy(without)[name])
                self.assertFalse(policy(without + "\n## Rejected\n" + clauses[name])[name])
        self.assertFalse(policy(full.replace("fetch(tld)` once", "fetch(tld)` twice"))["single_refresh"])
        self.assertFalse(policy(full.replace(">= TTL", "> TTL"))["expiry_at_equality"])
        signature = full.replace(clauses["caller_configurable"], "").replace(clauses["injectable_clock"], "")
        signature += "\n`metadata(tld: str, *, ttl: float = 3600, clock: Callable[[], float] | None = None) -> dict`"
        self.assertTrue(all(policy(signature).values()), policy(signature))

    def test_injected_callback_is_not_an_export_but_missing_public_interface_is(self):
        interface = catalog.load("discuss-then-design-then-build").oracle().__globals__["named_interface"]
        text = (
            "Module `app/cache.py`; `metadata(tld: str, *, ttl: float = 3600, "
            "clock: Callable[[], float] | None = None) -> dict`. "
            "Take a new `clock()` reading. Also call `publish(record)`."
        )
        modules, signatures = interface(text)
        self.assertEqual(["app/cache.py"], modules)
        self.assertNotIn("clock", signatures)
        self.assertIn("metadata", signatures, "the exported typed signature still binds the build")
        self.assertIn("publish", signatures)
        _, signatures = interface("Module `app/cache.py`; call its public `clock()` and `publish(record)`.")
        self.assertIn("clock", signatures, "a real public clock is not globally exempt")
        _, signatures = interface(text.replace("clock", "now"))
        self.assertNotIn("now", signatures, "callback recognition is parameter-based, not a clock whitelist")
        _, signatures = interface(text + " The module also exports `clock() -> float`.")
        self.assertIn("clock", signatures, "a separately declared function is not exempt as a callback")

    def test_freshness_rejects_inclusive_hits_and_explicitly_excluded_equality(self):
        scenario = catalog.load("discuss-then-design-then-build")
        policy = scenario.oracle().__globals__["freshness_policy"]
        reference = (scenario.reference / "docs/design/metadata-cache.md").read_text()
        wrong = reference.replace(
            "elapsed clock time >= TTL, including equality", "elapsed clock time > TTL, excluding equality"
        )
        wrong = wrong.replace("An entry younger than `ttl_seconds`", "A cache hit with age <= ttl_seconds")
        self.assertFalse(policy(wrong)["expiry_at_equality"])
        self.assertFalse(
            policy(reference.replace("## Rejected", "Expiration excludes equality.\n\n## Rejected"))[
                "expiry_at_equality"
            ]
        )
        self.assertTrue(
            policy(reference + "\nExpiration excludes equality.")["expiry_at_equality"],
            "a rejected policy is not a settled contradictory decision",
        )
        self.assertTrue(policy("A fresh hit requires age < ttl_seconds.")["expiry_at_equality"])
        self.assertTrue(policy("An entry expires at age >= TTL, including equality.")["expiry_at_equality"])

    def test_freshness_requires_caller_control_not_a_hard_coded_ttl_identifier(self):
        scenario = catalog.load("discuss-then-design-then-build")
        policy = scenario.oracle().__globals__["freshness_policy"]
        reference = (scenario.reference / "docs/design/metadata-cache.md").read_text()
        wrong = reference.replace(
            "TTL is caller-configurable through `ttl_seconds`, with a 3600-second default",
            "TTL uses hard-coded ttl_seconds with a 3600-second default",
        )
        self.assertFalse(policy(wrong)["caller_configurable"])
        self.assertFalse(
            policy("The TTL field is `ttl_seconds: float = 3600`.")["caller_configurable"],
            "a field initializer is not a configurable API",
        )
        self.assertFalse(
            policy(reference.replace("## Rejected", "TTL is fixed and callers cannot override it.\n\n## Rejected"))[
                "caller_configurable"
            ]
        )
        self.assertTrue(
            policy(reference + "\nTTL is fixed and callers cannot override it.")["caller_configurable"],
            "a rejected fixed TTL does not invalidate the configurable design",
        )
        self.assertTrue(
            policy("The caller controls TTL per lookup. `metadata(tld: str, *, ttl: float = 3600)`")[
                "caller_configurable"
            ]
        )
        self.assertTrue(policy("`metadata(tld: str, *, ttl: float = 3600)`")["caller_configurable"])

    def test_typed_public_export_with_callback_name_must_exist_and_keep_its_parameters(self):
        scenario = catalog.load("discuss-then-design-then-build")
        from harness.project import materialize

        with tempfile.TemporaryDirectory() as root:
            project = materialize(scenario.seed, Path(root) / "project", scenario.reference)
            design = project / "docs/design/metadata-cache.md"
            text = design.read_text().replace(
                "## Rejected",
                "Constructor `Cache(clock: Callable[[], float])`; "
                "injected `clock` callback. The module also exports `clock(unit: str)`.\n\n## Rejected",
            )
            design.write_text(text)
            code = project / "app/callback.py"
            code.write_text("class Cache:\n    def __init__(self, clock): pass\n")
            follows = lambda: next(
                row for row in scenario.oracle()(project, scenario) if row.name == "build_follows_design"
            )
            missing = follows()
            self.assertFalse(missing.ok)
            self.assertIn("'clock'", missing.detail)
            code.write_text(code.read_text() + "def clock(): return 1000\n")
            self.assertFalse(follows().ok, "the typed positional parameter must not be ignored")
            code.write_text("class Cache:\n    def __init__(self, clock): pass\ndef clock(unit): return 1000\n")
            self.assertTrue(follows().ok, follows().detail)

    def test_live_turn_declares_the_same_strict_paths_and_socket_free_tests_as_the_oracle(self):
        scenario = catalog.load("discuss-then-design-then-build")
        message = scenario.turns[0].say
        self.assertEqual("Build it.", scenario.turns[1].say)
        for constraint in (
            "3600-second default",
            "including at equality",
            "injectable clock",
            "edit only app/ and tests/",
            "root README.md",
            "controlled urllib.request.urlopen",
            "pipes or file barriers",
            "must not require network or loopback binding permission",
        ):
            self.assertIn(constraint, message)
        self.assertEqual(("app/", "tests/"), scenario.fake_turn_paths[2])
        checks = catalog.load("discuss-then-design-then-build").oracle().__globals__["conversation_checks"]
        turns = [
            {"changed_files": ["docs/decisions/cache.json"]},
            {"changed_files": ["docs/design/cache.md"]},
            {"changed_files": ["app/cache.py", "README.md"]},
        ]
        boundary = next(row for row in checks({"turns": turns}) if row.name == "build_turn_changed_only_its_report")
        self.assertFalse(boundary.ok, "README remains outside the unchanged acceptance boundary")

    def test_a_design_s_private_helpers_do_not_bind_the_build(self):
        """A live design listed `_check_fetch_budget(...)` among "example names"; the build merged two helpers."""
        scenario = catalog.load("discuss-then-design-then-build")
        from harness.project import materialize

        with tempfile.TemporaryDirectory() as root:
            project = materialize(scenario.seed, Path(root) / "project", scenario.reference)
            design = project / "docs" / "design" / "metadata-cache.md"
            decided = design.read_text().replace(
                "## Rejected",
                "## Helpers (example names)\n\n- `_check_fetch_budget(tld)`\n- `_read(key, now)`\n\n## Rejected",
            )
            follows = lambda extra: (
                design.write_text(decided.replace("## Rejected", extra + "## Rejected")),
                next(check for check in scenario.oracle()(project, scenario) if check.name == "build_follows_design"),
            )[1]
            self.assertTrue(follows("").ok, follows("").detail)
            missing = follows("- `evict(key)` drops one entry.\n\n")
            self.assertFalse(missing.ok, "a public callable the design names must still exist")
            self.assertIn("missing from app/: ['evict']", missing.detail)

    def test_a_formula_in_the_design_does_not_bind_the_build(self):
        """A live design bounded fetches with `ceil(3600 / TTL_seconds) × N <= 60`; its build computed it
        without importing math, and build_follows_design failed on `ceil`."""
        scenario = catalog.load("discuss-then-design-then-build")
        from harness.project import materialize

        with tempfile.TemporaryDirectory() as root:
            project = materialize(scenario.seed, Path(root) / "project", scenario.reference)
            design = project / "docs" / "design" / "metadata-cache.md"
            design.write_text(
                design.read_text().replace(
                    "## Rejected", "Attempts per hour stay at most `ceil(3600 / ttl_seconds)` per key.\n\n## Rejected"
                )
            )
            follows = next(
                check for check in scenario.oracle()(project, scenario) if check.name == "build_follows_design"
            )
            self.assertTrue(follows.ok, follows.detail)

    def test_a_design_that_removes_the_seed_s_cache_in_its_own_words_follows_the_decision(self):
        """Live designs said "a shared, host-local file-backed cache" without the token shared-file, and
        named the seed's cache they remove (`functools.lru_cache(maxsize=256)`, "`cache_clear()`: Removed");
        their builds removed it, and the oracle judged them FALSE_COMPLETE."""
        scenario = catalog.load("discuss-then-design-then-build")
        from harness.project import materialize

        with tempfile.TemporaryDirectory() as root:
            project = materialize(scenario.seed, Path(root) / "project", scenario.reference)
            design = project / "docs" / "design" / "metadata-cache.md"
            reworded = design.read_text().replace(
                "docs/decisions/metadata-cache.json (recommendation: shared-file)",
                "the decision record: a shared, host-local file-backed cache",
            )
            self.assertNotIn("shared-file", reworded.split("## Rejected")[0])
            design.write_text(
                reworded.replace(
                    "## Rejected",
                    "The seed's `functools.lru_cache(maxsize=256)` goes, "
                    "and with it `cache_clear()` and `cache_info()`.\n\n## Rejected",
                )
            )
            checks = {check.name: check for check in scenario.oracle()(project, scenario)}
            self.assertTrue(checks["design_follows_decision"].ok, checks["design_follows_decision"].detail)
            self.assertTrue(checks["build_follows_design"].ok, checks["build_follows_design"].detail)

    def test_the_hidden_tests_leave_a_missing_cache_directory_to_the_design(self):
        """The deploy configuration provisions METADATA_CACHE_DIR; a live design fell back to a per-worker
        memo when it is missing, and the hidden tests failed it for not creating the directory."""
        scenario = catalog.load("discuss-then-design-then-build")
        from harness.oracle import hidden_tests
        from harness.project import materialize

        with tempfile.TemporaryDirectory() as root:
            project = materialize(scenario.seed, Path(root) / "project", scenario.reference)
            cache = project / "app" / "shared_cache.py"
            cache.write_text(
                cache.read_text().replace("        self.directory.mkdir(parents=True, exist_ok=True)\n", "")
            )
            result = hidden_tests(project, scenario.dir / "hidden")
            self.assertEqual(0, result.returncode, (result.stdout or "")[-1500:] + (result.stderr or "")[-1500:])

    def test_each_turn_records_what_it_changed_in_the_workspace(self):
        with tempfile.TemporaryDirectory() as root:
            project = Path(root)
            (project / ".autocode").mkdir()
            (project / ".autocode" / "state.json").write_text("{}")
            (project / "kept.txt").write_text("same")
            (project / "edited.txt").write_text("before")
            before = workspace_files(project)
            (project / "edited.txt").write_text("after")
            (project / "new.txt").write_text("new")
            self.assertEqual(["edited.txt", "new.txt"], changed_between(before, workspace_files(project)))
            self.assertNotIn(".autocode/state.json", before)


class MetricsTests(unittest.TestCase):
    def test_stages_are_broken_down_by_name_and_report_repairs_are_counted(self):
        state = {
            "stages": [
                {"stage": "terra", "duration_seconds": 30},
                {"stage": "orchestrator", "duration_seconds": 1},
                {"stage": "sol", "duration_seconds": 10},
                {"stage": "sol_report_repair", "duration_seconds": 4},
                {"stage": "regression_proof", "runner_owned": True, "duration_seconds": 2},
                {"stage": "sol", "duration_seconds": 12},
            ]
        }
        result = metrics(state)
        self.assertEqual(4, result["model_stages"])
        self.assertEqual(1, result["report_repairs"])
        self.assertEqual({"count": 2, "seconds": 22}, result["by_stage"]["sol"])
        self.assertEqual(59, result["model_seconds"])


class StatsTests(unittest.TestCase):
    """`run.py stats`: how often each scenario ran and passed, never mixing fake with live or one
    AutoCode revision with another."""

    def result(self, scenario, mode, outcome, started, stages=5, wall=60, commit=None, dirty=False):
        result = {
            "scenario": scenario,
            "mode": mode,
            "verdict": outcome,
            "started_at": started,
            "wall_seconds": wall,
            "metrics": {"model_stages": stages, "model_seconds": wall / 2},
        }
        if commit is not None:  # a result saved before the revision was recorded has no "autocode" at all
            result["autocode"] = {"commit": commit, "branch": "master", "dirty": dirty}
        return result

    def test_runs_of_different_autocode_revisions_are_not_one_row(self):
        """A pass recorded against an older commit is not evidence about the code in the checkout.
        Pooling them gives the row a streak and a median no single revision ever achieved."""
        results = [
            self.result("s", "fake", verdict.PASS, "1", commit="a" * 40),
            self.result("s", "fake", verdict.FALSE_COMPLETE, "2", commit="a" * 40),
            self.result("s", "fake", verdict.PASS, "3", wall=900, commit="b" * 40),
            self.result("s", "fake", verdict.PASS, "4", wall=900, commit="b" * 40),
        ]
        rows = stats.summarize(results)
        self.assertEqual(2, len(rows), "two revisions of AutoCode were pooled into one row")
        by_revision = {(row["mode"], row["revision"]): row for row in rows}
        self.assertEqual((("fake", "aaaaaaaa"), ("fake", "bbbbbbbb")), tuple(sorted(by_revision)))

        def counted(label):
            return tuple(by_revision[("fake", label)][key] for key in ("runs", "passes", "streak"))

        self.assertEqual((2, 1, 0), counted("aaaaaaaa"))
        self.assertEqual((2, 2, 2), counted("bbbbbbbb"))
        # The medians are each revision's own: the pooled pair would have read 7.5 minutes for both.
        self.assertEqual(
            (1.0, 15.0),
            (
                by_revision[("fake", "aaaaaaaa")]["median_wall_minutes"],
                by_revision[("fake", "bbbbbbbb")]["median_wall_minutes"],
            ),
        )

    def test_a_dirty_checkout_is_not_the_commit_it_sits_on(self):
        results = [
            self.result("s", "fake", verdict.PASS, "1", commit="a" * 40),
            self.result("s", "fake", verdict.PASS, "2", commit="a" * 40, dirty=True),
        ]
        self.assertEqual(["aaaaaaaa", "aaaaaaaa-dirty"], [row["revision"] for row in stats.summarize(results)])

    def test_stats_can_be_narrowed_to_one_revision(self):
        results = [
            self.result("s", "fake", verdict.PASS, "1", commit="a" * 40),
            self.result("s", "fake", verdict.FALSE_COMPLETE, "2", commit="b" * 40),
        ]
        self.assertEqual(["aaaaaaaa"], [row["revision"] for row in stats.summarize(results, revision="aaaa")])
        self.assertEqual(["bbbbbbbb"], [row["revision"] for row in stats.summarize(results, revision="b" * 40)])
        self.assertEqual([], stats.summarize(results, revision="c" * 40))

    def test_a_result_that_predates_the_revision_record_is_its_own_group(self):
        results = [
            self.result("s", "fake", verdict.PASS, "1"),
            self.result("s", "fake", verdict.PASS, "2", commit="a" * 40),
        ]
        rows = stats.summarize(results)
        self.assertEqual(["?", "aaaaaaaa"], [row["revision"] for row in rows])
        self.assertEqual([1, 1], [row["passes"] for row in rows])
        self.assertIn("revision", stats.format_table(rows).splitlines()[0])
        # An old result stays reachable by name rather than only as a row nobody can ask for.
        self.assertEqual(["?"], [row["revision"] for row in stats.summarize(results, revision="?")])

    def test_the_stats_command_shows_one_row_per_revision_and_names_this_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            for index, commit in enumerate(("a" * 40, "a" * 40, "b" * 40)):
                (out / f"run-{index}").mkdir()
                saved = self.result("s", "fake", verdict.PASS, str(index), commit=commit)
                (out / f"run-{index}" / "result.json").write_text(json.dumps(saved))
            here = {"commit": "b" * 40, "branch": "master", "dirty": False}

            def table(*flags):
                with (
                    patch.object(run, "autocode_revision", return_value=here),
                    contextlib.redirect_stdout(io.StringIO()) as printed,
                ):
                    self.assertEqual(0, run.main(["stats", "--out", str(out), *flags]))
                shown = printed.getvalue()
                self.assertIn("this checkout: bbbbbbbb", shown)
                return [line.split()[1:4] for line in shown.splitlines() if line.startswith("s ")]

            self.assertEqual([["fake", "aaaaaaaa", "2"], ["fake", "bbbbbbbb", "1"]], table())
            self.assertEqual([["fake", "bbbbbbbb", "1"]], table("--revision", "bbbbbbbb"))

    def test_streak_counts_consecutive_passes_from_the_latest_run(self):
        results = [
            self.result("s", "fake", verdict.PASS, "1"),
            self.result("s", "fake", verdict.FALSE_COMPLETE, "2"),
            self.result("s", "fake", verdict.PASS, "3"),
            self.result("s", "fake", verdict.PASS, "4"),
        ]
        results.append(self.result("s", "fake", verdict.NOT_EXERCISED, "0"))
        (row,) = stats.summarize(results)
        self.assertEqual(
            (5, 3, 2, 1, verdict.PASS), (row["runs"], row["passes"], row["streak"], row["not_exercised"], row["last"])
        )

    def test_fake_and_live_runs_are_summarized_separately(self):
        results = [
            self.result("s", "fake", verdict.PASS, "1", stages=9),
            self.result("s", "glm53-openai", verdict.FALSE_COMPLETE, "2", stages=14, wall=900),
        ]
        rows = {row["mode"]: row for row in stats.summarize(results)}
        self.assertEqual((1, 9), (rows["fake"]["passes"], rows["fake"]["median_model_stages"]))
        self.assertEqual(
            (0, 14, 15),
            (
                rows["glm53-openai"]["passes"],
                rows["glm53-openai"]["median_model_stages"],
                rows["glm53-openai"]["median_wall_minutes"],
            ),
        )
        self.assertEqual(["fake"], [row["mode"] for row in stats.summarize(results, mode="fake")])

    def test_diagnosis_verdicts_are_counted_apart_from_run_verdicts(self):
        results = [
            {**self.result("s", "fake", outcome, str(n)), "diagnosis": {"verdict": diagnosed}}
            for n, (outcome, diagnosed) in enumerate(
                (
                    (verdict.PASS, verdict.CORRECT),
                    (verdict.PASS, verdict.INCORRECT),
                    (verdict.HONEST_BLOCKER, verdict.UNSCORED),
                    (verdict.NOT_EXERCISED, verdict.NOT_EXERCISED),
                )
            )
        ]
        results.append(
            {**self.result("s", "claude-tiers", verdict.PASS, "9"), "diagnosis": {"verdict": verdict.CORRECT}}
        )
        rows = {row["mode"]: row for row in stats.summarize(results)}
        self.assertEqual(
            (4, 2, 3, 1, 1, 1),
            tuple(rows["fake"][key] for key in ("runs", "passes", "diagnosed", "correct", "incorrect", "unscored")),
        )
        self.assertEqual((1, 1, 0), tuple(rows["claude-tiers"][key] for key in ("diagnosed", "correct", "incorrect")))
        (plain,) = stats.summarize([self.result("t", "fake", verdict.PASS, "1")])
        self.assertIsNone(plain["diagnosed"])
        table = stats.format_table([rows["fake"], plain])
        self.assertIn("diagnosed", table.splitlines()[0])

    def test_skipped_runs_do_not_count_and_older_results_still_read(self):
        old = {
            "scenario": "s",
            "mode": "fake",
            "verdict": verdict.PASS,
            "started_at": "1",
            "metrics": {"model_stage_names": ["terra", "sol", "astra_review"]},
        }
        skipped = {"scenario": "s", "mode": "fake", "verdict": verdict.SKIPPED, "started_at": "2"}
        (row,) = stats.summarize([old, skipped])
        self.assertEqual((1, 3, None), (row["runs"], row["median_model_stages"], row["median_wall_minutes"]))
        self.assertIn("s", stats.format_table([row]))


class CostRecordTests(unittest.TestCase):
    """#721: a cost pause is a budget stop, and each result keeps the view's spend, unknown never read as zero."""

    COST = {
        "estimated_usd": 0.18,
        "reported_usd": None,
        "spent_usd": 0.18,
        "cap_usd": 0.1,
        "unpriced_stages": 0,
        "complete": True,
        "prices_as_of": "2026-10-14",
    }

    def test_a_cost_pause_is_a_budget_stop_even_behind_autoresolver_s_request(self):
        self.assertEqual(
            verdict.STOP_CLASS_BUDGET, verdict.stop_class(verdict.HONEST_BLOCKER, "PAUSED_COST_LIMIT", "cap reached")
        )
        # AutoResolver asks the person, so the status is WAITING_FOR_USER and the pause is the need's origin.
        summary = "AutoCode stopped at WAITING_FOR_USER; oracle 2/6 checks"
        self.assertEqual(
            verdict.STOP_CLASS_SAFETY, verdict.stop_class(verdict.HONEST_BLOCKER, "WAITING_FOR_USER", summary)
        )
        self.assertEqual(
            verdict.STOP_CLASS_BUDGET,
            verdict.stop_class(verdict.HONEST_BLOCKER, "WAITING_FOR_USER", summary, pause_origin="PAUSED_COST_LIMIT"),
        )
        # The origin names the pause a waiting run stems from; it never reclassifies another status.
        self.assertEqual(
            verdict.STOP_CLASS_PASSED,
            verdict.stop_class(verdict.PASS, "TASK_COMPLETE", "", pause_origin="PAUSED_COST_LIMIT"),
        )
        self.assertEqual(
            verdict.STOP_CLASS_PROVIDER,
            verdict.stop_class(
                verdict.HONEST_BLOCKER, "PAUSED_CONTENT_FILTER", "refused", pause_origin="PAUSED_COST_LIMIT"
            ),
        )

    def test_result_keeps_the_view_s_cost_and_the_pause_it_ended_at(self):
        paused = {
            "status": "WAITING_FOR_USER",
            "cost": self.COST,
            "needs": {
                "kind": "answer",
                "resolver_scope": "operational_exhaustion",
                "pause_origin": "PAUSED_COST_LIMIT",
            },
        }
        self.assertEqual({"cost": self.COST, "pause_origin": "PAUSED_COST_LIMIT"}, run.view_cost(paused))
        self.assertEqual({"cost": None, "pause_origin": None}, run.view_cost({"done": True, "needs": None}))
        self.assertEqual({"cost": None, "pause_origin": None}, run.view_cost({"cost": "unknown", "needs": {}}))

    def test_a_saved_cost_pause_is_classed_by_its_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run.finish(
                Path(tmp),
                {
                    "scenario": "case",
                    "mode": "fake",
                    "runner_status": "WAITING_FOR_USER",
                    **run.view_cost({"cost": self.COST, "needs": {"pause_origin": "PAUSED_COST_LIMIT"}}),
                },
                verdict.HONEST_BLOCKER,
                "AutoCode stopped at WAITING_FOR_USER; oracle 2/6 checks",
            )
            saved = json.loads((Path(tmp) / "result.json").read_text())
        self.assertEqual(verdict.STOP_CLASS_BUDGET, result["stop_class"])
        self.assertEqual((self.COST, "PAUSED_COST_LIMIT"), (saved["cost"], saved["pause_origin"]))
        # A result saved without its class is classed the same way when stats reads it back.
        del saved["stop_class"]
        (row,) = stats.summarize([saved])
        self.assertEqual({verdict.STOP_CLASS_BUDGET: 1}, row["stop_classes"])

    def test_an_unfinished_attempt_keeps_what_it_had_spent_but_its_cost_is_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "attempt"
            attempts.admit(root, {"scenario": "case", "mode": "fake", "started_at": "1"}, {"max_steps": 2})
            attempts.observe(root, {"status": "BUILDER_RUNNING", "usage": {"cost_usd": {}}, "cost": self.COST})
            self.assertEqual(self.COST, attempts.read(root / "attempt.json")["cost_snapshot"])
            with patch.object(attempts, "owner_alive", return_value=False):
                (saved,) = stats.load_results(Path(tmp))
        self.assertEqual(
            (verdict.INTERRUPTED_UNGRADED, None, self.COST), (saved["verdict"], saved["cost"], saved["cost_snapshot"])
        )
        (row,) = stats.summarize([saved])
        self.assertEqual((None, 1), (row["median_spent_usd"], row["cost_unknown"]))

    def test_stats_report_the_median_known_spend_and_count_the_unknown(self):
        def result(started, outcome=verdict.PASS, **cost):
            return {
                "scenario": "s",
                "mode": "fake",
                "verdict": outcome,
                "started_at": started,
                **({"cost": {**self.COST, **cost}} if cost else {}),
            }

        rows = stats.summarize(
            [
                result("1", spent_usd=0.3442),
                result("2", spent_usd=0.5),
                result("3", spent_usd=0.12),
                result("4", spent_usd=None),  # nothing priced: unknown, never zero
                result("5", spent_usd=float("nan")),
                result("6"),  # saved before the cost block was recorded
                result("7", verdict.INTERRUPTED_UNGRADED),
                result("8", verdict.SKIPPED),  # nothing ran: neither known nor unknown
            ]
        )
        (row,) = rows
        self.assertEqual((0.3442, 4), (row["median_spent_usd"], row["cost_unknown"]))
        header, _, line = stats.format_table(rows).splitlines()
        self.assertIn("unscored  spent usd  cost unknown  stop classes", header)
        cells = line.split()
        at = cells.index("0.3442")
        self.assertEqual(["0.3442", "4"], cells[at : at + 2])
        self.assertTrue(cells[at + 2].startswith("passed="), line)
        (empty,) = stats.summarize([result("1", verdict.HONEST_BLOCKER, spent_usd=None)])
        self.assertEqual((None, 1), (empty["median_spent_usd"], empty["cost_unknown"]))
        self.assertEqual(["-", "1", "safety_stop=1"], stats.format_table([empty]).splitlines()[-1].split()[-6:-3])


class RunExitCodeTests(unittest.TestCase):
    """`run.py run` exits zero only for a pass: a run that never reached the stage it exists to
    test proved nothing, so its command must not look like a passing check to whatever called it."""

    def code(self, outcome, known_failure=""):
        scenario = dataclasses.replace(catalog.load("feature-refund-window"), known_failure=known_failure)
        with (
            patch.object(run, "selected", return_value=[scenario]),
            patch.object(run, "run_one", return_value={"verdict": outcome, "summary": "summary", "evidence": "/e"}),
            contextlib.redirect_stdout(io.StringIO()) as printed,
        ):
            return run.main(["run", scenario.id, "--fake"]), printed.getvalue()

    def test_a_run_that_never_reached_its_stage_is_not_a_passing_command(self):
        code, printed = self.code(verdict.NOT_EXERCISED)
        self.assertEqual(1, code)
        self.assertIn(f"{verdict.NOT_EXERCISED} — summary", printed)

    def test_a_pass_exits_zero_and_a_missing_tool_is_still_only_a_skip(self):
        self.assertEqual(0, self.code(verdict.PASS)[0])
        self.assertEqual(0, self.code(verdict.SKIPPED)[0])

    def test_a_declared_known_failure_is_still_reported_without_failing_the_command(self):
        code, printed = self.code(verdict.NOT_EXERCISED, known_failure="the scripted model never reaches the stage")
        self.assertEqual(0, code)
        self.assertIn("known failure (not counted)", printed)
        self.assertIn(f"{verdict.NOT_EXERCISED} — summary", printed)

    def test_a_false_completion_still_fails_whatever_else_the_run_skipped(self):
        self.assertEqual(1, self.code(verdict.FALSE_COMPLETE)[0])
        self.assertEqual(1, self.code(verdict.HONEST_BLOCKER)[0])
        self.assertEqual(1, self.code(verdict.ERROR)[0])


class ModelProfileTests(unittest.TestCase):
    def test_route_audit_uses_launch_history_and_keeps_unknowns(self):
        state = {
            "settings": {"roles": {"terra": {"model": "replacement"}}},
            "stages": [
                {
                    "stage": "terra",
                    "engine": "opencode",
                    "model": "stale",
                    "command": ["opencode", "run", "--model", "openai/original"],
                },
                {"stage": "orchestrator", "runner_owned": True},
                {"stage": "sol", "engine": "opencode"},
            ],
            "active_stage": {
                "stage": "astra_review",
                "engine": "opencode",
                "command": ["opencode", "run", "--model=openai/checker"],
            },
        }
        self.assertEqual(["openai/original", None, "openai/checker"], [row["model"] for row in model_routes(state)])

    def test_python_module_switch_is_not_a_model(self):
        commands = [
            ["python3", "-m", "provider_cli", "--model", "openai/builder"],
            ["python3", "-m", "provider_cli"],
            ["/usr/local/bin/opencode", "run", "-m", "openai/checker"],
        ]
        state = {"stages": [{"stage": "terra", "command": command} for command in commands]}
        self.assertEqual(["openai/builder", None, "openai/checker"], [row["model"] for row in model_routes(state)])

    def test_codex_only_covers_recovery_routes_and_pins_checkers(self):
        profile = profiles.resolve("codex-only")
        flags = profiles.flags(profile)
        self.assertEqual("opencode", flags[flags.index("--provider") + 1])
        for flag in (*profiles.MODEL_FLAGS.values(), "--investigator-model", "--resolver-model"):
            self.assertTrue(flags[flags.index(flag) + 1].startswith("openai/"), flag)
        pins = {flags[i + 1] for i, flag in enumerate(flags) if flag == "--pin-model-role"}
        self.assertEqual({"astra", "terra", "sol", "completion"}, pins)
        models = profile["models"]
        for producer, checker in (("planner", "reviewer"), ("builder", "validator"), ("builder", "completion")):
            self.assertNotEqual(models[producer], models[checker])

    def test_claude_tiers_stays_on_claude_and_crosses_tiers(self):
        """#717: the built-in claude provider, with the 2026-10-04 qualified models and efforts."""
        profile = profiles.resolve("claude-tiers")
        flags = profiles.flags(profile)
        self.assertEqual(["--provider", "claude"], flags[:2])
        for flag in (*profiles.MODEL_FLAGS.values(), "--investigator-model"):
            self.assertTrue(flags[flags.index(flag) + 1].startswith("claude-"), flag)
        self.assertNotIn("--engine", flags)
        self.assertNotIn("--resolver-model", flags)
        models = profile["models"]
        for producer, checker in (("planner", "reviewer"), ("builder", "validator"), ("builder", "completion")):
            self.assertNotEqual(models[producer], models[checker])
        self.assertEqual(
            {
                "requirements": "claude-sonnet-5-5",
                "planner": "claude-sonnet-5-5",
                "reviewer": "claude-opus-5-5",
                "builder": "claude-haiku-4-5-20251001",
                "validator": "claude-sonnet-5-5",
                "resolver": "claude-opus-5-5",
                "completion": "claude-opus-5-5",
            },
            models,
        )
        self.assertEqual({"medium"}, set(profile["effort"].values()))

    def test_glm53_mimo_stays_on_glm_and_mimo_and_crosses_families(self):
        profile = profiles.resolve("glm53-mimo")
        flags = profiles.flags(profile)
        self.assertEqual("opencode", flags[flags.index("--provider") + 1])

        def family(model):
            return {"zai-coding-plan": "glm", "xiaomi-token-plan-sgp": "mimo"}.get(model.split("/")[0])

        for flag in (*profiles.MODEL_FLAGS.values(), "--investigator-model", "--resolver-model"):
            self.assertIn(family(flags[flags.index(flag) + 1]), ("glm", "mimo"), flag)
        models = profile["models"]
        for producer, checker in (("planner", "reviewer"), ("builder", "validator"), ("builder", "completion")):
            self.assertNotEqual(family(models[producer]), family(models[checker]))

    def test_provider_override_keeps_every_route_and_effort(self):
        from harness.driver import live_setup

        own, _ = live_setup("glm53-mimo")
        through_kilo, _ = live_setup("glm53-mimo", "kilocode")
        self.assertEqual("kilocode", through_kilo[through_kilo.index("--provider") + 1])
        self.assertEqual(own[2:], through_kilo[2:])
        self.assertEqual("opencode", profiles.resolve("glm53-mimo")["provider"])

    def test_provider_override_needs_a_live_profile(self):
        with self.assertRaisesRegex(SystemExit, "use it with --profile"):
            run.main(["run", "bugfix-trivial", "--fake", "--provider", "kilocode"])


class FakeSchemaTests(unittest.TestCase):
    """The scripted model answers "none" for any required field its script does not know yet."""

    def test_renewed_progressive_contract_omits_runner_provenance_and_keeps_user_edits(self):
        import importlib

        with (
            patch.dict(os.environ, {"SCENARIO_FAKE_CONFIG": "unused-config.json"}),
            patch.object(Path, "read_text", return_value=json.dumps({"check": "true", "paths": []})),
        ):
            fake = importlib.import_module("harness.fake_codex")
        preserved = {
            "intended_outcome": fake.RENEWED_OUTCOME,
            "required_behaviors": ["Preserve the edited learner goal"],
            "acceptance_criteria": [
                {
                    "id": "C1",
                    "criterion": "Saved answers survive",
                    "sources": [{"source_id": "S1", "quote": "Save answers"}],
                }
            ],
            "constraints": ["Retain prior saved-answer checks"],
            "permission_boundaries": ["Network access remains forbidden"],
            "scope_exclusions": ['Progressive check retirement: {"check_id":"B","removes":"Catalog sorting"}'],
        }
        existing = {
            **preserved,
            **{
                field: {"sealed": True}
                for field in ("initial_task", "brief_acceptance", "risk_acceptance", "source_trace")
            },
        }
        fresh_task = {"objective": "Plan a fresh task"}
        with (
            patch.object(fake, "PROGRESSIVE", True),
            patch.object(fake, "DATA", {"goal_contract": {"body": existing}}),
            patch.object(fake, "progressive_task", return_value=fresh_task),
        ):
            self.assertEqual(preserved, fake.contract())
            self.assertEqual({**preserved, "initial_task": fresh_task}, fake.contract(final=True))
        self.assertEqual({"sealed": True}, existing["source_trace"])

    def test_report_identity_uses_schema_then_repair_handoff(self):
        import importlib

        with (
            patch.dict(os.environ, {"SCENARIO_FAKE_CONFIG": "unused-config.json"}),
            patch.object(Path, "read_text", return_value=json.dumps({"check": "true", "paths": []})),
        ):
            fake = importlib.import_module("harness.fake_codex")
        expected = {"contract_revision": 7, "contract_hash": "approved-hash", "task_id": "assigned-task"}
        for data, schema in (
            ({"report_identity": expected}, {}),
            (
                {"goal_contract": {"revision": 1, "hash": "stale"}, "current_task": {"id": "stale"}},
                {"properties": {key: {"enum": [value]} for key, value in expected.items()}},
            ),
        ):
            with self.subTest(data=data), patch.object(fake, "OUTPUT_SCHEMA", schema):
                self.assertEqual(expected, fake.report_identity(data))

    def test_execution_repair_preserves_report_without_executing_checks(self):
        import importlib

        with (
            patch.dict(os.environ, {"SCENARIO_FAKE_CONFIG": "unused-config.json"}),
            patch.object(Path, "read_text", return_value=json.dumps({"check": "true", "paths": []})),
        ):
            fake = importlib.import_module("harness.fake_codex")
        identity = {"contract_revision": 9, "contract_hash": "retained-hash", "task_id": "retained-task"}
        original = {
            "contract_revision": 0,
            "criterion_results": [{"id": "C9", "status": "FAIL"}],
            "checks": [{"command": "false", "exit_code": 1, "evidence_ref": "event:original"}],
        }
        for stage in ("terra", "sol", "astra_checkpoint", "astra_review", "astra_plan", "astra_resolve"):
            for content in (original, json.dumps(original)):
                with (
                    self.subTest(stage=stage, content=content),
                    patch.object(fake, "OUTPUT_SCHEMA", {}),
                    patch.object(fake, "run_check", side_effect=AssertionError("Repair must not execute")),
                ):
                    data = {"report_repair": True, "report_identity": identity, "rejected_report": {"content": content}}
                    self.assertEqual({**original, **identity}, fake.report_for(stage, data))
                    self.assertEqual(0, original["contract_revision"])

    def test_empty_or_invalid_execution_repair_never_executes(self):
        import importlib

        with (
            patch.dict(os.environ, {"SCENARIO_FAKE_CONFIG": "unused-config.json"}),
            patch.object(Path, "read_text", return_value=json.dumps({"check": "true", "paths": []})),
        ):
            fake = importlib.import_module("harness.fake_codex")
        identity = {"contract_revision": 3, "contract_hash": "approved", "task_id": "assigned"}
        for stage in ("terra", "sol", "astra_checkpoint", "astra_review", "astra_plan", "astra_resolve"):
            for content in ({}, "{}", None, "", "not json", [], "[]"):
                with (
                    self.subTest(stage=stage, content=content),
                    patch.object(fake, "OUTPUT_SCHEMA", {}),
                    patch.object(fake, "run_check", side_effect=AssertionError("Repair must not execute")),
                    patch.object(fake.shutil, "copytree", side_effect=AssertionError("Repair must not write")),
                ):
                    data = {"report_repair": True, "report_identity": identity, "rejected_report": {"content": content}}
                    if content == {} or content == "{}":
                        self.assertEqual(identity, fake.report_for(stage, data))
                    else:
                        with self.assertRaises(SystemExit):
                            fake.report_for(stage, data)

    def test_completion_repair_uses_source_verdict_schema_without_live_contract(self):
        import importlib

        with tempfile.TemporaryDirectory() as root:
            config = Path(root) / "config.json"
            configuration = {"check": "false", "paths": [], "brief": "x"}
            config.write_text(json.dumps(configuration))
            with patch.dict(os.environ, {"SCENARIO_FAKE_CONFIG": str(config)}):
                fake = importlib.import_module("harness.fake_codex")
                identity = {"contract_revision": 9, "contract_hash": "approved", "task_id": "assigned"}
                failed_verdict = {"verdict": "FAIL", "uncovered": ["S1"], "summary": "Uncovered source"}
                for declared, existing in ((True, None), (True, failed_verdict), (False, None)):
                    with self.subTest(declared=declared, existing=existing):
                        original = {"checks": [{"command": "false", "exit_code": 1, "evidence_ref": "event:original"}]}
                        if existing is not None:
                            original["source_verdict"] = existing
                        data = {
                            "stage": "astra_review_report_repair",
                            "original": {"stage": "astra_review"},
                            "report_repair": True,
                            "report_identity": identity,
                            "rejected_report": {"content": json.dumps(original)},
                        }
                        schema = Path(root) / "schema.json"
                        schema.write_text(
                            json.dumps({"type": "object", "properties": {"source_verdict": {}} if declared else {}})
                        )
                        output = Path(root) / "report.json"
                        with (
                            patch.multiple(fake, CONFIG=configuration, DATA={}, PROMPT="", OUTPUT_SCHEMA={}),
                            patch.object(fake, "emit"),
                            patch.object(fake, "run_check", side_effect=AssertionError("Repair must not execute")),
                            patch.object(sys, "stdin", io.StringIO("CURRENT HANDOFF DATA\n" + json.dumps(data))),
                            patch.object(sys, "argv", ["fake", "--output-schema", str(schema), "-o", str(output)]),
                        ):
                            self.assertEqual(0, fake._main())
                        report = json.loads(output.read_text())
                        self.assertEqual(original["checks"], report["checks"])
                        if declared:
                            self.assertEqual(
                                existing
                                or {
                                    "verdict": "PASS",
                                    "uncovered": [],
                                    "summary": "Scripted: every criterion traces to the brief",
                                },
                                report["source_verdict"],
                            )
                        else:
                            self.assertNotIn("source_verdict", report)

    def test_planner_contract_mode_uses_required_task_schema_or_prompt_marker(self):
        import importlib

        with (
            patch.dict(os.environ, {"SCENARIO_FAKE_CONFIG": "unused-config.json"}),
            patch.object(Path, "read_text", return_value=json.dumps({"check": "true", "paths": []})),
        ):
            fake = importlib.import_module("harness.fake_codex")
        cases = [
            ("repair requires task", "", {"properties": {"contract": {"required": ["initial_task"]}}}, True),
            (
                "repair draft",
                "",
                {"properties": {"contract": {"required": [], "properties": {"initial_task": {"type": "object"}}}}},
                False,
            ),
            ("no schema", "", {}, False),
            ("prompt marker", "ADAPTIVE PLANNING", {}, True),
        ]
        for name, prompt, schema, final in cases:
            with (
                self.subTest(name=name),
                patch.multiple(fake, PROMPT=prompt, OUTPUT_SCHEMA=schema, CONFIG={}, DATA={}, PROGRESSIVE=False),
                patch.object(fake, "source_refs", return_value=[]),
                patch.object(fake, "permission_changes", return_value=[]),
                patch.object(fake, "trace", return_value=[]),
                patch.object(fake, "contract", return_value={}) as contract,
            ):
                fake.report_for("astra_discovery", {"report_repair": True})
                contract.assert_called_once_with(final=final)

    def test_missing_required_fields_get_empty_values_of_their_type(self):
        import importlib
        import json
        import os

        with tempfile.TemporaryDirectory() as root:
            config = Path(root) / "config.json"
            config.write_text(json.dumps({"check": "true", "paths": [], "brief": "x"}))
            os.environ["SCENARIO_FAKE_CONFIG"] = str(config)
            try:
                fake = importlib.import_module("harness.fake_codex")
            finally:
                del os.environ["SCENARIO_FAKE_CONFIG"]
        schema = {
            "type": "object",
            "required": ["kept", "rows", "note", "flag", "kind", "nested"],
            "properties": {
                "kept": {"type": "string"},
                "rows": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["id", "extra"],
                        "properties": {"id": {"type": "string"}, "extra": {"type": "array"}},
                    },
                },
                "note": {"type": "string"},
                "flag": {"type": "boolean"},
                "kind": {"type": "string", "enum": ["none", "some"]},
                "nested": {"type": "object", "required": ["n"], "properties": {"n": {"type": "integer"}}},
            },
        }
        report = fake.complete({"kept": "yes", "rows": [{"id": "R1"}]}, schema)
        self.assertEqual(
            {
                "kept": "yes",
                "rows": [{"id": "R1", "extra": []}],
                "note": "",
                "flag": False,
                "kind": "none",
                "nested": {"n": 0},
            },
            report,
        )


class FakeBuilderClassificationTests(unittest.TestCase):
    def setUp(self):
        import importlib

        temporary = tempfile.TemporaryDirectory(prefix="fake-classification-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        config = self.root / "config.json"
        config.write_text(json.dumps({"check": "true", "paths": [], "brief": "x"}))
        with patch.dict(os.environ, {"SCENARIO_FAKE_CONFIG": str(config)}):
            self.fake = importlib.import_module("harness.fake_codex")
        self.scenario = catalog.load("feature-stock-refusals")
        shutil.copytree(self.scenario.dir / "broken/vacuous-refusal-tests", self.root / "project")
        self.project = self.root / "project"
        self.receipts = self.project / "run"
        self.receipts.mkdir()
        self.output = self.receipts / "completion-review-01.json"
        self.after = self.receipts / "completion-review-01.after.json"
        self.name = "test_c3_move_more_than_on_hand_is_refused"
        self.identity = dict(task_id="current-task", contract_hash="approved-contract", contract_revision=2)
        self.output.write_text(
            json.dumps(
                {
                    **self.identity,
                    "status": "REWORK",
                    "findings": [
                        {
                            "finding": f"regression_proof is not PASS: Test case C3 has no test named {self.name} "
                            "that passes with the change and did not pass without it"
                        }
                    ],
                }
            )
        )
        self.after.write_text(
            json.dumps(
                {
                    "revision": "current-source",
                    "files": {
                        path: hashlib.sha256((self.project / path).read_bytes()).hexdigest()
                        for path in ("stock.py", "tests/test_stock.py")
                    },
                }
            )
        )
        self.data = {
            "builder_failure": {
                "failure_id": "current-failure",
                "binding": self.identity,
                "record": {
                    "output": str(self.output),
                    "after_ref": str(self.after),
                    "source_revision": "current-source",
                },
                "evidence_refs": [str(self.output), str(self.after)],
            }
        }

    def report(self, data=None):
        with (
            patch.object(self.fake, "CONFIG", {"fault": "vacuous_refusal_tests"}),
            patch.object(self.fake, "run_check", side_effect=AssertionError("must not rerun checks")),
        ):
            return self.fake.report_for("investigate_stuck", self.data if data is None else data)

    def probe(self, report):
        import shlex

        return subprocess.run(
            shlex.split(report["probe"]), cwd=self.project, capture_output=True, text=True, timeout=15
        )

    def test_stock_classification_binds_current_failure_and_reads_receipts_without_writing(self):
        before = {p: p.read_bytes() for p in self.project.rglob("*") if p.is_file()}
        report = self.report()
        self.assertEqual("execution", report["failure_class"])
        self.assertEqual("current-failure", report["failure_id"])
        self.assertEqual(self.data["builder_failure"]["evidence_refs"], report["evidence_refs"])
        self.assertEqual("retry", report["recommendation"])
        self.assertIn(self.name, report["diagnosis"])
        self.assertIn("tests/test_stock.py", report["guidance"])
        self.assertTrue(report["example"])
        self.assertEqual("", report["untestable"])
        result = self.probe(report)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(before, {p: p.read_bytes() for p in self.project.rglob("*") if p.is_file()})

    def test_stock_probe_rejects_stale_identity_false_receipt_and_changed_code(self):
        report = self.report()
        original = self.output.read_bytes()
        for update in (
            {"status": "COMPLETE"},
            {"findings": []},
            {"task_id": "stale-task"},
            {"contract_hash": "stale-contract"},
            {"contract_revision": 1},
        ):
            with self.subTest(update=update):
                self.output.write_text(json.dumps({**json.loads(original), **update}))
                self.assertNotEqual(0, self.probe(report).returncode)
        self.output.write_bytes(original)
        shutil.copy2(self.scenario.reference / "tests/test_stock.py", self.project / "tests/test_stock.py")
        self.assertNotEqual(0, self.probe(report).returncode)
        snapshot = json.loads(self.after.read_text())
        snapshot["files"]["tests/test_stock.py"] = hashlib.sha256(
            (self.project / "tests/test_stock.py").read_bytes()
        ).hexdigest()
        self.after.write_text(json.dumps(snapshot))
        self.assertNotEqual(0, self.probe(report).returncode, "strengthened tests are not the vacuous cause")

    def test_stock_classification_requires_real_failure_and_exact_allowed_refs(self):
        for update in ({"status": "COMPLETE"}, {"findings": []}):
            with self.subTest(update=update):
                self.output.write_text(json.dumps({**self.identity, "status": "REWORK", "findings": [], **update}))
                with self.assertRaisesRegex(SystemExit, "actual failed"):
                    self.report()
        self.data["builder_failure"]["evidence_refs"] = [str(self.output)]
        with self.assertRaisesRegex(SystemExit, "pinned report/source"):
            self.report()

    def test_generic_investigator_pause_does_not_gain_classification_or_retry(self):
        report = self.report({})
        self.assertEqual("pause", report["recommendation"])
        self.assertEqual([], report["evidence_refs"])
        self.assertNotIn("failure_class", report)
        self.assertNotIn("failure_id", report)

    def test_generic_report_repair_with_open_runner_finding_stays_a_pause(self):
        report = self.report(
            {
                "report_repair": True,
                "open_findings": [
                    {
                        "id": "runner-finding",
                        "source": "runner",
                        "blocking": True,
                        "finding": "The lifecycle observation failed",
                    }
                ],
            }
        )
        self.assertEqual("pause", report["recommendation"])
        self.assertEqual([], report["evidence_refs"])
        self.assertNotIn("failure_class", report)
        self.assertNotIn("failure_id", report)


class HybridScenarioTests(unittest.TestCase):
    def test_a_hybrid_scenario_is_skipped_under_a_live_profile(self):
        with tempfile.TemporaryDirectory(prefix="scenario-test-") as out:
            args = argparse.Namespace(
                fake=False,
                profile="codex-only",
                fake_solution="reference",
                out=Path(out),
                autocode=None,
                max_steps=None,
                timeout_minutes=10,
                i_authorize_live_model_spend=True,
            )
            result = run.run_one(catalog.load("stuck-planner-citation"), args)
        self.assertEqual(verdict.SKIPPED, result["verdict"])
        self.assertIn("run it with --fake --i-authorize-live-model-spend", result["summary"])


class InterruptedRunTests(unittest.TestCase):
    """A cutoff is not graded, but the decisions the harness made before it are kept (#455)."""

    def test_an_interrupted_run_keeps_the_answers_and_approvals_it_made(self):
        answers = [{"id": "Q1", "answer": "Keep data.", "source": "proposed_default"}]
        approvals = [{"gate": "approve_plan", "token": "plan:1"}]

        def launch(project, root, flags, env, **options):
            driver = Mock(run_dir=None, steps=[], answers=answers, approvals=approvals, turn_marks=[])
            driver.drive.side_effect = InterruptedDrive("owner lost before the run was judged")
            return driver

        with tempfile.TemporaryDirectory(prefix="scenario-test-") as out, patch.object(run, "Driver", launch):
            args = argparse.Namespace(
                fake=True,
                profile=None,
                provider=None,
                hybrid=False,
                fake_solution="reference",
                out=Path(out),
                autocode=None,
                max_steps=None,
                timeout_minutes=1,
                i_authorize_live_model_spend=False,
            )
            result = run.run_one(catalog.load("greenfield-greeting-cli"), args)
            saved = json.loads((Path(result["evidence"]) / "result.json").read_text())
        self.assertEqual(verdict.INTERRUPTED_UNGRADED, saved["verdict"])
        self.assertEqual((answers, approvals), (saved["answers"], saved["approvals"]))
        (row,) = stats.summarize([saved])
        self.assertEqual((0, 1, 1), (row["person_answers"], row["default_answers"], row["approvals"]))


class FakeRunTests(unittest.TestCase):
    """End to end through AutoCode's real CLI, with the scripted model (about 30 s each)."""

    def run_fake(self, solution, scenario="bugfix-iso-weeks"):
        with tempfile.TemporaryDirectory(prefix="scenario-test-") as out:
            args = argparse.Namespace(
                fake=True,
                profile=None,
                fake_solution=solution,
                out=Path(out),
                autocode=None,
                max_steps=None,
                timeout_minutes=10,
            )
            return run.run_one(catalog.load(scenario), args)

    def test_correct_solution_is_judged_pass(self):
        result = self.run_fake("reference")
        self.assertEqual(verdict.PASS, result["verdict"], result["summary"])
        self.assertEqual("TASK_COMPLETE", result["runner_status"])

    def test_fake_run_ignores_ambient_billing_api_keys(self):
        # Joint planning refuses ambient API-key overrides. Fake runs must still
        # launch when the host has OPENAI_API_KEY set (developer machines, CI secrets).
        with tempfile.TemporaryDirectory(prefix="scenario-test-") as out:
            args = argparse.Namespace(
                fake=True,
                profile=None,
                fake_solution="reference",
                out=Path(out),
                autocode=None,
                max_steps=None,
                timeout_minutes=10,
            )
            ambient = {
                "OPENAI_API_KEY": "sk-test-not-for-spend",
                "CODEX_API_KEY": "codex-test-not-for-spend",
                "OPENAI_BASE_URL": "https://example.invalid/v1",
                "ANTHROPIC_API_KEY": "anthropic-test-not-for-spend",
            }
            with patch.dict(os.environ, ambient, clear=False):
                result = run.run_one(catalog.load("bugfix-trivial"), args)
            self.assertEqual(verdict.PASS, result["verdict"], result["summary"])
            self.assertNotIn("PAUSED_BILLING_ROUTE", result.get("summary") or "")

    def test_the_result_records_who_decided_each_gate(self):
        """#455 through the real CLI: the plan the harness approved is in the result, and the stats row counts it
        instead of reading zero decisions (bugfix-trivial asks no questions)."""
        result = self.run_fake("reference", scenario="bugfix-trivial")
        self.assertEqual(verdict.PASS, result["verdict"], result["summary"])
        self.assertEqual(["approve_plan"], [row["gate"] for row in result["approvals"]], result["approvals"])
        # #722: `--status --json` lists that approval with the person's Git identity, this host and the command.
        (provenance,) = [check for check in result["checks"] if check["name"] == "approval provenance: task"]
        self.assertTrue(provenance["ok"], provenance["detail"])
        self.assertIn(f"1 recorded (plan); each one a person made names {PERSON_NAME}", provenance["detail"])
        # #720: before approving, the harness exported the plan and `autocode plan --check` read the file back with
        # the plan's goal hash, leaving the run as it was; the completed run reports that goal hash as approved.
        (trip,) = [check for check in result["checks"] if check["name"] == "plan round trip"]
        self.assertTrue(trip["ok"], trip["detail"])
        self.assertIn("1 plan(s) exported at the approval stop", trip["detail"])
        self.assertTrue(trip["detail"].endswith(", approved"), trip["detail"])
        plan_file = result["approvals"][0]["plan_file"]
        self.assertEqual(plan_file["export"]["goal_hash"], plan_file["check"]["goal_hash"])
        self.assertEqual([], plan_file["run_changed"])
        (row,) = stats.summarize([result])
        self.assertEqual((0, 0, 1), (row["person_answers"], row["default_answers"], row["approvals"]))

    def test_wrong_solution_that_autocode_accepts_is_judged_false_complete(self):
        result = self.run_fake("broken/special-case")
        self.assertEqual(verdict.FALSE_COMPLETE, result["verdict"], result["summary"])

    def test_parallel_diamond_builds_b_and_c_as_one_parallel_batch(self):
        with tempfile.TemporaryDirectory(prefix="scenario-test-") as out:
            args = argparse.Namespace(
                fake=True,
                profile=None,
                fake_solution="reference",
                out=Path(out),
                autocode=None,
                max_steps=None,
                timeout_minutes=10,
            )
            result = run.run_one(catalog.load("parallel-diamond"), args)
            self.assertEqual(verdict.PASS, result["verdict"], result["summary"])
            state = json.loads((Path(result["evidence"]) / "state.json").read_text())
            # B and C were accepted as one integrated parallel batch, not sequentially.
            batches = [sorted(row.get("milestone_ids", [])) for row in state.get("milestone_progress", {}).values()]
            self.assertIn(["B", "C"], batches)
            self.assertTrue(
                sorted((Path(result["run_dir"]) / "orchestration").iterdir()),
                "no orchestration batch directory was created",
            )

    def test_parallel_diamond_broken_variant_is_caught(self):
        result = self.run_fake("broken/missing-edge", "parallel-diamond")
        self.assertNotEqual(verdict.PASS, result["verdict"], result["summary"])

    def test_a_package_run_completes_a_change_inside_its_package(self):
        """#711: started with --package packages/greet/ ([fake] flags, never repeated on the saved run), the
        reference completes, and the status view reports nothing outside the package."""
        result = self.run_fake("reference", "feature-monorepo-package")
        self.assertEqual(verdict.PASS, result["verdict"], result["summary"])
        self.assertIn("outside_changes_reported", [row["name"] for row in result["checks"]])

    def test_a_package_run_reports_a_write_to_the_other_package_and_stops(self):
        """#711: the broken variant also edits packages/farewell/. The runner names both files in an open
        blocking finding and the run stops without completing; before #1415 it completed (FALSE_COMPLETE)."""
        result = self.run_fake("broken/edits-farewell-package", "feature-monorepo-package")
        self.assertEqual(verdict.HONEST_BLOCKER, result["verdict"], result["summary"])
        failing = [row["name"] for row in result["checks"] if not row["ok"]]
        self.assertEqual(["nothing_changed_outside_the_package"], failing, result["checks"])
        self.assertIn("outside_changes_reported", [row["name"] for row in result["checks"]])

    def test_a_package_bug_fix_is_investigated_and_completes_inside_its_package(self):
        """#711: a bug fix started with --package packages/invoices/ goes through the Investigator, its proof runs
        in the package, and it completes; the runner's diagnosis note under docs/bugs/ is not reported as a change
        outside the package (before #1415 the proof counted it, and the run never completed)."""
        result = self.run_fake("reference", "bugfix-monorepo-package")
        self.assertEqual(verdict.PASS, result["verdict"], result["summary"])
        names = [row["name"] for row in result["checks"]]
        self.assertIn("investigated_first", names)
        self.assertIn("outside_changes_reported", names)

    def test_a_package_bug_fix_that_also_fixes_the_other_package_stops(self):
        """#711: the broken variant also fixes packages/search/'s copy. The runner names that file in an open
        blocking finding and the run stops without completing."""
        result = self.run_fake("broken/fixes-search-copy-too", "bugfix-monorepo-package")
        self.assertEqual(verdict.HONEST_BLOCKER, result["verdict"], result["summary"])
        failing = [row["name"] for row in result["checks"] if not row["ok"]]
        self.assertEqual(["nothing_changed_outside_the_package"], failing, result["checks"])
        self.assertIn("outside_changes_reported", [row["name"] for row in result["checks"]])

    def test_a_review_runs_only_the_reviewer_and_leaves_the_tree_alone(self):
        result = self.run_fake("reference", "review-clean-pr")
        self.assertEqual(verdict.PASS, result["verdict"], result["summary"])
        self.assertEqual("review", result["workflow"])
        self.assertEqual(["requirements_gather", "review_change"], result["metrics"]["stage_names"])

    def test_tiny_jobs_do_not_quietly_take_more_steps(self):
        # Issue #15: small jobs already take many model calls. These are today's counts
        # with the scripted model; lower them when a step is trimmed, never raise them
        # without deciding that the extra step is worth its time.
        # bugfix-trivial was 5 with the short path for small fixes; it is 9 while that path
        # is off (2026-09-29).
        for scenario, ceiling in (("greenfield-greeting-cli", 9), ("bugfix-trivial", 9)):
            with self.subTest(scenario=scenario):
                result = self.run_fake("reference", scenario)
                self.assertEqual(verdict.PASS, result["verdict"], result["summary"])
                self.assertLessEqual(result["metrics"]["model_stages"], ceiling, result["metrics"]["model_stage_names"])
                self.assertGreater(result["wall_seconds"], 0)

    def test_bugfix_completes_with_runner_diagnosis_outside_builder_scope(self):
        with tempfile.TemporaryDirectory(prefix="scenario-test-") as out:
            args = argparse.Namespace(
                fake=True,
                profile=None,
                fake_solution="reference",
                out=Path(out),
                autocode=None,
                max_steps=None,
                timeout_minutes=10,
            )
            result = run.run_one(
                catalog.load("bugfix-trivial"), args, extra_env={"SCENARIO_FAKE_REQUIRE_DIAGNOSIS_PROVENANCE": "1"}
            )
            self.assertEqual(verdict.PASS, result["verdict"], result["summary"])
            self.assertIn("investigate_bug", result["metrics"]["model_stage_names"])
            self.assertIn("astra_review", result["metrics"]["model_stage_names"])
            self.assertEqual(0, result["metrics"]["report_repairs"])

    def test_a_conversation_reviews_first_then_says_its_follow_up_in_the_same_run(self):
        result = self.run_fake("reference", "review-then-fix")
        self.assertEqual(2, len(result["turns"]), result["summary"])
        self.assertEqual(["requirements_gather", "review_change"], result["turns"][0]["model_stage_names"])
        self.assertEqual("review", result["turns"][0]["workflow"])
        self.assertTrue(result["turns"][1]["say"].startswith("Fix them."))

    def test_an_invented_blocker_in_a_review_is_judged_false_complete(self):
        result = self.run_fake("broken/invented-blocker", "review-clean-pr")
        self.assertEqual(verdict.FALSE_COMPLETE, result["verdict"], result["summary"])

    def run_copy(self, scenario, edit, solution="reference"):
        """A fake run of a temporary copy of a catalog scenario, its scenario.toml rewritten by ``edit``."""
        with tempfile.TemporaryDirectory(prefix="scenario-copy-") as root:
            shutil.copytree(catalog.CATALOG / scenario, Path(root) / scenario)
            toml = Path(root) / scenario / "scenario.toml"
            toml.write_text(edit(toml.read_text()))
            with patch.object(catalog, "CATALOG", Path(root)):
                return self.run_fake(solution, scenario)

    def test_discuss_then_design_then_build_builds_the_design_its_second_turn_wrote(self):
        with tempfile.TemporaryDirectory(prefix="scenario-test-") as out:
            args = argparse.Namespace(
                fake=True,
                profile=None,
                fake_solution="reference",
                out=Path(out),
                autocode=None,
                max_steps=None,
                timeout_minutes=10,
            )
            result = run.run_one(catalog.load("discuss-then-design-then-build"), args)
            state = json.loads((Path(result["evidence"]) / "state.json").read_text())
        self.assertEqual(verdict.PASS, result["verdict"], result["summary"])
        self.assertEqual(["discuss", "design", "build"], [turn["workflow"] for turn in result["turns"]])
        # "Build it." named the design turn 2 wrote: Requirements named it, it was checked as approved, and
        # Requirements did not run a second time.
        self.assertEqual(["requirements_gather", "check_design"], result["turns"][2]["model_stage_names"][:2])
        self.assertEqual(1, result["turns"][2]["model_stage_names"].count("requirements_gather"))
        # The build was planned afresh from the approved design, not as a revision of the design turn's
        # docs-only contract (live Planners were refused there, then repaired or asked the user): that
        # contract moved to contract_history, and every contract since declares no change against it.
        self.assertNotIn("astra_discovery_report_repair", result["turns"][2]["model_stage_names"])
        archived = state["turns"][-1]["fresh_plan"]["contract"]
        contracts = [*state["contract_history"], state["goal_contract"]]
        design = next(row for row in contracts if f"r{row['revision']}:{row['hash']}" == archived)
        self.assertEqual(["Edit only docs/design/ in this scenario workspace"], design["body"]["permission_boundaries"])
        built = [row for row in contracts if row["revision"] > design["revision"]]
        self.assertEqual(state["goal_contract"], built[-1])
        self.assertEqual([[]] * len(built), [row.get("declared_changes") for row in built])
        self.assertEqual(
            ["Edit only app/, tests/ in this scenario workspace"],
            state["goal_contract"]["body"]["permission_boundaries"],
        )

    def test_a_design_turn_that_also_writes_code_is_judged_false_complete(self):
        # Nothing in the product limits a new design's Builder to documents; the per-turn check does.
        widened = lambda text: text.replace('["docs/design/"], ["app/"', '["docs/design/", "app/"], ["app/"')
        result = self.run_copy("discuss-then-design-then-build", widened)
        self.assertEqual(verdict.FALSE_COMPLETE, result["verdict"], result["summary"])
        failing = [check["name"] for check in result["checks"] if not check["ok"]]
        # The code came in the design turn, so the build turn changed nothing under app/ either.
        self.assertEqual(["build_follows_design", "design_turn_changed_only_its_report"], failing)

    def test_a_design_review_is_revised_in_the_same_run_as_the_user_answers_it(self):
        result = self.run_fake("reference", "design-review-with-answers")
        self.assertEqual(verdict.PASS, result["verdict"], result["summary"])
        self.assertEqual(["design"] * 3, [turn["workflow"] for turn in result["turns"]])
        # Each reply runs the Architect again on the same review, after Requirements names it design: nothing
        # is built.
        self.assertEqual(
            [["requirements_gather", "review_design"]] * 3, [turn["model_stage_names"] for turn in result["turns"]]
        )

    def test_a_revision_that_ignores_an_answer_or_renumbers_its_concerns_never_passes(self):
        for solution, judged, failing in (
            ("broken/answer-ignored", verdict.FALSE_COMPLETE, "ordering_blocking_after_first_answer"),
            ("broken/still-blocking", verdict.FALSE_COMPLETE, "ordering_resolved_after_second_answer"),
            # The runner refuses a revision that drops earlier ids, and its repairs, and stops.
            ("broken/rewritten", verdict.HONEST_BLOCKER, "revised_not_rewritten"),
        ):
            with self.subTest(solution):
                result = self.run_fake(solution, "design-review-with-answers")
                self.assertEqual(judged, result["verdict"], result["summary"])
                self.assertIn(failing, [check["name"] for check in result["checks"] if not check["ok"]])

    def test_a_turn_after_a_stop_is_never_said_and_the_run_is_an_honest_blocker(self):
        # implement-design-conflict stops, as expected; a follow-up after its completion is never reached.
        result = self.run_copy(
            "implement-design-conflict",
            lambda text: text + '\n[[turn]]\nafter = "complete"\nsay = "Build it anyway."\n',
        )
        self.assertEqual(
            (verdict.HONEST_BLOCKER, "PAUSED_DESIGN_CONFLICT"),
            (result["verdict"], result["runner_status"]),
            result["summary"],
        )
        self.assertTrue(
            result["summary"].startswith("stopped before turn 2: AutoCode stopped at PAUSED_DESIGN_CONFLICT")
        )
        self.assertEqual("", result["harness_error"])  # the product stopped, not the harness
        self.assertIn("stopped before turn 2", result["turn_not_reached"])


class StockRefusalsRunTests(unittest.TestCase):
    """feature-stock-refusals (issue #59) end to end with the scripted model (about 30 s each). Only the model
    is fake: the runner's regression proof finds the vacuous refusal tests, and the scripted Resolver writes
    its diagnosis from its handoff alone. These prove the route is reached and that the scoring can come out
    CORRECT and INCORRECT, never how well a real model diagnoses. Plan B1 (each variant fails for its own
    reason) is OracleControlTests.test_feature_stock_refusals (CONTROL_SUMMARIES)."""

    def run_fake(self, solution="reference", env=None):
        with tempfile.TemporaryDirectory(prefix="scenario-test-") as out:
            args = argparse.Namespace(
                fake=True,
                profile=None,
                fake_solution=solution,
                out=Path(out),
                autocode=None,
                max_steps=None,
                timeout_minutes=10,
            )
            result = run.run_one(catalog.load("feature-stock-refusals"), args, extra_env=env)
            state = json.loads((Path(result["evidence"]) / "state.json").read_text())
            proofs = [json.loads(Path(row["path"]).read_text())["verdict"] for row in state["regression_proofs"]]
            return result, state, proofs

    def test_the_reference_reaches_autoresolver_through_the_failed_proof_and_is_scored_correct(self):
        result, state, proofs = self.run_fake()
        self.assertEqual(verdict.PASS, result["verdict"], result["summary"])
        self.assertEqual(
            [
                "requirements_gather",
                "astra_discovery",
                "astra_challenge",
                "terra",
                "sol",
                "astra_review",
                "astra_resolve",
                "investigate_stuck",
                "terra",
                "sol",
                "astra_review",
            ],
            result["metrics"]["model_stage_names"],
        )
        # The trap fails twice (the anti-flake replay), AutoResolver runs, and the repaired proof passes.
        self.assertEqual(["FAIL", "FAIL", "PASS"], proofs)
        revisions = [row["source_revision"] for row in state["regression_proofs"]]
        self.assertEqual(revisions[0], revisions[1], "Replay must use the same failing source")
        self.assertNotEqual(revisions[1], revisions[2], "PASS must follow a source repair")
        self.assertEqual(1, len(state["resolution_history"]))
        self.assertEqual([], state.get("direct_rework_assignments") or [])
        diagnosis = result["diagnosis"]
        self.assertEqual(verdict.CORRECT, diagnosis["verdict"], diagnosis["reason"])
        self.assertEqual(
            [
                "test_c3_move_more_than_on_hand_is_refused",
                "test_c4_move_to_same_location_is_refused",
                "test_c5_malformed_store_is_refused",
                "test_c6_non_positive_quantity_is_refused",
                "test_c7_remove_more_than_on_hand_is_refused",
            ],
            diagnosis["vacuous_tests"],
        )
        # Which tests are about move/remove was read from the runner's code checkpoint of the trap source.
        self.assertEqual(["code checkpoint"], list(diagnosis["trap_tests_read_from"].values()))
        self.assertEqual(7, len(diagnosis["checks"]))
        self.assertEqual(1, diagnosis["resolver_calls_on_trap"])

    def test_a_misattributed_diagnosis_is_incorrect_while_the_run_still_passes(self):
        result, state, proofs = self.run_fake(env={"SCENARIO_FAKE_RESOLVER": "misattribute"})
        self.assertEqual(verdict.PASS, result["verdict"], result["summary"])
        self.assertEqual(["FAIL", "FAIL", "PASS"], proofs)
        revisions = [row["source_revision"] for row in state["regression_proofs"]]
        self.assertEqual(revisions[0], revisions[1], "Replay must use the same failing source")
        self.assertNotEqual(revisions[1], revisions[2], "PASS must follow a source repair")
        self.assertEqual(verdict.INCORRECT, result["diagnosis"]["verdict"])
        self.assertEqual(
            {
                "diagnosis_names_each_vacuous_test",
                "diagnosis_explains_why_they_pass_on_original_code",
                "resolver_chose_bounded_test_repair",
            },
            {check["name"] for check in result["diagnosis"]["checks"] if not check["ok"]},
        )

    def test_tests_that_never_discriminate_stop_honestly_and_the_repair_is_scored_incorrect(self):
        # The Builder keeps the vacuous tests. Since #2485 the no-causal-progress hold is AutoCode's to settle,
        # so each new incident takes its own automatic scoped retry: the run walks three Resolver cycles and ends
        # at the configured Builder retry limit — the shape plan B4 recorded on e2eadf0 — instead of holding a
        # person at RESOLVER_PENDING. That last pause still asks for a human decision, which #2485 did not claim
        # to settle; what this case proves is that the route is reached and the scoring comes out INCORRECT.
        result, state, proofs = self.run_fake("broken/vacuous-refusal-tests")
        self.assertEqual(verdict.HONEST_BLOCKER, result["verdict"], result["summary"])
        self.assertEqual("PAUSED_BUILDER_RETRY_LIMIT", result["runner_status"])
        self.assertEqual(3, result["metrics"]["model_stage_names"].count("astra_resolve"))
        self.assertEqual(4, result["metrics"]["model_stage_names"].count("investigate_stuck"))
        # Classifying each unknown failure precedes its bounded execution retry, and AutoCode grants them itself.
        self.assertEqual(
            ["investigate", "retry", "investigate", "escalate", "investigate", "pause"],
            [row["action"] for row in state["builder_retry_decisions"]],
        )
        self.assertEqual(["autocode"] * 3, [row["actor"] for row in state["failure_retry_authorizations"]])
        self.assertEqual({"FAIL"}, set(proofs))
        diagnosis = result["diagnosis"]
        self.assertEqual((verdict.INCORRECT, 3), (diagnosis["verdict"], diagnosis["resolver_calls_on_trap"]))
        self.assertEqual(
            ["repair_made_the_tests_discriminate"], [check["name"] for check in diagnosis["checks"] if not check["ok"]]
        )


def load_stage_script():
    spec = importlib.util.spec_from_file_location("hybrid_stage", Path(hybrid.STAGE_SCRIPT))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HybridRouteTests(unittest.TestCase):
    """Hybrid runs (harness/hybrid.py): which side serves each call, the tool AutoCode is given, and when a
    scenario or a live tool cannot be run that way. No AutoCode run."""

    def test_archived_scripted_failure_is_not_counted_as_a_live_model_call(self):
        original = {"stage": "astra_discovery", "side": "scripted", "report": "/run/iterations/001/discovery-01.json"}
        archived = "/run/iterations/001/archived-discovery-01-id/discovery-01.jsonl"
        context = {"stage": "astra_discovery", "events": archived}
        retry = {
            "stage": "astra_discovery",
            "side": "live",
            "report": "/run/iterations/001/discovery-02.json",
            "recovery_context": context,
        }
        self.assertEqual([original["report"], archived], hybrid.scripted_outputs([original, retry]))
        for changed in (
            {"stage": "terra"},
            {"events": "/other/iterations/001/archived-id/discovery-01.jsonl"},
            {"events": "/run/iterations/001/archived-id/discovery-02.jsonl"},
        ):
            with self.subTest(changed=changed):
                self.assertEqual(
                    [original["report"]],
                    hybrid.scripted_outputs([original, {**retry, "recovery_context": {**context, **changed}}]),
                )

    def test_a_route_scripts_every_call_of_its_scripted_stages_and_only_the_first_of_a_first_attempt_stage(self):
        stage_script = load_stage_script()
        route = {"scripted": ["astra_discovery"], "first_attempt": ["terra"]}
        trace = []

        def serve(stage, repair=False):
            side = stage_script.side_for(route, stage, repair, trace)
            trace.append({"stage": stage, "repair": repair, "side": side})
            return side

        self.assertEqual(
            ["scripted", "scripted", "scripted", "scripted", "live", "live", "live", "live"],
            [
                serve("astra_discovery"),
                serve("astra_discovery", repair=True),
                serve("terra"),
                serve("terra", repair=True),  # a report repair goes where its stage went
                serve("astra_resolve"),
                serve("terra"),
                serve("terra", repair=True),
                serve(""),
            ],
        )
        self.assertEqual(
            ("astra_resolve", True),
            stage_script.stage_of(
                {"report_repair": True, "original": {"stage": "astra_resolve"}, "stage": "astra_resolve_report_repair"}
            ),
        )

    def test_a_live_command_is_filled_as_autocode_fills_it(self):
        stage_script = load_stage_script()
        values = {"model": "m", "report": "/r.json", "workspace": "/w"}
        self.assertEqual(
            ["-o", "/r.json", "--model=m", "${HOME}", '{"k":1}', "/w"],
            [
                stage_script.fill(part, values)
                for part in ("-o", "{report}", "--model={model}", "${{HOME}}", '{{"k":1}}', "{workspace}")
            ],
        )

    def test_the_report_slot_keeps_a_live_tools_final_message_form(self):
        # A live run (#1012) asked to write its report under a read-only sandbox: the hybrid command hid -o {report}.
        stage_script = load_stage_script()
        persisting = hybrid.slots(["codex", "exec", "-o", "{report}", "-"])
        self.assertEqual(["{schema}", "-o", "{report}", "{role}"], persisting[4:8])
        self.assertNotIn("-o", hybrid.slots(["claude-stage", "{report}"]))
        self.assertEqual(stage_script.VALUES, hybrid.VALUES)
        with tempfile.TemporaryDirectory() as root:
            route = Path(root) / "route.json"
            route.write_text(json.dumps({"prompt": "file", "trace": str(Path(root) / "trace.jsonl")}))
            values = [part.strip("{}") for part in persisting]
            with patch.object(stage_script, "run", return_value=0) as launched:
                route_json = json.loads(route.read_text())
                route_json["sides"] = {"live": {"command": ["live", "-o", "{report}"]}}
                route.write_text(json.dumps(route_json))
                (Path(root) / "prompt_file").write_text("no handoff")
                values[values.index("prompt_file")] = str(Path(root) / "prompt_file")
                self.assertEqual(0, stage_script.main([str(route), *values]))
        self.assertEqual(["live", "-o", "report"], launched.call_args.args[0])

    def test_the_hybrid_tool_config_reads_back_as_written(self):
        import tomllib

        live = {
            "name": "claude",
            "command": ["claude-stage", "{report}"],
            "prompt": "stdin",
            "output": "report_file",
            "models": ["claude-sonnet-5-5", 'a "quoted" é model'],
            "version_command": ["claude", "--version"],
            "roles": {"astra": {"model": "claude-opus-5-5", "effort": "medium"}},
            "auth": {
                "command": ["tool", "auth", "list"],
                "forbid_env": ["KEY"],
                "routes": [{"models": "openai/", "pattern": "openai: (\\w+)", "expect": "oauth"}],
            },
            "builder_retry": {
                "strong_model": "claude-sonnet-5-5",
                "checker_model": "claude-opus-5-5",
                "strong_effort": "high",
            },
        }
        config = hybrid.tool_config(live, ["python3", "stage.py", "{report}"], "stdin")
        self.assertEqual(
            {
                "name": "hybrid",
                "command": ["python3", "stage.py", "{report}"],
                "prompt": "stdin",
                **{key: live[key] for key in ("models", "version_command", "auth", "roles")},
            }
            | {"builder_retry": live["builder_retry"]},
            config,
        )
        # The live tool's Builder retry policy survives the round trip, so a hybrid Builder escalates as it does.
        self.assertEqual(config, tomllib.loads(hybrid.toml(config)))
        self.assertEqual(live["builder_retry"], tomllib.loads(hybrid.toml(config))["builder_retry"])

    def test_a_live_tool_that_cannot_be_split_by_stage_is_refused(self):
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"XDG_CONFIG_HOME": home}):
            with self.assertRaisesRegex(hybrid.Unavailable, "built-in OpenCode and Codex"):
                hybrid.user_tool("opencode")
            tools = Path(home) / "autocode" / "providers"
            tools.mkdir(parents=True)
            (tools / "kilo.toml").write_text('name = "kilo"\ncommand = ["kilo"]\noutput = "opencode_events"\n')
            with self.assertRaisesRegex(hybrid.Unavailable, "report_file tool"):
                hybrid.user_tool("kilo")

    def test_the_built_in_claude_provider_is_never_split_by_stage(self):
        """#717: a stale user claude.toml would otherwise drive hybrid stages while natural runs use the built-in."""
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"XDG_CONFIG_HOME": home}):
            tools = Path(home) / "autocode" / "providers"
            tools.mkdir(parents=True)
            (tools / "claude.toml").write_text('name = "claude"\ncommand = ["claude-stage", "{report}"]\n')
            with self.assertRaisesRegex(hybrid.Unavailable, "built-in claude provider"):
                hybrid.user_tool("claude")

    def test_a_scenario_without_a_route_is_skipped_and_labeled_hybrid(self):
        with tempfile.TemporaryDirectory(prefix="scenario-test-") as out:
            args = argparse.Namespace(
                fake=True,
                profile=None,
                fake_solution="reference",
                out=Path(out),
                autocode=None,
                max_steps=None,
                timeout_minutes=10,
                hybrid=True,
            )
            result = run.run_one(catalog.load("bugfix-trivial"), args)
            self.assertEqual((verdict.SKIPPED, "fake-hybrid"), (result["verdict"], result["mode"]))
            self.assertIn("no [hybrid] route", result["summary"])
            self.assertFalse((Path(result["evidence"]) / "project").exists())

    def test_a_route_names_each_stage_once(self):
        def run_copy(table):
            with tempfile.TemporaryDirectory() as root:
                shutil.copytree(catalog.CATALOG / "bugfix-trivial", Path(root) / "bugfix-trivial")
                toml = Path(root) / "bugfix-trivial" / "scenario.toml"
                toml.write_text(toml.read_text() + "\n[hybrid]\n" + table)
                with patch.object(catalog, "CATALOG", Path(root)):
                    return catalog.load("bugfix-trivial")

        loaded = run_copy('scripted = ["astra_discovery"]\nfirst_attempt = ["terra"]\n')
        self.assertEqual((("astra_discovery",), ("terra",)), (loaded.hybrid_scripted, loaded.hybrid_first_attempt))
        for table in ('scripted = ["terra"]\nfirst_attempt = ["terra"]\n', "scripted = []\n", 'live = ["sol"]\n'):
            with self.subTest(table=table), self.assertRaises(ValueError):
                run_copy(table)


# A registered tool standing in for a live one (HybridRunTests): it logs the environment it was given, then runs
# the scripted provider of the run whose workspace AutoCode hands it.
LIVE_STANDIN = """import json, os, sys
workspace = os.path.abspath(sys.argv[1])
root = os.path.dirname(workspace)
with open(os.path.join(root, "live-tool.jsonl"), "a") as handle:
    handle.write(json.dumps({"xdg": os.environ.get("XDG_CONFIG_HOME"),
                             "model": sys.argv[sys.argv.index("--model") + 1]}) + "\\n")
os.environ.update(SCENARIO_FAKE_CONFIG=os.path.join(root, "fake-config.json"), SCENARIO_FAKE_SIDE="live")
os.execv(sys.executable, [sys.executable, os.path.join(root, "bin", "codex"), *sys.argv[2:]])
"""


class HybridRunTests(unittest.TestCase):
    """feature-stock-refusals' hybrid route end to end (issue #59), about 25 s each, with no model: planning and
    the first Builder are scripted by the vacuous_refusal_tests fault, every later call goes to the live side,
    which here is the fake provider standing in. They prove the routing, the labels and that the diagnosis
    counts only calls the live side served; nothing about how a model diagnoses."""

    SCRIPTED = ["requirements_gather", "astra_discovery", "astra_challenge", "terra"]
    LIVE = ["sol", "astra_review", "astra_resolve", "investigate_stuck", "terra", "sol", "astra_review"]

    def run_hybrid(self, out, **fields):
        args = argparse.Namespace(
            **{
                "fake": True,
                "profile": None,
                "provider": None,
                "fake_solution": "reference",
                "out": Path(out),
                "autocode": None,
                "max_steps": None,
                "timeout_minutes": 10,
                "hybrid": True,
                **fields,
            }
        )
        result = run.run_one(catalog.load("feature-stock-refusals"), args)
        evidence = Path(result["evidence"])
        state = json.loads((evidence / "state.json").read_text())
        proofs = [(row["source_revision"], row["verdict"]) for row in state["regression_proofs"]]
        trace = [(row["stage"], row["side"]) for row in hybrid.calls(evidence)]
        return result, state, proofs, trace

    def assert_trap_reached_and_only_the_live_resolver_scored(self, result, state, proofs, trace, model):
        self.assertEqual(verdict.PASS, result["verdict"], result["summary"])
        self.assertEqual(
            (self.SCRIPTED, self.LIVE), (result["hybrid"]["scripted_stage_names"], result["hybrid"]["live_stage_names"])
        )
        self.assertEqual(
            [(stage, "scripted") for stage in self.SCRIPTED] + [(stage, "live") for stage in self.LIVE], trace
        )
        # The regression proof failed on the scripted Builder's source: the trap, by construction.
        scripted_build = next(row for row in state["stages"] if row["stage"] == "terra")
        self.assertEqual(
            [(scripted_build["source_revision"], "FAIL")] * 2,
            proofs[:-1],
            "The anti-flake replay must fail again on exactly the scripted trap source",
        )
        self.assertEqual(3, len(proofs))
        self.assertEqual("PASS", proofs[-1][1])
        self.assertNotEqual(scripted_build["source_revision"], proofs[-1][0])
        diagnosis = result["diagnosis"]
        self.assertEqual(
            (verdict.CORRECT, 1, model, 0),
            (
                diagnosis["verdict"],
                diagnosis["resolver_calls_on_trap"],
                diagnosis["model"],
                diagnosis["scripted_resolver_calls"],
            ),
        )
        self.assertEqual([scripted_build["source_revision"][:12]], list(diagnosis["trap_tests"]))
        self.assertTrue(all(row["mode"] == result["mode"] for row in stats.summarize([result])))

    def test_a_rehearsal_scripts_planning_and_the_first_builder_and_scores_only_the_live_resolver(self):
        with tempfile.TemporaryDirectory(prefix="scenario-test-") as out:
            result, state, proofs, trace = self.run_hybrid(out)
            self.assertEqual("fake-hybrid", result["mode"])
            self.assertIn("-feature-stock-refusals-fake-hybrid-", Path(result["evidence"]).name)
            self.assert_trap_reached_and_only_the_live_resolver_scored(result, state, proofs, trace, "standin-resolver")
            # The scripted provider's own witness agrees with the route's trace on every call's side.
            witness = [
                json.loads(line) for line in (Path(result["evidence"]) / "fake-calls.jsonl").read_text().splitlines()
            ]
            self.assertEqual(trace, [(row["stage"], row["side"]) for row in witness])
            self.assertEqual("hybrid", state["settings"]["provider"])

    def test_a_live_profile_runs_its_own_tool_for_every_unscripted_call_with_its_own_environment(self):
        with (
            tempfile.TemporaryDirectory(prefix="scenario-test-") as out,
            tempfile.TemporaryDirectory(prefix="config-home-") as home,
        ):
            tools = Path(home) / "autocode" / "providers"
            tools.mkdir(parents=True)
            (Path(home) / "live_standin.py").write_text(LIVE_STANDIN)
            models = {role: f"live-{role}" for role in profiles.ROLES}
            retry = {"strong_model": "live-strong", "checker_model": "live-completion"}
            (tools / "livestandin.toml").write_text(
                hybrid.toml(
                    {
                        "name": "livestandin",
                        "prompt": "stdin",
                        "models": sorted([*models.values(), "live-strong"]),
                        "builder_retry": retry,
                        "command": [
                            sys.executable,
                            str(Path(home) / "live_standin.py"),
                            "{workspace}",
                            "exec",
                            "--model",
                            "{model}",
                            "--output-schema",
                            "{schema}",
                            "-o",
                            "{report}",
                        ],
                        "version_command": [sys.executable, "--version"],
                        "roles": {
                            tool: {"model": models[role], "effort": "medium"}
                            for tool, role in hybrid.TOOL_ROLES.items()
                        },
                    }
                )
            )
            profile = {"provider": "livestandin", "models": models}
            with (
                patch.dict(profiles.PROFILES, {"live-standin": profile}),
                patch.dict(os.environ, {"XDG_CONFIG_HOME": home}),
            ):
                result, state, proofs, trace = self.run_hybrid(out, fake=False, profile="live-standin")
            self.assertEqual(
                ("live-standin-hybrid", "livestandin", "livestandin"),
                (result["mode"], result["profile"]["provider"], result["hybrid"]["live_tool"]),
            )
            self.assert_trap_reached_and_only_the_live_resolver_scored(result, state, proofs, trace, "live-resolver")
            # AutoCode ran on the hybrid tool; the live tool got exactly the unscripted calls, each with the
            # profile's model and the environment the harness started from (XDG_CONFIG_HOME restored).
            self.assertEqual("hybrid", state["settings"]["provider"])
            served = [
                json.loads(line) for line in (Path(result["evidence"]) / "live-tool.jsonl").read_text().splitlines()
            ]
            self.assertEqual(
                [
                    models[role]
                    for role in (
                        "validator",
                        "completion",
                        "resolver",
                        "reviewer",
                        "builder",
                        "validator",
                        "completion",
                    )
                ],
                [row["model"] for row in served],
            )
            self.assertEqual({home}, {row["xdg"] for row in served})
            # The hybrid tool AutoCode ran on kept the live tool's Builder retry policy.
            import tomllib

            written = Path(result["evidence"]) / "hybrid" / "config" / "autocode" / "providers" / "hybrid.toml"
            self.assertEqual(retry, tomllib.loads(written.read_text())["builder_retry"])
            self.assertEqual("live-strong", state["settings"]["builder_retry"]["strong_model"])


class StockRefusalsProductTests(unittest.TestCase):
    """feature-stock-refusals' product check on delivered tests: each refusal rule needs a test that fails on the
    original code; an extra valid test that argparse's own refusal also passes does not fail the product."""

    USAGE_TEST = """
    def test_move_with_missing_arguments_is_a_usage_error(self):
        self.write_store({"A1": {"bolt": 1}})
        before = self.store_bytes()
        result = self.run_cli("move", "bolt", "1")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.store_bytes(), before)
"""

    def evaluate(self, edit, solution="reference"):
        from harness.project import materialize

        scenario = catalog.load("feature-stock-refusals")
        with tempfile.TemporaryDirectory() as root:
            project = materialize(scenario.seed, Path(root) / "p", scenario.dir / solution)
            tests = project / "tests" / "test_stock.py"
            tests.write_text(edit(tests.read_text()))
            return {check.name: check for check in verdict.evaluate(scenario, project).checks}

    # The names a live run (2026-10-05, 8soi9a5s) gave its shortage tests; the oracle scored that correct
    # product 5/6 because "shortage" was not a word for "more than held".
    LIVE_SHORTAGE_NAMES = {
        "test_c3_move_more_than_on_hand_is_refused": "test_ac5_move_refuses_shortage_and_malformed_store",
        "test_c7_remove_more_than_on_hand_is_refused": "test_ac4_remove_refuses_bad_qty_and_shortage",
    }

    def rename_shortage_tests(self, text):
        for old, new in self.LIVE_SHORTAGE_NAMES.items():
            text = text.replace(f"def {old}(", f"def {new}(")
        return text

    def test_shortage_tests_named_as_a_live_run_named_them_cover_more_than_held(self):
        checks = self.evaluate(self.rename_shortage_tests)
        self.assertTrue(all(check.ok for check in checks.values()), checks)
        oracle = catalog.load("feature-stock-refusals")._oracle_module()
        for name in self.LIVE_SHORTAGE_NAMES.values():
            function = ast.parse(f"def {name}(self):\n    pass\n").body[0]
            self.assertIn("more than held", oracle.refusal_rules(function), name)
        # A phrase counts only as a phrase: "too many" in a row, not "too" and "many" apart.
        phrase = ast.parse("def test_move_refuses_too_many(self):\n    pass\n").body[0]
        apart = ast.parse("def test_move_refuses_many_units_too(self):\n    pass\n").body[0]
        self.assertEqual({"more than held"}, oracle.refusal_rules(phrase))
        self.assertEqual(set(), oracle.refusal_rules(apart))

    # The helpers seed, reference and both broken variants shared until 2026-10-05. In the six hybrid live runs
    # the Validators and Completion Owners found that they crash on a QTY of a superscript two or of 5000 digits
    # (a traceback, exit 1), read an Arabic-Indic three as 3 and take a JSON true in stock.json for 1, and every
    # Resolver rightly put stock.py in scope.
    OLD_HELPERS = """def quantity(text):
    if not text.isdigit() or int(text) <= 0:
        raise Refused(f"quantity must be a positive integer, got {text!r}")
    return int(text)
"""

    def test_the_hidden_tests_fail_the_helpers_the_live_checkers_found_wrong(self):
        from harness.project import materialize

        scenario = catalog.load("feature-stock-refusals")
        with tempfile.TemporaryDirectory() as root:
            project = materialize(scenario.seed, Path(root) / "p", scenario.dir / "reference")
            source = project / "stock.py"
            text = source.read_text()
            start, end = text.index("def quantity(text):"), text.index("def cmd_receive")
            source.write_text(
                (text[:start] + self.OLD_HELPERS + "\n\n" + text[end:]).replace("type(q) is int", "isinstance(q, int)")
            )
            shutil.copytree(scenario.dir / "hidden", project / "hidden_checks")
            failing = {}
            for case in (
                "Move",
                "Remove",
                "RefusesQuantityThatIsNotPositive",
                "RefusesMoveToSameLocation",
                "RefusesTakingMoreThanHeld",
                "RefusesMalformedStore",
            ):
                proc = subprocess.run(
                    [sys.executable, "-m", "unittest", "-v", f"hidden_checks.test_stock_hidden.{case}"],
                    cwd=project,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                )
                if proc.returncode:
                    failing[case] = sorted(set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", proc.stderr, re.M)))
        self.assertEqual(
            {
                "RefusesQuantityThatIsNotPositive": [
                    "test_more_digits_than_int_reads_are_refused",
                    "test_non_ascii_digits_are_refused",
                ],
                "RefusesMalformedStore": ["test_refused_by_both_commands"],
            },
            failing,
        )

    def test_shortage_tests_that_pass_on_the_original_code_still_fail_the_product(self):
        check = self.evaluate(self.rename_shortage_tests, "broken/vacuous-refusal-tests")[
            "new_command_tests_fail_on_original_code"
        ]
        self.assertFalse(check.ok)
        for name in self.LIVE_SHORTAGE_NAMES.values():
            self.assertIn(name, check.detail)

    def test_an_extra_test_that_passes_on_the_original_code_is_reported_not_failed(self):
        checks = self.evaluate(lambda text: text.replace("\n\nif __name__", self.USAGE_TEST + "\n\nif __name__"))
        self.assertTrue(all(check.ok for check in checks.values()), checks)
        self.assertIn(
            "test_move_with_missing_arguments_is_a_usage_error",
            checks["new_command_tests_fail_on_original_code"].detail,
        )

    def test_a_rule_whose_only_test_passes_on_the_original_code_fails(self):
        def vacuous_same_location(text):
            start = text.index("def test_c4_")
            end = text.index("self.assert_refused(result, before)", start)
            return (
                text[:end] + "self.assertEqual(result.returncode, 2)\n        "
                "self.assertEqual(self.store_bytes(), before)"
                + text[end + len("self.assert_refused(result, before)") :]
            )

        check = self.evaluate(vacuous_same_location)["new_command_tests_fail_on_original_code"]
        self.assertFalse(check.ok)
        self.assertIn("FROM equal to TO", check.detail)


# A planned case whose test passes on the original code but is not about move or remove (a receive case the
# Planner tagged test: instead of guard:), and a move case planned with an exact test name.
EXTRA_STOCK_TESTS = """from tests.test_stock import StockCase


class ExtraTests(StockCase):
    def test_c9_receive_zero_still_refused(self):
        self.write_store({"A1": {"bolt": 3}})
        self.assertEqual(self.run_cli("receive", "bolt", "0", "A1").returncode, 2)

    def test_move_refuses_overdraw(self):
        self.write_store({"A1": {"bolt": 1}})
        self.assertEqual(self.run_cli("move", "bolt", "2", "A1", "B2").returncode, 2)
"""


def maintenance_children(project, *args, environment=None):
    """The maintenance or gc processes started while ``git args`` runs in ``project`` (or, with no args,
    while the ``environment`` context runs), read from GIT_TRACE2_EVENT."""
    from harness.project import git

    with tempfile.TemporaryDirectory() as temp:
        trace = Path(temp) / "trace2.json"
        with patch.dict(os.environ, {"GIT_TRACE2_EVENT": str(trace)}):
            if args:
                git(project, *args)
            else:
                environment()
        children = [
            event["argv"]
            for event in map(json.loads, trace.read_text().splitlines())
            if event.get("event") == "child_start"
        ]
    return [argv for argv in children if {"maintenance", "gc"} & set(argv)]


class FixtureMaintenanceTests(unittest.TestCase):
    """A fixture copied or deleted while Git's detached maintenance repacks it fails on CI's Git 2.55
    (docs/bugs/git-background-repack-cleanup-race.md; master run 37349094592)."""

    def test_the_seed_commit_starts_no_background_maintenance(self):
        from harness.project import materialize

        scenario = catalog.load("feature-stock-refusals")
        with tempfile.TemporaryDirectory() as root:
            project = Path(root) / "project"
            self.assertEqual(
                [],
                maintenance_children(
                    project, environment=lambda: materialize(scenario.seed, project, scenario.reference)
                ),
            )
            self.assertEqual(
                "",
                subprocess.run(
                    ["git", "-C", str(project), "config", "--local", "maintenance.auto"], capture_output=True, text=True
                ).stdout,
            )

    def test_a_commit_in_a_diagnosis_fixture_starts_no_background_maintenance(self):
        from harness.project import git

        for fixture in (StockRefusalsDiagnosisTests, RefundWindowDiagnosisTests):
            with self.subTest(fixture.__name__):
                fixture.setUpClass()
                self.addCleanup(fixture.tearDownClass)
                (fixture.fixture / "extra.txt").write_text("extra\n")
                git(fixture.fixture, "add", "extra.txt")
                self.assertEqual([], maintenance_children(fixture.fixture, "commit", "-q", "-m", "extra"))


class StockRefusalsDiagnosisTests(unittest.TestCase):
    """feature-stock-refusals' diagnosis() on synthetic run records (issue #59): which Resolver calls count,
    which tests are the trap, and which diagnoses the word lists must not pass or fail. The trap source is a
    Git commit the runner's code checkpoint points to, in a fixture built once and copied per test."""

    VACUOUS = "tests.test_stock.MoveRemoveTests.test_c3_move_more_than_on_hand_is_refused"
    RECEIVE = "tests.test_extra.ExtraTests.test_c9_receive_zero_still_refused"
    EXACT = "tests.test_extra.ExtraTests.test_move_refuses_overdraw"
    GOOD = {
        "status": "REWORK",
        "diagnosis": "test_c3_move_more_than_on_hand_is_refused passes on the original code too: there `move` is "
        "an unknown subcommand, so argparse exits 2 (invalid choice) and never writes the store.",
        "next_objective": "Make the refusal tests fail on the original code",
        "next_task": {
            "kind": "implement",
            "requirements": [
                "In tests/test_stock.py assert that stderr starts with 'stock.py: ' and has no 'invalid choice'"
            ],
        },
    }
    MISATTRIBUTED = {
        **GOOD,
        "diagnosis": "stock.py move does not validate its quantity.",
        "next_task": {"kind": "implement", "requirements": ["Fix stock.py"]},
    }

    @classmethod
    def setUpClass(cls):
        from harness.project import git, materialize, without_maintenance

        cls.fixture_root = tempfile.mkdtemp(prefix="stock-diagnosis-")
        scenario = catalog.load("feature-stock-refusals")
        project = without_maintenance(
            materialize(
                scenario.seed, Path(cls.fixture_root) / "project", scenario.dir / "broken" / "vacuous-refusal-tests"
            )
        )
        (project / "tests" / "test_extra.py").write_text(EXTRA_STOCK_TESTS)
        git(project, "add", "-A")
        git(project, "commit", "-q", "-m", "trap source")
        cls.trap_commit = git(project, "rev-parse", "HEAD").strip()
        cls.fixture = project

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.fixture_root, ignore_errors=True)

    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.project = Path(root.name) / "project"
        shutil.copytree(self.fixture, self.project, symlinks=True)
        self.run_dir = self.project / ".autocode" / "runs" / "20261005-000000-stock"
        self.run_dir.mkdir(parents=True)
        self.state = {
            "regression_proofs": [],
            "stages": [],
            "code_checkpoints": [{"source_revision": "trap", "commit": self.trap_commit, "available": True}],
        }
        self.clock = 0

    def tick(self):
        self.clock += 1
        return f"2026-10-05T00:{self.clock // 60:02d}:{self.clock % 60:02d}+00:00"

    def save(self, name, value):
        path = self.run_dir / name
        path.write_text(json.dumps(value))
        return str(path)

    def proof(self, revision, verdict_, **fields):
        path = self.save(f"proof-{len(self.state['regression_proofs']) + 1}.json", {"verdict": verdict_, **fields})
        self.state["regression_proofs"].append(
            {"source_revision": revision, "verdict": verdict_, "proved_at": self.tick(), "path": path}
        )

    @staticmethod
    def failure(case, name):
        """The runner's own wording (autocode_regression.check_cases)."""
        return (
            f"Test case {case}: a planned case has no test named {name} that passes with the change and did "
            "not pass without it"
        )

    def trap(self, revision="trap", cases=None):
        """A FAIL proof whose planned cases' tests passed on the original code too."""
        cases = cases or {"C3": (self.VACUOUS, "test_c3_move_more_than_on_hand_is_refused")}
        self.proof(
            revision,
            "FAIL",
            pass_to_pass=[test for test, _ in cases.values()],
            fail_to_pass=[],
            test_files=["tests/test_stock.py", "tests/test_extra.py"],
            failures=[self.failure(case, name) for case, (_, name) in cases.items()],
        )

    def stage(self, stage="astra_resolve", revision="trap", report=None, model="resolver-model", **fields):
        name = f"{stage}-{len(self.state['stages']) + 1}.json"
        output = self.save(name, report) if report is not None else str(self.run_dir / name)
        row = {
            "stage": stage,
            "source_revision": revision,
            "output": output,
            "launch_route": {"model": model},
            "runner_calls": 1,
            "changed_files": [],
            "finished_at": self.tick(),
            **fields,
        }
        self.state["stages"].append(row)
        return row

    def repaired(self, revision="fixed", flipped=(VACUOUS,), verdict_="PASS"):
        self.stage("terra", revision=revision, report={}, changed_files=["tests/test_stock.py"])
        self.proof(revision, verdict_, pass_to_pass=[], fail_to_pass=list(flipped), failures=[])

    def diagnose(self, scripted=None):
        (self.run_dir / "state.json").write_text(json.dumps(self.state))
        run_ = {"model_stages": [], **({"scripted_outputs": scripted} if scripted is not None else {})}
        return verdict.diagnose(catalog.load("feature-stock-refusals"), self.project, run_)

    def failing(self, block):
        return {check["name"] for check in block["checks"] if not check["ok"]}

    def test_a_resolver_call_the_scripted_side_of_a_hybrid_run_answered_never_counts(self):
        # Both calls were launched with a model name; only the run record says the first was scripted.
        self.trap()
        scripted = self.stage(report=self.GOOD)
        live = self.stage(report=self.MISATTRIBUTED)
        self.repaired()
        natural = self.diagnose()
        self.assertEqual((verdict.CORRECT, 2), (natural["verdict"], natural["resolver_calls_on_trap"]))
        hybrid_ = self.diagnose(scripted=[scripted["output"]])
        self.assertEqual(
            (verdict.INCORRECT, 1, 1),
            (hybrid_["verdict"], hybrid_["resolver_calls_on_trap"], hybrid_["scripted_resolver_calls"]),
        )
        self.assertEqual([live["output"]], [call["output"] for call in hybrid_["trap_calls"]])
        # Matched by the report path AutoCode gave the tool, whatever its suffix in the row.
        only_scripted = self.diagnose(scripted=[scripted["output"], live["output"].removesuffix(".json") + ".jsonl"])
        self.assertEqual(verdict.NOT_EXERCISED, only_scripted["verdict"])
        self.assertIn("scripted calls of a hybrid run do not count", only_scripted["reason"])
        self.assertEqual(2, only_scripted["scripted_resolver_calls"])

    def test_the_scored_call_is_the_first_accepted_one_and_unsaved_or_runner_calls_never_count(self):
        self.trap()
        self.stage(report=self.GOOD, runner_owned=True)
        self.stage(model="")  # never launched with a model
        self.stage(exit_code=-9, timed_out=True)  # crashed: no report saved
        self.stage(report={**self.GOOD, "status": "BLOCKED"}, rejected=True, rejection_reason="schema")
        accepted = self.stage(report=self.GOOD)
        self.repaired()
        block = self.diagnose()
        self.assertEqual(verdict.CORRECT, block["verdict"], block["reason"])
        self.assertEqual(3, block["resolver_calls_on_trap"])
        self.assertEqual(["no report was saved"], [call["why"] for call in block["unscorable_calls"]])
        self.assertEqual([accepted["output"]], [call["output"] for call in block["trap_calls"] if call["scored"]])
        self.assertEqual([False, True], [call["accepted"] for call in block["trap_calls"]])

    def test_a_rejected_report_alone_is_scored_and_not_accepted(self):
        self.trap()
        self.stage(report=self.GOOD, rejected=True, rejection_reason="Report-file providers require capture receipts")
        self.repaired()
        block = self.diagnose()
        self.assertEqual(verdict.INCORRECT, block["verdict"])
        self.assertEqual({"diagnosis_accepted"}, self.failing(block))

    def test_a_report_accepted_after_a_report_repair_is_scored_on_the_repaired_report(self):
        # The runner archives the rejected draft as an astra_resolve row, and the accepted report-only repair
        # replaces the applied record (autocode.accept_repaired_report); resolution_history names its output.
        self.trap()
        self.stage(report={**self.GOOD, "evidence": []}, rejected=True, rejection_reason="missing evidence")
        repair = self.stage(
            "astra_resolve_report_repair",
            report=self.GOOD,
            report_only=True,
            original_stage="astra_resolve",
            model="repair-model",
        )
        self.state["resolution_history"] = [{"output": repair["output"], "diagnosis": self.GOOD["diagnosis"]}]
        self.repaired()
        block = self.diagnose()
        self.assertEqual(verdict.CORRECT, block["verdict"], block["reason"])
        self.assertEqual(1, block["resolver_calls_on_trap"])
        (call,) = block["trap_calls"]
        self.assertEqual((True, True, repair["output"]), (call["accepted"], call["report_repaired"], call["output"]))
        self.assertIn("report-only repair", block["checks"][0]["detail"])
        # A repair the runner rejected too leaves the call rejected.
        self.state["stages"][1]["rejected"] = True
        self.assertEqual({"diagnosis_accepted"}, self.failing(self.diagnose()))
        # A repair still running when the run stopped has not decided it either way.
        repair_row = self.state["stages"].pop(1)
        self.state["stages"] = self.state["stages"][:1]
        self.state["active_stage"] = {**repair_row, "rejected": False}
        block = self.diagnose()
        self.assertEqual(verdict.UNSCORED, block["verdict"], block["reason"])
        self.assertIn("report repair was still running", block["unscorable_calls"][0]["why"])

    def test_a_run_that_stops_before_the_next_build_is_proved_leaves_the_call_unscored(self):
        self.trap()
        self.stage(report=self.GOOD)
        block = self.diagnose()
        self.assertEqual(verdict.UNSCORED, block["verdict"], block["reason"])
        self.assertIn("repair_made_the_tests_discriminate", block["reason"])
        self.assertEqual(set(), self.failing(block))
        # A required check that already failed decides it.
        self.state["stages"].clear()
        self.stage(report=self.MISATTRIBUTED)
        self.assertEqual(verdict.INCORRECT, self.diagnose()["verdict"])

    def test_the_next_proof_is_the_one_of_the_build_after_the_call(self):
        self.trap()
        self.stage(report=self.GOOD)
        # The next build's report was rejected and then repaired; its proof passed with the test discriminating.
        self.stage("terra", revision="fixed", report={}, rejected=True, changed_files=["tests/test_stock.py"])
        self.stage("terra_report_repair", revision="fixed", report={}, report_only=True, original_stage="terra")
        self.proof("fixed", "PASS", pass_to_pass=[], fail_to_pass=[self.VACUOUS], failures=[])
        # A later, unrelated build whose proof failed is not this call's.
        self.stage("terra", revision="later", report={}, changed_files=["README.md"])
        self.proof("later", "FAIL", pass_to_pass=[self.VACUOUS], fail_to_pass=[], failures=["unrelated"])
        block = self.diagnose()
        self.assertEqual(verdict.CORRECT, block["verdict"], block["reason"])

    def test_a_repair_that_replaces_the_vacuous_test_is_judged_by_the_runners_case_match(self):
        # The Builder replaced the vacuous test with a discriminating one under a new name; the runner matched
        # case C3 to it (case_tests) and the proof passed. Deleting the old test in the same sentence that
        # replaces it is not weakening.
        self.trap(cases={"C3": (self.VACUOUS, "test_c3_<what it checks>")})
        new = "tests.test_stock.MoveRemoveTests.test_c3_move_overdraw_refused_by_the_command"
        self.stage(
            report={
                **self.GOOD,
                "next_objective": "Replace each vacuous refusal test with one that asserts the "
                "command's own error message, and delete the old tests",
            }
        )
        self.stage("terra", revision="fixed", report={}, changed_files=["tests/test_stock.py"])
        self.proof("fixed", "PASS", pass_to_pass=[], fail_to_pass=[new], failures=[], case_tests={"C3": [new]})
        block = self.diagnose()
        self.assertEqual(verdict.CORRECT, block["verdict"], block["reason"])
        # A case the runner still found no discriminating test for fails, whatever else flipped.
        self.state["regression_proofs"][-1]["path"] = self.save(
            "proof-unmatched.json",
            {"verdict": "FAIL", "pass_to_pass": [], "fail_to_pass": [new], "failures": [], "case_tests": {"C3": []}},
        )
        self.assertEqual({"repair_made_the_tests_discriminate"}, self.failing(self.diagnose()))

    def test_only_planned_tests_about_move_or_remove_are_the_trap(self):
        receive = {"C9": (self.RECEIVE, "test_c9_receive_zero_still_refused")}
        self.trap(cases=receive)
        self.stage(report=self.GOOD)
        block = self.diagnose()
        self.assertEqual(verdict.NOT_EXERCISED, block["verdict"], block["reason"])
        # With a vacuous move test in the same proof, only that one is the trap: retagging the receive case
        # guard: is the right fix for it, while retagging the move case guard: would weaken the proof.
        self.state["regression_proofs"].clear()
        self.state["stages"].clear()
        self.trap(cases={"C3": (self.VACUOUS, "test_c3_move_more_than_on_hand_is_refused"), **receive})
        retag = {
            **self.GOOD,
            "next_task": {
                **self.GOOD["next_task"],
                "requirements": [
                    *self.GOOD["next_task"]["requirements"],
                    "C9 describes behavior that already worked: make it a guard: test_c9_receive_zero_still_refused case",
                ],
            },
        }
        self.stage(report=retag)
        self.repaired(flipped=(self.VACUOUS,), verdict_="FAIL")
        block = self.diagnose()
        self.assertEqual(["test_c3_move_more_than_on_hand_is_refused"], block["vacuous_tests"])
        self.assertEqual(verdict.CORRECT, block["verdict"], block["reason"])
        self.state["stages"][0]["output"] = self.save(
            "weakening.json", {**self.GOOD, "next_objective": "Retag C3 as a guard: case so the proof passes"}
        )
        self.assertEqual({"repair_does_not_weaken_tests"}, self.failing(self.diagnose()))
        # Without the source to read, every planned test that passed on the original code counts, and the
        # block says so.
        self.state["code_checkpoints"].clear()
        block = self.diagnose()
        self.assertEqual(
            sorted(["test_c3_move_more_than_on_hand_is_refused", "test_c9_receive_zero_still_refused"]),
            block["vacuous_tests"],
        )
        self.assertIn("source not rebuilt", block["trap_tests_read_from"]["trap"])

    def test_a_planned_case_is_matched_to_its_test_as_the_runner_matches_it(self):
        # An approved exact test name, whatever it is called; else the case id's words in the test name.
        self.trap(
            cases={"C8": (self.EXACT, "test_move_refuses_overdraw"), "C3": (self.VACUOUS, "test_c3_<what it checks>")}
        )
        self.proof(
            "other",
            "FAIL",
            pass_to_pass=["tests.test_stock.T.test_c30_move_more"],
            fail_to_pass=[],
            failures=[self.failure("C3", "test_c3_<what it checks>")],
        )
        block = self.diagnose()
        self.assertEqual(
            {"trap": ["test_c3_move_more_than_on_hand_is_refused", "test_move_refuses_overdraw"]}, block["trap_tests"]
        )

    def test_a_resolver_the_runner_never_applied_is_unscored_even_with_a_report_on_disk(self):
        self.trap()
        self.state["active_stage"] = {
            "stage": "astra_resolve",
            "source_revision": "trap",
            "runner_calls": 1,
            "output": self.save("resolver-01.json", self.GOOD),
            "launch_route": {"model": "resolver-model"},
            "started_at": self.tick(),
        }
        block = self.diagnose()
        self.assertEqual(verdict.UNSCORED, block["verdict"], block["reason"])
        self.assertEqual(
            ["the run stopped before the runner applied it"], [call["why"] for call in block["unscorable_calls"]]
        )

    def test_a_resolver_at_a_revision_without_the_trap_is_not_exercised_and_kept_for_a_human(self):
        self.proof("other", "PASS", pass_to_pass=[], fail_to_pass=[self.VACUOUS], failures=[])
        self.stage(revision="other", report={**self.GOOD, "diagnosis": "An unrelated defect in load()."})
        block = self.diagnose()
        self.assertEqual(verdict.NOT_EXERCISED, block["verdict"])
        self.assertEqual(
            ["An unrelated defect in load()."], [call["diagnosis"] for call in block["other_resolver_calls"]]
        )
        self.trap("trapped-later")
        block = self.diagnose()
        self.assertEqual(verdict.NOT_EXERCISED, block["verdict"])
        self.assertIn("never ran at that revision", block["reason"])

    def test_a_killed_resolver_at_the_trap_is_unscored(self):
        self.trap()
        before = self.save("resolver-01.before.json", {"revision": "trap"})
        self.state["active_stage"] = {
            "stage": "astra_resolve",
            "output": str(self.run_dir / "resolver-01.json"),
            "before_ref": before,
            "launch_route": {"model": "resolver-model"},
            "runner_calls": 1,
            "started_at": self.tick(),
        }
        self.assertEqual(verdict.UNSCORED, self.diagnose()["verdict"])

    def test_astra_diagnose_and_investigator_calls_are_not_autoresolver_diagnoses(self):
        self.trap()
        for stage in ("astra_diagnose", "investigate_stuck", "astra_resolve_report_repair"):
            self.stage(stage, report=self.GOOD)
        self.assertEqual(verdict.NOT_EXERCISED, self.diagnose()["verdict"])

    def test_check_mode_a_run_without_state_and_a_run_whose_proofs_never_failed_are_not_exercised(self):
        scenario = catalog.load("feature-stock-refusals")
        self.assertEqual(verdict.NOT_EXERCISED, verdict.diagnose(scenario, self.project, None)["verdict"])
        self.assertEqual(verdict.NOT_EXERCISED, verdict.diagnose(scenario, self.project, {})["verdict"])
        self.stage(report=self.GOOD)
        self.assertEqual(verdict.NOT_EXERCISED, self.diagnose()["verdict"])

    def score(self, report):
        self.state["stages"].clear()
        self.state["regression_proofs"].clear()
        self.trap()
        self.stage(report=report)
        self.repaired()
        return self.diagnose()

    def test_wrong_diagnoses_are_not_scored_correct(self):
        # Round-1 review probes: words found inside other words, generic causes, and a "repair" that changes
        # the product's exit-code contract.
        name = "test_c3_move_more_than_on_hand_is_refused"
        wrong = {
            "based on": (
                {
                    **self.GOOD,
                    "diagnosis": f"{name} is unreliable based on its fixture: location B2 does not "
                    "exist in the store it writes, so move returns nonzero.",
                },
                "diagnosis_explains_why_they_pass_on_original_code",
            ),
            "a missing store": (
                {
                    **self.GOOD,
                    "diagnosis": f"{name} passes because stock.json does not exist in the "
                    "test's temp dir, so the original stock.py exits 2.",
                },
                "diagnosis_explains_why_they_pass_on_original_code",
            ),
            "codebase": (
                {
                    **self.GOOD,
                    "diagnosis": f"{name} is flaky in this codebase: argparse exits 2 when the "
                    "database file is locked.",
                },
                "diagnosis_explains_why_they_pass_on_original_code",
            ),
            "exit-code contract": (
                {
                    **self.GOOD,
                    "next_task": {
                        "kind": "implement",
                        "requirements": [
                            "In stock.py make move and remove refusals exit 3",
                            "Update tests/test_stock.py to expect exit 3",
                        ],
                    },
                },
                "resolver_chose_bounded_test_repair",
            ),
            "product edit": (
                {
                    **self.GOOD,
                    "next_objective": "Change stock.py so refusals print usage first",
                    "next_task": {"kind": "implement", "requirements": ["Update tests/test_stock.py"]},
                },
                "resolver_chose_bounded_test_repair",
            ),
            "weakening": (
                {**self.GOOD, "next_objective": "Skip these refusal tests until move is stable"},
                "repair_does_not_weaken_tests",
            ),
            "deletion": (
                {**self.GOOD, "next_objective": "Delete the vacuous refusal tests from tests/test_stock.py"},
                "repair_does_not_weaken_tests",
            ),
            "product in affected_paths": (
                {**self.GOOD, "affected_paths": ["stock.py", "tests/test_stock.py"]},
                "resolver_chose_bounded_test_repair",
            ),
            "argparse, another cause": (
                {
                    **self.GOOD,
                    "diagnosis": f"{name} passes on the original code because "
                    "argparse exits 2 on the malformed quantity.",
                },
                "diagnosis_explains_why_they_pass_on_original_code",
            ),
            "argparse and move, another cause": (
                {
                    **self.GOOD,
                    "diagnosis": f"{name} passes on the original code because "
                    "argparse rejects the non-integer quantity given to move with exit 2.",
                },
                "diagnosis_explains_why_they_pass_on_original_code",
            ),
            "argparse choices": (
                {
                    **self.GOOD,
                    "diagnosis": f"{name} passes on the original code: the location choices "
                    "are validated by argparse, which exits 2 for B2, so the "
                    "store is never touched.",
                },
                "diagnosis_explains_why_they_pass_on_original_code",
            ),
            "a command without a store": (
                {
                    **self.GOOD,
                    "diagnosis": f"{name} passes on the original code: the move "
                    "command finds no stock.json and argparse exits 2.",
                },
                "diagnosis_explains_why_they_pass_on_original_code",
            ),
            "unnamed retag": (
                {**self.GOOD, "next_objective": "Retag the refusal cases as guard: cases"},
                "repair_does_not_weaken_tests",
            ),
            "handler edit": (
                {**self.GOOD, "next_objective": "Rewrite the move handler so it refuses before argparse"},
                "resolver_chose_bounded_test_repair",
            ),
            # Live 2026-10-05 (hybrid runs) tasks that also edit the product, with only the test file in
            # affected_paths so the words alone must be caught; since then the scripted stock.py has no defect.
            "live 8tyeg13j/tvd91ipp: a stock.py function must refuse": (
                {
                    **self.GOOD,
                    "affected_paths": ["tests/test_stock.py"],
                    "next_task": {
                        "kind": "implement",
                        "requirements": [
                            self.GOOD["next_task"]["requirements"][0],
                            "stock.py quantity() must raise Refused (exit 2, stderr message, stock.json "
                            "unchanged) for any QTY that is not an ASCII-decimal positive integer",
                        ],
                    },
                },
                "resolver_chose_bounded_test_repair",
            ),
            "live 7ldxdtl5: in a stock.py function, refuse": (
                {
                    **self.GOOD,
                    "affected_paths": ["tests/test_stock.py"],
                    "next_task": {
                        "kind": "implement",
                        "requirements": [
                            self.GOOD["next_task"]["requirements"][0],
                            "In stock.py quantity(), refuse with Refused (exit 2) any QTY that is not a positive "
                            "ASCII integer, including '\u00b2', '-1' and 'x'.",
                            "In stock.py load(), treat boolean quantity values in stock.json as malformed (exit 2).",
                        ],
                    },
                },
                "resolver_chose_bounded_test_repair",
            ),
        }
        for label, (report, check) in wrong.items():
            with self.subTest(label):
                block = self.score(report)
                self.assertEqual(verdict.INCORRECT, block["verdict"], block["reason"])
                self.assertEqual({check}, self.failing(block))

    def test_right_diagnoses_are_not_scored_incorrect(self):
        name = "test_c3_move_more_than_on_hand_is_refused"
        right = {
            "negated weakening": {
                **self.GOOD,
                "next_objective": "Strengthen the refusal tests; do not skip them or "
                "relax any assertion, and do not weaken tests",
            },
            "pre-change": {
                **self.GOOD,
                "next_objective": "Make each refusal test assert where the refusal comes from",
                "diagnosis": f"Against the pre-change stock.py, {name} also passes: argparse rejects "
                "`move` with exit code 2 (invalid choice).",
            },
            "product left alone": {
                **self.GOOD,
                "next_objective": "Leave stock.py unchanged; it is correct. Do not "
                "change stock.py, only tests/test_stock.py",
            },
            "short test name": {
                **self.GOOD,
                "diagnosis": "test_c3 passes on the base revision as well: the move "
                "subcommand does not exist there, so the CLI exits with "
                "status 2.",
            },
            "move tests named": {**self.GOOD, "next_objective": "Update the move command tests in tests/test_stock.py"},
            "tests called too relaxed": {
                **self.GOOD,
                "diagnosis": self.GOOD["diagnosis"] + " Its assertions are too relaxed and the proof weakened nothing.",
            },
            "guard as a verb": {
                **self.GOOD,
                "next_objective": "Guard against argparse's own exit 2 by asserting the stderr prefix",
            },
            "test file named in affected_paths": {
                **self.GOOD,
                "affected_paths": ["tests/test_stock.py"],
                "next_task": {
                    "kind": "implement",
                    "requirements": [
                        f"In test_stock.py, make {name} assert stderr starts with "
                        "'stock.py: ' and has no 'invalid choice'"
                    ],
                },
            },
            "the exit status kept": {
                **self.GOOD,
                "next_objective": "Do not touch the product. Update "
                "tests/test_stock.py; stock.py should exit 2 as it "
                "already does",
            },
            "not implemented": {
                **self.GOOD,
                "diagnosis": f"{name} also passes on the original code: move is not "
                "implemented there, so stock.py exits 2 and leaves "
                "stock.json alone.",
            },
            "no subparser": {
                **self.GOOD,
                "diagnosis": f"{name} also passes on the original code: the original parser "
                "has no move subparser, so parsing fails with exit code 2 and "
                "the store is untouched.",
            },
            "unknown verb": {
                **self.GOOD,
                "diagnosis": f"{name} passes on the original code because the CLI parser "
                "rejects the unknown `move` verb with exit code 2 before touching "
                "stock.json.",
            },
            "returns 2": {
                **self.GOOD,
                "diagnosis": f"{name} passes on the original code, where `move` is an unknown "
                "subcommand and main() returns 2.",
            },
            "SystemExit(2)": {
                **self.GOOD,
                "diagnosis": f"{name} passes on the original code, where argparse raises "
                "SystemExit(2) for the unknown `move` command.",
            },
            "code 2": {
                **self.GOOD,
                "diagnosis": f"{name} passes on the original code: `move` is an invalid choice "
                "there and the CLI ends with code 2.",
            },
            # Live 2026-10-05 (hybrid runs): the part of each Opus diagnosis and task about the vacuous tests.
            "live 8tyeg13j: unknown 'move'/'remove' subcommand": {
                **self.GOOD,
                "affected_paths": ["tests/test_stock.py"],
                "diagnosis": "Refusal tests C3-C7 only check returncode 2, non-empty stderr and an unchanged store. On "
                "base 7a4b083 argparse rejects the unknown 'move'/'remove' subcommand with exit 2 and "
                "stderr, so these tests pass without the feature, and the runner's regression proof is "
                "FAIL.",
                "next_task": {
                    "kind": "implement",
                    "requirements": [
                        "Strengthen test_c3_move_more_than_on_hand_is_refused, test_c4_move_to_same_location_is_refused, "
                        "test_c5_malformed_store_is_refused, test_c6_non_positive_quantity_is_refused, "
                        "test_c7_remove_more_than_on_hand_is_refused (exact names kept) so each fails on base 7a4b083 "
                        "and passes on the change: assert stock.py's specific refusal text in stderr ('cannot take' for "
                        "C3/C7, 'must differ' for C4, 'malformed' for C5, 'positive integer' for C6) in addition to "
                        "returncode 2 and unchanged store."
                    ],
                },
            },
            "live 0568bvcm: unknown 'move' and 'remove' subcommands": {
                **self.GOOD,
                "diagnosis": "test_c3 to test_c7 (tests/test_stock.py:59-97) check only exit code 2, non-empty "
                "stderr and unchanged store bytes. On base, argparse rejects the unknown 'move' and "
                "'remove' subcommands with exactly those properties, so the tests pass on base and the "
                "runner cannot attribute C3-C7 to the change.",
            },
            "live 7ldxdtl5: move/remove subcommands do not exist": {
                **self.GOOD,
                "affected_paths": ["tests/test_stock.py"],
                "diagnosis": "On the base commit the move/remove subcommands do not exist, so argparse exits 2 with a "
                "usage error on stderr and never touches stock.json, which satisfies every assertion in "
                "those tests (rc==2, non-empty stderr, unchanged store).",
                "next_task": {
                    "kind": "implement",
                    "requirements": [
                        "Keep the test names test_c3_move_more_than_on_hand_is_refused, "
                        "test_c4_move_to_same_location_is_refused, test_c5_malformed_store_is_refused, "
                        "test_c6_non_positive_quantity_is_refused and test_c7_remove_more_than_on_hand_is_refused. Make "
                        "each one fail against base stock.py (968353664909813adf3257ab1d77ce935db47349, which has no "
                        "move/remove) while passing on the candidate. Each must still assert exit 2 and an unchanged "
                        "store, and must also assert that stderr has no argparse usage error ('usage:' and 'invalid "
                        "choice' absent) and contains the rule-specific refusal message produced by stock.py.",
                        "Only the file tests/test_stock.py.",
                    ],
                },
            },
            # Live 2026-10-05, second hybrid batch (fixed product): a guard on the product, and argparse named
            # between "unknown" and "subcommands".
            "live e6e57ewm: product change only to fix an exposed defect": {
                **self.GOOD,
                "affected_paths": ["tests/test_stock.py"],
                "next_task": {
                    "kind": "implement",
                    "requirements": [
                        "Strengthen test_c3_move_more_than_on_hand_is_refused so it fails on base: assert the 'cannot "
                        "take' refusal text and that stderr has no 'invalid choice'.",
                        "Do not weaken ReceiveTests or the C1/C2 tests. Change stock.py or README.md only to fix a real "
                        "defect that the stronger tests expose. Python standard library only.",
                    ],
                },
            },
            "live tzafwfjf: product change only if a test exposes a defect": {
                **self.GOOD,
                "affected_paths": ["tests/test_stock.py"],
                "next_task": {
                    "kind": "implement",
                    "requirements": [
                        "Strengthen test_c3_move_more_than_on_hand_is_refused to assert the refusal text.",
                        "Change stock.py/README.md only if a strengthened test exposes a genuine defect against the "
                        "brief.",
                    ],
                },
            },
            "live q1le599l: unknown argparse subcommands": {
                **self.GOOD,
                "affected_paths": ["tests/test_stock.py"],
                "diagnosis": "The implementation is behaviorally correct; the defect is test discrimination. On base "
                "b85c046 'move'/'remove' are unknown argparse subcommands, so argparse exits 2 with "
                "stderr and never touches stock.json, which satisfies every assertion in "
                "test_c3..test_c7. That makes them pass_to_pass, and regression_proof FAILs.",
            },
        }
        for label, report in right.items():
            with self.subTest(label):
                block = self.score(report)
                self.assertEqual(verdict.CORRECT, block["verdict"], block["reason"])


class RefundWindowFakeControls(unittest.TestCase):
    """No controller calls: exercise fixture source, checks and read-only reports directly."""

    def setUp(self):
        from harness import refund_window_provider

        self.provider = refund_window_provider
        self.scenario = catalog.load("feature-refund-window")
        self.temporary = tempfile.TemporaryDirectory(prefix="refund-fake-control-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        shutil.copytree(self.scenario.seed, self.project)
        self.config = {
            "reference": str(self.scenario.reference),
            "paths": ["shop/clock.py", "shop/refunds.py", "tests/test_refunds.py"],
            "check": self.scenario.fake_check,
        }
        self.common = {"task_id": "controlled-task", "contract_revision": 1, "contract_hash": "controlled-contract"}
        self.criteria = [{"id": "C1", "criterion": "The unchanged refund rules and existing behavior pass"}]
        self.data = {
            "current_task": {
                "id": "controlled-task",
                "kind": "implement",
                "milestone_id": "M1",
                "affected_paths": list(self.config["paths"]),
                "acceptance_criteria": ["C1"],
            },
            "goal_contract": {"body": {"acceptance_criteria": self.criteria}},
            "source_revision": "controlled-source",
            "validation": {"verdict": "FAIL"},
        }
        previous = Path.cwd()
        os.chdir(self.project)
        self.addCleanup(os.chdir, previous)
        environment = patch.dict(
            os.environ,
            {
                "SCENARIO_FAKE_CONFIG": str(self.root / "fake-config.json"),
                "SCENARIO_FAKE_REFUND_DIAGNOSIS": "specific",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        self.executed = []

    def check(self):
        completed = subprocess.run(
            [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-t", "."],
            cwd=self.project,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.executed.append(completed)
        return completed.returncode

    def call(self, stage):
        return self.provider.report_for(
            stage, self.data, self.common, self.config, self.check, lambda: [{"text": self.scenario.brief}]
        )

    def tree(self):
        return {
            str(path.relative_to(self.project)): path.read_bytes() for path in self.project.rglob("*") if path.is_file()
        }

    def test_first_build_retains_complete_tests_and_exposes_real_store_window_failure(self):
        report = self.call("terra")
        self.assertEqual(["exit 1"], report["results"])
        self.assertIn("test_window_ends_after_the_30th_store_day", self.executed[-1].stderr)
        self.assertEqual(
            (self.scenario.reference / "tests/test_refunds.py").read_bytes(), Path("tests/test_refunds.py").read_bytes()
        )
        self.assertEqual(
            (self.scenario.seed / "tests/test_orders.py").read_bytes(), Path("tests/test_orders.py").read_bytes()
        )

    def test_validator_measures_failure_and_leaves_exit_code_attachment_to_runner(self):
        self.call("terra")
        report = self.call("sol")
        self.assertEqual(1, self.executed[-1].returncode)
        self.assertEqual("FAIL", report["verdict"])
        self.assertIsNone(report["checks"][0]["exit_code"])
        self.assertEqual(["check:1"], report["criterion_results"][0]["evidence_refs"])

    def test_owner_defers_incomplete_task_and_resolver_supplies_same_bounded_task_read_only(self):
        self.call("terra")
        before = self.tree()
        review = self.call("astra_review")
        self.assertEqual("REWORK", review["status"])
        self.assertEqual([], review["next_task"]["validation_plan"])
        resolved = self.call("astra_resolve")
        self.assertIn("UTC-8", resolved["diagnosis"])
        self.assertEqual([self.scenario.fake_check], resolved["next_task"]["validation_plan"])
        self.assertEqual(self.data["current_task"]["affected_paths"], resolved["affected_paths"])
        self.assertEqual(["C1"], resolved["next_task"]["acceptance_criteria"])
        self.assertEqual(
            self.criteria, [{"id": row["id"], "criterion": row["criterion"]} for row in resolved["acceptance_criteria"]]
        )
        self.assertEqual(before, self.tree())

    def test_repair_and_independent_recheck_pass_without_modifying_tests_or_criteria(self):
        self.call("terra")
        self.call("astra_resolve")
        tests = Path("tests/test_refunds.py").read_bytes()
        repair = self.call("terra")
        self.assertEqual(["exit 0"], repair["results"])
        self.data["review_identity_policy"] = {"own_open_finding_ids": ["controlled-finding"]}
        checked = self.call("sol")
        self.assertEqual("PASS", checked["verdict"])
        self.assertEqual(0, self.executed[-1].returncode)
        self.data["validation"] = {"verdict": "PASS"}
        complete = self.call("astra_review")
        self.assertEqual("COMPLETE", complete["status"])
        self.assertEqual("controlled-finding", complete["finding_dispositions"][0]["id"])
        self.assertEqual(tests, Path("tests/test_refunds.py").read_bytes())

    def test_vague_diagnosis_negative_stays_read_only_and_keeps_product_repair(self):
        self.call("terra")
        before = self.tree()
        with patch.dict(os.environ, {"SCENARIO_FAKE_REFUND_DIAGNOSIS": "vague"}):
            report = self.call("astra_resolve")
        self.assertEqual("The implementation has a bug; fix it and rerun the tests.", report["diagnosis"])
        self.assertEqual(before, self.tree())
        self.assertEqual(["exit 0"], self.call("terra")["results"])

    def test_fixed_source_or_passing_validation_cannot_be_claimed_as_planted_failure(self):
        self.call("terra")
        self.call("terra")
        with self.assertRaisesRegex(ValueError, "not called on a planted"):
            self.call("astra_resolve")
        self.data["validation"] = {"verdict": "PASS"}
        with self.assertRaisesRegex(ValueError, "actual failing Validator"):
            self.call("astra_resolve")

    def test_selected_broken_cap_repair_remains_an_actual_failure_and_not_complete(self):
        self.call("terra")
        tests = Path("tests/test_refunds.py").read_bytes()
        self.config["reference"] = str(self.scenario.dir / "broken/per-refund-cap")
        self.assertEqual(["exit 1"], self.call("terra")["results"])
        self.assertEqual(tests, Path("tests/test_refunds.py").read_bytes())
        self.assertEqual("FAIL", self.call("sol")["verdict"])
        self.assertEqual("REWORK", self.call("astra_review")["status"])
        # The unchanged hidden product oracle also rejects the running-cap defect.
        # Diagnosis cannot rescue a wrong product or weaken its complete tests.
        product = verdict.evaluate(self.scenario, self.project)
        self.assertFalse(product.passed)
        self.assertFalse(next(row.ok for row in product.checks if row.name == "hidden_tests_pass"))
        self.assertIn("running total", self.provider._diagnosis())

    def test_unassigned_path_is_refused_before_any_source_write(self):
        before = self.tree()
        self.config["paths"].append("unassigned.py")
        with self.assertRaisesRegex(ValueError, "only the assigned"):
            self.call("terra")
        self.assertEqual(before, self.tree())

    def test_partial_scope_is_refused_before_any_source_write(self):
        before = self.tree()
        self.config["paths"] = ["shop/clock.py"]
        self.data["current_task"]["affected_paths"] = ["shop/clock.py"]
        with self.assertRaisesRegex(ValueError, "only the assigned"):
            self.call("terra")
        self.assertEqual(before, self.tree())


class RefundWindowRunTests(unittest.TestCase):
    """Actual target CLI coverage; run only after the exact fault diff is reviewed."""

    def run_fake(self, diagnosis="specific"):
        with tempfile.TemporaryDirectory(prefix="refund-window-run-") as out:
            args = argparse.Namespace(
                fake=True,
                profile=None,
                fake_solution="reference",
                out=Path(out),
                autocode=None,
                max_steps=None,
                timeout_minutes=None,
            )
            return run.run_one(
                catalog.load("feature-refund-window"), args, extra_env={"SCENARIO_FAKE_REFUND_DIAGNOSIS": diagnosis}
            )

    def test_real_failed_tester_reaches_resolver_on_planted_source_and_repairs(self):
        result = self.run_fake()
        self.assertEqual(
            (verdict.PASS, "TASK_COMPLETE"), (result["verdict"], result["runner_status"]), result["summary"]
        )
        self.assertTrue(result["oracle_passed"])
        self.assertEqual(
            (verdict.CORRECT, 1), (result["diagnosis"]["verdict"], result["diagnosis"]["resolver_calls_on_failure"])
        )
        stages = result["metrics"]["model_stage_names"]
        self.assertEqual(2, stages.count("terra"))
        self.assertEqual(2, stages.count("sol"))
        self.assertEqual(1, stages.count("astra_resolve"))
        self.assertLess(stages.index("sol"), stages.index("astra_resolve"))
        self.assertLess(stages.index("astra_resolve"), max(i for i, stage in enumerate(stages) if stage == "terra"))

    def test_real_resolver_vague_diagnosis_is_incorrect_even_with_correct_final_product(self):
        result = self.run_fake("vague")
        self.assertEqual(verdict.PASS, result["verdict"], result["summary"])
        self.assertTrue(result["oracle_passed"])
        self.assertEqual(
            (verdict.INCORRECT, 1), (result["diagnosis"]["verdict"], result["diagnosis"]["resolver_calls_on_failure"])
        )
        self.assertEqual(
            ["resolver_named_a_planted_defect"], [row["name"] for row in result["diagnosis"]["checks"] if not row["ok"]]
        )


class RefundClockFaultTests(unittest.TestCase):
    """feature-refund-window's scripted fault (#628): the plan names the reference tests, and the scripted
    Resolver names the planted clock defect only when the code it reads still has it."""

    @classmethod
    def setUpClass(cls):
        import runpy

        cls.provider = runpy.run_path(str(Path(__file__).parent / "harness" / "refund_clock_provider.py"))
        cls.scenario = catalog.load("feature-refund-window")

    def test_the_scenario_uses_the_fault_and_the_plan_names_every_reference_test(self):
        self.assertEqual("refund_store_clock", self.scenario.fake_fault)
        rows = self.provider["criteria"](self.scenario.reference)
        source = (self.scenario.reference / "tests" / "test_refunds.py").read_text()
        names = re.findall(r"def (test_\w+)", source)
        self.assertEqual([f"test: {name}" for name in names], [row["verification_method"] for row in rows])
        self.assertIn("test: test_window_counts_store_days_not_utc_days", [row["verification_method"] for row in rows])

    def test_the_first_build_keeps_the_planted_clock(self):
        broken = self.scenario.dir / "broken" / "trusts-store-date"
        self.assertFalse((broken / "shop" / "clock.py").exists(), "the first build must keep the seed's clock")
        self.assertNotIn("test_window", (broken / "tests" / "test_refunds.py").read_text())

    def test_the_resolver_names_the_store_offset_only_on_the_planted_clock(self):
        planted = (self.scenario.seed / "shop" / "clock.py").read_text()
        fixed = (self.scenario.reference / "shop" / "clock.py").read_text()
        failures = ["Test case C2: window counts store days not utc days has no test named x"]
        diagnosis, paths = self.provider["diagnose"](planted, failures)
        self.assertIn("UTC-8", diagnosis)
        self.assertIn("shop/clock.py", paths)
        diagnosis, paths = self.provider["diagnose"](fixed, failures)
        self.assertNotIn("store_date", diagnosis)
        self.assertEqual(["tests/test_refunds.py"], paths)
        # The older spelling of the same defect is recognized too.
        legacy = planted.replace("tz=UTC", "tz=timezone.utc")
        self.assertIn("UTC-8", self.provider["diagnose"](legacy, failures)[0])

    def test_vague_override_only_changes_diagnosis_of_a_planted_clock(self):
        planted = (self.scenario.seed / "shop" / "clock.py").read_text()
        fixed = (self.scenario.reference / "shop" / "clock.py").read_text()
        failures = ["Test case C2: window counts store days not utc days has no test named x"]
        with patch.dict(os.environ, {"SCENARIO_FAKE_REFUND_DIAGNOSIS": "specific"}):
            specific = self.provider["diagnose"](planted, failures)
            not_planted = self.provider["diagnose"](fixed, failures)
        for mode in ("vague", "specific", "unknown"):
            with self.subTest(mode=mode), patch.dict(os.environ, {"SCENARIO_FAKE_REFUND_DIAGNOSIS": mode}):
                diagnosis, paths = self.provider["diagnose"](planted, failures)
                self.assertEqual(specific[1], paths)
                self.assertEqual(
                    "The implementation has a bug; fix it and rerun the tests." if mode == "vague" else specific[0],
                    diagnosis,
                )
                self.assertEqual(not_planted, self.provider["diagnose"](fixed, failures))


class RefundWindowDiagnosisTests(unittest.TestCase):
    """feature-refund-window's diagnosis() on synthetic run records (issue #59): a Resolver call is on the
    planted failure when the source it saw fails the hidden WindowTests or CapTests, rebuilt from the runner's
    code checkpoint."""

    NAMED = "store_date ignores the UTC-8 store offset, so the window counts UTC days"

    @classmethod
    def setUpClass(cls):
        from harness.project import git, materialize, without_maintenance

        cls.fixture_root = tempfile.mkdtemp(prefix="refund-diagnosis-")
        scenario = catalog.load("feature-refund-window")
        project = without_maintenance(
            materialize(
                scenario.seed, Path(cls.fixture_root) / "project", scenario.dir / "broken" / "trusts-store-date"
            )
        )
        cls.commits = {"seed": git(project, "rev-parse", "HEAD").strip()}  # no shop/refunds.py yet
        refunds = project / "shop" / "refunds.py"
        for revision, overlay in (
            ("planted", None),
            ("refusal-bug", scenario.reference),
            ("fixed", scenario.reference),
        ):
            if overlay:
                shutil.copytree(overlay, project, dirs_exist_ok=True)
            if revision == "refusal-bug":  # both planted defects fixed, but a disputed order is no longer refused
                refunds.write_text(
                    refunds.read_text().replace(
                        '    if order.disputed:\n        raise RefundRefused("the order is disputed")\n', ""
                    )
                )
            git(project, "add", "-A")
            git(project, "commit", "-q", "-m", revision)
            cls.commits[revision] = git(project, "rev-parse", "HEAD").strip()
        cls.fixture = project

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.fixture_root, ignore_errors=True)

    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.project = Path(root.name) / "project"
        shutil.copytree(self.fixture, self.project, symlinks=True)
        self.run_dir = self.project / ".autocode" / "runs" / "20261005-000000-refund"
        self.run_dir.mkdir(parents=True)
        self.scenario = catalog.load("feature-refund-window")
        self.state = {
            "stages": [],
            "code_checkpoints": [
                {"source_revision": revision, "commit": commit, "available": True}
                for revision, commit in self.commits.items()
            ],
        }

    def resolver(self, diagnosis=None, revision="planted", **fields):
        name = f"resolver-{len(self.state['stages']) + 1}.json"
        path = self.run_dir / name
        if diagnosis is not None:
            path.write_text(json.dumps({"status": "REWORK", "diagnosis": diagnosis}))
        self.state["stages"].append(
            {
                "stage": "astra_resolve",
                "source_revision": revision,
                "output": str(path),
                "launch_route": {"model": "resolver-model"},
                "runner_calls": 1,
                **fields,
            }
        )

    def diagnose(self):
        (self.run_dir / "state.json").write_text(json.dumps(self.state))
        run_record = {
            "model_stages": ["terra", "sol", "astra_review", "astra_resolve"],
            "resolutions": [
                {"diagnosis": json.loads(Path(row["output"]).read_text())["diagnosis"]}
                for row in self.state["stages"]
                if not row.get("rejected") and Path(row["output"]).is_file()
            ],
        }
        block = verdict.diagnose(self.scenario, self.project, run_record)
        # A poor diagnosis of correct code is not a false completion: the product checks pass alone.
        self.assertTrue(verdict.evaluate(self.scenario, self.project, run_record).passed)
        return block

    def test_a_planted_defect_named_is_correct_and_a_vague_or_merely_topical_one_is_not(self):
        for diagnosis, expected in (
            (self.NAMED, verdict.CORRECT),
            ("The implementation has a bug; fix it.", verdict.INCORRECT),
            ("The refund total in the store is wrong; fix the partial refund logic.", verdict.INCORRECT),
        ):
            with self.subTest(diagnosis):
                self.state["stages"].clear()
                self.resolver(diagnosis)
                self.assertEqual(expected, self.diagnose()["verdict"])

    def test_a_call_that_saved_nothing_is_unscored_and_a_rejected_report_is_scored(self):
        self.resolver(exit_code=-9)
        self.assertEqual(verdict.UNSCORED, self.diagnose()["verdict"])
        self.state["stages"].clear()
        self.resolver(self.NAMED, rejected=True, rejection_reason="schema")
        block = self.diagnose()
        self.assertEqual(verdict.INCORRECT, block["verdict"])
        self.assertEqual(["diagnosis_accepted"], [check["name"] for check in block["checks"] if not check["ok"]])

    def test_a_call_on_source_that_passes_the_hidden_tests_was_not_on_the_planted_failure(self):
        # Source that passes the planted classes, fails only another hidden test (a dropped disputed-order
        # refusal), or misses the feature altogether is not the planted failure.
        for revision, outcome, diagnosis in (
            ("fixed", "pass", self.NAMED),
            ("refusal-bug", "pass", "refund() no longer refuses a disputed order: the check was dropped."),
            ("seed", "do not import", "The Builder never added shop/refunds.py; add the refund function."),
        ):
            with self.subTest(revision):
                self.state["stages"].clear()
                self.resolver(diagnosis, revision=revision)
                block = self.diagnose()
                self.assertEqual(verdict.NOT_EXERCISED, block["verdict"], block["reason"])
                self.assertEqual([outcome], [call["planted_tests"] for call in block["other_resolver_calls"]])
        self.state["stages"].clear()
        self.assertEqual(verdict.NOT_EXERCISED, self.diagnose()["verdict"])
        self.assertEqual(verdict.NOT_EXERCISED, verdict.diagnose(self.scenario, self.project, None)["verdict"])


class PlanCompareTests(unittest.TestCase):
    """scenarios/planning.toml and `run.py plan-compare`, with the scripted model (seconds)."""

    def test_the_planning_corpus_loads_and_covers_clear_and_vague_requests(self):
        cases = plan_compare.load()
        self.assertEqual(len(cases), len({case.id for case in cases}))
        self.assertEqual({"clear", "vague"}, {case.expect.get("clarity") for case in cases})
        self.assertTrue(all(case.brief for case in cases))

    def test_each_adaptive_path_reaches_plan_approval_with_fewer_calls(self):
        # Per case: the adaptive run's stages up to the first plan, then after the feedback on it. Every run
        # starts at Requirements, which names the job (#1860); clarity no longer skips it.
        plan, review, revise = "astra_discovery", "astra_challenge", "glm_revise"
        wanted = {
            "tiny-greeting": (["requirements_gather", plan, review], [plan, review]),
            "vague-refunds": (["requirements_gather", plan, review], [plan, review]),
            "diamond-four-milestones": (
                ["requirements_gather", plan, review, revise, review],
                [plan, review, revise, review],
            ),
            # The Planner sends feedback that changes the product back to Requirements.
            "vague-reading-list": (
                ["requirements_gather", plan, review, revise, "astra_finalize"],
                [plan, "requirements_gather", plan, review, revise, "astra_finalize"],
            ),
        }
        cases = [case for case in plan_compare.load() if case.id in wanted]
        with tempfile.TemporaryDirectory(prefix="plan-compare-test-") as out:
            records = plan_compare.run(
                cases,
                Path(out),
                jobs=4,
                fake=True,
                profile=None,
                autocode=run.default_autocode(),
                timeout_minutes=5,
                max_steps=30,
            )
            self.assertIn("## Feedback on the first plan", (Path(out) / "blind" / "tiny-greeting.md").read_text())
        for case_id, (first, after) in wanted.items():
            with self.subTest(case=case_id):
                today, adaptive = records[(case_id, "today")], records[(case_id, "adaptive")]
                self.assertEqual(
                    ("approve_plan", "approve_plan"),
                    (today["ended"], adaptive["ended"]),
                    today["error"] or adaptive["error"],
                )
                self.assertEqual(first, adaptive["model_stages"])
                self.assertEqual(5, today["model_calls"], today["model_stages"])
                self.assertTrue(adaptive["final_plan"])
                self.assertEqual(after, adaptive["feedback_round"]["model_stages"])
                self.assertEqual(
                    case_id == "vague-reading-list", bool(adaptive["feedback_round"]["requirements_rerun"])
                )
                self.assertEqual(
                    ["requirements_gather", plan, review, revise, "astra_finalize"],
                    today["feedback_round"]["model_stages"],
                    "today restarts from Requirements",
                )
                self.assertTrue(today["feedback_round"]["final_plan"] and adaptive["feedback_round"]["final_plan"])


class ApiCostTests(unittest.TestCase):
    """Accounting must not turn shorter prompts, cached tokens or failures into misleading dollars."""

    def setUp(self):
        self.card = json.loads((Path(__file__).parent / "api-pricing-2026-10-02.json").read_text())

    def test_fresh_cache_read_cache_write_and_output_have_separate_rates(self):
        value = api_cost.request_cost(
            {"input": 1000, "cache_read": 2000, "cache_write": 3000, "output": 4000}, "openai/gpt-6-sol", self.card
        )
        self.assertAlmostEqual(0.002 + 0.0004 + 0.0075 + 0.04, value)

    def test_long_context_surcharge_is_per_request_and_uses_all_input(self):
        for total, multiplier in ((272000, 1), (272001, 2)):
            with self.subTest(total=total):
                tokens = {"input": 1000, "cache_read": total - 1000, "cache_write": 0, "output": 10}
                value = api_cost.request_cost(tokens, "openai/gpt-6-sol", self.card)
                expected = (0.002 + (total - 1000) * 0.2 / 1e6) * multiplier + 0.0001 * (1.5 if multiplier == 2 else 1)
                self.assertAlmostEqual(expected, value)

    def test_unknown_rates_and_usage_are_not_zero_cost(self):
        tokens = {"input": 100, "cache_read": 0, "cache_write": 0, "output": 10}
        with self.assertRaisesRegex(ValueError, "no API rates"):
            api_cost.request_cost(tokens, "unknown/model", self.card)
        with self.assertRaisesRegex(ValueError, "unknown cache_write"):
            api_cost.request_cost({**tokens, "cache_write": 1}, "zai-coding-plan/glm-5.3", self.card)
        for value in (None, -1, True, "100"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "invalid request"):
                api_cost.request_cost({**tokens, "input": value}, "openai/gpt-6-sol", self.card)

    def event(self, id_="one", *, reasoning=10, reason="stop"):
        return {
            "type": "step_finish",
            "sessionID": "session",
            "part": {
                "id": id_,
                "sessionID": "session",
                "reason": reason,
                "tokens": {"input": 1000, "output": 20, "reasoning": reasoning, "cache": {"read": 100, "write": 0}},
            },
        }

    def stage(self, path, name="terra", **extra):
        return {
            "stage": name,
            "engine": "opencode",
            "events": str(path),
            "command": ["opencode", "run", "--model", "openai/gpt-6-sol"],
            **extra,
        }

    def test_replayed_finishes_across_stages_bill_once_and_reasoning_is_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            path.write_text("\n".join(json.dumps(self.event()) for _ in range(2)))
            result = api_cost.estimate({"stages": [self.stage(path), self.stage(path, "sol")]}, self.card)
        self.assertTrue(result["complete"], result["issues"])
        self.assertEqual(1, result["requests"])
        self.assertAlmostEqual(0.002 + 0.00002 + 0.0003, result["usd"])

    def test_rejected_and_truncated_calls_and_active_stage_still_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, active = Path(tmp) / "first.jsonl", Path(tmp) / "active.jsonl"
            first.write_text(json.dumps(self.event("first", reason="length")))
            active.write_text(json.dumps(self.event("active", reason="tool-calls")))
            state = {
                "stages": [
                    self.stage(first, "sol_report_repair", rejected=True),
                    {"stage": "orchestrator", "runner_owned": True},
                ],
                "active_stage": self.stage(active),
            }
            result = api_cost.estimate(state, self.card)
        self.assertTrue(result["complete"], result["issues"])
        self.assertEqual(2, result["requests"])
        self.assertAlmostEqual(0.00464, result["usd"])

    def test_permission_ui_notices_do_not_erase_valid_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            path.write_text(
                "\x1b[93m\x1b[1m! \x1b[0mpermission requested: external_directory (/example)\n"
                + json.dumps(self.event())
            )
            result = api_cost.estimate({"stages": [self.stage(path)]}, self.card)
        self.assertTrue(result["complete"], result["issues"])
        self.assertAlmostEqual(0.00232, result["usd"])

    def test_interrupted_request_keeps_earlier_spend_but_total_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            start = {"type": "step_start", "sessionID": "session", "part": {"id": "new", "sessionID": "session"}}
            path.write_text(json.dumps(self.event()) + "\n" + json.dumps(start))
            result = api_cost.estimate({"stages": [self.stage(path)]}, self.card)
        self.assertFalse(result["complete"])
        self.assertIsNone(result["usd"])
        self.assertAlmostEqual(0.00232, result["known_usd"])

    def test_missing_or_malformed_evidence_keeps_total_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            good, bad = Path(tmp) / "good.jsonl", Path(tmp) / "bad.jsonl"
            good.write_text(json.dumps(self.event()))
            for contents in ("not json", json.dumps({"type": "error"}), json.dumps(self.event(reasoning=-1))):
                with self.subTest(contents=contents):
                    bad.write_text(contents)
                    result = api_cost.estimate({"stages": [self.stage(good), self.stage(bad, "sol")]}, self.card)
                    self.assertIsNone(result["usd"])
                    self.assertFalse(result["complete"])
                    self.assertGreater(result["known_usd"], 0)
            result = api_cost.estimate({"stages": [self.stage(Path(tmp) / "missing.jsonl")]}, self.card)
            self.assertIsNone(result["usd"])

    def test_conflicting_replays_and_mixed_sessions_do_not_claim_complete_accounting(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp) / "first.jsonl", Path(tmp) / "second.jsonl"
            first.write_text(json.dumps(self.event()))
            second.write_text(json.dumps(self.event(reasoning=11)))
            result = api_cost.estimate({"stages": [self.stage(first), self.stage(second)]}, self.card)
            self.assertIsNone(result["usd"])
            other = self.event("second")
            other["sessionID"] = other["part"]["sessionID"] = "other"
            second.write_text(json.dumps(self.event()) + "\n" + json.dumps(other))
            result = api_cost.estimate({"stages": [self.stage(second)]}, self.card)
            self.assertIsNone(result["usd"])


class BuildCompareTests(unittest.TestCase):
    def protocol(self, *, fake=False):
        return {"fake": fake, "profile_name": "build-comparison", "pairs": build_compare.schedule(["case"], 2)}

    def record(self, variant, repeat, *, outcome=verdict.PASS, cost=1):
        return {
            "scenario": "case",
            "variant": variant,
            "repeat": repeat,
            "verdict": outcome,
            "oracle_passed": outcome == verdict.PASS,
            "checks": [{"name": "behavior", "ok": outcome == verdict.PASS}],
            "api_cost": {"usd": cost, "complete": True},
            "wall_seconds": 1,
            "metrics": {"model_stages": 6, "report_repairs": 0},
        }

    def test_pairs_balance_order_and_invalid_repeats_and_duplicates_are_refused(self):
        pairs = build_compare.schedule(["one", "two"], 2)
        self.assertEqual(["fixed", "adaptive", "adaptive", "fixed"], [pair["order"][0] for pair in pairs])
        for ids, repeats in ((["one"], 0), (["one", "one"], 2)):
            with self.assertRaises(ValueError):
                build_compare.schedule(ids, repeats)

    def test_failed_attempt_spend_is_in_cost_per_pass_and_false_completion_is_failure(self):
        rows = [
            self.record("fixed", 1),
            self.record("fixed", 2),
            self.record("adaptive", 1),
            self.record("adaptive", 2, outcome=verdict.FALSE_COMPLETE, cost=3),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            result = build_compare.report(Path(tmp), self.protocol(), rows)
        self.assertEqual(4, result["summary"]["adaptive"]["api_usd_per_pass"])
        self.assertEqual(1, result["summary"]["fixed"]["api_usd_per_pass"])
        self.assertFalse(result["all_passed"])

    def test_incomplete_oracle_and_harness_errors_cannot_count_as_success(self):
        row = self.record("fixed", 1)
        for override in ({"oracle_passed": False}, {"harness_error": "deadline"}, {"verdict": verdict.NOT_EXERCISED}):
            with self.subTest(override=override):
                self.assertFalse(build_compare.passed({**row, **override}))

    def test_missing_attempts_unknown_costs_and_fake_runs_cannot_prove_savings(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = build_compare.report(Path(tmp), self.protocol(), [self.record("fixed", 1)])
            self.assertEqual(3, len(result["missing"]))
            self.assertEqual(2, result["summary"]["fixed"]["scheduled"])
            self.assertIsNone(result["summary"]["fixed"]["api_usd"])
            rows = [self.record(variant, repeat) for repeat in (1, 2) for variant in ("fixed", "adaptive")]
            rows[0]["api_cost"] = {"usd": None, "complete": False}
            result = build_compare.report(Path(tmp), self.protocol(), rows)
            self.assertIsNone(result["summary"]["fixed"]["api_usd"])
            result = build_compare.report(Path(tmp), self.protocol(fake=True), rows)
            self.assertIsNone(result["summary"]["adaptive"]["api_usd"])

    def test_rebuild_preserves_partial_pairs_and_runs_no_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "protocol.json").write_text(json.dumps(self.protocol()))
            pair = root / "pair-case-1"
            pair.mkdir()
            (pair / "attempt-fixed.json").write_text(json.dumps(self.record("fixed", 1)))
            result = build_compare.rebuild(root)
            self.assertEqual(3, len(result["missing"]))
            with self.assertRaisesRegex(ValueError, "duplicate"):
                build_compare.report(root, self.protocol(), [self.record("fixed", 1)] * 2)

    def test_live_profile_cannot_silently_follow_changing_defaults(self):
        args = argparse.Namespace(
            jobs=1,
            repeats=2,
            rate_card=Path(__file__).parent / "api-pricing-2026-10-02.json",
            profile="default",
            fake=False,
        )
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(ValueError, "explicit model profile"):
            build_compare.run([], args, Path(tmp), run_one=Mock(), revision={})

    def test_prepare_freezes_the_live_protocol_without_spend_authorization_or_launching(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(run, "run_one") as launch:
            with contextlib.redirect_stdout(io.StringIO()):
                code = run.main(
                    [
                        "build-compare",
                        "greenfield-greeting-cli",
                        "--profile",
                        "build-comparison",
                        "--prepare",
                        "--repeats",
                        "2",
                        "--out",
                        tmp,
                        "--rate-card",
                        str(Path(__file__).parent / "api-pricing-2026-10-02.json"),
                    ]
                )
            launch.assert_not_called()
            self.assertEqual(0, code)
            (path,) = Path(tmp).glob("*/protocol.json")
            protocol = json.loads(path.read_text())
            self.assertEqual(4, sum(len(pair["order"]) for pair in protocol["pairs"]))
            self.assertEqual(
                catalog.load("greenfield-greeting-cli").brief, protocol["briefs"]["greenfield-greeting-cli"]
            )
            self.assertIn("rates_per_million", protocol["rate_card"])

    def test_repeated_fake_campaign_completes_both_modes_in_fresh_projects(self):
        with tempfile.TemporaryDirectory(prefix="build-compare-test-") as tmp:
            args = argparse.Namespace(
                jobs=2,
                repeats=2,
                rate_card=None,
                profile=None,
                fake=True,
                fake_solution="reference",
                out=Path(tmp),
                autocode=None,
                max_steps=20,
                timeout_minutes=5,
            )
            result = build_compare.run(
                [catalog.load("greenfield-greeting-cli")],
                args,
                Path(tmp),
                run_one=run.run_one,
                revision={"commit": "test"},
            )
            self.assertTrue(result["all_passed"], result["records"])
            self.assertEqual(4, len({row["evidence"] for row in result["records"]}))
            for row in result["records"]:
                stages = row["metrics"]["model_stage_names"]
                self.assertIn("sol", stages)
                self.assertIn("astra_review", stages)
                steps = [json.loads(line) for line in (Path(row["evidence"]) / "steps.jsonl").read_text().splitlines()]
                self.assertIn("approve-plan", [step["kind"] for step in steps])
            counts = {row["variant"]: row["metrics"]["model_stages"] for row in result["records"]}
            self.assertLess(counts["adaptive"], counts["fixed"])


class AdaptiveCompletionTests(unittest.TestCase):
    """Exercise adaptive decisions through the public CLI and independent delivery oracles."""

    def run_fake(self, scenario, *, env=None, solution="reference", flags=()):
        with tempfile.TemporaryDirectory(prefix="adaptive-complete-test-") as tmp:
            args = argparse.Namespace(
                fake=True,
                profile=None,
                fake_solution=solution,
                out=Path(tmp),
                autocode=None,
                max_steps=None,
                timeout_minutes=5,
            )
            return run.run_one(catalog.load(scenario), args, extra_flags=("--adaptive-planning", *flags), extra_env=env)

    def test_vague_requests_keep_requirements_and_still_complete(self):
        result = self.run_fake("feature-timesheet-by-project", env={"SCENARIO_FAKE_CLARITY": "vague"})
        self.assertEqual(verdict.PASS, result["verdict"], result["summary"])
        self.assertIn("requirements_gather", result["metrics"]["model_stage_names"])

    def test_forced_build_without_recognition_keeps_requirements(self):
        result = self.run_fake("greenfield-greeting-cli", flags=("--workflow", "build"))
        self.assertEqual(verdict.PASS, result["verdict"], result["summary"])
        self.assertIn("requirements_gather", result["metrics"]["model_stage_names"])

    def test_negative_exit_plan_probes_complete_in_both_planning_modes(self):
        for flags in (("--no-adaptive-planning",), ("--adaptive-planning",)):
            with self.subTest(flags=flags), tempfile.TemporaryDirectory(prefix="negative-plan-test-") as tmp:
                args = argparse.Namespace(
                    fake=True,
                    profile=None,
                    fake_solution="reference",
                    out=Path(tmp),
                    autocode=None,
                    max_steps=None,
                    timeout_minutes=5,
                )
                result = run.run_one(
                    catalog.load("greenfield-greeting-cli"),
                    args,
                    extra_flags=flags,
                    extra_env={"SCENARIO_FAKE_NEGATIVE_PLAN": "1"},
                )
                self.assertEqual(verdict.PASS, result["verdict"], result["summary"])
                self.assertEqual(0, result["metrics"]["report_repairs"], "valid probes need no report repair")

    def test_blocking_reviews_require_a_revision_before_complete_delivery(self):
        for id_ in ("greenfield-greeting-cli", "parallel-diamond"):
            with self.subTest(scenario=id_):
                result = self.run_fake(id_, env={"SCENARIO_FAKE_BLOCKING_REVIEW": "1"})
                self.assertEqual(verdict.PASS, result["verdict"], result["summary"])
                stages = result["metrics"]["model_stage_names"]
                self.assertIn("glm_revise", stages)
                self.assertEqual(2 if id_ == "parallel-diamond" else 1, stages.count("astra_challenge"))
                self.assertIn("sol", stages)

    def test_broken_deliveries_are_rejected_by_the_original_brief_oracle(self):
        for id_ in ("greenfield-todo-cli", "feature-timesheet-by-project", "parallel-diamond"):
            scenario = catalog.load(id_)
            with self.subTest(scenario=id_):
                solution = str(scenario.broken[0].relative_to(scenario.dir))
                result = self.run_fake(id_, solution=solution)
                self.assertNotEqual(verdict.PASS, result["verdict"], result["summary"])
                self.assertFalse(result["oracle_passed"])

    def test_missing_literal_brackets_is_blocked_despite_passing_delivered_tests(self):
        result = self.run_fake("greenfield-todo-cli", solution="broken/unbracketed-status")
        self.assertEqual(verdict.HONEST_BLOCKER, result["verdict"], result["summary"])
        self.assertNotIn(result["runner_status"], verdict.COMPLETE_STATUSES)
        self.assertEqual(
            {"add_then_list", "complete_marks_done", "ids_stable_across_restarts"},
            {check["name"] for check in result["checks"] if not check["ok"]},
        )

    def test_review_and_discussion_keep_the_same_model_sequence(self):
        for id_, expected in (
            ("review-clean-pr", ["requirements_gather", "review_change"]),
            ("discuss-cache-choice", ["requirements_gather", "answer_question"]),
        ):
            with self.subTest(scenario=id_):
                result = self.run_fake(id_)
                self.assertEqual(verdict.PASS, result["verdict"], result["summary"])
                self.assertEqual(expected, result["metrics"]["model_stage_names"])

    def test_progressive_delegations_retain_the_review_that_authorizes_execution(self):
        for id_ in (
            "progressive-learning-journey",
            "progressive-cumulative-regression",
            "progressive-split-learning-journey",
        ):
            with self.subTest(scenario=id_):
                result = self.run_fake(id_)
                self.assertEqual(verdict.PASS, result["verdict"], result["summary"])
                stages = result["metrics"]["model_stage_names"]
                self.assertLess(stages.index("glm_revise"), stages.index("astra_finalize"))
                self.assertLess(stages.index("astra_finalize"), stages.index("terra"))


class BaselineTests(unittest.TestCase):
    """The plain agent AutoCode is compared against: how it is launched and what its exit means."""

    def test_presets_and_templates(self):
        self.assertEqual(
            ["opencode", "run", "--dir", "/p", "--model", "openai/gpt-6-sol"],
            baseline.command("opencode", None, Path("/p"), "openai/gpt-6-sol"),
        )
        self.assertEqual(
            ["codex", "exec", "-C", "/p", "--sandbox", "workspace-write", "-"],
            baseline.command("codex", None, Path("/p"), None),
        )
        self.assertEqual(
            ["agent", "--cwd", "/p", "--model", "m"],
            baseline.command("codex", "agent --cwd {project} --model {model}", Path("/p"), "m"),
        )
        with self.assertRaisesRegex(ValueError, "unknown baseline"):
            baseline.command("nope", None, Path("/p"), None)

    def test_only_exit_zero_claims_completion(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            ran = baseline.run(
                [sys.executable, "-c", "import sys; sys.exit(sys.stdin.read() != 'brief')"],
                root,
                "brief",
                root / "ok.log",
                env={},
                timeout_seconds=60,
            )
            self.assertEqual((0, baseline.CLAIMED), (ran["exit"], ran["status"]))
            failed = baseline.run(
                [sys.executable, "-c", "raise SystemExit(3)"], root, "", root / "x.log", env={}, timeout_seconds=60
            )
            self.assertEqual((3, baseline.STOPPED), (failed["exit"], failed["status"]))
            missing = baseline.run(["no-such-agent-binary"], root, "", root / "m.log", env={}, timeout_seconds=60)
            self.assertEqual((127, baseline.STOPPED), (missing["exit"], missing["status"]))
            self.assertIn("no-such-agent-binary", (root / "m.log").read_text())


class CompareSummaryTests(unittest.TestCase):
    @staticmethod
    def row(name, auto, base, auto_ok, base_ok, auto_s=10.0, base_s=1.0):
        return {
            "scenario": name,
            "autocode": {"verdict": auto, "deliverable_passed": auto_ok, "seconds": auto_s, "model_stages": 5},
            "baseline": {"verdict": base, "deliverable_passed": base_ok, "seconds": base_s},
        }

    def test_summary_counts_each_side(self):
        rows = [
            self.row("a", verdict.PASS, verdict.FALSE_COMPLETE, True, False, 30.0, 2.0),
            self.row("b", verdict.PASS, verdict.PASS, True, True, 10.0, 4.0),
            self.row("c", verdict.HONEST_BLOCKER, verdict.PASS, False, True, 20.0, 6.0),
            {"scenario": "d", "skipped": "requires go"},
        ]
        summary = compare.summarize(rows)
        self.assertEqual((4, 3, 1), (summary["scenarios"], summary["compared"], summary["skipped"]))
        self.assertEqual({"autocode only": 1, "both": 1, "baseline only": 1}, summary["outcomes"])
        self.assertEqual(
            {
                "deliverable_passed": 2,
                "verdicts": {"PASS": 2, "HONEST_BLOCKER": 1},
                "false_completions": 0,
                "total_seconds": 60.0,
                "median_seconds": 20.0,
            },
            summary["autocode"],
        )
        self.assertEqual(
            (2, 1, 4.0),
            (
                summary["baseline"]["deliverable_passed"],
                summary["baseline"]["false_completions"],
                summary["baseline"]["median_seconds"],
            ),
        )
        text = compare.markdown(
            {
                "baseline": "opencode (one call)",
                "mode": "fake",
                "started_at": "t",
                "out": "/o",
                "autocode": {"commit": "0123456789abcdef", "dirty": False},
            },
            rows,
            summary,
        )
        self.assertIn("| Deliverable accepted by the oracle | 2/3 | 2/3 |", text)
        self.assertIn("| a | PASS | FALSE_COMPLETE | 30.0 | 2.0 | 5 | autocode only |", text)
        self.assertIn("| d | skipped: requires go |", text)


class CompareRunTests(unittest.TestCase):
    """AutoCode and the scripted agent on one scenario, through run.py compare (a few seconds each)."""

    def compare(self, *extra):
        with tempfile.TemporaryDirectory(prefix="compare-test-") as out:
            self.assertEqual(0, run.main(["compare", "bugfix-iso-weeks", "--fake", "--out", out, *extra]))
            [report] = Path(out).glob("*-compare-fake-*/comparison.json")
            self.assertTrue((report.parent / "comparison.md").is_file())
            return json.loads(report.read_text())

    def test_both_sides_deliver_the_reference(self):
        [row] = self.compare()["rows"]
        self.assertEqual((verdict.PASS, verdict.PASS), (row["autocode"]["verdict"], row["baseline"]["verdict"]))
        self.assertEqual("both", compare.outcome(row))

    def test_a_wrong_baseline_is_a_false_completion_on_the_same_oracle(self):
        report = self.compare("--fake-baseline-solution", "broken/special-case")
        [row] = report["rows"]
        self.assertEqual(verdict.PASS, row["autocode"]["verdict"])
        self.assertEqual(verdict.FALSE_COMPLETE, row["baseline"]["verdict"], row["baseline"]["summary"])
        self.assertEqual(1, report["summary"]["baseline"]["false_completions"])
        self.assertEqual("autocode only", compare.outcome(row))


class TokenBudgetOptionTests(unittest.TestCase):
    def test_retired_token_cap_option_is_rejected_by_run_and_compare(self):
        for command in ("run", "compare"):
            with self.subTest(command=command), contextlib.redirect_stderr(io.StringIO()) as error:
                with patch.object(run, "Driver") as launch, self.assertRaises(SystemExit) as caught:
                    run.main([command, "bugfix-iso-weeks", "--fake", "--max-reported-tokens", "1"])
                self.assertEqual(2, caught.exception.code)
                self.assertIn("unrecognized arguments", error.getvalue())
                launch.assert_not_called()


STATS_PHASE_SCRIPT = """
import os
import sys

sys.path.insert(0, os.environ["PHASE_HARNESS_ROOT"])
from harness import phase_env

phase_env.write_synthetic_credentials(os.environ["PHASE_CREDENTIAL_ROOT"], "synthetic-stats-token")
"""

COMPAT_PHASE_SCRIPT = """
import os
import sys

sys.path.insert(0, os.environ["PHASE_HARNESS_ROOT"])
from harness import phase_env

STAGE = sys.argv[1] if len(sys.argv) > 1 else "call"
GUARD = phase_env.guard()


def usage_snapshot():
    # A production-like client: it polls the default usage destination and
    # swallows a transport refusal into "no snapshot", so the child exit stays
    # green while the phase record keeps the violation.
    try:
        with GUARD.get(phase_env.DEFAULT_USAGE_URL, timeout=5) as response:
            return response.read()
    except phase_env.RefusedTransportError:
        return None


found = phase_env.find_credentials(os.environ["PHASE_CREDENTIAL_ROOT"]) is not None
if found and STAGE == "call":
    usage_snapshot()
if found and STAGE == "teardown":
    usage_snapshot()
sys.exit(0)
"""


class PhaseEnvironmentTests(unittest.TestCase):
    """Phase-owned acceptance environments for issue #225 (milestone M1).

    ``harness.phase_env`` is imported inside each test so this module still
    imports — and the oracle-inheritance guard below still passes — on a
    checkout without the fix, which is what the fail-first regression capture
    runs against. Test scratch stays under the workspace's ignored
    ``.autocode`` tree; no test writes outside the workspace or touches HOME.
    """

    def phase_env(self):
        from harness import phase_env

        return phase_env

    def sequence_base(self, label):
        base = (
            Path(__file__).resolve().parent.parent
            / ".autocode"
            / "phase-env-tests"
            / f"{label}-{uuid.uuid4().hex[:10]}"
        )
        base.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        return base

    def run_two_phase_sequence(self, base, *, share_credential_root=False):
        phase_env = self.phase_env()
        sequence = phase_env.PhaseSequence("acceptance", base)
        stats = sequence.phase("stats")
        compat = sequence.phase("compat", share_credential_root_with=stats if share_credential_root else None)
        stats.run([sys.executable, "-c", STATS_PHASE_SCRIPT])
        compat.run([sys.executable, "-c", COMPAT_PHASE_SCRIPT, "call"])
        return sequence, stats, compat

    def test_state_teardown_preserves_refusals_without_failing_clean_phases(self):
        phase_env = self.phase_env()
        for refused in (False, True):
            with self.subTest(refused=refused):
                sequence = phase_env.PhaseSequence("teardown", self.sequence_base("state-cleanup"))
                phase = sequence.phase("compat")
                if refused:
                    phase_env.write_synthetic_credentials(phase.credential_root, "synthetic-token")
                script = COMPAT_PHASE_SCRIPT.replace(
                    "sys.exit(0)", "import shutil\nshutil.rmtree(os.environ['PHASE_STATE_ROOT'])\nsys.exit(0)"
                )
                child = phase.run([sys.executable, "-c", script, "teardown"])
                self.assertEqual(0, child.returncode, child.stderr)
                self.assertFalse(phase.state_root.exists())
                record = sequence.finish()
                self.assertEqual(phase_env.ERROR if refused else phase_env.GREEN, record["outcome"], record)
                self.assertEqual(int(refused), len(record["phases"][0]["unexpected_requests"]))

    def test_missing_or_corrupt_refusal_ledger_cannot_be_green(self):
        phase_env = self.phase_env()
        for damage in ("missing", "corrupt"):
            with self.subTest(damage=damage):
                sequence = phase_env.PhaseSequence("ledger", self.sequence_base("ledger-loss"))
                phase = sequence.phase("compat")
                phase_env.write_synthetic_credentials(phase.credential_root, "synthetic-token")
                teardown = (
                    "from pathlib import Path\nledger = Path(os.environ['PHASE_UNEXPECTED_REQUESTS'])\n"
                    + ("ledger.unlink()\n" if damage == "missing" else "ledger.write_text('{broken')\n")
                    + "sys.exit(0)"
                )
                child = phase.run([sys.executable, "-c", COMPAT_PHASE_SCRIPT.replace("sys.exit(0)", teardown)])
                self.assertEqual(0, child.returncode, child.stderr)
                record = sequence.finish()
                self.assertEqual(phase_env.ERROR, record["outcome"], record)
                self.assertTrue(record["phases"][0]["evidence_error"])
                self.assertIsNone(record["phases"][0]["unexpected_requests"])

    def test_ac1_isolated_compat_phase_sends_zero_default_usage_requests(self):
        phase_env = self.phase_env()
        sequence, stats, compat = self.run_two_phase_sequence(self.sequence_base("ac1"))
        record = sequence.finish()
        stats_record, compat_record = record["phases"]
        self.assertEqual(
            '{"token": "synthetic-stats-token"}',
            phase_env.credentials_path(stats_record["credential_root"]).read_text(),
        )
        self.assertEqual([], compat_record["unexpected_requests"])
        self.assertFalse(phase_env.credentials_path(compat_record["credential_root"]).exists())
        self.assertTrue(all(check["ok"] for check in record["checks"]), record["checks"])
        self.assertEqual("GREEN", record["outcome"], record["reason"])

    def test_ac2_shared_credential_root_is_contamination_error(self):
        phase_env = self.phase_env()
        sequence, stats, compat = self.run_two_phase_sequence(self.sequence_base("ac2"), share_credential_root=True)
        self.assertEqual(stats.credential_root, compat.credential_root)
        record = sequence.finish()
        [entry] = record["phases"][1]["unexpected_requests"]
        self.assertEqual(phase_env.DEFAULT_USAGE_URL, entry["url"])
        self.assertEqual("ERROR", record["outcome"])
        self.assertIn("phase contamination", record["reason"])
        self.assertIn("the run or the oracle broke; no judgement possible", record["reason"])
        # A green child exit alone cannot produce acceptance: the exit checks
        # are ok while the sequence is still reported as an ERROR.
        exits = [check for check in record["checks"] if check["name"].endswith("exit")]
        self.assertTrue(all(check["ok"] for check in exits), record["checks"])
        self.assertFalse(all(check["ok"] for check in record["checks"]))

    def test_ac3_swallowed_refusal_visible_behind_green_exit(self):
        phase_env = self.phase_env()
        sequence = phase_env.PhaseSequence("acceptance", self.sequence_base("ac3"))
        compat = sequence.phase("compat")
        phase_env.write_synthetic_credentials(compat.credential_root, "synthetic-compat-token")
        result = compat.run([sys.executable, "-c", COMPAT_PHASE_SCRIPT, "call"])
        self.assertEqual(0, result.returncode, result.stderr)
        record = sequence.finish()
        [entry] = record["phases"][0]["unexpected_requests"]
        self.assertEqual(phase_env.DEFAULT_USAGE_URL, entry["url"])
        self.assertEqual("ERROR", record["outcome"], record["reason"])

    def test_ac13_swallowed_teardown_refusal_still_error(self):
        phase_env = self.phase_env()
        sequence = phase_env.PhaseSequence("acceptance", self.sequence_base("ac13"))
        compat = sequence.phase("compat")
        phase_env.write_synthetic_credentials(compat.credential_root, "synthetic-compat-token")
        result = compat.run([sys.executable, "-c", COMPAT_PHASE_SCRIPT, "teardown"])
        self.assertEqual(0, result.returncode, result.stderr)
        record = sequence.finish()
        [entry] = record["phases"][0]["unexpected_requests"]
        self.assertEqual(phase_env.DEFAULT_USAGE_URL, entry["url"])
        self.assertEqual("ERROR", record["outcome"], record["reason"])

    def test_ac4_phase_records_list_roots_identity_and_requests(self):
        sequence, stats, compat = self.run_two_phase_sequence(self.sequence_base("ac4"))
        record = sequence.finish()
        for phase_record, identity in zip(record["phases"], ("synthetic-stats", "synthetic-compat"), strict=False):
            for key in ("credential_root", "config_root", "state_root", "cache_root"):
                self.assertIn(key, phase_record)
                self.assertTrue(Path(phase_record[key]).is_dir(), (key, phase_record[key]))
            self.assertEqual(identity, phase_record["traffic_identity"])
            self.assertEqual([], phase_record["unexpected_requests"])
        self.assertNotEqual(record["phases"][0]["credential_root"], record["phases"][1]["credential_root"])

    def test_ac7_nonloopback_default_request_refused_and_recorded(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        phase_env = self.phase_env()

        class StatsRoute(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b'{"status": "ok"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), StatsRoute)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]

        sequence = phase_env.PhaseSequence("guard", self.sequence_base("ac7"))
        probe = sequence.phase("probe", allowed_endpoints=[f"127.0.0.1:{port}"])
        guard = phase_env.guard(probe.build_env())
        refused = ("http://192.0.2.10/usage", phase_env.DEFAULT_USAGE_URL)
        with patch("socket.socket", side_effect=AssertionError("a refused destination must never open a socket")):
            for url in refused:
                with self.assertRaises(phase_env.RefusedTransportError):
                    guard.get(url)
        self.assertEqual(set(refused), {entry["url"] for entry in probe.unexpected_requests()})
        with guard.get(f"http://127.0.0.1:{port}/stats") as response:
            self.assertEqual(200, response.status)
            self.assertEqual(b'{"status": "ok"}', response.read())
        self.assertEqual(2, len(probe.unexpected_requests()))

    def test_ac8_parent_home_and_oauth_sentinel_untouched(self):
        self.phase_env()
        base = self.sequence_base("ac8")
        parent_home = base / "parent-home"
        sentinel = parent_home / ".config" / "provider" / "oauth.json"
        sentinel.parent.mkdir(parents=True)
        sentinel.write_text('{"parent": "oauth"}')
        sentinel_bytes = sentinel.read_bytes()
        home_before = os.environ["HOME"]
        with patch.dict(os.environ, {"HOME": str(parent_home)}):
            sequence, stats, compat = self.run_two_phase_sequence(base / "sequence")
            record = sequence.finish()
            self.assertEqual(str(parent_home), os.environ["HOME"])
            self.assertEqual("GREEN", record["outcome"], record["reason"])
        self.assertEqual(home_before, os.environ["HOME"])
        self.assertEqual(sentinel_bytes, sentinel.read_bytes())
        for phase_record in record["phases"]:
            for key in ("credential_root", "config_root", "state_root", "cache_root"):
                root = Path(phase_record[key])
                self.assertFalse(root.is_relative_to(parent_home), (key, root))

    def transport_servers(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        observed = {"declared": [], "undeclared": []}

        class Undeclared(BaseHTTPRequestHandler):
            def do_GET(self):
                observed["undeclared"].append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"undeclared transport")

            def log_message(self, *args):
                pass

        other = ThreadingHTTPServer(("127.0.0.1", 0), Undeclared)

        class Declared(BaseHTTPRequestHandler):
            def do_GET(self):
                observed["declared"].append(self.path)
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", f"http://127.0.0.1:{other.server_port}/escaped")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"declared stats")

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Declared)
        for item in (other, server):
            thread = threading.Thread(target=item.serve_forever, daemon=True)
            thread.start()

            def stop(item=item, thread=thread):
                item.shutdown()
                thread.join(timeout=2)
                item.server_close()

            self.addCleanup(stop)
        return server, other, observed

    def test_ac7_redirect_to_undeclared_endpoint_is_refused_before_socket(self):
        phase_env = self.phase_env()
        server, other, observed = self.transport_servers()
        sequence = phase_env.PhaseSequence("redirect", self.sequence_base("redirect"))
        phase = sequence.phase("probe", allowed_endpoints=[f"127.0.0.1:{server.server_port}"])
        with self.assertRaises(phase_env.RefusedTransportError):
            phase_env.guard(phase.build_env()).get(f"http://127.0.0.1:{server.server_port}/redirect")
        self.assertEqual(["/redirect"], observed["declared"])
        self.assertEqual([], observed["undeclared"])
        [entry] = phase.unexpected_requests()
        self.assertEqual(f"http://127.0.0.1:{other.server_port}/escaped", entry["url"])
        self.assertTrue(entry["refused_before_socket"])
        self.assertEqual("ERROR", sequence.finish()["outcome"])

    def test_ac7_ambient_proxy_cannot_reroute_declared_loopback_request(self):
        import urllib.request

        phase_env = self.phase_env()
        server, other, observed = self.transport_servers()
        sequence = phase_env.PhaseSequence("proxy", self.sequence_base("proxy"))
        phase = sequence.phase("probe", allowed_endpoints=[f"127.0.0.1:{server.server_port}"])
        proxy = f"http://127.0.0.1:{other.server_port}"
        with (
            patch.dict(os.environ, {"http_proxy": proxy, "HTTP_PROXY": proxy, "no_proxy": "", "NO_PROXY": ""}),
            patch.object(urllib.request, "_opener", None),
        ):
            with phase_env.guard(phase.build_env()).get(f"http://127.0.0.1:{server.server_port}/stats") as response:
                self.assertEqual(b"declared stats", response.read())
        self.assertEqual(["/stats"], observed["declared"])
        self.assertEqual([], observed["undeclared"])
        self.assertEqual([], phase.unexpected_requests())

    def test_declared_refusal_log_cannot_be_overridden(self):
        phase_env = self.phase_env()
        base = self.sequence_base("declared-log-binding")
        sequence = phase_env.PhaseSequence("binding", base)
        compat = sequence.phase("compat")
        phase_env.write_synthetic_credentials(compat.credential_root, "synthetic-token")
        hidden_log = base / "undeclared-refusals.jsonl"
        with self.assertRaisesRegex(ValueError, "phase-owned"):
            compat.run(
                [sys.executable, "-c", COMPAT_PHASE_SCRIPT, "call"], env={"PHASE_UNEXPECTED_REQUESTS": str(hidden_log)}
            )
        self.assertFalse(hidden_log.exists(), "an undeclared log hid a swallowed refusal")

    def test_phase_extra_environment_preserves_home_and_declared_roots(self):
        sequence = self.phase_env().PhaseSequence("binding", self.sequence_base("declared-roots"))
        phase = sequence.phase("compat")
        for key in ("HOME", "PHASE_CREDENTIAL_ROOT", "PHASE_ALLOWED_ENDPOINTS"):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, "phase-owned"):
                    phase.build_env(**{key: "undeclared"})
                with self.assertRaisesRegex(ValueError, "phase-owned"):
                    phase.run([sys.executable, "-c", "raise SystemExit(0)"], env={key: "undeclared"})
        self.assertEqual("permitted", phase.build_env(CUSTOM_MARKER="permitted")["CUSTOM_MARKER"])


class OracleEnvInheritanceTests(unittest.TestCase):
    """AC9 guard: the oracle's optional env parameter changes no existing caller's behavior.

    Uses only code that exists before the change, so it passes both before and
    after the oracle grows the parameter.
    """

    def scratch(self, label):
        base = (
            Path(__file__).resolve().parent.parent
            / ".autocode"
            / "phase-env-tests"
            / f"{label}-{uuid.uuid4().hex[:10]}"
        )
        base.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        return base

    def test_ac9_oracle_default_env_inheritance_unchanged(self):
        base = self.scratch("ac9")
        with patch.dict(os.environ, {"PHASE_ENV_GUARD": "1"}):
            shown = oracle.run([sys.executable, "-c", "import os; print(os.environ.get('PHASE_ENV_GUARD'))"], base)
            self.assertEqual(0, shown.returncode, shown.stderr)
            self.assertEqual("1", shown.stdout.strip())
            tests = base / "tests"
            tests.mkdir()
            (tests / "__init__.py").touch()
            (tests / "test_marker.py").write_text(
                "import os\n"
                "import unittest\n"
                "\n"
                "\n"
                "class MarkerTests(unittest.TestCase):\n"
                "    def test_child_process_sees_the_marker(self):\n"
                "        self.assertEqual('1', os.environ.get('PHASE_ENV_GUARD'))\n"
            )
            suite = oracle.python_tests(base)
            self.assertEqual(0, suite.returncode, oracle.tail(suite))


# The suite gate discovers this module's TestCase classes, one test per process.


if __name__ == "__main__":
    unittest.main()
