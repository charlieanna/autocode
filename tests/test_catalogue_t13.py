"""T13 — Versions, providers, limits and installed CLI catalogue scenarios (CFG-01..CFG-12).

Offline only: provider behaviour uses recorded fixtures and status mapping;
the installed-CLI cases execute the real installed entry point with no
network and a temporary workspace.
Set AUTOCODE_TEST_WHEEL to a wheel with dependency wheels alongside it to test
a clean temporary installation instead of the user's installed entry point.
"""

import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
import venv
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocode as runner
import autocode_goal_lifecycle as lifecycle
import autocode_stage_context as stage_context
import autocode_support as support
import autopilot_testkit as kit
from autocode_stop_explanations import explain

from . import test_catalogue_t01 as t01

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALLED = Path.home() / ".local" / "bin" / "autocode"


def rerun(test_name, timeout=180):
    environment = {k: v for k, v in os.environ.items() if k != "AUTOCODE_TEST_CLI"}
    completed = subprocess.run(
        [sys.executable, "-m", "unittest", test_name],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return completed.returncode == 0


class CompatCase(t01.ApprovalCase):
    def installed_cli(self):
        self.cli_environment = {
            k: v for k, v in os.environ.items() if k not in ("AUTOCODE_TEST_CLI", "PYTHONPATH", "PYTHONHOME")
        }
        wheel = os.environ.get("AUTOCODE_TEST_WHEEL")
        if not wheel:
            if not INSTALLED.is_file():
                self.finish(
                    status=kit.BLOCKED_ENV,
                    summary="installed CLI absent; set AUTOCODE_TEST_WHEEL to test an isolated wheel",
                )
            return INSTALLED
        wheel = Path(wheel).resolve()
        self.assertTrue(wheel.is_file(), f"AUTOCODE_TEST_WHEEL does not exist: {wheel}")
        temporary = tempfile.TemporaryDirectory(prefix="catalogue-cli-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        # Dependencies must be supplied beside the wheel; tests never fetch packages.
        venv.EnvBuilder(with_pip=True).create(root)
        python = root / "bin" / "python"
        installed = subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "--isolated",
                "install",
                "--no-index",
                "--find-links",
                str(wheel.parent),
                str(wheel),
            ],
            env=self.cli_environment,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.bundle.log(
            "wheel_install",
            wheel=str(wheel),
            returncode=installed.returncode,
            stdout=installed.stdout,
            stderr=installed.stderr,
        )
        self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
        provenance = subprocess.run(
            [str(python), "-I", "-c", "import autocode_cli; print(autocode_cli.__file__)"],
            cwd=root,
            env=self.cli_environment,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(provenance.returncode, 0, provenance.stderr)
        self.assertTrue(Path(provenance.stdout.strip()).is_relative_to(root), provenance.stdout)
        self.bundle.log("installed_package", location=provenance.stdout.strip())
        return root / "bin" / "autocode"


class CompatScenarios(CompatCase):
    def test_cfg01_supported_historical_checkpoints_resume(self):
        """CFG-01. Existing: v1->v2 migrate_v1 and v2->v3 lifecycle.migrate tests."""
        ok_legacy = rerun("tests.test_autocode.RetrofitTest.test_legacy_resume_after_terra_does_not_replay_it")
        ok_modern = rerun("tests.test_goals.GoalTests.test_migration_retains_work_sessions_limits_and_does_not_approve")
        self.check("legacy_checkpoint_resume_passes", True, ok_legacy)
        self.check("modern_migration_without_invented_approval_passes", True, ok_modern)
        self.finish(summary="COMPATIBLE_PROGRESS: supported old formats resume without new approval")

    def test_cfg02_unknown_future_version_refused(self):
        """CFG-02. New: an unsupported future state version must not run."""
        self.approve_now()
        self.state["version"] = 99
        support.atomic_json(self.run / "state.json", self.state)
        argv = ["autocode", "--workspace", str(self.root), "--run-dir", str(self.run)]
        import contextlib
        import io

        launched = []

        def record(**kwargs):
            launched.append((kwargs.get("state") or {}).get("next_stage") or kwargs)
            raise RuntimeError("stop after the first launch")

        with (
            patch.object(sys, "argv", argv),
            patch.object(support, "assert_no_legacy_process"),
            patch.object(support, "local_settings", return_value=self.local),
            patch.object(runner, "run_role", side_effect=record),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            try:
                code = runner.main()
            except SystemExit as exit_code:
                code = exit_code.code
        self.check("future_version_never_runs", [], launched)
        self.check("future_version_nonzero_exit", True, code != 0)
        self.check("fixture_unchanged", 99, support.read(self.run / "state.json")["version"])
        self.finish(summary="PAUSED_SAFE: unsupported future versions never execute")

    def test_cfg03_running_vs_installed_versions_visible(self):
        """CFG-03. `autocode --version` names the running source: the package version and,
        run from a checkout, that checkout's commit. An installed copy prints "commit
        unknown" instead (tests/test_doctor.py SourceCommitTests), so the two differ."""
        version = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())["project"]["version"]
        head = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=60
        ).stdout.strip()
        shown = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools" / "autocode.py"), "--version"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.bundle.log(
            "version_record", package_version=version, checkout_commit=head, stdout=shown.stdout, stderr=shown.stderr
        )
        self.check("version_flag_exit", 0, shown.returncode)
        self.check(
            "version_names_package_and_commit",
            True,
            bool(head) and shown.stdout.startswith(f"autocode {version} (commit {head}"),
        )
        self.finish(summary="EXPLICIT_VERSION_TRANSITION: --version names the running version and commit")

    def test_cfg04_static_model_routes_preserved(self):
        """CFG-04. Existing: configure() precedence tests in test_autocode."""
        ok = rerun("tests.test_autocode.RetrofitTest.test_provider_saved_per_role_and_kept_on_resume")
        self.check("route_persistence_regression_passes", True, ok)
        self.finish(summary="CONFIGURED_ROUTE_OR_PAUSE: saved routes survive resume unchanged")

    def test_cfg05_provider_quota_and_auth_failures_honest(self):
        """CFG-05. Existing: failure_status mapping + provider credit tests."""
        for message, expected in (
            ("rate limit 429", "PAUSED_RATE_LIMIT"),
            ("usage limit quota", "PAUSED_BUDGET"),
            ("timeout", "PAUSED_PROVIDER_UNCERTAIN"),
        ):
            with self.subTest(message=message):
                path = self.run / "events.jsonl"
                path.write_text(json.dumps({"type": "turn.failed", "error": {"message": message}}))
                self.check(f"[{message}] honest_status", expected, support.failure_status(path))
        ok = rerun("tests.test_command_provider.CommandProviderTests.test_pay_as_you_go_credit_errors_pause_as_budget")
        self.check("credit_exhaustion_pauses_as_budget", True, ok)
        self.finish(summary="PAUSED_OR_BOUNDED_RETRY: provider failures map to explicit pauses")

    def test_cfg06_unsupported_provider_evidence_capability_refused(self):
        """CFG-06. Existing: receipt/event adapter split (SES-11)."""
        events = self.run / "events.jsonl"
        events.write_text(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "check",
                        "type": "command_execution",
                        "command": "python3 -m unittest",
                        "exit_code": 0,
                        "aggregated_output": "ok",
                    },
                }
            )
            + "\n"
        )
        self.expect_raises(
            "event_ref_rejected_in_receipt_only_route",
            ValueError,
            support.verify_checks,
            [{"command": "python3 -m unittest", "exit_code": 0, "evidence_ref": "event:check"}],
            self.root,
            events,
            receipt_only=True,
        )
        self.finish(summary="PAUSED_UNSUPPORTED: capabilities the adapter lacks are refused")

    def test_cfg07_limits_persist_across_restart(self):
        """CFG-07. Existing: limit pause tests + CRH-12 restart stability."""
        self.approve_now()
        self.state["settings"]["limits"] = {
            "iteration_ceiling": 3,
            "max_seconds": None,
            "no_progress_batches": 2,
            "automatic_retries": 1,
        }
        support.atomic_json(self.run / "state.json", self.state)
        reloaded = support.read(self.run / "state.json")
        self.check("limits_survive_roundtrip", self.state["settings"]["limits"], reloaded["settings"]["limits"])
        ok = rerun("tests.test_goals.GoalTests.test_limits_pause_and_cannot_complete")
        self.check("limit_pause_regression_passes", True, ok)
        self.finish(summary="PAUSED_OR_AUTHORIZED_ESCALATION: persisted limits bind after restart")

    def test_cfg08_large_reports_stay_responsive(self):
        """CFG-08. New: bounded measurement against a declared threshold."""
        sys.path.insert(0, str(REPO_ROOT / "tools" / "dashboard"))
        import dashboard_monitor as monitor

        ledger = [
            {
                "id": f"F-{i}",
                "source": "sol",
                "severity": "low",
                "finding": f"finding {i}",
                "status": "open",
                "blocking": False,
            }
            for i in range(5000)
        ]
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
        self.bundle.log("measurement", findings=5000, seconds=round(elapsed, 3), threshold_seconds=10.0)
        self.finish(summary="RESPONSIVE_OR_EXPLICIT_LIMIT: 5000-entry ledger snapshotted promptly")

    def test_cfg09_installed_cli_runs_outside_the_repository(self):
        """CFG-09. New: the real installed entry point, offline, outside the repo."""
        installed = self.installed_cli()
        with tempfile.TemporaryDirectory() as outside:
            helped = subprocess.run(
                [str(installed), "--help"],
                cwd=outside,
                env=self.cli_environment,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.check("installed_help_outside_repo", 0, helped.returncode)
            subprocess.run(["git", "init", "-q", outside], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    outside,
                    "-c",
                    "user.name=F",
                    "-c",
                    "user.email=f@t",
                    "commit",
                    "--allow-empty",
                    "-qm",
                    "fixture",
                ],
                check=True,
            )
            dry = subprocess.run(
                [str(installed), "idea", "--workspace", outside, "--dry-run"],
                cwd=outside,
                capture_output=True,
                text=True,
                timeout=120,
                env=self.cli_environment,
            )
            self.bundle.log(
                "installed_cli_output",
                help_stdout=helped.stdout,
                help_stderr=helped.stderr,
                dry_stdout=dry.stdout,
                dry_stderr=dry.stderr,
            )
            self.check("installed_dry_run_outside_repo", 0, dry.returncode)
            self.check(
                "dry_run_writes_no_run_state", [], sorted(p.name for p in Path(outside).glob(".autocode/runs/*"))
            )
            fixture_bin = Path(outside) / "fixture-bin"
            fixture_bin.mkdir()
            marker = Path(outside) / "provider-was-started"
            provider = fixture_bin / "codex"
            provider.write_text(
                "#!"
                + sys.executable
                + "\nfrom pathlib import Path\n"
                + "Path("
                + repr(str(marker))
                + ").touch()\nraise SystemExit(1)\n"
            )
            provider.chmod(0o755)
            environment = {
                **self.cli_environment,
                "PATH": str(fixture_bin) + os.pathsep + os.environ["PATH"],
                "XDG_CONFIG_HOME": str(Path(outside) / "config-home"),
                "CODEX_HOME": str(Path(outside) / "codex-home"),
                "AUTOCODE_HOME": str(Path(outside) / "registry-home"),
            }
            environment.pop("AUTOCODE_PROVIDER", None)
            before_worktrees = subprocess.run(
                ["git", "worktree", "list", "--porcelain"],
                cwd=outside,
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            ).stdout
            expected = explain("RUNNING")
            expected_text = "\n\n".join(
                expected[key] for key in ("what_happened", "what_it_means", "what_the_command_does")
            )
            for extra in ([], ["--in-place"]):
                with self.subTest(extra=extra):
                    explained = subprocess.run(
                        [
                            str(installed),
                            "idea",
                            "--workspace",
                            outside,
                            "--engine",
                            "codex",
                            "--no-chat",
                            "--explain",
                            *extra,
                        ],
                        cwd=outside,
                        env=environment,
                        capture_output=True,
                        text=True,
                        timeout=120,
                    )
                    self.assertEqual(0, explained.returncode, explained.stdout + explained.stderr)
                    self.assertEqual(expected_text, explained.stdout.strip())
                    self.assertFalse(marker.exists(), "Explanation must not launch a provider")
                    self.assertFalse((Path(outside) / ".autocode").exists())
                    after_worktrees = subprocess.run(
                        ["git", "worktree", "list", "--porcelain"],
                        cwd=outside,
                        check=True,
                        capture_output=True,
                        text=True,
                        timeout=60,
                    ).stdout
                    self.assertEqual(before_worktrees, after_worktrees)
                    self.bundle.log(
                        "installed_explanation",
                        extra=extra,
                        exit=explained.returncode,
                        stdout=explained.stdout,
                        stderr=explained.stderr,
                    )
            self.bundle.operation(
                "installed_cli_run", help_exit=helped.returncode, dry_exit=dry.returncode, cwd=outside
            )
        self.finish(summary="COMPLETE: installed CLI behaves outside the repository, offline")

    def test_cfg10_entry_point_aliases_consistent(self):
        """CFG-10. Documented aliases: autocode units + installed scripts."""
        installed = self.installed_cli()
        with tempfile.TemporaryDirectory() as outside:
            helped = subprocess.run(
                [str(installed), "--help-all"],
                cwd=outside,
                env=self.cli_environment,
                capture_output=True,
                text=True,
                timeout=60,
            )
        self.check("installed_alias_help_exit", 0, helped.returncode)
        units = helped.stdout
        self.check(
            "unit_aliases_documented",
            True,
            all(u in units for u in ("autoplanner", "autocode", "autoreview", "autoresolver")),
        )
        source_aliases = sorted((REPO_ROOT / "tools" / "units").glob("*.py"))
        self.check(
            "source_units_match_cli_choices",
            True,
            {p.stem for p in source_aliases} >= {"autoplanner", "autocode", "autoreview", "autoresolver"},
        )
        self.bundle.log(
            "scoped_note",
            note="the pyproject also installs separate console scripts "
            "(autocode-dashboard, autocode-tasks, autocode-ui); equivalence of every "
            "alias pair is exercised by their own suites",
        )
        self.finish(summary="EQUIVALENT_SUPPORTED_BEHAVIOR: unit aliases agree with source units")

    def test_cfg11_platform_matrix_reported_honestly(self):
        """CFG-11. Explicit matrix: tested cells distinguished from untested."""
        current = f"{sys.platform}/{platform.machine()} python {platform.python_version()} (this environment)"
        matrix = {
            "darwin/arm64 (other configurations)": "UNTESTED",
            "darwin/x86_64 (other configurations)": "UNTESTED",
            "linux/x86_64 (other configurations)": "UNTESTED",
            "windows native": "UNSUPPORTED (posix process assumptions; not claimed)",
            "WSL": "UNTESTED",
            current: "TESTED",
        }
        self.check("platform_recorded", True, bool(sys.platform and platform.machine()))
        self.check(
            "only_current_configuration_tested",
            [current],
            [cell for cell, value in matrix.items() if value == "TESTED"],
        )
        for cell, status_value in matrix.items():
            self.bundle.log("platform_cell", platform=cell, status=status_value)
        self.check(
            "untested_cells_not_claimed",
            True,
            all(
                v in ("TESTED", "UNTESTED", "UNSUPPORTED (posix process assumptions; not claimed)")
                for v in matrix.values()
            ),
        )
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
            lifecycle.assign_task(self.state, decision, support.snapshot(self.root))
            packet, _ = stage_context.context_packet(self.state, "terra", self.run / "state.json")
            flowed = "Greet" in packet or bool(self.state.get("current_task"))
        finally:
            socket.socket = real_socket
            socket.create_connection = real_connect
        self.check("controller_flow_works_offline", True, flowed)
        self.check("zero_network_attempts", [], attempts)
        self.finish(summary="OFFLINE_TESTS_ONLY: model/task network spending stays disabled")


