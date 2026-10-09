"""Worker admission controls through main(), with an offline provider boundary."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_builder_worker as worker
import goal_fixtures

runner = worker.runner


class WorkerBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='builder-worker-')
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        subprocess.run(['git', 'init', '-q', str(self.workspace)], check=True, capture_output=True)
        self.directory = self.workspace / '.autocode' / 'runs' / 'member'
        self.directory.mkdir(parents=True)
        self.parent = self.workspace / '.autocode' / 'runs' / 'parent'
        self.parent.mkdir()
        runner.write_json(self.parent / 'state.json', {'stages': []})
        self.calls = []
        self.intervention = None
        self.complete = False
        self.invalid_builder = False
        for target, name, replacement in (
                (worker.source_scope, 'snapshot', self.snapshot),
                (runner.support, 'snapshot', self.snapshot),
                (runner.util, 'snapshot', self.snapshot),
                (runner.autopilot.regression, 'before_review', lambda *args: None),
                (runner.autocode_providers, 'resolve', lambda *args: runner.opencode),
                (worker.stage_context, 'context_packet', lambda *args: ('prompt', {})),
                (runner, 'run_role', self.provider)):
            mock = patch.object(target, name, replacement)
            mock.start()
            self.addCleanup(mock.stop)

    def snapshot(self, workspace, *args, **kwargs):
        source = Path(workspace) / 'greet.py'
        files = {'greet.py': runner.support.file_hash(source)} if source.exists() else {}
        return {'head': 'fixture', 'files': files, 'revision': runner.support.digest(files)}

    def seed(self, **updates):
        state = {'version': 3, 'workspace': str(self.workspace), 'task': 'Deliver a greeting CLI',
                 'task_id': 'fixture-goal',
                 'status': 'RUNNING', 'iteration': 1, 'sessions': {}, 'stages': [], 'history': [],
                 'parent_run': str(self.parent), 'settings': {
                     'engine': 'codex', 'provider': 'opencode',
                     'roles': {name: {'model': 'fixture-' + name, 'reasoning_effort': 'high'}
                               for name in ('terra', 'sol', 'completion', 'astra')},
                     'builder_retry': dict(runner.autopilot.builder_policy.DEFAULTS),
                     'report_repair': {'max_attempts': 2},
                     'limits': {}, 'headroom': {'enabled': False}}}
        goal_fixtures.approve_fixture(state, runner.goals)
        contract = state['goal_contract']
        state['current_task'] = {
            'id': 'member-task', 'kind': 'implement', 'milestone_id': 'M1',
            'objective': 'Deliver a greeting CLI', 'affected_paths': ['greet.py'],
            'requirements': ['Print a greeting'], 'acceptance_criteria': ['C1'],
            'validation_plan': ['Check the greeting'], 'findings': [],
            'contract_hash': contract['hash'], 'contract_revision': contract['revision'],
            'source_revision': self.snapshot(self.workspace)['revision']}
        state.update(updates)
        runner.write_json(self.directory / 'state.json', state)
        return state

    def report(self, state, output):
        return {**goal_fixtures.envelope(state), 'evidence_refs': [str(output)],
                'commands_run': [], 'results': ['Nothing written'], 'changed_files': [],
                'remaining_risks': [], 'untested_behavior': [], 'addressed_requirements': []}

    def attempt(self, state, stage='terra', *, report_only=False):
        base = self.directory / (stage + '-fixture')
        output = base.with_suffix('.json')
        before = self.snapshot(self.workspace)
        if self.complete:
            (self.workspace / 'greet.py').write_text("print('Hello')\n")
        after = self.snapshot(self.workspace)
        value = self.report(state, output)
        record = {'role': 'terra', 'stage': stage, 'iteration': 1,
                  'output': str(output), 'events': str(base.with_suffix('.jsonl')),
                  'before_ref': str(base.with_suffix('.before.json')),
                  'after_ref': str(base.with_suffix('.after.json')),
                  'schema': str(self.directory / 'schema.json'), 'exit_code': 0,
                  'started_at': '2026-10-08T00:00:00+00:00', 'finished_at': '2026-10-08T00:00:01+00:00',
                  'metrics': {}, 'task_id': state['current_task']['id'],
                  'contract_hash': state['goal_contract']['hash'],
                  'contract_revision': state['goal_contract']['revision'],
                  'source_revision': after['revision'],
                  'changed_files': ['greet.py'] if self.complete else [], 'diff_ref': None}
        if report_only:
            record.update(report_only=True, original_stage='terra')
        runner.write_json(output, value)
        Path(record['events']).write_text(json.dumps({'type': 'turn.completed'}) + '\n')
        runner.write_json(Path(record['before_ref']), before)
        runner.write_json(Path(record['after_ref']), after)
        runner.write_json(Path(record['schema']), {'type': 'object'})
        return value, record

    def submit(self):
        runner.interventions.submit(self.workspace, self.directory,
                                    request_id='operator-' + self.intervention, kind=self.intervention, text='')

    def provider(self, **kwargs):
        report_only = kwargs.get('report_only', False)
        stage = kwargs['state']['next_stage'] + ('_report_repair' if report_only else '')
        self.calls.append(stage)
        self.assertEqual('terra', kwargs['role'], 'No subsequent diagnosis may be paid for')
        value, record = self.attempt(kwargs['state'], stage, report_only=report_only)
        if self.invalid_builder and not report_only:
            value.pop('evidence_refs')
            runner.write_json(Path(record['output']), value)
        kwargs['state']['active_stage'] = record
        runner.write_json(self.directory / 'state.json', kwargs['state'])
        if self.intervention and (not self.invalid_builder or report_only):
            self.submit()
        return value, record

    def result(self):
        return runner.read_json(self.directory / 'result.json')

    def test_queued_pause_at_no_change_commit_admits_no_subsequent_paid_call(self):
        self.seed()
        self.intervention = 'pause'
        self.assertEqual(2, worker.main(self.directory))
        self.assertEqual(['terra'], self.calls)
        self.assertEqual('PAUSED_INTERVENTION', self.result()['status'])
        self.assertIn('explicitly resume', self.result()['reason'])
        self.assertFalse((self.workspace / 'greet.py').exists())
        self.assertEqual([], runner.interventions.pending(self.directory))
        self.assertEqual(2, worker.main(self.directory, 'recover'))
        self.assertEqual(['terra'], self.calls)
        self.assertEqual('PAUSED_INTERVENTION', self.result()['status'])

    def test_queued_stop_at_no_change_commit_admits_no_subsequent_paid_call(self):
        self.seed()
        self.intervention = 'stop'
        self.assertEqual(2, worker.main(self.directory))
        self.assertEqual(['terra'], self.calls)
        self.assertEqual('PAUSED_INTERVENTION', self.result()['status'])
        self.assertEqual(runner.stop_policy.STOP_REASON, self.result()['reason'])
        self.assertEqual(2, worker.main(self.directory, 'retry'))
        self.assertEqual(['terra'], self.calls)

    def test_recovered_completed_builder_commit_respects_queued_operator_hold(self):
        self.recovered_hold('pause')

    def test_recovered_completed_builder_commit_respects_queued_stop(self):
        self.recovered_hold('stop')

    def test_recovered_report_only_repair_commit_respects_queued_operator_hold(self):
        self.recovered_hold('pause', report_only=True)

    def test_recovered_report_only_repair_commit_respects_queued_stop(self):
        self.recovered_hold('stop', report_only=True)

    def test_explicit_retry_cannot_override_a_pause_at_recovered_result_commit(self):
        self.recovered_hold('pause', mode='retry')

    def test_explicit_retry_cannot_override_a_stop_at_recovered_repair_commit(self):
        self.recovered_hold('stop', report_only=True, mode='retry')

    def recovered_hold(self, kind, *, report_only=False, mode='recover'):
        state = self.seed(status='PAUSED_BUDGET' if mode == 'retry' else 'RUNNING')
        value, record = self.attempt(state)
        if report_only:
            state['pending_report_repair'] = {
                'original': record, 'attempts': 1, 'contract_hash': state['goal_contract']['hash'],
                'pins': {record[key]: runner.support.file_hash(record[key])
                         for key in ('events', 'before_ref', 'after_ref', 'schema')}}
            value, record = self.attempt(state, 'terra_report_repair', report_only=True)
        state['active_stage'] = record
        runner.write_json(self.directory / 'state.json', state)
        self.intervention = kind
        self.submit()
        def recover(current, directory, workspace):
            if report_only:
                runner.accept_repaired_report(current, directory, workspace, value, record)
            else:
                runner.commit_stage_result(current, 'terra', value, record, workspace, directory)
        with patch.object(runner, 'reconcile_active', side_effect=recover):
            self.assertEqual(2, worker.main(self.directory, mode))
        self.assertEqual([], self.calls)
        self.assertEqual('PAUSED_INTERVENTION', self.result()['status'])
        self.assertEqual(runner.stop_policy.STOP_REASON if kind == 'stop' else
                         'Queued pause was applied; explicitly resume when ready.', self.result()['reason'])

    def test_report_only_repair_commit_respects_queued_operator_hold(self):
        self.repair_hold('pause')

    def test_report_only_repair_commit_respects_queued_stop(self):
        self.repair_hold('stop')

    def repair_hold(self, kind):
        self.seed()
        self.invalid_builder = True
        self.intervention = kind
        self.assertEqual(2, worker.main(self.directory))
        self.assertEqual(['terra', 'terra_report_repair'], self.calls)
        self.assertEqual('PAUSED_INTERVENTION', self.result()['status'])
        self.assertEqual(runner.stop_policy.STOP_REASON if kind == 'stop' else
                         'Queued pause was applied; explicitly resume when ready.', self.result()['reason'])
        self.assertFalse((self.workspace / 'greet.py').exists())

    def test_interrupted_investigator_retry_retains_ownership_and_no_writer_authority(self):
        self.interrupted_diagnosis('investigate_stuck')

    def test_interrupted_investigator_repair_retry_retains_ownership_and_no_writer_authority(self):
        self.interrupted_diagnosis('investigate_stuck_report_repair', report_only=True)

    def interrupted_diagnosis(self, stage, *, report_only=False):
        state = self.seed(next_stage='investigate_stuck', status='PAUSED_INTERRUPTED',
                          stuck_investigation={'mode': 'builder_failure', 'identity': 'bound-incident'})
        _, record = self.attempt(state, stage, report_only=report_only)
        if report_only:
            record['original_stage'] = 'investigate_stuck'
        state['active_stage'] = record
        runner.write_json(self.directory / 'state.json', state)
        output = Path(record['output']).read_bytes()
        with (patch.object(runner, 'reconcile_active', side_effect=runner.support.Paused(
                'PAUSED_INTERRUPTED', 'Investigator fixture interrupted; inspect owned attempt')),
                patch.object(runner, 'abandon_stage') as abandon):
            self.assertEqual(2, worker.main(self.directory, 'retry'))
        abandon.assert_not_called()
        self.assertEqual([], self.calls)
        self.assertEqual('PAUSED_INTERRUPTED', self.result()['status'])
        saved = runner.read_json(self.directory / 'state.json')
        self.assertEqual(record, saved['active_stage'])
        self.assertEqual('investigate_stuck', saved['next_stage'])
        self.assertEqual('bound-incident', saved['stuck_investigation']['identity'])
        self.assertEqual(output, Path(record['output']).read_bytes())

    def test_explicit_quota_stopped_builder_retry_still_builds(self):
        self.seed(status='PAUSED_BUDGET', stop_reason='Provider quota stopped this member')
        self.complete = True
        self.assertEqual(0, worker.main(self.directory, 'retry'))
        self.assertEqual(['terra'], self.calls)
        self.assertEqual('BUILT', self.result()['status'])
        self.assertEqual("print('Hello')\n", (self.workspace / 'greet.py').read_text())

    def test_explicit_member_retry_can_resume_an_entry_time_nonterminal_pause(self):
        self.seed(status='PAUSED_INTERVENTION', stop_reason='Queued pause was applied',
                  pause_intent={'acknowledged_at': None, 'request_ids': ['saved-pause']},
                  applied_interventions=[{'kind': 'pause', 'id': 'saved-pause'}])
        self.complete = True
        self.assertEqual(0, worker.main(self.directory, 'retry'))
        self.assertEqual(['terra'], self.calls)
        self.assertEqual('BUILT', self.result()['status'])
        saved = runner.read_json(self.directory / 'state.json')
        self.assertTrue(saved['pause_intent']['acknowledged_at'])
        self.assertEqual(saved['pause_intent']['acknowledged_at'],
                         saved['applied_interventions'][0]['resumed_at'])

    def test_explicit_interrupted_builder_retry_still_abandons_only_the_writer(self):
        state = self.seed(status='PAUSED_INTERRUPTED')
        _, record = self.attempt(state)
        state['active_stage'] = record
        runner.write_json(self.directory / 'state.json', state)
        self.complete = True
        with patch.object(runner, 'reconcile_active', side_effect=runner.support.Paused(
                'PAUSED_INTERRUPTED', 'Stopped Builder fixture requires explicit retry')):
            self.assertEqual(0, worker.main(self.directory, 'retry'))
        self.assertEqual(['terra'], self.calls)
        self.assertEqual('BUILT', self.result()['status'])
        self.assertEqual(1, len(list(self.directory.glob('archived-terra-fixture-*'))))

    def test_committed_implementation_does_not_publish_built_over_an_operator_pause(self):
        self.seed()
        self.complete = True
        self.intervention = 'pause'
        self.assertEqual(2, worker.main(self.directory))
        self.assertEqual(['terra'], self.calls)
        self.assertEqual('PAUSED_INTERVENTION', self.result()['status'])
        self.assertEqual("print('Hello')\n", (self.workspace / 'greet.py').read_text())

    def test_explicit_retry_does_not_reset_exhausted_builder_authority(self):
        state = self.seed(status='PAUSED_BUILDER_RETRY_LIMIT')
        key = runner.autopilot.builder_policy.key(state)
        state['builder_retries'] = {key: {
            'initial_route': dict(state['settings']['roles']['terra']),
            'failures': ['ordinary', 'retry', 'strong'], 'action': 'pause'}}
        runner.write_json(self.directory / 'state.json', state)
        self.assertEqual(2, worker.main(self.directory, 'retry'))
        self.assertEqual([], self.calls)
        self.assertEqual('PAUSED_BUILDER_RETRY_LIMIT', self.result()['status'])
        self.assertEqual(state['builder_retries'], runner.read_json(
            self.directory / 'state.json')['builder_retries'])

    def test_explicit_exhausted_report_retry_preserves_spent_repair_allowance(self):
        state = self.seed(status='PAUSED_REPORT_REPAIR_LIMIT')
        _, original = self.attempt(state, 'prior-terra')
        original['stage'] = 'terra'
        original['rejected'] = True
        state['stages'] = [original]
        state['pending_report_repair'] = {
            'original': original, 'attempts': 2, 'contract_hash': state['goal_contract']['hash'],
            'pins': {original[key]: runner.support.file_hash(original[key])
                     for key in ('events', 'before_ref', 'after_ref', 'schema')}}
        runner.write_json(self.directory / 'state.json', state)
        self.complete = True
        self.assertEqual(0, worker.main(self.directory, 'retry'))
        self.assertEqual(['terra'], self.calls)
        self.assertEqual('BUILT', self.result()['status'])
        saved = runner.read_json(self.directory / 'state.json')
        self.assertEqual(2, saved['report_repair_archive'][0]['repair']['attempts'])
        self.assertNotIn('pending_report_repair', saved)


if __name__ == '__main__':
    unittest.main()
