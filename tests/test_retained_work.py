"""Retained Builder work across attempts: a compiled program a build left behind is not source.

A live Go run's `go build .` wrote ./policy into the repository (2026-09-29). The serial scope gate
already excused it (autocode_assignment.build_output); the retained-work routes must too, and must
not hand it to the Validator as a changed file.
"""
import json
import copy
from pathlib import Path
import tempfile
import unittest

import autocode_retained_work as retained_work

ELF = b"\x7fELF\x02\x01\x01" + b"\0" * 64


class RetainedWorkTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.workspace = self.dir / "project"
        (self.workspace / "src").mkdir(parents=True)
        (self.workspace / "src" / "new.go").write_text("package src\n")

    def snapshot(self, name, files, revision):
        path = self.dir / name
        path.write_text(json.dumps({"head": "h", "files": files, "revision": revision}))
        return str(path)

    def state(self, after_files):
        """A first attempt that left work, then a retry that changed nothing."""
        start = self.snapshot("a.before", {}, "r0")
        left = self.snapshot("a.after", after_files, "r1")
        first = {"stage": "terra", "iteration": 1, "task_id": "task-1", "started_at": "t1",
                 "before_ref": start, "after_ref": left, "changed_files": sorted(after_files)}
        retry = {"stage": "terra", "iteration": 1, "task_id": "task-1", "started_at": "t2",
                 "before_ref": left, "after_ref": self.snapshot("b.after", after_files, "r1"),
                 "changed_files": [], "source_revision": "r1", "output": "terra-02.json"}
        state = {"workspace": str(self.workspace), "goal_contract": {"hash": "c"}, "stages": [first],
                 "current_task": {"id": "task-1", "kind": "implement", "affected_paths": ["src"]}}
        return state, retry

    def test_a_build_left_binary_is_neither_out_of_scope_nor_part_of_the_candidate(self):
        (self.workspace / "policy").write_bytes(ELF)
        state, retry = self.state({"src/new.go": "1", "policy": "executable:x"})
        self.assertEqual({"source_revision": "r1", "retained_paths": ["src/new.go"]},
                         retained_work.fresh_candidate(state, retry, "r1"))

    def test_a_binary_alone_is_no_candidate(self):
        (self.workspace / "src" / "new.go").unlink()
        (self.workspace / "policy").write_bytes(ELF)
        state, retry = self.state({"policy": "executable:x"})
        self.assertIsNone(retained_work.fresh_candidate(state, retry, "r1"))

    def test_any_other_file_outside_the_assignment_still_stops_the_route(self):
        (self.workspace / "notes.txt").write_text("stray\n")
        state, retry = self.state({"src/new.go": "1", "notes.txt": "n"})
        self.assertIsNone(retained_work.fresh_candidate(state, retry, "r1"))
        (self.workspace / "policy").write_text("#!/bin/sh\n")  # executable text is not build output
        state, retry = self.state({"src/new.go": "1", "policy": "executable:x"})
        self.assertIsNone(retained_work.fresh_candidate(state, retry, "r1"))

    def repaired_assignment(self):
        state, retry = self.state({"src/new.go": "1"})
        earlier = {**state['current_task'], 'contract_hash': 'c', 'milestone_id': 'M1'}
        state['task_archive'] = [earlier]
        state['current_task'] = {**earlier, 'id': 'repair-2', 'source_revision': 'r1'}
        retry['task_id'] = 'repair-2'
        return state, retry

    def test_new_repair_assignment_retains_same_milestone_candidate_for_fresh_review(self):
        state, retry = self.repaired_assignment()
        self.assertEqual({'source_revision': 'r1', 'retained_paths': ['src/new.go'], 'origin_task_id': 'task-1'},
                         retained_work.fresh_candidate(state, retry, 'r1'))

    def test_repair_cannot_inherit_other_contract_milestone_scope_or_source(self):
        state, retry = self.repaired_assignment()
        for change in ({'contract_hash': 'other'}, {'milestone_id': 'M2'}, {'affected_paths': ['elsewhere']},
                       {'kind': 'validate'}):
            with self.subTest(change=change):
                modified = copy.deepcopy(state)
                modified['task_archive'][0].update(change)
                self.assertIsNone(retained_work.fresh_candidate(modified, retry, 'r1'))
        self.assertIsNone(retained_work.fresh_candidate(state, retry, 'changed-again'))
        Path(state['stages'][0]['before_ref']).unlink()
        self.assertIsNone(retained_work.fresh_candidate(state, retry, 'r1'))

    def test_repair_does_not_hide_an_earlier_out_of_scope_edit(self):
        state, retry = self.repaired_assignment()
        after = Path(retry['after_ref'])
        data = json.loads(after.read_text()); data['files']['outside.txt'] = 'stray'
        after.write_text(json.dumps(data))
        self.assertIsNone(retained_work.fresh_candidate(state, retry, 'r1'))


