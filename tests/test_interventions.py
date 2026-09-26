"""Pending-only intervention submission tests with no competing state writer."""
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
import autocode_interventions as interventions
import autocode_support as support


def submit_in_process(workspace, run_dir, request_id):
    interventions.submit(Path(workspace), Path(run_dir), request_id=request_id, kind="feedback", text=request_id)


class InterventionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "registry-home"
        self.environment = patch.dict(os.environ, {"AUTOCODE_HOME": str(self.home), "PYTHONDONTWRITEBYTECODE": "1"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        self.run = self.workspace / ".autocode/runs/fixture"
        self.run.mkdir(parents=True)
        self.state_path = self.run / "state.json"
        self.state_path.write_text(json.dumps({"version": 3, "workspace": str(self.workspace), "task": "fixture",
            "status": "RUNNING", "goal_contract": {"revision": 2, "hash": "goal-hash"}}))
        self.entry = [sys.executable, str(Path(__file__).with_name("autocode.py"))]

    def cli(self, *args):
        return subprocess.run([*self.entry, "intervention", *args], cwd=self.root, env=os.environ.copy(),
                              capture_output=True, text=True, check=False)

    def test_busy_workspace_submission_via_cli_preserves_state(self):
        before = self.state_path.read_bytes()
        with support.workspace_lock(self.workspace):
            feedback = self.cli("submit", "--workspace", str(self.workspace), "--run-dir", str(self.run),
                                "--request-id", "feedback-1", "--kind", "feedback", "--text", "Keep the draft", "--json")
            pause = self.cli("submit", "--workspace", str(self.workspace), "--run-dir", str(self.run),
                             "--request-id", "pause-1", "--kind", "pause", "--json")
        self.assertEqual(0, feedback.returncode, feedback.stdout + feedback.stderr)
        self.assertEqual(0, pause.returncode, pause.stdout + pause.stderr)
        receipt = json.loads(feedback.stdout)["receipt"]
        self.assertEqual("r2:goal-hash", receipt["observed_goal_token"])
        self.assertTrue(receipt["boundary_pause_requested"])
        self.assertEqual(before, self.state_path.read_bytes())
        inspected = self.cli("inspect", "--workspace", str(self.workspace), "--run-dir", str(self.run), "--json")
        self.assertEqual(0, inspected.returncode, inspected.stdout + inspected.stderr)
        payload = json.loads(inspected.stdout)
        self.assertEqual("pending_only", payload["consumer"])
        self.assertEqual(["feedback-1", "pause-1"], [item["id"] for item in payload["requests"]])

    def test_idempotent_retry_and_conflict_do_not_change_history(self):
        first = interventions.submit(self.workspace, self.run, request_id="same", kind="feedback", text="Original")
        before = (self.run / interventions.INBOX_NAME).read_bytes()
        repeated = interventions.submit(self.workspace, self.run, request_id="same", kind="feedback", text="Original")
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(first["receipt"], repeated["receipt"])
        self.assertEqual(before, (self.run / interventions.INBOX_NAME).read_bytes())
        with self.assertRaisesRegex(interventions.InterventionError, "different payload"):
            interventions.submit(self.workspace, self.run, request_id="same", kind="feedback", text="Changed")
        self.assertEqual(before, (self.run / interventions.INBOX_NAME).read_bytes())

    def test_applied_idempotent_retry_returns_original_receipt_and_rejects_conflict(self):
        first = interventions.submit(self.workspace, self.run, request_id="applied", kind="feedback", text="Original")
        state = json.loads(self.state_path.read_text())

        def write_state():
            support.atomic_json(self.state_path, state)

        interventions.consume(self.run, state, write_state=write_state, apply_feedback=lambda *_: None)
        repeated = interventions.submit(self.workspace, self.run, request_id="applied", kind="feedback", text="Original")
        self.assertTrue(repeated["idempotent"])
        self.assertEqual("already_applied", repeated["consumer"])
        self.assertEqual(first["receipt"], repeated["receipt"])
        self.assertEqual([], interventions.inspect(self.workspace, self.run)["requests"])
        with self.assertRaisesRegex(interventions.InterventionError, "different payload"):
            interventions.submit(self.workspace, self.run, request_id="applied", kind="pause", text="")

    def test_empty_inbox_recovery_clears_applied_acknowledgement_marker(self):
        state = json.loads(self.state_path.read_text())
        state["applied_interventions"] = [{"id": "recovered", "kind": "pause", "text": "", "order": 1,
                                            "submitted_at": "then", "observed_goal_token": None,
                                            "boundary_pause_requested": True, "applied_at": "now"}]
        state["intervention_ack_pending"] = ["recovered"]
        writes = []

        def write_state():
            writes.append(True)
            support.atomic_json(self.state_path, state)

        self.assertEqual([], interventions.consume(self.run, state, write_state=write_state, apply_feedback=lambda *_: None))
        self.assertEqual([True], writes)
        self.assertNotIn("intervention_ack_pending", state)
        self.assertNotIn("intervention_ack_pending", json.loads(self.state_path.read_text()))

    def test_multiprocess_submissions_are_all_durable_and_ordered(self):
        workers = [multiprocessing.Process(target=submit_in_process, args=(str(self.workspace), str(self.run), f"request-{index}"))
                   for index in range(6)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(10)
            self.assertEqual(0, worker.exitcode)
        requests = interventions.inspect(self.workspace, self.run)["requests"]
        self.assertEqual({f"request-{index}" for index in range(6)}, {item["id"] for item in requests})
        self.assertEqual(list(range(1, 7)), sorted(item["order"] for item in requests))

    def test_symlinked_inbox_and_invalid_target_cannot_write_outside_run(self):
        outside = self.root / "outside.json"
        outside.write_text("outside")
        (self.run / interventions.INBOX_NAME).symlink_to(outside)
        with self.assertRaisesRegex(interventions.InterventionError, "must not be a symlink"):
            interventions.submit(self.workspace, self.run, request_id="bad", kind="pause", text="")
        self.assertEqual("outside", outside.read_text())
        outside_run = self.root / "outside-run"
        outside_run.mkdir()
        with self.assertRaisesRegex(interventions.InterventionError, "contained"):
            interventions.submit(self.workspace, outside_run, request_id="bad", kind="pause", text="")

    def test_write_failure_and_read_only_inspection_are_honest(self):
        before_state = self.state_path.read_bytes()
        before_entries = sorted(path.relative_to(self.run) for path in self.run.iterdir())
        self.assertEqual([], interventions.inspect(self.workspace, self.run)["requests"])
        self.assertEqual(before_entries, sorted(path.relative_to(self.run) for path in self.run.iterdir()))
        self.assertEqual(before_state, self.state_path.read_bytes())
        with patch.object(interventions.support, "atomic_json", side_effect=OSError("full")):
            with self.assertRaisesRegex(interventions.InterventionError, "update failed"):
                interventions.submit(self.workspace, self.run, request_id="failed", kind="pause", text="")
        self.assertFalse((self.run / interventions.INBOX_NAME).exists())

    def test_corrupt_inbox_and_lock_contention_do_not_acknowledge_submission(self):
        inbox = self.run / interventions.INBOX_NAME
        inbox.write_text(json.dumps({"version": 1, "requests": [{}]}))
        with self.assertRaisesRegex(interventions.InterventionError, "invalid or duplicate"):
            interventions.submit(self.workspace, self.run, request_id="bad", kind="pause", text="")
        self.assertEqual(json.dumps({"version": 1, "requests": [{}]}), inbox.read_text())
        inbox.unlink()

        def blocked_lock(_handle, operation):
            if operation & interventions.fcntl.LOCK_NB:
                raise BlockingIOError()

        with patch.object(interventions, "LOCK_TIMEOUT_SECONDS", 0), patch.object(interventions.fcntl, "flock", side_effect=blocked_lock):
            with self.assertRaisesRegex(interventions.InterventionError, "busy"):
                interventions.submit(self.workspace, self.run, request_id="blocked", kind="pause", text="")
        self.assertFalse(inbox.exists())

    def test_malformed_optional_goal_contract_returns_a_receipt_without_a_token(self):
        self.state_path.write_text(json.dumps({"version": 3, "workspace": str(self.workspace), "task": "fixture",
            "status": "RUNNING", "goal_contract": {"unexpected": "shape"}}))
        result = self.cli("submit", "--workspace", str(self.workspace), "--run-dir", str(self.run),
                          "--request-id", "malformed-contract", "--kind", "pause", "--json")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIsNone(json.loads(result.stdout)["receipt"]["observed_goal_token"])


if __name__ == "__main__":
    unittest.main()
