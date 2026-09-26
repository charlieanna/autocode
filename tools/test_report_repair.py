"""Report-only recovery tests: fixtures and mocked providers, no live model calls."""
import copy
import json
import shutil
from pathlib import Path
from unittest.mock import patch
import unittest
from . import test_autocode as base
from . import test_subprocess

runner, support = base.runner, base.s


class RepairTests(unittest.TestCase):
    setUp = base.RetrofitTest.setUp

    def test_builder_repair_preserves_failed_results_commands_and_user_decisions(self):
        report = self.run / 'original-builder.json'
        original = {'stage': 'terra', 'output': str(report), 'events': str(self.run / 'events.jsonl')}
        value = {'commands_run': ['failed-test'], 'results': ['exit=1'], 'changed_files': ['x.py'],
                 'remaining_risks': ['not verified'], 'user_request': {'kind': 'permission', 'decision_needed': 'Allow?'}}
        report.write_text(json.dumps(value))
        runner.assert_repair_preserves_builder_history(original, {**value, 'summary': 'Added missing summary',
                                                                 'evidence_refs': ['event:check']})
        for field, replacement in [('commands_run', ['invented-test']), ('results', ['PASS']),
                                   ('remaining_risks', []), ('changed_files', []),
                                   ('user_request', {'kind': 'none'})]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                runner.assert_repair_preserves_builder_history(original, {**value, field: replacement})

    def test_builder_repair_missing_commands_must_use_original_events(self):
        report = self.run / 'original-builder.json'
        report.write_text('{')
        events = self.run / 'original-events.jsonl'
        events.write_text(json.dumps({'type': 'item.completed', 'item': {'type': 'command_execution',
            'command': 'real-test', 'exit_code': 1}}))
        original = {'stage': 'terra', 'output': str(report), 'events': str(events)}
        runner.assert_repair_preserves_builder_history(original, {'commands_run': ['real-test']})
        with self.assertRaisesRegex(ValueError, 'invented a command'):
            runner.assert_repair_preserves_builder_history(original, {'commands_run': ['fake-test']})

    def queue(self, error=None, **overrides):
        self.state['settings']['report_repair'] = {'max_attempts': 2}
        path = self.run / 'iterations/005/terra-01'
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {'role': 'terra', 'stage': 'terra', 'iteration': 5, 'exit_code': 0,
                  'duration_seconds': 1, 'source_revision': support.snapshot(self.root)['revision']}
        for key, suffix, value in [('output', '.json', '{}'), ('events', '.jsonl',
                json.dumps({'type': 'thread.started', 'thread_id': 't-session'})+'\n'+json.dumps({'type': 'turn.completed'})),
                ('before_ref', '.before.json', '{}'), ('after_ref', '.after.json', '{}'), ('schema', '.schema.json', '{}')]:
            p = Path(str(path)+suffix); p.write_text(value); record[key] = str(p)
        record.update(overrides)
        with self.assertRaises(runner.ReportRepairQueued):
            runner.reject_completed_stage(self.state, self.run, record, error or ValueError('Missing summary'))
        return self.state['pending_report_repair']

    def reject_repair(self, iteration, error):
        original = self.state['pending_report_repair']['original']
        path = self.run / 'iterations' / f'{iteration:03d}' / 'terra_report_repair-01'
        path.parent.mkdir(parents=True, exist_ok=True)
        role = original['role']
        record = {'role': role, 'stage': f'{role}_report_repair', 'original_stage': original['stage'],
                  'report_only': True, 'iteration': iteration, 'exit_code': 0,
                  'duration_seconds': 1, 'source_revision': original['source_revision'],
                  'schema': original['schema']}
        for field, suffix in (('output', '.json'), ('events', '.jsonl'),
                              ('before_ref', '.before.json'), ('after_ref', '.after.json')):
            destination = Path(str(path) + suffix)
            shutil.copy2(original[field], destination)
            record[field] = str(destination)
        return runner.reject_completed_stage(self.state, self.run, record, error)

    def test_exact_format_failure_can_request_one_fresh_sol_validation(self):
        self.state['next_stage'] = 'sol'
        error = RuntimeError('OpenCode final message is not a JSON report; inspect the saved raw events')
        self.queue(role='sol', stage='sol', error=error)
        self.state['pending_report_repair']['attempts'] = 1
        with self.assertRaises(runner.ReportRepairQueued):
            self.reject_repair(6, error)
        self.state = support.read(self.run / 'state.json')
        self.state['pending_report_repair']['attempts'] = 2
        with self.assertRaises(support.Paused):
            self.reject_repair(7, error)
        self.state = support.read(self.run / 'state.json')
        selected = runner.attempt_id(self.state['stages'][-1])
        with self.assertRaises(ValueError):
            runner.retry_format_failed_report(self.state, self.run, self.root, selected + '-wrong')
        self.assertIn('pending_report_repair', self.state)
        changed = self.root / 'changed-input.txt'
        changed.write_text('new source revision')
        with self.assertRaises(ValueError):
            runner.retry_format_failed_report(self.state, self.run, self.root, selected)
        changed.unlink()
        runner.retry_format_failed_report(self.state, self.run, self.root, selected)
        self.assertEqual('sol', self.state['next_stage'])
        self.assertNotIn('pending_report_repair', self.state)
        self.assertEqual('report_retry_after_format_fix', self.state['user_events'][-1]['kind'])
        self.assertTrue(self.state['report_repair_archive'])

    def test_terminal_error_is_durably_queued_without_replaying_implementation(self):
        sessions = copy.deepcopy(self.state['sessions'])
        pending = self.queue()
        saved = support.read(self.run/'state.json')
        self.assertEqual('RUNNING', saved['status'])
        self.assertEqual(0, pending['attempts'])
        self.assertEqual(sessions, saved['sessions'])
        self.assertTrue(Path(pending['original']['events']).is_file())

    def test_same_failure_survives_restart_and_stops_unchanged_retry(self):
        self.queue()
        self.assertEqual(1, next(iter(self.state['failure_history'].values()))['count'])
        self.state['pending_report_repair']['attempts'] = 1
        with self.assertRaises(runner.ReportRepairQueued):
            self.reject_repair(6, ValueError('Missing summary'))
        self.state = support.read(self.run / 'state.json')  # a new runner process
        self.state['pending_report_repair']['attempts'] = 2
        with self.assertRaises(support.Paused) as error:
            self.reject_repair(7, ValueError('Missing summary'))
        self.assertEqual('PAUSED_REPEATED_FAILURE', error.exception.status)
        saved = support.read(self.run / 'state.json')
        entry = next(iter(saved['failure_history'].values()))
        self.assertEqual({'stage': 'terra', 'artifact_hash': saved['stages'][0]['source_revision'],
                          'error_class': 'ValueError'}, entry['identity'])
        self.assertEqual(3, entry['count'])
        self.assertEqual(1, entry['output_probe']['attempts'])
        self.assertTrue(entry['output_probe']['result']['provider_events']['terminal_turn'])
        self.assertTrue(Path(saved['stages'][-1]['events']).is_file())
        self.state = saved
        fourth = copy.deepcopy(saved['stages'][-1])
        fourth.pop('failure_attempt')
        fourth['iteration'] = 8
        runner.failures.record(self.state, fourth, ValueError('Missing summary'), runner.now())
        self.assertEqual(4, self.state['failure_history'][next(iter(saved['failure_history']))]['count'])
        self.assertEqual(1, self.state['failure_history'][next(iter(saved['failure_history']))]['output_probe']['attempts'])
        with patch.object(runner, 'run_role') as launch, self.assertRaises(support.Paused) as blocked:
            runner.repeated_failure_resume_guard(self.state, self.root)
        self.assertEqual('PAUSED_REPEATED_FAILURE', blocked.exception.status)
        launch.assert_not_called()
        self.assertEqual(3, support.read(self.run / 'state.json')['failure_history'][next(iter(saved['failure_history']))]['count'])
        (self.root / 'cause-fixed.txt').write_text('new artifact')
        runner.repeated_failure_resume_guard(self.state, self.root)
        self.assertTrue(runner.prepare_exhausted_execution_report_retry(self.state, self.run, self.root))

    def test_failure_identity_deduplicates_attempt_and_separates_error_classes(self):
        self.queue()
        entry = next(iter(self.state['failure_history'].values()))
        same = copy.deepcopy(self.state['stages'][-1])
        runner.failures.record(self.state, same, ValueError('different words'), runner.now())
        self.assertEqual(1, entry['count'])
        other = copy.deepcopy(same)
        other['iteration'] = 6
        runner.failures.record(self.state, other, RuntimeError('Missing summary'), runner.now())
        changed_artifact = copy.deepcopy(other)
        changed_artifact.update(iteration=7, source_revision='different-artifact')
        runner.failures.record(self.state, changed_artifact, RuntimeError('Missing summary'), runner.now())
        changed_stage = copy.deepcopy(other)
        changed_stage.update(iteration=8, stage='sol', original_stage='sol')
        runner.failures.record(self.state, changed_stage, RuntimeError('Missing summary'), runner.now())
        self.assertEqual(4, len(self.state['failure_history']))
        self.assertEqual(1, sum(row['count'] for row in self.state['failure_history'].values()
                                if row['identity']['error_class'] == 'RuntimeError'
                                and row['identity']['stage'] == 'terra'
                                and row['identity']['artifact_hash'] == same['source_revision']))

    def test_source_drift_or_changed_evidence_refuses_dispatch(self):
        pending = self.queue()
        Path(pending['original']['events']).write_text('changed')
        with patch.object(runner, 'run_role') as launch, self.assertRaises(support.Paused):
            runner.execute_report_repair(self.state, self.run, self.root)
        launch.assert_not_called()

    def test_no_retry_after_repair_limit(self):
        self.queue()['attempts'] = 2
        with patch.object(runner, 'run_role') as launch, self.assertRaises(support.Paused) as err:
            runner.execute_report_repair(self.state, self.run, self.root)
        self.assertEqual('PAUSED_REPORT_REPAIR_LIMIT', err.exception.status)
        launch.assert_not_called()

    def test_explicit_execution_retry_archives_exhausted_repairs_and_rotates_session(self):
        pending = self.queue()
        pending['attempts'] = 2
        self.state.update(status='PAUSED_REPORT_REPAIR_LIMIT', next_stage='terra')
        self.state['sessions']['terra'] = 'failed-session'
        self.assertTrue(runner.prepare_exhausted_execution_report_retry(self.state, self.run))
        self.assertNotIn('pending_report_repair', self.state)
        self.assertNotIn('terra', self.state['sessions'])
        self.assertEqual(2, self.state['report_repair_archive'][-1]['repair']['attempts'])
        self.assertEqual('failed-session', self.state['session_rotations'][-1]['old_session'])

    def test_explicit_execution_retry_accepts_rejected_final_repair(self):
        pending = self.queue()
        pending['attempts'] = 2
        self.state.update(status='PAUSED_INVALID_OUTPUT', next_stage='terra')
        self.assertTrue(runner.prepare_exhausted_execution_report_retry(self.state, self.run))
        self.assertNotIn('pending_report_repair', self.state)

    def test_abandoned_sol_repair_retries_sol_instead_of_astra_review(self):
        pending = self.queue()
        pending['attempts'] = 2
        pending['original'].update(stage='sol', role='sol')
        abandoned = {'stage': 'sol_report_repair', 'role': 'sol', 'iteration': 5,
                     'output': str(self.run / 'iterations/005/sol_report_repair-01.json'),
                     'abandoned': True}
        self.state['stages'].append(abandoned)
        self.state['recovery_context'] = {'role': 'sol', 'attempt_id': runner.attempt_id(abandoned)}
        self.state.update(status='PAUSED_REPORT_REPAIR_LIMIT', next_stage='astra_review')
        self.assertTrue(runner.prepare_exhausted_execution_report_retry(self.state, self.run))
        self.assertEqual('sol', self.state['next_stage'])
        self.assertNotIn('pending_report_repair', self.state)

    def test_execution_retry_requires_an_exhausted_clean_execution_checkpoint(self):
        pending = self.queue()
        pending['attempts'] = 2
        self.state.update(status='PAUSED_REPORT_REPAIR_LIMIT', next_stage='astra_plan')
        before = copy.deepcopy(self.state)
        self.assertFalse(runner.prepare_exhausted_execution_report_retry(self.state, self.run))
        self.assertEqual(before, self.state)
        self.state['next_stage'] = 'terra'
        self.state['active_stage'] = {'stage': 'terra'}
        before = copy.deepcopy(self.state)
        self.assertFalse(runner.prepare_exhausted_execution_report_retry(self.state, self.run))
        self.assertEqual(before, self.state)

    def test_goal_change_refuses_dispatch(self):
        self.queue()
        self.state['goal_contract'] = {'hash':'new-goal'}
        with patch.object(runner,'run_role') as launch, self.assertRaises(support.Paused):
            runner.execute_report_repair(self.state,self.run,self.root)
        launch.assert_not_called()

    def test_invalid_or_unbounded_repair_configuration_is_rejected(self):
        for value in (-1,3,True,'2'):
                self.state['settings']['report_repair']={'max_attempts':value}
                with self.subTest(value=value),self.assertRaises(ValueError):
                    runner.repair_limit(self.state)

    def test_legacy_missing_event_citation_is_requeued_only_with_exact_pins(self):
        pending = self.queue()
        original = copy.deepcopy(pending['original'])
        self.state.pop('pending_report_repair')
        original.update(rejected=True,
                        rejection_reason='Implementation evidence references a missing executed event: event:missing')
        self.state['stages'] = [original]
        self.state.update(status='PAUSED_INVALID_OUTPUT', phase='PAUSED_OR_BLOCKED',
                          stop_reason='legacy invalid evidence citation')
        self.assertTrue(runner.recover_legacy_report_repair(self.state, self.run, self.root))
        self.assertEqual('RUNNING', self.state['status'])
        self.assertEqual('REPORT_REPAIR', self.state['phase'])
        self.assertEqual(original['events'], self.state['pending_report_repair']['original']['events'])
        self.assertEqual(original['rejection_reason'], self.state['pending_report_repair']['error'])

    def test_legacy_non_evidence_invalid_output_is_not_requeued(self):
        pending = self.queue()
        original = copy.deepcopy(pending['original'])
        self.state.pop('pending_report_repair')
        original.update(rejected=True, rejection_reason='Duplicate acceptance IDs')
        self.state['stages'] = [original]
        self.state.update(status='PAUSED_INVALID_OUTPUT', phase='PAUSED_OR_BLOCKED')
        self.assertFalse(runner.recover_legacy_report_repair(self.state, self.run, self.root))

    def test_resume_queued_checkpoint_retains_attempt_count(self):
        self.queue()['attempts'] = 1
        support.atomic_json(self.run/'state.json',self.state)
        resumed = support.read(self.run/'state.json')
        with patch.object(runner,'run_role',side_effect=RuntimeError('fixture stop')):
            with self.assertRaises(RuntimeError):
                runner.execute_report_repair(resumed,self.run,self.root)
        self.assertEqual(2,support.read(self.run/'state.json')['pending_report_repair']['attempts'])

    def test_missing_completion_never_qualifies_for_repair(self):
        pending = self.queue()
        state = copy.deepcopy(self.state)
        state.pop('pending_report_repair')
        record = copy.deepcopy(pending['original'])
        Path(record['events']).write_text(json.dumps({'type':'error','message':'quota'}))
        with self.assertRaises(support.Paused):
            runner.reject_completed_stage(state,self.run,record,ValueError('malformed'))
        self.assertNotIn('pending_report_repair',state)

    def test_report_request_is_readonly_and_uses_same_role_model(self):
        self.queue()
        runner.findings_ledger.record_decision(self.state, {"status": "REWORK", "findings": [
            {"finding": "Tablet cards are too narrow", "severity": "high", "evidence": "astra-01.json"}
        ]}, {"output": "astra-01.json"})
        with patch.object(runner, 'run_role', side_effect=RuntimeError('fixture stop')) as launch:
            with self.assertRaises(RuntimeError):
                runner.execute_report_repair(self.state, self.run, self.root)
        call = launch.call_args.kwargs
        self.assertFalse(call['allow_write'])
        self.assertTrue(call['report_only'])
        self.assertEqual('read-only', call['sandbox'])
        self.assertEqual('model-terra', call['model'])
        self.assertIn('Do not redo implementation', call['prompt'])
        handoff = json.loads(call['prompt'].split('CURRENT HANDOFF DATA\n', 1)[1])
        self.assertEqual('astra', handoff['open_findings'][0]['source'])
        self.assertEqual(self.state['findings_ledger'][0]['id'], handoff['open_findings'][0]['id'])
        self.assertEqual(support.criteria_definition(self.state['acceptance_criteria']), handoff['acceptance_criteria'])
        self.assertIn('exactly one JSON object', call['prompt'])
        self.assertIn('independently executed Validator tool event', call['prompt'])

    def test_repair_handoff_contains_exact_original_command_receipts(self):
        pending = self.queue()
        event = {'type': 'item.completed', 'item': {'type': 'command_execution',
            'id': 'item_7', 'command': "/bin/zsh -lc \"printf 'quoted'\"", 'exit_code': 2}}
        Path(pending['original']['events']).write_text(json.dumps(event))
        pending['pins'][pending['original']['events']] = support.file_hash(Path(pending['original']['events']))
        with patch.object(runner, 'run_role', side_effect=RuntimeError('fixture stop')) as launch:
            with self.assertRaisesRegex(RuntimeError, 'fixture stop'):
                runner.execute_report_repair(self.state, self.run, self.root)
        data = json.loads(launch.call_args.kwargs['prompt'].split('CURRENT HANDOFF DATA\n', 1)[1])
        self.assertEqual([{'command': event['item']['command'], 'exit_code': 2,
                           'evidence_ref': 'event:item_7'}], data['original_executed_checks'])
        self.assertIn('contract_hash', data['report_identity'])

    def test_accept_uses_original_evidence_and_does_not_double_count_original(self):
        pending = self.queue()
        pending['attempts'] = 1
        original_events = pending['original']['events']
        repair = {'stage':'terra_report_repair','events':'repair-events','output':'repair-output'}
        def apply(state, stage, value, record, workspace, run):
            self.assertEqual(original_events, record['events'])
            state['stages'].append(record); state['history'].append(record)
            state['next_stage'] = 'sol'
        with patch.object(runner, 'apply_result', side_effect=apply):
            runner.accept_repaired_report(self.state, self.run, self.root, {}, repair)
        self.assertEqual(2, len(self.state['stages']))
        self.assertEqual('terra_report_repair', self.state['stages'][-1]['stage'])
        self.assertNotIn('pending_report_repair', self.state)

    def test_rejected_repair_updates_owner_before_outer_pause_save(self):
        pending = self.queue()
        pending['attempts'] = 2
        self.state['settings'].update(engine='opencode')
        self.state['settings']['roles']['terra'].update(
            engine='opencode', provider=None, model='xiaomi-token-plan-sgp/mimo-v2.6-pro', reasoning_effort='medium')
        repair = copy.deepcopy(pending['original'])
        repair.update(stage='terra_report_repair', report_only=True)
        self.state['active_stage'] = repair
        with patch.object(runner, 'apply_result', side_effect=ValueError('invalid repaired report')):
            with self.assertRaises(support.Paused):
                runner.accept_repaired_report(self.state, self.run, self.root, {}, repair)
        # main() saves this owner again when handling the pause exception.
        support.atomic_json(self.run/'state.json', self.state)
        saved = support.read(self.run/'state.json')
        self.assertNotIn('active_stage', saved)
        self.assertEqual('invalid repaired report', saved['pending_report_repair']['error'])
        self.assertTrue(saved['stages'][-1]['rejected'])
        self.assertEqual('PAUSED_INVALID_OUTPUT', saved['status'])
        self.assertEqual('Mimo Pro High', saved['reasoning_escalations'][-1]['selected']['profile'])
        self.assertNotIn('terra', saved['sessions'])

    def test_rejected_active_is_not_reconciled_again(self):
        self.state['active_stage'] = {'rejected':True}
        with patch.object(runner, 'load_stage_report') as load, self.assertRaises(support.Paused):
            runner.reconcile_active(self.state, self.run, self.root)
        load.assert_not_called()

    def test_retry_does_not_clear_uncertain_or_implementation_attempt(self):
        for stage, active in [('terra',None),('astra_discovery',{'rejected':True,'exit_code':0,'timed_out':True}),('astra_discovery',{'exit_code':0})]:
            self.state.update(status='PAUSED_INVALID_OUTPUT', next_stage=stage, active_stage=active)
            before=copy.deepcopy(self.state)
            self.assertFalse(runner.prepare_planning_retry(self.state,self.run))
            self.assertEqual(before,self.state)

    def test_uncertain_completion_and_disabled_recovery_do_not_queue(self):
        for override in ({'exit_code': 1}, {'timed_out': True}, {'source_revision': None}):
            with self.subTest(override=override), self.assertRaises(support.Paused):
                self.queue(**override)
            self.assertNotIn('pending_report_repair', self.state)


