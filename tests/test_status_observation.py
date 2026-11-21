"""One bounded budget covers all nested, read-only status observations."""

import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autocode_status_observation as observation


class StatusObservationTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
