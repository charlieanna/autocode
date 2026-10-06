import unittest

from events.billing import Billing
from events.notifications import Notifier
from events.processor import Processor, RegistryEvent


def event(event_id, kind, domain="example.de", registry="denic", seq=1):
    return RegistryEvent(event_id=event_id, registry=registry, domain=domain, kind=kind, seq=seq)


class ProcessorTests(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.billing = Billing()
        self.processor = Processor(self.billing, Notifier(self.sent.append))

    def test_a_renew_is_charged_and_notified(self):
        self.processor.process(event("e1", "renew"))
        self.assertEqual({"e1": ("example.de", 1200)}, self.billing.charges)
        self.assertEqual(["e1"], [sent.event_id for sent in self.sent])

    def test_a_redelivered_renew_is_charged_and_notified_once(self):
        renew = event("e1", "renew")
        self.processor.process(renew)
        self.processor.process(renew)
        self.assertEqual(1, len(self.billing.charges))
        self.assertEqual(1, len(self.sent))

    def test_only_a_renew_is_charged(self):
        for number, kind in enumerate(("create", "transfer", "delete"), start=1):
            self.processor.process(event(f"e{number}", kind))
        self.assertEqual({}, self.billing.charges)
        self.assertEqual(3, len(self.sent))


if __name__ == "__main__":
    unittest.main()
