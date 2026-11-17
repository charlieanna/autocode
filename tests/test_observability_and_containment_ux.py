"""Regression tests for #3733: runner check progress, plain machinery summaries, and macOS Seatbelt loopback UX."""

from __future__ import annotations

import contextlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_chat_activity as chat_activity
import autocode_chat_progress as chat_progress
import autocode_connections as connections
import autocode_containment_policy as containment_policy
import autocode_doctor as doctor
import autocode_mutation_check as mutation_check
import autocode_person_text as person_text
import autocode_recovery_history as recovery_history
import autocode_recovery_view as recovery_view
import autocode_run_view as run_view
import autocode_runner_check as runner_check
import autocode_status as status
import autocode_status_summary as status_summary
import autocode_tool_containment as tool_containment

from tests.test_liveness import inspection, metadata


class LiveRunnerProgressTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.run_dir = self.root / ".autocode" / "runs" / "r1"
        self.run_dir.mkdir(parents=True)
        self.state_path = self.run_dir / "state.json"
        self.state = {
            "version": 3,
            "task": "Verify live mutation check progress",
            "workspace": str(self.root),
            "run_dir": str(self.run_dir),
            "status": "RUNNING",
            "iteration": 1,
            "sessions": {},
            "stages": [],
            "next_stage": "tester",
            "settings": {},
        }

    def test_mutation_check_publishes_live_n_of_m_and_phase_progress_on_disk(self):
        """#3733 Part 1: mutation_check must update active_runner_check with live k/N progress on disk."""
        src = self.root / "calc.py"
        src.write_text("def check(x):\n    if x > 0:\n        return x + 1\n    return 0\n")
        test_dir = self.root / "tests"
        test_dir.mkdir()
        (test_dir / "test_calc.py").write_text("import calc\n\ndef test_positive():\n    assert calc.check(2) == 3\n")
        evidence = self.run_dir / "mutation-check"
        diff = "diff --git a/calc.py b/calc.py\n--- /dev/null\n+++ b/calc.py\n@@ -0,0 +1,4 @@\n+def check(x):\n+    if x > 0:\n+        return x + 1\n+    return 0\n"
        snapshots = []

        def fake_run(command, label, limit):
            saved = json.loads(self.state_path.read_text())
            snapshots.append((label, saved["active_runner_check"], list(saved.get("progress_messages") or [])))
            output = evidence / f"{label}.log"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("ok\n")
            if label == "unmutated":
                return {
                    "command": command,
                    "exit_code": 0,
                    "timed_out": False,
                    "duration_seconds": 0.1,
                    "output": str(output),
                    "results": {
                        "complete": True,
                        "total": 1,
                        "passed": ["tests/test_calc.py::test_positive"],
                        "failed": [],
                        "skipped": [],
                    },
                }
            return {
                "command": command,
                "exit_code": 1,
                "timed_out": False,
                "duration_seconds": 0.1,
                "output": str(output),
                "results": {
                    "complete": True,
                    "total": 1,
                    "passed": [],
                    "failed": ["tests/test_calc.py::test_positive"],
                    "skipped": [],
                },
            }

        with runner_check.track(
            self.state,
            self.run_dir,
            "regression_proof",
            "Preparing regression checks",
            status.persist,
        ) as progress:
            report = mutation_check.collect_selected_tests(
                "pytest",
                "python -m pytest -q",
                ["tests/test_calc.py::test_positive"],
                self.root,
                evidence,
                [("calc.py", "added")],
                lambda _chosen: diff,
                fake_run,
                enforcement=True,
                progress=progress,
            )

        self.assertTrue(report["collected"])
        self.assertGreaterEqual(len(snapshots), 3, "expected unmutated reference plus multiple mutants")
        ref_label, ref_check, _ = snapshots[0]
        self.assertEqual("unmutated", ref_label)
        self.assertEqual("mutation_reference", ref_check["phase"])
        self.assertEqual(0, ref_check["current"])
        self.assertGreaterEqual(ref_check["total"], 2)
        self.assertIn("verifying unmutated tests", ref_check["summary"])

        mutant_checks = [check for label, check, _ in snapshots[1:] if label.startswith("mutant-")]
        self.assertEqual(list(range(1, len(mutant_checks) + 1)), [c["current"] for c in mutant_checks])
        for idx, check in enumerate(mutant_checks, 1):
            self.assertEqual("mutation", check["phase"])
            self.assertEqual(ref_check["total"], check["total"])
            self.assertIn(f"({idx}/{ref_check['total']},", check["summary"])
            self.assertIn("calc.py:", check["summary"])
            self.assertIsInstance(check["elapsed_seconds"], float)

        # Intra-phase mutant updates refresh the latest Runner message in place without flooding history.
        final_messages = snapshots[-1][2]
        runner_entries = [m for m in final_messages if m.get("speaker") == "Runner"]
        self.assertLessEqual(len(runner_entries), 3)
        self.assertIn(f"({len(mutant_checks)}/{ref_check['total']},", runner_entries[-1]["text"])
        self.assertIn("no model is active", runner_entries[-1]["text"])

    def test_status_view_summary_and_chat_activity_reflect_supervised_runner_check(self):
        """#3733 Part 1: view.runner_check, view.summary, chat.progress and chat.activity expose live progress."""
        saved_meta = metadata()
        self.state["active_runner_check"] = {
            "stage": "regression_proof",
            "summary": "Checking changed-line mutations (12/80, 45s elapsed): chubby/server.py:93",
            "phase": "mutation",
            "current": 12,
            "total": 80,
            "elapsed_seconds": 45.2,
            "started_at": "2026-11-16T17:00:00Z",
            "updated_at": "2026-11-16T17:00:45Z",
            "command": "pytest -x -q tests/test_server.py",
            "output": str(self.run_dir / "mutation-check"),
            "supervision": saved_meta,
        }
        liveness = inspection(saved_meta)
        public = run_view.view(
            self.state,
            runner_check_liveness=liveness,
            controller={"kind": "running", "checked": True, "alive": True},
            workspace=self.root,
            run_dir=self.run_dir,
        )
        rc = public["runner_check"]
        self.assertEqual("mutation", rc["phase"])
        self.assertEqual(12, rc["current"])
        self.assertEqual(80, rc["total"])
        self.assertEqual(45.2, rc["elapsed_seconds"])
        self.assertEqual("supervised", rc["liveness"]["kind"])

        public["progress"] = {
            "headline": rc["summary"],
            "needs_you": "nothing needed from you",
            "tasks": {"total": 1, "label": "1 of 1 task done"},
            "requirements": {"total": 1, "label": "0 of 1 requirement checked"},
        }
        summary = status_summary.project(public)
        self.assertEqual("working", summary["state"])
        self.assertIn("(12/80, 45s elapsed)", summary["sentence"])

        lines = chat_progress.project(self.state, public)
        self.assertIn("(12/80, 45s elapsed)", lines[0])
        activity = chat_activity.project(self.state, public, self.run_dir)
        self.assertEqual(
            [{"kind": "command", "text": "pytest -x -q tests/test_server.py", "status": "running"}],
            activity,
        )


