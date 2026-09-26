"""Interaction regressions for inboxes with existing planning/report recovery."""
import copy
import unittest
from unittest.mock import patch
try:
    from . import test_report_repair as repair_fixtures
    from . import test_goals as goal_fixtures
except ImportError:
    import test_report_repair as repair_fixtures
    import test_goals as goal_fixtures

runner = repair_fixtures.runner
support = repair_fixtures.support
inbox = runner.interventions


class DashboardIntegrationTests(unittest.TestCase):
    def repair_fixture(self):
        fixture = repair_fixtures.RepairTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.queue()
        return fixture

    def submit(self, fixture):
        return inbox.submit(fixture.root, fixture.run, request_id='changed-goal', kind='feedback', text='Keep partial work')

    def test_feedback_archives_pending_format_repair_and_returns_to_discovery(self):
        fixture = self.repair_fixture()
        original = copy.deepcopy(fixture.state['pending_report_repair'])
        self.submit(fixture)
        runner.consume_interventions(fixture.state, fixture.run, fixture.root)
        self.assertNotIn('pending_report_repair', fixture.state)
        self.assertEqual(original, fixture.state['report_repair_archive'][-1]['repair'])
        self.assertEqual('PAUSED_INTERVENTION', fixture.state['status'])
        self.assertEqual('astra_discovery', fixture.state['next_stage'])

    def test_denied_repair_admission_does_not_spend_a_retry(self):
        fixture = self.repair_fixture()
        with patch.object(runner, 'run_role', side_effect=support.Paused('PAUSED_INTERVENTION_PENDING', 'Queued request')):
            with self.assertRaises(support.Paused):
                runner.execute_report_repair(fixture.state, fixture.run, fixture.root)
        self.assertEqual(0, fixture.state['pending_report_repair']['attempts'])
        self.assertEqual(0, support.read(fixture.run/'state.json')['pending_report_repair']['attempts'])

    def test_repaired_completion_cannot_commit_over_earlier_feedback(self):
        fixture = self.repair_fixture()
        self.submit(fixture)
        writes = []
        original_write = runner.write_json
        def observe(path, value):
            if path == fixture.run/'state.json':
                writes.append(value['status'])
            original_write(path, value)
        def complete(state, stage, value, record, workspace, run):
            state.update(status='TASK_COMPLETE', next_stage=None, completed_at='fixture')
            runner.save_record(state, record)
        repair = {'stage':'astra_review_report_repair','events':'repair-events','output':'repair-output'}
        with patch.object(runner, 'apply_result', side_effect=complete), patch.object(runner, 'write_json', side_effect=observe):
            runner.accept_repaired_report(fixture.state, fixture.run, fixture.root, {}, repair)
        self.assertTrue(writes)
        self.assertNotIn('TASK_COMPLETE', writes)
        self.assertEqual('PAUSED_INTERVENTION', fixture.state['status'])
        self.assertEqual('accepted', fixture.state['report_repair_history'][-1]['result'])
        self.assertEqual('astra_review_report_repair', fixture.state['stages'][-1]['stage'])

    def test_denied_planning_admission_does_not_spend_an_astra_call(self):
        fixture = goal_fixtures.GoalTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.approve()
        fixture.state['settings']['joint_planning'] = True
        runner.planning.start(fixture.state)
        support.atomic_json(fixture.run/'state.json', fixture.state)
        snapshot = support.snapshot(fixture.root)
        self.submit(fixture)
        with patch.object(support, 'snapshot', return_value=snapshot), \
             patch.object(runner.processes, 'process_table', return_value={}), \
             patch.object(runner.subprocess, 'Popen') as launch:
            with self.assertRaises(support.Paused):
                runner.run_role(role='astra', prompt='Fixture only', sandbox='read-only', workspace=fixture.root,
                    run_dir=fixture.run, state=fixture.state, schema=runner.SCHEMA_DIR/'v2/astra-decision.schema.json',
                    model='fixture', allow_write=False, dry_run=False)
        launch.assert_not_called()
        self.assertEqual(0, fixture.state['planning']['astra_calls'])
        self.assertNotIn('active_stage', fixture.state)


if __name__ == '__main__':
    unittest.main()
