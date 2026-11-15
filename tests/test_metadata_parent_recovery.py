"""Ancestor collection releases custody without granting a metadata command result."""

import json
import select
import signal
import sys
import unittest

from tests.metadata_capture_fixture import MetadataCaptureFixture


@unittest.skipUnless(sys.platform == "linux", "Linux capture child adoption")
class MetadataParentRecoveryTests(MetadataCaptureFixture, unittest.TestCase):
    def test_ancestor_collection_releases_custody_without_turning_a_failed_check_into_success(self):
        for depth in (1, 2):
            with (
                self.subTest(capture_depth=depth),
                self.owned_capture(wrapper_depth=depth, ignore_term=True) as fixture,
            ):
                signal.pidfd_send_signal(fixture.handles["helper"], signal.SIGKILL)
                _stdout, stderr = fixture.first.communicate(timeout=15)
                self.assertEqual(0, fixture.first.returncode, stderr)
                self.assertEqual(
                    {"kind": "result", "connection_status": "check_failed"},
                    json.loads(fixture.outcome.read_text()),
                    "the ancestor's family proof grants no result authority to the failed metadata check",
                )
                self.assertEqual(
                    list(fixture.handles.values()),
                    select.select(list(fixture.handles.values()), [], [], 0)[0],
                    "every nested native process must end before the fresh public check",
                )
                self.assertIsNone(fixture.sentinel.poll())
                checked = fixture.public(["connections", "check", "--provider", "kilocode", "--json"])
                self.assertEqual("credentials_present", checked["connection_status"])
                self.assertEqual(["auth", "auth"], fixture.calls.read_text().splitlines())
                self.assertIsNone(fixture.sentinel.poll())

    def test_terminal_cancellation_preserves_interrupt_and_releases_collected_nested_metadata(self):
        for depth in (1, 2):
            with (
                self.subTest(capture_depth=depth),
                self.owned_capture(wrapper_depth=depth, ignore_term=True) as fixture,
            ):
                fixture.first.send_signal(signal.SIGTERM)
                _stdout, stderr = fixture.first.communicate(timeout=15)
                self.assertEqual(0, fixture.first.returncode, stderr)
                self.assertEqual({"kind": "interrupted"}, json.loads(fixture.outcome.read_text()))
                self.assertEqual(
                    list(fixture.handles.values()),
                    select.select(list(fixture.handles.values()), [], [], 0)[0],
                    "cancellation must settle the whole nested family",
                )
                checked = fixture.public(["connections", "check", "--provider", "kilocode", "--json"])
                self.assertEqual("credentials_present", checked["connection_status"])
                self.assertEqual(["auth", "auth"], fixture.calls.read_text().splitlines())
                self.assertIsNone(fixture.sentinel.poll())


if __name__ == "__main__":
    unittest.main()
