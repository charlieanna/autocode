"""Prospective session authority is recorded before a blocked setsid continues.

Pure native metadata fixtures exercise pending, failed and exited creators;
there are no processes, signals, clocks or paid providers in these tests.
"""

import contextlib
import copy
import unittest
from unittest.mock import patch

import autocode_process as processes


def native(pid, *, sid, birth=1.0, parent=1, group=None):
    return {
        "pid": pid,
        "parent": parent,
        "session": sid,
        "group": pid if group is None else group,
        "birth_identity": birth,
        "started": f"birth-{birth}",
        "state": "sleeping",
    }


class SessionAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.provider = native(501, sid=501)
        self.creator = native(502, sid=501, birth=2.0, group=501)
        self.rows = {row["pid"]: row for row in (self.provider, self.creator)}
        contexts = contextlib.ExitStack()
        self.addCleanup(contexts.close)
        self.native_table = contexts.enter_context(
            patch.object(
                processes, "process_table", side_effect=lambda pids: {p: self.rows[p] for p in pids if p in self.rows}
            )
        )
        contexts.enter_context(patch.object(processes, "process_ids", side_effect=lambda: list(self.rows)))
        contexts.enter_context(patch.object(processes.os, "getsid", side_effect=lambda p: self.rows[p]["session"]))
        contexts.enter_context(patch.object(processes.os, "getpgid", side_effect=lambda p: self.rows[p]["group"]))
        contexts.enter_context(patch.object(processes.process_children, "descendants", return_value=[]))
        contexts.enter_context(
            patch.object(processes, "_birth_identity", side_effect=lambda p: self.rows[p.pid]["birth_identity"])
        )
        contexts.enter_context(
            patch.object(processes.psutil, "Process", side_effect=lambda p: type("Process", (), {"pid": p})())
        )
        self.tree = processes.ProcessTree(501, lambda rows: None, sessions=(self.provider,))
        self.tree.known = {501: self.provider}

    def sampled(self, tree=None):
        return [row["pid"] for row in (tree or self.tree).sample(notify=False)]

    def test_pending_authority_survives_an_empty_future_sid_before_continue(self):
        self.tree.admit_session(self.creator)
        saved = self.tree.admitted_sessions()
        self.assertEqual(
            {
                "kind": "setsid_pending",
                "creator": {**processes.identity(self.creator), "session": 501},
                "requested_session": 502,
            },
            saved[0],
        )
        self.assertEqual({501, 502}, set(self.sampled()))
        self.assertIn(502, self.tree.sessions, "An empty prospective SID revoked a call that has not run yet")
        self.assertEqual(saved, self.tree.admitted_sessions(), "Sampling manufactured a successful setsid outcome")
        saved[0]["creator"]["birth_identity"] = 999
        self.assertEqual(2.0, self.tree.admitted_sessions()[0]["creator"]["birth_identity"])

    def test_creator_exit_before_next_sample_retains_a_reparented_new_session_child(self):
        self.tree.admit_session(self.creator)
        self.rows.clear()
        self.rows[503] = native(503, sid=502, birth=3.0, group=503)
        self.assertEqual([503], self.sampled())
        self.assertEqual({502}, set(self.tree.sessions))
        self.assertEqual("setsid_pending", self.tree.admitted_sessions()[0]["kind"])

    def test_fallback_reconstructs_pending_authority_after_creator_has_exited(self):
        self.tree.admit_session(self.creator)
        records = self.tree.admitted_sessions()
        self.rows.clear()
        self.rows[503] = native(503, sid=502, birth=3.0)
        fallback = processes.ProcessTree(501, lambda rows: None, session_admissions=records)
        self.assertEqual([503], self.sampled(fallback))
        self.assertEqual(records, fallback.admitted_sessions())

    def test_actual_session_observation_does_not_publish_a_syscall_success(self):
        self.tree.admit_session(self.creator)
        saved = self.tree.admitted_sessions()
        self.rows[502] = {**self.creator, "session": 502, "group": 502}
        self.assertEqual({501, 502}, set(self.sampled()))
        self.assertEqual(saved, self.tree.admitted_sessions())
        self.tree.admit_session(self.rows[502])  # another setsid on a session leader may fail EPERM
        self.assertEqual(saved, self.tree.admitted_sessions())

    def test_repeated_setsid_handoff_acknowledges_exact_retained_authority_after_session_change(self):
        self.tree.admit_session(self.creator)
        outer = processes.ProcessTree(501, lambda rows: None, sessions=(self.provider,))
        initial = self.tree.admitted_sessions()
        outer.admit_session(initial[0]["creator"])
        self.rows[502] = {**self.creator, "session": 502, "group": 502}
        # A second native call may fail EPERM. The inner keeper sends its
        # original ownership record; the outer keeper only acknowledges it.
        self.tree.admit_session(self.rows[502])
        repeated = self.tree.admitted_sessions()
        outer.admit_session(repeated[0]["creator"])
        self.assertEqual(initial, repeated)
        self.assertEqual(initial, outer.admitted_sessions())
        changed = {**initial[0]["creator"], "group": 999}
        with self.assertRaisesRegex(processes.ProcessError, "membership changed"):
            outer.admit_session(changed)
        self.rows[502] = {**self.rows[502], "birth_identity": 999}
        with self.assertRaisesRegex(processes.ProcessError, "birth changed"):
            outer.admit_session(initial[0]["creator"])

    def test_failed_setsid_retires_only_after_creator_is_gone_and_future_sid_is_empty(self):
        self.tree.admit_session(self.creator)
        self.assertIn(502, self.sampled())
        self.rows.pop(502)  # it remained in the old SID until exiting
        self.assertEqual([501], self.sampled())
        self.assertNotIn(502, self.tree.sessions)
        self.assertEqual([], self.tree.admitted_sessions())
        self.rows[601] = native(601, sid=502, birth=20.0)  # a later unrelated reuse is not owned
        self.assertEqual([501], self.sampled())
        self.assertNotIn(601, self.tree.known)

    def test_reused_creator_pid_revokes_its_future_sid_without_claiming_foreign_members(self):
        self.tree.admit_session(self.creator)
        self.rows.clear()
        self.rows[502] = native(502, sid=502, birth=20.0)
        self.rows[503] = native(503, sid=502, birth=30.0)
        self.assertEqual([], self.sampled())
        self.assertNotIn(502, self.tree.sessions)
        self.assertEqual([], self.tree.admitted_sessions())

    def test_denied_or_reused_creator_cannot_be_admitted(self):
        with patch.object(processes, "process_table", side_effect=processes.ProcessError("inspection denied")):
            with self.assertRaisesRegex(processes.ProcessError, "inspection denied"):
                self.tree.admit_session(self.creator)
        self.rows[502] = {**self.creator, "birth_identity": 20.0}
        with self.assertRaisesRegex(processes.ProcessError, "birth changed"):
            self.tree.admit_session(self.creator)
        self.assertEqual([], self.tree.admitted_sessions())

    def test_foreign_session_and_excluded_keeper_cannot_gain_authority(self):
        foreign = native(601, sid=601, birth=20.0)
        self.rows[601] = foreign
        with self.assertRaisesRegex(processes.ProcessError, "outside an admitted"):
            self.tree.admit_session(foreign)
        excluded = processes.ProcessTree(501, lambda rows: None, sessions=(self.provider,), excluded=(self.creator,))
        with self.assertRaisesRegex(processes.ProcessError, "no exact creator"):
            excluded.admit_session(self.creator)
        self.assertEqual([], excluded.admitted_sessions())

    def test_unknown_membership_keeps_pending_authority(self):
        self.tree.admit_session(self.creator)
        self.rows.clear()
        self.rows[503] = native(503, sid=502, birth=3.0)
        with patch.object(processes.os, "getsid", side_effect=PermissionError("inspection denied")):
            with self.assertRaisesRegex(processes.ProcessError, "cleanup remains unknown"):
                self.sampled()
        self.assertIn(502, self.tree.sessions)

    def test_changing_empty_scan_does_not_discard_escaped_authority(self):
        self.tree.admit_session(self.creator)
        self.rows.clear()
        with patch.object(processes, "process_ids", side_effect=[[], [900], [], [901], [], [902]]):
            with self.assertRaisesRegex(processes.ProcessError, "Process set changed"):
                self.sampled()
        self.assertIn(502, self.tree.sessions)
        self.assertTrue(self.tree.admitted_sessions())

    def test_invalid_restored_authority_fails_closed_and_old_receipts_stay_unchanged(self):
        original = {"kind": "setsid_pending", "creator": self.creator, "requested_session": 502}
        for changed in (
            {"requested_session": 0},
            {"requested_session": True},
            {"kind": "setsid_succeeded"},
            {"creator": {**self.creator, "birth_identity": float("nan")}},
            {"creator": {**self.creator, "group": 0}},
            {"creator": {**self.creator, "started": None}},
        ):
            with self.subTest(changed=changed):
                with self.assertRaises(processes.ProcessError):
                    processes.ProcessTree(501, lambda rows: None, session_admissions=({**original, **changed},))
        with self.assertRaises(processes.ProcessError):
            processes.ProcessTree(501, lambda rows: None, session_admissions=None)
        self.assertEqual([], processes.ProcessTree(501, lambda rows: None).admitted_sessions())

    def test_ownership_bound_refuses_before_mutating_pending_authority(self):
        before = copy.deepcopy(self.tree.known)
        with patch.object(processes.command_receipts, "within_reader_limit", return_value=False):
            with self.assertRaisesRegex(processes.ProcessError, "receipt bound"):
                self.tree.admit_session(self.creator)
        self.assertEqual(before, self.tree.known)
        self.assertEqual([], self.tree.admitted_sessions())
        self.assertNotIn(502, self.tree.sessions)


if __name__ == "__main__":
    unittest.main()
