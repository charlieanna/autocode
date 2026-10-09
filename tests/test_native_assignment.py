"""Pure causal-assignment controls; synthetic rows grant no native authority.

No subprocess, provider, browser, capture, state reader or retained run. The
native owner separately authenticates original decision bytes and sink ACKs.
"""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json
import unittest
from unittest.mock import Mock, patch

import autocode_contract_identity as contracts
import autocode_goal_lifecycle as lifecycle
import autocode_native_assignment as assignment
import autocode_util as util
import autocode_visual_checkpoint as checkpoint
import autocode_visual_protection as protection
import autocode_visual_scope as scope


def fixture():
    paths = ['ui/chat.py', 'ui/work.py']
    spec = {'kind': 'implement', 'milestone_id': 'M1', 'acceptance_criteria': ['C1', 'C2'],
            'requirements': ['Both panes remain usable'], 'validation_plan': ['Run complete-case validation'], 'findings': []}
    body = {'open_blocking_questions': [], 'acceptance_criteria': [{'id': 'C1'}, {'id': 'C2'}],
        'constraints': [scope.MARKER + scope.POLICY,
                        scope.CASE_MARKER + json.dumps({'chat': ['C1'], 'work': ['C2']})],
        'milestones': [{'id': 'M1', 'acceptance_criteria': ['C1', 'C2'], 'depends_on': [], 'affected_paths': paths}],
        'initial_task': {**deepcopy(spec), 'objective': 'Build both panes', 'affected_paths': paths}}
    goal = {'task_id': 'pure', 'revision': 1, 'body': body}
    goal['hash'] = util.digest(goal)
    goal.update(origin='planning', approval_status='approved', approval_event=None)
    goal['approval_event'] = {'actor': 'user_cli', 'token': contracts.token(goal)}
    source = {'head': 'fixed', 'files': {'ui/chat.py': 'a' * 64, 'ui/work.py': 'b' * 64}}
    source['revision'] = util.digest(source)
    task = {**deepcopy(spec), 'id': 'task-original', 'assigned_at': 'original', 'objective': 'Build both panes',
        'affected_paths': deepcopy(paths), 'source_revision': source['revision'], 'contract_hash': goal['hash'],
        'contract_revision': 1, 'decision': 'CONTINUE'}
    settings = {'design_manifest': {'body': {'version': 1, 'cases': [
        {'id': 'chat', 'implementation_paths': ['ui/chat.py']}, {'id': 'work', 'implementation_paths': ['ui/work.py']}]}},
        'roles': {'terra': {'model': 'controlled-builder', 'reasoning_effort': 'high'}},
        'milestone_checkpoints': {'enabled': True}, 'limits': {'stage_timeout_seconds': 15}}
    state = {'goal_contract': goal, 'settings': settings, 'current_task': task,
        'user_events': [deepcopy(goal['approval_event'])], 'findings_ledger': [], 'milestone_progress': {}}
    decision = {'status': 'CONTINUE', 'next_objective': 'Independently validate every approved case',
        'next_task': {**deepcopy(spec), 'kind': 'validate'}, 'affected_paths': [], 'evidence': [],
        'user_request': {'kind': 'none'}}
    return state, source, decision


