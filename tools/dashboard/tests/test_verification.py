"""The dashboard trusts only a matching supported inspection, not saved PASS text."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard_verification import for_view, digest, VerificationViewMixin


def fixture():
    view = {'status': 'TASK_COMPLETE', 'goal': {'revision': 2, 'hash': 'plan'},
            'criteria': [{'id': 'C1', 'criterion': 'Sends a message'}],
            'validation': {'source_revision': 'source-one', 'criterion_results': [{'id': 'C1', 'status': 'PASS'}]}}
    report = {'version': 1, 'freshness': 'current', 'reasons': ['Current bytes inspected'],
              'contract_token': 'r2:plan', 'criteria_token': digest(view['criteria']),
              'report_token': digest(view['validation']), 'coverage': [{'id': 'C1', 'state': 'checked'}]}
    return view, {'status': 'TASK_COMPLETE', 'completion_current': True, 'view': {'verification': report}}


class VerificationTests(unittest.TestCase):
    def test_matching_inspection_is_copied_and_retains_proof_identity(self):
        view, status = fixture()
        before = copy.deepcopy(status)
        result = for_view(view, status)
        self.assertEqual('current', result['freshness'])
        result['coverage'][0]['state'] = 'unchecked'
        self.assertEqual(before, status)

    def test_wrong_report_plan_criteria_status_missing_or_error_never_shows_current(self):
        for field, value in [('report_token', 'other'), ('contract_token', 'r1:old'), ('criteria_token', 'other'), ('task_id', 'other-task'), ('criteria_revision', 999)]:
            with self.subTest(field=field):
                view, status = fixture()
                status['view']['verification'][field] = value
                self.assertEqual('unavailable', for_view(view, status)['freshness'])
        view, status = fixture()
        status['status'] = 'RUNNING'
        for data, error in [(status, None), ({}, None), ({'view': []}, None), (fixture()[1], {'message': 'timeout'})]:
            with self.subTest(data=data):
                self.assertEqual('unavailable', for_view(view, data, error)['freshness'])

    def test_displayed_attempt_or_runner_check_cannot_borrow_newer_inspection(self):
        for field in ('active_stage', 'runner_check'):
            view, status = fixture()
            view[field] = {'stage': 'sol'}
            self.assertEqual('unavailable', for_view(view, status)['freshness'])

    def test_detail_uses_supported_read_only_status_and_requires_full_completion_gate(self):
        console = VerificationViewMixin()
        console.INTERVENTION_TIMEOUT = 30
        for completed, freshness in [(True, 'current'), (False, 'current'), (True, 'stale_or_unverified')]:
            view, status = fixture()
            status['completion_current'] = completed
            status['view']['verification']['freshness'] = freshness
            console._json_command = Mock(return_value=(status, None))
            console.inspect_verification('/project', '/project/.autocode/runs/test', view)
            self.assertEqual(completed and freshness == 'current', view['completion_current'])
            console._json_command.assert_called_once_with(
                ['--workspace', '/project', '--run-dir', '/project/.autocode/runs/test', '--status', '--inspect-evidence'], timeout=30)

    def test_no_report_needs_no_additional_cli_process_and_cannot_claim_complete(self):
        console = VerificationViewMixin()
        console._json_command = Mock(side_effect=AssertionError('No report needs no inspection'))
        view = {'status': 'TASK_COMPLETE'}
        console.inspect_verification('/project', '/run', view)
        self.assertFalse(view['completion_current'])
        self.assertEqual('not_recorded', view['verification']['freshness'])
