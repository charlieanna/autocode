"""Only a completed original review can authorize retained dispositions."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from autocode_report_findings import preserved_dispositions
from autocode_report_source import original_report_for_repair


def disposition(fid='F-1', *, action='resolved', evidence='event:check'):
    return {'id': fid, 'disposition': action, 'evidence': evidence}


def report(*rows, source='sol', outcome=None):
    field, default = ('verdict', 'PASS') if source == 'sol' else ('status', 'COMPLETE')
    return {field: default if outcome is None else outcome,
            'finding_dispositions': list(rows), 'unrelated_evidence': 'missing.json'}


class PreservedDispositionsTests(unittest.TestCase):
    def test_citation_repair_retains_exact_dispositions_without_mutating_inputs(self):
        for stage, source in (('sol', 'sol'), ('astra_review', 'astra'), ('astra_plan', 'astra')):
            with self.subTest(stage=stage):
                record = {'stage': stage, 'exit_code': 0, 'rejected': True}
                original = report(disposition(), disposition('F-2', action='retracted'), source=source)
                repaired = {**copy.deepcopy(original), 'unrelated_evidence': 'existing.json'}
                before = copy.deepcopy((record, original, repaired))
                kept = preserved_dispositions(record, original, repaired)
                self.assertEqual({source: original['finding_dispositions']}, kept)
                self.assertEqual(before, (record, original, repaired))
                kept[source][0]['evidence'] = 'changed by caller'
                self.assertEqual(before, (record, original, repaired))

    def test_repair_never_whitelists_a_new_id_action_or_evidence(self):
        original = report(disposition())
        for change in ({'id': 'F-new'}, {'disposition': 'retracted'},
                       {'evidence': 'event:new'}, {'evidence': ' event:check '}):
            with self.subTest(change=change):
                changed = {**disposition(), **change}
                self.assertEqual({}, preserved_dispositions(
                    {'stage': 'sol', 'exit_code': 0}, original, report(changed)))
        kept = preserved_dispositions({'stage': 'sol', 'exit_code': 0}, original,
            report(disposition(), disposition('F-new')))
        self.assertEqual({'sol': [disposition()]}, kept)

    def test_checkpoint_preserves_each_reviewers_rows_without_crossing_sources(self):
        original = {'validation': report(disposition('F-sol')),
                    'decision': report(disposition('F-astra', action='retracted'), source='astra')}
        record = {'stage': 'astra_checkpoint', 'exit_code': 0}
        expected = {'sol': [disposition('F-sol')],
                    'astra': [disposition('F-astra', action='retracted')]}
        self.assertEqual(expected, preserved_dispositions(record, original, copy.deepcopy(original)))
        swapped = {'validation': original['decision'], 'decision': original['validation']}
        self.assertEqual({}, preserved_dispositions(record, original, swapped))
        duplicate = {'validation': report(disposition()), 'decision': report(disposition(), source='astra')}
        self.assertEqual({}, preserved_dispositions(record, duplicate, duplicate))

    def test_blocked_missing_or_unknown_original_outcome_cannot_gain_authority(self):
        for stage, source, field in (('sol', 'sol', 'verdict'),
                                    ('astra_review', 'astra', 'status'),
                                    ('astra_plan', 'astra', 'status')):
            record = {'stage': stage, 'exit_code': 0}
            repaired = report(disposition(), source=source)
            for outcome in ('BLOCKED', 'UNKNOWN', 'NOT_VERIFIED', None, '', False):
                with self.subTest(stage=stage, outcome=outcome):
                    original = copy.deepcopy(repaired)
                    if outcome is None:
                        original.pop(field)
                    else:
                        original[field] = outcome
                    self.assertEqual({}, preserved_dispositions(record, original, repaired))

    def test_recognized_original_outcomes_retain_existing_dispositions(self):
        for source, stage, outcomes in (('sol', 'sol', ('PASS', 'FAIL')),
            ('astra', 'astra_review', ('CONTINUE', 'REWORK', 'COMPLETE', 'TASK_COMPLETE'))):
            for outcome in outcomes:
                with self.subTest(source=source, outcome=outcome):
                    original = report(disposition(), source=source, outcome=outcome)
                    self.assertEqual({source: [disposition()]}, preserved_dispositions(
                        {'stage': stage, 'exit_code': 0}, original, copy.deepcopy(original)))

    def test_checkpoint_outcome_authority_is_separate_for_each_reviewer(self):
        record = {'stage': 'astra_checkpoint', 'exit_code': 0}
        valid = {'validation': report(disposition('F-sol')),
                 'decision': report(disposition('F-astra'), source='astra')}
        for blocked_source, section, field, retained_source, retained_id in (
            ('sol', 'validation', 'verdict', 'astra', 'F-astra'),
            ('astra', 'decision', 'status', 'sol', 'F-sol')):
            for outcome in ('BLOCKED', 'UNKNOWN', None):
                with self.subTest(source=blocked_source, outcome=outcome):
                    original = copy.deepcopy(valid)
                    if outcome is None:
                        original[section].pop(field)
                    else:
                        original[section][field] = outcome
                    self.assertEqual({retained_source: [disposition(retained_id)]},
                        preserved_dispositions(record, original, valid))

    def test_incomplete_stopped_or_already_repaired_original_cannot_authorize(self):
        original = report(disposition())
        record = {'stage': 'sol', 'exit_code': 0}
        for change in ({'stage': 'terra'}, {'stage': 'sol_report_repair'}, {'exit_code': 1},
                       {'exit_code': None}, {'exit_code': False}, *({flag: True} for flag in ('report_only',
                           'report_repaired', 'truncated_output', 'timed_out', 'interrupted', 'abandoned'))):
            with self.subTest(change=change):
                self.assertEqual({}, preserved_dispositions({**record, **change}, original, original))
        for extracted in (None, [], 'invalid JSON', {'report': original, 'extraction_error': 'incomplete'},
                          {'report': original, 'extraction_error': None}):
            with self.subTest(extracted=extracted):
                self.assertEqual({}, preserved_dispositions(record, extracted, original))

    def test_malformed_or_duplicate_ids_fail_closed_for_original_and_repair(self):
        record = {'stage': 'sol', 'exit_code': 0}
        good = report(disposition())
        bad_reports = [report(disposition(), disposition()),
            {'verdict': 'PASS', 'finding_dispositions': {}}, report({'id': 'F-1'}),
            report({**disposition(), 'extra': 'ignored?'}),
            report(disposition('')), report(disposition(' F-1')),
            report(disposition(action='fixed')), report(disposition(evidence=' '))]
        for bad in bad_reports:
            with self.subTest(bad=bad):
                self.assertEqual({}, preserved_dispositions(record, bad, good))
                self.assertEqual({}, preserved_dispositions(record, good, bad))

    def test_absent_dispositions_do_not_gain_authority_from_a_repair(self):
        record = {'stage': 'sol', 'exit_code': 0}
        self.assertEqual({}, preserved_dispositions(record, {'verdict': 'PASS'}, report(disposition())))
        self.assertEqual({}, preserved_dispositions(record, report(disposition()), {'verdict': 'PASS'}))


class OriginalReportExtractionTests(unittest.TestCase):
    def test_saved_json_is_extracted_without_acceptance_or_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'report.json'
            value = report(disposition())
            path.write_text(json.dumps(value))
            before = path.read_bytes()
            self.assertEqual({'report': value, 'extraction_error': None},
                original_report_for_repair({'stage': 'sol', 'output': str(path)}))
            self.assertEqual(before, path.read_bytes())
            path.write_text('{invalid')
            result = original_report_for_repair({'output': str(path)})
            self.assertIsNone(result['report'])
            self.assertTrue(result['extraction_error'])

    def test_opencode_uses_terminal_events_and_retains_extraction_failure(self):
        original = {'engine': 'opencode', 'events': 'original.jsonl', 'output': 'report.json'}
        with patch('autocode_report_source.opencode.final_report', return_value=report(disposition())) as extract:
            result = original_report_for_repair(original)
        extract.assert_called_once_with('original.jsonl')
        self.assertEqual({'report': report(disposition()), 'extraction_error': None}, result)
        with patch('autocode_report_source.opencode.final_report', side_effect=RuntimeError('no terminal report')):
            self.assertEqual({'report': None, 'extraction_error': 'no terminal report'},
                original_report_for_repair(original))
