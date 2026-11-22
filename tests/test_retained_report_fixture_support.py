"""The editor fixture refuses an unpinned fake before starting any CLI child."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from . import retained_report_fixture_support as support

REPO = Path(__file__).resolve().parents[1]


class RetainedReportFixtureSupportTests(unittest.TestCase):
    def test_exact_provider_and_config_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "bin").mkdir()
            provider = root / "bin/codex"
            provider.write_text(support.scripted_provider((REPO / "scenarios/harness/fake_codex.py").read_text()))
            provider.chmod(0o755)
            (root / "bin/fake_changed_code.py").write_bytes(
                (REPO / "scenarios/harness/fake_changed_code.py").read_bytes()
            )
            reference = root / "reference"
            reference.mkdir()
            items = [f"item-{index:02}" for index in range(25)]
            source = (
                "def labels():\n    return [\n"
                + "".join(f"        {value!r},\n" for value in [*items, "added"])
                + "    ]\n"
            )
            existing = (
                "import unittest\nfrom labels import labels\n\nclass Existing(unittest.TestCase):\n"
                f"    def test_original_prefix(self):\n        self.assertEqual(labels()[:25], {items!r})\n"
            )
            added = (
                "import unittest\nfrom labels import labels\n\nclass Added(unittest.TestCase):\n"
                f"    def test_c1_added_label(self):\n        self.assertEqual(labels(), {[*items, 'added']!r})\n"
            )
            for name, text in (("labels.py", source), ("test_existing.py", existing), ("test_added.py", added)):
                (reference / name).write_text(text)
            config = root / "fake-config.json"
            config_text = json.dumps(
                {
                    "reference": str(reference),
                    "paths": sorted(support.REFERENCE),
                    "check": "python3 -m unittest -v test_added test_existing",
                    "fault": "",
                }
            )
            config.write_text(config_text)
            (root / "provider-stages.jsonl").write_text("")
            (root / "provider-manifest.json").write_text(
                json.dumps({"config_sha256": hashlib.sha256(config.read_bytes()).hexdigest()})
            )
            env = {
                "SCENARIO_FAKE_CONFIG": str(config),
                "AUTOCODE_REPORT_FAKE_TRACE": str(root / "provider-stages.jsonl"),
                "AUTOCODE_HOME": str(root / "registry"),
                "AUTOCODE_SOURCE_HOST": "0",
            }
            support.verify(root, REPO, env)
            config.write_text('{"changed": true}')
            with self.assertRaises(ValueError):
                support.verify(root, REPO, env)
            config.write_text(config_text)
            (reference / "labels.py").write_text("different source")
            with self.assertRaises(ValueError):
                support.verify(root, REPO, env)
            (reference / "labels.py").write_text(source)
            provider.write_text("not the scripted provider")
            with self.assertRaises(ValueError):
                support.verify(root, REPO, env)


if __name__ == "__main__":
    unittest.main()
