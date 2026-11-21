"""Where `autocode capture` may write its receipt (#1630)."""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import autocode_capture_command as capture
import autocode_workspaces as workspaces


class CaptureOutputLocationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="capture-location-")
        self.addCleanup(temporary.cleanup)
        # A task worktree inside an outer project, as AutoCode creates them.
        self.outer = Path(temporary.name).resolve()
        self.project = self.outer / ".autocode/worktrees/task"
        # The Investigator's runner-prepared scratch copy of the project.
        self.scratch = self.project / ".autocode/investigation/bug-1"
        self.scratch.mkdir(parents=True)
        previous = Path.cwd()
        self.addCleanup(os.chdir, previous)

    def run_capture(self, cwd, output):
        os.chdir(cwd)
        command = [sys.executable, "-c", "import os; print(os.getcwd())"]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as errors:
            try:
                return capture.cli(["--output", str(output), "--", *command]), errors.getvalue()
            except SystemExit as stop:
                return stop.code, errors.getvalue()

    def test_a_scratch_copy_may_write_to_the_project_evidence_directory(self):
        receipt = self.project / ".autocode/evidence/repro.json"
        code, _ = self.run_capture(self.scratch, receipt)
        self.assertEqual(0, code)
        saved = json.loads(receipt.read_text())
        self.assertEqual(0, saved["exit_code"])
        self.assertEqual(str(self.scratch), Path(saved["full_output"]).read_text().strip())

    def test_a_scratch_copy_may_still_write_beside_itself(self):
        code, _ = self.run_capture(self.scratch, Path(".autocode/evidence/repro.json"))
        self.assertEqual(0, code)
        self.assertTrue((self.scratch / ".autocode/evidence/repro.json").is_file())

    def test_a_relative_output_from_a_project_subfolder_lands_in_the_projects_own_evidence(self):
        # A Go module or monorepo package runs its tests from a subfolder. The receipt used to land in a new
        # client/v3/.autocode there, outside the assigned paths, and the Builder attempt was rejected.
        module = self.project / "client/v3"
        module.mkdir(parents=True)
        # Stages bind the output store to the workspace (autocode_output_policy); the receipt path is the test.
        with unittest.mock.patch.dict(os.environ, {"AUTOCODE_OUTPUT_WORKSPACE": str(self.project)}):
            code, _ = self.run_capture(module, Path(".autocode/evidence/resolver-tests.json"))
        self.assertEqual(0, code)
        saved = json.loads((self.project / ".autocode/evidence/resolver-tests.json").read_text())
        self.assertEqual(str(module), Path(saved["full_output"]).read_text().strip())
        self.assertFalse((module / ".autocode").exists())

    def test_nested_verification_checkout_does_not_reuse_its_parent_receipt(self):
        relative = Path(".autocode/evidence/check.json")
        original = self.project / relative
        original.parent.mkdir()
        original.write_text("original receipt")
        for subfolder in ("", "client"):
            with self.subTest(subfolder=subfolder):
                checkout = self.project / ".autocode/check-replay" / f"copy-{subfolder or 'root'}"
                checkout.mkdir(parents=True)
                subprocess.run(["git", "init", "-q", str(checkout)], check=True)
                cwd = checkout / subfolder
                cwd.mkdir(exist_ok=True)
                code, errors = self.run_capture(cwd, relative)
                self.assertEqual(0, code, errors)
                saved = json.loads((checkout / relative).read_text())
                self.assertEqual(str(cwd), Path(saved["full_output"]).read_text().strip())
                self.assertEqual("original receipt", original.read_text())

    def test_outside_the_nearest_project_autocode_is_refused(self):
        for cwd, output in (
            (self.scratch, self.project / "repro.json"),
            (self.scratch, self.outer / ".autocode/repro.json"),
            (self.project, self.outer / ".autocode/repro.json"),
        ):
            with self.subTest(cwd=cwd, output=output):
                code, errors = self.run_capture(cwd, output)
                self.assertEqual(2, code)
                self.assertIn("evidence output must be under this project's .autocode directory", errors)
                self.assertFalse(output.exists())


REAL_RUN = "20261120-1200-fix-chat-7f31f2cdb7c939"
TYPO_RUN = "20261120-1200-fix-chat-7f31f2ddb7c939"


