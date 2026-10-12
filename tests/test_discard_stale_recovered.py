"""discard_stale_recovered sets a stale recovered result aside without losing the checkpoint (#1115)."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_run_actions as actions
import autocode_run_records as records


class DiscardStaleRecoveredTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="discard-stale-")
        self.addCleanup(temporary.cleanup)
        self.run_dir = Path(temporary.name)
        stage_dir = self.run_dir / "iterations" / "001"
        stage_dir.mkdir(parents=True)
        self.output = stage_dir / "terra-01.json"
        self.prompt = stage_dir / "terra-01.prompt.md"
        self.output.write_text('{"summary": "stale"}')
        self.prompt.write_text("prompt")
        self.record = {
            "stage": "terra",
            "role": "terra",
            "iteration": 1,
            "output": str(self.output),
            "prompt": str(self.prompt),
        }
        self.state = {"status": "PAUSED_STALE_VALIDATION", "stop_reason": "stale", "active_stage": self.record}

    def test_the_originals_go_only_after_the_state_that_replaces_them_is_saved(self):
        actions.discard_stale_recovered(self.state, self.run_dir, self.record)
        saved = json.loads((self.run_dir / "state.json").read_text())
        self.assertEqual("RUNNING", saved["status"])
        self.assertEqual("terra", saved["next_stage"])
        self.assertNotIn("active_stage", saved)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.prompt.exists())
        archived = Path(saved["stages"][-1]["archived_paths"][str(self.output)])
        self.assertEqual('{"summary": "stale"}', archived.read_text())

    def test_a_failed_save_leaves_the_originals_the_saved_state_still_names(self):
        with patch.object(records, "write_json", side_effect=OSError(28, "No space left on device")):
            with self.assertRaises(OSError):
                actions.discard_stale_recovered(self.state, self.run_dir, self.record)
        self.assertTrue(self.output.is_file(), "the original output was deleted before the state was saved")
        self.assertTrue(self.prompt.is_file())
        self.assertFalse((self.run_dir / "state.json").exists())


if __name__ == "__main__":
    unittest.main()
