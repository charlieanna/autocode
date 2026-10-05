import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


# Every invocation imports the delivered Store in a fresh interpreter. The sink
# journal is independent of the Store; fsync precedes exit inside the callback,
# so neither callback return nor exception/finally cleanup can acknowledge it.
WORKER = r'''
import json
import os
import sys
from outbox import Store

request = json.loads(sys.argv[1])
store = Store(request["path"])

def report(**state):
    print("OUTBOX_STATE " + json.dumps(state), flush=True)

before = store.pending()
created = [store.create_order(*order) for order in request["orders"]]
report(before=before, created=created, orders=store.orders(), pending=store.pending())

def sink(event):
    assert store.orders() == {order: amount for order, amount, key in request["orders"]}
    with open(request["journal"], "a", encoding="utf-8") as accepted:
        accepted.write(json.dumps(event) + "\n")
        accepted.flush()
        os.fsync(accepted.fileno())
    if event["order_id"] == request["crash_order"]:
        os._exit(73)

if request["publish"]:
    count = store.publish(sink, limit=request["limit"])
    report(count=count, orders=store.orders(), pending=store.pending())

# Also exercise durability of returned create_order/publish calls without
# interpreter shutdown hooks, including acknowledgements of successful batches.
os._exit(0)
'''


class OutboxProcessRecovery(unittest.TestCase):
    def _worker(self, folder, orders, *, publish=False, crash_order=None, limit=100):
        request = dict(path=str(folder / "db"), journal=str(folder / "accepted.jsonl"),
                       orders=orders, publish=publish, crash_order=crash_order, limit=limit)
        # subprocess.run kills and reaps a timed-out worker; no sleep, polling,
        # inherited Store connection, or unbounded child remains on failure.
        result = subprocess.run([sys.executable, "-c", WORKER, json.dumps(request)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 73 if crash_order is not None else 0,
                         f"worker failed: {result.stdout}\n{result.stderr}")
        states = [json.loads(line.removeprefix("OUTBOX_STATE "))
                  for line in result.stdout.splitlines() if line.startswith("OUTBOX_STATE ")]
        self.assertEqual(len(states), 2 if publish and crash_order is None else 1)
        return states

    def _assert_replay(self, state, orders, pending):
        self.assertEqual(state["orders"], orders)
        self.assertEqual(state["created"], [False] * len(orders))
        self.assertEqual(state["before"], pending)
        self.assertEqual(state["pending"], pending,
                         "replaying order requests must not create or replace events")

    def test_repeated_crash_after_sink_acceptance_before_ack(self):
        requests = [(f"order-{i}", i + 1, f"key-{i}") for i in range(5)]
        orders = {order: amount for order, amount, key in requests}
        # First, middle, and last callback boundaries cover empty/nonempty
        # acknowledged prefixes and undispatched tails without timing races.
        for crash_index in (0, 2, 4):
            with self.subTest(crash_index=crash_index), tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                initial, = self._worker(folder, requests)
                self.assertEqual(initial["before"], [])
                self.assertEqual(initial["created"], [True] * len(requests))
                self.assertEqual(initial["orders"], orders)
                original = initial["pending"]
                self.assertEqual([(event["order_id"], event["amount"]) for event in original],
                                 [(order, amount) for order, amount, key in requests])
                for event in original:
                    self.assertIsInstance(event["event_id"], str)
                self.assertEqual(len({event["event_id"] for event in original}), len(requests))

                pending = original
                accepted = []
                journal = folder / "accepted.jsonl"
                for attempt in range(3):
                    opened, = self._worker(folder, requests, publish=True,
                                          crash_order=requests[crash_index][0])
                    self._assert_replay(opened, orders, pending)
                    accepted = original[:crash_index] + [original[crash_index]] * (attempt + 1)
                    self.assertEqual([json.loads(line) for line in journal.read_text().splitlines()],
                                     accepted)
                    pending = original[crash_index:]
                    reopened, = self._worker(folder, requests)
                    self._assert_replay(reopened, orders, pending)

                # Recover in bounded batches, restarting after each successful
                # publish too. Already acknowledged events must never reappear.
                while pending:
                    opened, published = self._worker(folder, requests, publish=True, limit=2)
                    self._assert_replay(opened, orders, pending)
                    self.assertEqual(published["count"], min(2, len(pending)))
                    accepted += pending[:2]
                    pending = pending[2:]
                    self.assertEqual(published["orders"], orders)
                    self.assertEqual(published["pending"], pending)

                opened, empty = self._worker(folder, requests, publish=True, limit=2)
                self._assert_replay(opened, orders, [])
                self.assertEqual(empty, dict(count=0, orders=orders, pending=[]))
                deliveries = [json.loads(line) for line in journal.read_text().splitlines()]
                self.assertEqual(deliveries, accepted)
                self.assertEqual(deliveries, original[:crash_index]
                                 + [original[crash_index]] * 3 + original[crash_index:])
                # Delivery is at least once, not exactly once. Stable identities
                # let an external idempotent sink retain one effect per event.
                self.assertEqual({event["event_id"]: event for event in deliveries},
                                 {event["event_id"]: event for event in original})
