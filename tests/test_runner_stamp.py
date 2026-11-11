"""The #980 runner-identity stamp: state names its producer, resume warns on skew.

The idlecampus run-3 lesson: a paused state executed by an older runner snapshot
became unresumable by both old and new runners, and nothing in the state named
its producer — diagnosis required diffing two runner trees. These tests pin the
stamp (path + source commit on every state.json persist, restamped only when the
runner moves so idempotent re-saves stay byte-identical), the runner-changed
warning, and the doctor's runner check.
"""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_doctor as doctor
import autocode_run_records as records
import autocode_util as util
import autocode_verification_copy as copies


def commit_source_returning(value):
    return lambda: value


class RunnerIdentityTests(unittest.TestCase):
    def test_the_identity_names_the_runner_path_and_its_source_commit(self):
        identity = util.runner_identity(commit_source_returning("1a2b3c4+modified"))
        self.assertEqual({"path", "commit"}, set(identity))
        self.assertEqual("1a2b3c4+modified", identity["commit"])
        self.assertEqual(str(Path(util.__file__).resolve().parent), identity["path"])

    def test_an_unversioned_runner_reports_commit_none_not_an_error(self):
        identity = util.runner_identity(commit_source_returning(None))
        self.assertIsNone(identity["commit"])


class RunnerChangedTests(unittest.TestCase):
    def test_identical_identities_do_not_warn(self):
        identity = {"path": "/x", "commit": "1a2b3c4"}
        self.assertIsNone(util.runner_changed(identity, dict(identity)))

    def test_a_moved_commit_warns_naming_both_ends(self):
        warning = util.runner_changed(
            {"commit": "aaaaaaaaaaaa"},
            {"commit": "bbbbbbbbbbbb+modified"},
        )
        self.assertIn("aaaaaaaaaaaa", warning)
        self.assertIn("bbbbbbbbbbbb+modified", warning)
        self.assertIn("different autocode code", warning)

    def test_missing_sides_never_warn(self):
        self.assertIsNone(util.runner_changed(None, {"commit": "1a2b3c4"}))
        self.assertIsNone(util.runner_changed({"commit": "1a2b3c4"}, None))


class StateStampTests(unittest.TestCase):
    def test_a_state_persist_gains_the_runner_stamp(self):
        with tempfile.TemporaryDirectory(prefix="runner-stamp-") as name:
            run_dir = Path(name) / ".autocode" / "runs" / "one"
            run_dir.mkdir(parents=True)
            state = {"status": "RUNNING", "phase": "EXECUTING", "iteration": 1}
            records.write_json(run_dir / "state.json", state)
            loaded = json.loads((run_dir / "state.json").read_text())
            self.assertEqual({"path", "commit"}, set(loaded["runner"]))

    def test_an_unchanged_re_save_stays_byte_identical(self):
        """installed_cli_explain asserts a read-only explanation does not mutate the
        run; the stamp must not break that by rewriting state on every save."""
        with tempfile.TemporaryDirectory(prefix="runner-idem-") as name:
            run_dir = Path(name) / ".autocode" / "runs" / "one"
            run_dir.mkdir(parents=True)
            state_path = run_dir / "state.json"
            state = {"status": "RUNNING", "phase": "EXECUTING", "iteration": 1}
            records.write_json(state_path, state)
            first = state_path.read_bytes()
            records.write_json(state_path, json.loads(state_path.read_text()))
            self.assertEqual(first, state_path.read_bytes())

    def test_schema_and_other_files_are_not_stamped(self):
        with tempfile.TemporaryDirectory(prefix="runner-nostamp-") as name:
            directory = Path(name)
            payload = {"type": "object", "properties": {}}
            records.write_json(directory / "schema.json", payload)
            self.assertNotIn("runner", payload)


