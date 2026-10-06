"""Issue #288: stop advice never names --grant-recovery unless the CLI will accept it."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_recovery_accounting as accounting
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
        # #448: information alone never admits a no-progress pause; the advice names its bound.
        held = {'no_progress_batches': 3, 'settings': {'limits': {'no_progress_batches': 3}}}
        text = limits.advice(allow_grant=False, pause_status='PAUSED_NO_PROGRESS', state=held,
                             cause=limits.NO_PROGRESS_CAUSE)
        self.assertIn('autocode resume --no-progress-limit N', text)
        self.assertNotIn('--resolver-response', text)
        self.assertNotIn('--grant-recovery', text)
        self.assertNotIn('--max-seconds', text)

    @staticmethod
    def escalation(minute, discovered, pause_status='PAUSED_NO_PROGRESS', status='consumed'):
        return {'status': status, 'issued_at': f'2026-10-05T10:{minute:02d}:00+00:00', 'identity': {'proposal': {
            'scope': 'operational_exhaustion', 'origin': {'pause_status': pause_status},
            'request': {'discovered': discovered}}}}

    def test_no_progress_bound_advice_is_for_a_pause_the_limit_caused(self):
        """Other holds share PAUSED_NO_PROGRESS (a recovery novelty hold names --retry-failed-stage).

        The cause, not the count, decides (#448): a limit saved above the count after the pause still
        needs acknowledging, and a novelty hold at or above the limit is not the limit's.
        """
        import autocode_run_view as run_view
        bound = limits.NO_PROGRESS_CAUSE + '. AutoResolver could not resolve no_progress.'
        novelty = 'Incident 0123 (check): no new information. --resume-paused --retry-failed-stage authorizes one attempt'
        note = 'AutoResolver received the human response; no execution, approval or additional allowance was authorized.'
        # Saved state sorts the ledger by request id; only issued_at orders the requests.
        republished = {'resolver': {'human_escalations': {
            'a-again': self.escalation(30, note),
            'b-plan': {'status': 'consumed', 'issued_at': '2026-10-05T09:00:00+00:00',
                       'identity': {'proposal': {'scope': 'goal_approval'}}},
            'c-first': self.escalation(10, limits.NO_PROGRESS_CAUSE)}}}
        for name, count, limit, extra, holds, saved in (
                ('at the limit', 3, 3, {'stop_reason': bound}, True, None),
                ('above the limit', 5, 3, {'stop_reason': bound}, True, None),
                ('raise saved after the pause', 3, 4, {'stop_reason': bound}, True, 4),
                ('cap removed after the pause', 3, 0, {'stop_reason': bound}, True, None),
                ('a response, a saved raise, a republished request and another response', 3, 4,
                 {'stop_reason': note, **republished}, True, 4),
                ('novelty hold at the limit', 3, 3, {'stop_reason': novelty}, False, None),
                ('novelty hold after a response', 5, 3, {'stop_reason': note, 'resolver': {'human_escalations': {
                    'a-earlier': self.escalation(5, limits.NO_PROGRESS_CAUSE),
                    'b-hold': self.escalation(20, novelty)}}}, False, None),
                ('owned workers', 5, 3, {'stop_reason': 'Reconcile owned active or uncertain workers before '
                                                         'recovery; do not restart them'}, False, None),
                ('another pause before the response', 3, 4, {'stop_reason': note, 'resolver': {'human_escalations': {
                    'a-first': self.escalation(10, limits.NO_PROGRESS_CAUSE),
                    'b-again': self.escalation(30, note),
                    'c-time': self.escalation(20, 'Saved active-time limit reached', 'PAUSED_TIME_LIMIT')}}},
                 False, None),
                ('no recorded reason', 3, 3, {}, False, None)):
            state = {'status': 'PAUSED_NO_PROGRESS', 'no_progress_batches': count,
                     'settings': {'limits': {'no_progress_batches': limit}}, **extra}
            with self.subTest(name):
                self.assertEqual(holds, limits.no_progress_bound_holds(state))
                text = limits.advice(allow_grant=False, pause_status='PAUSED_NO_PROGRESS', state=state)
                self.assertEqual(holds, '--no-progress-limit' in text, text)
                self.assertEqual(not holds, '--resolver-response' in text, text)
                if holds:
                    self.assertIn(f'above the retained count of {count} unchanged', text)
                    # Offered only where reasserting it is accepted: a saved limit that admits the count.
                    self.assertEqual(saved is not None, 'Reasserting the saved limit' in text, text)
                    if saved is not None:
                        self.assertIn(f'saved limit, {saved},', text)
                need = run_view.needs(state)
                self.assertEqual('resume', need['kind'])
                self.assertEqual('--resume-paused --no-progress-limit N' if holds else None, need.get('action'))
                self.assertEqual(count if holds else None, need.get('no_progress_batches'))
        # While a request is being published, its error is the cause; an AutoResolver note is not.
        state = {'no_progress_batches': 3, 'settings': {'limits': {'no_progress_batches': 3}}}
        self.assertTrue(limits.no_progress_bound_holds(state, limits.NO_PROGRESS_CAUSE))
        self.assertFalse(limits.no_progress_bound_holds(state, novelty))
        self.assertTrue(limits.no_progress_bound_holds({**state, 'stop_reason': note, **republished}, note))

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
                    'next_stage': 'terra', 'settings': {'limits': {'no_progress_batches': 3}},
                    'no_progress_batches': 3, 'automatic_recoveries_since_resume': count,
                    'automatic_timeout_recoveries': [], 'automatic_capacity_recoveries': [],
                    'automatic_permission_recoveries': [], 'stages': [],
                }
                run = __import__('pathlib').Path('/tmp/issue-288-advice-run')
                run.mkdir(exist_ok=True)
                # The build loop's own reason, which no_progress_bound_holds attributes to the limit.
                reason = limits.NO_PROGRESS_CAUSE if pause_status == 'PAUSED_NO_PROGRESS' else 'budget spent'
                with patch.object(resolver_runtime, '_operational_receipt',
                                  return_value='receipt-1'), \
                     patch.object(resolver_runtime.human, 'queue') as queue:
                    self.assertTrue(resolver_runtime.record_operational_exhaustion(
                        Runner, state, run, support.Paused(pause_status, reason)))
                request = queue.call_args.kwargs['request']
                decision = request['decision_needed']
                if expect_grant:
                    self.assertIn('--grant-recovery', decision)
                else:
                    self.assertNotIn('--grant-recovery', decision)
                    bound = {'PAUSED_TIME_LIMIT': '--max-seconds',
                             'PAUSED_NO_PROGRESS': '--no-progress-limit'}[pause_status]
                    self.assertIn(bound, decision)
                    self.assertNotIn('--resolver-response', decision)
                self.assertEqual(pause_status == 'PAUSED_NO_PROGRESS',
                                 'Acknowledge the pause with autocode resume --no-progress-limit N' in request['options'])
                self.assertTrue(state['stop_reason'].startswith(reason + '.'), state['stop_reason'])
                self.assertTrue(state['stop_reason'].endswith(decision), state['stop_reason'])
                # #511: none of these runs recorded a recovery, so none is claimed.
                self.assertIn('no automatic operational recovery ran', decision)
                self.assertNotIn('recorded operational', decision)
                # Whatever we just advertised, grant() agrees at this stop.
                published = {'request_id': 'req-later', 'scope': 'operational_exhaustion'}
                cause = pause_status
                self.assertEqual(
                    expect_grant,
                    grants.eligible(state, current_request=lambda s: published, count=count,
                                    maximum=3, issued=published, cause=cause))


    def test_unchanged_builder_batches_are_not_a_spent_budget_to_grant(self):
        # #511: three Builder batches without source changes and no recovery: no grant, and the
        # request says no recovery ran rather than "after 0 recorded operational recoveries".
        class Runner:
            MAX_AUTOMATIC_RECOVERIES = 3
            recovery_count = staticmethod(accounting.spent)

        state = {'status': 'PAUSED_BUILDER_RETRY_LIMIT', 'workspace': '/tmp/unused-workspace',
                 'next_stage': 'terra', 'settings': {'limits': {}}, 'no_progress_batches': 3, 'stages': []}
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        run = Path(temp.name)
        with patch.object(resolver_runtime, '_operational_receipt', return_value='receipt-1'), \
             patch.object(resolver_runtime.human, 'queue') as queue:
            self.assertTrue(resolver_runtime.record_operational_exhaustion(
                Runner, state, run,
                support.Paused('PAUSED_BUILDER_RETRY_LIMIT', 'Builder retry allowance used for M1')))
        decision = queue.call_args.kwargs['request']['decision_needed']
        self.assertNotIn('--grant-recovery', decision)
        self.assertIn('no automatic operational recovery ran', decision)
        state['automatic_timeout_recoveries'] = [{}]
        with patch.object(resolver_runtime, '_operational_receipt', return_value='receipt-2'), \
             patch.object(resolver_runtime.human, 'queue') as queue:
            resolver_runtime.record_operational_exhaustion(
                Runner, state, run,
                support.Paused('PAUSED_BUILDER_RETRY_LIMIT', 'Builder retry allowance used for M1'))
        self.assertIn('after 1 recorded operational recovery.', queue.call_args.kwargs['request']['decision_needed'])


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
        self.assertIn('then autocode resume', text)
        self.assertEqual(1, text.count('autocode resume'), 'resume once, after the abandon')
        self.assertNotIn('--grant-recovery', text)
        text = limits.advice(allow_grant=False, pause_status='PAUSED_BUDGET')
        self.assertNotIn('--abandon-stage', text)
