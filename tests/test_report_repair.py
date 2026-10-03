"""Report-only recovery tests: fixtures and mocked providers, no live model calls."""
import copy
import json
import shutil
import sys
from pathlib import Path
from unittest.mock import patch
import unittest
from . import test_autocode as base
from . import test_subprocess
from goal_fixtures import body
from .test_opencode import event, terminal

runner, support = base.runner, base.s


class RepairTests(unittest.TestCase):
    setUp = base.RetrofitTest.setUp

    def test_repair_launch_keeps_the_original_pre_upgrade_schema_bytes(self):
        self.state["next_stage"] = "astra_review"
        schema = self.run / "legacy-review.schema.json"
        schema.write_text(json.dumps(support.read(runner.SCHEMA_DIR / "v2/astra-decision.schema.json")))
        original = schema.read_bytes()
        _, record = runner.run_role(role="astra", prompt="Repair the saved report", sandbox="read-only",
            workspace=self.root, run_dir=self.run, state=self.state, schema=schema,
            model="model-astra", allow_write=False, dry_run=True, report_only=True)
        self.assertEqual(original, Path(record["schema"]).read_bytes())
        self.assertEqual(support.file_hash(schema), support.file_hash(record["schema"]))

    def timed_out_repair(self):
        pending = self.queue()
        pending['attempts'] = 1
        self.state['next_stage'] = 'terra'
        original = pending['original']
        path = self.run / 'iterations/005/terra_report_repair-01'
        record = {key: original[key] for key in ('role', 'iteration', 'schema')}
        record.update(stage='terra_report_repair', original_stage='terra', report_only=True,
                      timed_out=True, exit_code=-15, finished_at=runner.now(),
                      processes=[{'pid': 99999999, 'birth_time': 1}], duration_seconds=1)
        for key, suffix, value in [('events', '.jsonl', '{}'), ('output', '.json', '{}'),
                                  ('before_ref', '.before.json', json.dumps(support.snapshot(self.root)))]:
            target = Path(str(path) + suffix)
            target.write_text(value)
            record[key] = str(target)
        self.state['active_stage'] = record
        return pending, record

    def test_timeout_recovery_retains_pending_original_and_bindings(self):
        pending, record = self.timed_out_repair()
        expected = copy.deepcopy(pending)
        sessions = copy.deepcopy(self.state['sessions'])
        with patch.object(runner.processes, 'live_processes', return_value=[]):
            runner.reconcile_active(self.state, self.run, self.root)
        self.assertEqual(expected, self.state['pending_report_repair'])
        self.assertEqual(sessions, self.state['sessions'])
        self.assertEqual('terra', self.state['next_stage'])
        self.assertNotIn('active_stage', self.state)
        self.assertEqual('retry', self.state['stages'][-1]['decision']['action'])
        self.assertEqual(1, self.state['stages'][-1]['receipt']['evidence']['repair_attempts_used'])

    def test_timeout_recovery_fails_closed_without_changing_pending(self):
        pending, record = self.timed_out_repair()
        initial = copy.deepcopy(self.state)
        for case in ('live', 'unknown', 'terminal', 'source', 'pins', 'exhausted', 'route', 'pause', 'intervention'):
            self.state = copy.deepcopy(initial)
            record = self.state['active_stage']
            with self.subTest(case=case):
                if case == 'unknown':
                    record.pop('processes')
                if case == 'terminal':
                    Path(record['events']).write_text(json.dumps({'type': 'turn.completed'}))
                if case == 'source':
                    (self.root / 'changed.txt').write_text('changed')
                if case == 'pins':
                    self.state['pending_report_repair']['pins'] = {}
                if case == 'exhausted':
                    self.state['pending_report_repair']['attempts'] = 2
                if case == 'route':
                    self.state['next_stage'] = 'sol'
                if case == 'pause':
                    (self.run / 'pause-requested').touch()
                expected = copy.deepcopy(self.state['pending_report_repair'])
                with patch.object(runner.processes, 'live_processes', return_value=[record] if case == 'live' else []), \
                        patch.object(runner.interventions, 'pending', return_value=[{}] if case == 'intervention' else []), \
                        self.assertRaises(support.Paused):
                    runner.reconcile_active(self.state, self.run, self.root)
                self.assertEqual(expected, self.state['pending_report_repair'])
                self.assertIn('active_stage', self.state)
                Path(record['events']).write_text('{}')
                (self.root / 'changed.txt').unlink(missing_ok=True)
                (self.run / 'pause-requested').unlink(missing_ok=True)

    def test_plan_review_missing_blocking_is_preserved_as_blocking(self):
        schema = self.run / 'plan-review.schema.json'
        schema.write_text(json.dumps(runner.planning.SCHEMAS['astra_challenge']))
        concern = {'id': 'C-1', 'concern': 'The build gate is premature',
                   'evidence_refs': ['goal_contract.body'],
                   'requested_change': 'Move the gate',
                   'acceptance_test': 'Review happens after build'}
        raw = {'summary': 'Rework is needed', 'concerns': [concern, {
            **concern, 'id': 'C-2', 'blocking': False}]}
        for stage in ('astra_challenge', 'astra_challenge_report_repair'):
            with self.subTest(stage=stage):
                output = self.run / f'{stage}.json'
                record = {'stage': stage, 'engine': 'opencode',
                          'events': str(self.run / f'{stage}.jsonl'),
                          'output': str(output), 'schema': str(schema)}
                with patch.object(runner.opencode, 'final_report', return_value=raw):
                    result = runner.load_stage_report(record)
                self.assertEqual([True, False], [row['blocking'] for row in result['concerns']])
                self.assertEqual(raw, json.loads(output.with_suffix('.reported.json').read_text()))
                self.assertEqual(result, json.loads(output.read_text()))
                self.assertEqual(str(output.with_suffix('.reported.json')), record['reported_output'])

    def test_plan_review_normalization_does_not_hide_other_schema_errors(self):
        schema = self.run / 'plan-review.schema.json'
        schema.write_text(json.dumps(runner.planning.SCHEMAS['astra_challenge']))
        record = {'stage': 'astra_challenge', 'engine': 'opencode',
                  'events': str(self.run / 'plan-review.jsonl'),
                  'output': str(self.run / 'plan-review.json'), 'schema': str(schema)}
        raw = {'summary': 'Rework is needed', 'concerns': [{
            'id': 'C-1', 'concern': 'Missing requested change',
            'evidence_refs': ['goal_contract.body'], 'acceptance_test': 'Gate passes'}]}
        with patch.object(runner.opencode, 'final_report', return_value=raw):
            with self.assertRaises(ValueError):
                runner.load_stage_report(record)
        self.assertEqual(raw, support.read(Path(record['output'])))

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

    def stage_record(self, *, report='{}', event_rows=None, stage='terra', iteration=5, **overrides):
        path = self.run / 'iterations' / f'{iteration:03d}' / f'{stage}-01'
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {'role': 'terra', 'stage': stage, 'iteration': iteration, 'exit_code': 0,
                  'duration_seconds': 1, 'source_revision': support.snapshot(self.root)['revision']}
        if event_rows is None:
            event_rows = [{'type': 'thread.started', 'thread_id': 't-session'}, {'type': 'turn.completed'}]
        for key, suffix, value in [('output', '.json', report), ('events', '.jsonl',
                '\n'.join(json.dumps(row) for row in event_rows)),
                ('before_ref', '.before.json', '{}'), ('after_ref', '.after.json', '{}'), ('schema', '.schema.json', '{}')]:
            p = Path(str(path) + suffix)
            if value is not None:
                p.write_text(value)
            record[key] = str(p)
        record.update(overrides)
        return record

    def queue(self, error=None, **overrides):
        self.state['settings']['report_repair'] = {'max_attempts': 2}
        # ResolverRuntimeTests borrows this helper without inheriting RepairTests.
        record = RepairTests.stage_record(self, **overrides)
        with self.assertRaises(runner.ReportRepairQueued):
            runner.reject_completed_stage(self.state, self.run, record, error or ValueError('Missing summary'))
        return self.state['pending_report_repair']

    def reject_repair(self, iteration, error, *, report=None):
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
        if report is not None:
            Path(record['output']).write_text(report)
        return runner.reject_completed_stage(self.state, self.run, record, error)

    def repair_request(self):
        with patch.object(runner, 'run_role', side_effect=RuntimeError('fixture stop')) as launch:
            with self.assertRaisesRegex(RuntimeError, 'fixture stop'):
                runner.execute_report_repair(self.state, self.run, self.root)
        launch.assert_called_once()
        return launch.call_args.kwargs

    FORMAT_ERROR = RuntimeError('OpenCode final message is not a JSON report; inspect the saved raw events')

    def test_format_error_resumes_the_session_with_a_tiny_correction_first(self):
        self.state['next_stage'] = 'terra'
        self.queue(error=self.FORMAT_ERROR, engine='opencode')
        with patch.object(runner, 'account_stage') as accounted, \
                patch.object(runner, 'accept_repaired_report') as accepted, \
                patch.object(runner, 'run_role', return_value=({}, {'stage': 'terra_report_repair'})) as launch:
            runner.execute_report_repair(self.state, self.run, self.root)
        kwargs = launch.call_args.kwargs
        self.assertEqual('t-session', kwargs['resume_session'])
        self.assertTrue(kwargs['report_only'])
        self.assertLess(len(kwargs['prompt']), 800)
        self.assertIn('could not be parsed as the report', kwargs['prompt'])
        self.assertNotIn('CURRENT HANDOFF DATA', kwargs['prompt'])
        accounted.assert_called_once()
        accepted.assert_called_once()
        # The correction borrows no repair budget: the full repair remains available.
        self.assertEqual(0, self.state['pending_report_repair']['attempts'])

    def test_failed_correction_falls_back_to_the_unchanged_full_repair(self):
        self.state['next_stage'] = 'terra'
        self.queue(error=self.FORMAT_ERROR, engine='opencode')
        with patch.object(runner, 'run_role', side_effect=RuntimeError('correction also malformed')):
            with self.assertRaisesRegex(RuntimeError, 'correction also malformed'):
                runner.execute_report_repair(self.state, self.run, self.root)
        self.assertEqual(0, self.state['pending_report_repair']['attempts'])
        prompt = self.repair_request()['prompt']
        self.assertIn('CURRENT HANDOFF DATA', prompt)
        self.assertEqual(1, self.state['pending_report_repair']['attempts'])

    def test_without_a_session_thread_the_full_repair_runs_directly(self):
        self.state['next_stage'] = 'terra'
        self.queue(error=self.FORMAT_ERROR, engine='opencode', event_rows=[{'type': 'turn.completed'}])
        self.assertIn('CURRENT HANDOFF DATA', self.repair_request()['prompt'])

    def test_non_format_errors_never_resume_the_session(self):
        self.state['next_stage'] = 'terra'
        self.queue(error=ValueError('$.requirements[48]: unexpected fields'), engine='opencode')
        kwargs = self.repair_request()
        self.assertIsNone(kwargs.get('resume_session'))
        self.assertIn('CURRENT HANDOFF DATA', kwargs['prompt'])

    def test_requirements_repair_receives_authoritative_user_sources(self):
        self.state['task'] = 'Build a planner.'
        self.state['brief_feedback'] = [{'text': 'Declare the test file in M2.'}]
        self.state['current_task'] = {'requirements': ['Fix an existing test race.']}
        self.state['settings']['roles']['requirements'] = {'model': 'requirements-model'}
        self.state['next_stage'] = 'requirements_gather'
        self.queue(stage='requirements_gather', role='requirements')
        prompt = self.repair_request()['prompt']
        data = json.loads(prompt.split('CURRENT HANDOFF DATA\n', 1)[1])
        self.assertEqual(['Build a planner.', 'Declare the test file in M2.'], data['source_texts'])
        self.assertNotIn('Fix an existing test race.', data['source_texts'])
        self.assertIn('current Builder task and approved contract are inherited obligations', prompt)

    def test_planner_repair_receives_the_requirements_its_trace_must_cover(self):
        # Without the rows a repair of a trace with a missing requirement_id guessed "R1" (2026-10-02).
        self.state['requirements_handoff'] = {'report': {'requirements': [
            {'id': 'R1', 'text': 'Print a greeting', 'source_quote': 'Print a greeting'}]}, 'output': 'r.json'}
        self.state['settings']['roles']['glm'] = {'model': 'planner-model'}
        self.state['next_stage'] = 'astra_discovery'
        self.queue(ValueError('$.requirement_trace[0]: missing requirement_id'), stage='astra_discovery', role='glm')
        data = json.loads(self.repair_request()['prompt'].split('CURRENT HANDOFF DATA\n', 1)[1])
        self.assertEqual(['R1'], [row['requirement_id'] for row in data['requirement_trace_rows']])

    def test_finalizer_repair_receives_current_concerns_and_saved_human_context(self):
        self.state.update(next_stage='astra_finalize', requirements_handoff={'report': {
            'requirements': [{'id': 'R1', 'source_quote': 'Reject blank names'}], 'open_questions': []}},
            brief_feedback=[{'id': 'feedback-1', 'text': 'Preserve valid-name output'}],
            planning={'reports': {
                'astra_challenge': {'report': {'concerns': [{'id': 'C1', 'concern': 'Keep the guard'}]}},
                'glm_revise': {'report': {'responses': [{'concern_id': 'C1'}]}}}})
        self.queue(stage='astra_finalize', role='astra')

        prompt = self.repair_request()['prompt']
        data = json.loads(prompt.split('CURRENT HANDOFF DATA\n', 1)[1])
        context = data['clarification_context']

        self.assertEqual(self.state['requirements_handoff'], context['requirements_handoff'])
        self.assertEqual(self.state['planning']['reports'], context['planning_exchange'])
        self.assertEqual(self.state['brief_feedback'], context['saved_feedback'])
        self.assertIn('preserving that task inside contract.initial_task', prompt)
        self.assertIn('original_report is historical planning context', prompt)
        self.assertNotIn('original_report is also supplied, it is the immutable execution-history baseline', prompt)

    def test_builder_repair_retains_immutable_execution_baseline_instruction(self):
        self.queue()

        prompt = self.repair_request()['prompt']
        data = json.loads(prompt.split('CURRENT HANDOFF DATA\n', 1)[1])

        self.assertIn('original_report is also supplied, it is the immutable execution-history baseline', prompt)
        self.assertNotIn('clarification_context', data)
        self.assertNotIn(runner.planning.PROGRESSIVE_POLICY, prompt)

    def test_nonfinal_planning_repair_distinguishes_drafts_from_execution_history(self):
        before = copy.deepcopy(self.state)
        requirements = [{'id': 'R1', 'text': 'Build a planner', 'source_quote': 'Build a planner.'}]
        for stage, role in (('requirements_gather', 'requirements'),
                            ('astra_discovery', 'astra'), ('glm_revise', 'astra')):
            with self.subTest(stage=stage):
                self.state.clear()
                self.state.update(copy.deepcopy(before))
                self.state.update(next_stage=stage, requirements_handoff={'report': {
                    'requirements': requirements, 'open_questions': []}})
                self.state['settings']['roles']['requirements'] = {'model': 'requirements-model'}
                self.queue(stage=stage, role=role)

                prompt = self.repair_request()['prompt']
                data = json.loads(prompt.split('CURRENT HANDOFF DATA\n', 1)[1])

                self.assertNotIn('clarification_context', data)
                self.assertEqual(requirements if stage == 'requirements_gather' else None,
                                 data['previous_requirements'])
                self.assertNotIn('Planning report repair:', prompt)
                self.assertIn('original_report is historical planning context', prompt)
                self.assertNotIn('original_report is also supplied, it is the immutable execution-history baseline', prompt)

    def test_planning_repair_repeats_semantic_rules_without_changing_the_saved_schema(self):
        before = copy.deepcopy(self.state)
        for stage in ('astra_discovery', 'glm_revise', 'astra_finalize'):
            with self.subTest(stage=stage):
                self.state = copy.deepcopy(before)
                self.state['next_stage'] = stage
                schema = self.run / f'{stage}.schema.json'
                schema.write_text(json.dumps(runner.planning.SCHEMAS[stage]))
                schema_bytes = schema.read_bytes()
                error = 'progressive planning requires one whole-product milestone'
                rejected = {'summary': 'Retain this rejected draft'}
                self.queue(stage=stage, role='astra', schema=str(schema),
                           error=ValueError(error), report=json.dumps(rejected))

                request = self.repair_request()
                prompt = request['prompt']
                data = json.loads(prompt.split('CURRENT HANDOFF DATA\n', 1)[1])

                self.assertIn(runner.planning.PROGRESSIVE_POLICY, prompt)
                self.assertIn('exactly one whole-product milestone', prompt)
                self.assertIn('retain the protected text and add a blocking decision', prompt)
                if stage == 'glm_revise':
                    self.assertIn('Each responses[].evidence_refs must be nonempty', prompt)
                self.assertEqual(error, data['error'])
                self.assertEqual(rejected, data['rejected_report']['content'])
                self.assertEqual(schema_bytes, request['schema'].read_bytes())
                self.assertFalse(request['allow_write'])
                self.assertTrue(request['report_only'])

    def test_planning_repair_respects_legacy_and_alternate_saved_schemas(self):
        before = copy.deepcopy(self.state)
        for fields in ({'contract': runner.goals.BODY_SCHEMA},
                       {'progressive_proposal': runner.planning.PROGRESSIVE_PROPOSAL},
                       {'accepted': {'type': 'boolean'}}):
            with self.subTest(fields=list(fields)):
                self.state = copy.deepcopy(before)
                self.state['next_stage'] = 'glm_revise'
                schema = self.run / 'saved.schema.json'
                schema.write_text(json.dumps({'type': 'object', 'properties': fields}))
                self.queue(stage='glm_revise', role='astra', schema=str(schema))
                prompt = self.repair_request()['prompt']
                self.assertNotIn(runner.planning.PROGRESSIVE_POLICY, prompt)

    def assert_repair_blocked(self, status='PAUSED_REPORT_REPAIR_INPUT'):
        attempts = self.state['pending_report_repair']['attempts']
        with patch.object(runner, 'run_role') as launch, self.assertRaises(support.Paused) as error:
            runner.execute_report_repair(self.state, self.run, self.root)
        self.assertEqual(status, error.exception.status)
        launch.assert_not_called()
        self.assertEqual(attempts, self.state['pending_report_repair']['attempts'])
        self.assertEqual(attempts, support.read(self.run / 'state.json')['pending_report_repair']['attempts'])

    def test_native_schema_rejections_preserve_reports_before_validation_and_archive_pins(self):
        cases = [
            ('astra_challenge', {'summary': 'Review', 'concerns': [{
                'id': 'P1', 'concern': 'Failure is not handled', 'evidence_refs': ['app.py:10'],
                'requested_change': 'Handle failure', 'acceptance_test': 'Failure test passes',
                'blocking': True}]}, '$.concerns[0]: missing requested_change'),
            ('astra_discovery', {'contract': body(), 'summary': 'Draft', 'code_refs': [],
                'alternatives': [], 'uncertainties': [], 'contract_changes': [],
                'conflict_resolutions': [], 'requirement_trace': []}, '$: missing summary'),
        ]
        initial = copy.deepcopy(self.state)
        validate = support.validate_schema
        for stage, report, expected_error in cases:
            with self.subTest(stage=stage):
                self.state = copy.deepcopy(initial)
                self.state['settings']['report_repair'] = {'max_attempts': 2}
                self.state['next_stage'] = stage
                schema = runner.planning.SCHEMAS[stage]
                validate(report, schema)
                if stage == 'astra_challenge':
                    del report['concerns'][0]['requested_change']
                else:
                    # A missing provenance list (code_refs) is now defaulted without repair
                    # (tests/test_bugfix_workflow.py); a missing summary still must be rejected.
                    del report['summary']
                text = json.dumps(report)
                record = self.stage_record(report=None, role='astra', stage=stage, engine='opencode',
                                           event_rows=[event('text', text=text), terminal()])
                Path(record['schema']).write_text(json.dumps(schema))
                output = Path(record['output'])
                response = output.with_suffix('.response.txt')
                self.assertFalse(output.exists())

                def validate_saved(value, saved_schema, where='$'):
                    self.assertEqual(report, support.read(output))
                    self.assertEqual(text, response.read_text())
                    self.assertEqual(str(response), record['response_text'])
                    return validate(value, saved_schema, where)

                with patch.object(support, 'validate_schema', side_effect=validate_saved):
                    with self.assertRaises(ValueError) as error:
                        runner.load_stage_report(record, self.root)
                self.assertEqual(expected_error, str(error.exception))
                artifacts = {key: (Path(record[key]), Path(record[key]).read_bytes())
                             for key in ('output', 'response_text', 'events', 'before_ref', 'after_ref')}
                with self.assertRaises(runner.ReportRepairQueued):
                    runner.reject_completed_stage(self.state, self.run, record, error.exception)
                self.state = support.read(self.run / 'state.json')
                pending = self.state['pending_report_repair']
                for key, (old_path, contents) in artifacts.items():
                    archived = Path(pending['original'][key])
                    self.assertNotEqual(old_path, archived)
                    self.assertFalse(old_path.exists())
                    self.assertTrue(archived.parent.name.startswith('archived-'))
                    self.assertEqual(contents, archived.read_bytes())
                    self.assertEqual(support.file_hash(archived), pending['pins'][str(archived)])
                    self.assertEqual(str(archived), pending['original']['archived_paths'][str(old_path)])
                source = runner.repair_report_source(pending['original'])
                self.assertEqual({'path': pending['original']['output'], 'format': 'json', 'content': report,
                                  'bytes': Path(pending['original']['output']).stat().st_size, 'truncated': False,
                                  'sha256': pending['pins'][pending['original']['output']]}, source)
                data = json.loads(self.repair_request()['prompt'].split('CURRENT HANDOFF DATA\n', 1)[1])
                self.assertEqual(expected_error, data['error'])
                self.assertEqual(source, data['rejected_report'])
                self.assertIsNone(data.get('original_report'))
                self.assertEqual(pending['original']['archived_paths'], data['archived_paths'])

    def test_malformed_terminal_text_is_standalone_and_excludes_transport_noise(self):
        text = '{"summary":"Keep this entire rejected draft", "results":["exit=7"],'
        noise = 'TOOL_OUTPUT_ONLY_' + 'x' * (runner.REPAIR_HANDOFF_BYTES * 2)
        rows = [event('text', id='earlier', messageID='old', text='{"summary":"EARLIER_MESSAGE_ONLY"}'),
                event('tool_use', tool='bash', state={'status': 'completed',
                    'input': {'command': 'failed-check'}, 'metadata': {'exit': 7}, 'output': noise}),
                event('text', text='SUPERSEDED_TERMINAL_PART'), event('text', text=text), terminal()]
        record = self.stage_record(report=None, engine='opencode', event_rows=rows,
                                   command=['OLD_COMMAND_ONLY_' + noise], metrics={'old_prompt': noise})
        output = Path(record['output'])
        response = output.with_suffix('.response.txt')
        with self.assertRaisesRegex(RuntimeError, 'not a JSON report') as error:
            runner.load_stage_report(record, self.root)
        self.assertFalse(output.exists())
        self.assertEqual(text, response.read_text())
        self.assertEqual(str(response), record['response_text'])
        self.state['settings']['report_repair'] = {'max_attempts': 2}
        with self.assertRaises(runner.ReportRepairQueued):
            runner.reject_completed_stage(self.state, self.run, record, error.exception)
        self.state = support.read(self.run / 'state.json')
        pending = self.state['pending_report_repair']
        archived = Path(pending['original']['response_text'])
        self.assertFalse(response.exists())
        self.assertEqual(text, archived.read_text())
        self.assertEqual(support.file_hash(archived), pending['pins'][str(archived)])
        self.assertGreater(Path(pending['original']['events']).stat().st_size, runner.REPAIR_HANDOFF_BYTES)
        # The same-session format correction runs first for this error (see
        # FormatCorrectionTests); the full handoff asserted here is the fallback attempt.
        pending['attempts'] = 1
        prompt = self.repair_request()['prompt']
        data = json.loads(prompt.split('CURRENT HANDOFF DATA\n', 1)[1])
        self.assertEqual(str(error.exception), data['error'])
        self.assertEqual({'path': str(archived), 'sha256': support.file_hash(archived),
                          'bytes': len(text.encode('utf-8')), 'truncated': False,
                          'format': 'text', 'content': text}, data['rejected_report'])
        self.assertIsNone(data.get('original_report'))
        self.assertEqual(str(archived), data['archived_paths'][str(response)])
        self.assertNotIn(str(output), data['archived_paths'])
        self.assertEqual([{'command': 'failed-check', 'exit_code': 7,
                           'evidence_ref': 'event:prt_tool_use'}], data['original_executed_checks'])
        for excluded in ('TOOL_OUTPUT_ONLY_', 'EARLIER_MESSAGE_ONLY', 'SUPERSEDED_TERMINAL_PART', 'OLD_COMMAND_ONLY_'):
            self.assertNotIn(excluded, prompt)
        self.assertNotIn('metrics', data['original'])
        self.assertNotIn('command', data['original'])
        self.assertNotIn('rejection_reason', data['original'])
        self.assertLess(len(prompt.encode('utf-8')), runner.REPAIR_HANDOFF_BYTES)

    def test_second_repair_pairs_latest_error_with_latest_draft_and_preserves_original(self):
        original = {'commands_run': ['failed-test'], 'results': ['exit=1'], 'changed_files': ['app.py'],
                    'remaining_risks': ['not verified'], 'user_request': {'kind': 'permission', 'decision_needed': 'Allow?'}}
        first_error = '$: missing summary'
        pending = self.queue(error=ValueError(first_error), report=json.dumps(original))
        baseline = copy.deepcopy(pending['original'])
        first = json.loads(self.repair_request()['prompt'].split('CURRENT HANDOFF DATA\n', 1)[1])
        self.assertEqual(first_error, first['error'])
        self.assertEqual(original, first['rejected_report']['content'])
        self.assertIsNone(first.get('original_report'))
        latest = {**original, 'summary': 'First repaired draft', 'evidence_refs': ['event:missing']}
        latest_error = 'Implementation evidence references a missing executed event: event:missing\nKeep "exit=1".'
        with self.assertRaises(runner.ReportRepairQueued):
            self.reject_repair(6, ValueError(latest_error), report=json.dumps(latest))
        self.state = support.read(self.run / 'state.json')
        pending = self.state['pending_report_repair']
        self.assertEqual(baseline, pending['original'])
        self.assertEqual(latest_error, pending['error'])
        rejected = pending['latest_rejected']
        self.assertEqual('terra_report_repair', rejected['stage'])
        self.assertNotEqual(baseline['output'], rejected['output'])
        self.assertEqual(latest, support.read(rejected['output']))
        for key in ('output', 'events', 'schema'):
            self.assertEqual(support.file_hash(rejected[key]), pending['pins'][rejected[key]])
        second = json.loads(self.repair_request()['prompt'].split('CURRENT HANDOFF DATA\n', 1)[1])
        self.assertEqual(latest_error, second['error'])
        self.assertEqual(runner.repair_report_source(rejected), second['rejected_report'])
        self.assertEqual(first['rejected_report'], second['original_report'])
        self.assertEqual(first['original'], second['original'])
        self.assertEqual({**baseline['archived_paths'], **rejected['archived_paths']}, second['archived_paths'])
        self.assertEqual(2, support.read(self.run / 'state.json')['pending_report_repair']['attempts'])
        runner.assert_repair_preserves_builder_history(baseline, latest)
        with self.assertRaisesRegex(ValueError, 'changed recorded Builder history: results'):
            runner.assert_repair_preserves_builder_history(baseline, {**latest, 'results': ['PASS']})

    def test_original_and_latest_output_tampering_blocks_dispatch_without_charging(self):
        pending = self.queue(report='{"summary":"original draft"}')
        pending['attempts'] = 1
        with self.assertRaises(runner.ReportRepairQueued):
            self.reject_repair(6, ValueError('Latest failure'), report='{"summary":"latest draft"}')
        self.state = support.read(self.run / 'state.json')
        for key in ('original', 'latest_rejected'):
            path = Path(self.state['pending_report_repair'][key]['output'])
            contents = path.read_bytes()
            for change in ('rewrite', 'remove'):
                with self.subTest(source=key, change=change):
                    try:
                        if change == 'rewrite':
                            path.write_text('{"summary":"tampered draft"}')
                        else:
                            path.unlink()
                        self.assert_repair_blocked('PAUSED_STALE_VALIDATION')
                    finally:
                        path.write_bytes(contents)

    def test_legacy_latest_draft_is_reassociated_from_history_or_mismatch_blocks(self):
        initial = copy.deepcopy(self.state)
        for mismatch in (False, True):
            with self.subTest(mismatch=mismatch):
                self.state = copy.deepcopy(initial)
                original = {'summary': 'Original draft', 'results': ['exit=1']}
                latest = {**original, 'summary': 'Latest legacy draft'}
                pending = self.queue(report=json.dumps(original))
                original_pins = copy.deepcopy(pending['pins'])
                pending['attempts'] = 1
                error = 'Exact latest legacy rejection'
                with self.assertRaises(runner.ReportRepairQueued):
                    self.reject_repair(5, ValueError(error), report=json.dumps(latest))
                rejected = pending.pop('latest_rejected')
                pending['pins'] = original_pins
                if mismatch:
                    pending['error'] = 'Error not belonging to the latest saved draft'
                support.atomic_json(self.run / 'state.json', self.state)
                self.state = support.read(self.run / 'state.json')
                self.assertNotIn('latest_rejected', self.state['pending_report_repair'])
                if mismatch:
                    self.assert_repair_blocked('PAUSED_STALE_VALIDATION')
                else:
                    data = json.loads(self.repair_request()['prompt'].split('CURRENT HANDOFF DATA\n', 1)[1])
                    self.assertEqual(error, data['error'])
                    self.assertEqual(latest, data['rejected_report']['content'])
                    self.assertEqual(rejected['output'], data['rejected_report']['path'])
                    self.assertEqual(original, data['original_report']['content'])
                    saved = support.read(self.run / 'state.json')['pending_report_repair']
                    self.assertEqual(rejected, saved['latest_rejected'])
                    self.assertEqual(2, saved['attempts'])
                    for key in ('output', 'events', 'schema'):
                        self.assertEqual(support.file_hash(rejected[key]), saved['pins'][rejected[key]])

    def test_rejected_repair_cannot_replace_original_shared_schema_pin(self):
        pending = self.queue()
        schema = Path(pending['original']['schema'])
        original_hash = pending['pins'][str(schema)]
        pending['attempts'] = 1
        schema.write_text('{"type":"object","required":["invented-field"]}')
        with self.assertRaises(runner.ReportRepairQueued):
            self.reject_repair(6, ValueError('Rejected against altered schema'))
        self.state = support.read(self.run / 'state.json')
        pending = self.state['pending_report_repair']
        self.assertEqual(str(schema), pending['latest_rejected']['schema'])
        self.assertNotEqual(original_hash, support.file_hash(schema))
        self.assertEqual(original_hash, pending['pins'][str(schema)])
        self.assert_repair_blocked('PAUSED_STALE_VALIDATION')

    def test_report_byte_limit_accepts_complete_boundary_and_rejects_overflow(self):
        self.assertEqual(128 * 1024, runner.REPAIR_REPORT_BYTES)
        initial = copy.deepcopy(self.state)
        for format_, prefix, suffix in [('json', '{"summary":"', '"}'), ('text', 'malformed: ', ' :end')]:
            with self.subTest(format=format_):
                self.state = copy.deepcopy(initial)
                padding = runner.REPAIR_REPORT_BYTES - len((prefix + suffix).encode('utf-8'))
                text = prefix + '\u00e9' * (padding // 2) + 'x' * (padding % 2) + suffix
                record = self.stage_record(report=text if format_ == 'json' else None)
                key = 'output' if format_ == 'json' else 'response_text'
                if format_ == 'text':
                    record[key] = str(Path(record['output']).with_suffix('.response.txt'))
                    Path(record[key]).write_text(text)
                self.assertLess(len(text), runner.REPAIR_REPORT_BYTES)
                self.assertEqual(runner.REPAIR_REPORT_BYTES, Path(record[key]).stat().st_size)
                source = runner.repair_report_source(record)
                self.assertEqual(format_, source['format'])
                self.assertEqual(json.loads(text) if format_ == 'json' else text, source['content'])
                self.assertEqual(support.file_hash(record[key]), source['sha256'])
                self.assertEqual(runner.REPAIR_REPORT_BYTES, source['bytes'])
                self.assertIs(False, source['truncated'])
                if format_ == 'json':
                    pending = self.queue(report=text + '\n')
                else:
                    Path(record[key]).write_text(text + '\n')
                    pending = self.queue(report=None, response_text=record[key])
                path = Path(pending['original'][key])
                self.assertEqual(runner.REPAIR_REPORT_BYTES + 1, path.stat().st_size)
                self.assert_repair_blocked()
                self.assertEqual((text + '\n').encode('utf-8'), path.read_bytes())
                self.assertEqual(pending['pins'][str(path)], support.file_hash(path))

    def test_latest_rejected_report_overflow_does_not_charge_second_attempt(self):
        self.queue()['attempts'] = 1
        oversized = 'x' * (runner.REPAIR_REPORT_BYTES + 1)
        with self.assertRaises(runner.ReportRepairQueued):
            self.reject_repair(6, ValueError('Malformed large repair'), report=oversized)
        pending = self.state['pending_report_repair']
        self.assert_repair_blocked()
        self.assertEqual(oversized, Path(pending['latest_rejected']['output']).read_text())
        self.assertEqual('{}', Path(pending['original']['output']).read_text())

    def test_combined_report_handoff_overflow_preserves_both_sources_and_attempt(self):
        original = json.dumps({'summary': 'original-' + 'x' * (runner.REPAIR_REPORT_BYTES - 100)})
        latest = json.dumps({'summary': 'latest-' + 'y' * (runner.REPAIR_REPORT_BYTES - 100)})
        self.queue(report=original)['attempts'] = 1
        with self.assertRaises(runner.ReportRepairQueued):
            self.reject_repair(6, ValueError('Latest draft rejected'), report=latest)
        pending = self.state['pending_report_repair']
        for key in ('original', 'latest_rejected'):
            path = Path(pending[key]['output'])
            self.assertLess(path.stat().st_size, runner.REPAIR_REPORT_BYTES)
            self.assertEqual(support.read(path), runner.repair_report_source(pending[key])['content'])
        self.assert_repair_blocked()
        self.assertEqual(original, Path(pending['original']['output']).read_text())
        self.assertEqual(latest, Path(pending['latest_rejected']['output']).read_text())

    def test_handoff_byte_limit_includes_instructions_and_all_metadata(self):
        self.assertEqual(256 * 1024, runner.REPAIR_HANDOFF_BYTES)
        self.queue()
        initial = copy.deepcopy(self.state)
        prompt = self.repair_request()['prompt']
        padding = runner.REPAIR_HANDOFF_BYTES - len(prompt.encode('utf-8'))
        self.assertGreater(padding, 0)
        self.state = copy.deepcopy(initial)
        self.state['acceptance_criteria'][0]['criterion'] += 'x' * padding
        support.atomic_json(self.run / 'state.json', self.state)
        boundary_prompt = self.repair_request()['prompt']
        self.assertEqual(runner.REPAIR_HANDOFF_BYTES, len(boundary_prompt.encode('utf-8')))
        data = json.loads(boundary_prompt.split('CURRENT HANDOFF DATA\n', 1)[1])
        self.assertEqual(self.state['acceptance_criteria'][0]['criterion'], data['acceptance_criteria'][0]['criterion'])
        self.state = copy.deepcopy(initial)
        self.state['acceptance_criteria'][0]['criterion'] += 'x' * (padding + 1)
        support.atomic_json(self.run / 'state.json', self.state)
        self.assert_repair_blocked()
        self.assertEqual(len(initial['acceptance_criteria'][0]['criterion']) + padding + 1,
                         len(self.state['acceptance_criteria'][0]['criterion']))

    def test_provider_decorated_handoff_overflow_never_publishes_or_launches_and_refunds_attempt(self):
        self.state['settings']['engine'] = 'opencode'
        self.queue()
        initial = copy.deepcopy(self.state)
        prompt = self.repair_request()['prompt']
        self.state = copy.deepcopy(initial)
        padding = runner.REPAIR_HANDOFF_BYTES - len(prompt.encode('utf-8')) - 1
        self.state['acceptance_criteria'][0]['criterion'] += 'x' * padding
        support.atomic_json(self.run / 'state.json', self.state)
        snapshot = support.snapshot(self.root)
        decorate = runner.opencode.prompt_for_schema
        with patch.object(runner.opencode, 'launch', return_value=(['fixture-provider'], {}, {})), \
             patch.object(runner.opencode, 'prompt_for_schema', wraps=decorate) as schema_prompt, \
             patch.object(support, 'snapshot', return_value=snapshot), \
             patch.object(runner.processes, 'process_table', return_value={}), \
             patch.object(runner.subprocess, 'Popen', side_effect=AssertionError('No provider may launch')) as launch:
            with self.assertRaises(support.Paused) as error:
                runner.execute_report_repair(self.state, self.run, self.root)
        self.assertEqual('PAUSED_REPORT_REPAIR_INPUT', error.exception.status)
        schema_prompt.assert_called_once()
        args = schema_prompt.call_args.args
        self.assertEqual(runner.REPAIR_HANDOFF_BYTES - 1, len(args[0].encode('utf-8')))
        self.assertGreater(len(decorate(*args).encode('utf-8')), runner.REPAIR_HANDOFF_BYTES)
        launch.assert_not_called()
        self.assertFalse(Path(args[2]).with_suffix('.prompt.md').exists())
        self.assertNotIn('active_stage', self.state)
        saved = support.read(self.run / 'state.json')
        self.assertNotIn('active_stage', saved)
        self.assertEqual(0, self.state['pending_report_repair']['attempts'])
        self.assertEqual(0, saved['pending_report_repair']['attempts'])

    def test_oversized_command_receipts_pause_instead_of_truncating_or_launching(self):
        command = 'exact-command-' + 'x' * runner.REPAIR_HANDOFF_BYTES
        rows = [{'type': 'thread.started', 'thread_id': 't-session'},
                {'type': 'item.completed', 'item': {'type': 'command_execution', 'id': 'check',
                    'command': command, 'exit_code': 1}}, {'type': 'turn.completed'}]
        pending = self.queue(event_rows=rows)
        path = Path(pending['original']['events'])
        contents = path.read_bytes()
        self.assert_repair_blocked()
        self.assertEqual(contents, path.read_bytes())
        self.assertEqual(command, support.events(path)[1]['item']['command'])

    def test_persisted_legacy_missing_native_report_is_recovered_locally_from_pinned_events(self):
        initial = copy.deepcopy(self.state)
        for text in ('{"summary":"legacy draft", "results":["exit=1"]}', '{"summary":"legacy malformed draft",'):
            with self.subTest(text=text):
                self.state = copy.deepcopy(initial)
                rows = [event('text', id='old', messageID='old', text='EARLIER_LEGACY_MESSAGE'),
                        event('tool_use', tool='bash', state={'status': 'completed',
                            'input': {'command': 'legacy-check'}, 'metadata': {'exit': 1},
                            'output': 'LEGACY_TOOL_NOISE_' + 'x' * runner.REPAIR_HANDOFF_BYTES}),
                        event('text', text=text), terminal()]
                error = ValueError('Exact saved legacy validation error')
                self.queue(error=error, report=None, engine='opencode', event_rows=rows)
                self.state = support.read(self.run / 'state.json')
                pending = self.state['pending_report_repair']
                original = pending['original']
                output = Path(original['output'])
                response = output.with_suffix('.response.txt')
                self.assertFalse(output.exists())
                self.assertFalse(response.exists())
                event_hash = pending['pins'][original['events']]
                with patch.object(runner.opencode, 'final_report', wraps=runner.opencode.final_report) as extract:
                    prompt = self.repair_request()['prompt']
                extract.assert_called_once_with(original['events'], recover_wrapped=False, response_path=response)
                data = json.loads(prompt.split('CURRENT HANDOFF DATA\n', 1)[1])
                self.assertEqual(str(error), data['error'])
                self.assertEqual(text, response.read_text())
                self.assertEqual(str(response), original['response_text'])
                if text.endswith('}'):
                    self.assertEqual(json.loads(text), support.read(output))
                    self.assertEqual('json', data['rejected_report']['format'])
                    self.assertEqual(json.loads(text), data['rejected_report']['content'])
                    source = output
                else:
                    self.assertFalse(output.exists())
                    self.assertEqual('text', data['rejected_report']['format'])
                    self.assertEqual(text, data['rejected_report']['content'])
                    source = response
                saved = support.read(self.run / 'state.json')['pending_report_repair']
                self.assertEqual(str(source), data['rejected_report']['path'])
                self.assertEqual(support.file_hash(source), data['rejected_report']['sha256'])
                self.assertEqual(source.stat().st_size, data['rejected_report']['bytes'])
                self.assertIs(False, data['rejected_report']['truncated'])
                self.assertEqual(support.file_hash(source), saved['pins'][str(source)])
                self.assertEqual(str(response), saved['original']['response_text'])
                self.assertEqual(event_hash, support.file_hash(original['events']))
                self.assertEqual(1, saved['attempts'])
                self.assertIn('do not search raw JSONL or old prompts', prompt)
                self.assertNotIn('EARLIER_LEGACY_MESSAGE', prompt)
                self.assertNotIn('LEGACY_TOOL_NOISE_', prompt)

    def test_legacy_event_tampering_blocks_local_extraction_and_dispatch(self):
        pending = self.queue(report=None, engine='opencode',
                             event_rows=[event('text', text='{"summary":"legacy draft"}'), terminal()])
        Path(pending['original']['events']).write_text('tampered events')
        self.state = support.read(self.run / 'state.json')
        with patch.object(runner.opencode, 'final_report') as extract:
            self.assert_repair_blocked('PAUSED_STALE_VALIDATION')
        extract.assert_not_called()

    def test_legacy_response_only_recovery_keeps_builder_history_immutable(self):
        original = {'commands_run': ['failed-test'], 'results': ['exit=1'], 'changed_files': ['app.py'],
                    'remaining_risks': ['not verified'], 'user_request': {'kind': 'permission', 'decision_needed': 'Allow?'}}
        text = json.dumps(original)
        rows = [event('tool_use', tool='bash', state={'status': 'completed', 'input': {'command': 'failed-test'},
                    'metadata': {'exit': 1}, 'output': 'FAIL'}), event('text', text=text), terminal()]
        pending = self.queue(report=None, engine='opencode', event_rows=rows)
        output = Path(pending['original']['output'])
        response = output.with_suffix('.response.txt')
        # A recovery process died after saving terminal text but before parsed JSON.
        response.write_text(text)
        self.state = support.read(self.run / 'state.json')
        self.assertFalse(output.exists())
        data = json.loads(self.repair_request()['prompt'].split('CURRENT HANDOFF DATA\n', 1)[1])
        self.assertEqual(original, data['rejected_report']['content'])
        baseline = self.state['pending_report_repair']['original']
        runner.assert_repair_preserves_builder_history(baseline, original)
        for field, replacement in [('results', ['PASS']), ('remaining_risks', []),
                                   ('user_request', {'kind': 'none'})]:
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'changed'):
                runner.assert_repair_preserves_builder_history(baseline, {**original, field: replacement})

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

    def test_exact_evidence_mismatch_can_request_fresh_sol_validation(self):
        self.state['next_stage'] = 'sol'
        error = ValueError('Check is not supported by an exact executed Validator event')
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
        original_schema = Path(self.state['pending_report_repair']['original']['schema'])
        copied_schema = self.run / 'same-validator-schema.json'
        copied_schema.write_text('{"not": "the validator schema"}')
        self.state['stages'][-1]['schema'] = str(copied_schema)
        with self.assertRaises(ValueError):
            runner.retry_format_failed_report(self.state, self.run, self.root, selected)
        copied_schema.write_bytes(original_schema.read_bytes())
        self.state['stages'].append({'stage': 'investigate_stuck_report_repair',
                                     'original_stage': 'investigate_stuck',
                                     'report_only': True, 'rejected': True,
                                     'output': str(self.run / 'investigate_stuck_report_repair-02.json')})
        runner.retry_format_failed_report(self.state, self.run, self.root, selected)
        self.assertEqual('sol', self.state['next_stage'])
        self.assertNotIn('pending_report_repair', self.state)
        self.assertEqual('report_retry_after_format_fix', self.state['user_events'][-1]['kind'])

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
        for value in (-1,3,6,7,True,False,'2',2.0,None):
                self.state['settings']['report_repair']={'max_attempts':value}
                with self.subTest(value=value),self.assertRaises(ValueError):
                    runner.repair_limit(self.state)

    def test_missing_repair_settings_do_not_enable_automatic_spend(self):
        self.state['settings'].pop('report_repair', None)
        self.assertEqual(0, runner.repair_limit(self.state))
        self.state['settings']['report_repair'] = {}
        self.assertEqual(0, runner.repair_limit(self.state))
        for limit in (0, 1, 2):
            self.state['settings']['report_repair'] = {'max_attempts': limit}
            self.assertEqual(limit, runner.repair_limit(self.state))

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

    def test_an_investigator_repair_repeats_where_its_probe_finds_the_cited_files(self):
        # Live run 2026-09-29: both repairs of a rejected probe guessed the cited file's path.
        self.queue(role='astra', stage='investigate_stuck', error=ValueError('diagnosed causes\' probes did not exit 0'))
        self.state['next_stage'] = 'investigate_stuck'
        with patch.object(runner, 'run_role', side_effect=RuntimeError('fixture stop')) as launch:
            with self.assertRaises(RuntimeError):
                runner.execute_report_repair(self.state, self.run, self.root)
        self.assertIn('each at run/<its file name>', launch.call_args.kwargs['prompt'].split('CURRENT HANDOFF DATA\n')[0])
        self.assertEqual('', runner.jobs.repair_rules('terra'))

    def test_a_validator_pass_without_checks_gets_a_report_repair(self):
        # Live run 2026-09-29: the Validator ran its check but returned "checks": [], and the run paused.
        with self.assertRaisesRegex(ValueError, 'lacks successful executed checks'):
            runner.autopilot.apply_review_result(runner, self.state, 'sol', {
                'verdict': 'PASS', 'checks': [], 'criterion_results': [], 'evidence_refs': []},
                {'events': str(self.run / 'none.jsonl'), 'source_revision': 'r', 'output': 'sol-01.json'},
                self.root, self.run)

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

    # Superseded: the rejected_report repair flow replaced the original_report
    # extraction path; the no-replay and tampering invariants live in
    # test_terminal_error_is_durably_queued_without_replaying_implementation and the
    # tampering tests.

    def test_repair_does_not_replace_unparseable_events_with_saved_report(self):
        pending = self.queue()
        original = pending['original']
        original['engine'] = 'opencode'
        Path(original['output']).write_text(json.dumps({'verdict': 'PASS'}))
        extracted = runner.original_report_for_repair(original)
        self.assertIsNone(extracted['report'])
        self.assertTrue(extracted['extraction_error'])

    def test_accept_uses_original_evidence_and_does_not_double_count_original(self):
        pending = self.queue()
        pending['attempts'] = 1
        original_events = pending['original']['events']
        output = self.run / 'repair-output.json'
        output.write_text('{}')
        repair = {'stage':'terra_report_repair','events':'repair-events','output':str(output)}
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
            engine='opencode', provider=None, model='openai/gpt-6-sol', reasoning_effort='medium')
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
        self.assertEqual('GPT-6 Sol High', saved['reasoning_escalations'][-1]['selected']['profile'])
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

    def timeout_provider(self, mode='first', stage='astra_discovery'):
        provider = self.root / 'fixture-bin/codex'
        real = provider.with_name('real_codex.py')
        provider.rename(real)
        provider.write_text(f'''#!{sys.executable}
import json, os, subprocess, sys, time
from pathlib import Path
if sys.argv[1:] == ['login', 'status']:
    raise SystemExit(subprocess.call([sys.executable, {str(real)!r}, *sys.argv[1:]]))
prompt = sys.stdin.read()
data = json.loads(prompt.split('CURRENT HANDOFF DATA\\n', 1)[1])
output = Path(sys.argv[sys.argv.index('-o') + 1])
if data.get('report_repair') and ({mode!r} == 'all' or output.stem.endswith('-01')):
    if {mode!r} == 'pause':
        Path(data['state_file']).with_name('pause-requested').touch()
    if {mode!r} == 'changed':
        Path('unexpected.txt').write_text('changed source')
    print(json.dumps({{'type': 'thread.started', 'thread_id': 'timeout-fixture'}}), flush=True)
    time.sleep(30)
raise SystemExit(subprocess.run([sys.executable, {str(real)!r}, *sys.argv[1:]], input=prompt, text=True).returncode)
''')
        provider.chmod(0o755)
        self.env.update(AUTOCODE_FIXTURE_MODE='no-human', AUTOCODE_FIXTURE_REPORT_REPAIR_STAGE=stage)

    # Superseded: the rejected_report repair flow replaced the original_report
    # extraction path; the no-replay and tampering invariants live in
    # test_terminal_error_is_durably_queued_without_replaying_implementation and the
    # tampering tests.

    # Superseded: the rejected_report repair flow replaced the original_report
    # extraction path; the no-replay and tampering invariants live in
    # test_terminal_error_is_durably_queued_without_replaying_implementation and the
    # tampering tests.

    def test_cli_changed_source_escalates_without_replaying_repair(self):
        self.timeout_provider('changed')
        self.launch(['Build greeting', '--no-chat', '--max-stage-seconds', '2'], 2)
        _, state = self.saved()
        self.assertEqual('WAITING_FOR_USER', state['status'])
        self.assertEqual('operational_exhaustion', runner.resolver_human.current(state)['scope'])
        self.assertEqual(1, state['pending_report_repair']['attempts'])
        self.assertTrue(state['active_stage']['timed_out'])
        self.assertEqual('changed source', (self.project / 'unexpected.txt').read_text())

    # Superseded: the rejected_report repair flow replaced the original_report
    # extraction path; the no-replay and tampering invariants live in
    # test_terminal_error_is_durably_queued_without_replaying_implementation and the
    # tampering tests.

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
        _,opened=self.saved()
        self.assertEqual(2,len(opened['stages']),(opened['status'],[r['stage'] for r in opened['stages']]))
        self.launch(['--run-dir',str(run),'--no-chat','--resume-paused'],2)
        _,saved=self.saved()
        self.assertEqual('WAITING_FOR_USER',saved['status'])
        self.assertNotIn('active_stage',saved)
        self.assertNotIn('pending_report_repair',saved)
        self.assertNotEqual(str(old_output),saved['stages'][-1]['output'])
        self.assertIn('invented-conversation-id',old_output.read_text())
        self.assertEqual(2, saved['report_repair_archive'][-1]['repair']['attempts'])

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
        self.assertEqual(['recognize_workflow','astra_discovery','astra_discovery_report_repair'],[r['stage'] for r in state['stages']])
        self.assertNotEqual('approved',state['goal_contract']['approval_status'])


if __name__ == '__main__': unittest.main()
