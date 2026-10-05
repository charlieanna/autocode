"""Issue #288: stop advice never names --grant-recovery unless the CLI will accept it."""
import copy
import unittest
from unittest.mock import patch

import autocode_recovery_grants as grants
import autocode_recovery_limits as limits
import autocode_resolver_runtime as resolver_runtime
import autocode_support as support


def _request(pause_status, scope='operational_exhaustion', request_id='req-1'):
    return {'request_id': request_id, 'scope': scope}


def _state_with_cause(pause_status, status='WAITING_FOR_USER', request_id='req-1'):
    return {
        'status': status,
        'workspace': '/tmp/unused-workspace',
        'resolver': {'human_escalations': {
            request_id: {'status': 'pending', 'identity': {'proposal': {'origin': {
                'pause_status': pause_status, 'stage': 'terra'}}}}},
        },
        'resolver_human_request': {'request_id': request_id, 'scope': 'operational_exhaustion'},
    }


class Eligibility(unittest.TestCase):
    def current(self, state):
        public = state.get('resolver_human_request')
        return dict(public) if public else None

    def test_timeout_pause_is_grantable(self):
        state = {'status': 'PAUSED_TIMEOUT_RECOVERY'}
        self.assertTrue(grants.eligible(state, current_request=self.current, count=3, maximum=3))

    def test_waiting_user_with_matching_origin_is_grantable(self):
        state = _state_with_cause('PAUSED_TIMEOUT_RECOVERY')
        self.assertTrue(grants.eligible(state, current_request=self.current, count=3, maximum=3))

    def test_spent_budget_on_any_operational_exhaustion_is_grantable(self):
        # #288: idle-limit / stage-cap exhaustion publishes a non-timeout origin;
        # the advertised grant must still be accepted once the budget is spent.
        state = _state_with_cause('PAUSED_TIME_LIMIT')
        self.assertTrue(grants.eligible(state, current_request=self.current, count=3, maximum=3))

    def test_unspent_budget_and_other_causes_are_not_grantable(self):
        state = _state_with_cause('PAUSED_TIME_LIMIT')
        self.assertFalse(grants.eligible(state, current_request=self.current, count=1, maximum=3))

    def test_no_operational_request_is_not_grantable_even_when_exhausted(self):
        state = {'status': 'WAITING_FOR_USER', 'resolver': {'human_escalations': {}}}
        self.assertFalse(grants.eligible(state, current_request=self.current, count=3, maximum=3))


class AdviceMatchesEligibility(unittest.TestCase):
    def current(self, state):
        public = state.get('resolver_human_request')
        return dict(public) if public else None

    def test_stop_reason_advertises_grant_only_when_eligible(self):
        self.assertIn('--grant-recovery', limits.stop_reason({}, 3, 3, allow_grant=True)[1])
        self.assertNotIn('--grant-recovery', limits.stop_reason({}, 3, 3, allow_grant=False)[1])
        self.assertIn('--resolver-response', limits.stop_reason({}, 3, 3, allow_grant=False)[1])

    def test_time_limit_pause_names_max_seconds_not_grant(self):
        text = limits.advice(allow_grant=False, pause_status='PAUSED_TIME_LIMIT')
        self.assertIn('--max-seconds', text)
        self.assertNotIn('--grant-recovery', text)
        text = limits.advice(allow_grant=False, pause_status='PAUSED_NO_PROGRESS')
        self.assertIn('--resolver-response', text)
        self.assertNotIn('--grant-recovery', text)
        self.assertNotIn('--max-seconds', text)

    def test_published_exhaustion_advertises_exactly_what_grant_accepts(self):
        class Runner:
            MAX_AUTOMATIC_RECOVERIES = 3
            @staticmethod
            def recovery_count(state):
                return state.get('automatic_recoveries_since_resume', 3)

        for pause_status, count, expect_grant in (
                ('PAUSED_TIMEOUT_RECOVERY', 3, True),
                ('PAUSED_TIME_LIMIT', 3, True),
                ('PAUSED_TIME_LIMIT', 1, False),
                ('PAUSED_NO_PROGRESS', 0, False),
        ):
            with self.subTest(pause_status=pause_status, count=count):
                state = {
                    'status': pause_status, 'workspace': '/tmp/unused-workspace',
                    'next_stage': 'terra', 'settings': {'limits': {}},
                    'automatic_recoveries_since_resume': count,
                    'automatic_timeout_recoveries': [], 'automatic_capacity_recoveries': [],
                    'automatic_permission_recoveries': [], 'stages': [],
                }
                run = __import__('pathlib').Path('/tmp/issue-288-advice-run')
                run.mkdir(exist_ok=True)
                with patch.object(resolver_runtime, '_operational_receipt',
                                  return_value='receipt-1'), \
                     patch.object(resolver_runtime.human, 'queue') as queue:
                    self.assertTrue(resolver_runtime.record_operational_exhaustion(
                        Runner, state, run, support.Paused(pause_status, 'budget spent')))
                request = queue.call_args.kwargs['request']
                decision = request['decision_needed']
                if expect_grant:
                    self.assertIn('--grant-recovery', decision)
                else:
                    self.assertNotIn('--grant-recovery', decision)
                    if pause_status == 'PAUSED_TIME_LIMIT':
                        self.assertIn('--max-seconds', decision)
                    else:
                        self.assertIn('--resolver-response', decision)
                self.assertTrue(state['stop_reason'].startswith('budget spent.'), state['stop_reason'])
                self.assertTrue(state['stop_reason'].endswith(decision), state['stop_reason'])
                # Whatever we just advertised, grant() agrees at this stop.
                published = {'request_id': 'req-later', 'scope': 'operational_exhaustion'}
                cause = pause_status
                self.assertEqual(
                    expect_grant,
                    grants.eligible(state, current_request=lambda s: published, count=count,
                                    maximum=3, issued=published, cause=cause))


