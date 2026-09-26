"""Derive only missing check metadata from unique, verified execution evidence."""
# path bootstrap: runtime in tools/, fakes in tests/fakes/
import sys as _sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[2] if 'fakes' in _Path(__file__).parts else _Path(__file__).resolve().parents[1]
_TOOLS = _ROOT / 'tools'
_FAKES = _ROOT / 'tests' / 'fakes'
for _p in (_ROOT, _TOOLS, _ROOT / 'tests', _FAKES):
    _s = str(_p)
    if _s not in _sys.path:
        _sys.path.insert(0, _s)
import copy
import json
from pathlib import Path
import tempfile
import unittest

from .test_autocode import runner, s as support


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

    def test_ambiguity_conflicts_and_unknown_exits_are_not_repaired(self):
        for items, check in [
            ([self.event(), self.event(id='other', exit_code=1)], {'evidence_ref': 'event:missing'}),
            ([self.event(), self.event()], {'evidence_ref': 'event:actual'}),
            ([self.event(exit_code=None)], {'evidence_ref': 'event:actual'}),
            ([self.event(exit_code=False)], {'evidence_ref': 'event:actual'}),
            ([self.event(command='other')], {'evidence_ref': 'event:actual'}),
            ([self.event(exit_code=1)], {'evidence_ref': 'event:actual', 'exit_code': 0}),
            ([self.event()], {'evidence_ref': 'event:actual', 'exit_code': None}),
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