class OwnRepairSourceTests(unittest.TestCase):
    """The source a packet-bound Builder retry starts from: the packet's, or its own attempts' retained work.

    Live feature-stock-refusals run (2026-10-06): a rejected repair attempt kept its test edit, the runner
    removed the backup it wrote outside its assignment, and the retry's admission called that a stale handoff.
    """
    PACKET = {"path": "packet.json", "sha256": "p"}
    BOUND = {"stock.py": "s0", "tests/test_stock.py": "t0", "README.md": "d0"}

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)

    def snapshot(self, name, files, revision, head="h"):
        path = self.dir / name
        path.write_text(json.dumps({"head": head, "files": files, "revision": revision}))
        return str(path)

    def attempt(self, name, before, after, packet=PACKET, **row):
        return {"stage": "terra", "before_ref": self.snapshot(name + ".before", *before),
                "after_ref": self.snapshot(name + ".after", *after), "recovery_novelty": {"packet": packet}, **row}

    def rejected(self):
        """The live attempt: a test edit in scope and a backup the runner then removed."""
        left = {**self.BOUND, "tests/test_stock.py": "t1", "stock_current.py": "s0"}
        return [{"stage": "astra_resolve", "recovery_novelty": {"packet": self.PACKET}},
                self.attempt("builder-01", (self.BOUND, "r0"), (left, "r1"), rejected=True)]

    def own(self, stages, files, head="h", bound="r0"):
        return retained_work.own_repair_source(stages, self.PACKET, bound, {"head": head, "files": files})

    def test_the_rejected_attempts_retained_edit_and_the_runners_cleanup_are_its_own_work(self):
        retained = {**self.BOUND, "tests/test_stock.py": "t1"}
        self.assertTrue(self.own(self.rejected(), retained))
        # An edit the runner restored to its bound content, or never removed, is still the attempt's own.
        self.assertTrue(self.own(self.rejected(), self.BOUND))
        self.assertTrue(self.own(self.rejected(), {**retained, "stock_current.py": "s0"}))

    def test_a_later_attempt_under_the_same_packet_carries_the_earlier_ones_work(self):
        later = {**self.BOUND, "tests/test_stock.py": "t1", "stock.py": "s2"}
        stages = [*self.rejected(), self.attempt("builder-02", ({**self.BOUND, "tests/test_stock.py": "t1"}, "r1"),
                                                 (later, "r2"), timed_out=True)]
        self.assertTrue(self.own(stages, later))
        self.assertFalse(self.own(stages, {**later, "stock.py": "s1"}))

    def test_any_other_change_is_not_autocodes_own(self):
        retained = {**self.BOUND, "tests/test_stock.py": "t1"}
        for name, files, head in (
                ("a person's edit of another file", {**retained, "README.md": "d1"}, "h"),
                ("a further edit of the retained file", {**retained, "tests/test_stock.py": "t2"}, "h"),
                ("a new file", {**retained, "notes.txt": "n"}, "h"),
                ("a deleted file", {key: value for key, value in retained.items() if key != "stock.py"}, "h"),
                ("a new commit", retained, "h2")):
            with self.subTest(name):
                self.assertFalse(self.own(self.rejected(), files, head))

    def test_a_head_the_attempt_moved_is_not_its_own_work(self):
        # A Builder that committed, reset or switched branches moved HEAD itself: fail closed.
        retained = {**self.BOUND, "tests/test_stock.py": "t1"}
        moved = [self.attempt("builder-01", (self.BOUND, "r0"), (retained, "r1", "h-commit"), rejected=True)]
        for head in ("h-commit", "h"):
            with self.subTest(head=head):
                self.assertFalse(self.own(moved, retained, head))

    def test_missing_evidence_or_another_packet_proves_nothing(self):
        retained = {**self.BOUND, "tests/test_stock.py": "t1"}
        self.assertFalse(self.own([], retained))
        self.assertFalse(self.own(self.rejected(), retained, bound="another-revision"))
        other = [self.attempt("other", (self.BOUND, "r0"), (retained, "r1"), packet={"path": "other.json"})]
        self.assertFalse(self.own(other, retained))
        stages = self.rejected()
        Path(stages[-1]["after_ref"]).unlink()
        self.assertFalse(self.own(stages, retained))
        self.assertFalse(retained_work.own_repair_source(self.rejected(), None, "r0", {"head": "h", "files": retained}))


if __name__ == "__main__":
    unittest.main()
