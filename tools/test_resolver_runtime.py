"""Runner boundary integration, with no live models or autonomous code writes."""
import copy
import unittest
from unittest.mock import patch

from . import test_autocode as base
from . import test_report_repair as repairs
from .goal_fixtures import approve_fixture

runner, support = base.runner, base.s


class ResolverRuntimeTests(unittest.TestCase):
    queue = repairs.RepairTests.queue

    def setUp(self):
        base.RetrofitTest.setUp(self)
        approve_fixture(self.state, runner.goals)

    def boundary(self):
        return runner.resolver_runtime.boundary(runner, self.state, self.run, self.root)

    def test_report_repair_gate_is_durable_idempotent_and_runner_owned(self):
        self.queue()
        original_goal = copy.deepcopy(self.state['goal_contract'])
        self.assertTrue(self.boundary())
        saved = support.read(self.run / 'state.json')
        outcome = saved['stages'][-1]
        self.assertTrue(outcome['runner_owned'])
        self.assertEqual('runner', outcome['engine'])
        self.assertEqual(0, outcome['runner_calls'])
        self.assertNotIn('command', outcome)
        self.assertEqual('retry', outcome['decision']['action'])
        self.assertEqual(1, outcome['receipt']['attempt'])
        self.state = saved
        self.boundary()
        self.assertEqual(len(saved['stages']), len(support.read(self.run / 'state.json')['stages']))
        self.assertEqual([1], list(self.state['resolver']['attempts'].values()))
        self.assertEqual(original_goal, self.state['goal_contract'])

    def test_execute_repair_reaches_resolver_before_only_report_launch(self):
        self.queue()
        with patch.object(runner, 'run_role', side_effect=RuntimeError('offline stop')) as launch:
            with self.assertRaisesRegex(RuntimeError, 'offline stop'):
                runner.execute_report_repair(self.state, self.run, self.root)
        self.assertTrue(self.state['stages'][-1]['runner_owned'])
        self.assertTrue(launch.call_args.kwargs['report_only'])
        self.assertFalse(launch.call_args.kwargs['allow_write'])

    def test_repeated_failure_caps_resolver_retry_after_restart(self):
        self.queue()
        self.boundary()
        entry = next(iter(self.state['failure_history'].values()))
        entry['count'] = 3
        support.atomic_json(self.run / 'state.json', self.state)
        self.state = support.read(self.run / 'state.json')
        with patch.object(runner, 'run_role') as launch, self.assertRaises(support.Paused) as caught:
            runner.execute_report_repair(self.state, self.run, self.root)
        self.assertEqual('PAUSED_REPEATED_FAILURE', caught.exception.status)
        self.assertEqual('escalate', self.state['stages'][-1]['decision']['action'])
        self.assertEqual(0, self.state['pending_report_repair']['attempts'])
        launch.assert_not_called()

    def test_resolver_attempt_budget_survives_reload(self):
        self.queue()
        self.boundary()
        self.state['pending_report_repair']['attempts'] = 1
        self.boundary()
        self.state = support.read(self.run / 'state.json')
        # Even if another path resets the local repair counter, a third distinct
        # request for the same blocker cannot reset the resolver's durable budget.
        self.state['pending_report_repair']['attempts'] = 0
        self.state['pending_report_repair']['error'] = 'different error wording'
        with self.assertRaises(support.Paused):
            self.boundary()
        self.assertEqual([2], list(self.state['resolver']['attempts'].values()))

    def test_permissions_goal_changes_and_untyped_blockers_remain_user_owned(self):
        for kind in ('permission', 'goal_change', 'clarification', 'blocker'):
            request = {'kind': kind, 'decision_needed': 'Make a material decision', 'impact': 'Changes work',
                       'discovered': 'Needs decision', 'options': ['yes', 'no'], 'proposed_delta': ''}
            runner.goals.wait_for_user(self.state, request)
            before = copy.deepcopy(self.state)
            self.boundary()
            self.assertEqual('WAITING_FOR_USER', self.state['status'])
            self.assertEqual(before['pending_questions'], self.state['pending_questions'])
            self.assertEqual(before['goal_contract'], self.state['goal_contract'])
            self.assertEqual('escalate', self.state['stages'][-1]['decision']['action'])
            self.assertFalse(self.state['stages'][-1]['receipt']['callbacks_used'])

    def test_no_resolver_during_active_operation_or_unapproved_goal(self):
        self.queue()
        self.state['active_stage'] = {'stage': 'terra'}
        before = copy.deepcopy(self.state)
        self.assertFalse(self.boundary())
        self.assertEqual(before, self.state)
        self.state.pop('active_stage')
        self.state['goal_contract']['approval_status'] = 'draft'
        self.assertFalse(self.boundary())
        self.assertNotIn('resolver', self.state)

    def test_corrupt_saved_ledger_pauses_before_launch(self):
        self.queue()
        self.state['resolver'] = {'cache': {'bad': []}}
        with patch.object(runner, 'run_role') as launch, self.assertRaises(support.Paused):
            runner.execute_report_repair(self.state, self.run, self.root)
        launch.assert_not_called()
        self.assertEqual(0, self.state['pending_report_repair']['attempts'])

    def test_blocked_validation_keeps_original_review_route_and_evidence(self):
        record = {'stage': 'sol', 'role': 'sol', 'iteration': 5, 'output': str(self.run / 'sol.json'),
                  'source_revision': support.snapshot(self.root)['revision']}
        support.atomic_json(self.run / 'sol.json', {'verdict': 'BLOCKED'})
        self.state['stages'].append(record)
        self.state.update(validation={'verdict': 'BLOCKED', 'output': record['output']}, next_stage='astra_review')
        self.boundary()
        self.assertEqual('astra_review', self.state['next_stage'])
        self.assertEqual('BLOCKED', self.state['validation']['verdict'])
        self.assertEqual('continue', self.state['stages'][-1]['decision']['action'])

    def test_plain_fail_does_not_record_resolver_or_consume_failure_budget(self):
        self.state.update(validation={'verdict': 'FAIL', 'output': str(self.run / 'sol.json')},
                          next_stage='astra_review')
        self.state['stages'].append({'stage': 'sol', 'output': str(self.run / 'sol.json')})
        before = copy.deepcopy(self.state)
        for _ in range(4):
            self.assertFalse(self.boundary())
        self.assertEqual(before, self.state)
