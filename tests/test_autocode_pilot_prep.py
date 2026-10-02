"""Preparation coordinator boundary tests (AC6, AC7, AC8, AC15).

Drives tools/autocode_pilot_prep.py end to end as a subprocess with the
offline fixture provider (tools/live_fixture_provider.py) bound as ``codex``
on PATH behind a shell shim that logs every invocation, the tests/test_taskrun.py
pattern. The positive fixture proves declared inputs survive into the verified
copy with exact identities while a run really starts; the negative fixture
proves an ignored declared input stops preparation before any model call; the
isolation control keeps the dirty overlay and excludes ignored trees; and the
passivity check leaves the started run awaiting the user's plan approval.
"""
import hashlib
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import autocode_taskrun as taskrun
import live_fixture_provider

ROOT = Path(__file__).resolve().parents[1]
PREP = ROOT / "tools" / "autocode_pilot_prep.py"
PROVIDER = ROOT / "tools" / "live_fixture_provider.py"
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
PYTHON = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable

PROOF = b"proof instructions v1\n"  # 22 bytes, the issue's synthetic proof input
PATCH = b"--- a/x\n+++ b/x\n"      # 16 bytes, the issue's synthetic overlay
BRIEF = ("Build a deterministic greeting CLI named greet.py. It prints 'Hello, NAME' for one nonempty name "
         "argument and exits 0. Any other argument count (no arguments, or two or more) prints a usage line "
         "to stderr and exits 2. The supplied public proof inputs are pilot-public/PUBLIC-PROOF.md and "
         "pilot-public/pr42.patch, at those exact project-relative paths. Deliver greet.py, test_greet.py "
         "with regression tests, and a short README.md. Python standard library only.")
FIXTURE_OPTIONS = ["--engine", "codex", "--joint-planning", "--astra-model", "gpt-6-astra",
                   "--terra-model", "gpt-5.6-terra", "--sol-model", "gpt-5.6-sol", "--completion-model",
                   "gpt-6-astra", "--glm-model", "gpt-5.6-sol", "--plan-reviewer-model", "gpt-6-astra"]


def write(root, relpath, data, mode=0o644):
    path = Path(root) / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.chmod(path, mode)


class PilotPrepTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="pilot-prep-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        self.calls = self.root / "codex-calls.log"
        bindir = self.root / "bin"
        bindir.mkdir()
        provider = bindir / "live_fixture_provider.py"
        shutil.copy2(PROVIDER, provider)
        provider.chmod(0o755)
        shim = bindir / "codex"
        shim.write_text("#!/bin/sh\n"
                        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(self.calls))}\n"
                        f"exec {shlex.quote(str(provider))} \"$@\"\n")
        shim.chmod(0o755)
        self.env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
                    "AUTOCODE_HOME": str(self.root / "registry"), "PYTHONDONTWRITEBYTECODE": "1"}
        self.report = self.workspace / "prep-report.json"
        self.brief = self.root / "brief.txt"
        self.brief.write_text(BRIEF)

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.workspace), *args], check=True)

    def commit(self, *tracked):
        self.git("add", *tracked)
        self.git("-c", "user.name=T", "-c", "user.email=t@example.test", "commit", "-qm", "fixture")

    def manifest(self, entries):
        path = self.root / "manifest.json"
        path.write_text(json.dumps({"inputs": entries}))
        return path

    @staticmethod
    def entry(path, data):
        return {"path": path, "type": "file", "mode": "0644", "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest()}

    def coordinator(self, manifest):
        return subprocess.run(
            [PYTHON, str(PREP), "--workspace", str(self.workspace), "--manifest", str(manifest),
             "--report", str(self.report), "--brief", str(self.brief), *FIXTURE_OPTIONS],
            capture_output=True, text=True, env=self.env, timeout=600)

    def run_dirs(self):
        return sorted((self.workspace / ".autocode" / "runs").glob("*/state.json"))

    def positive_fixture(self):
        """AC6's repository: tracked proof input, ordinary untracked patch."""
        write(self.workspace, ".gitignore", b".autocode/\n")
        write(self.workspace, "README.md", b"readme\n")
        write(self.workspace, "pilot-public/PUBLIC-PROOF.md", PROOF)
        self.commit(".gitignore", "README.md", "pilot-public/PUBLIC-PROOF.md")
        write(self.workspace, "pilot-public/pr42.patch", PATCH)
        return self.manifest([self.entry("pilot-public/PUBLIC-PROOF.md", PROOF),
                              self.entry("pilot-public/pr42.patch", PATCH)])

    def test_ac6_declared_inputs_survive_and_run_starts(self):
        manifest = self.positive_fixture()
        proc = self.coordinator(manifest)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        report = json.loads(self.report.read_text())
        copy_root = Path(report["copy_root"])
        self.assertTrue(copy_root.is_dir(), report)
        for relpath in ("pilot-public/PUBLIC-PROOF.md", "pilot-public/pr42.patch"):
            original = (self.workspace / relpath).read_bytes()
            copied = copy_root / relpath
            info = copied.stat()
            self.assertEqual(len(original), info.st_size, relpath)
            self.assertEqual(0o644, stat.S_IMODE(info.st_mode), relpath)
            self.assertEqual(hashlib.sha256(original).hexdigest(),
                             hashlib.sha256(copied.read_bytes()).hexdigest(), relpath)
        self.assertGreaterEqual(len(self.calls.read_text().splitlines()), 1)
        self.assertEqual(1, len(self.run_dirs()))

    def test_ac7_ignored_input_stops_before_any_model_call(self):
        write(self.workspace, ".gitignore", b".pilot-env/\n.autocode/\n")
        write(self.workspace, "README.md", b"readme\n")
        write(self.workspace, "pilot-public/PUBLIC-PROOF.md", PROOF)
        self.commit(".gitignore", "README.md", "pilot-public/PUBLIC-PROOF.md")
        write(self.workspace, "pilot-public/pr42.patch", PATCH)
        write(self.workspace, ".pilot-env/public/PROOF.md", PROOF)
        manifest = self.manifest([self.entry(".pilot-env/public/PROOF.md", PROOF)])
        proc = self.coordinator(manifest)
        self.assertEqual(2, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn(".pilot-env/public/PROOF.md", proc.stderr)
        self.assertIn("ignored", proc.stderr)
        self.assertFalse(self.calls.exists())
        self.assertFalse((self.workspace / ".autocode" / "runs").exists())

    def test_ac8_copy_excludes_ignored_and_keeps_dirty_overlay(self):
        write(self.workspace, ".gitignore", b".autocode/\nsecrets.env\nnode_modules/\ndist/\n")
        write(self.workspace, "README.md", b"base readme\n")
        write(self.workspace, "pilot-public/PUBLIC-PROOF.md", PROOF)
        self.commit(".gitignore", "README.md", "pilot-public/PUBLIC-PROOF.md")
        write(self.workspace, "pilot-public/pr42.patch", PATCH)
        write(self.workspace, "README.md", b"dirty readme\n")  # uncommitted overlay, 13 bytes
        write(self.workspace, "secrets.env", b"token=1\n")
        write(self.workspace, "node_modules/dep.js", b"dep\n")
        write(self.workspace, "dist/out.js", b"out\n")
        proc = self.coordinator(self.positive_manifest_for_dirty_fixture())
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        copy_root = Path(json.loads(self.report.read_text())["copy_root"])
        for excluded in ("secrets.env", "node_modules/dep.js", "dist/out.js"):
            self.assertFalse((copy_root / excluded).exists(), excluded)
        self.assertEqual(b"dirty readme\n", (copy_root / "README.md").read_bytes())

    def positive_manifest_for_dirty_fixture(self):
        return self.manifest([self.entry("pilot-public/PUBLIC-PROOF.md", PROOF),
                              self.entry("pilot-public/pr42.patch", PATCH)])

    def test_ac15_success_path_leaves_run_awaiting_user_approval(self):
        manifest = self.positive_fixture()
        proc = self.coordinator(manifest)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        runs = self.run_dirs()
        self.assertEqual(1, len(runs))
        run = taskrun.TaskRun(self.workspace, runs[0].parent,
                              options=tuple(FIXTURE_OPTIONS), env=self.env)
        view = run.status()
        self.assertFalse(view["done"], view)
        self.assertEqual("approve_plan", view["needs"]["kind"], view)
        self.assertTrue(view["needs"]["token"])

    def test_fixture_only_untracked_source_reaches_plan_approval(self):
        self.git('-c', 'user.name=T', '-c', 'user.email=t@example.test',
                 'commit', '-qm', 'empty base', '--allow-empty')
        write(self.workspace, 'README.md', b'Greeting project\n')
        run = taskrun.TaskRun.start(self.workspace, BRIEF, options=tuple(FIXTURE_OPTIONS),
                                    env=self.env, timeout=90)
        view = run.status()
        self.assertFalse(view['done'], view)
        self.assertEqual('approve_plan', view['needs']['kind'], view)

    def test_fixture_run_artifacts_or_links_cannot_displace_source_citations(self):
        self.git('-c', 'user.name=T', '-c', 'user.email=t@example.test',
                 'commit', '-qm', 'empty base', '--allow-empty')
        write(self.workspace, 'README.md', b'Greeting project\n')
        for index in range(9):
            write(self.workspace, f'.autocode/runs/example/artifact-{index}.json', b'{}')
            (self.workspace / f'a-link-{index}').symlink_to(
                self.workspace / f'.autocode/runs/example/artifact-{index}.json')
        old_cwd = Path.cwd()
        try:
            os.chdir(self.workspace)
            refs = live_fixture_provider._source_refs()
        finally:
            os.chdir(old_cwd)
        self.assertEqual(['README.md'], refs)

    def test_fixture_ignored_link_targets_are_not_source_citations(self):
        self.git('-c', 'user.name=T', '-c', 'user.email=t@example.test',
                 'commit', '-qm', 'empty base', '--allow-empty')
        write(self.workspace, '.gitignore', b'ignored/\n')
        write(self.workspace, 'README.md', b'Greeting project\n')
        write(self.workspace, 'ignored/PROOF.md', b'Ignored proof\n')
        (self.workspace / 'a-proof.md').symlink_to('ignored/PROOF.md')
        old_cwd = Path.cwd()
        try:
            os.chdir(self.workspace)
            refs = live_fixture_provider._source_refs()
        finally:
            os.chdir(old_cwd)
        self.assertIn('README.md', refs)
        self.assertNotIn('a-proof.md', refs)
        self.assertNotIn('ignored/PROOF.md', refs)


if __name__ == "__main__":
    unittest.main()