class CaptureRunIdentityTests(unittest.TestCase):
    """#3892: an --output aimed inside .autocode/runs/<id>/ names a run, and is refused outright
    when no such run exists: before any directory is created and before the command executes."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="capture-run-identity-")
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name).resolve() / "project"
        self.project.mkdir()
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        identity = ["-c", "user.name=T", "-c", "user.email=t@example.test"]
        subprocess.run(
            ["git", "-C", str(self.project), *identity, "commit", "-q", "--allow-empty", "-m", "base"], check=True
        )
        self.runs = self.project / ".autocode" / "runs"
        self.runs.mkdir(parents=True)
        self.real = self.runs / REAL_RUN
        self.real.mkdir()
        (self.real / "state.json").write_text(
            json.dumps({"version": 3, "workspace": str(self.project), "status": "RUNNING"})
        )
        previous = Path.cwd()
        os.chdir(self.project)
        self.addCleanup(os.chdir, previous)

    def run_capture(self, output, *, environment=None):
        """The public CLI entry on a command that would leave marker.txt behind in the project."""
        command = [sys.executable, "-c", "from pathlib import Path; Path('marker.txt').write_text('ran')"]
        env = {key: value for key, value in os.environ.items() if key != "AUTOCODE_CAPTURE_CONTEXT"}
        env.update(environment or {})
        with (
            unittest.mock.patch.dict(os.environ, env, clear=True),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()) as errors,
        ):
            try:
                return capture.cli(["--output", str(output), "--", *command]), errors.getvalue()
            except SystemExit as stop:
                return stop.code, errors.getvalue()

    def moved_run(self, name=REAL_RUN):
        """Carry the run to a real task worktree, both records naming each other as move writes."""
        tree = Path(workspaces.create(self.project, "moved")["workspace"])
        child = tree / ".autocode" / "runs" / name
        child.mkdir(parents=True)
        (self.real / "state.json").write_text(
            json.dumps(
                {
                    "version": 3,
                    "workspace": str(self.project),
                    "status": "PAUSED_REQUESTED",
                    "stop_reason": f"Moved to its own worktree to build: {child}",
                    "build_worktree": {"state": "moved", "moved_to": {"workspace": str(tree), "run_dir": str(child)}},
                }
            )
        )
        (child / "state.json").write_text(
            json.dumps(
                {
                    "version": 3,
                    "workspace": str(tree),
                    "status": "RUNNING",
                    "build_worktree": {
                        "state": "building",
                        "moved_from": {
                            "workspace": str(self.project),
                            "run_dir": str(self.real),
                            "at": "2026-11-20T12:00:00Z",
                        },
                    },
                }
            )
        )
        return tree, child

    def foreign_tree(self, run_id):
        """A directory under worktrees/ whose metadata passes the shape check but whose Git common
        directory is another repository's, so it is not an authenticated task worktree."""
        tree = self.project / ".autocode" / "worktrees" / f"{run_id}-tree"
        tree.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(tree)], check=True)
        (tree / ".autocode").mkdir()
        (tree / ".autocode" / "task-workspace.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "kind": "task",
                    "project_workspace": str(self.project),
                    "workspace": str(tree),
                    "branch": "autocode/x",
                    "base_commit": "0" * 40,
                }
            )
        )
        run = tree / ".autocode" / "runs" / run_id
        run.mkdir(parents=True)
        (run / "state.json").write_text(json.dumps({"version": 3, "workspace": str(tree), "status": "RUNNING"}))
        return tree

    def test_ac1_capture_refuses_a_run_shaped_output_for_an_unknown_run(self):
        output = self.runs / TYPO_RUN / "evidence" / "repro.json"
        code, errors = self.run_capture(output)
        self.assertEqual(2, code)
        self.assertIn(TYPO_RUN, errors)
        self.assertIn("not an existing run", errors)
        # Nothing is left behind: no run-shaped directory for `autocode runs` to enumerate, no .log,
        # no marker from an executed command, and nothing moved or relabeled.
        self.assertFalse((self.runs / TYPO_RUN).exists())
        self.assertFalse((self.project / "marker.txt").exists())

    def test_ac1_the_same_refusal_guards_a_noncanonical_address_through_a_symlink(self):
        alias = self.runs / "alias-of-real"
        alias.symlink_to(self.real)
        code, errors = self.run_capture(alias / "evidence" / "repro.json")
        self.assertEqual(2, code)
        self.assertIn("alias-of-real", errors)
        self.assertIn("not an existing run", errors)
        # No receipt or .log is written into the real run, and both directories are left unchanged.
        self.assertEqual(["state.json"], sorted(item.name for item in self.real.iterdir()))
        self.assertTrue(alias.is_symlink())
        self.assertFalse((self.project / "marker.txt").exists())

    def test_capture_refuses_an_existing_directory_without_a_checkpoint(self):
        orphan = self.runs / TYPO_RUN
        orphan.mkdir()
        code, errors = self.run_capture(orphan / "evidence" / "repro.json")
        self.assertEqual(2, code)
        self.assertIn("not an existing run", errors)
        self.assertEqual([], list(orphan.iterdir()))
        self.assertFalse((self.project / "marker.txt").exists())

    def test_capture_refuses_a_directory_with_a_malformed_checkpoint(self):
        orphan = self.runs / TYPO_RUN
        orphan.mkdir()
        (orphan / "state.json").write_text("{broken")
        code, errors = self.run_capture(orphan / "evidence" / "repro.json")
        self.assertEqual(2, code)
        self.assertIn("not an existing run", errors)
        self.assertEqual(["state.json"], [path.name for path in orphan.iterdir()])
        self.assertFalse((self.project / "marker.txt").exists())

    def test_ac2_capture_refusal_names_the_expected_owner(self):
        output = self.runs / TYPO_RUN / "evidence" / "repro.json"
        # (a) the runner's capture context names the owning run from its attempt path.
        context = {
            "attempt": str(self.real / "iterations" / "001" / "tester-01.json"),
            "nonce": "n1",
            "source_revision": "abc123",
        }
        code, errors = self.run_capture(output, environment={"AUTOCODE_CAPTURE_CONTEXT": json.dumps(context)})
        self.assertEqual(2, code)
        self.assertIn(REAL_RUN, errors)
        self.assertFalse((self.runs / TYPO_RUN).exists())
        # (b) without a context, the single active run saved in the project.
        code, errors = self.run_capture(output)
        self.assertEqual(2, code)
        self.assertIn(REAL_RUN, errors)
        self.assertFalse((self.runs / TYPO_RUN).exists())
        self.assertFalse((self.project / "marker.txt").exists())
        # (c) a run that moved to its authenticated task worktree is found there, and the record
        # left in the checkout is not counted as a second active run.
        tree, child = self.moved_run()
        code, errors = self.run_capture(output)
        self.assertEqual(2, code)
        self.assertIn(REAL_RUN, errors)
        self.assertFalse((self.runs / TYPO_RUN).exists())
        self.assertFalse((self.project / "marker.txt").exists())
        # (d) a worktree-shaped directory of an unrelated repository is never the named owner.
        self.foreign_tree("fake-run-9f3e")
        code, errors = self.run_capture(output)
        self.assertEqual(2, code)
        self.assertIn(REAL_RUN, errors)
        self.assertNotIn("fake-run-9f3e", errors)
        # With only that metadata-only candidate remaining, the refusal omits the owner entirely.
        shutil.rmtree(child)
        code, errors = self.run_capture(output)
        self.assertEqual(2, code)
        self.assertNotIn("fake-run-9f3e", errors)
        self.assertNotIn(REAL_RUN, errors)
        self.assertFalse((self.runs / TYPO_RUN).exists())
        self.assertFalse((self.project / "marker.txt").exists())

    def test_ac3_valid_capture_outputs_still_work(self):
        for label, output in (
            ("run-owned", self.real / "evidence" / "owned.json"),
            ("project evidence", self.project / ".autocode" / "evidence" / "project.json"),
            (
                "contained scratch",
                self.project / ".autocode" / "tool-containment-ab12cd34" / "scratch" / "batch" / "capture.json",
            ),
        ):
            with self.subTest(label=label):
                code, _ = self.run_capture(output)
                self.assertEqual(0, code)
                saved = json.loads(output.read_text())
                self.assertEqual(0, saved["exit_code"])

    def test_ac13_capture_owner_requires_a_matching_move_record(self):
        # No run is saved in the project itself, so the owner can only come from a worktree record.
        (self.real / "state.json").unlink()
        output = self.runs / TYPO_RUN / "evidence" / "repro.json"
        # (a) a genuine worktree whose record claims a move the project's records do not corroborate
        # names no owner: the claimed move does not count as an active run.
        tree = Path(workspaces.create(self.project, "claimed")["workspace"])
        claimed = tree / ".autocode" / "runs" / "wt-claimed-2222"
        claimed.mkdir(parents=True)
        (claimed / "state.json").write_text(
            json.dumps(
                {
                    "version": 3,
                    "workspace": str(tree),
                    "status": "RUNNING",
                    "build_worktree": {
                        "state": "building",
                        "moved_from": {
                            "workspace": str(self.project),
                            "run_dir": str(self.runs / "no-such-origin"),
                        },
                    },
                }
            )
        )
        code, errors = self.run_capture(output)
        self.assertEqual(2, code)
        self.assertIn("not an existing run", errors)
        self.assertNotIn("wt-claimed-2222", errors)
        self.assertFalse((self.runs / TYPO_RUN).exists())
        self.assertFalse((self.project / "marker.txt").exists())
        # (b) a run that started in that worktree still counts and is named as the expected owner.
        started = tree / ".autocode" / "runs" / "wt-start-1111"
        started.mkdir(parents=True)
        (started / "state.json").write_text(json.dumps({"version": 3, "workspace": str(tree), "status": "RUNNING"}))
        code, errors = self.run_capture(output)
        self.assertEqual(2, code)
        self.assertIn("wt-start-1111", errors)
        self.assertNotIn("wt-claimed-2222", errors)
        self.assertFalse((self.runs / TYPO_RUN).exists())
        self.assertFalse((self.project / "marker.txt").exists())


if __name__ == "__main__":
    unittest.main()
