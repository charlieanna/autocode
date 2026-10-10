import sqlite3
import unittest
from pathlib import Path

from events import consumer

SCHEMA = (Path(__file__).resolve().parent.parent / "events" / "schema.sql").read_text()


class Notifier:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send(self, event_id, event):
        if self.fail:
            raise RuntimeError("notification service down")
        self.sent.append(event_id)


def database():
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA)
    db.execute("INSERT INTO events (event_id, domain, kind) VALUES ('e1', 'example.com', 'renew')")
    db.commit()
    return db


class ConsumerTests(unittest.TestCase):
    def test_a_renew_is_charged_notified_and_marked(self):
        db, notifier = database(), Notifier()
        self.assertTrue(consumer.run_once(db, notifier))
        self.assertEqual(
            [("example.com", "e1", 1200)], db.execute("SELECT domain, event_id, amount_cents FROM charges").fetchall()
        )
        self.assertEqual(["e1"], notifier.sent)
        self.assertFalse(consumer.run_once(db, notifier))

    def test_a_failure_after_the_charge_leaves_the_event_to_be_handled_again(self):
        # This is the known problem: the retry charges a second time.
        db = database()
        with self.assertRaises(RuntimeError):
            consumer.run_once(db, Notifier(fail=True))
        consumer.run_once(db, Notifier())
        self.assertEqual(2, db.execute("SELECT count(*) FROM charges").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