class DoctorRunnerCheckTests(unittest.TestCase):
    def test_a_versioned_runner_reports_ok_with_the_commit(self):
        check = doctor.runner_check(commit_source_returning("1a2b3c4+modified"))
        self.assertEqual(doctor.OK, check.status)
        self.assertIn("1a2b3c4+modified", check.detail)

    def test_an_unversioned_runner_warns_that_states_will_not_identify(self):
        check = doctor.runner_check(commit_source_returning(None), release=lambda: None)
        self.assertEqual(doctor.WARN, check.status)
        self.assertIn("unversioned", check.detail)
        self.assertIn("git checkout", check.fix)

    def test_an_installed_release_is_identified_without_sending_its_user_to_a_checkout(self):
        check = doctor.runner_check(commit_source_returning(None), release=lambda: "0.8.2")
        self.assertEqual(doctor.OK, check.status)
        self.assertIn("installed release autocode-supervisor 0.8.2", check.detail)
        self.assertEqual("", check.fix)

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


class SourceCommitObservationTests(unittest.TestCase):
    """#3039: the provenance probe only reads; it never rewrites the checkout's index.

    A ``git status`` that finds stale stat data refreshes the index unless optional
    locks are off. Run from an authenticated verification copy, that rewrite made
    every later capture refuse with ``Verification input changed: .git/index``.
    """

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.root = self.home / "autocode"
        (self.root / "tools").mkdir(parents=True)
        (self.root / "pyproject.toml").write_text('[project]\nname = "autocode-supervisor"\nversion = "0.0.1"\n')
        (self.root / "tools/autocode_util.py").write_text("# marker\n")
        (self.root / "tools/other.py").write_text("# other\n")
        self.git_env = {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "HOME": str(self.home),
        }
        self.git("init", "-q")
        self.git("config", "maintenance.auto", "false")
        self.git("config", "gc.auto", "0")
        self.git("add", "-A")
        self.git(
            "-c", "user.name=T", "-c", "user.email=t@localhost", "-c", "commit.gpgsign=false", "commit", "-qm", "c"
        )
        self.head = self.git("rev-parse", "--short", "HEAD").strip()
        self.run_dir = self.root / ".autocode/runs/fixture"
        self.run_dir.mkdir(parents=True)

    def git(self, *args, root=None):
        env = {**os.environ, **self.git_env}
        return subprocess.run(
            ["git", *args], cwd=root or self.root, env=env, check=True, capture_output=True, text=True, timeout=30
        ).stdout

    def probe(self, checkout):
        # The caller asks for optional locks; the reader must still not take them.
        with (
            mock.patch.dict(os.environ, {**self.git_env, "GIT_OPTIONAL_LOCKS": "1"}),
            mock.patch.object(util, "__file__", str(checkout / "tools/autocode_util.py")),
        ):
            return util.source_commit()

    @staticmethod
    def make_stat_stale(checkout, name):
        path = checkout / name
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns - 3_600_000_000_000))

    def test_a_probe_in_a_verification_copy_leaves_its_sealed_index_valid(self):
        allocation = copies.allocate(self.root, self.run_dir)
        tree = Path(allocation["tree"])
        self.make_stat_stale(tree, "tools/autocode_util.py")
        copies.execution(allocation["manifest"], allocation["sha256"], self.root)
        index = (tree / ".git/index").read_bytes()

        self.assertEqual(self.head, self.probe(tree))

        copies.execution(allocation["manifest"], allocation["sha256"], self.root)
        self.assertEqual(index, (tree / ".git/index").read_bytes())

    def test_a_modified_checkout_is_reported_without_rewriting_its_index(self):
        (self.root / "tools/autocode_util.py").write_text("# changed marker\n")
        self.make_stat_stale(self.root, "tools/other.py")
        index = (self.root / ".git/index").read_bytes()

        self.assertEqual(self.head + "+modified", self.probe(self.root))

        self.assertEqual(index, (self.root / ".git/index").read_bytes())


if __name__ == "__main__":
    unittest.main()