class HumanMachinerySummaryTests(unittest.TestCase):
    def test_internal_handoff_and_gap_enum_errors_use_human_prose_while_preserving_stop_reason(self):
        """#3733 Part 2: person-facing summary/history never leak raw handoff or gap_id-enum validator prose."""
        cases = (
            (
                "PAUSED_INVALID_OUTPUT",
                "Provider handoff must contain a JSON object",
                person_text.HANDOFF_SUMMARY,
            ),
            (
                "PAUSED_REPEATED_FAILURE",
                "Completed tester output was rejected (The missing-test FAIL leaves other offered changed lines "
                "undecided: L93 chubby/server.py:93 (abcdef0123456789), and 18 more in the gap_id enum); "
                "attempt archived.",
                person_text.GAP_ENUM_SUMMARY,
            ),
            (
                "PAUSED_INVALID_OUTPUT",
                "$: missing 'summary'; $.criterion_results[0]: unexpected fields: extra_key",
                person_text.SCHEMA_SUMMARY,
            ),
        )
        for pause_status, raw_reason, expected_summary in cases:
            with self.subTest(pause_status=pause_status, raw_reason=raw_reason[:40]):
                state = {
                    "status": pause_status,
                    "stop_reason": raw_reason,
                    "next_stage": "tester",
                    "iteration": 2,
                    "stages": [
                        {
                            "stage": "tester",
                            "finished_at": "2026-11-16T17:10:00Z",
                            "exit_code": 0,
                            "rejected": True,
                            "rejection_reason": raw_reason,
                        }
                    ],
                    "failure_history": {
                        "f1": {
                            "identity": {"stage": "tester", "error_class": "ValueError"},
                            "last_error": raw_reason,
                            "last_seen": "2026-11-16T17:10:00Z",
                            "count": 2,
                        }
                    },
                }
                public = run_view.view(state, workspace=Path("/project"), run_dir=Path("/project/.autocode/runs/r1"))
                # Exact technical stop_reason stays intact for diagnostics and existing tests.
                self.assertEqual(raw_reason, public["stop_reason"])
                self.assertEqual(raw_reason, public["recovery"]["saved_reason"])

                public["progress"] = {
                    "headline": "Paused",
                    "needs_you": "inspect the pause, then use --resume-paused",
                }
                summary = status_summary.project(public)
                self.assertEqual(expected_summary + ".", summary["sentence"])
                for leaked in (
                    "Provider handoff must contain",
                    "in the gap_id enum",
                    "leaves other offered changed lines undecided",
                    "$: missing",
                    "--resume-paused",
                ):
                    self.assertNotIn(leaked, summary["sentence"])
                    self.assertNotIn(leaked, public["chat"]["notice"] or "")
                    for entry in recovery_history.project(state):
                        self.assertNotIn(leaked, json.dumps(entry))


