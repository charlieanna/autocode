"""Source/approval transaction controls; no native/model qualification.

Planning publication is an explicitly constructed fixture. Actual Git snapshots,
lock handles and the atomic JSON sink exercise source and durability semantics.
No retained run or its private state is opened.
"""
from copy import deepcopy
from pathlib import Path
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import autocode_contract_identity as contracts
import autocode_source_scope as source_scope
import autocode_source_snapshot as source_snapshot
import autocode_util as util
import autocode_visual_protection as protection
import autocode_visual_history as history
import autocode_visual_source_transition as transition


class SourceTransitionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='native-source-transition-unit-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        (self.root / '.gitignore').write_text('.autocode/\nignored.txt\nfuture.txt\nunrelated.txt\n')
        (self.root / 'tracked.txt').write_text('original\n')
        for args in (['init', '-q'], ['add', '.'], ['-c', 'user.name=Fixture', '-c',
                     'user.email=fixture@example.test', 'commit', '-qm', 'fixture']):
            subprocess.run(['git', '-C', str(self.root), *args], check=True, capture_output=True)
        (self.root / 'ignored.txt').write_text('approved ignored bytes\n')
        self.run = self.root / '.autocode' / 'runs' / 'owned'
        self.run.mkdir(parents=True)
        self.run_handle = (self.run / 'writer.lock').open('a+')
        self.checkout_handle = (self.root / '.autocode' / 'writer.lock').open('a+')
        self.addCleanup(self.run_handle.close)
        self.addCleanup(self.checkout_handle.close)
        self.runtime = {'controlled-runtime.py': 'a' * 64}
        runtime = patch.object(protection.history, 'runtime_files', return_value=self.runtime)
        runtime.start(); self.addCleanup(runtime.stop)
        self.raw = util.snapshot(self.root)
        self.owner = protection.NativeProtection(self.root, self.run,
            run_handle=self.run_handle, checkout_handle=self.checkout_handle,
            start_state={'goal_contract': None, 'settings': {}, 'stages': []}, snapshot=self.raw)
        self.origin_file = next(self.owner.root.iterdir())
        self.origin_bytes = self.origin_file.read_bytes()
        paths = ['ignored.txt', 'future.txt']
        initial = {'kind': 'validate', 'milestone_id': 'M1', 'objective': 'Read approved source',
            'requirements': ['Preserve source'], 'acceptance_criteria': ['C1'],
            'affected_paths': paths, 'validation_plan': ['controlled check'], 'findings': []}
        body = {'open_blocking_questions': [], 'acceptance_criteria': [{'id': 'C1'}],
            'constraints': ['VISUAL_SLICE_GATES=complete-cases-v1', 'VISUAL_CASE_CRITERIA={"only":["C1"]}'],
            'milestones': [{'id': 'M1', 'acceptance_criteria': ['C1'], 'depends_on': [], 'affected_paths': paths}],
            'initial_task': initial}
        goal = {'task_id': 'fixture', 'revision': 1, 'body': body}
        goal['hash'] = util.digest(goal)
        goal.update(origin='user_cli_edit', approval_status='draft', approval_event=None)
        self.token = contracts.token(goal)
        self.state = {'workspace': str(self.root), 'goal_contract': goal, 'settings': {
            'joint_planning': True, 'design_manifest': {'body': {'version': 1, 'cases': [
                {'id': 'only', 'implementation_paths': paths}]}}}, 'current_task': None,
            'status': 'AWAITING_GOAL_APPROVAL', 'displayed_goal': self.token,
            'planning': {'final_token': self.token}, 'user_events': [], 'findings_ledger': []}
        anchor = self.run / 'controlled-planning.txt'
        anchor.write_text('Controlled fixture, not a native planning receipt.\n')
        self.owner._append('planning-committed', {'contract': deepcopy(goal),
            'settings': deepcopy(self.state['settings']), 'source': deepcopy(self.raw),
            'token': self.token, 'pins': {str(anchor): util.file_hash(anchor)}})

    def prepare(self):
        handle = self.owner.prepare_initial_approval(self.state, self.token, snapshot=self.raw)
        candidate = deepcopy(self.state)
        event = {'kind': 'goal_approval', 'actor': 'user_cli', 'token': self.token, 'at': util.now()}
        candidate['user_events'].append(event)
        candidate['goal_contract'].update(approval_status='approved', approval_event=event)
        selected = source_scope.snapshot(self.root, candidate)
        self.owner.initial_assignment_proof(handle, candidate, snapshot=selected)
        decision = protection._json(handle.body)['decision']
        candidate['current_task'] = {**deepcopy(decision['next_task']), 'id': 'task-0123456789ab',
            'assigned_at': util.now(), 'objective': decision['next_objective'],
            'affected_paths': decision['affected_paths'], 'source_revision': selected['revision'],
            'contract_hash': candidate['goal_contract']['hash'], 'contract_revision': 1, 'decision': 'CONTINUE'}
        return handle, candidate, selected

    def acknowledge(self, handle, candidate, selected):
        intent = self.owner.prepare_parent_commit(candidate, snapshot=selected, approval=handle)
        receipts = []
        # This is a new disposable fixture sink, never a retained task state.
        util.atomic_json(self.run / 'state.json', candidate, after_commit=lambda **written:
            receipts.append(self.owner.capture_parent_commit(intent,
                committed_candidate=written['value'], serialized_bytes=written['serialized_bytes'],
                sink_identity=written['sink_identity'], snapshot=selected)))
        return receipts[0]

    def commit(self):
        handle, candidate, selected = self.prepare()
        ack = self.acknowledge(handle, candidate, selected)
        self.owner.commit_initial_approval(handle, candidate, snapshot=selected, parent_commit=ack)
        return handle, candidate, selected, ack

    def test_raw_origin_and_planning_remain_immutable_through_actual_sink_transition(self):
        _handle, candidate, selected, _ack = self.commit()
        self.assertIn('ignored.txt', selected['files'])
        self.assertNotIn('ignored.txt', self.raw['files'])
        self.assertEqual(['future.txt', 'ignored.txt'], selected['source_paths'])
        self.assertEqual(self.origin_bytes, self.origin_file.read_bytes())
        rows = self.owner._chain()[0]
        self.assertEqual(self.raw, rows[1]['body']['source'])
        self.assertEqual((selected, candidate['goal_contract']), transition.cursor(rows))
        self.owner._source_continuity(selected)
        reopened = protection.NativeProtection(self.root, self.run, run_handle=self.run_handle,
            checkout_handle=self.checkout_handle, start_state=candidate, snapshot=selected)
        self.assertEqual(selected, reopened._fresh_snapshot())

    def test_preparation_and_ack_alone_do_not_advance_source_or_launch_authority(self):
        handle, candidate, selected = self.prepare()
        for acknowledged in (False, True):
            if acknowledged:
                self.acknowledge(handle, candidate, selected)
            with self.subTest(ack=acknowledged):
                self.assertEqual(self.raw, transition.cursor(self.owner._rows())[0])
                with self.assertRaises(util.Paused):
                    self.owner.require_task_authority(candidate, snapshot=selected)

    def test_missing_user_event_failed_candidate_and_changed_paths_cannot_commit(self):
        handle, candidate, selected = self.prepare()
        for change in ('event', 'paths', 'task'):
            broken = deepcopy(candidate)
            if change == 'event': broken['user_events'] = []
            elif change == 'paths': broken['goal_contract']['body']['milestones'][0]['affected_paths'].append('tracked.txt')
            else: broken['current_task']['source_revision'] = self.raw['revision']
            with self.subTest(change=change), self.assertRaises(util.Paused):
                self.owner.prepare_parent_commit(broken, snapshot=selected, approval=handle)
        self.assertEqual(self.raw, transition.cursor(self.owner._rows())[0])

    def test_stale_ignored_source_between_preparation_and_sink_refuses(self):
        handle, candidate, selected = self.prepare()
        (self.root / 'ignored.txt').write_text('changed while approval pending\n')
        self.assertEqual(self.raw, util.snapshot(self.root))
        with self.assertRaises(util.Paused):
            self.owner.prepare_parent_commit(candidate, snapshot=selected, approval=handle)

    def test_stale_ignored_source_after_actual_ack_cannot_advance_scope(self):
        handle, candidate, selected = self.prepare()
        ack = self.acknowledge(handle, candidate, selected)
        (self.root / 'ignored.txt').unlink()
        with self.assertRaises(util.Paused):
            self.owner.commit_initial_approval(handle, candidate, snapshot=selected, parent_commit=ack)
        self.assertEqual(self.raw, transition.cursor(self.owner._rows())[0])

    def test_failed_atomic_sink_has_no_ack_or_committed_scope(self):
        handle, candidate, selected = self.prepare()
        original = deepcopy(candidate)
        with patch.object(util.os, 'replace', side_effect=OSError('controlled replace failure')):
            with self.assertRaisesRegex(OSError, 'controlled replace failure'):
                self.acknowledge(handle, candidate, selected)
        self.assertEqual(original, candidate)
        self.assertEqual(self.raw, transition.cursor(self.owner._rows())[0])
        self.assertFalse(any(r['kind'] in ('parent-commit-acknowledged', 'approval-committed')
                             for r in self.owner._rows()))

    def test_history_current_boundaries_refuse_changed_approved_ignored_bytes(self):
        _handle, candidate, selected, _ack = self.commit()
        owner = object.__new__(history.NativeReviewHistory)
        owner.workspace = self.root
        (self.root / 'ignored.txt').write_text('changed before current history proof\n')
        initial = {**candidate, 'current_task': None}
        calls = [lambda: owner.retain_review(candidate, {}, {}, context={}, accepted_validation={}, snapshot=selected),
                 lambda: owner.prepare_checkpoint(candidate, review_selector={}, completion_record={},
                     accepted_decision={}, prerequisite_selectors={}, snapshot=selected),
                 lambda: owner.fresh_facts(state=candidate, snapshot=selected),
                 lambda: owner.authorize_initial_assignment(initial, {}, snapshot=selected)]
        for call in calls:
            with self.subTest(boundary=call), self.assertRaises(util.Paused):
                call()

    def test_intervening_parent_write_invalidates_ack_and_preserves_raw_epoch(self):
        handle, candidate, selected = self.prepare()
        ack = self.acknowledge(handle, candidate, selected)
        util.atomic_json(self.run / 'state.json', {**candidate, 'intervention': 'later'},
            after_commit=lambda **_: self.owner.invalidate_parent_ack())
        with self.assertRaises(util.Paused):
            self.owner.commit_initial_approval(handle, candidate, snapshot=selected, parent_commit=ack)
        self.assertEqual(self.raw, transition.cursor(self.owner._rows())[0])

    def test_foreign_or_replayed_handle_cannot_publish(self):
        handle, candidate, selected = self.prepare()
        ack = self.acknowledge(handle, candidate, selected)
        foreign = protection.PreparedApproval(handle.owner, handle.nonce, handle.row_id, handle.body)
        with self.assertRaises(util.Paused):
            self.owner.commit_initial_approval(foreign, candidate, snapshot=selected, parent_commit=ack)
        self.owner.commit_initial_approval(handle, candidate, snapshot=selected, parent_commit=ack)
        with self.assertRaises(util.Paused):
            self.owner.commit_initial_approval(handle, candidate, snapshot=selected, parent_commit=ack)

    def test_chronological_replay_rejects_missing_reordered_foreign_or_invalidated_ack(self):
        self.commit()
        rows = self.owner._rows()
        for kind in ('missing-intent', 'reordered', 'foreign', 'replayed', 'invalidated'):
            broken = deepcopy(rows)
            if kind == 'missing-intent': broken = [r for r in broken if r['kind'] != 'parent-commit-prepared']
            elif kind == 'reordered': broken[-1], broken[-2] = broken[-2], broken[-1]
            elif kind == 'foreign': broken[-1]['body']['parent_commit_id'] = 'f' * 64
            elif kind == 'replayed': broken.append(deepcopy(broken[-1]))
            else: broken.insert(-1, {'id': 'invalidated', 'kind': 'parent-commit-invalidated',
                'body': {'parent_commit_id': broken[-2]['id']}})
            with self.subTest(kind=kind), self.assertRaises(util.Paused):
                transition.cursor(broken)

    def test_approved_ignored_create_edit_delete_invalidate_but_unrelated_ignored_does_not(self):
        _handle, _candidate, selected, _ack = self.commit()
        (self.root / 'unrelated.txt').write_text('not selected\n')
        self.owner._source_continuity(selected)
        for name, content in [('future.txt', 'new'), ('ignored.txt', 'edited'), ('ignored.txt', None)]:
            path = self.root / name
            old = path.read_bytes() if path.exists() else None
            try:
                if content is None: path.unlink()
                else: path.write_text(content)
                with self.subTest(name=name, content=content), self.assertRaises(util.Paused):
                    self.owner._source_continuity(selected)
            finally:
                if old is None: path.unlink(missing_ok=True)
                else: path.write_bytes(old)
        self.assertEqual(self.origin_bytes, self.origin_file.read_bytes())

    def test_declared_paths_are_canonical_but_do_not_approve_a_draft(self):
        self.assertEqual(['future.txt', 'ignored.txt'], source_scope.declared_paths(self.state['goal_contract']['body']))
        self.assertEqual([], source_scope.paths(self.state))
        self.assertEqual(self.raw, source_scope.snapshot(self.root, self.state))
        chosen = source_snapshot.snapshot(self.root, paths=['ignored.txt', 'future.txt'])
        for paths in (['ignored.txt'], ['ignored.txt', 'future.txt'], ['../escape'], ['ignored.txt', 'ignored.txt']):
            with self.subTest(paths=paths), self.assertRaises(util.Paused):
                transition.selected({**chosen, 'source_paths': paths}, self.state['goal_contract']['body'])


if __name__ == '__main__':
    unittest.main()
