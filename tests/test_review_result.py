"""Review report inspection preserves the verdict while exposing changed evidence."""

import hashlib
import tempfile
import unittest
from pathlib import Path

import autocode_review_result as review_result


class ReviewResultTests(unittest.TestCase):
    def test_current_missing_modified_unbound_and_unsafe_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "review/findings.json"
            report.parent.mkdir()
            report.write_bytes(b"recorded review")
            result = {
                "outcome": "approve",
                "artifact": "review/findings.json",
                "artifact_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
                "source_revision": "source-a",
            }

            def inspect(**changes):
                return review_result.inspect({**result, **changes}, root, "source-a")

            self.assertEqual("current", inspect()["freshness"])
            self.assertEqual("stale_or_unverified", inspect(source_revision="source-b")["freshness"])
            self.assertEqual("unknown", inspect(source_revision=None)["freshness"])
            self.assertEqual("stale_or_unverified", inspect(artifact="../secret")["freshness"])
            report.write_bytes(b"changed review")
            self.assertEqual("stale_or_unverified", inspect()["freshness"])
            report.unlink()
            self.assertEqual("approve", inspect()["outcome"])
            self.assertEqual("stale_or_unverified", inspect()["freshness"])
