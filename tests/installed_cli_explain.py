"""Installed CLI explanation smoke; run with fresh-venv Python -I outside checkout.

No provider exists in the disposable configuration. Every supported engine name
on PATH is a fail-closed marker shim, so these read-only checks cannot call models.
"""
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest

from autocode_cli import autocode, autocode_run_finder, autocode_status_command

UNAVAILABLE_PROVIDER = "explain_must_not_dispatch"
OBSERVATIONS = []


def snapshot(root):
    """Bytes, mode, modification time and every path, including Git and run files."""
    result = {}
    for path in [root, *sorted(root.rglob("*"))]:
        relative = str(path.relative_to(root))
        info = path.lstat()
        entry = {"mode": stat.S_IMODE(info.st_mode), "mtime_ns": info.st_mtime_ns}
        if path.is_symlink():
            entry.update(kind="symlink", target=os.readlink(path))
        elif path.is_file():
            entry.update(kind="file", sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        else:
            entry.update(kind="directory")
        result[relative] = entry
    return result


class InstalledExplainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        prefix = Path(sys.prefix).resolve()
        for module in (autocode, autocode_run_finder, autocode_status_command):
            origin = Path(module.__file__).resolve()
            if not origin.is_relative_to(prefix):
                raise AssertionError(f"Expected installed module under {prefix}, found {origin}")
        checkout = next((path for path in Path(__file__).resolve().parents if (path / ".git").exists()), None)
        if checkout is not None and Path.cwd().resolve().is_relative_to(checkout):
            raise AssertionError("Run this smoke outside the source checkout")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="autocode-installed-explain-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        git = shutil.which("git")
        self.assertIsNotNone(git, "Git is needed to make the disposable workspace")
        for args in (("init", "-q"), ("config", "maintenance.auto", "false")):
            subprocess.run([git, *args], cwd=self.project, check=True, capture_output=True)
        (self.project / "app.txt").write_text("original delivery\n")
        subprocess.run([git, "add", "app.txt"], cwd=self.project, check=True, capture_output=True)
        subprocess.run([git, "-c", "user.name=Wheel smoke", "-c", "user.email=wheel@example.test",
                        "commit", "-qm", "original"], cwd=self.project, check=True, capture_output=True)
        self.dispatch = self.root / "engine-dispatch.txt"
        self.dispatch.write_text("")
        binaries = self.root / "bin"
        binaries.mkdir()
        (binaries / "git").symlink_to(git)
        for name in ("opencode", "codex", "kilo", "kilocode", "claude"):
            shim = binaries / name
            shim.write_text("#!/bin/sh\nprintf '%s\\n' \"$0 $*\" >> "
                            + shlex.quote(str(self.dispatch)) + "\nexit 99\n")
            shim.chmod(0o755)
        for name in ("home", "config", "registry"):
            (self.root / name).mkdir()
        # Deliberately do not inherit credentials, provider selection, PYTHONPATH,
        # user configuration or a real engine executable from the invoking session.
        self.environment = {"PATH": str(binaries) + os.pathsep + os.defpath,
                            "HOME": str(self.root / "home"),
                            "XDG_CONFIG_HOME": str(self.root / "config"),
                            "AUTOCODE_HOME": str(self.root / "registry"), "LANG": "C"}

    def saved_run(self, name, status, reason, *, created, completed=None):
        run = self.project / ".autocode" / "runs" / name
        run.mkdir(parents=True)
        state = {"version": 3, "task": "Explain this retained stop", "workspace": str(self.project),
                 "status": status, "stop_reason": reason, "iteration": 1, "sessions": {},
                 "stages": [], "history": [], "next_stage": "astra_plan", "created_at": created,
                 "settings": {"provider": UNAVAILABLE_PROVIDER, "engine": "codex"}}
        if completed is not None:
            state["completed_at"] = completed
        (run / "state.json").write_text(json.dumps(state, indent=2) + "\n")
        (run / "retained-artifact.txt").write_text("This retained artifact must not change.\n")
        return run

    def invoke(self, *args):
        before = snapshot(self.root)
        command = [sys.executable, "-I", "-m", "autocode_cli.autocode", "--no-chat",
                   "--workspace", str(self.project), "--provider", UNAVAILABLE_PROVIDER, *map(str, args)]
        started = time.monotonic()
        completed = subprocess.run(command, cwd=self.root, env=self.environment,
                                   capture_output=True, text=True, timeout=30)
        after = snapshot(self.root)
        changed = sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))
        OBSERVATIONS.append({"test": self.id(), "argv": command, "cwd": str(self.root),
                             "seconds": time.monotonic() - started, "exit_code": completed.returncode,
                             "stdout": completed.stdout, "stderr": completed.stderr,
                             "dispatch_bytes": self.dispatch.read_text(), "changed_paths": changed,
                             "before": before, "after": after})
        self.assertEqual("", self.dispatch.read_text(), "Read-only explanation dispatched an engine")
        self.assertEqual([], changed, "Explanation mutated disposable workspace/run/registry")
        return completed

    def assert_explanation(self, completed, status, reason):
        self.assertEqual(0, completed.returncode, completed.stderr)
        paragraphs = completed.stdout.strip().split("\n\n")
        self.assertEqual(3, len(paragraphs), completed.stdout)
        self.assertIn(status, paragraphs[0])
        self.assertIn(reason, paragraphs[0])
        with self.assertRaises(json.JSONDecodeError):
            json.loads(completed.stdout)
        self.assertNotIn("Traceback", completed.stderr)

    def test_explicit_saved_run_explanation_is_read_only_without_provider(self):
        reason = "retained-budget-stop"
        run = self.saved_run("paused", "PAUSED_BUDGET", reason, created="2026-10-01T01:00:00+00:00")
        completed = self.invoke("--run-dir", run, "--explain")
        self.assert_explanation(completed, "PAUSED_BUDGET", reason)
        self.assertIn("budget", completed.stdout.lower())
        self.assertIn("--resume-paused", completed.stdout)

    def test_automatic_sole_unfinished_run_explanation_is_read_only(self):
        reason = "only-unfinished-budget-stop"
        run = self.saved_run("paused", "PAUSED_BUDGET", reason, created="2026-10-01T01:00:00+00:00")
        completed = self.invoke("--explain")
        self.assert_explanation(completed, "PAUSED_BUDGET", reason)
        self.assertIn(str(run), completed.stderr)
        self.assertIn("only unfinished run", completed.stderr)

    def test_automatic_explanation_reads_latest_finished_run(self):
        self.saved_run("older", "TASK_COMPLETE", "older-completion-must-not-be-shown",
                       created="2026-10-01T01:00:00+00:00", completed="2026-10-01T02:00:00+00:00")
        reason = "latest-finished-completion"
        latest = self.saved_run("latest", "TASK_COMPLETE", reason, created="2026-10-02T01:00:00+00:00",
                                completed="2026-10-02T02:00:00+00:00")
        completed = self.invoke("--explain")
        self.assert_explanation(completed, "TASK_COMPLETE", reason)
        self.assertNotIn("older-completion-must-not-be-shown", completed.stdout)
        self.assertIn(str(latest), completed.stderr)
        self.assertIn("latest finished run", completed.stderr)

    def test_explicit_saved_run_command_word_is_read_only_without_provider(self):
        reason = "command-word-retained-budget-stop"
        run = self.saved_run("paused", "PAUSED_BUDGET", reason, created="2026-10-01T01:00:00+00:00")
        completed = self.invoke("--run-dir", run, "explain")
        self.assert_explanation(completed, "PAUSED_BUDGET", reason)
        self.assertIn("--resume-paused", completed.stdout)

    def test_automatic_command_word_reads_sole_unfinished_run(self):
        reason = "command-word-only-unfinished-stop"
        run = self.saved_run("paused", "PAUSED_BUDGET", reason, created="2026-10-01T01:00:00+00:00")
        completed = self.invoke("explain")
        self.assert_explanation(completed, "PAUSED_BUDGET", reason)
        self.assertIn(str(run), completed.stderr)
        self.assertIn("only unfinished run", completed.stderr)

    def test_automatic_command_word_reads_latest_finished_run(self):
        self.saved_run("older", "TASK_COMPLETE", "command-word-older-must-not-be-shown",
                       created="2026-10-01T01:00:00+00:00", completed="2026-10-01T02:00:00+00:00")
        reason = "command-word-latest-finished"
        latest = self.saved_run("latest", "TASK_COMPLETE", reason, created="2026-10-02T01:00:00+00:00",
                                completed="2026-10-02T02:00:00+00:00")
        completed = self.invoke("explain")
        self.assert_explanation(completed, "TASK_COMPLETE", reason)
        self.assertNotIn("command-word-older-must-not-be-shown", completed.stdout)
        self.assertIn(str(latest), completed.stderr)
        self.assertIn("latest finished run", completed.stderr)

    def test_new_task_explanation_refuses_without_worktree_or_registry_changes(self):
        completed = self.invoke("Build a fresh task", "--explain")
        self.assertEqual(2, completed.returncode, completed.stdout + completed.stderr)
        self.assertIn("--explain", completed.stderr)
        self.assertIn("saved run", completed.stderr.lower())
        self.assertEqual("", completed.stdout)


if __name__ == "__main__":
    program = unittest.main(exit=False)
    destination = os.environ.get("AUTOCODE_INSTALLED_EXPLAIN_RECEIPT")
    if destination:
        result = program.result
        Path(destination).write_text(json.dumps({"python": sys.executable, "prefix": sys.prefix,
            "cwd": str(Path.cwd()), "tests_run": result.testsRun,
            "failures": [{"test": test.id(), "detail": detail} for test, detail in result.failures],
            "errors": [{"test": test.id(), "detail": detail} for test, detail in result.errors],
            "observations": OBSERVATIONS}, indent=2) + "\n")
    raise SystemExit(not program.result.wasSuccessful())
