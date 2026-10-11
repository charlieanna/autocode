"""The #980 runner-identity stamp: state names its producer, resume warns on skew.

The idlecampus run-3 lesson: a paused state executed by an older runner snapshot
became unresumable by both old and new runners, and nothing in the state named
its producer — diagnosis required diffing two runner trees. These tests pin the
stamp (path + git commit + dirty flag on every state.json persist), the
runner-changed warning, and the doctor's runner check.
"""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_doctor as doctor
import autocode_run_records as records
import autocode_util as util

IDENTITY_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def committed_repo(directory: Path) -> str:
    subprocess.run(["git", "-C", str(directory), "init", "-q", "-b", "main"], check=True)
    (directory / "marker.txt").write_text("one\n")
    subprocess.run(["git", "-C", str(directory), "add", "."], check=True)
    subprocess.run(["git", "-C", str(directory), "commit", "-q", "-m", "one"], check=True, env=IDENTITY_ENV)
    return subprocess.run(
        ["git", "-C", str(directory), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


class RunnerIdentityTests(unittest.TestCase):
    def test_a_git_checkout_reports_commit_and_cleanliness(self):
        with tempfile.TemporaryDirectory(prefix="runner-id-") as name:
            directory = Path(name)
            commit = committed_repo(directory)
            identity = util.runner_identity(directory)
            self.assertEqual({"path": str(directory), "commit": commit, "dirty": False}, identity)

    def test_uncommitted_changes_mark_the_runner_dirty(self):
        with tempfile.TemporaryDirectory(prefix="runner-dirty-") as name:
            directory = Path(name)
            committed_repo(directory)
            (directory / "scratch.txt").write_text("untracked\n")
            identity = util.runner_identity(directory)
            self.assertTrue(identity["dirty"])
            self.assertIsNotNone(identity["commit"])

    def test_a_non_repo_directory_is_unversioned_not_an_error(self):
        with tempfile.TemporaryDirectory(prefix="runner-norepo-") as name:
            identity = util.runner_identity(Path(name))
            self.assertIsNone(identity["commit"])
            self.assertIsNone(identity["dirty"])
            self.assertEqual(str(Path(name)), identity["path"])


class RunnerChangedTests(unittest.TestCase):
    def test_identical_identities_do_not_warn(self):
        identity = {"path": "/x", "commit": "a" * 40, "dirty": False}
        self.assertIsNone(util.runner_changed(identity, dict(identity)))

    def test_a_moved_commit_warns_naming_both_ends(self):
        warning = util.runner_changed(
            {"commit": "a" * 40, "dirty": False},
            {"commit": "b" * 40, "dirty": False},
        )
        self.assertIn("a" * 12, warning)
        self.assertIn("b" * 12, warning)
        self.assertIn("different autocode code", warning)

    def test_a_dirty_transition_alone_warns(self):
        warning = util.runner_changed(
            {"commit": "a" * 40, "dirty": False},
            {"commit": "a" * 40, "dirty": True},
        )
        self.assertIn("dirty", warning)

    def test_missing_sides_never_warn(self):
        self.assertIsNone(util.runner_changed(None, {"commit": "a" * 40, "dirty": False}))
        self.assertIsNone(util.runner_changed({"commit": "a" * 40, "dirty": False}, None))


class StateStampTests(unittest.TestCase):
    def test_every_state_persist_carries_the_runner_stamp(self):
        with tempfile.TemporaryDirectory(prefix="runner-stamp-") as name:
            directory = Path(name)
            run_dir = directory / ".autocode" / "runs" / "one"
            run_dir.mkdir(parents=True)
            commit = committed_repo(directory)
            state = {"status": "RUNNING", "phase": "EXECUTING", "iteration": 1}
            records.write_json(run_dir / "state.json", state)
            loaded = json.loads((run_dir / "state.json").read_text())
            self.assertEqual({"path", "commit", "dirty"}, set(loaded["runner"]))
            self.assertIsNotNone(loaded["runner"]["commit"])
            self.assertNotEqual(commit, "not-a-repo-marker")

    def test_schema_and_other_files_are_not_stamped(self):
        with tempfile.TemporaryDirectory(prefix="runner-nostamp-") as name:
            directory = Path(name)
            payload = {"type": "object", "properties": {}}
            records.write_json(directory / "schema.json", payload)
            self.assertNotIn("runner", payload)


class DoctorRunnerCheckTests(unittest.TestCase):
    def test_a_versioned_runner_reports_ok_with_the_commit(self):
        with tempfile.TemporaryDirectory(prefix="doctor-runner-") as name:
            directory = Path(name)
            commit = committed_repo(directory)
            check = doctor.runner_check(directory)
            self.assertEqual(doctor.OK, check.status)
            self.assertIn(commit[:12], check.detail)

    def test_an_unversioned_runner_warns_that_states_will_not_identify(self):
        with tempfile.TemporaryDirectory(prefix="doctor-norepo-") as name:
            check = doctor.runner_check(Path(name))
            self.assertEqual(doctor.WARN, check.status)
            self.assertIn("unversioned", check.detail)
            self.assertIn("git checkout", check.fix)

    def test_all_checks_carries_the_runner_check(self):
        workspace = Path(tempfile.mkdtemp(prefix="doctor-runner-all-"))
        self.addCleanup(self._wipe, workspace)
        names = [check.name for check in doctor.all_checks(workspace, runner=fake_runner())]
        self.assertIn("runner", names)

    def _wipe(self, folder):
        import shutil

        shutil.rmtree(folder, ignore_errors=True)


def fake_runner():
    class FakeRunner:
        def __call__(self, *args, **kwargs):
            return FakeRunner()

        def strip(self, *args, **kwargs):
            return ""

        def splitlines(self, *args, **kwargs):
            return []

        def __getattr__(self, name):
            return FakeRunner()

    return FakeRunner()


if __name__ == "__main__":
    unittest.main()
