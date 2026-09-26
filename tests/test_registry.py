"""Registry persistence and read-only discovery tests using isolated storage."""
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocode_registry as registry


def register_in_process(home, workspace, run_dir):
    os.environ["AUTOCODE_HOME"] = home
    state = json.loads((Path(run_dir) / "state.json").read_text())
    registry.register_run(Path(workspace), Path(run_dir), state)


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "registry-home"
        self.environment = patch.dict(os.environ, {"AUTOCODE_HOME": str(self.home)})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def fixture(self, name):
        workspace = self.root / name
        workspace.mkdir()
        subprocess.run(["git", "init", "-q", str(workspace)], check=True)
        subprocess.run(["git", "-C", str(workspace), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                        "commit", "--allow-empty", "-qm", "fixture"], check=True)
        run = workspace / ".autocode/runs/fixture"
        run.mkdir(parents=True)
        state = {"version": 3, "workspace": str(workspace.resolve()), "task": "fixture", "status": "RUNNING",
                 "task_id": "run-task-id", "current_task": {"id": "implementation-task-id"}}
        (run / "state.json").write_text(json.dumps(state))
        return workspace.resolve(), run.resolve(), state

    def test_absent_location_and_list_are_read_only(self):
        self.assertFalse(self.home.exists())
        self.assertFalse(registry.location()["exists"])
        listed = registry.listing()
        self.assertFalse(listed["registry_exists"])
        self.assertEqual("registry_absent", listed["diagnostics"][0]["code"])
        self.assertFalse(self.home.exists())

    def test_workspace_storage_defaults_to_workspace_and_preserves_explicit_shared_home(self):
        workspace, _, _ = self.fixture("workspace")
        with patch.dict(os.environ, {}, clear=True):
            registry.configure_workspace_storage(workspace)
            self.assertEqual(str(workspace / ".autocode" / "registry"), os.environ["AUTOCODE_HOME"])
        with patch.dict(os.environ, {"AUTOCODE_HOME": str(self.home)}, clear=True):
            registry.configure_workspace_storage(workspace)
            self.assertEqual(str(self.home), os.environ["AUTOCODE_HOME"])

    def test_register_deduplicates_canonical_alias_and_lists_checkpoint_summary(self):
        workspace, run, state = self.fixture("workspace")
        first = registry.register_run(workspace, run, state)
        alias = self.root / "alias"
        alias.symlink_to(workspace, target_is_directory=True)
        second = registry.register_run(alias, alias / ".autocode/runs/fixture", state)
        self.assertEqual(first, second)
        listed = registry.listing()
        self.assertEqual(1, len(listed["workspaces"]))
        self.assertEqual(1, len(listed["runs"]))
        self.assertEqual("available", listed["runs"][0]["availability"])
        self.assertEqual("RUNNING", listed["runs"][0]["diagnostic"]["status"])
        self.assertEqual("run-task-id", listed["runs"][0]["task_id"])

    def test_listing_derives_run_task_id_after_migration_without_mutating_registry(self):
        workspace, run, state = self.fixture("workspace")
        state.pop("task_id")
        registry.register_run(workspace, run, state)
        before = registry.registry_path().read_bytes()
        state["task_id"] = "run-task-assigned-later"
        state["current_task"] = {"id": "unrelated-implementation-assignment"}
        (run / "state.json").write_text(json.dumps(state))
        listed = registry.listing()
        self.assertEqual("run-task-assigned-later", listed["runs"][0]["task_id"])
        self.assertEqual(before, registry.registry_path().read_bytes())

    def test_legacy_checkpoint_without_task_id_remains_readable(self):
        workspace, run, state = self.fixture("workspace")
        state.pop("task_id")
        state.pop("version")
        state.pop("current_task")
        (run / "state.json").write_text(json.dumps(state))
        registry.register_run(workspace, run, state)
        listed = registry.listing()
        self.assertEqual("available", listed["runs"][0]["availability"])
        self.assertIsNone(listed["runs"][0]["task_id"])

    def test_multiprocess_registrations_keep_every_acknowledged_run(self):
        fixtures = [self.fixture(f"workspace-{index}") for index in range(4)]
        workers = [multiprocessing.Process(target=register_in_process, args=(str(self.home), str(workspace), str(run)))
                   for workspace, run, _ in fixtures]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(10)
            self.assertEqual(0, worker.exitcode)
        listed = registry.listing()
        self.assertEqual(4, len(listed["runs"]))
        self.assertEqual({str(run) for _, run, _ in fixtures}, {item["run_dir"] for item in listed["runs"]})

    def test_corrupt_registry_is_not_replaced_and_stale_records_remain_diagnostic(self):
        workspace, run, state = self.fixture("workspace")
        self.home.mkdir()
        path = registry.registry_path()
        path.write_text("not json")
        with self.assertRaisesRegex(registry.RegistryError, "cannot be read"):
            registry.register_run(workspace, run, state)
        self.assertEqual("not json", path.read_text())
        path.write_text(json.dumps({"version": 1, "workspaces": {}, "runs": {"bad": {"id": "bad"}}}))
        listed = registry.listing()
        self.assertEqual("malformed_record", listed["runs"][0]["availability"])
        self.assertEqual("bad", listed["runs"][0]["id"])

    def test_listing_reports_invalid_identities_and_checkpoint_shapes_without_writes(self):
        workspace, run, state = self.fixture("workspace")
        registered = registry.register_run(workspace, run, state)
        path = registry.registry_path()
        document = json.loads(path.read_text())
        run_record = document["runs"][registered["run_id"]]
        run_record["id"] = "forged"
        path.write_text(json.dumps(document))
        before = path.read_bytes()
        self.assertEqual("malformed_record", registry.listing()["runs"][0]["availability"])
        self.assertEqual(before, path.read_bytes())

        run_record["id"] = registered["run_id"]
        document["runs"]["duplicate-canonical-alias"] = dict(run_record)
        path.write_text(json.dumps(document))
        listed = registry.listing()
        self.assertIn("malformed_record", {item["availability"] for item in listed["runs"]})
        document["runs"].pop("duplicate-canonical-alias")
        state["version"] = 999
        (run / "state.json").write_text(json.dumps(state))
        path.write_text(json.dumps(document))
        before = (path.read_bytes(), (run / "state.json").read_bytes())
        listed = registry.listing()
        self.assertEqual("checkpoint_unsupported", listed["runs"][0]["availability"])
        self.assertEqual(before, (path.read_bytes(), (run / "state.json").read_bytes()))

        state.pop("task")
        state["version"] = 3
        (run / "state.json").write_text(json.dumps(state))
        self.assertEqual("checkpoint_malformed", registry.listing()["runs"][0]["availability"])

    def test_listing_reports_invalid_containment_and_symlinked_checkpoint(self):
        workspace, run, state = self.fixture("workspace")
        registered = registry.register_run(workspace, run, state)
        path = registry.registry_path()
        document = json.loads(path.read_text())
        document["runs"][registered["run_id"]]["run_dir"] = str((workspace / "outside").resolve())
        path.write_text(json.dumps(document))
        self.assertEqual("malformed_record", registry.listing()["runs"][0]["availability"])

        registry.register_run(workspace, run, state)
        state_path = run / "state.json"
        saved = state_path.read_text()
        state_path.unlink()
        target = run / "state-copy.json"
        target.write_text(saved)
        state_path.symlink_to(target)
        self.assertEqual("checkpoint_missing", registry.listing()["runs"][0]["availability"])

    def test_registration_rejects_checkpoint_identity_mismatch(self):
        workspace, run, state = self.fixture("workspace")
        state["workspace"] = str(self.root / "other")
        with self.assertRaisesRegex(registry.RegistryError, "does not match"):
            registry.register_run(workspace, run, state)
        self.assertFalse(self.home.exists())

    def test_atomic_write_and_lock_failures_do_not_acknowledge_or_replace_entries(self):
        workspace, run, state = self.fixture("workspace")
        registry.register_run(workspace, run, state)
        before = registry.registry_path().read_bytes()
        second_workspace, second_run, second_state = self.fixture("second-workspace")
        with patch.object(registry.support, "atomic_json", side_effect=OSError("fixture interruption")):
            with self.assertRaisesRegex(registry.RegistryError, "update failed"):
                registry.register_run(second_workspace, second_run, second_state)
        self.assertEqual(before, registry.registry_path().read_bytes())

        def blocked_lock(_handle, operation):
            if operation & registry.fcntl.LOCK_NB:
                raise BlockingIOError()

        with patch.object(registry, "LOCK_TIMEOUT_SECONDS", 0), patch.object(registry.fcntl, "flock", side_effect=blocked_lock):
            with self.assertRaisesRegex(registry.RegistryError, "busy"):
                registry.register_run(second_workspace, second_run, second_state)
        self.assertEqual(before, registry.registry_path().read_bytes())

    def test_interruption_after_atomic_replace_preserves_acknowledged_entries_on_restart(self):
        workspace, run, state = self.fixture("workspace")
        acknowledged = registry.register_run(workspace, run, state)
        second_workspace, second_run, _ = self.fixture("second-workspace")
        script = """
import json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import autocode_registry as registry
old = registry.support.atomic_json
def interrupted(path, value):
    old(path, value)
    os._exit(75)
registry.support.atomic_json = interrupted
state = json.loads((Path(sys.argv[3]) / 'state.json').read_text())
registry.register_run(Path(sys.argv[2]), Path(sys.argv[3]), state)
"""
        result = subprocess.run([sys.executable, "-c", script, str(Path(__file__).parent), str(second_workspace), str(second_run)],
                                env={**os.environ, "AUTOCODE_HOME": str(self.home)}, capture_output=True, text=True)
        self.assertEqual(75, result.returncode, result.stdout + result.stderr)
        restarted = registry.listing()
        self.assertIn(acknowledged["run_id"], {item["id"] for item in restarted["runs"]})
        self.assertEqual({str(run), str(second_run)}, {item["run_dir"] for item in restarted["runs"]})

if __name__ == "__main__":
    unittest.main()
