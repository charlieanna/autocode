"""The Arena attempt ledger closes every SQLite connection it opens (#952)."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_arena_store as arena_store


class Tracked(sqlite3.Connection):
    """A real connection that records whether it was closed."""

    opened: list["Tracked"] = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.was_closed = False
        Tracked.opened.append(self)

    def close(self):
        self.was_closed = True
        super().close()


class StoreConnectionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = arena_store.Store(Path(temp.name) / "arena")
        Tracked.opened = []
        connect = sqlite3.connect
        tracked = patch.object(arena_store.sqlite3, "connect", lambda database: connect(database, factory=Tracked))
        tracked.start()
        self.addCleanup(tracked.stop)

    def test_every_operation_closes_its_connection_and_keeps_its_transaction(self):
        self.store.initialize()
        self.store.insert({"id": "a1", "cohort": "c", "verdict": "RUNNING"})
        self.store.finish({"id": "a1", "cohort": "c", "verdict": "PASS"})
        with self.assertRaisesRegex(arena_store.ArenaError, "only a running attempt"):
            self.store.finish({"id": "a1", "cohort": "c", "verdict": "FAIL"})  # refused: rolled back
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.insert({"id": "a1", "cohort": "c", "verdict": "RUNNING"})  # the same id twice
        self.assertEqual(
            [
                {
                    "id": "a1",
                    "cohort": "c",
                    "verdict": "PASS",
                    "failure_attribution": {
                        "evidence_refs": [],
                        "suspected_cause": None,
                        "confirmed_diagnosis": None,
                        "regression": None,
                        "confirmed_by": None,
                    },
                }
            ],
            self.store.rows("c"),
        )
        self.assertEqual(6, len(Tracked.opened))  # one connection per operation
        self.assertEqual([], [index for index, db in enumerate(Tracked.opened) if not db.was_closed])


if __name__ == "__main__":
    unittest.main()
