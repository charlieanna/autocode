"""Fresh metadata requires authenticated proof of its original family collection."""

import json
import select
import signal
import sys
import unittest

from tests.metadata_capture_fixture import MetadataCaptureFixture


@unittest.skipUnless(sys.platform == "linux", "Linux capture child adoption")
class MetadataRecoveryTests(MetadataCaptureFixture, unittest.TestCase):
    def test_lost_helper_blocks_fresh_public_checks_without_relaunching_backend(self):
        with self.owned_capture() as fixture:
            signal.pidfd_send_signal(fixture.handles["helper"], signal.SIGKILL)
            stdout, stderr = fixture.first.communicate(timeout=15)
            self.assertEqual(0, fixture.first.returncode, stderr)
            failed = json.loads(stdout)
            self.assertFalse(select.select([fixture.handles["root"], fixture.handles["worker"]], [], [], 0)[0])
            listed = fixture.public(["connections", "list", "--json"])
            kilo = next(item for item in listed["connections"] if item["id"] == "kilocode")
            forced = fixture.public(["connections", "check", "--provider", "kilocode", "--json"])
            self.assertEqual(
                {"statuses": ["check_failed"] * 3, "actions": [[], [], []], "backend_calls": ["auth"]},
                {
                    "statuses": [item["connection_status"] for item in (failed, kilo, forced)],
                    "actions": [item["actions"] for item in (failed, kilo, forced)],
                    "backend_calls": fixture.calls.read_text().splitlines(),
                },
                "a fresh Core must retain uncertainty and refuse a second backend launch",
            )
            self.assertIsNone(fixture.sentinel.poll(), "metadata recovery touched an unrelated process")

    def test_core_loss_collects_owned_family_and_clean_receipt_admits_a_fresh_public_check(self):
        with self.owned_capture() as fixture:
            fixture.first.kill()
            fixture.first.communicate(timeout=5)
            self.assertEqual(-signal.SIGKILL, fixture.first.returncode)
            self.assertTrue(
                select.select([fixture.handles["helper"]], [], [], 10)[0],
                "surviving helper did not finish owner-EOF cleanup within its bound",
            )
            descendants = [fixture.handles["root"], fixture.handles["worker"]]
            self.assertEqual(
                descendants,
                select.select(descendants, [], [], 0)[0],
                "helper completion must follow the old backend family's exit",
            )
            self.assertIsNone(fixture.sentinel.poll(), "owner-EOF cleanup touched an unrelated process")
            checked = fixture.public(["connections", "check", "--provider", "kilocode", "--json"])
            self.assertEqual("credentials_present", checked["connection_status"])
            self.assertEqual(["auth", "auth"], fixture.calls.read_text().splitlines())
            self.assertIsNone(fixture.sentinel.poll(), "fresh metadata touched an unrelated process")


if __name__ == "__main__":
    unittest.main()
