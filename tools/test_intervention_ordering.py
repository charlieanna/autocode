"""Deterministic lifecycle races using only isolated state and fake providers."""
import contextlib
import copy
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocode as runner
import autocode_goals as goals
import autocode_interventions as inbox
import autocode_support as support
import test_goals as fixtures


class InterventionOrderingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.GoalTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.approve()
        self.state = self.fixture.state
        self.workspace, self.run = self.fixture.root, self.fixture.run
        self.state['next_stage'] = 'terra'
        self.persist()

    def persist(self):
        support.atomic_json(self.run / 'state.json', self.state)

    def submit(self, request_id='change', kind='feedback'):
        return inbox.submit(self.workspace, self.run, request_id=request_id, kind=kind,
                            text='Keep this change' if kind == 'feedback' else '')

    def role(self):
        return runner.run_role(role='terra', prompt='Fixture', sandbox='workspace-write',
            workspace=self.workspace, run_dir=self.run, state=self.state,
            schema=runner.SCHEMA_DIR / 'v2/terra-report.schema.json', model='fixture', allow_write=True, dry_run=False)

    def test_explicit_resume_is_durable_and_retry_preserves_original_receipt(self):
        first = self.submit('pause', 'pause')
        runner.consume_interventions(self.state, self.run, self.workspace)
        self.fixture.state = self.state
        (self.run / 'pause-requested').touch()  # Stop before any provider, after explicit acknowledgement.
        self.assertEqual(2, self.fixture.invoke('--resume-paused'))
        self.state = self.fixture.state
        self.assertIsNotNone(self.state['applied_interventions'][0]['resumed_at'])
        retry = self.submit('pause', 'pause')
        self.assertTrue(retry['idempotent'])
        self.assertEqual(first['receipt'], retry['receipt'])

    def test_receipt_order_and_observed_contract_are_fresh_after_lock_acquisition(self):
        first = self.submit('first', 'pause')
        runner.consume_interventions(self.state, self.run, self.workspace)
        second = self.submit('second', 'pause')
        self.assertGreater(second['receipt']['order'], first['receipt']['order'])
        lock = inbox._locked_inbox
        @contextlib.contextmanager
        def update_before_lock(path):
            self.state['goal_contract']['revision'] += 1
            self.persist()
            with lock(path):
                yield
        with patch.object(inbox, '_locked_inbox', update_before_lock):
            third = self.submit('third', 'pause')
        self.assertEqual(goals.token(self.state['goal_contract']), third['receipt']['observed_goal_token'])

    def test_pause_and_feedback_effects_survive_each_state_commit_crash(self):
        initial = copy.deepcopy(self.state)
        real_write = runner.write_json
        for mixed in (False, True):
            for failing_write in (1, 2):
                with self.subTest(mixed=mixed, failing_write=failing_write):
                    self.state = copy.deepcopy(initial)
                    (self.run / inbox.INBOX_NAME).unlink(missing_ok=True)
                    self.persist()
                    self.submit('pause', 'pause')
                    if mixed:
                        self.submit('feedback')
                    count = 0
                    def crash(path, value):
                        nonlocal count
                        count += 1
                        if count == failing_write:
                            raise SystemExit('simulated process loss')
                        real_write(path, value)
                    with patch.object(runner, 'write_json', side_effect=crash):
                        try:
                            runner.consume_interventions(self.state, self.run, self.workspace)
                        except SystemExit:
                            pass
                    disk = support.read(self.run / 'state.json')
                    if disk.get('applied_interventions'):
                        self.assertEqual('PAUSED_INTERVENTION', disk['status'])
                        self.assertIsNone(disk['pause_intent']['acknowledged_at'])
                    runner.consume_interventions(disk, self.run, self.workspace)
                    self.assertEqual('PAUSED_INTERVENTION', disk['status'])
                    self.assertIsNone(disk['pause_intent']['acknowledged_at'])
                    self.assertNotIn('intervention_ack_pending', disk)
                    self.assertEqual(2 if mixed else 1, len(disk['applied_interventions']))
                    self.assertEqual(1 if mixed else 0, len(disk.get('brief_feedback', [])))
                    self.assertEqual([], inbox.inspect(self.workspace, self.run)['requests'])

    def test_crash_after_effect_commit_before_inbox_clear_recovers_without_replay(self):
        self.submit('pause', 'pause')
        self.submit('feedback')
        original = support.atomic_json
        def crash_on_acknowledgement(path, value):
            if path == self.run / inbox.INBOX_NAME:
                raise SystemExit('simulated process loss before inbox acknowledgement')
            original(path, value)
        with patch.object(support, 'atomic_json', side_effect=crash_on_acknowledgement):
            with self.assertRaises(SystemExit):
                runner.consume_interventions(self.state, self.run, self.workspace)
        disk = support.read(self.run / 'state.json')
        self.assertEqual('PAUSED_INTERVENTION', disk['status'])
        self.assertIsNone(disk['pause_intent']['acknowledged_at'])
        self.assertEqual(2, inbox.inspect(self.workspace, self.run)['pending_count'])
        self.assertEqual(2, len(disk['applied_interventions']))
        self.assertEqual(1, len(disk['brief_feedback']))
        runner.consume_interventions(disk, self.run, self.workspace)
        self.assertEqual([], inbox.inspect(self.workspace, self.run)['requests'])
        self.assertNotIn('intervention_ack_pending', disk)
        self.assertEqual(2, len(disk['applied_interventions']))
        self.assertEqual(1, len(disk['brief_feedback']))

    def test_feedback_winning_admission_leaves_no_active_request_or_launch(self):
        snapshot = support.snapshot(self.workspace)
        def submit_during_preparation(workspace):
            self.submit()
            return snapshot
        with patch.object(runner.processes, 'process_table', return_value={}), \
             patch.object(support, 'snapshot', side_effect=submit_during_preparation), \
             patch.object(runner.subprocess, 'Popen') as launch:
            with self.assertRaisesRegex(support.Paused, 'Queued intervention'):
                self.role()
        launch.assert_not_called()
        self.assertNotIn('active_stage', self.state)
        self.assertNotIn('active_stage', support.read(self.run / 'state.json'))
        self.assertEqual([], [p for p in (self.run / 'iterations').rglob('*') if p.is_file()])

    def test_submission_after_admission_is_available_during_provider_wait(self):
        attempted = threading.Event()
        accepted = threading.Event()
        errors = []
        class Child:
            pid = 1234567
            def poll(self): return 0
        def submitter():
            attempted.set()
            try:
                self.submit()
                accepted.set()
            except Exception as error:
                errors.append(error)
        worker = threading.Thread(target=submitter)
        def launch(*args, **kwargs):
            worker.start()
            self.assertTrue(attempted.wait(1))
            self.assertFalse(accepted.is_set())
            return Child()
        def wait(*args, **kwargs):
            worker.join(2)
            self.assertFalse(errors)
            self.assertTrue(accepted.is_set(), 'Admission lock was held across provider execution')
            raise KeyboardInterrupt()
        snapshot = support.snapshot(self.workspace)
        with patch.object(runner.processes, 'process_table', return_value={}), \
             patch.object(support, 'snapshot', return_value=snapshot), \
             patch.object(runner.subprocess, 'Popen', side_effect=launch), \
             patch.object(runner.processes, 'wait_for_stage', side_effect=wait):
            with self.assertRaisesRegex(support.Paused, 'interrupted'):
                self.role()
        self.assertEqual(1, inbox.inspect(self.workspace, self.run)['pending_count'])

    def test_cli_goal_approval_cannot_commit_feedback_accepted_during_validation(self):
        self.fixture.draft()
        goals.present(self.state)
        original = goals.approve
        def approve_then_submit(state, token):
            original(state, token)
            self.submit()
        with patch.object(goals, 'approve', side_effect=approve_then_submit):
            result = self.fixture.invoke('--approve-goal', self.state['displayed_goal'])
        self.assertEqual(2, result)
        self.assertFalse(goals.approved(self.fixture.state))
        self.assertEqual('PAUSED_INTERVENTION_PENDING', self.fixture.state['status'])

    def test_interactive_approval_does_not_hold_lock_over_input_and_rechecks_after_reply(self):
        self.fixture.draft()
        self.persist()
        def reply(_):
            self.submit()
            return 'yes'
        with patch('builtins.input', side_effect=reply):
            with self.assertRaisesRegex(support.Paused, 'Queued intervention'):
                runner.chat_checkpoint(self.state, self.run)
        self.assertFalse(goals.approved(self.state))

    def test_approval_committed_first_allows_later_submission_without_losing_it(self):
        self.fixture.draft()
        goals.present(self.state)
        self.persist()
        candidate = copy.deepcopy(self.state)
        goals.approve(candidate, candidate['displayed_goal'])
        runner.commit_user_action(self.state, candidate, self.run)
        self.submit()
        self.assertTrue(goals.approved(support.read(self.run / 'state.json')))
        runner.consume_interventions(self.state, self.run, self.workspace)
        self.assertFalse(goals.approved(self.state))
        self.assertEqual('astra_discovery', self.state['next_stage'])

    def test_completion_report_and_feedback_commit_as_one_paused_boundary(self):
        self.fixture.validation()
        self.persist()
        report = self.fixture.decision('COMPLETE')
        self.submit()
        writes = []
        original = runner.write_json
        def observe(path, value):
            if path == self.run / 'state.json':
                writes.append(copy.deepcopy(value))
            original(path, value)
        with patch.object(runner, 'write_json', side_effect=observe):
            runner.commit_stage_result(self.state, 'astra_review', report, {'output': 'completion'}, self.workspace, self.run)
        self.assertTrue(writes)
        self.assertTrue(all(s['status'] != 'TASK_COMPLETE' for s in writes))
        self.assertEqual('PAUSED_INTERVENTION', self.state['status'])
        self.assertEqual('astra_discovery', self.state['next_stage'])
        self.assertEqual('completion', self.state['stages'][-1]['output'])

    def test_artifact_approval_commit_rejects_earlier_feedback(self):
        self.fixture.approve(human=True)
        self.fixture.validation()
        runner.apply_result(self.state, 'astra_review', self.fixture.decision('COMPLETE'), {'output': 'review'}, self.workspace, self.run)
        goals.present(self.state)
        self.persist()
        candidate = copy.deepcopy(self.state)
        goals.approve_review(candidate, 'C1', candidate['displayed_review'], support.snapshot(self.workspace))
        self.submit()
        with self.assertRaises(support.Paused):
            runner.commit_user_action(self.state, candidate, self.run)
        self.assertTrue(goals.missing_human_reviews(self.state))


if __name__ == '__main__':
    unittest.main()
