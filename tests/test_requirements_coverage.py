"""Exact requirement quotes, formatting-aware coverage, and guarded retries."""
import copy
import json
from pathlib import Path
import tempfile
import subprocess
import unittest

import autocode as runner, autocode_goals as goals, autocode_support as support
from units import autoplanner


class CoverageTests(unittest.TestCase):
    def report(self, *quotes):
        return {'requirements': [{'id': 'R' + str(i), 'text': quote, 'source_quote': quote}
                                 for i, quote in enumerate(quotes)], 'ignored_statements': []}

    def test_multisentence_quote_covers_bulleted_requirement(self):
        quote = 'The Validator must inspect the actual images. No human review gate is requested.'
        for marker in ('- ', '* ', '+ ', '1. ', '2) '):
            with self.subTest(marker=marker):
                state = {'task': 'Review policy.\n' + marker + quote}
                goals.check_requirement_handoff(state, self.report(quote))

    def test_formatting_normalization_does_not_authorize_invented_quotes(self):
        with self.assertRaisesRegex(ValueError, 'source_quote is not in'):
            goals.check_requirement_handoff({'task': '- The Validator must inspect images.'},
                                            self.report('The Validator must approve images.'))

    def test_builder_task_cannot_be_cited_as_new_user_requirement(self):
        inherited = 'Fix the scheduling-dependent race in the conversation test.'
        state = {'task': 'Build a planner.',
                 'current_task': {'requirements': [inherited]},
                 'brief_feedback': [{'text': 'Declare the test file in M2.'}]}
        report = {'requirements': [
            {'id': 'R1', 'text': 'Declare the test file',
             'source_quote': 'Declare the test file in M2.'},
            {'id': 'R2', 'text': inherited, 'source_quote': inherited}],
            'ignored_statements': []}
        with self.assertRaisesRegex(ValueError, 'keep that existing obligation'):
            goals.check_requirement_handoff(state, report)
        report['requirements'].pop()
        goals.check_requirement_handoff(state, report)

    def test_feedback_archives_stale_report_repair_before_new_handoff(self):
        state = {'status': 'WAITING_FOR_USER', 'settings': {'roles': {'requirements': {}}},
                 'pending_report_repair': {'attempts': 2, 'error': 'stale citation'}}
        goals.feedback(state, 'Declare the consumer test in M2.')
        self.assertEqual('requirements_gather', state['next_stage'])
        self.assertNotIn('pending_report_repair', state)
        self.assertEqual('stale citation', state['report_repair_archive'][-1]['repair']['error'])

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


class HeadingCueTests(unittest.TestCase):
    """A Markdown heading label is formatting, not a user requirement (#216)."""

    def report(self, *quotes):
        return {'requirements': [{'id': 'R' + str(i), 'text': quote, 'source_quote': quote}
                                 for i, quote in enumerate(quotes)], 'ignored_statements': []}

    def test_label_heading_is_not_an_obligation(self):
        # The live-run shape: the heading fused with an orphaned list marker into
        # one un-quotable "requirement-like sentence".
        task = ('Add refunds.\n\n## Required behavior\n\n'
                '1. Refunds must credit the original payment method.')
        self.assertEqual(['Refunds must credit the original payment method.'], goals.cue_sentences(task))
        goals.check_requirement_handoff({'task': task},
                                        self.report('Refunds must credit the original payment method.'))

    def test_substantive_heading_stays_a_clean_obligation(self):
        task = 'Harden storage.\n\n## Files must be encrypted\n'
        self.assertEqual(['Files must be encrypted.'], goals.cue_sentences(task))
        with self.assertRaisesRegex(ValueError, 'Files must be encrypted'):
            goals.check_requirement_handoff({'task': task}, self.report())
        goals.check_requirement_handoff({'task': task}, self.report('Files must be encrypted'))

    def test_plain_requirement_sentences_are_still_enforced(self):
        task = 'Add refunds. Refunds must credit the original payment method.'
        self.assertEqual(['Refunds must credit the original payment method.'], goals.cue_sentences(task))
        with self.assertRaisesRegex(ValueError, 'Refunds must credit'):
            goals.check_requirement_handoff({'task': task}, self.report())

    def test_a_fenced_block_is_context_not_an_obligation(self):
        task = ('Build notes. Notes must be numbered from 1.\n\n```json\n{"note": "M4 must only test the journey."}\n```\n'
                'Search must ignore case.\n\n```\nexport must never print a header\n')  # the last fence never closes
        self.assertEqual(['Notes must be numbered from 1.', 'Search must ignore case.'], goals.cue_sentences(task))
        goals.check_requirement_handoff({'task': task}, self.report('Notes must be numbered from 1.',
                                                                    'Search must ignore case.'))
        with self.assertRaisesRegex(ValueError, 'Search must ignore case'):
            goals.check_requirement_handoff({'task': task}, self.report('Notes must be numbered from 1.'))
        # A sentence inside the block stays quotable.
        self.assertIn(task, goals.source_texts({'task': task}))

    def test_delegated_answer_is_quotable_but_never_owed(self):
        state = {'task': 'Add receipts.',
                 'answers': {'q1': {'kind': 'delegated', 'text': 'Use plain-text receipts only.'}}}
        self.assertIn('Use plain-text receipts only.', goals.source_texts(state))
        self.assertNotIn('Use plain-text receipts only.', goals.scan_texts(state))
        # Not covering the model's own default passes...
        goals.check_requirement_handoff(state, self.report())
        # ...and a report may still quote it as a source.
        goals.check_requirement_handoff(state, self.report('Use plain-text receipts only.'))

    def test_real_answer_still_must_be_covered(self):
        state = {'task': 'Add receipts.',
                 'answers': {'q1': {'kind': 'answer', 'text': 'Receipts must be plain text.'}}}
        with self.assertRaisesRegex(ValueError, 'Receipts must be plain text'):
            goals.check_requirement_handoff(state, self.report())

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
