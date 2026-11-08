import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from outbox import Store


class OutboxTests(unittest.TestCase):
    def test_creation_locks_before_the_idempotency_read_and_closes_its_connection(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "db"
            store = Store(path)
            connections, observed = [], []
            connect = sqlite3.connect

            class ObservedConnection(sqlite3.Connection):
                def execute(connection, sql, *args, **kwargs):
                    cursor = super().execute(sql, *args, **kwargs)
                    if sql.startswith("SELECT id,amount FROM orders"):
                        # A separate writer cannot interleave between the lookup
                        # and insertion. timeout=0 makes this a deterministic check.
                        contender = connect(path, timeout=0)
                        try:
                            with self.assertRaises(sqlite3.OperationalError) as refused:
                                contender.execute("BEGIN IMMEDIATE")
                            self.assertIn("locked", str(refused.exception))
                            observed.append(True)
                        finally:
                            contender.close()
                    return cursor

            def observed_connect(*args, **kwargs):
                connection = connect(*args, **kwargs, factory=ObservedConnection)
                connections.append(connection)
                return connection

            with patch("outbox.sqlite3.connect", side_effect=observed_connect):
                self.assertTrue(store.create_order("locked", 1, "request"))
            self.assertEqual([True], observed)
            self.assertTrue(connections)
            for connection in connections:
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.execute("SELECT 1")

    def test_orders_publish_and_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "db"
            store = Store(path)
            self.assertTrue(store.create_order("a", 9, "key"))
            self.assertFalse(store.create_order("a", 9, "key"))
            deliveries = []
            self.assertEqual(store.publish(deliveries.append), 1)
            self.assertEqual(deliveries[0]["order_id"], "a")
            store = Store(path)
            self.assertEqual(store.orders(), {"a": 9})
            self.assertEqual(store.pending(), [])

    def test_sink_failure_retries_same_durable_event(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "db"
            store = Store(path)
            store.create_order("pending", 20, "request")
            seen = []

            def fail(event):
                seen.append(event)
                raise RuntimeError("broker unavailable")

            with self.assertRaises(RuntimeError):
                store.publish(fail)
            store = Store(path)
            self.assertEqual(store.pending(), seen)
            self.assertEqual(store.publish(lambda event: None), 1)

    def test_inputs_and_conflicts_are_refused_without_effect(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "db")
            for amount in (0, -1, True, "9"):
                with self.assertRaises(ValueError):
                    store.create_order("bad", amount, "bad")
            self.assertTrue(store.create_order("a", 1, "key"))
            with self.assertRaises(ValueError):
                store.create_order("b", 1, "key")
            with self.assertRaises(ValueError):
                store.create_order("a", 1, "other")
            for limit in (0, -1, True):
                with self.assertRaises(ValueError):
                    store.pending(limit)
            self.assertEqual(store.orders(), {"a": 1})
            self.assertEqual([("a", 1)], [(row["order_id"], row["amount"]) for row in store.pending(1)])
