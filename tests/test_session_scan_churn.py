"""Empty SID retirement ignores only birth-checked foreign PID churn."""

import contextlib
import unittest
from unittest.mock import Mock, patch

import autocode_process as processes


class SessionScanChurnTests(unittest.TestCase):
    def scan(self, snapshots, rows, *, vanished=(), unavailable=()):
        leader = {"pid": 4242, "session": 4242, "group": 4242, "birth_identity": 1.0, "started": "owner"}
        tree = processes.ProcessTree(4242, lambda rows: None, sessions=(leader,))
        tree.known = {4242: leader}
        current = {row["pid"]: row for row in rows}
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(processes, "process_ids", side_effect=snapshots))
        stack.enter_context(
            patch.object(
                processes,
                "process_table",
                side_effect=lambda pids: {pid: current[pid] for pid in pids if pid in current and pid not in vanished},
            )
        )

        def native_session(pid):
            if pid in unavailable:
                raise ProcessLookupError()
            return current[pid]["session"]

        stack.enter_context(patch.object(processes.os, "getsid", side_effect=native_session))
        stack.enter_context(patch.object(processes.os, "getpgid", side_effect=lambda pid: current[pid]["group"]))
        return tree

    @staticmethod
    def row(pid, session=10, birth=2.0):
        return {
            "pid": pid,
            "parent": 1,
            "session": session,
            "group": pid,
            "birth_identity": birth,
            "started": str(birth),
            "state": "sleeping",
        }

    def test_classified_foreign_additions_and_removals_do_not_block_empty_sid_retirement(self):
        tree = self.scan([[10], [11]], [self.row(10), self.row(11)])
        self.assertEqual([], tree.sample(notify=False))
        self.assertEqual({}, tree.sessions)
        self.assertNotIn(11, tree.known, "Foreign churn gained process ownership")

    def test_unclassified_disappearance_cannot_retire_the_sid(self):
        tree = self.scan([[10], [11]] * 3, [self.row(10), self.row(11)], unavailable=(10,))
        with self.assertRaisesRegex(processes.ProcessError, "Process set changed"):
            tree.sample(notify=False)
        self.assertEqual({4242}, set(tree.sessions))

    def test_vanished_foreign_addition_remains_unknown(self):
        tree = self.scan([[10], [11]] * 3, [self.row(10), self.row(11)], vanished=(11,))
        with self.assertRaisesRegex(processes.ProcessError, "Process set changed"):
            tree.sample(notify=False)
        self.assertEqual({4242}, set(tree.sessions))

    def test_owned_candidate_vanishing_before_birth_read_is_not_foreign_churn(self):
        tree = self.scan([[10], [11]] * 3, [self.row(10, 4242), self.row(11)], vanished=(10,))
        with self.assertRaisesRegex(processes.ProcessError, "Process set changed"):
            tree.sample(notify=False)
        self.assertEqual({4242}, set(tree.sessions))

    def test_new_reparented_owned_member_is_discovered_by_the_next_scan(self):
        member = self.row(11, 4242)
        tree = self.scan([[10], [11], [11]], [self.row(10), member])
        self.assertEqual([11], [row["pid"] for row in tree.sample(notify=False)])
        self.assertEqual({4242}, set(tree.sessions))

    def test_changed_native_session_cannot_certify_foreign_additions(self):
        tree = self.scan([[10], [11]] * 3, [self.row(10), self.row(11)])
        with patch.object(processes.os, "getsid", side_effect=lambda pid: 4242 if pid == 11 else 10):
            with self.assertRaisesRegex(processes.ProcessError, "Process set changed"):
                tree.sample(notify=False)
        self.assertEqual({4242}, set(tree.sessions))

    def test_retry_revalidates_the_session_leaders_birth_before_discovery(self):
        replacement = self.row(4242, 4242, birth=9.0)
        tree = self.scan([[10], [4242], [4242]], [self.row(10), replacement])
        leader_reads = 0

        def native_rows(pids):
            nonlocal leader_reads
            if 4242 in pids:
                leader_reads += 1
                return {4242: replacement} if leader_reads >= 3 else {}
            return {}

        with patch.object(processes, "process_table", side_effect=native_rows):
            self.assertEqual([], tree.sample(notify=False))
        self.assertEqual({}, tree.sessions)
        self.assertNotEqual(replacement, tree.known.get(4242))

    def test_retry_revalidates_retained_groups_against_the_same_new_leader_birth(self):
        replacement = self.row(4242, 4242, birth=9.0)
        tree = self.scan([[10], [4242], [4242]], [self.row(10), replacement])
        tree.groups = {4242: dict(tree.known[4242])}
        leader_reads = 0

        def native_rows(pids):
            nonlocal leader_reads
            if 4242 in pids:
                leader_reads += 1
                return {4242: replacement} if leader_reads >= 3 else {}
            return {}

        with (
            patch.object(processes, "process_table", side_effect=native_rows),
            patch.object(processes.os, "killpg"),
        ):
            self.assertEqual([], tree.sample(notify=False))
        self.assertEqual({}, tree.groups)
        self.assertEqual({}, tree.sessions)
        self.assertNotEqual(replacement, tree.known.get(4242))

    def test_fresh_leader_retirement_cannot_readmit_a_reincarnated_foreign_child(self):
        old_root = self.row(4242, 4242, birth=1.0)
        old_child = self.row(4243, 4242, birth=2.0)
        foreign_root = self.row(4242, 4242, birth=9.0)
        foreign_child = {**self.row(4243, 4242, birth=10.0), "group": 4242}
        tree = processes.ProcessTree(4242, lambda rows: None, groups=(old_root,), sessions=(old_root,))
        tree.known = {4242: old_root, 4243: old_child}
        parent = Mock()
        parent._ident = (4242, 1.0)
        with (
            patch.object(
                processes,
                "process_table",
                side_effect=[{4242: old_root, 4243: foreign_child}, {}, {4242: foreign_root}, {}],
            ),
            patch.object(processes, "process_ids", return_value=[4242, 4243]),
            patch.object(processes.psutil, "Process", return_value=parent),
            patch.object(processes.process_children, "descendants", return_value=[]),
            patch.object(processes.os, "getsid", return_value=4242),
            patch.object(processes.os, "getpgid", return_value=4242),
        ):
            self.assertEqual([], tree.sample(notify=False))
        self.assertEqual(old_child, tree.known[4243], "Retired PGID captured a foreign child birth")
        self.assertEqual({}, tree.groups)
        self.assertEqual({}, tree.sessions)

    def test_settlement_retries_only_until_a_fresh_exact_scan_succeeds(self):
        tree = Mock()
        tree.sample.side_effect = [processes.SessionScanIncomplete("changing"), []]
        with (
            patch.object(processes.util, "monotonic", side_effect=[0, 0.1]),
            patch.object(processes.util, "sleep") as sleep,
        ):
            self.assertEqual([], processes.settled_sample(tree, notify=False))
        self.assertEqual(2, tree.sample.call_count)
        sleep.assert_called_once_with(0.01)

    def test_persistently_unknown_settlement_remains_an_error_at_its_bound(self):
        tree = Mock()
        tree.sample.side_effect = processes.SessionScanIncomplete("changing")
        with (
            patch.object(processes.util, "monotonic", side_effect=[0, 0.1, 0.5]),
            patch.object(processes.util, "sleep") as sleep,
        ):
            with self.assertRaises(processes.SessionScanIncomplete):
                processes.settled_sample(tree, notify=False)
        self.assertEqual(2, tree.sample.call_count)
        sleep.assert_called_once_with(0.01)

    def test_denied_inspection_is_never_a_transient_settlement_retry(self):
        tree = Mock()
        tree.sample.side_effect = processes.ProcessError("access denied")
        with patch.object(processes.util, "sleep") as sleep:
            with self.assertRaisesRegex(processes.ProcessError, "access denied"):
                processes.settled_sample(tree, notify=False)
        tree.sample.assert_called_once()
        sleep.assert_not_called()

    def test_settlement_retries_at_least_once_when_first_scan_exceeds_window(self):
        tree = Mock()
        tree.sample.side_effect = [processes.SessionScanIncomplete("stalled"), []]
        with (
            patch.object(processes.util, "monotonic", side_effect=[0, 0.85]),
            patch.object(processes.util, "sleep") as sleep,
        ):
            self.assertEqual([], processes.settled_sample(tree, notify=False))
        self.assertEqual(2, tree.sample.call_count)
        sleep.assert_called_once_with(0.01)

        persistent = Mock()
        persistent.sample.side_effect = processes.SessionScanIncomplete("stalled")
        with (
            patch.object(processes.util, "monotonic", side_effect=[0, 0.85, 1.70]),
            patch.object(processes.util, "sleep") as sleep,
        ):
            with self.assertRaises(processes.SessionScanIncomplete):
                processes.settled_sample(persistent, notify=False)
        self.assertEqual(2, persistent.sample.call_count)
        sleep.assert_called_once_with(0.01)

        multi = Mock()
        multi.sample.side_effect = [
            processes.SessionScanIncomplete("1"),
            processes.SessionScanIncomplete("2"),
            [],
        ]
        with (
            patch.object(processes.util, "monotonic", side_effect=[0, 0.1, 0.2]),
            patch.object(processes.util, "sleep") as sleep,
        ):
            self.assertEqual([], processes.settled_sample(multi, notify=False))
        self.assertEqual(3, multi.sample.call_count)
        self.assertEqual(2, sleep.call_count)

    def test_non_retiring_sample_records_live_members_and_retains_empty_sid_during_churn(self):
        member = self.row(55, 4242)
        extra_leader = self.row(4343, 4343, birth=3.0)
        tree = self.scan(
            [[10, 55], *([[10, 55], [11, 55]] * 3)],
            [self.row(10), self.row(11), member],
            unavailable=(10,),
        )
        tree.sessions[4343] = extra_leader
        tree.known[4343] = extra_leader
        rows = tree.sample(notify=False, retire_empty=False)
        self.assertEqual([55], [row["pid"] for row in rows])
        self.assertIn(55, tree.known)
        self.assertEqual({4242, 4343}, set(tree.sessions))
        with self.assertRaises(processes.SessionScanIncomplete):
            tree._session_candidates(set(), {4242, 4343})

    def test_live_poll_sample_defers_empty_sid_churn_within_bound(self):
        import autocode_supervision_keeper as keeper

        tree = Mock()
        tree.sample.return_value = [{"pid": 5}]
        with patch.object(keeper.processes, "settled_sample", return_value=[{"pid": 1}]) as settled:
            self.assertEqual(([{"pid": 1}], None), keeper.live_poll_sample(tree, 10.0))
        settled.assert_called_once_with(tree, notify=False)
        tree.sample.assert_not_called()

        with patch.object(keeper.processes, "settled_sample", side_effect=processes.SessionScanIncomplete("churn")):
            with self.assertRaises(processes.SessionScanIncomplete):
                keeper.live_poll_sample(tree, None, is_triggered=lambda: True)
        tree.sample.assert_not_called()

        with (
            patch.object(keeper.processes, "settled_sample", side_effect=processes.SessionScanIncomplete("churn")),
            patch.object(keeper.time, "monotonic", side_effect=[10.0, 24.9, 25.0]),
        ):
            rows_1, since_1 = keeper.live_poll_sample(tree, None)
            self.assertEqual(([{"pid": 5}], 10.0), (rows_1, since_1))
            tree.sample.assert_called_once_with(notify=False, retire_empty=False)

            rows_2, since_2 = keeper.live_poll_sample(tree, since_1)
            self.assertEqual(([{"pid": 5}], 10.0), (rows_2, since_2))
            self.assertEqual(2, tree.sample.call_count)

            with self.assertRaises(processes.SessionScanIncomplete):
                keeper.live_poll_sample(tree, since_2)
            self.assertEqual(2, tree.sample.call_count)


if __name__ == "__main__":
    unittest.main()
