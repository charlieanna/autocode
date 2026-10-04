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


if __name__ == "__main__":
    unittest.main()
