"""autocode bundle: one redacted, reproducible archive of a run for a bug report (#716)."""

import contextlib
import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_bundle as bundle

# Credential canaries (#712): token shapes the shared redaction knows, and one environment value it does not.
GITHUB_TOKEN = "ghp_" + "C" * 36
KEY_LINE = "api_key=sk-" + "D" * 30
ENVIRONMENT_SECRET = "canary-from-the-environment"
ENVIRONMENT = {"FIXTURE_DEPLOY_TOKEN": ENVIRONMENT_SECRET, "HOME": "/home/fixture"}
CANARIES = (GITHUB_TOKEN, "sk-" + "D" * 30, ENVIRONMENT_SECRET)


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


class BundleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name).resolve() / "project"
        self.workspace.mkdir()
        git(self.workspace, "init", "-q")
        (self.workspace / "app.py").write_text("SOURCE_LINE = 1\n")
        git(self.workspace, "add", "app.py")
        git(self.workspace, "-c", "user.name=F", "-c", "user.email=f@example.test", "commit", "-qm", "base")
        self.run = self.workspace / ".autocode" / "runs" / "r1"
        iteration = self.run / "iterations" / "001"
        iteration.mkdir(parents=True)
        state = {
            "status": "TASK_COMPLETE",
            "task": "Fix the greeting. " + KEY_LINE,
            "base_commit": git(self.workspace, "rev-parse", "HEAD").strip(),
            "settings": {"engine": "opencode", "note": GITHUB_TOKEN},
        }
        (self.run / "state.json").write_text(json.dumps(state))
        (self.run / "activity.jsonl").write_text(json.dumps({"command": "autocode", "env": ENVIRONMENT_SECRET}) + "\n")
        (iteration / "builder-01.json").write_text(json.dumps({"summary": "done", "token": GITHUB_TOKEN}))
        (iteration / "builder-01.before.json").write_text('{"files": {}}')
        (iteration / "builder-01.prompt.md").write_text("the prompt")
        (iteration / "builder-01.diff").write_text("+SOURCE_LINE = 2\n")
        events = [json.dumps({"n": n}) for n in range(500)] + [json.dumps({"auth": ENVIRONMENT_SECRET})]
        (iteration / "builder-01.jsonl").write_text("\n".join(events) + "\n")
        receipts = self.run / "check-replay" / "obligations"
        receipts.mkdir(parents=True)
        (receipts / "c1.json").write_text('{"verdict": "PASS"}')

    def collect(self, **kwargs):
        kwargs.setdefault("recording", self.no_recording)
        return bundle.collect(self.run, environ=ENVIRONMENT, doctor=lambda workspace: {"ok": True}, **kwargs)

    @staticmethod
    def no_recording(run_dir, scratch):
        raise ValueError("saved run has no supported recording projection")

    def recording_with(self, text):
        def export(run_dir, scratch):
            directory = scratch / "recording"
            directory.mkdir()
            (directory / "recording.json").write_text(text)
            return directory / "recording.json"  # as replay's export returns its manifest

        return export

    def test_the_bundle_carries_the_run_files_redacted_and_no_source(self):
        files, manifest = self.collect()
        self.assertEqual(
            {
                "run/state.json",
                "run/activity.jsonl",
                "run/iterations/001/builder-01.json",
                "run/iterations/001/builder-01.jsonl",
                "run/check-replay/obligations/c1.json",
                "settings.json",
                "doctor.json",
                "version.txt",
            },
            set(files),
        )
        for name, data in files.items():
            for canary in CANARIES:
                self.assertNotIn(canary.encode(), data, name)
        self.assertIn(b"[REDACTED]", files["run/state.json"])
        self.assertIn(b"[REDACTED]", files["run/activity.jsonl"])
        # The tail of a provider event stream; the redacted environment value is its last line.
        events = files["run/iterations/001/builder-01.jsonl"].decode().splitlines()
        self.assertEqual(bundle.TAIL_LINES, len(events))
        self.assertEqual('{"auth": "[REDACTED]"}', events[-1])
        self.assertFalse(any(b"SOURCE_LINE" in data for data in files.values()))
        self.assertIn("recording", manifest["notes"])
        self.assertEqual(
            [
                {"path": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
                for name, data in sorted(files.items())
            ],
            manifest["files"],
        )

    def test_with_diff_adds_the_runs_diff_against_its_base_commit(self):
        (self.workspace / "app.py").write_text("SOURCE_LINE = 2\n")
        files, manifest = self.collect(with_diff=True)
        self.assertIn(b"+SOURCE_LINE = 2", files["diff.patch"])
        self.assertEqual(
            json.loads((self.run / "state.json").read_text())["base_commit"], manifest["notes"]["diff_base"]
        )

    def test_an_archived_runs_bundle_takes_the_project_and_diffs_its_kept_branch(self):
        """clean-worktrees copies a finished worktree's records to PROJECT/.autocode/archive/TREE (#2377)."""
        git(self.workspace, "checkout", "-qb", "autocode/task-1")
        (self.workspace / "app.py").write_text("SOURCE_LINE = 2\n")
        git(self.workspace, "-c", "user.name=F", "-c", "user.email=f@example.test", "commit", "-qam", "work")
        git(self.workspace, "checkout", "-q", "-")
        archive = self.workspace / ".autocode" / "archive" / "task-1"
        (archive / "runs").mkdir(parents=True)
        self.run.rename(archive / "runs" / "r1")
        self.run = archive / "runs" / "r1"
        (archive / "task-workspace.json").write_text(json.dumps({"kind": "task", "branch": "autocode/task-1"}))
        files, manifest = self.collect(with_diff=True)
        self.assertIn(b"+SOURCE_LINE = 2", files["diff.patch"])
        base = json.loads((self.run / "state.json").read_text())["base_commit"]
        self.assertEqual(f"{base}..autocode/task-1", manifest["notes"]["diff_base"])
        stdout = io.StringIO()
        with (
            mock.patch.object(bundle, "_doctor", lambda workspace: {"ok": True}),
            mock.patch.object(bundle, "_recording", self.no_recording),
            contextlib.redirect_stdout(stdout),
        ):
            self.assertEqual(0, bundle.cli([str(self.run), "--json"]))
        self.assertEqual(
            str(self.workspace / ".autocode" / "bundles" / "r1.tar.gz"), json.loads(stdout.getvalue())["bundle"]
        )
        # Without the saved branch the diff cannot be taken; the bundle without it still can.
        (archive / "task-workspace.json").unlink()
        with self.assertRaisesRegex(ValueError, "without --with-diff"):
            self.collect(with_diff=True)
        self.assertIn("doctor.json", self.collect()[0])

    def test_a_recording_redaction_would_still_change_is_left_out(self):
        files, manifest = self.collect(recording=self.recording_with('{"clean": true}'))
        self.assertEqual(b'{"clean": true}', files["recording/recording.json"])
        self.assertNotIn("recording", manifest["notes"])
        files, manifest = self.collect(recording=self.recording_with(json.dumps({"leak": ENVIRONMENT_SECRET})))
        self.assertFalse(any(name.startswith("recording/") for name in files))
        self.assertIn("redaction would change recording/recording.json", manifest["notes"]["recording"])

    def test_the_archive_is_reproducible_and_its_manifest_hashes_hold(self):
        files, manifest = self.collect()
        first, second = self.workspace / "one.tar.gz", self.workspace / "two.tar.gz"
        digest = bundle.write(first, "bundle-r1", files, manifest)
        self.assertEqual(digest, bundle.write(second, "bundle-r1", files, manifest))
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with tarfile.open(first) as archive:
            names = archive.getnames()
            saved = json.loads(archive.extractfile("bundle-r1/manifest.json").read())
            self.assertEqual(digest, hashlib.sha256(archive.extractfile("bundle-r1/manifest.json").read()).hexdigest())
            for row in saved["files"]:
                data = archive.extractfile(f"bundle-r1/{row['path']}").read()
                self.assertEqual(row["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(sorted(names), names)
        self.assertEqual(manifest, saved)

    def test_the_cli_writes_the_default_archive_and_reads_the_run_without_changing_it(self):
        before = {path: path.read_bytes() for path in self.run.rglob("*") if path.is_file()}
        stdout = io.StringIO()
        with (
            mock.patch.object(bundle, "_doctor", lambda workspace: {"ok": True}),
            mock.patch.object(bundle, "_recording", self.no_recording),
            contextlib.redirect_stdout(stdout),
        ):
            self.assertEqual(0, bundle.cli([str(self.run), "--json"]))
        result = json.loads(stdout.getvalue())
        self.assertEqual(str(self.workspace / ".autocode" / "bundles" / "r1.tar.gz"), result["bundle"])
        self.assertTrue(Path(result["bundle"]).is_file())
        self.assertEqual(before, {path: path.read_bytes() for path in self.run.rglob("*") if path.is_file()})
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(2, bundle.cli([str(self.workspace / "not-a-run")]))
        self.assertIn("WORKSPACE/.autocode/runs/RUN", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
