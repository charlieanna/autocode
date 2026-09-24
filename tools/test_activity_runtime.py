"""Offline runner coverage for activity timeouts, durable status, and recovery."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import textwrap
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from . import test_goals
import autocode as runner
import autocode_goals as goals
import autocode_milestones as milestones
import autocode_support as support
from goal_fixtures import envelope


class ActivityRuntimeTests(unittest.TestCase):
    setUp = test_goals.GoalTests.setUp
    draft = test_goals.GoalTests.draft
    approve = test_goals.GoalTests.approve
    decision = test_goals.GoalTests.decision

    def args(self, **selected):
        values = dict(engine='codex', astra_model=None, terra_model=None, sol_model=None,
                      reasoning_effort=None, headroom=None, context_soft_tokens=None,
                      rotate_after_input_tokens=None, legacy_iteration_ceiling=None,
                      max_iterations=None, max_seconds=None, max_reported_tokens=None,
                      no_progress_limit=None, max_stage_seconds=None, max_idle_seconds=None,
                      max_tool_seconds=None)
        return SimpleNamespace(**{**values, **selected})

    def start_task(self):
        self.approve()
        self.state['settings']['milestone_checkpoints'] = copy.deepcopy(milestones.DEFAULTS)
        goals.assign_task(self.state, self.decision(), support.snapshot(self.root))
        self.state.update(next_stage='terra', status='RUNNING', phase='EXECUTING')

    def invoke(self, *args):
        output, error = io.StringIO(), io.StringIO()
        with patch.object(sys, 'argv', ['autocode', '--workspace', str(self.root),
                                       '--run-dir', str(self.run), *args]), \
             patch.object(runner, 'run_role', side_effect=AssertionError('No provider may launch')), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            code = runner.main()
        return code, output.getvalue(), error.getvalue()

    def test_new_run_limits_separate_idle_and_tool_from_disabled_stage_cap(self):
        fresh = {'workspace': str(self.root), 'iteration': 1}
        with patch.object(support, 'local_settings', return_value=self.local):
            configured = runner.configure(self.args(), fresh)
        self.assertEqual(0, configured['limits']['stage_timeout_seconds'])
        self.assertEqual(300, configured['limits']['idle_timeout_seconds'])
        self.assertEqual(1800, configured['limits']['tool_timeout_seconds'])

    def test_saved_hard_cap_preserved_while_missing_activity_limits_are_added(self):
        for saved_cap in (0, 300, 1800):
            with self.subTest(saved_cap=saved_cap):
                self.state['settings']['limits']['stage_timeout_seconds'] = saved_cap
                original = copy.deepcopy(self.state)
                configured = runner.configure(self.args(), self.state)
                self.assertEqual(saved_cap, configured['limits']['stage_timeout_seconds'])
                self.assertEqual(300, configured['limits']['idle_timeout_seconds'])
                self.assertEqual(1800, configured['limits']['tool_timeout_seconds'])
                self.assertEqual(original['settings']['roles'],
                    {role: configured['roles'][role] for role in original['settings']['roles']})
                self.assertIn('completion', configured['roles'])
                self.assertEqual(original, self.state, 'configure must return a new configuration')

    def test_saved_activity_limits_are_not_reset_on_resume(self):
        self.state['settings']['limits'].update(stage_timeout_seconds=4200,
                                               idle_timeout_seconds=600,
                                               tool_timeout_seconds=2400)
        configured = runner.configure(self.args(), self.state)
        for key in ('stage_timeout_seconds', 'idle_timeout_seconds', 'tool_timeout_seconds'):
            self.assertEqual(self.state['settings']['limits'][key], configured['limits'][key])

    def test_explicit_flags_can_disable_or_change_each_limit_independently(self):
        self.state['settings']['limits'].update(stage_timeout_seconds=300,
                                               idle_timeout_seconds=300,
                                               tool_timeout_seconds=1800)
        configured = runner.configure(self.args(max_stage_seconds=0, max_idle_seconds=90,
                                               max_tool_seconds=0), self.state)
        self.assertEqual(0, configured['limits']['stage_timeout_seconds'])
        self.assertEqual(90, configured['limits']['idle_timeout_seconds'])
        self.assertEqual(0, configured['limits']['tool_timeout_seconds'])

    def test_cli_rejects_negative_activity_limits_before_touching_state(self):
        support.atomic_json(self.run / 'state.json', self.state)
        original = (self.run / 'state.json').read_bytes()
        for flag in ('--max-stage-seconds', '--max-idle-seconds', '--max-tool-seconds'):
            with self.subTest(flag=flag):
                error = io.StringIO()
                with contextlib.redirect_stderr(error), patch.object(sys, 'argv', [
                        'autocode', '--workspace', str(self.root), '--run-dir', str(self.run),
                        flag, '-1']), self.assertRaises(SystemExit) as caught:
                    runner.main()
                self.assertEqual(2, caught.exception.code)
                self.assertIn(flag + ' must be nonnegative', error.getvalue())
                self.assertEqual(original, (self.run / 'state.json').read_bytes())

    def test_status_shows_checkpointed_activity_without_migrating_or_launching(self):
        observed = {'activity': 'running_tool', 'command': 'python3 -m unittest',
                    'observed_at': '2026-09-20T01:02:03Z', 'elapsed_seconds': 600,
                    'stage_limit_seconds': 1800, 'tool_elapsed_seconds': 240}
        self.state['settings']['limits']['stage_timeout_seconds'] = 1800
        self.state['active_stage'] = {
            'iteration': 1, 'role': 'terra', 'stage': 'terra',
            'output': str(self.run / 'iterations/001/terra-01.json'), 'activity': observed}
        support.atomic_json(self.run / 'state.json', self.state)
        original = (self.run / 'state.json').read_bytes()
        code, output, _ = self.invoke('--status')
        self.assertEqual(0, code)
        displayed = json.loads(output)
        for key, value in observed.items():
            self.assertEqual(value, displayed['active_stage']['activity'][key])
        self.assertNotIn('idle_timeout_seconds', displayed['settings']['limits'])
        self.assertNotIn('tool_timeout_seconds', displayed['settings']['limits'])
        self.assertEqual(original, (self.run / 'state.json').read_bytes())

    def test_timeout_reason_and_kind_are_durable_for_each_deadline(self):
        self.start_task()
        self.state['settings']['limits'].update(stage_timeout_seconds=20,
                                               idle_timeout_seconds=5,
                                               tool_timeout_seconds=10)

        class Child:
            pid = 987654321

            def __init__(child, command, **kwargs):
                kwargs['stdout'].write('{"type":"thread.started","thread_id":"fixture"}\n')
                kwargs['stdout'].flush()

        for kind, reason in [('idle', 'No meaningful provider activity for 5 seconds'),
                             ('tool', 'Tool python3 -m unittest exceeded 10 seconds'),
                             ('stage', 'Provider stage exceeded explicit 20-second limit')]:
            with self.subTest(kind=kind):
                state = copy.deepcopy(self.state)
                run = self.run / kind
                observed = []

                def wait(child, hard_limit, checkpoint, *, activity, activity_checkpoint, startup_grace):
                    self.assertEqual(20, hard_limit)
                    self.assertEqual(5, startup_grace)
                    checkpoint([])
                    activity.timeout = {'kind': kind, 'reason': reason}
                    detail = {**activity.snapshot(),
                              'activity': 'stalled' if kind == 'idle' else 'timed_out',
                              'timeout_kind': kind, 'timeout_reason': reason}
                    activity_checkpoint(detail)
                    observed.append(support.read(run / 'state.json')['active_stage']['activity'])
                    return -15, True

                with patch.object(runner.subprocess, 'Popen', Child), \
                     patch.object(support, 'snapshot', return_value={'head': 'h', 'files': {}, 'revision': 'r'}), \
                     patch.object(runner.processes, 'process_table', return_value={}), \
                     patch.object(runner.processes, 'wait_for_stage', side_effect=wait), \
                     contextlib.redirect_stdout(io.StringIO()), self.assertRaises(support.Paused) as caught:
                    runner.run_role(role='terra', prompt='Finish the bounded greeting task',
                        sandbox='workspace-write', workspace=self.root, run_dir=run,
                        state=state, schema=runner.SCHEMA_DIR / 'v2/terra-report.schema.json',
                        model='fixture-terra', allow_write=True, dry_run=False)
                self.assertEqual('PAUSED_PROVIDER_TIMEOUT', caught.exception.status)
                self.assertIn(reason, str(caught.exception))
                active = support.read(run / 'state.json')['active_stage']
                self.assertEqual(kind, active['timeout_kind'])
                self.assertEqual(reason, active['timeout_reason'])
                self.assertTrue(active['accounted'])
                self.assertTrue(active['timed_out'])
                self.assertEqual(kind, observed[0]['timeout_kind'])
                self.assertIn('observed_at', observed[0])
                self.assertGreaterEqual(observed[0]['elapsed_seconds'], 0)
                self.assertEqual(20, observed[0]['stage_limit_seconds'])

    def interrupted_attempt(self, *, terminal=False):
        before = support.snapshot(self.root)
        base = self.run / 'iterations/001/terra-01'
        base.parent.mkdir(parents=True, exist_ok=True)
        support.atomic_json(base.with_suffix('.before.json'), before)
        rows = [{'type': 'thread.started', 'thread_id': 'expired-session'}]
        if terminal:
            rows.append({'type': 'turn.completed'})
        base.with_suffix('.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
        source = self.root / 'greet.py'
        source.write_text("print('retained partial greeting')\n")
        record = {'role': 'terra', 'stage': 'terra', 'iteration': 1,
                  'output': str(base.with_suffix('.json')), 'events': str(base.with_suffix('.jsonl')),
                  'before_ref': str(base.with_suffix('.before.json')), 'duration_seconds': 10,
                  'exit_code': 0 if terminal else -15, 'timed_out': True, 'processes': [],
                  'timeout_kind': 'idle', 'timeout_reason': 'No meaningful activity for 5 seconds'}
        self.state['active_stage'] = record
        self.state['sessions']['terra'] = 'expired-session'
        return source, record

    def test_partial_timeout_archives_once_preserves_work_and_routes_to_astra(self):
        self.start_task()
        self.state['settings']['limits'].update(tool_timeout_seconds=1800, idle_timeout_seconds=600,
                                                stage_timeout_seconds=5400)
        source, record = self.interrupted_attempt()
        original_source = source.read_bytes()
        original_goal = copy.deepcopy(self.state['goal_contract'])
        with patch.object(runner, 'run_role', side_effect=AssertionError('Recovery must not replay the writer')):
            self.assertTrue(runner.automatically_recover_timed_out_stage(
                self.state, self.run, self.root, support.Paused('PAUSED_PROVIDER_TIMEOUT', 'idle')))
            self.assertFalse(runner.automatically_recover_timed_out_stage(
                self.state, self.run, self.root, support.Paused('PAUSED_PROVIDER_TIMEOUT', 'idle')))
        self.assertEqual(original_source, source.read_bytes())
        self.assertEqual(original_goal, self.state['goal_contract'])
        self.assertEqual('astra_review', self.state['next_stage'])
        self.assertNotIn('active_stage', self.state)
        self.assertNotIn('terra', self.state['sessions'])
        self.assertEqual(1, len(self.state['automatic_timeout_recoveries']))
        recovery = self.state['recovery_context']
        self.assertEqual(self.state['current_task']['id'], recovery['task_id'])
        self.assertEqual(1800, recovery['execution_limits']['tool_timeout_seconds'])
        self.assertEqual(600, recovery['execution_limits']['idle_timeout_seconds'])
        self.assertIn('split long tool work into bounded calls', recovery['instruction'])
        archived = self.state['stages'][-1]
        self.assertEqual('idle', archived['timeout_kind'])
        self.assertTrue(Path(archived['events']).is_file())
        self.assertEqual(['greet.py'], archived['changed_files'])

    def test_uncertain_recovery_requires_a_durable_timeout_not_just_an_error_label(self):
        self.start_task()
        _, record = self.interrupted_attempt()
        record['timed_out'] = False
        original = copy.deepcopy(self.state)
        self.assertFalse(runner.automatically_recover_timed_out_stage(
            self.state, self.run, self.root, support.Paused('PAUSED_PROVIDER_UNCERTAIN', 'no final')))
        self.assertEqual(original, self.state)
        record['timed_out'] = True
        self.assertFalse(runner.automatically_recover_timed_out_stage(
            self.state, self.run, self.root, support.Paused('PAUSED_BILLING_ROUTE', 'not authorized')))
        decision = self.decision('VALIDATE')
        decision['next_task']['kind'] = 'validate'
        runner.apply_result(self.state, 'astra_review', decision, {'output': 'review.json'}, self.root, self.run)
        self.assertEqual('sol', self.state['next_stage'], 'Retained work still needs independent verification')
        self.assertFalse(support.completion_ready(self.state, self.decision('TASK_COMPLETE'),
                                                 support.snapshot(self.root)))

    def test_timeout_racing_a_completed_report_reconciles_to_sol_without_replay(self):
        self.start_task()
        source, record = self.interrupted_attempt(terminal=True)
        evidence = self.run / 'retained-check.log'
        evidence.write_text('Implementation finished; Sol must verify the result.\n')
        report = {**envelope(self.state), 'summary': 'Retained completed greeting',
                  'changed_files': ['greet.py'], 'commands_run': [], 'results': ['Implementation saved'],
                  'remaining_risks': ['Independent verification pending'], 'evidence_refs': [str(evidence)],
                  'addressed_requirements': ['Greet a valid name'],
                  'untested_behavior': ['Invalid input'], 'recommended_checks': ['Execute both CLI cases']}
        schema = self.run / 'terra.schema.json'
        support.atomic_json(schema, goals.role_schema(
            support.read(runner.SCHEMA_DIR / 'v2/terra-report.schema.json'), 'terra'))
        record['schema'] = str(schema)
        support.atomic_json(Path(record['output']), report)
        original_source = source.read_bytes()
        with patch.object(runner, 'run_role', side_effect=AssertionError('A completed writer must not replay')):
            self.assertFalse(runner.automatically_recover_timed_out_stage(
                self.state, self.run, self.root, support.Paused('PAUSED_PROVIDER_TIMEOUT', 'idle')))
            runner.reconcile_active(self.state, self.run, self.root)
            runner.reconcile_active(self.state, self.run, self.root)
        self.assertEqual(original_source, source.read_bytes())
        self.assertEqual('sol', self.state['next_stage'])
        self.assertNotIn('active_stage', self.state)
        self.assertEqual(1, sum(row.get('stage') == 'terra' for row in self.state['stages']))
        self.assertFalse(self.state.get('automatic_timeout_recoveries'))

    def test_timeout_does_not_recover_while_a_recorded_worker_is_live(self):
        self.start_task()
        self.interrupted_attempt()
        self.state['active_stage']['processes'] = [{'pid': 12345, 'started': 'fixture'}]
        original = copy.deepcopy(self.state)
        with patch.object(runner.processes, 'live_processes', return_value=[{'pid': 12345}]):
            self.assertFalse(runner.automatically_recover_timed_out_stage(
                self.state, self.run, self.root, support.Paused('PAUSED_PROVIDER_TIMEOUT', 'idle')))
        self.assertEqual(original, self.state)

    def test_repeated_astra_timeout_recovery_stops_before_another_provider_launch(self):
        self.start_task()
        self.state['next_stage'] = 'astra_review'
        for attempt in range(3):
            runner.timeout_recovery_guard(self.state)
            _, record = self.interrupted_attempt()
            record.update(role='astra', stage='astra_review', iteration=attempt + 1)
            self.assertTrue(runner.automatically_recover_timed_out_stage(
                self.state, self.run, self.root, support.Paused('PAUSED_PROVIDER_TIMEOUT', 'idle')))
            self.assertEqual(attempt + 1, self.state['consecutive_timeout_recoveries'])
            self.assertEqual('astra_review', self.state['next_stage'])
        with patch.object(runner.subprocess, 'Popen', side_effect=AssertionError('Recovery budget must stop launch')), \
             self.assertRaises(support.Paused) as caught:
            runner.run_role(role='astra', prompt='Inspect retained work', sandbox='read-only',
                workspace=self.root, run_dir=self.run, state=self.state,
                schema=runner.SCHEMA_DIR / 'v2/astra-decision.schema.json',
                model='fixture-astra', allow_write=False, dry_run=False)
        self.assertEqual('PAUSED_TIMEOUT_RECOVERY', caught.exception.status)
        self.assertEqual(3, len(self.state['automatic_timeout_recoveries']))
        self.assertNotIn('active_stage', self.state)

    def test_only_an_accepted_result_resets_consecutive_timeout_recoveries(self):
        self.start_task()
        self.state['consecutive_timeout_recoveries'] = 2
        evidence = self.run / 'implementation-check.log'
        evidence.write_text('Retained greeting implementation.\n')
        events = self.run / 'completed-terra.jsonl'
        events.write_text('{"type":"turn.completed"}\n')
        report = {**envelope(self.state), 'summary': 'Implementation ready for Sol',
                  'changed_files': ['greet.py'], 'commands_run': [], 'results': ['Ready'],
                  'remaining_risks': [], 'evidence_refs': [str(evidence)]}
        record = {'role': 'terra', 'stage': 'terra', 'events': str(events),
                  'output': str(self.run / 'completed-terra.json'), 'changed_files': ['greet.py'],
                  'after_ref': str(self.run / 'completed-terra.after.json'),
                  'source_revision': support.snapshot(self.root)['revision']}
        with self.assertRaises(support.Paused) as caught:
            runner.apply_result(self.state, 'terra', {**report, 'contract_hash': 'stale'},
                                record, self.root, self.run)
        self.assertEqual('PAUSED_STALE_GOAL', caught.exception.status)
        self.assertEqual(2, self.state['consecutive_timeout_recoveries'])
        runner.apply_result(self.state, 'terra', report, record, self.root, self.run)
        self.assertEqual(0, self.state.get('consecutive_timeout_recoveries', 0))
        self.assertEqual('sol', self.state['next_stage'])

    def test_zero_no_progress_budget_does_not_disable_aggregate_recovery_ceiling(self):
        self.state['settings']['limits']['no_progress_batches'] = 0
        self.state['consecutive_timeout_recoveries'] = 0
        self.state['automatic_recoveries_since_resume'] = 3
        with self.assertRaises(support.Paused):
            runner.timeout_recovery_guard(self.state)

    def test_legacy_recent_failures_seed_the_aggregate_recovery_ceiling(self):
        self.state['settings']['limits']['no_progress_batches'] = 0
        self.state.update(consecutive_timeout_recoveries=1, no_progress_batches=3,
                          automatic_timeout_recoveries=[{}, {}],
                          automatic_permission_recoveries=[{}])
        self.assertEqual(3, runner.recovery_count(self.state))
        with self.assertRaises(support.Paused):
            runner.timeout_recovery_guard(self.state)

    def test_legacy_recovered_history_does_not_exhaust_a_new_resume(self):
        self.state.update(consecutive_timeout_recoveries=0, no_progress_batches=0,
                          automatic_timeout_recoveries=[{}, {}, {}],
                          automatic_recoveries_since_resume=0)
        self.assertEqual(0, runner.recovery_count(self.state))
        runner.timeout_recovery_guard(self.state)

    def test_explicit_resume_reopens_recovery_budget_without_erasing_history(self):
        self.start_task()
        history = [{'attempt_id': f'001/astra_review-{n:02d}', 'timeout_kind': 'idle'}
                   for n in range(1, 4)]
        self.state.update(status='PAUSED_TIMEOUT_RECOVERY', next_stage='astra_review',
                          consecutive_timeout_recoveries=3, no_progress_batches=3,
                          automatic_timeout_recoveries=copy.deepcopy(history), automatic_recoveries_since_resume=3)
        calls = []

        def inspect(**kwargs):
            calls.append(kwargs['role'])
            self.assertEqual(0, kwargs['state'].get('consecutive_timeout_recoveries', 0))
            self.assertEqual(0, runner.recovery_count(kwargs['state']))
            self.assertEqual(history, kwargs['state']['automatic_timeout_recoveries'])
            raise support.Paused('PAUSED_TEST', 'Offline dispatch inspected')

        self.assertEqual(2, test_goals.GoalTests.invoke(self, role=inspect))
        self.assertEqual([], calls, 'An ordinary invocation must retain the exhausted pause')
        self.assertEqual(3, self.state['consecutive_timeout_recoveries'])
        self.assertEqual(2, test_goals.GoalTests.invoke(self, '--resume-paused', role=inspect))
        self.assertEqual(['astra'], calls)
        self.assertEqual(history, self.state['automatic_timeout_recoveries'])

    def fake_provider(self, script):
        """Install a workspace-local offline executable, never the user's Codex."""
        directory = self.root / '.autocode' / 'fixture-bin'
        directory.mkdir(parents=True, exist_ok=True)
        binary = directory / 'codex'
        binary.write_text(f'#!{sys.executable}\n' + textwrap.dedent(script))
        binary.chmod(0o755)
        return patch.dict(os.environ, {'PATH': str(directory) + os.pathsep + os.environ['PATH']})

    def real_terra(self):
        schema = self.run / 'fixture-terra.schema.json'
        support.atomic_json(schema, goals.role_schema(
            support.read(runner.SCHEMA_DIR / 'v2/terra-report.schema.json'), 'terra'))
        return runner.run_role(role='terra', prompt='Complete the greeting fixture',
            sandbox='workspace-write', workspace=self.root, run_dir=self.run, state=self.state,
            schema=schema, model='offline-fixture', allow_write=True, dry_run=False)

    def test_real_quiet_tool_survives_idle_deadline_and_completed_writer_routes_sol(self):
        self.start_task()
        self.state['settings']['limits'].update(stage_timeout_seconds=4,
                                               idle_timeout_seconds=.4,
                                               tool_timeout_seconds=2)
        report = {**envelope(self.state), 'summary': 'Greeting implementation tested',
                  'changed_files': ['greet.py'], 'commands_run': [], 'results': ['Hello, fixture'],
                  'remaining_risks': [], 'evidence_refs': ['event:quiet-check'],
                  'addressed_requirements': ['Greet a valid name'], 'untested_behavior': ['Invalid input'],
                  'recommended_checks': ['Sol executes valid and invalid CLI cases']}
        script = f'''
            import json, shlex, subprocess, sys
            from pathlib import Path
            def event(value):
                print(json.dumps(value), flush=True)
            sys.stdin.read()
            event({{'type': 'thread.started', 'thread_id': 'offline-tool-session'}})
            Path('greet.py').write_text("print('Hello, fixture')\\n")
            command = [sys.executable, '-c', "import time; time.sleep(.85); exec(open('greet.py').read())"]
            text = shlex.join(command)
            event({{'type': 'item.started', 'item': {{'id': 'quiet-check', 'type': 'command_execution',
                'command': text, 'status': 'in_progress'}}}})
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            event({{'type': 'item.completed', 'item': {{'id': 'quiet-check', 'type': 'command_execution',
                'command': text, 'exit_code': result.returncode, 'aggregated_output': result.stdout}}}})
            report = {report!r}
            report['commands_run'] = [text]
            Path(sys.argv[sys.argv.index('-o') + 1]).write_text(json.dumps(report))
            event({{'type': 'turn.completed', 'usage': {{'input_tokens': 1, 'output_tokens': 1}}}})
        '''
        with self.fake_provider(script), contextlib.redirect_stdout(io.StringIO()):
            value, record = self.real_terra()
        self.assertGreaterEqual(record['duration_seconds'], .8)
        self.assertFalse(record['timed_out'])
        self.assertEqual(0, record['exit_code'])
        command = next(row['item'] for row in support.events(record['events'])
                       if row.get('type') == 'item.completed')
        self.assertEqual(0, command['exit_code'])
        self.assertEqual('Hello, fixture\n', command['aggregated_output'])
        self.assertEqual([], runner.processes.live_processes(record.get('processes', [])))
        runner.apply_result(self.state, 'terra', value, record, self.root, self.run)
        self.assertEqual('sol', self.state['next_stage'])
        self.assertEqual('Hello, fixture', (self.root / 'greet.py').read_text().split("'")[1])
        self.assertNotIn('active_stage', self.state)

    def test_real_stalled_writer_preserves_partial_work_and_recovers_once_to_astra(self):
        self.start_task()
        self.state['settings']['limits'].update(stage_timeout_seconds=4,
                                               idle_timeout_seconds=.4,
                                               tool_timeout_seconds=2)
        script = '''
            import json, sys, time
            from pathlib import Path
            sys.stdin.read()
            Path('greet.py').write_text("print('retained partial greeting')\\n")
            print(json.dumps({'type': 'thread.started', 'thread_id': 'offline-stalled-session'}), flush=True)
            time.sleep(30)
        '''
        with self.fake_provider(script), contextlib.redirect_stdout(io.StringIO()), \
             self.assertRaises(support.Paused) as caught:
            self.real_terra()
        self.assertEqual('PAUSED_PROVIDER_TIMEOUT', caught.exception.status)
        record = support.read(self.run / 'state.json')['active_stage']
        self.assertEqual('idle', record['timeout_kind'])
        self.assertTrue(record['timeout_reason'])
        self.assertTrue(record['timed_out'])
        self.assertTrue(record['accounted'])
        self.assertNotEqual(0, record['exit_code'])
        self.assertEqual([], runner.processes.live_processes(record.get('processes', [])))
        source = self.root / 'greet.py'
        original = source.read_bytes()
        self.assertTrue(runner.automatically_recover_timed_out_stage(
            self.state, self.run, self.root, caught.exception))
        self.assertFalse(runner.automatically_recover_timed_out_stage(
            self.state, self.run, self.root, caught.exception))
        self.assertEqual(original, source.read_bytes())
        self.assertEqual('astra_review', self.state['next_stage'])
        self.assertEqual(1, len(self.state['automatic_timeout_recoveries']))
        self.assertEqual('idle', self.state['stages'][-1]['timeout_kind'])
        self.assertTrue(Path(self.state['stages'][-1]['events']).is_file())
        self.assertNotIn('active_stage', self.state)


if __name__ == '__main__':
    unittest.main()
