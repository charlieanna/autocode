"""Test-only busy-process observations retain no command arguments."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import psutil

from tests import job_route_vscode_cli as diagnostics


class HostActionDiagnosticsTests(unittest.TestCase):
    def test_busy_process_sample_contains_identity_and_only_safe_classification(self):
        process = SimpleNamespace(
            create_time=lambda: 123.0,
            cmdline=lambda: ["python3", "/runtime/autocode.py", "--run-dir", "/private/run", "--status", "--json"],
        )
        with patch.object(psutil, "Process", return_value=process):
            sample = diagnostics.busy_observation("Existing run process 44 is active; leave it untouched")
        self.assertEqual(44, sample["pid"])
        self.assertEqual(123.0, sample["started"])
        self.assertEqual("autocode.py", sample["script"])
        self.assertEqual("later", sample["status_option"])
        self.assertTrue(sample["sampled_after_refusal"])
        self.assertNotIn("/private/run", json.dumps(sample))

    def test_absent_or_already_gone_process_grants_no_identity(self):
        self.assertIsNone(diagnostics.busy_observation("different failure"))
        with patch.object(psutil, "Process", side_effect=psutil.NoSuchProcess(44)):
            self.assertEqual(
                {"pid": 44, "sampled_after_refusal": True, "identity": "unavailable"},
                diagnostics.busy_observation("Existing run process 44 is active; leave it untouched"),
            )
