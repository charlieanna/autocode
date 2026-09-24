"""Exact requirement quotes, formatting-aware coverage, and guarded retries."""
import copy
import json
from pathlib import Path
import tempfile
import subprocess
import unittest

from . import autocode as runner, autocode_goals as goals, autocode_support as support
from .units import autoplanner


class CoverageTests(unittest.TestCase):
    def report(self, *quotes):
        return {'requirements': [{'id': 'R' + str(i), 'text': quote, 'source_quote': quote}
                                 for i, quote in enumerate(quotes)], 'ignored_statements': []}

    def test_multisentence_quote_covers_bulleted_requirement(self):
        quote = 'Sol must inspect the actual images. No human review gate is requested.'
        for marker in ('- ', '* ', '+ ', '1. ', '2) '):
            with self.subTest(marker=marker):
                state = {'task': 'Review policy.\n' + marker + quote}
                goals.check_requirement_handoff(state, self.report(quote))

    def test_formatting_normalization_does_not_authorize_invented_quotes(self):
        with self.assertRaisesRegex(ValueError, 'source_quote is not in'):
            goals.check_requirement_handoff({'task': '- Sol must inspect images.'},
                                            self.report('Sol must approve images.'))

    def test_all_missing_sentences_are_reported_without_truncation(self):
        sentences = ['You must preserve the reference.', 'You must inspect ' + 'every image pair ' * 12 + '.']
        with self.assertRaises(ValueError) as caught:
            goals.check_requirement_handoff({'task': ' '.join(sentences)}, self.report())
        for sentence in sentences:
            self.assertIn(sentence, str(caught.exception))

    def test_ignored_statement_still_requires_explicit_coverage(self):
        state = {'task': 'You must retain drafts. You must use fixtures.'}
        report = self.report('You must retain drafts.')
        with self.assertRaisesRegex(ValueError, 'You must use fixtures'):
            goals.check_requirement_handoff(state, report)
        report['ignored_statements'] = ['You must use fixtures. This is a validation procedure.']
        goals.check_requirement_handoff(state, report)

    def test_requirements_prompt_exposes_the_exact_coverage_checklist(self):
        state = {'task': 'Make a dashboard.\n- You must retain drafts.', 'workspace': '/fixture',
                 'settings': {'engine': 'codex', 'joint_planning': True, 'roles': {'requirements': {}}}}
        prompt, _ = autoplanner.context(state, 'requirements_gather', Path('/fixture/state.json'))
        packet = json.loads(prompt.split('CURRENT HANDOFF DATA\n', 1)[1])
        self.assertEqual(['- You must retain drafts.'], packet['requirement_coverage_checklist'])
        self.assertIsNone(packet['goal_contract'])

    def test_changed_cause_allows_fresh_planning_retry_without_erasing_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run = root / '.autocode/run'; run.mkdir(parents=True)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            subprocess.run(['git', '-C', str(root), '-c', 'user.name=Fixture', '-c',
                            'user.email=fixture@example.test', 'commit', '--allow-empty', '-qm', 'fixture'], check=True)
            (root / 'checker.py').write_text('old checker')
            record = {'stage': 'requirements_gather', 'source_revision': support.snapshot(root)['revision'],
                      'output': str(run / 'rejected.json'), 'failure_key': 'failure'}
            Path(record['output']).write_text('{"rejected":true}')
            state = {'status': 'PAUSED_REPEATED_FAILURE', 'next_stage': 'requirements_gather',
                     'settings': {'joint_planning': True}, 'stages': [record],
                     'pending_report_repair': {'original': record, 'attempts': 2},
                     'failure_history': {'failure': {'count': 3, 'identity': {'error_class': 'ValueError'}}}}
            before = copy.deepcopy(state)
            with self.assertRaises(support.Paused):
                runner.repeated_failure_resume_guard(state, root)
            self.assertEqual(before, state)
            (root / 'checker.py').write_text('fixed checker')
            runner.repeated_failure_resume_guard(state, root)
            self.assertTrue(runner.prepare_planning_retry(state, run))
            self.assertNotIn('pending_report_repair', state)
            self.assertEqual(before['pending_report_repair'], state['report_repair_archive'][-1]['repair'])
            self.assertEqual(before['stages'], state['stages'])
            self.assertEqual('{"rejected":true}', Path(record['output']).read_text())
            self.assertEqual('PAUSED_REPEATED_FAILURE', state['status'])


if __name__ == '__main__':
    unittest.main()
