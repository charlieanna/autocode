import os
import sqlite3
import tempfile
import threading
import unittest

from outbox import Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tempdir.name, "outbox.sqlite")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_ac1_create_order_commits_order_and_event(self):
        store = Store(self.path)
        self.assertTrue(store.create_order("order-1", 125, "key-1"))
        self.assertEqual(store.orders(), {"order-1": 125})
        event = store.pending()[0]
        self.assertEqual(event["order_id"], "order-1")
        self.assertEqual(event["amount"], 125)

    def test_ac2_identical_replay_does_not_add_event(self):
        store = Store(self.path)
        store.create_order("order-1", 125, "key-1")
        self.assertFalse(store.create_order("order-1", 125, "key-1"))
        self.assertEqual([event["order_id"] for event in store.pending()], ["order-1"])

    def test_ac3_rejects_nonpositive_and_bool_amounts(self):
        store = Store(self.path)
        for amount in (True, 0, -1):
            with self.assertRaises(ValueError):
                store.create_order(str(amount), amount, str(amount))
        self.assertEqual(store.orders(), {})
        self.assertEqual(store.pending(), [])

    def test_ac4_conflicts_leave_no_partial_or_reserved_state(self):
        store = Store(self.path)
        store.create_order("order-1", 125, "key-1")
        with self.assertRaises(ValueError):
            store.create_order("order-2", 125, "key-1")
        with self.assertRaises(ValueError):
            store.create_order("order-1", 125, "key-2")
        self.assertTrue(store.create_order("order-2", 125, "key-2"))
        self.assertEqual(len(store.orders()), 2)
        self.assertEqual(len(store.pending()), 2)

    def test_ac5_persists_unbounded_positive_integer_amounts(self):
        store = Store(self.path)
        amounts = [9223372036854775807, 9223372036854775808, 10**5000]
        for index, amount in enumerate(amounts):
            store.create_order(f"order-{index}", amount, f"key-{index}")
        reopened = Store(self.path)
        self.assertEqual(reopened.orders(), {f"order-{i}": amount for i, amount in enumerate(amounts)})
        self.assertEqual([event["amount"] for event in reopened.pending()], amounts)

    def test_ac6_pending_orders_and_validates_limit(self):
        store = Store(self.path)
        for number in range(3):
            store.create_order(f"order-{number}", number + 1, f"key-{number}")
        events = store.pending(2)
        self.assertEqual([event["order_id"] for event in events], ["order-0", "order-1"])
        self.assertTrue(all(set(event) == {"event_id", "order_id", "amount"} for event in events))
        for limit in (0, -1, True):
            with self.assertRaises(ValueError):
                store.pending(limit)

    def test_ac7_publish_acknowledges_successful_callbacks(self):
        store = Store(self.path)
        store.create_order("order-1", 1, "key-1")
        store.create_order("order-2", 2, "key-2")
        received = []
        self.assertEqual(store.publish(received.append), 2)
        self.assertEqual([event["order_id"] for event in received], ["order-1", "order-2"])
        self.assertEqual(store.pending(), [])

    def test_ac8_publish_failure_preserves_failed_and_later_events(self):
        store = Store(self.path)
        for number in range(3):
            store.create_order(f"order-{number}", number + 1, f"key-{number}")
        received = []

        def sink(event):
            received.append(event)
            if event["order_id"] == "order-1":
                raise RuntimeError("boom")

        with self.assertRaisesRegex(RuntimeError, "boom"):
            store.publish(sink)
        self.assertEqual([event["order_id"] for event in store.pending()], ["order-1", "order-2"])

    def test_ac9_retry_reuses_stable_event_id(self):
        store = Store(self.path)
        store.create_order("order-1", 1, "key-1")
        received = []

        def fail_after_record(event):
            received.append(event["event_id"])
            raise RuntimeError("after-record")

        with self.assertRaisesRegex(RuntimeError, "after-record"):
            store.publish(fail_after_record)
        store.publish(lambda event: received.append(event["event_id"]))
        self.assertEqual(received, [received[0], received[0]])
        self.assertEqual(store.pending(), [])

    def test_ac10_sink_can_use_public_read_api(self):
        store = Store(self.path)
        store.create_order("order-1", 1, "key-1")
        observed = []

        def sink(event):
            observed.append((store.orders(), store.pending()))

        self.assertEqual(store.publish(sink), 1)
        self.assertEqual(len(observed), 1)
        self.assertEqual(store.pending(), [])

    def test_ac11_reopen_preserves_orders_and_unpublished_events(self):
        store = Store(self.path)
        store.create_order("order-1", 125, "key-1")
        event_id = store.pending()[0]["event_id"]
        reopened = Store(self.path)
        self.assertEqual(reopened.orders(), {"order-1": 125})
        self.assertEqual(reopened.pending()[0]["event_id"], event_id)

    def test_ac12_concurrent_same_key_commits_once(self):
        barrier = threading.Barrier(2)
        results = []

        def create():
            store = Store(self.path)
            barrier.wait()
            results.append(store.create_order("order-1", 125, "key-1"))

        threads = [threading.Thread(target=create) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        reopened = Store(self.path)
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(reopened.orders(), {"order-1": 125})
        self.assertEqual(len(reopened.pending()), 1)

    def test_ac14_publish_limit_and_durable_acknowledgements(self):
        store = Store(self.path)
        for number in range(3):
            store.create_order(f"order-{number}", number + 1, f"key-{number}")
        received = []
        self.assertEqual(store.publish(received.append, limit=2), 2)
        reopened = Store(self.path)
        self.assertEqual([event["order_id"] for event in received], ["order-0", "order-1"])
        self.assertEqual([event["order_id"] for event in reopened.pending()], ["order-2"])

    def test_ac15_rejects_noninteger_amounts_without_partial_state(self):
        store = Store(self.path)
        for amount in (1.0, "1", None):
            with self.assertRaises((TypeError, ValueError)):
                store.create_order(f"order-{amount!r}", amount, f"key-{amount!r}")
        self.assertEqual(store.orders(), {})
        self.assertEqual(store.pending(), [])

    def test_ac16_allows_empty_opaque_order_id_and_key(self):
        store = Store(self.path)
        self.assertTrue(store.create_order("", 125, ""))
        self.assertEqual(store.orders(), {"": 125})
        event = store.pending()[0]
        self.assertEqual(event["order_id"], "")
        self.assertEqual(event["amount"], 125)

    def test_ac17_outbox_insert_failure_rolls_back_order_and_key(self):
        store = Store(self.path)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "CREATE TRIGGER prevent_outbox BEFORE INSERT ON outbox_events BEGIN SELECT RAISE(ABORT, 'blocked'); END"
            )
        with self.assertRaises(sqlite3.DatabaseError):
            store.create_order("order-1", 125, "key-1")
        self.assertEqual(store.orders(), {})
        self.assertEqual(store.pending(), [])
        with sqlite3.connect(self.path) as connection:
            connection.execute("DROP TRIGGER prevent_outbox")
        self.assertTrue(store.create_order("order-1", 125, "key-1"))
        self.assertEqual(len(store.pending()), 1)

    def test_ac18_concurrent_conflicting_same_key_commits_once(self):
        barrier = threading.Barrier(2)
        results = []

        def create(order_id, amount):
            store = Store(self.path)
            barrier.wait()
            try:
                results.append(store.create_order(order_id, amount, "shared-key"))
            except ValueError:
                results.append("conflict")

        threads = [
            threading.Thread(target=create, args=("order-1", 125)),
            threading.Thread(target=create, args=("order-2", 250)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        reopened = Store(self.path)
        self.assertEqual(sorted(results, key=str), [True, "conflict"])
        self.assertEqual(len(reopened.orders()), 1)
        self.assertEqual(len(reopened.pending()), 1)

    def test_ac19_event_id_is_nonempty_string(self):
        store = Store(self.path)
        store.create_order("order-1", 1, "key-1")
        event_id = store.pending()[0]["event_id"]
        self.assertIsInstance(event_id, str)
        self.assertTrue(event_id)