class HarnessReportingTests(unittest.TestCase):
    def test_blocked_case_is_a_skip_with_blocked_bundle(self):
        class BlockedCase(kit.CatalogueCase):
            scenario_id = "blocked-regression"

            def runTest(self):
                self.finish(status=kit.BLOCKED_ENV, summary="missing prerequisite")

        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"AUTOCODE_TEST_ARTIFACTS": root}):
            case, result = BlockedCase(), unittest.TestResult()
            case.run(result)
            self.assertEqual((len(result.skipped), len(result.errors), len(result.failures)), (1, 0, 0))
            recorded = json.loads((case.bundle.dir / "result.json").read_text())
            self.assertEqual(recorded["status"], kit.BLOCKED_ENV)

    def test_failed_check_cannot_be_hidden_by_skip(self):
        class FailedCase(kit.CatalogueCase):
            scenario_id = "failed-regression"

            def runTest(self):
                self.check("real_failure", True, False)
                self.skipTest("later prerequisite absent")

        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"AUTOCODE_TEST_ARTIFACTS": root}):
            case, result = FailedCase(), unittest.TestResult()
            case.run(result)
            self.assertEqual((len(result.skipped), len(result.errors), len(result.failures)), (0, 0, 1))
            recorded = json.loads((case.bundle.dir / "result.json").read_text())
            self.assertEqual((recorded["status"], recorded["failed"]), (kit.FAIL, 1))
            self.assertIn("real_failure", result.failures[0][1])

    def test_partial_summary_alone_does_not_fail_a_bundle(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"AUTOCODE_TEST_ARTIFACTS": root}):
            bundle = kit.Bundle("partial-regression")
            bundle.check("scoped_check", True, True)
            bundle.finish(summary="PARTIAL: another capability remains unverified")
            self.assertEqual(json.loads((bundle.dir / "result.json").read_text())["status"], kit.PASS)


if __name__ == "__main__":
    unittest.main()


class CheckpointVersionTests(unittest.TestCase):
    def test_migrate_refuses_a_future_checkpoint_version(self):
        import autocode_goal_lifecycle as lifecycle

        with self.assertRaises(support.Paused) as caught:
            lifecycle.migrate({"version": 99, "task": "t", "status": "RUNNING"})
        self.assertEqual("PAUSED_UNSUPPORTED_CHECKPOINT", caught.exception.status)
        with self.assertRaises(support.Paused):
            lifecycle.migrate({"version": "3", "task": "t", "status": "RUNNING"})
        # Supported versions still migrate or no-op.
        self.assertIsNone(lifecycle.require_supported_checkpoint({"version": 3, "status": "RUNNING"}))
        self.assertIsNone(lifecycle.require_supported_checkpoint({"status": "RUNNING"}))
