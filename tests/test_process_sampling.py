"""Completed process receipts need not incur another native probe at every tick."""

import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import autocode_process as processes


def row(pid, birth):
    return {
        "pid": pid,
        "parent": 1 if pid == 101 else 101,
        "group": 101,
        "session": 101,
        "started": str(birth),
        "birth_time": birth,
        "birth_identity": birth,
        "state": "sleeping",
    }


class CompletedProcessSamplingTests(unittest.TestCase):
    def setUp(self):
        self.root, self.finished = row(101, 1000), row(202, 2000)
        self.visible = {101: self.root}
        self.calls = []
        self.groups = {}
        self.tree = processes.ProcessTree(101, lambda rows: None)
        self.tree.known = {value["pid"]: processes.identity(value) for value in (self.root, self.finished)}
        stack = self.enterContext(ExitStack())

        def table(pids):
            self.calls.append(set(pids))
            return {pid: self.visible[pid] for pid in pids if pid in self.visible}

        def process(pid):
            value = self.visible[pid]
            result = MagicMock(pid=pid, _ident=(pid, value["birth_identity"]))
            result.create_time.return_value = value["birth_time"]
            return result

        self.table = stack.enter_context(patch.object(processes, "process_table", side_effect=table))
        stack.enter_context(patch.object(processes.psutil, "Process", side_effect=process))
        stack.enter_context(patch.object(processes.process_children, "descendants", return_value=[]))
        stack.enter_context(patch.object(self.tree, "_group_candidates", side_effect=lambda *args: dict(self.groups)))

    def test_confirmed_exit_is_probed_once_but_its_receipt_is_retained(self):
        saved = dict(self.tree.known)
        for _ in range(20):
            self.assertEqual([self.root], self.tree.sample())
        self.assertEqual(1, sum(202 in call for call in self.calls))
        self.assertEqual(20, sum(101 in call for call in self.calls))
        self.assertEqual(saved, self.tree.known)

    def test_new_owned_birth_at_the_same_pid_is_discovered_and_probed(self):
        self.tree.sample()
        replacement = row(202, 3000)
        self.visible[202] = replacement
        self.groups[202] = replacement
        self.assertIn(replacement, self.tree.sample())
        self.assertEqual(processes.identity(replacement), self.tree.known[202])
        self.calls.clear()
        self.tree.sample()
        self.assertIn(202, self.calls[0])

    def test_a_new_admission_during_a_probe_is_not_retired_as_the_old_birth(self):
        observe = self.table.side_effect
        replacement = {**row(202, 3000), "parent": 1, "group": 303, "session": 303}

        def admit_after_observation(pids):
            observed = observe(pids)
            self.tree.known[202] = processes.identity(replacement)
            self.visible[202] = replacement
            self.table.side_effect = observe
            return observed

        self.table.side_effect = admit_after_observation
        self.tree.sample()
        self.calls.clear()
        self.assertIn(replacement, self.tree.sample())
        self.assertIn(202, self.calls[0])

    def test_inspection_denial_does_not_retire_a_process(self):
        observe = self.table.side_effect
        self.table.side_effect = processes.ProcessError("inspection denied")
        with self.assertRaisesRegex(processes.ProcessError, "inspection denied"):
            self.tree.sample()
        self.table.side_effect = observe
        self.visible[202] = self.finished
        self.assertIn(self.finished, self.tree.sample())
        self.assertIn(202, self.calls[0])

    def test_live_detached_worker_keeps_its_native_probe_and_ownership(self):
        detached = {**self.finished, "parent": 1, "group": 303, "session": 303}
        self.tree.known[202] = processes.identity(detached)
        self.visible[202] = detached
        for _ in range(20):
            self.assertIn(detached, self.tree.sample())
        self.assertEqual(20, sum(202 in call for call in self.calls))

    def test_cleanup_still_rechecks_retained_identities_before_any_signal(self):
        self.tree.sample()
        self.visible[202] = row(202, 3000)
        self.calls.clear()
        with patch.object(processes.os, "kill") as kill:
            self.tree.signal([processes.identity(self.finished)], processes.signal.SIGTERM)
        self.assertEqual([{202}], self.calls)
        kill.assert_not_called()

    def test_retained_group_and_session_leaders_are_still_probed_every_time(self):
        self.tree.sample()
        leader = {**self.finished, "group": 202, "session": 202}
        self.tree.groups[202] = processes.identity(leader)
        self.tree.sessions[202] = dict(leader)
        self.calls.clear()
        with (
            patch.object(processes.os, "killpg"),
            patch.object(self.tree, "_session_candidates", return_value=({}, {202}, {})),
        ):
            self.tree.sample()
            self.tree.sample()
        self.assertEqual(2, sum(202 in call for call in self.calls))

    def test_root_identity_is_rechecked_even_after_a_missing_observation(self):
        self.visible.clear()
        self.assertEqual([], self.tree.sample())
        self.visible[101] = {**row(101, 4000), "group": 999, "session": 999}
        self.calls.clear()
        self.assertEqual([], self.tree.sample(), "A different root birth gains no ownership")
        self.assertIn(101, self.calls[0])
