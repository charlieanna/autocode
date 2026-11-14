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
import autocode_doctor as doctor

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

    def test_collection_records_unrun_diagnostics_without_checking_backend_health(self):
        with mock.patch.object(
            doctor, "all_checks", side_effect=subprocess.TimeoutExpired(["kilo", "models"], 90)
        ) as live_checks:
            files, manifest = bundle.collect(self.run, environ=ENVIRONMENT, recording=self.no_recording)
        live_checks.assert_not_called()
        report = json.loads(files["doctor.json"])
        self.assertEqual("not_run", report["status"])
        self.assertIsNone(report["ok"])
        self.assertEqual([], report["checks"])
        self.assertIn("live", report["reason"].lower())
        self.assertIn("run/iterations/001/builder-01.json", files)
        row = next(row for row in manifest["files"] if row["path"] == "doctor.json")
        self.assertEqual(hashlib.sha256(files["doctor.json"]).hexdigest(), row["sha256"])

    def test_bundle_cli_exports_retained_evidence_without_live_checks(self):
        before = {path: path.read_bytes() for path in self.run.rglob("*") if path.is_file()}
        stdout = io.StringIO()
        with (
            mock.patch.object(
                doctor, "all_checks", side_effect=subprocess.TimeoutExpired(["kilo", "models"], 90)
            ) as live_checks,
            mock.patch.object(bundle, "_recording", self.recording_with('{"retained": true}')),
            mock.patch.dict("os.environ", ENVIRONMENT),
            contextlib.redirect_stdout(stdout),
        ):
            self.assertEqual(0, bundle.cli(["--run-dir", str(self.run), "--with-diff", "--json"]))
        live_checks.assert_not_called()
        result = json.loads(stdout.getvalue())
        with tarfile.open(result["bundle"]) as archive:
            prefix = f"autocode-bundle-{self.run.name}/"
            manifest_bytes = archive.extractfile(prefix + "manifest.json").read()
            self.assertEqual(result["manifest_sha256"], hashlib.sha256(manifest_bytes).hexdigest())
            manifest = json.loads(manifest_bytes)
            self.assertEqual(result["manifest"], manifest)
            retained = {row["path"]: archive.extractfile(prefix + row["path"]).read() for row in manifest["files"]}
            self.assertIn("diff.patch", retained)
            self.assertEqual(b'{"retained": true}', retained["recording/recording.json"])
            self.assertEqual("not_run", json.loads(retained["doctor.json"])["status"])
            for row in manifest["files"]:
                self.assertEqual(row["sha256"], hashlib.sha256(retained[row["path"]]).hexdigest())
                for canary in CANARIES:
                    self.assertNotIn(canary.encode(), retained[row["path"]])
        self.assertEqual(before, {path: path.read_bytes() for path in self.run.rglob("*") if path.is_file()})

    def test_with_diff_adds_the_runs_diff_against_its_base_commit(self):
        (self.workspace / "app.py").write_text("SOURCE_LINE = 2\n")
        files, manifest = self.collect(with_diff=True)
        self.assertIn(b"+SOURCE_LINE = 2", files["diff.patch"])
        self.assertEqual(
            json.loads((self.run / "state.json").read_text())["base_commit"], manifest["notes"]["diff_base"]
        )

    def test_with_diff_keeps_the_bytes_of_a_file_that_is_not_utf8(self):
        # A Latin-1 source file: the bundle stopped with "'utf-8' codec can't decode byte 0xe9".
        (self.workspace / "app.py").write_bytes('NAME = "Jos\u00e9"\n'.encode("latin-1"))
        files, _manifest = self.collect(with_diff=True)
        self.assertIn(b'+NAME = "Jos\xe9"\n', files["diff.patch"])

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

    def test_the_run_may_be_named_with_run_dir_like_the_other_commands(self):
        """Every other command takes --run-dir; bundle took only a positional RUN (#2385)."""
        stdout = io.StringIO()
        with (
            mock.patch.object(bundle, "_doctor", lambda workspace: {"ok": True}),
            mock.patch.object(bundle, "_recording", self.no_recording),
            contextlib.redirect_stdout(stdout),
        ):
            self.assertEqual(0, bundle.cli(["--run-dir", str(self.run), "--json"]))
        self.assertEqual(
            str(self.workspace / ".autocode" / "bundles" / "r1.tar.gz"), json.loads(stdout.getvalue())["bundle"]
        )
        for argv in ([], [str(self.run), "--run-dir", str(self.run)]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as stop:
                    bundle.cli(argv)
                self.assertEqual(2, stop.exception.code)

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

    def test_model_preflight_receipts_and_event_tails_are_bundled_and_redacted(self):
        (self.run / "model-preflight.json").write_text(json.dumps({"models": {"m1": {"token": GITHUB_TOKEN}}}))
        attempt = self.run / "model-preflight" / "opencode__m1" / "attempt-abc"
        attempt.mkdir(parents=True)
        (attempt / "report.json").write_text(json.dumps({"ok": False, "secret": KEY_LINE}))
        (attempt / "supervision.json").write_text(json.dumps({"phase": "stopped", "env": ENVIRONMENT_SECRET}))
        (attempt / "snapshot.before.json").write_text('{"files": {"app.py": "SOURCE_LINE"}}')
        preflight_events = [json.dumps({"seq": i}) for i in range(250)] + [json.dumps({"secret": ENVIRONMENT_SECRET})]
        (attempt / "events.jsonl").write_text("\n".join(preflight_events) + "\n")

        files, manifest = self.collect()
        self.assertIn("run/model-preflight.json", files)
        self.assertIn("run/model-preflight/opencode__m1/attempt-abc/report.json", files)
        self.assertIn("run/model-preflight/opencode__m1/attempt-abc/supervision.json", files)
        self.assertIn("run/model-preflight/opencode__m1/attempt-abc/events.jsonl", files)
        self.assertNotIn("run/model-preflight/opencode__m1/attempt-abc/snapshot.before.json", files)

        tailed = files["run/model-preflight/opencode__m1/attempt-abc/events.jsonl"].decode().splitlines()
        self.assertEqual(bundle.TAIL_LINES, len(tailed))
        self.assertEqual('{"secret": "[REDACTED]"}', tailed[-1])
        for name, data in files.items():
            for canary in CANARIES:
                self.assertNotIn(canary.encode(), data, name)
        manifest_paths = {row["path"] for row in manifest["files"]}
        self.assertIn("run/model-preflight/opencode__m1/attempt-abc/supervision.json", manifest_paths)

    def test_moved_worktree_run_bundles_canonical_worktree_state_diff_and_pre_move_preflight(self):
        import autocode_workspaces as workspaces

        pre_attempt = self.run / "model-preflight" / "opencode__m1" / "attempt-pre"
        pre_attempt.mkdir(parents=True)
        (pre_attempt / "report.json").write_text(json.dumps({"probe": "pre-move", "token": GITHUB_TOKEN}))
        pre_events = [json.dumps({"n": n}) for n in range(220)] + [json.dumps({"env": ENVIRONMENT_SECRET})]
        (pre_attempt / "events.jsonl").write_text("\n".join(pre_events) + "\n")

        created = workspaces.create(self.workspace, "Fix the greeting")
        tree = Path(created["workspace"])
        worktree_run = tree / ".autocode" / "runs" / self.run.name
        (worktree_run / "iterations" / "002").mkdir(parents=True)
        (worktree_run / "iterations" / "002" / "tester-01.json").write_text(
            json.dumps({"verdict": "PASS", "key": KEY_LINE})
        )
        (tree / "app.py").write_text("SOURCE_LINE = 3\n")

        root_state = {
            "status": "DISCOVERING",
            "task": "Fix the greeting",
            "base_commit": "0" * 40,
            "build_worktree": {
                "state": "moved",
                "moved_to": {
                    "workspace": str(tree),
                    "run_dir": str(worktree_run),
                    "branch": created["branch"],
                },
            },
        }
        (self.run / "state.json").write_text(json.dumps(root_state))

        canonical_state = {
            "status": "TASK_COMPLETE",
            "task": "Fix the greeting. " + KEY_LINE,
            "workspace": str(tree),
            "base_commit": created["base_commit"],
            "settings": {"engine": "opencode", "note": GITHUB_TOKEN},
            "build_worktree": {
                "state": "building",
                "moved_from": {"workspace": str(self.workspace), "run_dir": str(self.run.resolve())},
                "branch": created["branch"],
            },
            "stages": [
                {
                    "stage": "model_preflight",
                    "events": str((pre_attempt / "events.jsonl").resolve()),
                    "output": str((pre_attempt / "report.json").resolve()),
                }
            ],
        }
        (worktree_run / "state.json").write_text(json.dumps(canonical_state))

        files, manifest = self.collect(with_diff=True)
        self.assertEqual("TASK_COMPLETE", json.loads(files["run/state.json"])["status"])
        self.assertIn("run/iterations/002/tester-01.json", files)
        self.assertNotIn("run/iterations/001/builder-01.json", files)
        self.assertIn("run/pre-move-model-preflight/opencode__m1/attempt-pre/report.json", files)
        self.assertIn("run/pre-move-model-preflight/opencode__m1/attempt-pre/events.jsonl", files)
        self.assertEqual(
            bundle.TAIL_LINES,
            len(files["run/pre-move-model-preflight/opencode__m1/attempt-pre/events.jsonl"].decode().splitlines()),
        )
        self.assertIn(b"+SOURCE_LINE = 3", files["diff.patch"])
        self.assertEqual(created["base_commit"], manifest["notes"]["diff_base"])
        self.assertEqual("project/.autocode/runs/r1", manifest["notes"]["moved_from"])
        for name, data in files.items():
            for canary in CANARIES:
                self.assertNotIn(canary.encode(), data, name)

        stdout = io.StringIO()
        with (
            mock.patch.object(bundle, "_doctor", lambda workspace: {"ok": True}),
            mock.patch.object(bundle, "_recording", self.no_recording),
            contextlib.redirect_stdout(stdout),
        ):
            self.assertEqual(0, bundle.cli(["--run-dir", str(self.run), "--with-diff", "--json"]))
        cli_out = json.loads(stdout.getvalue())
        self.assertEqual(str(self.workspace / ".autocode" / "bundles" / "r1.tar.gz"), cli_out["bundle"])
        self.assertEqual("project/.autocode/runs/r1", cli_out["manifest"]["notes"]["moved_from"])

    def test_tampered_or_symlinked_moved_to_pointer_is_refused(self):
        import autocode_workspaces as workspaces

        outside = self.workspace.parent / "outside"
        outside_run = outside / ".autocode" / "runs" / self.run.name
        outside_run.mkdir(parents=True)
        (outside_run / "state.json").write_text(json.dumps({"status": "TASK_COMPLETE", "workspace": str(outside)}))

        (self.run / "state.json").write_text(
            json.dumps(
                {
                    "status": "DISCOVERING",
                    "build_worktree": {
                        "state": "moved",
                        "moved_to": {"workspace": str(outside), "run_dir": str(outside_run), "branch": "autocode/x"},
                    },
                }
            )
        )
        with self.assertRaises(ValueError):
            self.collect()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(2, bundle.cli(["--run-dir", str(self.run)]))

        created = workspaces.create(self.workspace, "Real worktree")
        real_tree = Path(created["workspace"])
        link_tree = self.workspace / ".autocode" / "worktrees" / "aliased-tree"
        link_tree.symlink_to(real_tree)
        link_run = link_tree / ".autocode" / "runs" / self.run.name
        (real_tree / ".autocode" / "runs" / self.run.name).mkdir(parents=True)
        (real_tree / ".autocode" / "runs" / self.run.name / "state.json").write_text(
            json.dumps(
                {
                    "status": "TASK_COMPLETE",
                    "workspace": str(link_tree),
                    "build_worktree": {
                        "state": "building",
                        "moved_from": {"workspace": str(self.workspace), "run_dir": str(self.run.resolve())},
                    },
                }
            )
        )
        (self.run / "state.json").write_text(
            json.dumps(
                {
                    "status": "DISCOVERING",
                    "build_worktree": {
                        "state": "moved",
                        "moved_to": {
                            "workspace": str(link_tree),
                            "run_dir": str(link_run),
                            "branch": created["branch"],
                        },
                    },
                }
            )
        )
        with self.assertRaises(ValueError):
            self.collect()


if __name__ == "__main__":
    unittest.main()
