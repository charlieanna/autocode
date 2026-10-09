"""Derive only missing check metadata from verified execution evidence (the latest run of a command)."""
import contextlib
import copy
import io
import json
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from .test_autocode import runner
from .test_autocode import s as support


class ValidationMetadataTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name).resolve()
        self.run = self.workspace / '.autocode/runs/fixture'
        self.run.mkdir(parents=True)
        self.log = self.run / 'sol.jsonl'
        self.context = {'attempt': str(self.run / 'sol.json'), 'nonce': 'fixture', 'source_revision': 'source'}

    def events(self, *items):
        self.log.write_text('\n'.join(json.dumps({'type': 'item.completed', 'item': item}) for item in items))

    def event(self, **overrides):
        return {'type': 'command_execution', 'id': 'actual', 'command': 'python test.py',
                'exit_code': 0, **overrides}

    def receipt(self, exit_code=0):
        raw = self.run / 'check.log'
        raw.write_text('complete output')
        value = {'command': ['python', 'test.py'], 'exit_code': exit_code,
                 'full_output': str(raw), 'full_output_sha256': support.file_hash(raw),
                 'capture_context': self.context}
        path = self.run / 'check.json'
        path.write_text(json.dumps(value))
        return path, value

    def test_exact_event_or_unique_command_derives_real_nonzero_exit(self):
        self.events(self.event(exit_code=7))
        for ref in ('event:actual', 'event:conversation-id'):
            checks = [{'command': 'python test.py', 'evidence_ref': ref}]
            support.verify_checks(checks, self.workspace, self.log)
            self.assertEqual([{'command': 'python test.py', 'evidence_ref': 'event:actual', 'exit_code': 7}], checks)

    def test_exact_event_canonicalizes_workspace_wrapper_only(self):
        executed = f'cd {self.workspace} && python test.py 2>&1'
        self.events(self.event(command=executed))
        check = {'command': 'python test.py', 'evidence_ref': 'event:actual', 'exit_code': 0}
        support.verify_checks([check], self.workspace, self.log)
        self.assertEqual(executed, check['command'])
        for command in ('python other.py', 'python test.py; echo pass'):
            with self.subTest(command=command), self.assertRaises(ValueError):
                support.verify_checks([{'command': command, 'evidence_ref': 'event:actual', 'exit_code': 0}],
                                      self.workspace, self.log)
        self.events(self.event(command=f'cd {self.workspace.parent} && python test.py 2>&1'))
        with self.assertRaises(ValueError):
            support.verify_checks([{'command': 'python test.py', 'evidence_ref': 'event:actual', 'exit_code': 0}],
                                  self.workspace, self.log)

    def test_unique_alias_canonicalizes_workspace_wrapper_only(self):
        executed = f'cd {self.workspace} && python test.py 2>&1'
        self.events(self.event(command=executed))
        check = {'command': 'python test.py', 'evidence_ref': 'event:conversation-id', 'exit_code': 0}
        support.verify_checks([check], self.workspace, self.log)
        self.assertEqual({'command': executed, 'evidence_ref': 'event:actual', 'exit_code': 0}, check)

        self.events(self.event(command=executed), self.event(id='other', command=executed))
        repeated = {'command': 'python test.py', 'evidence_ref': 'event:conversation-id', 'exit_code': 0}
        support.verify_checks([repeated], self.workspace, self.log)
        self.assertEqual('event:other', repeated['evidence_ref'])

        self.events(self.event(command=f'cd {self.workspace.parent} && python test.py 2>&1'))
        with self.assertRaises(ValueError):
            support.verify_checks([{'command': 'python test.py', 'evidence_ref': 'event:conversation-id', 'exit_code': 0}],
                                  self.workspace, self.log)

    def test_repeated_wrapped_executions_bind_the_latest_stale_event_alias(self):
        executed = f'cd {self.workspace} && python test.py 2>&1'
        self.events(self.event(id='first', command=executed, aggregated_output='one test passed'),
                    self.event(id='second', command=executed, aggregated_output='one test passed'))
        check = {'command': executed, 'evidence_ref': 'event:prior-attempt', 'exit_code': 0}
        support.verify_checks([check], self.workspace, self.log)
        self.assertEqual('event:second', check['evidence_ref'])

        self.events(self.event(id='first', command=executed, aggregated_output='one test passed'),
                    self.event(id='second', command=executed, aggregated_output='different output'))
        latest = {'command': executed, 'evidence_ref': 'event:prior-attempt', 'exit_code': 0}
        support.verify_checks([latest], self.workspace, self.log)
        self.assertEqual('event:second', latest['evidence_ref'])

        self.events(self.event(id='first', command=executed, aggregated_output='one test passed'),
                    {'type': 'tool_output', 'id': 'second', 'command': executed, 'aggregated_output': 'FAILED'})
        with self.assertRaises(ValueError):
            support.verify_checks([{'command': 'python test.py', 'evidence_ref': 'event:', 'exit_code': None}],
                                  self.workspace, self.log)
    def test_a_bare_event_reference_binds_to_the_latest_run_of_the_command(self):
        # The Validator cannot see event IDs or exit codes; it says event: and null (2026-10-02).
        self.events(self.event(), self.event(id='other', exit_code=1))
        for check in ({'evidence_ref': 'event:'}, {'evidence_ref': 'event:missing'},
                      {'evidence_ref': 'event:', 'exit_code': None}, {'evidence_ref': 'event:', 'exit_code': 1}):
            with self.subTest(check=check):
                checks = [{'command': 'python test.py', **check}]
                support.verify_checks(checks, self.workspace, self.log)
                self.assertEqual([{'command': 'python test.py', 'evidence_ref': 'event:other', 'exit_code': 1}], checks)
        checks = [{'command': 'python test.py', 'evidence_ref': 'event:actual', 'exit_code': None}]
        support.verify_checks(checks, self.workspace, self.log)
        self.assertEqual(0, checks[0]['exit_code'], 'a cited event fills a null exit code from that event')

    def test_an_earlier_success_is_never_picked_over_the_latest_run(self):
        self.events(self.event(), self.event(id='other', exit_code=1))
        checks = [{'command': 'python test.py', 'evidence_ref': 'event:', 'exit_code': 0}]
        with self.assertRaises(ValueError):
            support.verify_checks(checks, self.workspace, self.log)
        self.assertEqual('event:', checks[0]['evidence_ref'])

    def test_a_later_run_without_an_exit_code_is_never_skipped_for_an_earlier_success(self):
        # Review of #260: the latest run's exit was unknown (an OpenCode bridge without exit metadata), and the check
        # was bound to the run before it, a convenient success. It must be refused: capture a receipt instead.
        self.events(self.event(id='r1'), self.event(id='r2'),
                    {'type': 'tool_output', 'id': 'r3', 'command': 'python test.py', 'aggregated_output': 'FAILED'})
        for check in ({'evidence_ref': 'event:', 'exit_code': None}, {'evidence_ref': 'event:', 'exit_code': 0},
                      {'evidence_ref': 'event:r3', 'exit_code': None}):
            with self.subTest(check=check):
                checks = [{'command': 'python test.py', **check}]
                with self.assertRaises(ValueError):
                    support.verify_checks(checks, self.workspace, self.log)
                self.assertEqual(check['evidence_ref'], checks[0]['evidence_ref'])

    def test_ambiguity_conflicts_and_unknown_exits_are_not_repaired(self):
        for items, check in [
            ([self.event(), self.event()], {'evidence_ref': 'event:actual'}),
            ([self.event(command='other')], {'evidence_ref': 'event:'}),
            ([self.event(exit_code=None)], {'evidence_ref': 'event:actual'}),
            ([self.event(exit_code=False)], {'evidence_ref': 'event:actual'}),
            ([self.event(command='other')], {'evidence_ref': 'event:actual'}),
            ([self.event(exit_code=1)], {'evidence_ref': 'event:actual', 'exit_code': 0}),
            ([self.event()], {'evidence_ref': 'event:actual', 'exit_code': False}),
        ]:
            with self.subTest(items=items, check=check):
                self.events(*items)
                checks = [{'command': 'python test.py', **check}]
                original = copy.deepcopy(checks)
                with self.assertRaises(ValueError):
                    support.verify_checks(checks, self.workspace, self.log)
                self.assertEqual(original, checks)

    def test_report_file_receipt_is_sufficient_without_any_event_log(self):
        path, _ = self.receipt(exit_code=3)
        checks = [{'command': 'python test.py', 'evidence_ref': str(path)}]
        support.verify_checks(checks, self.workspace, self.log, receipt_only=True, capture_context=self.context)
        self.assertEqual(3, checks[0]['exit_code'])
        self.assertFalse(self.log.exists())

    def test_missing_receipt_is_repairable_without_searching_other_directories(self):
        actual, _ = self.receipt()
        missing = self.run / 'evidence' / actual.name
        checks = [{'command': 'python test.py', 'evidence_ref': str(missing)}]
        for receipt_only in (False, True):
            with self.subTest(receipt_only=receipt_only), self.assertRaises(ValueError) as caught:
                support.verify_checks(checks, self.workspace, self.log,
                                      receipt_only=receipt_only, capture_context=self.context)
            self.assertIn(str(missing), str(caught.exception))
            self.assertIn('exact captured receipt path', str(caught.exception))
            self.assertIsInstance(caught.exception.__cause__, FileNotFoundError)
        self.assertTrue(actual.is_file())
        self.assertNotIn('exit_code', checks[0])

    def test_capture_exposes_canonical_command_without_relaxing_verification(self):
        path = self.run / 'quoted.json'
        command = [sys.executable, '-c', 'import sys; print(sys.argv[1])', 'spaces; "quotes" and \'apostrophes\'']
        output = io.StringIO()
        with patch.object(Path, 'cwd', return_value=self.workspace), \
                patch.dict('os.environ', {'AUTOCODE_CAPTURE_CONTEXT': json.dumps(self.context)}), \
                contextlib.redirect_stdout(output):
            self.assertEqual(0, runner.capture_command(['--output', str(path), '--', *command]))
        receipt = json.loads(output.getvalue())
        self.assertEqual(receipt, support.read(path))
        self.assertEqual(shlex.join(command), receipt['command_text'])
        self.assertEqual(command, shlex.split(receipt['command_text']))
        check = {'command': receipt['command_text'], 'evidence_ref': str(path)}
        support.verify_checks([check], self.workspace, self.log,
                              receipt_only=True, capture_context=self.context)
        self.assertEqual(0, check['exit_code'])
        receipt['command_text'] = ' '.join(command)
        path.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(ValueError, 'command/result differs'):
            support.verify_checks([{**check, 'command': receipt['command_text']}], self.workspace, self.log,
                                  receipt_only=True, capture_context=self.context)

    def test_receipt_rejects_wrong_attempt_conflict_tampering_and_boolean_exit(self):
        for mutation in ('context', 'hash', 'command', 'exit', 'boolean'):
            path, receipt = self.receipt()
            checks = [{'command': 'python test.py', 'evidence_ref': str(path)}]
            if mutation == 'context':
                receipt['capture_context'] = {**self.context, 'nonce': 'older-stage'}
            elif mutation == 'hash':
                Path(receipt['full_output']).write_text('tampered')
            elif mutation == 'command':
                checks[0]['command'] = 'python different.py'
            elif mutation == 'exit':
                checks[0]['exit_code'] = 1
            else:
                receipt['exit_code'] = False
            path.write_text(json.dumps(receipt))
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                support.verify_checks(checks, self.workspace, self.log, receipt_only=True, capture_context=self.context)

    def test_receipt_does_not_bypass_event_attestation_for_event_provider(self):
        path, receipt = self.receipt()
        checks = [{'command': 'python test.py', 'evidence_ref': str(path)}]
        with self.assertRaises(ValueError):
            support.verify_checks(checks, self.workspace, self.log)
        self.events(self.event(aggregated_output=json.dumps(receipt)))
        support.verify_checks(checks, self.workspace, self.log)
        self.assertEqual(0, checks[0]['exit_code'])

    def test_report_file_cannot_derive_from_event_even_when_present(self):
        self.events(self.event())
        with self.assertRaises(ValueError):
            support.verify_checks([{'command': 'python test.py', 'evidence_ref': 'event:actual'}],
                self.workspace, self.log, receipt_only=True, capture_context=self.context)

    def test_load_normalizes_before_strict_schema_and_preserves_original_and_verdict(self):
        self.events(self.event(exit_code=2))
        schema = support.read(runner.SCHEMA_DIR / 'v2/sol-report.schema.json')
        report = {'verdict': 'FAIL', 'checks_run': ['python test.py'], 'findings': [],
                  'unverified_criteria': [], 'criterion_results': [],
                  'checks': [{'command': 'python test.py', 'evidence_ref': 'event:actual'}]}
        for nested in (False, True):
            selected = {'validation': report} if nested else report
            schema_path = self.run / 'schema.json'
            schema_path.write_text(json.dumps({'type': 'object', 'properties': {'validation': schema}} if nested else schema))
            output = self.run / ('nested.json' if nested else 'sol.json')
            output.write_text(json.dumps(selected))
            record = {'output': str(output), 'events': str(self.log), 'schema': str(schema_path)}
            result = runner.load_stage_report(record, self.workspace)
            validation = result.get('validation', result)
            self.assertEqual(2, validation['checks'][0]['exit_code'])
            self.assertEqual('FAIL', validation['verdict'])
            self.assertEqual(selected, support.read(record['reported_output']))
            self.assertEqual(result, support.read(output))
            self.assertEqual(result, runner.load_stage_report(record, self.workspace))

    def test_missing_other_required_fields_still_fails_schema(self):
        self.events(self.event())
        output = self.run / 'sol.json'
        report = {'checks': [{'command': 'python test.py', 'evidence_ref': 'event:actual'}]}
        output.write_text(json.dumps(report))
        record = {'output': str(output), 'events': str(self.log),
                  'schema': str(runner.SCHEMA_DIR / 'v2/sol-report.schema.json')}
        with self.assertRaises(ValueError):
            runner.load_stage_report(record, self.workspace)
        self.assertEqual(report, support.read(output))

    def test_repair_derivation_uses_original_attempt_receipt(self):
        receipt, _ = self.receipt()
        output = self.run / 'sol_report_repair.json'
        output.write_text(json.dumps({'verdict': 'PASS', 'checks_run': ['python test.py'],
            'findings': [], 'unverified_criteria': [], 'criterion_results': [],
            'checks': [{'command': 'python test.py', 'evidence_ref': str(receipt)}]}))
        original = {'events': str(self.log), 'output_mode': 'report_file', 'capture_context': self.context}
        record = {**original, 'output': str(output),
                  'capture_context': {**self.context, 'nonce': 'repair-attempt'},
                  'schema': str(runner.SCHEMA_DIR / 'v2/sol-report.schema.json')}
        with self.assertRaises(ValueError):
            runner.load_stage_report(record, self.workspace)
        value = runner.load_stage_report(record, self.workspace, original)
        self.assertEqual(0, value['checks'][0]['exit_code'])
