"""Coordinator messages persist independently of long-running task operations."""

import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_coordination_store as store
import autocode_util as util


class MessageInboxTests(unittest.TestCase):
    def test_plan_reply_preserves_exact_snapshot_and_rejects_changed_retry(self):
        from tests.test_coordination_gate import approval_context

        snapshot = approval_context()
        first = self.inbox().submit(
            self.run, request_id="reply", text="yes coplete", plan_token="shown-token", plan_context=snapshot
        )
        self.assertEqual(snapshot, self.inbox().read(self.run)[0]["plan_context"])
        repeated = self.inbox().submit(
            self.run, request_id="reply", text="yes coplete", plan_token="shown-token", plan_context=snapshot
        )
        self.assertEqual(first["receipt"], repeated["receipt"])
        for token, context in (("other-token", snapshot), (None, None)):
            with self.assertRaises(store.CoordinationError):
                self.inbox().submit(
                    self.run, request_id="reply", text="yes coplete", plan_token=token, plan_context=context
                )

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.enterContext(patch.dict(os.environ, {"AUTOCODE_HOME": str(self.root / "catalog")}))
        self.run = self.root / "project/.autocode/runs/source"
        self.document = {
            "version": 1,
            "id": "task-identity",
            "anchor": str(self.run),
            "phase": "fixing",
            "members": [],
            "budget": {"cap_usd": 20, "spent_usd": 3},
            "approved_contracts": {"member": "approved-token"},
        }
        store.save(self.document)

    def inbox(self):
        return importlib.import_module("autocode_coordination_message_inbox")

    def submit(self, request_id="question-1", text="Could we reuse the converter?"):
        return self.inbox().submit(self.run, request_id=request_id, text=text)

    def test_accepts_during_advance_without_changing_coordination_or_task_files(self):
        record = store.root(self.run) / "coordination.json"
        before = record.read_bytes()
        with store.locked(self.run):
            result = self.submit(text="  Can XML-RPC reuse MarshalEPP?\nKeep this text.  ")
        self.assertTrue(result["accepted"])
        self.assertFalse(result["idempotent"])
        self.assertEqual("pending_only", result["consumer"])
        self.assertEqual("  Can XML-RPC reuse MarshalEPP?\nKeep this text.  ", result["receipt"]["text"])
        self.assertEqual(1, result["receipt"]["order"])
        self.assertEqual("question-1", result["receipt"]["id"])
        self.assertEqual("message", result["receipt"]["kind"])
        self.assertEqual(before, record.read_bytes())
        self.assertFalse(self.run.exists(), "The inbox must never inspect or write a task checkpoint")
        self.assertFalse((store.root(self.run) / "cancel-request.json").exists())

    def test_read_does_not_create_an_empty_inbox(self):
        before = set(store.root(self.run).iterdir())
        self.assertEqual([], self.inbox().read(self.run))
        self.assertEqual(before, set(store.root(self.run).iterdir()))

    def test_exact_retry_survives_reopen_and_preserves_order_and_receipt(self):
        first = self.submit()
        second = self.submit("question-2", "A second question")
        retry = self.submit()
        self.assertTrue(retry["idempotent"])
        self.assertEqual(first["receipt"], retry["receipt"])
        self.assertEqual([first["receipt"], second["receipt"]], self.inbox().read(self.run))
        self.assertEqual(2, second["receipt"]["order"])
        self.assertEqual("task-identity", first["receipt"]["coordination_id"])

    def test_id_cannot_be_reused_for_changed_text(self):
        first = self.submit()
        with self.assertRaisesRegex(store.CoordinationError, "different text"):
            self.submit(text="Implement a different feature")
        self.assertEqual([first["receipt"]], self.inbox().read(self.run))

    def test_receipts_are_detached_from_returned_objects(self):
        first = self.submit()
        first["receipt"]["text"] = "modified response"
        rows = self.inbox().read(self.run)
        rows[0]["text"] = "modified read"
        self.assertEqual("Could we reuse the converter?", self.submit()["receipt"]["text"])

    def test_new_messages_refuse_terminal_publication_but_exact_retries_still_reconcile(self):
        first = self.submit()
        for phase in ("publishing", "complete"):
            with self.subTest(phase=phase):
                self.document["phase"] = phase
                store.save(self.document)
                self.assertEqual(first["receipt"], self.submit()["receipt"])
                with self.assertRaisesRegex(store.CoordinationError, "publication"):
                    self.submit("new-message", "Please change the plan")
        self.assertEqual([first["receipt"]], self.inbox().read(self.run))

    def test_pause_or_cancellation_is_not_released_when_message_is_saved(self):
        cancellation = store.root(self.run) / "cancel-request.json"
        cancellation.write_text('{"request_id":"operator-stop"}')
        before = cancellation.read_bytes()
        self.submit()
        self.assertEqual(before, cancellation.read_bytes())
        self.assertTrue(store.cancelled(self.run))

    def test_uninitialized_coordination_refuses_without_creating_files(self):
        other = self.root / "other/.autocode/runs/missing"
        with self.assertRaisesRegex(store.CoordinationError, "not started"):
            self.inbox().submit(other, request_id="id", text="Hello")
        self.assertFalse(store.root(other).exists())
        self.assertEqual([], self.inbox().read(other))

    def test_text_and_request_identity_are_bounded_without_normalizing_human_text(self):
        invalid = [("", "Hello"), (None, "Hello"), ("x" * 129, "Hello"), ("x", " \n"), ("x", 1), ("x", "é" * 32769)]
        for request_id, text in invalid:
            with self.subTest(request_id=request_id, text_type=type(text).__name__):
                with self.assertRaises(store.CoordinationError):
                    self.submit(request_id, text)
        self.assertEqual([], self.inbox().read(self.run))
        accepted = self.submit("x" * 128, "é" * 32768)
        self.assertEqual("é" * 32768, accepted["receipt"]["text"])

    def test_busy_inbox_refuses_boundedly_and_does_not_lose_the_existing_message(self):
        first = self.submit()
        with (store.root(self.run) / "messages.lock").open("a+") as handle:
            util.lock_file(handle)
            try:
                with self.assertRaisesRegex(store.CoordinationError, "message inbox is busy"):
                    self.submit("second", "New question")
            finally:
                util.unlock_file(handle)
        second = self.submit("second", "New question")
        self.assertEqual([first["receipt"], second["receipt"]], self.inbox().read(self.run))

    def test_failed_write_leaves_prior_receipts_and_retry_adds_exactly_once(self):
        first = self.submit()
        with patch.object(self.inbox().util, "atomic_json", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.submit("second", "New question")
        self.assertEqual([first["receipt"]], self.inbox().read(self.run))
        second = self.submit("second", "New question")
        self.assertEqual(2, second["receipt"]["order"])

    def test_replaced_file_with_lost_response_reconciles_without_duplicate(self):
        original = self.inbox().util.atomic_json

        def lost_response(path, value):
            original(path, value)
            raise OSError("response lost")

        with patch.object(self.inbox().util, "atomic_json", side_effect=lost_response):
            with self.assertRaisesRegex(OSError, "response lost"):
                self.submit()
        self.assertTrue(self.submit()["idempotent"])
        self.assertEqual(1, len(self.inbox().read(self.run)))

    def test_rejects_symlinked_inbox_and_lock_without_writing_target(self):
        outside = self.root / "outside"
        outside.write_text("untouched")
        for name in ("messages.json", "messages.lock"):
            with self.subTest(name=name):
                path = store.root(self.run) / name
                path.symlink_to(outside)
                try:
                    with self.assertRaisesRegex(store.CoordinationError, "symlink"):
                        self.submit()
                    with self.assertRaisesRegex(store.CoordinationError, "symlink"):
                        self.inbox().read(self.run)
                finally:
                    path.unlink()
        self.assertEqual("untouched", outside.read_text())

    def test_rejects_corrupt_records_and_keeps_them_for_inspection(self):
        self.submit()
        path = store.root(self.run) / "messages.json"
        original = json.loads(path.read_text())
        variants = [
            "{",
            "[]",
            json.dumps({**original, "version": 9}),
            json.dumps({**original, "anchor": "/another/run"}),
            json.dumps({**original, "coordination_id": "another-task"}),
            json.dumps({**original, "requests": original["requests"] * 2}),
        ]
        for mutation in (
            {"order": True},
            {"order": 2},
            {"text": ""},
            {"kind": "approval"},
            {"coordination_id": "another-task"},
            {"submitted_at": None},
        ):
            variants.append(json.dumps({**original, "requests": [{**original["requests"][0], **mutation}]}))
        for raw in variants:
            with self.subTest(raw=raw):
                path.write_text(raw)
                with self.assertRaises(store.CoordinationError):
                    self.inbox().read(self.run)
                with self.assertRaises(store.CoordinationError):
                    self.submit("second", "Hello")
                self.assertEqual(raw, path.read_text())

    def test_existing_receipt_cannot_be_moved_to_another_coordination_identity(self):
        self.submit()
        self.document["id"] = "replacement-task"
        store.save(self.document)
        with self.assertRaisesRegex(store.CoordinationError, "identity"):
            self.submit()


if __name__ == "__main__":
    unittest.main()
