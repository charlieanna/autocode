"""Joint-check evidence cannot move to another command, waiver or member set (#2144, #2145).

The real change-set store supplies locked conditional writes. Only workspace hashing and the
person's check process are mocked; no provider or supervised task is launched.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_changeset_delivery as delivery
import autocode_changeset_store as store


class ChangesetDeliveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="changeset-delivery-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.source = self.root / "api" / ".autocode" / "runs" / "one"
        patcher = mock.patch.dict(os.environ, {"AUTOCODE_HOME": str(self.root / "home")})
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(delivery, "workspace_fingerprint", return_value="unchanged-code")
        self.code = patcher.start()
        self.addCleanup(patcher.stop)
        record = store.reserve(
            source_workspace=self.root / "api",
            source_run_dir=self.source,
            target="history",
            workspace=self.root / "history",
            brief="Record the API event.",
        )
        store.bind(record["id"], "history", self.root / "history" / ".autocode" / "runs" / "two")
        summary, record = self.observe()
        delivery.set_check(summary, record, "api", ["old-interface-check"])

    def observe(self):
        record = store.find(self.source)
        source = record["source"]
        runs = [{"role": "source", "target": Path(source["workspace"]).name, **source}]
        runs += [{"role": "member", **row} for row in record["members"]]
        for row in runs:
            row["status"] = "TASK_COMPLETE"
        return {"changeset": record["id"], "record_revision": record.get("revision", 0), "runs": runs}, record

    def check(self, *, exit_code=0, during=None):
        summary, record = self.observe()

        def execute(argv, **kwargs):
            if during:
                during()
            return subprocess.CompletedProcess(argv, exit_code, "check output", "")

        with mock.patch.object(delivery.subprocess, "run", side_effect=execute):
            return delivery.verify(summary, record)

    def test_pass_names_the_actual_command_and_stales_when_code_or_member_identity_changes(self):
        self.check()
        summary, record = self.observe()
        self.assertTrue(delivery.view(summary, record)["done"])
        self.assertIn(
            "Integration check passed: `old-interface-check` in api", delivery.pr_body(summary, record, "api", [])
        )

        # Identical code in a replacement run is not the run that supplied the evidence.
        summary["runs"][1]["run_dir"] += "-replacement"
        self.assertFalse(delivery.view(summary, record)["done"])
        summary, record = self.observe()
        self.code.return_value = "changed-code"
        self.assertFalse(delivery.view(summary, record)["done"])

    def test_replacing_the_command_or_target_withdraws_the_old_waiver(self):
        for target, command in (("api", ["critical-interface-check"]), ("history", ["critical-interface-check"])):
            with self.subTest(target=target, command=command):
                summary, record = self.observe()
                delivery.waive(summary, record, "The legacy endpoint is unused.")
                summary, record = self.observe()
                self.assertTrue(delivery.view(summary, record)["done"])
                delivery.set_check(summary, record, target, command)
                summary, record = self.observe()
                current = delivery.view(summary, record)
                self.assertFalse(current["done"])
                self.assertIsNone(current["waiver"])
                self.assertIsNotNone(current["next_command"])
                self.assertNotIn("waived by the person", delivery.pr_body(summary, record, "api", []))

        # A new deliberate waiver can accept precisely the replacement check.
        delivery.waive(summary, record, "The replacement is verified separately.")
        summary, record = self.observe()
        self.assertTrue(delivery.view(summary, record)["waiver"]["current"])
        self.assertIn("The replacement is verified separately", delivery.pr_body(summary, record, "api", []))

    def test_old_unbound_records_are_visible_but_never_current(self):
        summary, record = self.observe()
        store.set_fields(
            record["id"],
            {
                "check_result": {
                    "passed": True,
                    "fingerprint": delivery.fingerprint(summary),
                    "output": "legacy output",
                },
                "waiver": {"reason": "legacy approval", "fingerprint": delivery.fingerprint(summary)},
            },
        )
        summary, record = self.observe()
        current = delivery.view(summary, record)
        self.assertFalse(current["done"])
        self.assertFalse(current["result"]["current"])
        self.assertFalse(current["waiver"]["current"])
        self.assertEqual("legacy output", current["result"]["output"])
        self.assertIsNotNone(current["next_command"])

    def test_changed_command_during_check_cannot_inherit_its_pass(self):
        def replace():
            summary, record = self.observe()
            delivery.set_check(summary, record, "history", ["critical-interface-check"])

        with self.assertRaises(store.ChangesetError):
            self.check(during=replace)
        summary, record = self.observe()
        current = delivery.view(summary, record)
        self.assertEqual(["critical-interface-check"], current["check"]["command"])
        self.assertIsNone(current["result"])
        self.assertFalse(current["done"])
        self.assertNotIn("Integration check passed", delivery.pr_body(summary, record, "api", []))

    def test_older_check_cannot_overwrite_a_competing_failed_check(self):
        with self.assertRaises(store.ChangesetError):
            self.check(during=lambda: self.check(exit_code=1))
        summary, record = self.observe()
        current = delivery.view(summary, record)
        self.assertEqual(1, current["result"]["exit_code"])
        self.assertTrue(current["result"]["current"])
        self.assertFalse(current["done"])

    def test_membership_change_during_check_refuses_the_old_result(self):
        def join():
            record = store.reserve(
                source_workspace=self.root / "api",
                source_run_dir=self.source,
                target="billing",
                workspace=self.root / "billing",
                brief="Accept the new API payload.",
            )
            store.bind(record["id"], "billing", self.root / "billing" / ".autocode" / "runs" / "three")

        with self.assertRaises(store.ChangesetError):
            self.check(during=join)
        summary, record = self.observe()
        self.assertEqual(3, len(summary["runs"]))
        self.assertFalse(delivery.view(summary, record)["done"])
        self.assertIsNone(delivery.view(summary, record)["result"])

    def test_status_and_record_from_different_revisions_cannot_dispatch_or_waive(self):
        old_summary, record = self.observe()
        delivery.set_check(old_summary, record, "history", ["critical-interface-check"])
        _, new_record = self.observe()
        with mock.patch.object(delivery.subprocess, "run") as execute:
            with self.assertRaisesRegex(delivery.DeliveryRefused, "changed since it was inspected"):
                delivery.verify(old_summary, new_record)
            execute.assert_not_called()
        with self.assertRaises(delivery.DeliveryRefused):
            delivery.waive(old_summary, new_record, "This was the earlier check.")


if __name__ == "__main__":
    unittest.main()
