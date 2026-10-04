"""Saved public status is the sole authority for the Work summary."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard_work_summary import project, progress_from_status


def fixture(status='RUNNING'):
    view = {'status': status, 'goal': {'revision': 2, 'hash': 'current', 'body': {'milestones': [
        {'id': 'M1', 'objective': 'Build the form'}, {'id': 'M2', 'objective': 'Check delivery'}]}},
        'criteria': [{'id': 'R1', 'criterion': 'Message sends'}, {'id': 'R2', 'criterion': 'Errors are readable'}],
        'validation': {'contract_hash': 'current', 'criterion_results': [{'id': 'R1', 'status': 'PASS'}]},
        'monitor': {'findings': []}, 'questions': []}
    view['interventions'] = {'work_progress': progress_from_status({
        'status': status, 'contract_token': 'r2:current', 'active_stage': {'stage': 'sol'},
        'current_task': {'milestone_id': 'M2'}, 'milestone_checkpoint': {'accepted_milestones': ['M1']}})}
    return view


class WorkSummaryTests(unittest.TestCase):
    def test_failed_check_counts_and_links_are_saved_and_do_not_mutate_input(self):
        view = fixture()
        view['validation']['criterion_results'].append({'id': 'R2', 'status': 'FAIL'})
        view['monitor']['findings'] = [{'id': 'F7', 'finding': 'Error is hidden', 'source': 'sol', 'times_reported': 2}]
        before = json.dumps(view, sort_keys=True)
        result = project(view)
        self.assertEqual('1 of 2 tasks complete · 1 of 2 requirements checked · 1 failed · 0 unchecked · 1 open problem · No decision requested', result['line'])
        self.assertEqual(['done', 'working'], [r['state'] for r in result['tasks']])
        self.assertEqual(['checked', 'failed'], [r['state'] for r in result['requirements']])
        self.assertEqual('F7', result['problems'][0]['id'])
        self.assertEqual(2, result['problems'][0]['times_reported'])
        self.assertEqual(before, json.dumps(view, sort_keys=True))

    def test_pending_questions_and_unknown_checks_remain_visible(self):
        view = fixture('WAITING_FOR_USER')
        view['questions'] = [{'id': 'Q1', 'question': 'Which inbox?'}, {'id': 'Q2', 'question': 'Which host?'}]
        result = project(view)
        self.assertEqual('1 of 2 tasks complete · 1 of 2 requirements checked · 1 unchecked · 0 open problems · 2 questions to answer', result['line'])
        self.assertEqual('waiting', result['tasks'][1]['state'])

    def test_finished_job_only_uses_recorded_acceptance(self):
        view = fixture('TASK_COMPLETE')
        view['interventions']['work_progress']['accepted'].append('M2')
        view['validation']['criterion_results'].append({'id': 'R2', 'status': 'PASS'})
        result = project(view)
        self.assertEqual('2 of 2 tasks complete · 2 of 2 requirements checked · 0 unchecked · 0 open problems · No decision requested', result['line'])
        del view['interventions']
        self.assertFalse(project(view)['task_progress_known'])
        self.assertEqual(['unknown', 'unknown'], [r['state'] for r in project(view)['tasks']])

    def test_prior_contract_and_missing_status_cannot_show_current_completion(self):
        view = fixture()
        view['interventions']['work_progress']['contract_token'] = 'r1:old'
        view['validation']['contract_hash'] = 'old'
        result = project(view)
        self.assertEqual(0, result['counts']['done'])
        self.assertEqual(0, result['counts']['checked'])
        self.assertEqual(2, result['counts']['unchecked'])
        self.assertTrue(result['verification_stale'])
        self.assertFalse(result['task_progress_known'])
        self.assertIsNone(progress_from_status({})['accepted'])

    def test_prose_plans_and_absent_plans_do_not_invent_acceptance(self):
        view = fixture()
        view['goal']['body'] = {}
        view['plan'] = ['Build the form', 'Check delivery']
        result = project(view)
        self.assertEqual(['Build the form', 'Check delivery'], [r['label'] for r in result['tasks']])
        self.assertFalse(result['task_progress_known'])
        view['plan'] = []
        self.assertEqual('No saved task list', project(view)['task_label'])

    def test_stale_controller_is_not_reported_as_working(self):
        source = {'status': 'RUNNING', 'active_stage': {'stage': 'terra'},
                  'contract_token': 'r2:current', 'current_task': {'milestone_id': 'M2'},
                  'milestone_checkpoint': {'accepted_milestones': ['M1']}, 'stale': True}
        view = fixture()
        view['interventions']['work_progress'] = progress_from_status(source)
        self.assertEqual(['done', 'waiting'], [row['state'] for row in project(view)['tasks']])
