import sqlite3
import unittest

from events.billing import Billing
from events.notifications import Notifier
from events.processor import Processor, RegistryEvent


def event(event_id, kind, domain="example.de", registry="denic", seq=1):
    return RegistryEvent(event_id=event_id, registry=registry, domain=domain, kind=kind, seq=seq)


class ProcessorTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.sent = []
        self.processor = self.start()

    def start(self):
        """A consumer process: its state is only what the database holds."""
        return Processor(Billing(self.db), Notifier(self.db, self.sent.append))

    def charges(self):
        return self.db.execute("SELECT event_id, domain, amount_cents FROM charges").fetchall()

    def test_a_renew_is_charged_and_notified(self):
        self.processor.process(event("e1", "renew"))
        self.assertEqual([("e1", "example.de", 1200)], self.charges())
        self.assertEqual(["e1"], [sent.event_id for sent in self.sent])

    def test_a_redelivered_renew_is_charged_and_notified_once(self):
        renew = event("e1", "renew")
        self.processor.process(renew)
        self.processor.process(renew)
        self.assertEqual(1, len(self.charges()))
        self.assertEqual(1, len(self.sent))

    def test_a_renew_redelivered_after_a_restart_is_not_charged_again(self):
        self.processor.process(event("e1", "renew"))
        self.start().process(event("e1", "renew"))
        self.assertEqual(1, len(self.charges()))
        self.assertEqual(1, len(self.sent))

    def test_only_a_renew_is_charged(self):
        for number, kind in enumerate(("create", "transfer", "delete"), start=1):
            self.processor.process(event(f"e{number}", kind))
        self.assertEqual([], self.charges())
        self.assertEqual(3, len(self.sent))


if __name__ == "__main__":
    unittest.main()
