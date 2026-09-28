"""Preserved candidates may proceed only to fresh independent validation."""
import copy
import unittest

import autopilot, autocode_support as support
from . import test_autocode


class RetainedCandidateTests(unittest.TestCase):
    setUp = test_autocode.RetrofitTest.setUp
    tearDown = test_autocode.RetrofitTest.tearDown

    def fixture(self):
        (self.root / 'greet.py').write_text('print("fixed")\n')
        revision = support.snapshot(self.root)['revision']
        if 'goal_contract' not in self.state:
            contract = {'task_id': 'task-1', 'revision': 1,
                        'body': {'open_blocking_questions': [], 'acceptance_criteria': []}}
            contract.update(hash=support.digest(contract), approval_status='approved', approval_event={})
            self.state['goal_contract'] = contract
        self.state['goal_contract']['body']['acceptance_criteria'] = [
            {'id': 'AC1', 'criterion': 'fixed', 'verification': {'method': 'test'}}]
        self.state['current_task'] = {'id': 'task-1', 'kind': 'implement',
                                      'affected_paths': ['greet.py'], 'source_revision': revision}
        validation = {'verdict': 'PASS', 'source_revision': revision, 'output': 'sol-old.json',
                      'criterion_results': [{'id': 'AC1', 'status': 'PASS'}]}
        self.state['validation_archive'] = [{'reason': 'plan feedback', 'validation': validation}]
        value = {'changed_files': ['greet.py'], 'commands_run': ['python test.py'],
                 'evidence_refs': ['event:check'], 'summary': 'verified'}
        record = {'changed_files': [], 'output': 'terra.json'}
        return value, record, revision

    def test_exact_retained_pass_routes_to_validator_not_completion(self):
        value, record, revision = self.fixture()
        evidence = autopilot.retained_validated_candidate(self.state, value, record, self.root)
        self.assertEqual(revision, evidence['source_revision'])
        self.assertEqual(['AC1'], evidence['criteria'])

    def test_changed_source_missing_criterion_or_scope_claim_fails_closed(self):
        value, record, _ = self.fixture()
        baseline = copy.deepcopy(self.state)
        mutations = (
            lambda: (self.root / 'greet.py').write_text('changed again\n'),
            lambda: self.state['validation_archive'][0]['validation'].__setitem__('criterion_results', []),
            lambda: value.__setitem__('changed_files', ['outside.py']),
            lambda: value.__setitem__('evidence_refs', []),
            lambda: record.__setitem__('changed_files', ['greet.py']),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.state = copy.deepcopy(baseline)
                value, record, _ = self.fixture()
                mutate()
                self.assertIsNone(autopilot.retained_validated_candidate(self.state, value, record, self.root))
                (self.root / 'greet.py').write_text('print("fixed")\n')

    def test_saved_no_progress_report_routes_to_fresh_validator_without_builder(self):
        value, record, revision = self.fixture()
        record.update(stage='terra', source_revision=revision, output='terra.json', after_ref='after.json')
        self.state['stages'].append(record)
        self.state['no_progress_reports'] = [copy.deepcopy(value)]
        self.state['no_progress_batches'] = 3
        self.assertTrue(autopilot.recover_retained_candidate(self.state, self.root))
        self.assertEqual('sol', self.state['next_stage'])
        self.assertEqual(0, self.state['no_progress_batches'])
        self.assertEqual(revision, self.state['implementation']['source_revision'])
        self.assertTrue(self.state['retained_candidate_handoffs'][-1]['reconsidered'])

    def test_saved_no_progress_report_cannot_bypass_changed_source_or_missing_pass(self):
        value, record, revision = self.fixture()
        record.update(stage='terra', source_revision=revision, output='terra.json')
        self.state['stages'].append(record)
        self.state['no_progress_reports'] = [copy.deepcopy(value)]
        baseline = copy.deepcopy(self.state)
        self.state['validation_archive'] = []
        self.assertFalse(autopilot.recover_retained_candidate(self.state, self.root))
        self.state = copy.deepcopy(baseline)
        (self.root / 'greet.py').write_text('changed again\n')
        self.assertFalse(autopilot.recover_retained_candidate(self.state, self.root))
