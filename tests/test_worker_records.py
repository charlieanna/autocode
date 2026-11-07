"""A durable launch retry reconciles identity and never re-arms dispatch."""

import os
import tempfile
import unittest
from pathlib import Path

import autocode_worker_records as records


@unittest.skipUnless(hasattr(os, "getuid"), "durable local execution requires supported Linux user identity")
class LaunchReceipts(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "launches"
        self.wanted = records.request(
            workspace="/project",
            run_dir="/project/.autocode/runs/one",
            request_id="viewer-request-1",
            argv=["--brief", "$HOME literal 你好"],
            source={"runtime_hash": "qualified"},
            boot_id="boot-one",
        )

    def test_identical_start_replays_receipt_and_changed_content_refuses(self):
        first, created = records.prepare(self.root, self.wanted)
        self.assertTrue(created)
        repeated, created = records.prepare(self.root, self.wanted)
        self.assertFalse(created)
        self.assertEqual(first, repeated)
        changed = {**self.wanted, "argv": ["different task"]}
        with self.assertRaisesRegex(ValueError, "different content"):
            records.prepare(self.root, changed)
        self.assertEqual(first, records.read(self.root, first["id"]))

    def test_dispatch_is_sealed_once_even_if_transport_receipt_never_arrives(self):
        intent, _ = records.prepare(self.root, self.wanted)
        issued, dispatch = records.begin_dispatch(self.root, intent["id"], intent["request_hash"])
        self.assertTrue(dispatch)
        reread, created = records.prepare(self.root, self.wanted)
        self.assertFalse(created)
        self.assertEqual(issued, reread)
        unchanged, dispatch = records.begin_dispatch(self.root, intent["id"], intent["request_hash"])
        self.assertFalse(dispatch)
        self.assertEqual(issued, unchanged)

    def test_a_binding_cannot_adopt_a_second_controller_or_rearm_a_finished_service(self):
        intent, _ = records.prepare(self.root, self.wanted)
        binding = {"workspace": "/project", "run_dir": "/project/.autocode/runs/one", "controller_id": "owner-1"}
        with self.assertRaises(ValueError):
            records.bind(self.root, intent["id"], intent["request_hash"], binding)
        records.begin_dispatch(self.root, intent["id"], intent["request_hash"])
        bound = records.bind(self.root, intent["id"], intent["request_hash"], binding)
        self.assertEqual(bound, records.bind(self.root, intent["id"], intent["request_hash"], binding))
        with self.assertRaisesRegex(ValueError, "another owner"):
            records.bind(self.root, intent["id"], intent["request_hash"], {**binding, "controller_id": "owner-2"})
        _, dispatch = records.begin_dispatch(self.root, intent["id"], intent["request_hash"])
        self.assertFalse(dispatch)

    def test_a_changed_request_hash_cannot_cross_either_boundary(self):
        intent, _ = records.prepare(self.root, self.wanted)
        with self.assertRaises(ValueError):
            records.begin_dispatch(self.root, intent["id"], "different")
        with self.assertRaises(ValueError):
            records.bind(
                self.root, intent["id"], "different", {"workspace": "/project", "run_dir": "/run", "controller_id": "x"}
            )

    def test_symlink_receipts_are_not_adopted(self):
        intent, _ = records.prepare(self.root, self.wanted)
        path = self.root / (intent["id"] + ".json")
        outside = self.root.parent / "retained.json"
        path.rename(outside)
        path.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "symlink"):
            records.read(self.root, intent["id"])
