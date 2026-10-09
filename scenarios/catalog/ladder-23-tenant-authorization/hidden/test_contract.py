import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tenants import Service


class TenantContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "db"
        self.s = Service(self.path)
        self.s.create_tenant("a", "alice")
        self.s.create_tenant("b", "bruno")
        self.s.put("alice", "a", "same", "A")
        self.s.put("bruno", "b", "same", "B")

    def test_tenant_isolation_on_every_document_operation(self):
        with self.assertRaises(PermissionError):
            self.s.get("alice", "b", "same")
        with self.assertRaises(PermissionError):
            self.s.documents("alice", "b")
        with self.assertRaises(PermissionError):
            self.s.put("alice", "b", "same", "evil")
        with self.assertRaises(PermissionError):
            self.s.delete("alice", "b", "same")
        with self.assertRaises(PermissionError):
            self.s.grant("alice", "b", "alice", "admin")
        self.s.delete("alice", "a", "same")
        self.assertEqual(self.s.get("bruno", "b", "same"), "B")
        self.assertEqual(self.s.documents("alice", "a"), {})

    def test_viewer_revocation_and_existence_hiding(self):
        self.s.grant("alice", "a", "reader", "viewer")
        self.assertEqual(self.s.get("reader", "a", "same"), "A")
        for key in ("same", "missing"):
            with self.assertRaises(PermissionError):
                self.s.delete("reader", "a", key)
            with self.assertRaises(PermissionError):
                self.s.get("outsider", "a", key)
        with self.assertRaises(KeyError):
            self.s.get("reader", "a", "missing")
        with self.assertRaises(PermissionError):
            self.s.grant("reader", "a", "reader", "admin")
        self.s.revoke("alice", "a", "reader")
        with self.assertRaises(PermissionError):
            Service(self.path).documents("reader", "a")

    def test_last_admin_cannot_be_removed_or_demoted(self):
        with self.assertRaises(ValueError):
            self.s.grant("alice", "a", "alice", "viewer")
        with self.assertRaises(ValueError):
            self.s.revoke("alice", "a", "alice")
        self.s.grant("alice", "a", "second", "admin")
        barrier = threading.Barrier(2)

        def revoke(user):
            service = Service(self.path)
            barrier.wait(timeout=5)
            try:
                service.revoke(user, "a", user)
                return True
            except ValueError:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(revoke, ("alice", "second")))
        self.assertEqual(sum(results), 1)
        remaining = []
        for user in ("alice", "second"):
            try:
                self.s.documents(user, "a")
                remaining.append(user)
            except PermissionError:
                pass
        self.assertEqual(len(remaining), 1)
        self.s.grant(remaining[0], "a", "new", "editor")
