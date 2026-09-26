"""Bounded, non-migrating registry import tests using workspace-local fixtures."""
# path bootstrap: runtime in tools/, fakes in tests/fakes/
import sys as _sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[2] if 'fakes' in _Path(__file__).parts else _Path(__file__).resolve().parents[1]
_TOOLS = _ROOT / 'tools'
_FAKES = _ROOT / 'tests' / 'fakes'
for _p in (_ROOT, _TOOLS, _ROOT / 'tests', _FAKES):
    _s = str(_p)
    if _s not in _sys.path:
        _sys.path.insert(0, _s)
import json
import io
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

_ROOT = _Path(__file__).resolve().parents[1] if _Path(__file__).name != 'live_trial.py' else _Path(__file__).resolve().parent.parent
for _p in (_ROOT, _ROOT / 'tools', _ROOT / 'tests', _ROOT / 'tests' / 'fakes'):
    _s = str(_p)
    if _s not in _sys.path:
        _sys.path.insert(0, _s)
import autocode_registry as registry


def import_in_process(home, selected_root):
    os.environ["AUTOCODE_HOME"] = home
    registry.registry_import(Path(selected_root))


class RegistryImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "registry-home"
        self.environment = patch.dict(os.environ, {"AUTOCODE_HOME": str(self.home)})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def fixture(self, relative, *, task_id="task-id", state=None):
        workspace = self.root / relative
        workspace.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(workspace)], check=True)
        run = workspace / ".autocode/runs/run"
        run.mkdir(parents=True)
        checkpoint = state or {"version": 3, "workspace": str(workspace.resolve()), "task": "fixture",
                               "status": "RUNNING", "task_id": task_id}
        (run / "state.json").write_text(json.dumps(checkpoint))
        return workspace.resolve(), run.resolve()

    def test_default_depth_counts_selected_root_and_preserves_checkpoint_bytes(self):
        root_workspace, root_run = self.fixture("selected")
        nested_workspace, nested_run = self.fixture("selected/a/b/nested")
        too_deep_workspace, _ = self.fixture("selected/a/b/c/too-deep")
        before = {run: (run / "state.json").read_bytes() for run in (root_run, nested_run)}
        result = registry.registry_import(root_workspace)
        self.assertEqual(str(root_workspace), result["selected_root"])
        self.assertEqual(3, result["max_depth"])
        self.assertTrue(result["complete"])
        self.assertEqual({str(root_run), str(nested_run)}, {item["run_dir"] for item in registry.listing()["runs"]})
        self.assertNotIn(str(too_deep_workspace), {item["workspace"] for item in registry.listing()["workspaces"]})
        self.assertEqual(before, {run: (run / "state.json").read_bytes() for run in before})

    def test_budget_aliases_and_escapes_are_truthful_and_idempotent(self):
        selected, run = self.fixture("selected")
        outside, outside_run = self.fixture("outside")
        (selected / "inside").mkdir()
        (selected / "alias").symlink_to(selected, target_is_directory=True)
        (selected / "escape").symlink_to(outside, target_is_directory=True)
        limited = registry.registry_import(selected, directory_budget=1)
        self.assertFalse(limited["complete"])
        self.assertIn("budget_exhausted", {item["code"] for item in limited["diagnostics"]})
        self.assertEqual([], limited["imported"])
        result = registry.registry_import(selected, directory_budget=20)
        self.assertEqual(1, len(result["imported"]))
        self.assertEqual([], result["already_registered"])
        codes = {item["code"] for item in result["diagnostics"]}
        self.assertIn("duplicate_directory", codes)
        self.assertIn("symlink_escape", codes)
        self.assertEqual({str(run)}, {item["run_dir"] for item in registry.listing()["runs"]})
        repeated = registry.registry_import(selected, directory_budget=20)
        self.assertEqual([], repeated["imported"])
        self.assertEqual(1, len(repeated["already_registered"]))
        self.assertNotIn(str(outside_run), {item["run_dir"] for item in registry.listing()["runs"]})

    def test_budget_bounds_run_candidate_inspection_and_retry(self):
        selected, first_run = self.fixture("selected")
        runs_dir = first_run.parent
        runs = [first_run]
        for number in range(1, 5):
            run = runs_dir / f"run-{number}"
            run.mkdir()
            (run / "state.json").write_text(json.dumps({"version": 3, "workspace": str(selected),
                "task": f"fixture-{number}", "status": "RUNNING", "task_id": f"task-{number}"}))
            runs.append(run)

        limited = registry.registry_import(selected, max_depth=0, directory_budget=3)
        self.assertFalse(limited["complete"])
        self.assertEqual(3, limited["directories_inspected"])
        self.assertEqual(2, len(limited["imported"]))
        self.assertIn("budget_exhausted", {item["code"] for item in limited["diagnostics"]})
        self.assertEqual(2, len(registry.listing()["runs"]))

        retried = registry.registry_import(selected, max_depth=0, directory_budget=10)
        self.assertTrue(retried["complete"])
        self.assertEqual(6, retried["directories_inspected"])
        self.assertEqual(3, len(retried["imported"]))
        self.assertEqual(2, len(retried["already_registered"]))
        self.assertEqual({str(run) for run in runs}, {item["run_dir"] for item in registry.listing()["runs"]})

    def test_runs_enumeration_failure_is_incomplete_and_cli_returns_partial_exit(self):
        selected, _ = self.fixture("selected")
        runs_dir = selected / ".autocode" / "runs"
        original_iterdir = Path.iterdir

        def fail_runs_enumeration(path):
            if path == runs_dir:
                raise PermissionError("denied runs enumeration")
            return original_iterdir(path)

        with patch.object(Path, "iterdir", fail_runs_enumeration):
            result = registry.registry_import(selected, max_depth=0)
            output = io.StringIO()
            with patch("sys.stdout", output):
                exit_code = registry.cli(["import", str(selected), "--max-depth", "0", "--json"])
        self.assertFalse(result["complete"])
        self.assertEqual([], result["imported"])
        self.assertIn("inaccessible", {item["code"] for item in result["diagnostics"]})
        self.assertEqual(1, exit_code)
        self.assertFalse(json.loads(output.getvalue())["complete"])
        self.assertEqual([], registry.listing()["runs"])

    def test_malformed_legacy_and_duplicate_task_ids_do_not_mutate_candidates(self):
        selected = self.root / "selected"
        selected.mkdir()
        first_workspace, first_run = self.fixture("selected/first", task_id="shared")
        second_workspace, second_run = self.fixture("selected/second", task_id="shared")
        legacy_workspace, legacy_run = self.fixture("selected/legacy", state={
            "workspace": str((selected / "legacy").resolve()), "task": "legacy", "status": "PAUSED"})
        malformed_workspace, malformed_run = self.fixture("selected/malformed")
        (malformed_run / "state.json").write_text("not json")
        before = {run: (run / "state.json").read_bytes() for run in
                  (first_run, second_run, legacy_run, malformed_run)}
        result = registry.registry_import(selected)
        self.assertEqual(3, len(result["imported"]))
        self.assertEqual({str(first_run), str(second_run), str(legacy_run)},
                         {item["run_dir"] for item in registry.listing()["runs"]})
        self.assertIn("checkpoint_malformed", {item["code"] for item in result["diagnostics"]})
        self.assertEqual(before, {run: (run / "state.json").read_bytes() for run in before})
        self.assertEqual(2, sum(item["task_id"] == "shared" for item in registry.listing()["runs"]))

    def test_inconsistent_and_symlinked_checkpoints_are_diagnostic_only(self):
        selected = self.root / "selected"
        selected.mkdir()
        mismatch_workspace, mismatch_run = self.fixture("selected/mismatch")
        mismatch = json.loads((mismatch_run / "state.json").read_text())
        mismatch["workspace"] = str(selected / "elsewhere")
        (mismatch_run / "state.json").write_text(json.dumps(mismatch))
        symlink_workspace, symlink_run = self.fixture("selected/symlink")
        state_path = symlink_run / "state.json"
        target = symlink_run / "state-copy.json"
        target.write_bytes(state_path.read_bytes())
        state_path.unlink()
        state_path.symlink_to(target)
        before = {mismatch_run: (mismatch_run / "state.json").read_bytes(),
                  symlink_run: (symlink_run / "state.json").readlink()}
        result = registry.registry_import(selected)
        self.assertEqual([], result["imported"])
        self.assertEqual({"checkpoint_malformed", "checkpoint_missing"},
                         {item["code"] for item in result["diagnostics"]})
        self.assertEqual(before[mismatch_run], (mismatch_run / "state.json").read_bytes())
        self.assertEqual(before[symlink_run], (symlink_run / "state.json").readlink())

    def test_invalid_bounds_and_registry_write_failure_are_reported_without_acknowledgment(self):
        selected, run = self.fixture("selected")
        with self.assertRaisesRegex(registry.RegistryError, "depth"):
            registry.registry_import(selected, max_depth=-1)
        with self.assertRaisesRegex(registry.RegistryError, "budget"):
            registry.registry_import(selected, directory_budget=0)
        with patch.object(registry.support, "atomic_json", side_effect=OSError("full")):
            result = registry.registry_import(selected)
        self.assertFalse(result["complete"])
        self.assertEqual("write_failed", result["registry_error"]["code"])
        self.assertEqual([], result["imported"])
        self.assertFalse(registry.registry_path().exists())

    def test_cli_reports_invalid_bound_as_json_error(self):
        selected, _ = self.fixture("selected")
        result = subprocess.run([sys.executable, str(Path(__file__).with_name("autocode.py")), "registry", "import",
                                 str(selected), "--max-depth", "-1", "--json"], capture_output=True, text=True, check=False)
        self.assertEqual(2, result.returncode)
        self.assertEqual("invalid_depth", json.loads(result.stdout)["error"]["code"])

    def test_import_and_automatic_registration_preserve_acknowledged_pointers(self):
        selected = self.root / "selected"
        selected.mkdir()
        imported_workspace, imported_run = self.fixture("selected/imported")
        registered_workspace, registered_run = self.fixture("registered")
        state = json.loads((registered_run / "state.json").read_text())
        worker = multiprocessing.Process(target=import_in_process, args=(str(self.home), str(selected)))
        worker.start()
        registry.register_run(registered_workspace, registered_run, state)
        worker.join(10)
        self.assertEqual(0, worker.exitcode)
        self.assertEqual({str(imported_run), str(registered_run)},
                         {item["run_dir"] for item in registry.listing()["runs"]})


if __name__ == "__main__":
    unittest.main()