class AssignmentLogicTests(unittest.TestCase):
    def setUp(self):
        self.state, self.source, self.decision = fixture()
        self.binding = {'contract': self.state['goal_contract'], 'settings': self.state['settings'],
            'task': self.state['current_task'], 'source': self.source, 'runtime_sha256': 'c' * 64}
        self.rows = []
        self.append('approval-committed', {'contract': self.binding['contract'], 'settings': self.binding['settings'],
            'current_task': self.binding['task'], 'snapshot': self.source, 'runtime_sha256': self.binding['runtime_sha256']})

    def append(self, kind, body):
        row = {'id': str(len(self.rows)), 'sequence': len(self.rows), 'kind': kind, 'body': deepcopy(body)}
        self.rows.append(row)
        return row

    def handoff(self, old=None):
        old = old or self.binding
        task = {**deepcopy(old['task']), 'id': 'task-next-' + str(len(self.rows)), 'kind': 'validate'}
        prepared = self.append('assignment-prepared', {'binding': deepcopy(old), 'new_task': task})
        intent = self.append('parent-commit-prepared', {'publications': [{'kind': 'assignments', 'id': prepared['id']}],
            'task': task, 'source': old['source'], 'contract': old['contract'],
            'settings_sha256': util.digest(old['settings']), 'runtime_sha256': old['runtime_sha256']})
        ack = self.append('parent-commit-acknowledged', {**intent['body'], 'intent_id': intent['id'],
            'sink_identity': {'path': '/controlled/state.json', 'device': 1, 'inode': 1, 'bytes': 1, 'mode': 0o600}})
        committed = self.append('assignment-committed', {**prepared['body'], 'prepared_id': prepared['id'], 'parent_commit_id': ack['id']})
        return {**deepcopy(old), 'task': task}, prepared, ack, committed

    def proof(self, decision):
        derived = scope.derive(self.state['goal_contract']['body'], self.state['settings']['design_manifest']['body'],
            self.state['current_task'], accepted_milestones=(), accepted_case_input_paths={})
        return checkpoint.VerifiedCheckpoint(checkpoint.binding(self.state, self.source), self.source['revision'],
            'c' * 64, json.dumps(derived, sort_keys=True, separators=(',', ':')), (), (), True, 'd' * 64,
            authorized_assignment=checkpoint.assignment_identity(assignment.normalized(self.state, decision)))

    def broad_validation(self):
        body = self.state['goal_contract']['body']
        writes = ['ui/chat.py', 'ui/work.py']
        inputs = [*writes, 'companion/api.py', 'qualification/helper.py']
        body['constraints'].append('VISUAL_WRITER_PATHS=' + json.dumps(writes, separators=(',', ':')))
        body['milestones'][0]['affected_paths'] = inputs
        body['initial_task'].update(kind='validate', affected_paths=deepcopy(inputs))
        self.state['current_task'].update(kind='validate', affected_paths=deepcopy(inputs))
        goal = self.state['goal_contract']
        goal['hash'] = util.digest({key: goal[key] for key in ('task_id', 'revision', 'body')})
        event = {'actor': 'user_cli', 'token': contracts.token(goal)}
        goal['approval_event'] = event; self.state['user_events'] = [deepcopy(event)]
        self.state['current_task']['contract_hash'] = goal['hash']
        decision = deepcopy(self.decision)
        decision.update(status='REWORK', affected_paths=deepcopy(writes), evidence=['original retained defect'])
        decision['next_task']['kind'] = 'implement'
        return decision, inputs, writes

    def test_actual_ordinary_assignment_preserves_narrow_writer_under_broad_read_owner(self):
        decision, inputs, writes = self.broad_validation()
        before = deepcopy(self.state)
        kind = lifecycle.assign_task(self.state, decision, self.source, visual_proof=self.proof(decision))
        task = assignment.capture(before, self.state, assignment.normalized(before, decision), self.source)
        self.assertEqual(kind, 'implement')
        self.assertEqual(task['affected_paths'], writes)
        self.assertEqual(self.state['goal_contract']['body']['milestones'][0]['affected_paths'], inputs)
        self.assertEqual(task['acceptance_criteria'], ['C1', 'C2'])
        self.assertEqual(self.state['task_archive'], [before['current_task']])
        self.assertEqual(self.state['settings'], before['settings'])
        self.assertNotEqual(task['id'], before['current_task']['id'])

    def test_protected_companion_helper_or_foreign_writer_path_refuses_before_actual_assignment(self):
        decision, inputs, writes = self.broad_validation()
        for bad in ('companion/api.py', 'qualification/helper.py', 'foreign/outside.py'):
            changed = deepcopy(decision); changed['affected_paths'].append(bad)
            original = deepcopy(self.state)
            with self.subTest(path=bad), self.assertRaises(util.Paused):
                lifecycle.assign_task(self.state, changed, self.source, visual_proof=self.proof(changed))
            self.assertEqual(original, self.state)

    def test_writer_inventory_missing_duplicate_noncanonical_and_wildcards_refuse(self):
        decision, inputs, writes = self.broad_validation()
        body = self.state['goal_contract']['body']
        baseline = deepcopy(body)
        for value in (None, '[]', '["ui/*"]', '[ "ui/chat.py" ]', '["../escape"]', '["companion/new.py"]'):
            body.clear(); body.update(deepcopy(baseline))
            body['constraints'] = [row for row in body['constraints'] if not row.startswith('VISUAL_WRITER_PATHS=')]
            if value is not None: body['constraints'].append('VISUAL_WRITER_PATHS=' + value)
            with self.subTest(inventory=value), self.assertRaises((util.Paused, ValueError)):
                assignment.normalized(self.state, decision)
        body.clear(); body.update(baseline)
        body['constraints'].append(body['constraints'][-1])
        with self.assertRaises(util.Paused): assignment.normalized(self.state, decision)

    def test_ordinary_assign_task_generates_and_archives_actual_final_validate(self):
        before = deepcopy(self.state)
        kind = lifecycle.assign_task(self.state, self.decision, self.source, visual_proof=self.proof(self.decision))
        task = assignment.capture(before, self.state, assignment.normalized(before, self.decision), self.source)
        self.assertEqual(kind, 'validate')
        self.assertEqual(task['kind'], 'validate')
        self.assertEqual(task['acceptance_criteria'], ['C1', 'C2'])
        self.assertEqual(task['affected_paths'], ['ui/chat.py', 'ui/work.py'])
        self.assertEqual(self.state['task_archive'], [before['current_task']])
        self.assertEqual(self.state['settings'], before['settings'])
        self.assertEqual(self.state['goal_contract'], before['goal_contract'])
        self.assertNotEqual(task['id'], before['current_task']['id'])

    def test_ordinary_rework_assigns_open_findings_without_fabricating_task(self):
        self.state['findings_ledger'] = [{'id': 'F2', 'status': 'open'}, {'id': 'F1', 'status': 'open'}]
        decision = deepcopy(self.decision)
        decision.update(status='REWORK', next_objective='Correct the two verified pane defects',
                        affected_paths=['ui/chat.py', 'ui/work.py'], evidence=['actual defect'])
        decision['next_task']['kind'] = 'implement'
        before = deepcopy(self.state)
        lifecycle.assign_task(self.state, decision, self.source, visual_proof=self.proof(decision))
        task = assignment.capture(before, self.state, assignment.normalized(before, decision), self.source)
        self.assertEqual(task['findings'], ['F1', 'F2'])
        self.assertEqual(task['decision'], 'REWORK')

    def test_foreign_owner_criteria_paths_or_status_do_not_normalize(self):
        for key in ('owner', 'criteria', 'paths', 'status', 'human'):
            decision = deepcopy(self.decision)
            if key == 'owner': decision['next_task']['milestone_id'] = 'M2'
            elif key == 'criteria': decision['next_task']['acceptance_criteria'] = ['C1']
            elif key == 'paths': decision['affected_paths'] = ['outside.py']
            elif key == 'status': decision['status'] = 'REWORK'
            else: decision['user_request']['kind'] = 'human_review'
            with self.subTest(key=key), self.assertRaises(util.Paused): assignment.normalized(self.state, decision)

    def test_real_lifecycle_requires_exact_typed_assignment_proof(self):
        before = deepcopy(self.state)
        with self.assertRaises(util.Paused): lifecycle.assign_task(self.state, self.decision, self.source,
            visual_proof=replace(self.proof(self.decision), authorized_assignment=None))
        self.assertEqual(before, self.state)

    def test_pure_chain_selects_latest_and_historical_exact_tasks(self):
        new, prepared, ack, committed = self.handoff()
        result = assignment.committed_chain(self.rows, new)
        self.assertIs(result[1], committed)
        self.assertEqual(result[3], (committed,))
        old = assignment.committed_chain(self.rows, self.binding, historical=True)
        self.assertIs(old[1], self.rows[0])
        self.assertEqual(old[3], ())
        with self.assertRaises(util.Paused): assignment.committed_chain(self.rows, self.binding)

    def test_preparation_without_ack_or_commit_has_no_even_historical_authority(self):
        self.append('assignment-prepared', {'binding': self.binding, 'new_task': {'id': 'uncommitted'}})
        with self.assertRaisesRegex(util.Paused, 'unfinished assignment'):
            assignment.committed_chain(self.rows, self.binding, historical=True)

    def test_ack_without_commit_is_preserved_but_not_authority(self):
        new, prepared, ack, committed = self.handoff()
        self.rows.pop()
        with self.assertRaises(util.Paused): assignment.committed_chain(self.rows, self.binding, historical=True)
        self.assertEqual(self.rows[-1], ack)

    def test_invalidated_ack_and_late_branched_task_reject_whole_historical_chain(self):
        new, prepared, ack, committed = self.handoff()
        original = deepcopy(self.rows)
        self.rows.insert(committed['sequence'], {'id': 'invalid', 'sequence': committed['sequence'],
            'kind': 'parent-commit-invalidated', 'body': {'parent_commit_id': ack['id']}})
        committed['sequence'] += 1
        with self.assertRaises(util.Paused): assignment.committed_chain(self.rows, self.binding, historical=True)
        self.rows = original
        bad = deepcopy(self.binding); bad['task']['id'] = 'branched'
        self.handoff(bad)
        with self.assertRaisesRegex(util.Paused, 'branches'):
            assignment.committed_chain(self.rows, self.binding, historical=True)

    def test_changed_source_before_assignment_or_after_ack_is_not_handoff(self):
        new, prepared, ack, committed = self.handoff()
        for row in (prepared, committed): row['body']['binding']['source'] = {'revision': 'foreign'}
        with self.assertRaises(util.Paused): assignment.committed_chain(self.rows, self.binding, historical=True)

    def test_all_historical_assignments_are_authenticated_not_only_selected_prefix(self):
        new, prepared, ack, committed = self.handoff()
        owner = object.__new__(protection.NativeProtection)
        owner.run = Path('/controlled'); owner._assignment_validator = Mock(side_effect=util.Paused('HELD', 'late decision changed'))
        with self.assertRaisesRegex(util.Paused, 'late decision changed'):
            owner._initial_task_authority(self.binding, self.rows, historical=True)
        owner._assignment_validator.assert_called_once_with(committed['body'])

    def test_foreign_original_sink_is_refused_even_for_old_writer_identity(self):
        new, prepared, ack, committed = self.handoff()
        owner = object.__new__(protection.NativeProtection)
        owner.run = Path('/another'); owner._assignment_validator = Mock()
        with self.assertRaisesRegex(util.Paused, 'sink identity'):
            owner._initial_task_authority(self.binding, self.rows, historical=True)

    def test_taskless_unfinished_planning_launch_still_holds_first_writer(self):
        owner = object.__new__(protection.NativeProtection)
        taskless = {'id': 'unfinished', 'body': {'binding': {'task': None}}}
        owner._chain = Mock(return_value=([{'kind': 'prepared', **taskless}], {'unfinished': taskless}, {}, {}, {}))
        with self.assertRaisesRegex(util.Paused, 'crash gap'):
            owner.first_writer_authority({}, snapshot={})
        owner._chain.assert_called_once_with()

    def test_taskless_unfinished_check_still_holds_first_writer(self):
        owner = object.__new__(protection.NativeProtection)
        owner._chain = Mock(return_value=([{'kind': 'check-prepared', 'body': {'binding': {'task': None}}}], {}, {}, {}, {}))
        with self.assertRaisesRegex(util.Paused, 'crash gap'):
            owner.first_writer_authority({}, snapshot={})
        owner._chain.assert_called_once_with()


if __name__ == '__main__': unittest.main()