class MacOSSeatbeltLoopbackGuidanceTests(unittest.TestCase):
    @contextlib.contextmanager
    def simulated_seatbelt(self):
        with (
            patch.object(tool_containment, "sys", SimpleNamespace(platform="darwin")),
            patch.object(tool_containment, "sandbox_problem", return_value=None),
            patch.object(tool_containment, "backend_version", return_value="macOS 15.4"),
        ):
            yield

    def test_doctor_surfaces_seatbelt_loopback_note_for_opencode_and_codex_without_breaking_json_schema(self):
        """#3733 Part 3: doctor explains Seatbelt 127.0.0.1 loopback asymmetry while keeping codex containment None."""
        opencode_ok = subprocess.CompletedProcess(["opencode", "--version"], 0, "1.18.33", "")
        with self.simulated_seatbelt():
            containment = doctor.containment_status(opencode_ok)
            self.assertEqual(
                {"backend": "seatbelt", "backend_version": "macOS 15.4", "problem": None},
                containment,
            )
            opencode_row = doctor.containment_check(containment)
            self.assertEqual(("containment", doctor.OK), (opencode_row.name, opencode_row.status))
            self.assertIn("127.0.0.1", opencode_row.detail)
            self.assertIn("kilocode", opencode_row.detail)
            self.assertIn("--allow-uncontained-tools", opencode_row.detail)

            checks = doctor.engine_checks(
                lambda name: f"/usr/bin/{name}" if name == "codex" else None,
                lambda cmd, cwd=None: subprocess.CompletedProcess(cmd, 0, "", ""),
            )
            codex_row = next(c for c in checks if c.name == "engine:codex")
            self.assertEqual(doctor.OK, codex_row.status)
            self.assertIn("127.0.0.1", codex_row.detail)
            self.assertIn("Seatbelt", codex_row.detail)

    def test_connections_and_recovery_surface_clean_person_prose_for_seatbelt_loopback(self):
        """#3733 Part 3: Connections and recovery explain Seatbelt loopback blocks without CLI flags."""
        with (
            self.simulated_seatbelt(),
            patch.object(connections.shutil, "which", return_value="/usr/local/bin/tool"),
        ):
            codex_conn = connections.connection_view("codex")
            opencode_conn = connections.connection_view("opencode")
            kilo_conn = connections.connection_view("kilocode")
        self.assertIn("127.0.0.1", codex_conn["detail"])
        self.assertIn("Seatbelt", codex_conn["detail"])
        self.assertFalse(person_text.machinery(codex_conn["detail"]))
        self.assertIn("127.0.0.1", opencode_conn["detail"])
        self.assertNotIn("Seatbelt", kilo_conn["detail"])

        loopback_err = (
            "PermissionError: [Errno 1] Operation not permitted — socket.bind(('127.0.0.1', 0)) failed in test_rpc.py"
        )
        self.assertTrue(containment_policy.loopback_bind_blocked(loopback_err))
        self.assertFalse(
            containment_policy.loopback_bind_blocked("PermissionError: [Errno 13] Permission denied: '/etc'")
        )
        card = recovery_view.project(
            {"status": "PAUSED_JOB_FAILURE", "stop_reason": loopback_err, "next_stage": "tester"}
        )
        self.assertIn("127.0.0.1", card["what_happened"])
        self.assertIn("Seatbelt", card["what_happened"])
        self.assertFalse(person_text.machinery(card["what_happened"]))


if __name__ == "__main__":
    unittest.main()
