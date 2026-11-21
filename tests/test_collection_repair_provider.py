"""The scripted repair cannot waive changed-code evidence for arbitrary input."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scenarios.harness import attack_collection_repair


class CollectionRepairProviderTests(unittest.TestCase):
    def test_unrecognized_repair_bytes_are_refused(self):
        fake = SimpleNamespace(builder_copy_source=lambda data: "unused", report_for=lambda stage, data: {})
        reasons = SimpleNamespace(GREETING_REFERENCE=("original-source", "original-test"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "greet.py").write_text("unexpected source\n")
            (root / "test_greet.py").write_text("unexpected tests\n")
            with (
                patch.dict(os.environ, {"SCENARIO_FAKE_REWORK_SOLUTION": str(root)}),
                patch.dict(sys.modules, {"fake_changed_code": reasons}),
            ):
                with self.assertRaisesRegex(ValueError, "unexpected bytes"):
                    attack_collection_repair.install(fake, {"case": "repair_after_collection"}, lambda *a, **k: None)
            self.assertEqual(("original-source", "original-test"), reasons.GREETING_REFERENCE)
