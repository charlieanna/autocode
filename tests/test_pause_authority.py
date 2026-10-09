"""Which pause holds a run, and what its own authority is: autocode_pause_authority and the records
autocode_stop keeps for a pause an intervention interrupted (#379, #486).

Pure functions over saved state, no processes. The CLI behaviour they decide is driven end to end in
tests.test_operational_pause_authority (a slow module); docs/bugs/2026-10-06-operational-pause-authority.md.
"""
import unittest

import autocode_pause_authority as pause_authority
import autocode_stop as stop


class ChangesHeldBoundTests(unittest.TestCase):
    def test_only_the_flag_for_the_exhausted_bound_changes_it(self):
        cases = (('PAUSED_TIME_LIMIT', {}, 'max_seconds'),
                 ('PAUSED_ITERATION_LIMIT', {}, 'max_iterations'),
                 ('PAUSED_ITERATION_LIMIT', {}, 'unlimited_iterations'),
                 ('PAUSED_MILESTONE_TIME_LIMIT', {}, 'max_milestone_seconds'),
                 # Neither records a bound kind in its request: the pause names it (run_setup.load_locked).
                 ('PAUSED_MILESTONE_BUDGET', {}, 'max_milestone_seconds'),
                 ('PAUSED_NO_PROGRESS', {}, 'no_progress_limit'),
                 # A request after recovery burn-out names the bound it exhausted (#301).
                 ('PAUSED_RESOLVER_OPERATIONAL', {'kind': 'iteration_ceiling'}, 'max_iterations'))
        for pause, budget, flag in cases:
            with self.subTest(pause=pause, flag=flag):
                origin = {'pause_status': pause, **({'budget': budget} if budget else {})}
                self.assertTrue(pause_authority.changes_held_bound({flag}, origin))
                for other in ('max_stage_seconds', 'max_idle_seconds', 'max_tool_seconds'):
                    self.assertFalse(pause_authority.changes_held_bound({other}, origin), other)
                self.assertFalse(pause_authority.changes_held_bound(set(), origin))
        for pause in ('PAUSED_RATE_LIMIT', 'PAUSED_TIMEOUT_RECOVERY', 'PAUSED_RESOLVER_OPERATIONAL'):
            flags = {flag for flags in pause_authority.BUDGET_FLAGS.values() for flag in flags}
            self.assertFalse(pause_authority.changes_held_bound(flags, {'pause_status': pause}), pause)


