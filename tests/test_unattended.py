"""Isolated tests for the unattended AutoCode wrapper: no model calls."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import autocode_unattended as unattended


class RefusalTests(unittest.TestCase):
    def test_operator_flags_and_abbreviations_are_refused(self):
        for argv in (["--approve-goal", "r1:abc"], ["--approve-g=r1:abc"], ["--resume-paused"],
                     ["--answer", "Q1=yes"], ["--delegate-all"], ["--accept-completion"],
                     ["--retry-failed-stage"], ["--feedback", "x"], ["--chat"]):
            with self.subTest(argv=argv):
                self.assertIsNotNone(unattended.refused(["--run-dir", "r", *argv]))

    def test_operator_subcommands_are_refused(self):
        self.assertIn("intervention", unattended.refused(["intervention", "submit"]))
        self.assertIn("program", unattended.refused(["program", "run"]))

    def test_run_and_status_arguments_are_allowed(self):
        for argv in (["Build a CLI", "--workspace", "/w", "--engine", "opencode"],
                     ["--run-dir", "r", "--status"], ["--no-chat", "--max-iterations", "5"],
                     ["task", "--", "--approve-goal"]):
            with self.subTest(argv=argv):
                self.assertIsNone(unattended.refused(argv))


class RunTests(unittest.TestCase):
    def run_wrapper(self, argv, exit_code):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "calls"
            fake = Path(tmp) / "fake_autocode.py"
            fake.write_text(textwrap.dedent(f"""
                import sys
                open({str(log)!r}, "a").write(" ".join(sys.argv[1:]) + "\\n")
                if "--status" in sys.argv:
                    print('{{"status": "PAUSED"}}')
                    raise SystemExit(0)
                assert sys.stdin.read() == ""
                print("Run: /w/.autocode/runs/r1")
                raise SystemExit({exit_code})
            """))
            out = io.StringIO()
            env = {"AUTOCODE_UNATTENDED_COMMAND": f"{sys.executable} {fake}"}
            with patch.dict(os.environ, env), contextlib.redirect_stdout(out):
                rc = unattended.run(argv)
            return rc, out.getvalue(), log.read_text().splitlines() if log.exists() else []

    def test_stop_reports_status_and_tells_caller_to_stop(self):
        rc, out, calls = self.run_wrapper(["Build it", "--workspace", "/w"], 2)
        self.assertEqual(rc, 2)
        self.assertEqual(calls[0], "Build it --workspace /w --no-chat")
        self.assertEqual(calls[1], "--run-dir /w/.autocode/runs/r1 --status --workspace /w")
        self.assertIn('"status": "PAUSED"', out)
        self.assertIn("AUTOCODE STOPPED FOR THE OPERATOR (exit 2)", out)

    def test_success_has_no_stop_notice(self):
        rc, out, _ = self.run_wrapper(["Build it"], 0)
        self.assertEqual(rc, 0)
        self.assertNotIn("STOPPED", out)

    def test_refused_call_launches_nothing(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc, _, calls = self.run_wrapper(["--run-dir", "r", "--resume-paused"], 0)
        self.assertEqual((rc, calls), (2, []))
        self.assertIn("refused", err.getvalue())


class CompletionNoticeTests(unittest.TestCase):
    def test_completed_run_points_to_analyze(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            (run_dir / "state.json").write_text(json.dumps({"status": "TASK_COMPLETE"}))
            fake = Path(tmp) / "fake.py"
            fake.write_text(f"print('Run: {run_dir}')\n")
            out = io.StringIO()
            with patch.dict(os.environ, {"AUTOCODE_UNATTENDED_COMMAND": f"{sys.executable} {fake}"}), \
                    contextlib.redirect_stdout(out):
                self.assertEqual(0, unattended.run(["Build it"]))
            self.assertIn(f"autocode-unattended --analyze --run-dir {run_dir}", out.getvalue())


class AnalyzeTests(unittest.TestCase):
    def git(self, cwd, *args):
        return subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                              check=True, capture_output=True, text=True).stdout.strip()

    def test_report_covers_outcome_findings_stages_and_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            self.git(workspace, "init", "-q")
            (workspace / "app.py").write_text("print('hi')\n")
            self.git(workspace, "add", ".")
            self.git(workspace, "commit", "-qm", "base")
            base = self.git(workspace, "rev-parse", "HEAD")
            (workspace / "app.py").write_text("print('hello')\n")
            self.git(workspace, "commit", "-qam", "Builder change")
            (workspace / "app.py").write_text("print('hello, world')\n")
            (workspace / "new.py").write_text("x = 1\n")
            run_dir = workspace / ".autocode" / "runs" / "r1"
            run_dir.mkdir(parents=True)
            (workspace / ".autocode" / "task-workspace.json").write_text(json.dumps({"base_commit": base}))
            (run_dir / "state.json").write_text(json.dumps({
                "status": "TASK_COMPLETE", "task": "Greet", "workspace": str(workspace), "iteration": 3,
                "goal_contract": {"body": {"intended_outcome": "Greets the world", "acceptance_criteria": [
                    {"id": "C1", "criterion": "Prints a greeting", "verification_method": "run app.py"}]}},
                "last_decision": {"acceptance_criteria": [
                    {"id": "C1", "criterion": "Prints a greeting", "status": "verified", "evidence": "ran it"}]},
                "validation": {"source_revision": "abc",
                               "criterion_results": [{"id": "C1", "status": "PASS"}]},
                "human_reviews": {"C1": {"actor": "user_cli", "criterion": "C1"}},
                "findings_ledger": [{"id": "F1", "status": "resolved", "severity": "high", "source": "validator",
                                     "finding": "Missing comma"}],
                "stages": [{"stage": "terra", "iteration": 1, "output": "terra-01.json"},
                           {"stage": "orchestrator", "iteration": 1},
                           {"stage": "sol", "role": "sol", "duration_seconds": 30.0,
                            "metrics": {"provider_tokens": {"input_tokens": 900, "output_tokens": 90}}},
                           {"stage": "sol_report_repair", "role": "sol", "duration_seconds": 5.0,
                            "metrics": {"provider_tokens": {"input_tokens": 100, "output_tokens": 10}}}]}))
            out_dir = workspace.parent / (workspace.name + "-analysis")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = unattended.run(["--analyze", "--run-dir", str(run_dir), "--out", str(out_dir)])
            report = out.getvalue()
            self.assertEqual(rc, 0)
            for expected in ("TASK_COMPLETE", "Greets the world",
                             "**C1** [verified, validator: PASS, human-reviewed]: Prints a greeting",
                             "Verification: run app.py", "Evidence: ran it", "F1 [resolved, high, validator",
                             "| terra |", "`terra-01.json`", "app.py", "Untracked files (new, not committed): `new.py`", "Builder change",
                             "| sol | 2 | 35.0 | 1000/100 |", "| **total** | 3 | 35.0 | 1000/100 |",
                             "Report-format repair calls: 1."):
                self.assertIn(expected, report)
            self.assertNotIn(".autocode/", report.split("Untracked files (new, not committed):")[1].splitlines()[0])
            diff = (out_dir / "changes.diff").read_text()
            self.assertIn("hello, world", diff)
            self.assertIn("+x = 1", diff)
            self.assertTrue((out_dir / "analysis.md").is_file())

    def test_analyze_takes_only_its_own_arguments(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            unattended.run(["--analyze", "--run-dir", "r", "--approve-goal", "t"])


if __name__ == "__main__":
    unittest.main()
