import multiprocessing
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from leasequeue import LeaseQueue


def _serve(path, connection):
    try:
        queue = LeaseQueue(path)
        while True:
            request = connection.recv()
            if request is None:
                return
            method, args = request
            try:
                result = getattr(queue, method)(*args)
            except ValueError as error:
                connection.send(("ValueError", str(error)))
            else:
                connection.send(("ok", result))
    finally:
        connection.close()


class ProcessRecoveryContract(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "queue.db"

    @contextmanager
    def worker(self, *, crash=False):
        # Spawn starts a fresh interpreter rather than inheriting token state.
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(target=_serve, args=(self.path, child))
        process.start()
        child.close()

        def call(method, *args):
            parent.send((method, args))
            self.assertTrue(parent.poll(15), f"worker timed out on {method}")
            status, result = parent.recv()
            if status == "ValueError":
                raise ValueError(result)
            self.assertEqual(status, "ok")
            return result

        try:
            yield call
            if crash:
                # The claim has returned; kill while waiting for another request,
                # without acknowledging or running any worker shutdown hooks.
                self.assertTrue(process.is_alive())
                process.kill()
            else:
                parent.send(None)
            process.join(5)
            self.assertFalse(process.is_alive(), "worker did not exit")
            if crash:
                self.assertNotEqual(process.exitcode, 0)
            else:
                self.assertEqual(process.exitcode, 0)
        finally:
            if process.is_alive():
                process.kill()
                process.join(5)
            parent.close()
            self.assertFalse(process.is_alive(), "worker could not be killed")
            process.close()

    def test_repeated_worker_death_deadline_recovery_and_fencing(self):
        jobs = {"completed": "C", "retry": "R", "held": "H", "tail": "T"}
        with self.worker() as call:
            for job, payload in jobs.items():
                self.assertTrue(call("enqueue", job, payload))
            completed = call("claim", 0, 1)
            self.assertEqual(completed["id"], "completed")
            self.assertTrue(call("ack", "completed", completed["token"], 0))
            self.assertEqual(call("pending"), 3)

        tokens = []
        for generation in range(6):
            now = 10 + generation * 10
            with self.worker(crash=True) as call:
                if tokens:
                    # Exactly at expiry, neither operation may revive the
                    # dead worker's lease or remove its still-unfinished job.
                    self.assertFalse(call("ack", "retry", tokens[-1], now))
                    self.assertFalse(call("nack", "retry", tokens[-1], now))
                current = call("claim", now, 10)
                self.assertIsNotNone(current)
                self.assertEqual(
                    (current["id"], current["payload"], current["deadline"]),
                    ("retry", "R", now + 10),
                )
                if not tokens:
                    held = call("claim", now, 1000)
                    self.assertEqual((held["id"], held["payload"]), ("held", "H"))
                self.assertEqual(call("pending"), 3)

            with self.worker() as call:
                self.assertEqual(call("pending"), 3)
                if not tokens:
                    # Before expiry, skip both dead worker leases, but keep
                    # the unrelated queued job available in FIFO order.
                    tail = call("claim", now + 9, 1000)
                    self.assertEqual((tail["id"], tail["payload"]), ("tail", "T"))
                self.assertIsNone(call("claim", now + 9, 10))
                for stale in tokens:
                    self.assertFalse(
                        call("ack", "retry", stale, now + 9),
                        f"generation {generation}: stale token completed a reassigned job",
                    )
                    self.assertFalse(call("nack", "retry", stale, now + 9))
                    self.assertEqual(call("pending"), 3)
                    self.assertIsNone(call("claim", now + 9, 10))
                    self.assertNotEqual(current["token"], stale)
                for job, payload in jobs.items():
                    self.assertFalse(call("enqueue", job, payload))
                    with self.assertRaises(ValueError):
                        call("enqueue", job, payload + " changed")
                self.assertEqual(call("pending"), 3)
            tokens.append(current["token"])

        # The latest token must work in another process, not just reject stale
        # tokens. Nack/reclaim also rotates it without waiting for a deadline.
        with self.worker() as call:
            self.assertTrue(call("nack", "retry", tokens[-1], now + 9))
            latest = call("claim", now + 9, 10)
            self.assertEqual(
                (latest["id"], latest["payload"], latest["deadline"]),
                ("retry", "R", now + 19),
            )

        with self.worker() as call:
            for stale in tokens:
                self.assertFalse(call("ack", "retry", stale, now + 9))
                self.assertFalse(call("nack", "retry", stale, now + 9))
                self.assertNotEqual(latest["token"], stale)
            self.assertEqual(call("pending"), 3)
            self.assertIsNone(call("claim", now + 9, 10))
            self.assertTrue(call("ack", "retry", latest["token"], now + 9))
            self.assertEqual(call("pending"), 2)
            # Leases for the other jobs survived every crash and reopen too.
            self.assertTrue(call("ack", "held", held["token"], now + 9))
            self.assertEqual(call("pending"), 1)
            self.assertTrue(call("ack", "tail", tail["token"], now + 9))
            self.assertEqual(call("pending"), 0)

        with self.worker() as call:
            self.assertEqual(call("pending"), 0)
            for job, payload in jobs.items():
                self.assertFalse(call("enqueue", job, payload))
                with self.assertRaises(ValueError):
                    call("enqueue", job, payload + " changed")
            self.assertIsNone(call("claim", 2000, 10))
            self.assertEqual(call("pending"), 0)