class GrantStillAudited(unittest.TestCase):
    def test_grant_refuses_without_eligibility_and_records_with_it(self):
        from pathlib import Path
        run = Path('/tmp/issue-288-grant-audit')
        run.mkdir(exist_ok=True)
        state = _state_with_cause('PAUSED_TIME_LIMIT')
        events = []
        with self.assertRaisesRegex(ValueError, 'grant-recovery requires'):
            grants.grant(state, run, 1, previous_settings=None,
                         current_request=lambda s: s.get('resolver_human_request'),
                         count=1, maximum=3,
                         supersede=lambda s, r: events.append(r), persist=lambda p, s: None)
        state = _state_with_cause('PAUSED_TIME_LIMIT')
        grants.grant(state, run, 1, previous_settings=None,
                     current_request=lambda s: s.get('resolver_human_request'),
                     count=3, maximum=3,
                     supersede=lambda s, r: events.append(r), persist=lambda p, s: None)
        self.assertEqual(1, len(state['recovery_grants']))
        self.assertEqual(1, len([e for e in state['user_events'] if e.get('kind') == 'recovery_grant']))
        self.assertEqual(2, state['automatic_recoveries_since_resume'])


if __name__ == '__main__':
    unittest.main()


class BoundChangeSupersede(unittest.TestCase):
    def test_explicit_budget_flags_count_as_recovery(self):
        from types import SimpleNamespace
        import autocode_run_actions as run_actions
        self.assertFalse(run_actions.explicit_recovery_requested(SimpleNamespace(
            _explicit_budget_flags=set(), grant_recovery=None, retry_builder=None,
            retry_failed_stage=False, retry_report=None, abandon_stage=None,
            diagnose_failed_stage=False)))
        self.assertTrue(run_actions.explicit_recovery_requested(SimpleNamespace(
            _explicit_budget_flags={'max_seconds'}, grant_recovery=None, retry_builder=None,
            retry_failed_stage=False, retry_report=None, abandon_stage=None,
            diagnose_failed_stage=False)))

    def test_bound_flags_match_budget_kind_not_only_pause_status(self):
        # An operational-exhaustion request after burn-out may name a different
        # origin.pause_status than the bound the operator is raising (#301).
        relevant = {'PAUSED_TIMEOUT_RECOVERY': ()}  # status map alone would miss it
        budget_kind = 'max_seconds'
        bound_flags = ('max_seconds',)
        paused_for = 'PAUSED_TIMEOUT_RECOVERY'
        explicit = {'max_seconds'}
        self.assertFalse(any(f in explicit for f in relevant.get(paused_for, ())))
        self.assertTrue(any(f in explicit for f in set(relevant.get(paused_for, ())) | set(bound_flags)))


class QuotaStopAdviceNamesAbandon(unittest.TestCase):
    def test_inform_advice_includes_abandon_when_an_attempt_is_stuck(self):
        text = limits.advice(allow_grant=False, pause_status='PAUSED_BUDGET', attempt='001/terra-01')
        self.assertIn('--resolver-response', text)
        self.assertIn('--abandon-stage 001/terra-01', text)
        self.assertIn('autocode resume', text)
        self.assertNotIn('--grant-recovery', text)
        text = limits.advice(allow_grant=False, pause_status='PAUSED_BUDGET')
        self.assertNotIn('--abandon-stage', text)