class RepairSubprocessTests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ('--engine', 'codex')

    def test_explicit_retry_replaces_legacy_rejected_planning_attempt(self):
        self.launch(['Build greeting','--no-chat'],2)
        run,state=self.saved()
        original=copy.deepcopy(state['stages'][-1])
        old_output=Path(original['output'])
        value=json.loads(old_output.read_text())
        value['contract']['accepted_assumptions'].append({'text':'Correction in conversation','basis':'user_feedback','answer_id':'invented-conversation-id'})
        old_output.write_text(json.dumps(value))
        active=copy.deepcopy(original)
        active.update(stage='astra_discovery_report_repair',report_only=True,rejected=True,rejection_reason='A feedback-based decision needs an actual saved feedback event')
        state.update(status='PAUSED_INVALID_OUTPUT',next_stage='astra_discovery',pending_questions=[],active_stage=active)
        state['pending_report_repair']={'original':original,'attempts':2,'contract_hash':state['goal_contract']['hash'],'pins':{}}
        support.atomic_json(run/'state.json',state)
        # Merely opening/continuing without explicit retry cannot replay it.
        self.launch(['--run-dir',str(run),'--no-chat'],2)
        self.assertEqual(1,len(self.saved()[1]['stages']))
        self.launch(['--run-dir',str(run),'--no-chat','--resume-paused'],2)
        _,saved=self.saved()
        self.assertEqual('WAITING_FOR_USER',saved['status'])
        self.assertNotIn('active_stage',saved)
        self.assertNotIn('pending_report_repair',saved)
        self.assertNotEqual(str(old_output),saved['stages'][-1]['output'])
        self.assertIn('invented-conversation-id',old_output.read_text())
        self.assertEqual(2,saved['report_repair_archive'][-1]['repair']['attempts'])

    def test_completed_implementation_is_not_replayed_to_fix_report(self):
        self.env.update(AUTOCODE_FIXTURE_MODE='no-human', AUTOCODE_FIXTURE_REPORT_REPAIR_STAGE='terra')
        self.launch(['Build greeting', '--chat'], 0, answers='CLI\nyes\n')
        _, state = self.saved()
        self.assertEqual('COMPLETE', state['phase'])
        self.assertEqual(1, sum(r['stage']=='terra' for r in state['stages']))
        repairs = [r for r in state['stages'] if r['stage']=='terra_report_repair']
        self.assertEqual(1, len(repairs))
        self.assertIn('read-only', repairs[0]['command'])
        self.assertNotIn('resume', repairs[0]['command'])
        self.assertEqual(1, len(state['report_repair_history']))

    def test_planning_report_repair_does_not_restart_discovery_or_approve_goal(self):
        self.env.update(AUTOCODE_FIXTURE_MODE='no-human',AUTOCODE_FIXTURE_REPORT_REPAIR_STAGE='astra_discovery')
        self.launch(['Build greeting','--no-chat'],2)
        _,state = self.saved()
        self.assertEqual('WAITING_FOR_USER',state['status'])
        self.assertEqual(['astra_discovery','astra_discovery_report_repair'],[r['stage'] for r in state['stages']])
        self.assertNotEqual('approved',state['goal_contract']['approval_status'])


if __name__ == '__main__': unittest.main()
