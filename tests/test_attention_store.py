"""Real private acknowledgement storage, corruption, duplicate and scope faults."""

import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import autocode_attention_store as store

ITEM = "a" * 64


@unittest.skipUnless(hasattr(os, "getuid"), "requires local POSIX ownership")
class LocalAcknowledgement(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.workspace = self.base / "project"
        self.workspace.mkdir()
        self.run = self.workspace / ".autocode" / "runs" / "saved"
        self.root = self.base / "attention"
        self.environment = patch.dict(os.environ, {"AUTOCODE_ATTENTION_HOME": str(self.root)})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def inspect(self, item=ITEM, *, run=None):
        return store.inspect(item, self.workspace, run or self.run)

    def test_reading_an_unread_alert_creates_no_files(self):
        self.assertEqual("unread", self.inspect()["state"])
        self.assertFalse(self.root.exists())

    def test_explicit_private_storage_needs_no_account_home(self):
        with patch.object(Path, "home", side_effect=RuntimeError("No account home")):
            self.assertEqual(self.root, store.storage())
            self.assertEqual("unread", self.inspect()["state"])
        self.assertFalse(self.root.exists())

    def test_two_viewers_share_one_idempotent_receipt_and_no_task_state_is_created(self):
        with ThreadPoolExecutor(2) as workers:
            receipts = list(workers.map(lambda _: store.acknowledge(ITEM, self.workspace, self.run), range(2)))
        self.assertEqual(receipts[0], receipts[1])
        self.assertEqual(receipts[0], self.inspect()["receipt"])
        self.assertEqual("acknowledged", self.inspect()["state"])
        self.assertEqual(0o600, (self.root / (ITEM + ".json")).stat().st_mode & 0o777)
        self.assertFalse((self.workspace / ".autocode").exists())

    def test_another_run_cannot_reconcile_a_receipt_from_this_run(self):
        store.acknowledge(ITEM, self.workspace, self.run)
        self.assertEqual("unknown", self.inspect(run=self.run.with_name("other"))["state"])
        self.assertEqual("unread", self.inspect("b" * 64)["state"])

    def test_corrupt_oversized_wrong_actor_and_symlink_receipts_remain_unknown(self):
        receipt = store.acknowledge(ITEM, self.workspace, self.run)
        path = self.root / (ITEM + ".json")
        for bad in (
            "invalid json",
            "x" * (store.MAX_BYTES + 1),
            json.dumps({**receipt, "actor": os.getuid() + 1}),
            json.dumps({**receipt, "version": True}),
        ):
            with self.subTest(contents=bad[:30]):
                path.write_text(bad)
                self.assertEqual("unknown", self.inspect()["state"])
                with self.assertRaises((OSError, ValueError)):
                    store.acknowledge(ITEM, self.workspace, self.run)
        path.unlink()
        target = self.base / "other.json"
        target.write_text(json.dumps(receipt))
        target.chmod(0o600)
        path.symlink_to(target)
        self.assertEqual("unknown", self.inspect()["state"])
        self.assertEqual(json.dumps(receipt), target.read_text())

    def test_nonprivate_storage_and_task_owned_storage_are_refused(self):
        self.root.mkdir(mode=0o755)
        self.root.chmod(0o755)
        self.assertEqual("unknown", self.inspect()["state"])
        with self.assertRaises(ValueError):
            store.acknowledge(ITEM, self.workspace, self.run)
        inside = self.workspace / "untrusted"
        with patch.dict(os.environ, {"AUTOCODE_ATTENTION_HOME": str(inside)}):
            with self.assertRaises(ValueError):
                store.acknowledge(ITEM, self.workspace, self.run)
            self.assertFalse(inside.exists())

    def test_nonregular_receipt_is_unknown_without_waiting_for_a_fifo_writer(self):
        self.root.mkdir(mode=0o700)
        os.mkfifo(self.root / (ITEM + ".json"), 0o600)
        self.assertEqual("unknown", self.inspect()["state"])

    def test_bad_ids_never_create_storage_or_escape_its_namespace(self):
        for bad in ("../escape", "a" * 63, "x" * 64, True, None):
            with self.subTest(identity=bad), self.assertRaises(ValueError):
                store.acknowledge(bad, self.workspace, self.run)
        self.assertFalse(self.root.exists())