class HeldPauseTests(unittest.TestCase):
    """The records behind the held pause: its cause, whether feedback is its authority, what an intervention keeps."""

    def test_held_cause_drops_the_advice_its_request_appended(self):
        def request(discovered, pause='PAUSED_RATE_LIMIT'):
            return {'identity': {'proposal': {'scope': 'operational_exhaustion', 'origin': {'pause_status': pause},
                                              'request': {'discovered': discovered}}}}
        state = {'stop_reason': 'Rate limited. AutoResolver could not resolve it. Provide information.',
                 'resolver': {'human_escalations': {'a': request('Rate limited'), 'b': request('Other', 'PAUSED_TIME_LIMIT'),
                                                    'c': request('Rate')}}}
        self.assertEqual('Rate limited', pause_authority.held_cause(state, 'PAUSED_RATE_LIMIT'))
        self.assertEqual(state['stop_reason'], pause_authority.held_cause(state, 'PAUSED_TIME_LIMIT'))
        state['stop_reason'] = 'AutoResolver received the human response; no execution was authorized.'
        self.assertEqual(state['stop_reason'], pause_authority.held_cause(state, 'PAUSED_RATE_LIMIT'))

    def test_feedback_acknowledges_only_a_pause_that_offers_it(self):
        # The planning budget, and the validation-only stop whose request names --feedback (tests.test_rework_cli).
        def asked(request, at, pause='PAUSED_RESOLVER', status='pending'):
            return {'status': status, 'issued_at': at, 'identity': {'proposal': {
                'scope': 'operational_exhaustion', 'origin': {'pause_status': pause}, 'request': request}}}
        stalled = {'kind': 'blocker', 'discovered': 'stalled', 'finding_ids': ['F1']}
        live = {'status': 'WAITING_FOR_USER', 'resolver_human_request': {'scope': 'operational_exhaustion',
                                                                         'request_id': 'b'},
                'resolver': {'human_escalations': {'a': asked({'discovered': 'older'}, '2026-10-06T01:00:00'),
                                                   'b': asked(stalled, '2026-10-06T02:00:00')}}}
        self.assertTrue(pause_authority.feedback_acknowledges(live, 'PAUSED_RESOLVER'))
        self.assertIsNone(pause_authority.feedback_refusal(live))
        withdrawn = {'status': 'PAUSED_RESOLVER', 'resolver': live['resolver']}  # queued input applied under it
        self.assertTrue(pause_authority.feedback_acknowledges(withdrawn, 'PAUSED_RESOLVER'))
        newer = {'status': 'PAUSED_RESOLVER', 'resolver': {'human_escalations': {
            **live['resolver']['human_escalations'], 'c': asked({'discovered': 'other'}, '2026-10-06T03:00:00')}}}
        self.assertFalse(pause_authority.feedback_acknowledges(newer, 'PAUSED_RESOLVER'))
        self.assertIn('does not acknowledge PAUSED_RESOLVER', pause_authority.feedback_refusal(newer))
        self.assertTrue(pause_authority.feedback_acknowledges({}, 'PAUSED_PLANNING_BUDGET'))
        self.assertIsNone(pause_authority.feedback_refusal({'status': 'PAUSED_INVALID_OUTPUT'}))

    def test_queued_feedback_holds_only_a_pause_it_does_not_acknowledge(self):
        for held, holds in (({'status': 'PAUSED_RATE_LIMIT', 'feedback': False}, True),
                            ({'status': 'PAUSED_RESOLVER', 'feedback': True}, False),
                            ({'status': 'PAUSED_PLANNING_BUDGET'}, False),
                            ({'status': 'PAUSED_INVALID_OUTPUT', 'feedback': False}, False)):
            with self.subTest(held=held):
                state = {'status': stop.STOP_STATUS, 'next_stage': 'astra_discovery'}
                stop.boundary_effects(state, [{'id': 'f', 'kind': 'feedback'}], lambda: 'now', held)
                self.assertEqual(holds, 'held_pause' in (state.get('pause_intent') or {}))
        # A pause in the same batch holds whatever the feedback would acknowledge (docs/interventions.md).
        state = {'status': stop.STOP_STATUS, 'next_stage': 'sol'}
        stop.boundary_effects(state, [{'id': 'p', 'kind': 'pause'}, {'id': 'f', 'kind': 'feedback'}], lambda: 'now',
                              {'status': 'PAUSED_RESOLVER', 'feedback': True})
        self.assertEqual(('PAUSED_RESOLVER', ['p']), (state['pause_intent']['held_pause']['status'],
                                                      state['pause_intent']['request_ids']))

    def test_the_stop_reason_promises_only_what_the_held_pause_requires(self):
        # Any pause is returned to, but only an operational one waits for its own authority: a plain
        # --resume-paused releases an abandoned stage at once (#486 review).
        for held, operational in (('PAUSED_RATE_LIMIT', True), ('PAUSED_STAGE_ABANDONED', False)):
            with self.subTest(held=held):
                state = {'status': held, 'next_stage': 'sol'}
                stop.boundary_effects(state, [{'id': 'p', 'kind': 'pause'}], lambda: 'now',
                                      {'status': held, 'stop_reason': 'earlier stop'})
                self.assertEqual(held, state['pause_intent']['held_pause']['status'])
                reason = state['stop_reason']
                self.assertTrue(reason.startswith('Queued pause was applied'), reason)
                self.assertIn(held, reason)
                self.assertIn('--resume-paused returns to', reason)
                self.assertEqual(operational, 'only its own authority releases it' in reason, reason)

    def test_a_later_intervention_keeps_the_pause_the_first_one_interrupted(self):
        held = {'status': 'PAUSED_RATE_LIMIT', 'stop_reason': 'Rate limited'}
        state = {'status': stop.STOP_STATUS, 'pause_intent': {'acknowledged_at': None, 'held_pause': held}}
        self.assertEqual(held, stop.interrupted_pause(state))
        state['pause_intent']['acknowledged_at'] = '2026-10-06T00:00:00+00:00'
        self.assertIsNone(stop.interrupted_pause(state))
        self.assertIsNone(stop.interrupted_pause({'status': 'RUNNING'}))
        self.assertEqual('PAUSED_TIME_LIMIT', stop.interrupted_pause({'status': 'PAUSED_TIME_LIMIT'})['status'])
        self.assertEqual(stop.STOP_STATUS, pause_authority.INTERVENTION_STATUS)

    def test_a_correction_is_refused_at_the_pause_an_intervention_interrupted(self):
        held = {'status': 'PAUSED_RATE_LIMIT', 'stop_reason': 'Rate limited'}
        state = {'status': pause_authority.INTERVENTION_STATUS, 'pause_intent': {'acknowledged_at': None,
                                                                                'held_pause': held}}
        self.assertIn('An edited goal does not acknowledge PAUSED_RATE_LIMIT',
                      pause_authority.correction_refusal(state, 'An edited goal'))
        self.assertIn('Queued feedback is applied under the pause', pause_authority.feedback_refusal(state))
        state['pause_intent']['acknowledged_at'] = '2026-10-07T00:00:00+00:00'  # resumed: nothing interrupted
        self.assertIsNone(pause_authority.correction_refusal(state, 'An edited goal'))
        for status in ('PAUSED_PLANNING_BUDGET', 'PAUSED_STAGE_ABANDONED', 'AWAITING_GOAL_APPROVAL'):
            self.assertIsNone(pause_authority.correction_refusal({'status': status}, 'An edited goal'), status)


if __name__ == '__main__':
    unittest.main()
