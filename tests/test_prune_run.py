"""prune-run (#971): delete settled evidence bytes, keep every receipt and hash.

The kill criteria are conservative: file-level state references, live stages,
pending failure recoveries and the newest of each kind all keep their bytes.
"""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

import autocode_prune_run as prune_run
import autocode_stop as stop


def write(root, name, text="x" * 10):
    path = Path(root) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def containment(run, uuid, *, with_file=True):
    directory = Path(run) / f"tool-containment-{uuid}"
    write(directory, "verification-copy.json", "{}")
    if with_file:
        write(directory, "scratch/verification/src/a.py", "source\n")
    return directory


def source_store(run, iteration, stem, blob="blob\n"):
    directory = Path(run) / "iterations" / iteration / f"{stem}.source"
    write(directory, "7a0b7f", blob)
    write(Path(run) / "iterations" / iteration, f"{stem}.source.json", "{}")
    return directory


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run = Path(self.tmp.name)
        self.aaaa = containment(self.run, "aaaa")
        self.bbbb = containment(self.run, "bbbb")
        self.cccc = containment(self.run, "cccc")
        self.job_a = source_store(self.run, "001", "job-a")
        self.job_b = source_store(self.run, "002", "job-b")
        # Deterministic recency: bbbb and job-b are the newest of their kind.
        for path, when in ((self.aaaa, 100), (self.cccc, 200), (self.bbbb, 300), (self.job_a, 100), (self.job_b, 300)):
            os.utime(path, (when, when))
        self.state = {
            "stages": [
                {
                    "output": str(self.run / "iterations" / "001" / "job-a.json"),
                    "verification_copy": {"manifest": str(self.aaaa / "verification-copy.json"), "sha256": "h"},
                    "receipt": {"cwd": str(self.aaaa / "scratch" / "verification")},
                }
            ],
            "job_failure": {"attempt": "job-c"},
        }
        self.job_c = source_store(self.run, "001", "job-c")
        os.utime(self.job_c, (150, 150))
        write(self.cccc, "scratch/verification/pinned.py", "pinned\n")
        self.state["stages"].append({"pinned_file": str(self.cccc / "scratch" / "verification" / "pinned.py")})

    def plan(self):
        return prune_run.candidates(self.state, self.run)

    def test_a_settled_copy_and_store_are_deletable(self):
        plan = self.plan()
        deleted = {item["path"] for item in plan["delete"]}
        self.assertEqual(deleted, {str(self.aaaa), str(self.job_a)})
        self.assertGreater(plan["freed_bytes"], 0)

    def test_the_newest_of_each_kind_is_kept(self):
        plan = self.plan()
        kept = {item["path"]: item["reason"] for item in plan["keep"]}
        self.assertIn("newest", kept[str(self.bbbb)])
        self.assertIn("newest", kept[str(self.job_b)])

    def test_bytes_named_at_file_level_by_state_are_kept_with_the_reference(self):
        plan = self.plan()
        kept = {item["path"]: item["reason"] for item in plan["keep"]}
        self.assertIn("pinned.py", kept[str(self.cccc)])
        self.assertIn("still named by state", kept[str(self.cccc)])

    def test_a_pending_failure_recovery_keeps_its_source_store(self):
        plan = self.plan()
        kept = {item["path"]: item["reason"] for item in plan["keep"]}
        self.assertIn("job-c", kept[str(self.job_c)])
        self.assertIn("job_failure", kept[str(self.job_c)])

    def test_manifest_and_cwd_provenance_references_do_not_block(self):
        plan = self.plan()
        deleted = {item["path"] for item in plan["delete"]}
        self.assertIn(str(self.aaaa), deleted)  # named only by its manifest path and a captured cwd


class PruneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run = Path(self.tmp.name)
        self.aaaa = containment(self.run, "aaaa")
        self.bbbb = containment(self.run, "bbbb")
        self.job_a = source_store(self.run, "001", "job-a")
        self.job_b = source_store(self.run, "002", "job-b")
        for path, when in ((self.aaaa, 100), (self.job_a, 100), (self.job_b, 300), (self.bbbb, 300)):
            os.utime(path, (when, when))
        self.state = {}
        write(self.run, "state.json", json.dumps(self.state))

    def test_prune_removes_the_dead_bytes_but_keeps_every_manifest(self):
        plan = prune_run.candidates(self.state, self.run)
        freed = prune_run.prune(plan)
        self.assertGreater(freed, 0)
        self.assertTrue((self.aaaa / "verification-copy.json").is_file())
        self.assertFalse((self.aaaa / "scratch").exists())
        self.assertFalse(self.job_a.exists())
        self.assertTrue((self.run / "iterations" / "001" / "job-a.source.json").is_file())
        self.assertTrue((self.job_b / "7a0b7f").is_file())
        self.assertTrue((self.bbbb / "scratch" / "verification" / "src" / "a.py").is_file())

    def test_list_mode_changes_nothing_and_yes_deletes(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = prune_run.cli([str(self.run)])
        self.assertEqual(code, 0)
        self.assertIn("re-run with --yes", stdout.getvalue())
        self.assertTrue((self.aaaa / "scratch").exists())
        with contextlib.redirect_stdout(io.StringIO()):
            code = prune_run.cli([str(self.run), "--yes"])
        self.assertEqual(code, 0)
        self.assertFalse((self.aaaa / "scratch").exists())
        self.assertTrue((self.aaaa / "verification-copy.json").is_file())

    def test_the_run_may_be_named_with_run_dir_like_the_other_commands(self):
        """Every other command takes --run-dir; prune-run took only a positional RUN (#2385)."""
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = prune_run.cli(["--run-dir", str(self.run)])
        self.assertEqual(code, 0)
        self.assertIn("re-run with --yes", stdout.getvalue())
        for argv in ([], [str(self.run), "--run-dir", str(self.run)]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as stop:
                    prune_run.cli(argv)
                self.assertEqual(2, stop.exception.code)

    def test_a_live_stage_refuses_the_prune(self):
        write(self.run, "state.json", json.dumps({**self.state, "active_stage": {"stage": "terra"}}))
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = prune_run.cli([str(self.run), "--yes"])
        self.assertEqual(code, 2)
        self.assertIn("Refused", stdout.getvalue())
        self.assertTrue((self.aaaa / "scratch").exists())

    def test_cli_reads_state_from_the_run_directory(self):
        write(self.run, "state.json", json.dumps(self.state))
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = prune_run.cli([str(self.run), "--yes"])
        self.assertEqual(code, 0)
        self.assertIn("Pruned", stdout.getvalue())

    def test_cli_json_output_dry_run_and_pruned(self):
        write(self.run, "state.json", json.dumps(self.state))
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = prune_run.cli([str(self.run), "--json"])
        self.assertEqual(code, 0)
        data = json.loads(stdout.getvalue())
        self.assertFalse(data["pruned"])
        self.assertEqual(str(self.run), data["run"])
        self.assertGreater(data["freed_bytes"], 0)
        self.assertEqual(2, len(data["delete"]))

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = prune_run.cli([str(self.run), "--yes", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(stdout.getvalue())
        self.assertTrue(data["pruned"])
        self.assertEqual(str(self.run), data["run"])
        self.assertGreater(data["freed_bytes"], 0)

    def test_cli_json_output_refused_when_stage_active(self):
        write(self.run, "state.json", json.dumps({**self.state, "active_stage": {"stage": "terra"}}))
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = prune_run.cli([str(self.run), "--json"])
        self.assertEqual(code, 2)
        self.assertEqual("", stdout.getvalue())
        err = json.loads(stderr.getvalue())
        self.assertEqual("stage_active", err["error"]["code"])


class FinishedRunTests(unittest.TestCase):
    """A finished run never restores: lingering recovery fields must not block (#1060)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run = Path(self.tmp.name)
        self.job_a = source_store(self.run, "001", "job-a")
        self.job_b = source_store(self.run, "002", "job-b")
        for path, when in ((self.job_a, 100), (self.job_b, 300)):
            os.utime(path, (when, when))

    def test_task_complete_releases_a_lingering_failure_hold(self):
        state = {"status": "TASK_COMPLETE", "job_failure": {"attempt": "job-a"}}
        plan = prune_run.candidates(state, self.run)
        deleted = {item["path"] for item in plan["delete"]}
        self.assertIn(str(self.job_a), deleted)

    def test_a_durable_stop_releases_it_too(self):
        state = {
            "status": "RUNNING",
            "applied_interventions": [{"kind": stop.STOP_KIND}],
            "job_failure": {"attempt": "job-a"},
        }
        plan = prune_run.candidates(state, self.run)
        deleted = {item["path"] for item in plan["delete"]}
        self.assertIn(str(self.job_a), deleted)

    def test_an_unfinished_run_still_holds_its_pending_store(self):
        state = {"status": "RUNNING", "job_failure": {"attempt": "job-a"}}
        plan = prune_run.candidates(state, self.run)
        kept = {item["path"]: item["reason"] for item in plan["keep"]}
        self.assertIn("job_failure", kept[str(self.job_a)])


if __name__ == "__main__":
    unittest.main()
