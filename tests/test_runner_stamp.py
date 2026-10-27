"""The #980 runner-identity stamp: state names its producer, resume warns on skew.

The idlecampus run-3 lesson: a paused state executed by an older runner snapshot
became unresumable by both old and new runners, and nothing in the state named
its producer — diagnosis required diffing two runner trees. These tests pin the
stamp (path + source commit on every state.json persist, restamped only when the
runner moves so idempotent re-saves stay byte-identical), the runner-changed
warning, and the doctor's runner check.
"""

import json
import tempfile
import unittest
from pathlib import Path

import autocode_doctor as doctor
import autocode_run_records as records
import autocode_util as util


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


if __name__ == "__main__":
    unittest.main()
