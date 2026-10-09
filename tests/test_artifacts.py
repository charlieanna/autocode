import tempfile
import unittest
from pathlib import Path

import autocode_artifacts as artifacts
import autocode_support as support


class ArtifactNameTests(unittest.TestCase):
    def test_slug_maps_stage_codes_to_unique_readable_stems(self):
        self.assertEqual("builder", artifacts.slug("terra"))
        self.assertEqual("validator", artifacts.slug("sol"))
        self.assertEqual("builder-report-repair", artifacts.slug("terra_report_repair"))
        self.assertEqual("completion-review", artifacts.slug("astra_review"))
        self.assertEqual("completion-checkpoint", artifacts.slug("astra_checkpoint"))
        self.assertEqual("unknown-stage", artifacts.slug("unknown_stage"))
        slugs = [artifacts.slug(stage) for stage in artifacts.FILE_SLUGS]
        self.assertEqual(len(slugs), len(set(slugs)), "file stems must stay unique per stage")

    def test_reserve_refuses_new_and_legacy_stems(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            base = artifacts.reserve(run, 5, "terra", 1)
            self.assertEqual(run / "iterations/005/builder-01", base)
            self.assertEqual(run / "iterations/005/builder-01", artifacts.stage_base(run, 5, "terra", 1))
            # A legacy code-name artifact still blocks the same attempt number.
            legacy = run / "iterations/005/terra-01.jsonl"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("{}")
            with self.assertRaises(support.Paused) as caught:
                artifacts.reserve(run, 5, "terra", 1)
            self.assertEqual("PAUSED_UNCERTAIN_STAGE", caught.exception.status)
            # The reserved new stem blocks too.
            legacy.unlink()
            (run / "iterations/005/builder-01.json").write_text("{}")
            with self.assertRaises(support.Paused) as caught:
                artifacts.reserve(run, 5, "terra", 1)
            self.assertEqual("PAUSED_UNCERTAIN_STAGE", caught.exception.status)
            # A different attempt number is free.
            self.assertEqual(run / "iterations/005/builder-02", artifacts.reserve(run, 5, "terra", 2))
