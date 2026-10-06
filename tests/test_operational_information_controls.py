"""What AutoResolver's one re-evaluation of corrective information decides at each stop (#486).

Pure functions over a saved state, no processes: a stop that needs an operator control is held and
names only a control the CLI accepts there (#288); only an allow-list of causes outside the run
continues. The CLI paths are in test_operational_information (slow: it runs real processes).
"""
import unittest

import autocode as runner
import autocode_operational_information as information


class OperatorControlTests(unittest.TestCase):
    """A held decision names only a control the CLI accepts at that stop (#288)."""

    def flags(self, cause, **state):
        return information.operator_flags(state, cause)

    def test_each_operator_only_stop_names_its_control(self):
        self.assertEqual('--resume-paused --grant-recovery N', self.flags('PAUSED_TIMEOUT_RECOVERY'))
        self.assertEqual('--resume-paused --max-seconds N', self.flags('PAUSED_TIME_LIMIT'))
        self.assertEqual('--resume-paused --no-progress-limit N', self.flags('PAUSED_NO_PROGRESS'))
        self.assertEqual('--resume-paused --retry-failed-stage', self.flags('PAUSED_REPEATED_FAILURE'))
        lane = {'terra': {'action': 'pause', 'failures': ['out.json']}}
        self.assertEqual('--resume-paused --retry-builder M2', self.flags(
            'PAUSED_BUILDER_RETRY_LIMIT', next_stage='terra', builder_retries=lane,
            current_task={'milestone_id': 'M2'}))
        batch = {'status': 'BUILDING', 'workers': [{'milestone_id': 'M1', 'status': 'PAUSED_BUDGET'},
                                                   {'milestone_id': 'M2', 'status': 'COMPLETE'},
                                                   {'milestone_id': 'M3', 'status': 'FAILED'}]}
        self.assertEqual('--resume-paused --retry-builder M1 --retry-builder M3', self.flags(
            'PAUSED_ORCHESTRATOR_WORKER', next_stage='orchestrator', orchestration_batch=batch))

    def test_a_control_the_cli_would_refuse_is_not_named(self):
        self.assertIsNone(self.flags('PAUSED_REPEATED_FAILURE', pending_report_repair={'attempts': 2}))
        self.assertIsNone(self.flags('PAUSED_BUILDER_RETRY_LIMIT', next_stage='terra'))
        self.assertIsNone(self.flags('PAUSED_ORCHESTRATOR_WORKER', next_stage='terra',
                                     orchestration_batch={'status': 'BUILDING', 'workers': []}))
        self.assertIsNone(self.flags('PAUSED_MILESTONE_STALLED'))
        self.assertIsNone(self.flags('PAUSED_REPORT_REPAIR_LIMIT'))

    def test_only_a_known_information_cause_can_continue(self):
        decide = lambda cause, **state: information.decide(runner, state, '/run', '/ws', cause)
        self.assertEqual(('hold', None), decide('PAUSED_UNKNOWN_FUTURE_STOP')[::2])
        held = decide('PAUSED_RATE_LIMIT', active_stage={'iteration': 3, 'output': '/run/iterations/003/builder-01.json'})
        self.assertEqual(('hold', '--abandon-stage 003/builder-01'), held[::2])
        self.assertEqual('hold', decide('PAUSED_PROVIDER_UNCERTAIN', uncertain_artifacts=['/run/x'])[0])
        self.assertEqual(('continue', None), decide('PAUSED_RATE_LIMIT')[::2])

    def test_a_bound_the_run_set_itself_is_held(self):
        decide = lambda cause: information.decide(runner, {}, '/run', '/ws', cause)[0]
        for cause in ('PAUSED_REPORT_REPAIR_LIMIT', 'PAUSED_BUILDER_RETRY_LIMIT', 'PAUSED_MILESTONE_STALLED',
                      'PAUSED_REPEATED_FAILURE', 'PAUSED_NO_PROGRESS', 'PAUSED_TIMEOUT_RECOVERY'):
            self.assertEqual('hold', decide(cause), cause)

    def test_spent_report_repairs_are_held_behind_any_stop(self):
        # An admitted continuation renews no repair allowance, so it could only stop again: the
        # Resolver publishes spent repairs as PAUSED_RESOLVER, not only PAUSED_REPORT_REPAIR_LIMIT.
        def state(attempts):
            return {'settings': {'report_repair': {'max_attempts': 2}},
                    'pending_report_repair': {'attempts': attempts}}
        for cause in ('PAUSED_RESOLVER', 'PAUSED_RESOLVER_OPERATIONAL', 'PAUSED_RATE_LIMIT'):
            action, reason, flags, _ = information.decide(runner, state(2), '/run', '/ws', cause)
            self.assertEqual(('hold', None), (action, flags), cause)
            self.assertIn('report-only repairs for this attempt are spent', reason)
            self.assertEqual('continue', information.decide(runner, state(1), '/run', '/ws', cause)[0], cause)


if __name__ == '__main__':
    unittest.main()
