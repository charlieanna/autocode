"""Original typed ParentSink handoff controls with controlled model evidence.

The ordinary assign_task, generated task/archive, original journal, typed
publication handle and actual normalized atomic sink ACK are real. Independent
model review/decision evidence is explicitly controlled; these tests grant no
native image/model, source-write, or final integration qualification.
"""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import replace
import unittest
from unittest.mock import patch

import autocode_goal_lifecycle as lifecycle
import autocode_native_assignment as assignments
import autocode_native_parent as parent
import autocode_source_scope as source_scope
import autocode_util as util
import autocode_visual_checkpoint as checkpoint
import tests.test_native_writer as fixtures


class OriginalAssignmentSinkControls(unittest.TestCase):
    prepare = fixtures.FirstWriterAuthorityTests.prepare
    acknowledge = fixtures.FirstWriterAuthorityTests.acknowledge
    commit = fixtures.FirstWriterAuthorityTests.commit

    def setUp(self):
        fixtures.FirstWriterAuthorityTests.setUp(self)
        self.state, self.current = self.commit()
        self.sink = parent.ParentSink(self.run, factory=self.factory, ordinary_write=util.atomic_json,
            snapshot=lambda value: source_scope.snapshot(self.root, value))
        self.factory.bind_parent_sink(self.sink, parent.ParentSink)
        initial = self.state['goal_contract']['body']['initial_task']
        self.decision = {'status': 'CONTINUE', 'next_objective': 'Perform actual all-case final validation',
            'next_task': {key: deepcopy(value) for key, value in initial.items() if key not in ('objective', 'affected_paths')},
            'affected_paths': [], 'evidence': [], 'user_request': {'kind': 'none'}}
        self.decision['next_task']['kind'] = 'validate'
        self.record = {'stage': 'astra_review', 'role': 'astra_review', 'output': str(self.run / 'controlled-decision.json')}
        util.atomic_json(self.record['output'], self.decision)
        self.state['stages'] = [self.record]
        self.selected = {'kind': 'reviews', 'path': str(self.run / 'controlled-review.json'), 'sha256': 'd' * 64}
        self.bound = self.owner._binding(self.state, self.current)
        self.proof = replace(self.factory.first_writer_proof(self.state, snapshot=self.current),
            current_scope_accepted=True, receipt_sha256='d' * 64)
        self.pins = {str(self.anchor): util.file_hash(self.anchor)}

    @contextmanager
    def controlled_decision(self):
        with ExitStack() as stack:
            stack.enter_context(patch.object(self.factory, 'current_decision_review', return_value=self.selected))
            stack.enter_context(patch.object(self.factory, 'decision_review_proof', return_value=self.proof))
            stack.enter_context(patch.object(self.factory, 'verify_decision_review', return_value={
                'metadata': {'binding': self.bound, 'receipt': {'scope_verdict': 'PASS', 'scope_all_accepted': True}}}))
            stack.enter_context(patch.object(self.factory, '_decision_evidence',
                return_value=(deepcopy(self.pins), util.digest(self.decision))))
            stack.enter_context(patch.object(self.factory.history, '_retained_pins', return_value=self.pins))
            yield

    def assign(self):
        old = deepcopy(self.state)
        kind = self.factory.assign_task(lifecycle.assign_task, self.state, self.decision, self.current, self.record)
        self.assertEqual(kind, 'validate')
        self.assertEqual(self.state['task_archive'], [old['current_task']])
        self.assertEqual(self.state['settings'], old['settings'])
        self.assertEqual(self.state['goal_contract'], old['goal_contract'])
        handles = self.factory.evidence_publications()
        self.assertEqual(len(handles), 1)
        self.assertIs(type(handles[0]), assignments.PreparedAssignment)
        return handles

    def persist(self, handles):
        with self.sink.publication(publications=handles):
            self.sink.write(self.run / 'state.json', self.state)
        self.assertIsNotNone(self.sink.ack)

    def test_actual_typed_assignment_commits_only_after_original_normalized_sink_ack(self):
        with self.controlled_decision():
            handles = self.assign()
            with self.assertRaisesRegex(util.Paused, 'unfinished assignment'):
                self.owner.require_task_authority(self.state, snapshot=self.current)
            self.persist(handles)
            self.assertFalse(any(row['kind'] == 'assignment-committed' for row in self.owner._rows()))
            published = self.sink.publish(self.state, publications=handles)
            self.assertEqual(len(published), 1)
            assignment = self.owner.require_task_authority(self.state, snapshot=self.current)
            self.assertEqual(assignment['id'], published[0])
            self.assertEqual(assignment['body']['new_task'], self.state['current_task'])
            self.assertEqual(self.state['current_task']['kind'], 'validate')
            ack = next(row for row in self.owner._rows() if row['id'] == assignment['body']['parent_commit_id'])
            self.assertEqual(ack['body']['publications'], [{'kind': 'assignments', 'id': handles[0].row_id}])
            self.assertEqual(ack['body']['sink_identity']['path'], str(self.run / 'state.json'))
            self.assertEqual(self.sink.durable_candidate, self.state)
        # Controlled evidence cannot survive as forged native authority.
        with self.assertRaises(util.Paused): self.owner.require_task_authority(self.state, snapshot=self.current)

    def test_failed_replace_preserves_actual_task_but_grants_no_assignment(self):
        with self.controlled_decision():
            handles = self.assign(); actual = deepcopy(self.state)
            with patch.object(util.os, 'replace', side_effect=OSError('controlled before replace')):
                with self.assertRaises(OSError): self.persist(handles)
            self.assertEqual(self.state, actual)
            self.assertFalse(any(row['kind'] == 'assignment-committed' for row in self.owner._rows()))
            with self.assertRaises(util.Paused): self.owner.require_task_authority(self.state, snapshot=self.current)

    def test_post_durable_failure_keeps_exact_candidate_and_holds_authority(self):
        with self.controlled_decision():
            handles = self.assign(); actual = deepcopy(self.state); self.persist(handles)
            append = self.owner._append
            def fail(kind, body):
                if kind == 'assignment-committed': raise OSError('controlled commit publication failure')
                return append(kind, body)
            with patch.object(self.owner, '_append', side_effect=fail), self.assertRaises(util.JSONCommitUncertain) as error:
                self.sink.publish(self.state, publications=handles)
            self.assertEqual(error.exception.candidate, actual)
            self.assertEqual(self.sink.durable_candidate, actual)
            self.assertTrue(self.sink.blocked)
            self.assertFalse(any(row['kind'] == 'assignment-committed' for row in self.owner._rows()))
            with self.assertRaises(util.Paused): self.owner.require_task_authority(actual, snapshot=self.current)

    def test_later_actual_parent_write_invalidates_assignment_ack(self):
        with self.controlled_decision():
            handles = self.assign(); self.persist(handles)
            prior_ack = self.sink.ack.row_id
            later = {**deepcopy(self.state), 'controlled_later_intervention': True}
            self.sink.write(self.run / 'state.json', later)
            with self.assertRaises(util.JSONCommitUncertain) as error:
                self.sink.publish(self.state, publications=handles)
            self.assertEqual(error.exception.candidate, later)
            self.assertTrue(any(row['kind'] == 'parent-commit-invalidated'
                and row['body']['parent_commit_id'] == prior_ack for row in self.owner._rows()))
            self.assertFalse(any(row['kind'] == 'assignment-committed' for row in self.owner._rows()))


if __name__ == '__main__': unittest.main()
