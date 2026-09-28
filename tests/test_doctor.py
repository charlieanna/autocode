"""autocode doctor and autocode --version (issue #67)."""
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

import autocode_doctor as doctor

REPO_ROOT = Path(__file__).resolve().parents[1]


def fake_runner(outputs):
    """A command runner answering from a table: tuple(cmd) -> (exit code, stdout)."""
    def runner(cmd, cwd=None):
        code, out = outputs.get(tuple(cmd), (127, ""))
        return subprocess.CompletedProcess(cmd, code, out, "")
    return runner


def on_path(*names):
    return lambda name: f"/bin/{name}" if name in names else None


class EngineTests(unittest.TestCase):
    def test_opencode_1x_is_ready_and_2x_is_refused(self):
        for version, status in (("1.18.32", doctor.OK), ("2.0.1", doctor.MISSING)):
            with self.subTest(version=version):
                checks = doctor.engine_checks(on_path("opencode"), fake_runner({("opencode", "--version"): (0, version)}))
                self.assertEqual(status, {c.name: c.status for c in checks}["engine:opencode"])

    def test_codex_must_be_logged_in(self):
        for code, status in ((0, doctor.OK), (1, doctor.MISSING)):
            checks = doctor.engine_checks(on_path("codex"), fake_runner({("codex", "login", "status"): (code, "")}))
            self.assertEqual(status, {c.name: c.status for c in checks}["engine:codex"])

    def test_any_ready_engine_is_enough_unless_one_is_required(self):
        checks = doctor.engine_checks(on_path("codex"), fake_runner({("codex", "login", "status"): (0, "")}))
        self.assertEqual(doctor.OK, doctor.engine_verdict(checks, None).status)
        self.assertEqual(doctor.MISSING, doctor.engine_verdict(checks, "opencode").status)
        self.assertEqual(doctor.MISSING, doctor.engine_verdict(doctor.engine_checks(on_path(), fake_runner({})),
                                                               None).status)

    def test_old_python_is_missing(self):
        self.assertEqual(doctor.MISSING, doctor.python_check((3, 10, 9)).status)
        self.assertEqual(doctor.OK, doctor.python_check((3, 11, 0)).status)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="doctor-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def git(self, *args):
        subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@example.test", *args], cwd=self.root,
                       check=True, capture_output=True)

    def statuses(self):
        return {c.name: c.status for c in doctor.workspace_check(self.root)}

    def test_a_folder_that_is_not_a_repository_is_missing(self):
        self.assertEqual({"workspace": doctor.MISSING}, self.statuses())

    def test_a_repository_needs_a_commit(self):
        self.git("init", "-q")
        self.assertEqual({"workspace": doctor.MISSING}, self.statuses())
        self.git("commit", "-q", "--allow-empty", "-m", "start")
        self.assertEqual({"workspace": doctor.OK}, self.statuses())

    def test_uncommitted_changes_warn_but_do_not_block(self):
        self.git("init", "-q")
        self.git("commit", "-q", "--allow-empty", "-m", "start")
        (self.root / "notes.txt").write_text("draft")
        checks = doctor.workspace_check(self.root)
        self.assertEqual(doctor.WARN, {c.name: c.status for c in checks}["workspace:clean"])
        self.assertTrue(doctor.passed(checks))


class CliTests(unittest.TestCase):
    def autocode(self, *args, cwd=REPO_ROOT):
        return subprocess.run([sys.executable, str(REPO_ROOT / "tools" / "autocode.py"), *args], cwd=cwd,
                              capture_output=True, text=True, timeout=60, env={**os.environ, "PATH": os.defpath})

    def test_version_names_the_package_version(self):
        proc = self.autocode("--version")
        self.assertEqual(0, proc.returncode, proc.stderr)
        version = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())["project"]["version"]
        self.assertTrue(proc.stdout.startswith(f"autocode {version} ("), proc.stdout)

    def test_doctor_reports_json_and_fails_without_a_repository(self):
        with tempfile.TemporaryDirectory() as folder:
            proc = self.autocode("doctor", "--json", "--workspace", folder)
        self.assertEqual(1, proc.returncode, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertFalse(report["ok"])
        self.assertEqual(doctor.MISSING, {c["name"]: c["status"] for c in report["checks"]}["workspace"])


if __name__ == "__main__":
    unittest.main()
