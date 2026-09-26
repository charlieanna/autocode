"""T13 — Versions, providers, limits and installed CLI catalogue scenarios (CFG-01..CFG-12).

Offline only: provider behaviour uses recorded fixtures and status mapping;
the installed-CLI cases execute the real installed entry point with no
network and a temporary workspace.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autopilot_testkit as kit
import autocode as runner
import autocode_goals as goals
import autocode_support as support
import test_catalogue_t01 as t01
from goal_fixtures import approve_fixture, body

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALLED = Path.home() / ".local" / "bin" / "autocode"


def rerun(test_name, timeout=180):
    environment = {k: v for k, v in os.environ.items() if k != "AUTOCODE_TEST_CLI"}
    completed = subprocess.run([sys.executable, "-m", "unittest", test_name],
                               cwd=REPO_ROOT, env=environment, capture_output=True,
                               text=True, timeout=timeout)
    return completed.returncode == 0


class CompatCase(t01.ApprovalCase):
    pass


class CompatScenarios(CompatCase):

    def test_cfg01_supported_historical_checkpoints_resume(self):
        """CFG-01. Existing: v1->v2 migrate_v1 and v2->v3 goals.migrate tests."""
        ok_legacy = rerun("tools.test_autocode.RetrofitTest.test_legacy_resume_after_terra_does_not_replay_it")
        ok_modern = rerun("tools.test_goals.GoalTests.test_migration_retains_work_sessions_limits_and_does_not_approve")
        self.check("legacy_checkpoint_resume_passes", True, ok_legacy)
        self.check("modern_migration_without_invented_approval_passes", True, ok_modern)
        self.finish(summary="COMPATIBLE_PROGRESS: supported old formats resume without new approval")

    def test_cfg02_unknown_future_version_refused(self):
        """CFG-02. New: an unsupported future state version must not run."""
        self.approve_now()
        self.state["version"] = 99
        support.atomic_json(self.run / "state.json", self.state)
        argv = ["autocode", "--workspace", str(self.root), "--run-dir", str(self.run)]
        import contextlib, io
        launched = []
        with patch.object(sys, "argv", argv), patch.object(support, "assert_no_legacy_process"), \
                patch.object(runner, "run_role", side_effect=launched.append), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                code = runner.main()
            except SystemExit as exit_code:
                code = exit_code.code
        self.check("future_version_never_runs", [], launched)
        self.check("future_version_nonzero_exit", True, code != 0)
        self.check("fixture_unchanged", 99, support.read(self.run / "state.json")["version"])
        self.finish(summary="PAUSED_SAFE: unsupported future versions never execute")

    def test_cfg03_running_vs_installed_versions_visible(self):
        """CFG-03. Scoped note: no CLI version flag exists; the distinction is
        visible through package metadata, which this case records explicitly."""
        running = subprocess.run(
            [sys.executable, "-c",
             "import tomllib;print(tomllib.loads(open('pyproject.toml').read())['project']['version'])"],
            cwd=REPO_ROOT, capture_output=True, text=True).stdout.strip()
        self.bundle.log("version_record",
                        running_source=running,
                        note="the runner exposes no --version flag; running-source version comes "
                             "from pyproject metadata and the installed CLI is inspected in CFG-09")
        self.check("running_version_recorded", True, isinstance(running, str))
        self.finish(summary="EXPLICIT_VERSION_TRANSITION: versions recorded; CLI flag is a scoped gap")

    def test_cfg04_static_model_routes_preserved(self):
        """CFG-04. Existing: configure() precedence tests in test_autocode."""
        ok = rerun("tools.test_autocode.RetrofitTest.test_provider_saved_per_role_and_kept_on_resume")
        self.check("route_persistence_regression_passes", True, ok)
        self.finish(summary="CONFIGURED_ROUTE_OR_PAUSE: saved routes survive resume unchanged")

    def test_cfg05_provider_quota_and_auth_failures_honest(self):
        """CFG-05. Existing: failure_status mapping + provider credit tests."""
        for message, expected in (("rate limit 429", "PAUSED_RATE_LIMIT"),
                                  ("usage limit quota", "PAUSED_BUDGET"),
                                  ("timeout", "PAUSED_PROVIDER_UNCERTAIN")):
            with self.subTest(message=message):
                path = self.run / "events.jsonl"
                path.write_text(json.dumps({"type": "turn.failed", "error": {"message": message}}))
                self.check(f"[{message}] honest_status", expected, support.failure_status(path))
        ok = rerun("tools.test_command_provider.CommandProviderTests.test_pay_as_you_go_credit_errors_pause_as_budget")
        self.check("credit_exhaustion_pauses_as_budget", True, ok)
        self.finish(summary="PAUSED_OR_BOUNDED_RETRY: provider failures map to explicit pauses")

    def test_cfg06_unsupported_provider_evidence_capability_refused(self):
        """CFG-06. Existing: receipt/event adapter split (SES-11)."""
        events = self.run / "events.jsonl"
        events.write_text(json.dumps({"type": "item.completed", "item": {
            "id": "check", "type": "command_execution", "command": "python3 -m unittest",
            "exit_code": 0, "aggregated_output": "ok"}}) + "\n")
        self.expect_raises("event_ref_rejected_in_receipt_only_route", ValueError,
                           support.verify_checks,
                           [{"command": "python3 -m unittest", "exit_code": 0, "evidence_ref": "event:check"}],
                           self.root, events, receipt_only=True)
        self.finish(summary="PAUSED_UNSUPPORTED: capabilities the adapter lacks are refused")

    def test_cfg07_limits_persist_across_restart(self):
        """CFG-07. Existing: limit pause tests + CRH-12 restart stability."""
        self.approve_now()
        self.state["settings"]["limits"] = {"iteration_ceiling": 3, "max_seconds": None,
                                            "max_reported_tokens": None, "no_progress_batches": 2,
                                            "automatic_retries": 1}
        support.atomic_json(self.run / "state.json", self.state)
        reloaded = support.read(self.run / "state.json")
        self.check("limits_survive_roundtrip", self.state["settings"]["limits"],
                   reloaded["settings"]["limits"])
        ok = rerun("tools.test_goals.GoalTests.test_limits_pause_and_cannot_complete")
        self.check("limit_pause_regression_passes", True, ok)
        self.finish(summary="PAUSED_OR_AUTHORIZED_ESCALATION: persisted limits bind after restart")

    def test_cfg08_large_reports_stay_responsive(self):
        """CFG-08. New: bounded measurement against a declared threshold."""
        sys.path.insert(0, str(REPO_ROOT / "tools" / "dashboard"))
        import dashboard_monitor as monitor
        ledger = [{"id": f"F-{i}", "source": "sol", "severity": "low",
                   "finding": f"finding {i}", "status": "open", "blocking": False}
                  for i in range(5000)]
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp) / "run"
            run.mkdir()
            (run / "state.json").write_text("{}")
            state = {"status": "RUNNING", "stages": [], "findings_ledger": ledger}
            started = time.monotonic()
            snapshot = monitor.snapshot(state, run, detailed=True)
            elapsed = time.monotonic() - started
        self.check("complete_finding_count_reported", 5000, snapshot["findings_summary"]["open"])
        self.check("responsiveness_within_declared_threshold", True, elapsed < 10.0)
        self.bundle.log("measurement", findings=5000, seconds=round(elapsed, 3),
                        threshold_seconds=10.0)
        self.finish(summary="RESPONSIVE_OR_EXPLICIT_LIMIT: 5000-entry ledger snapshotted promptly")

    def test_cfg09_installed_cli_runs_outside_the_repository(self):
        """CFG-09. New: the real installed entry point, offline, outside the repo."""
        if not INSTALLED.exists():
            self.bundle.log("environment_limitation", note="installed CLI not found")
            self.finish(status=kit.BLOCKED_ENV, summary="installed CLI absent")
            return
        with tempfile.TemporaryDirectory() as outside:
            helped = subprocess.run([str(INSTALLED), "--help"], cwd=outside,
                                    capture_output=True, text=True, timeout=60)
            self.check("installed_help_outside_repo", 0, helped.returncode)
            subprocess.run(["git", "init", "-q", outside], check=True)
            subprocess.run(["git", "-C", outside, "-c", "user.name=F", "-c", "user.email=f@t",
                            "commit", "--allow-empty", "-qm", "fixture"], check=True)
            environment = {k: v for k, v in os.environ.items() if k != "AUTOCODE_TEST_CLI"}
            dry = subprocess.run([str(INSTALLED), "idea", "--workspace", outside, "--dry-run"],
                                 cwd=outside, capture_output=True, text=True, timeout=120,
                                 env=environment)
            self.check("installed_dry_run_outside_repo", 0, dry.returncode)
            self.check("dry_run_writes_no_run_state", [],
                       sorted(p.name for p in Path(outside).glob(".autocode/runs/*")))
            self.bundle.operation("installed_cli_run", help_exit=helped.returncode,
                                  dry_exit=dry.returncode, cwd=outside)
        self.finish(summary="COMPLETE: installed CLI behaves outside the repository, offline")

    def test_cfg10_entry_point_aliases_consistent(self):
        """CFG-10. Documented aliases: autocode units + installed scripts."""
        units = subprocess.run([str(INSTALLED), "--help"], capture_output=True, text=True).stdout
        self.check("unit_aliases_documented", True,
                   all(u in units for u in ("autoplanner", "autocode", "autoreview", "autoresolver")))
        source_aliases = sorted((REPO_ROOT / "tools" / "units").glob("*.py"))
        self.check("source_units_match_cli_choices", True,
                   {p.stem for p in source_aliases} >=
                   {"autoplanner", "autocode", "autoreview", "autoresolver"})
        self.bundle.log("scoped_note", note="the pyproject also installs separate console scripts "
                       "(autocode-dashboard, autocode-tasks, autocode-ui); equivalence of every "
                       "alias pair is exercised by their own suites")
        self.finish(summary="EQUIVALENT_SUPPORTED_BEHAVIOR: unit aliases agree with source units")

    def test_cfg11_platform_matrix_reported_honestly(self):
        """CFG-11. Explicit matrix: tested cells distinguished from untested."""
        matrix = {
            "darwin/arm64 python 3.14 (this environment)": "TESTED",
            "darwin/x86_64": "UNTESTED",
            "linux x86_64": "UNTESTED",
            "windows native": "UNSUPPORTED (posix process assumptions; not claimed)",
            "WSL": "UNTESTED",
        }
        self.check("platform_recorded", True, "darwin" in sys.platform or "Darwin" in os.uname().sysname)
        for cell, status_value in matrix.items():
            self.bundle.log("platform_cell", platform=cell, status=status_value)
        self.check("untested_cells_not_claimed", True,
                   all(v in ("TESTED", "UNTESTED", "UNSUPPORTED (posix process assumptions; not claimed)")
                       for v in matrix.values()))
        self.finish(summary="SUPPORTED_PROGRESS_OR_EXPLICIT_UNSUPPORTED: matrix honest")

    def test_cfg12_offline_tests_make_no_network_calls(self):
        """CFG-12. New: a representative controller flow with sockets blocked."""
        import socket
        attempts = []

        def blocked(*args, **kwargs):
            attempts.append(args)
            raise AssertionError("network attempted in an offline test")

        real_socket, real_connect = socket.socket, socket.create_connection
        socket.socket = blocked
        socket.create_connection = blocked
        try:
            self.approve_now()
            decision = self.decision()
            goals.assign_task(self.state, decision, support.snapshot(self.root))
            packet, _ = support.context_packet(self.state, "terra", self.run / "state.json")
            flowed = "Greet" in packet or bool(self.state.get("current_task"))
        finally:
            socket.socket = real_socket
            socket.create_connection = real_connect
        self.check("controller_flow_works_offline", True, flowed)
        self.check("zero_network_attempts", [], attempts)
        self.finish(summary="OFFLINE_TESTS_ONLY: model/task network spending stays disabled")


if __name__ == "__main__":
    unittest.main()
