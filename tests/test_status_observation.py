"""One bounded budget covers all nested, read-only status observations."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autocode_status_observation as observation
import autocode_status_observation_worker as worker
import autocode_verification_inspection as verification


class StatusObservationTests(unittest.TestCase):
    def test_suite_reports_fail_closed_on_timeout_or_malformed_worker_output(self):
        with patch.object(observation, "_observe", side_effect=subprocess.TimeoutExpired("worker", 1)):
            self.assertEqual([], observation.suite_reports({}, "/tmp/project", "/tmp/run"))
        with patch.object(
            observation, "_observe", return_value=[{"kind": "collection", "independently_verified": True}]
        ):
            self.assertEqual([], observation.suite_reports({}, "/tmp/project", "/tmp/run"))

    def test_nested_observations_share_one_deadline_and_do_not_launch_after_it(self):
        result = SimpleNamespace(stdout='{"result": {"ok": true}}')
        run = Mock(return_value=result)

        @observation.budgeted
        def project():
            observation._observe("first", {})
            observation._observe("second", {})
            with self.assertRaises(subprocess.TimeoutExpired):
                observation._observe("third", {})

        with (
            patch.object(observation.time, "monotonic", side_effect=[100, 101, 110, 116]),
            patch.object(observation.captured, "run", run),
        ):
            project()
        self.assertEqual([14.0, 5.0], [call.kwargs["timeout"] for call in run.call_args_list])

    def test_failed_worker_is_not_an_observation(self):
        with patch.object(observation.captured, "run", side_effect=subprocess.TimeoutExpired("worker", 1)):
            self.assertIsNone(observation.report_repair({}, "/tmp/project"))
        with patch.object(observation, "_observe", return_value={"revision": "fresh", "stale": True}):
            self.assertEqual({"revision": "fresh", "stale": True}, observation.report_repair({}, "/tmp/project"))

    def test_uncertain_cleanup_blocks_later_observations_in_the_same_projection(self):
        run = Mock(side_effect=observation.captured.ProcessError("cleanup unconfirmed"))

        @observation.budgeted
        def project():
            self.assertIsNone(observation.source_snapshot({}, "/tmp/project"))
            self.assertIsNone(observation.report_repair({}, "/tmp/project"))

        with patch.object(observation.captured, "run", run):
            project()
        self.assertEqual(1, run.call_count)

    def test_evidence_worker_rereads_old_checkpoint_with_saved_name_migration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "state.json").write_text('{"next_stage": "terra", "settings": {"roles": {"terra": {}}}}')
            request = {
                "operation": "evidence_inspection",
                "payload": {
                    "state": {},
                    "workspace": str(root),
                    "run_dir": str(root),
                    "accepted_human_ids": [],
                    "initial_snapshot": None,
                },
            }
            with patch.object(verification, "inspect", side_effect=lambda *args, **kwargs: kwargs["read_state"]()):
                result = worker._worker(request)
        self.assertEqual("builder", result["next_stage"])


if __name__ == "__main__":
    unittest.main()
