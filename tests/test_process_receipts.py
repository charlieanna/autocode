"""Persistence cannot suspend provider ownership or hide uncertain cleanup."""

import threading
import unittest
from unittest.mock import patch

import autocode_process as processes


class CheckpointIsolationTests(unittest.TestCase):
    def run_provider(self, *, cleanup_fails=False, checkpoint_fails=False):
        # Event barriers drive the ordering. Their timeouts only bound a broken
        # test; no sleep, elapsed-time assertion or real provider is involved.
        saving = threading.Event()
        cleaned = threading.Event()
        controller = threading.get_ident()
        worker_threads, writes, saved = [], [], []
        rows = [{"pid": 424242, "birth_identity": 1.0}, {"pid": 424243, "birth_identity": 2.0}]

        class Child:
            pid, returncode = 424242, None

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                return self.returncode

        child = Child()

        class Tree:
            def __init__(self, pid, record):
                self.known, self.record = {}, record

            def capture_root(self):
                self.known[rows[0]["pid"]] = rows[0]
                self.record(list(self.known.values()))

            def sample(self, **kwargs):
                if not saving.wait(5):
                    raise AssertionError("Controller never entered its checkpoint")
                worker_threads.append(threading.get_ident())
                self.known[rows[1]["pid"]] = rows[1]
                self.record(list(self.known.values()))
                writes.append("detached worker is running")
                child.returncode = 7
                return list(self.known.values())

            def stop(self, provider):
                worker_threads.append(threading.get_ident())
                writes.clear()
                cleaned.set()
                if cleanup_fails:
                    raise processes.ProcessError("fixture cleanup is uncertain")

        def checkpoint(receipt):
            self.assertEqual(controller, threading.get_ident())
            saved[:] = receipt
            if not saving.is_set():
                saving.set()
                self.assertTrue(cleaned.wait(5), "A blocked checkpoint prevented process cleanup")
                self.assertEqual([], writes)
                if checkpoint_fails:
                    raise OSError("fixture checkpoint failed")

        with patch.object(processes, "ProcessTree", Tree):
            result = processes.wait_for_stage(child, None, checkpoint)
        self.assertTrue(worker_threads)
        self.assertTrue(all(thread != controller for thread in worker_threads))
        self.assertEqual(rows, saved, "The final receipt must include the later detached worker")
        return result

    def test_discovery_and_cleanup_finish_while_controller_checkpoint_is_blocked(self):
        self.assertEqual((7, False), self.run_provider())

    def test_uncertain_cleanup_is_not_hidden_by_a_checkpoint_error(self):
        with self.assertRaisesRegex(processes.ProcessError, "cleanup is uncertain") as caught:
            self.run_provider(cleanup_fails=True, checkpoint_fails=True)
        self.assertEqual([424242, 424243], [row["pid"] for row in caught.exception.processes])

    def test_interrupted_thread_start_leaves_cleanup_to_the_controller(self):
        controller = threading.get_ident()
        actions, owned = [], []
        row = {"pid": 424242, "birth_identity": 1.0}

        class Child:
            pid, returncode = 424242, None

            def poll(self):
                return self.returncode

        class Tree:
            def __init__(self, pid, record):
                self.known, self.record = {pid: row}, record

            def capture_root(self):
                self.record([row])

            def sample(self, **kwargs):
                actions.append(("sample", threading.get_ident()))
                return []

            def stop(self, child):
                actions.append(("stop", threading.get_ident()))
                child.returncode = 0

        launched = []
        real_start = threading.Thread.start

        def interrupted_start(thread):
            real_start(thread)
            launched.append(thread)
            raise KeyboardInterrupt()

        try:
            with (
                patch.object(processes, "ProcessTree", Tree),
                patch.object(threading.Thread, "start", interrupted_start),
                self.assertRaises(KeyboardInterrupt),
            ):
                processes.wait_for_stage(Child(), None, lambda rows: owned.extend(rows))
        finally:
            for thread in launched:
                thread.join(5)
                self.assertFalse(thread.is_alive())
        self.assertEqual([("stop", controller)], actions)
        self.assertEqual([row], owned)


if __name__ == "__main__":
    unittest.main()
