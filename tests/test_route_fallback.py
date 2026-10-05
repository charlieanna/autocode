"""Pure tests of the model-fallback policy (issue #184): no processes, no files."""
from __future__ import annotations

import copy
import itertools
import json
import re
import unittest

import autocode_builder_policy as builder_policy
import autocode_dispatch as dispatch
import autocode_route_fallback as rf
import autocode_support as support


QUOTA = [{'type': 'thread.started', 'thread_id': 'thread-1'},
         {'type': 'error', 'error': {'message': 'subscription usage limit reached'}}]
RATE = [{'type': 'thread.started', 'thread_id': 'thread-1'},
        {'type': 'turn.failed', 'error': {'message': '429 rate limit exceeded'}}]
ENTRY_KEYS = {'id', 'role', 'stage', 'status', 'from', 'to', 'events', 'attempt_id', 'at', 'task_id',
              'milestone_ids', 'milestone_key', 'contract_hash', 'skipped', 'plan', 'ended_at', 'end_reason'}
QUIET = lambda line: None  # noqa: E731
AZURE_THROTTLE = ('Requests to the ChatCompletions_Create Operation under Azure OpenAI API version 2024-10-21 have '
                  'exceeded token rate limit of your current OpenAI S0 pricing tier. Please retry after 6 seconds. '
                  'Please go here: https://aka.ms/oai/quotaincrease if you would like to further increase the '
                  'default rate limit.')


def codex_settings(**routes):
    roles = {'glm': {'model': 'gpt-5.6-terra', 'reasoning_effort': 'medium', 'provider': None},
             'plan_reviewer': {'model': 'gpt-6-astra', 'reasoning_effort': 'high', 'provider': None},
             'terra': {'model': 'gpt-5.6-terra', 'reasoning_effort': 'medium', 'provider': None},
             'sol': {'model': 'gpt-5.6-sol', 'reasoning_effort': 'high', 'provider': None},
             'completion': {'model': 'gpt-6-sol', 'reasoning_effort': 'medium', 'provider': None},
             'astra': {'model': 'gpt-6-astra', 'reasoning_effort': 'high', 'provider': None}}
    for role, model in routes.items():
        roles[role]['model'] = model
    return {'engine': 'codex', 'roles': roles}


def opencode_settings(**routes):
    settings = codex_settings(terra='zai-coding-plan/glm-5.3', sol='openai/gpt-6-sol', completion='openai/gpt-6-sol',
                              glm='zai-coding-plan/glm-5.3', plan_reviewer='openai/gpt-6-sol',
                              astra='openai/gpt-6-astra')
    settings['engine'] = 'opencode'
    for role, model in routes.items():
        settings['roles'][role]['model'] = model
    return settings


# A run on the Claude provider config (examples/claude-provider/claude.toml): engine opencode,
# a provider config that names its models bare, and lists them (its LISTED_MODELS).
CLAUDE_MODELS = ['claude-haiku-4-5-20251001', 'claude-sonnet-5-5', 'claude-opus-5-5']


def claude_settings():
    roles = {role: {'model': model, 'reasoning_effort': 'medium', 'provider': None}
             for role, model in (('astra', 'claude-opus-5-5'), ('terra', 'claude-haiku-4-5-20251001'),
                                 ('sol', 'claude-sonnet-5-5'), ('completion', 'claude-opus-5-5'),
                                 ('glm', 'claude-sonnet-5-5'), ('plan_reviewer', 'claude-opus-5-5'))}
    return {'engine': 'opencode', 'provider': 'claude', 'roles': roles}


def run_state(fallback=('sol=gpt-6-luna',), settings=None):
    settings = settings or codex_settings()
    if fallback:
        settings['route_fallback'] = rf.configure(list(fallback), settings)
    return {'settings': settings, 'goal_contract': {'hash': 'contract-1', 'revision': 1},
            'current_task': {'id': 'task-1', 'milestone_id': 'M1'}, 'sessions': {'sol': 'thread-1'},
            'stages': [], 'user_events': []}


def stopped(state, stage='sol', **extra):
    route_role = {'terra': 'terra', 'sol': 'sol', 'astra_review': 'completion',
                  'astra_checkpoint': 'completion'}.get(stage, stage)
    route = state['settings']['roles'].get(route_role) or {}
    record = {'stage': stage, 'role': 'astra' if stage in ('astra_review', 'astra_checkpoint') else route_role,
              'iteration': 1,
              'exit_code': 3, 'timed_out': False, 'task_id': state['current_task'].get('id'),
              'contract_hash': 'contract-1', 'events': f'/run/iterations/001/{stage}-01.jsonl',
              'launch_route': {key: route.get(key) for key in rf.ROUTE_KEYS}}
    if route_role != record['role']:
        record['route_role'] = route_role
    record.update(extra)
    return record


def move_to(state, milestone):
    state.setdefault('task_archive', []).append(state['current_task'])
    state['current_task'] = {'id': f'task-{milestone}', 'milestone_id': milestone}


class RouteFallbackTests(unittest.TestCase):
    def decide(self, state, record=None, *, events=QUOTA, raised='PAUSED_BUDGET', classified=None, **options):
        before = copy.deepcopy(state)
        options.setdefault('source_changed', False)
        result = rf.decide(state, record or stopped(state), raised=raised, classified=classified or raised,
                           events=events, **options)
        self.assertEqual(before, state, 'decide() must never mutate the run state')
        return result

    def switch(self, state, stage='sol', attempt='001/tester-01'):
        decision = self.decide(state, stopped(state, stage))
        self.assertEqual('switch', decision['action'], decision)
        return rf.record_switch(state, decision, attempt_id=attempt, events_path=f'/run/archived/{attempt}.jsonl')

    # --- configuration ------------------------------------------------------------------

    def test_parse_and_configure_codex(self):
        settings = codex_settings()
        before = copy.deepcopy(settings)
        config = rf.configure(['sol=gpt-6-luna', 'sol=openai/gpt-6-sol@medium'], settings)
        self.assertEqual({'version': 1, 'roles': {'sol': [
            {'model': 'gpt-6-luna', 'reasoning_effort': None, 'plan': None, 'billing': None},
            {'model': 'gpt-6-sol', 'reasoning_effort': 'medium', 'plan': None, 'billing': None}]}}, config)
        self.assertEqual(before, settings)
        self.assertIsNone(rf.configure([], settings))
        self.assertEqual([{'role': 'completion', 'model': 'm', 'reasoning_effort': 'xhigh'}],
                         rf.parse([' completion = m@xhigh ']))

    def test_configure_opencode(self):
        settings = opencode_settings()
        config = rf.configure(['sol=zai-coding-plan/glm-5.3-flash', 'terra=openai/gpt-6-luna@high'], settings)
        self.assertEqual({'model': 'zai-coding-plan/glm-5.3-flash', 'reasoning_effort': None,
                          'plan': 'Z.AI Coding Plan', 'billing': 'subscription'}, config['roles']['sol'][0])
        self.assertEqual('OpenAI', config['roles']['terra'][0]['plan'])
        self.assertEqual({'sol#fallback1': {'model': 'zai-coding-plan/glm-5.3-flash'},
                          'terra#fallback1': {'model': 'openai/gpt-6-luna'}}, rf.preflight_roles(config))
        self.assertEqual({}, rf.preflight_roles(rf.configure(['sol=gpt-6-luna'], codex_settings()), codex_settings()))

    def test_configure_provider_config(self):
        # A provider config names its own models: bare names it lists are accepted, its name is the plan.
        settings = claude_settings()
        before = copy.deepcopy(settings)
        config = rf.configure(['completion=claude-sonnet-5-5', 'sol=claude-opus-5-5@high'], settings,
                              offered=CLAUDE_MODELS)
        self.assertEqual({'version': 1, 'roles': {
            'completion': [{'model': 'claude-sonnet-5-5', 'reasoning_effort': None, 'plan': 'claude', 'billing': None}],
            'sol': [{'model': 'claude-opus-5-5', 'reasoning_effort': 'high', 'plan': 'claude', 'billing': None}]}},
            config)
        self.assertEqual(before, settings)
        settings['route_fallback'] = config
        lines = rf.creation_lines(settings)
        self.assertIn('  Completion Reviewer: claude-opus-5-5 -> claude-sonnet-5-5, medium (inherited) effort '
                      '(claude, billing unknown)', lines)
        self.assertIn('    Note: on the same plan as claude-opus-5-5 (claude); its quota may run out too', lines)
        self.assertEqual({'completion#fallback1': {'model': 'claude-sonnet-5-5'},
                          'sol#fallback1': {'model': 'claude-opus-5-5'}}, rf.preflight_roles(config, settings))
        # The Opus limit, then Sonnet: the Completion Reviewer switches on a bare name.
        state = run_state(['completion=claude-sonnet-5-5'], claude_settings())
        decision = self.decide(state, stopped(state, 'astra_review'))
        self.assertEqual(('switch', 'claude-sonnet-5-5', {'plan': 'claude', 'billing': None}),
                         (decision['action'], decision['to']['model'], decision['plan']))
        # A provider config listing models through models_command (kilocode) offers no list.
        kilocode = {**opencode_settings(), 'provider': 'kilocode'}
        self.assertEqual([{'model': 'openai/gpt-6-luna', 'reasoning_effort': None, 'plan': 'kilocode', 'billing': None}],
                         rf.configure(['sol=openai/gpt-6-luna'], kilocode)['roles']['sol'])
        for spec, text in (('sol=claude-opus-4', 'does not list'), ('sol=anthropic/claude-opus-5-5', 'does not list'),
                           ('sol=claude-sonnet-5-5', 'already the Tester model')):
            with self.subTest(spec=spec):
                with self.assertRaises(ValueError) as caught:
                    rf.configure([spec], claude_settings(), offered=CLAUDE_MODELS)
                self.assertIn(text, str(caught.exception))

    def test_configure_refusals(self):
        pinned = codex_settings()
        pinned['roles']['sol']['model_pinned'] = True
        continuous = {**codex_settings(), 'conversation_profile': 'continuous-v1'}
        cases = [
            (['glm=gpt-6-luna'], codex_settings(), None, 'plan_reviewer'),
            (['plan_reviewer=gpt-6-luna'], codex_settings(), None, 'Resolver'),
            (['astra=gpt-6-luna'], codex_settings(), None, 'not supported'),
            (['sol'], codex_settings(), None, 'ROLE=MODEL'),
            (['sol='], codex_settings(), None, 'name one model'),
            (['sol=gpt-6-luna@ultra'], codex_settings(), None, 'effort must be one of'),
            (['sol=zai-coding-plan/glm-5.3'], codex_settings(), None, 'billing route'),
            (['sol=glm-5.3'], opencode_settings(), None, 'provider/model'),
            (['sol=gpt-5.6-sol'], codex_settings(), None, 'already the Tester model'),
            (['sol=openai/gpt-5.6-sol'], codex_settings(), None, 'already the Tester model'),
            (['sol=gpt-6-luna', 'sol=openai/gpt-6-luna'], codex_settings(), None, 'listed twice'),
            (['sol=gpt-6-luna'], pinned, None, 'pinned'),
            (['sol=gpt-6-luna'], continuous, None, 'continuous-v1'),
            (['sol=c/x'], opencode_settings(), ['a/b', 'b/c'], 'does not list'),
        ]
        for specs, settings, offered, text in cases:
            with self.subTest(specs=specs, text=text):
                before = copy.deepcopy(settings)
                with self.assertRaises(ValueError) as caught:
                    rf.configure(specs, settings, offered=offered)
                self.assertIn(text, str(caught.exception))
                self.assertEqual(before, settings)

    def test_creation_lines_show_plans_and_warn_without_refusing(self):
        self.assertEqual([], rf.creation_lines(codex_settings()))
        settings = codex_settings(terra='glm-5.3')
        settings['route_fallback'] = rf.configure(['sol=glm-5.3-flash', 'sol=gpt-6-luna@low'], settings)
        lines = rf.creation_lines(settings)
        self.assertIn('  Tester: gpt-5.6-sol -> glm-5.3-flash, high (inherited) effort (same sign-in as the Tester)', lines)
        self.assertIn('  Tester: gpt-5.6-sol -> gpt-6-luna, low effort (same sign-in as the Tester)', lines)
        self.assertEqual(1, sum('skipped while the Builder runs glm-5.3' in line for line in lines))
        self.assertTrue(any('per-model limits' in line for line in lines))
        settings = opencode_settings(sol='zai-coding-plan/glm-5.3', terra='openai/gpt-6-luna')
        settings['route_fallback'] = rf.configure(['sol=zai-coding-plan/glm-5.3-flash'], settings)
        lines = rf.creation_lines(settings)
        self.assertIn('Z.AI Coding Plan, subscription', lines[1])
        self.assertTrue(any('same plan as zai-coding-plan/glm-5.3' in line for line in lines))

    # --- the decision -------------------------------------------------------------------

    def test_not_configured_is_inert(self):
        state = run_state(fallback=())
        before = copy.deepcopy(state)
        printed = []
        self.assertFalse(rf.enabled(state))
        self.assertEqual(('pause', 'not_configured'), tuple(self.decide(state).get(k) for k in ('action', 'reason')))
        self.assertIsNone(rf.dispatch_route(state, 'sol', 'sol', out=printed.append))
        self.assertIsNone(rf.active(state, 'sol'))
        self.assertEqual(state['settings']['roles'], rf.effective_routes(state))
        self.assertIsNone(rf.view(state))
        self.assertEqual([], rf.summary_lines(state))
        self.assertEqual([], printed)
        self.assertEqual(before, state)

    def test_budget_switches_tester(self):
        state = run_state()
        decision = self.decide(state)
        self.assertEqual(('switch', 'granted'), (decision['action'], decision['reason']))
        self.assertEqual({**state['settings']['roles']['sol'], 'engine': None, 'model': 'gpt-6-luna'}, decision['to'])
        self.assertEqual(builder_policy.key(state), decision['milestone_key'])
        self.assertEqual(([], ['M1'], 'task-1', 'contract-1'),
                         (decision['skipped'], decision['milestone_ids'], decision['task_id'], decision['contract_hash']))
        self.assertEqual('gpt-5.6-sol', decision['from']['model'])
        # The Completion Reviewer runs astra_review on its own route role.
        state = run_state(fallback=('completion=gpt-6-luna@high',))
        decision = self.decide(state, stopped(state, 'astra_review'))
        self.assertEqual(('switch', 'completion', 'astra_review'), (decision['action'], decision['role'], decision['stage']))
        self.assertEqual('high', decision['to']['reasoning_effort'])
        self.assertEqual("Tester: quota stop on gpt-5.6-sol (PAUSED_BUDGET); attempt archived; continuing on your "
                         "fallback gpt-6-luna for the rest of milestone M1", rf.message(self.decide(run_state())))

    def test_triggers_and_vetoes(self):
        def failed(text, **extra):
            return [{'type': 'thread.started'}, {'type': 'turn.failed', 'error': {'message': text}, **extra}]
        state = run_state()
        cases = [
            ({'raised': 'PAUSED_PROVIDER_CAPACITY'}, 'not_a_trigger'),
            ({'raised': 'PAUSED_PROVIDER_UNCERTAIN'}, 'not_a_trigger'),
            ({'raised': 'PAUSED_PLANNING_BUDGET'}, 'not_a_trigger'),
            ({'raised': 'PAUSED_MILESTONE_BUDGET'}, 'not_a_trigger'),
            ({'classified': 'PAUSED_RATE_LIMIT'}, 'status_mismatch'),
            ({'events': QUOTA + [{'type': 'turn.completed', 'usage': {}}]}, 'turn_completed'),
            ({'events': failed('model is at capacity; quota')}, 'capacity'),
            ({'events': failed('401 Unauthorized: invalid api key (quota project)')}, 'authentication'),
            ({'events': failed('quota check: please log in')}, 'authentication'),
            ({'events': failed('thinking budget exceeded')}, 'no_limit_marker'),
            # Azure OpenAI's per-minute 429 (support.failure_status reads it as PAUSED_BUDGET for the
            # quotaincrease link): a transient throttle keeps the role's one switch.
            ({'events': failed(AZURE_THROTTLE)}, 'no_limit_marker'),
            ({'events': [{'type': 'error', 'error': {'name': 'APIError', 'data': {
                'message': AZURE_THROTTLE, 'statusCode': 429, 'responseBody': AZURE_THROTTLE}}}]}, 'no_limit_marker'),
            ({'events': failed('output token limit', usage={'input_tokens': 14290}),
              'raised': 'PAUSED_RATE_LIMIT', 'prior_rate_limit_stops': 1}, 'no_limit_marker'),
            ({'events': failed('a 14290-token request exceeds the output token limit'),
              'raised': 'PAUSED_RATE_LIMIT', 'prior_rate_limit_stops': 1}, 'no_limit_marker'),
            ({'record': stopped(state, timed_out=True)}, 'not_stopped'),
            ({'record': stopped(state, exit_code=0)}, 'not_stopped'),
            ({'record': stopped(state, exit_code=None)}, 'not_stopped'),
            ({'record': stopped(state, cleanup_error='still alive')}, 'not_stopped'),
        ]
        for options, reason in cases:
            with self.subTest(reason=reason, options=options):
                options = dict(options)
                record = options.pop('record', None)
                decision = self.decide(state, record, **options)
                self.assertEqual(('pause', reason), (decision['action'], decision['reason']))
        for text in ('You exceeded your current quota, please check your plan and billing details.', 'Quota exceeded.',
                     '{"code": "insufficient_quota"}', 'quota_exceeded'):
            with self.subTest(text=text):
                self.assertEqual('granted', self.decide(state, events=failed(text))['reason'])
        self.assertEqual('error\n14290\nquota', rf.failure_text([{'type': 'error', 'usage': {'status': 429, 'note': 'quota'},
                                                            'error': {'code': 14290, 'message': 'Quota'}},
                                                           {'type': 'item.completed', 'text': 'rate limit'}]))

    def test_scope_refusals(self):
        def check(state, reason, record=None, **options):
            decision = self.decide(state, record, **options)
            self.assertEqual(('pause', reason), (decision['action'], decision['reason']))
        state = run_state()
        with self.subTest('report repair'):
            check(state, 'report_repair', stopped(state, 'sol_report_repair', report_only=True, original_stage='sol'))
        for stage, role in (('astra_challenge', 'plan_reviewer'), ('review_change', 'completion'),
                            ('astra_resolve', 'astra')):
            with self.subTest(stage=stage):
                check(state, 'stage_not_eligible', stopped(state, stage, role=role))
        with self.subTest('checkpoint on the Resolver route (a run without a completion route)'):
            check(state, 'stage_not_eligible', stopped(state, 'astra_checkpoint', route_role='astra'))
        with self.subTest('role not listed'):
            check(state, 'role_not_listed', stopped(state, 'terra'))
        changes = {
            'parallel_worker': lambda s: s.update(parent_run='/parent'),
            'pinned': lambda s: s['settings']['roles']['sol'].update(model_pinned=True),
            'continuous_profile': lambda s: s['settings'].update(conversation_profile='continuous-v1'),
            'no_milestone': lambda s: s.update(current_task={'id': 'task-1'}),
        }
        for reason, change in changes.items():
            with self.subTest(reason=reason):
                changed = run_state()
                change(changed)
                check(changed, reason, stopped(run_state()))
        with self.subTest('attempt ran on another route'):
            record = stopped(state)
            record['launch_route']['model'] = 'gpt-6-luna'
            check(state, 'route_drift', record)
        with self.subTest('source changed'):
            check(state, 'source_changed', source_changed=True)

    def test_rate_limit_needs_an_earlier_archived_stop(self):
        state = run_state()
        decision = self.decide(state, events=RATE, raised='PAUSED_RATE_LIMIT')
        self.assertEqual('rate_limit_first_stop', decision['reason'])
        self.assertEqual('switch', self.decide(state, events=RATE, raised='PAUSED_RATE_LIMIT',
                                               prior_rate_limit_stops=1)['action'])
        statuses = {}

        def archived(name, status, stage='sol', **extra):
            statuses[name] = status
            state['stages'].append(stopped(state, stage, events=name, rejected=True, **extra))
        state['task_archive'] = [{'id': 'task-0', 'milestone_id': 'M0'}, {'id': 'task-1a', 'milestone_id': 'M1'}]
        archived('counted', 'PAUSED_RATE_LIMIT')
        archived('same milestone, earlier rework task', 'PAUSED_RATE_LIMIT', task_id='task-1a')
        archived('another role', 'PAUSED_RATE_LIMIT', 'terra')
        archived('another milestone', 'PAUSED_RATE_LIMIT', task_id='task-0')
        archived('another contract', 'PAUSED_RATE_LIMIT', contract_hash='contract-0')
        archived('report repair', 'PAUSED_RATE_LIMIT', 'sol_report_repair', report_only=True)
        archived('quota archive', 'PAUSED_BUDGET')
        statuses['not archived'] = 'PAUSED_RATE_LIMIT'
        state['stages'].append(stopped(state, events='not archived'))
        before = copy.deepcopy(state)
        self.assertEqual(2, rf.prior_limit_stops(state, 'sol', 'PAUSED_RATE_LIMIT', classify=statuses.__getitem__))
        self.assertEqual(1, rf.prior_limit_stops(state, 'sol', 'PAUSED_BUDGET', classify=statuses.__getitem__))
        self.assertEqual(before, state)

    def test_candidate_order_and_family_skips(self):
        state = run_state(settings=codex_settings(terra='glm-5.3'))
        state['settings']['route_fallback'] = {'version': 1, 'roles': {'sol': [
            {'model': model, 'reasoning_effort': None, 'plan': None, 'billing': None}
            for model in ('glm-5.3-flash', 'GLM-5.3-Turbo', 'gpt-5.6-sol', 'gpt-6-luna')]}}
        decision = self.decide(state)
        self.assertEqual(('switch', 'gpt-6-luna'), (decision['action'], decision['to']['model']))
        self.assertEqual([{'model': 'glm-5.3-flash', 'reason': 'not_independent:terra'},
                          {'model': 'GLM-5.3-Turbo', 'reason': 'not_independent:terra'},
                          {'model': 'gpt-5.6-sol', 'reason': 'current_model'}], decision['skipped'])
        state = run_state(['sol=zai-coding-plan/glm-5.3-flash', 'sol=openai/gpt-6-luna'], opencode_settings())
        decision = self.decide(state)
        self.assertEqual(('openai/gpt-6-luna', [{'model': 'zai-coding-plan/glm-5.3-flash', 'reason': 'not_independent:terra'}]),
                         (decision['to']['model'], decision['skipped']))
        state = run_state(['sol=gpt-5.6-terra', 'sol=gpt-6-luna'])
        self.assertEqual([{'model': 'gpt-5.6-terra', 'reason': 'not_independent:terra'}], self.decide(state)['skipped'])
        state = run_state(['terra=gpt-6-sol', 'terra=gpt-6-luna'])
        decision = self.decide(state, stopped(state, 'terra'))
        self.assertEqual(([{'model': 'gpt-6-sol', 'reason': 'not_independent:completion'}], 'gpt-6-luna'),
                         (decision['skipped'], decision['to']['model']))
        state = run_state(['sol=glm-5.3-flash', 'sol=gpt-5.6-terra'], codex_settings(terra='glm-5.3'))
        state['stages'].append(stopped(state, 'terra', launch_route={'model': 'gpt-5.6-terra'}))
        decision = self.decide(state)
        self.assertEqual(('pause', 'no_candidate', None), (decision['action'], decision['reason'], decision['to']))
        self.assertEqual(['not_independent:terra', 'checks_earlier_builder_work'],
                         [row['reason'] for row in decision['skipped']])
        self.assertEqual("Tester: quota stop on gpt-5.6-sol; model fallback not used (no_candidate; skipped "
                         "glm-5.3-flash: not_independent:terra; gpt-5.6-terra: checks_earlier_builder_work)",
                         rf.message(decision))

    def test_effective_and_earlier_builder_models(self):
        state = run_state(['terra=gpt-6-luna', 'sol=gpt-6-luna', 'sol=gpt-5.6-terra', 'sol=gpt-6-astra'])
        self.switch(state, 'terra', '001/builder-01')
        self.assertEqual('gpt-6-luna', rf.effective_routes(state)['terra']['model'])
        self.assertEqual('gpt-5.6-terra', state['settings']['roles']['terra']['model'])
        decision = self.decide(state)
        self.assertEqual([{'model': 'gpt-6-luna', 'reason': 'not_independent:terra'},
                          {'model': 'gpt-5.6-terra', 'reason': 'exhausted_this_milestone'}], decision['skipped'])
        self.assertEqual('gpt-6-astra', decision['to']['model'])
        self.assertEqual('unavailable', self.decide(state, available=lambda model: False)['skipped'][-1]['reason'])

        state = run_state(['sol=gpt-6-luna', 'sol=gpt-6-astra'])
        state['stages'].append(stopped(state, 'terra', exit_code=0, launch_route={'model': 'gpt-6-luna'}))
        decision = self.decide(state)
        self.assertEqual([{'model': 'gpt-6-luna', 'reason': 'checks_earlier_builder_work'}], decision['skipped'])
        # Any Builder model of this run counts, also one from an earlier milestone or a parallel worker.
        state = run_state(['completion=gpt-6-astra', 'completion=gpt-6-luna'])
        state['stages'].append({'stage': 'terra', 'role': 'terra', 'task_id': 'task-0', 'worker_milestone': 'M0',
                                'command': ['codex', 'exec', '--model', 'openai/gpt-6-astra']})
        decision = self.decide(state, stopped(state, 'astra_review'))
        self.assertEqual(([{'model': 'gpt-6-astra', 'reason': 'checks_earlier_builder_work'}], 'gpt-6-luna'),
                         (decision['skipped'], decision['to']['model']))

    def test_independent_is_at_least_as_strict_as_dispatch(self):
        models = ['gpt-6-sol', 'openai/gpt-6-sol', 'OpenAI/gpt-6-sol', 'GPT-6-SOL', 'gpt-6-luna', 'gpt-5.6-terra',
                  'glm-5.3', 'glm-5.3-flash', 'GLM-5.3-Turbo', 'zai-coding-plan/glm-5.3', 'Zai-Coding-Plan/GLM-5.3',
                  'openai/glm-5.3', 'mimo-v2', 'MiMo-V2-Pro', 'xiaomi-token-plan-sgp/mimo-v2.6-pro',
                  'anthropic/claude-x', 'claude-x', 'qwen-plan/qwen3-coder']
        refused = 0
        for producer, checker in itertools.product(models, repeat=2):
            state = {'settings': {'roles': {'terra': {'model': producer}, 'sol': {'model': checker}}}}
            try:
                dispatch.enforce_cross_model_verification(state)
            except support.Paused:
                refused += 1
                with self.subTest(producer=producer, checker=checker):
                    self.assertFalse(rf.independent(producer, checker))
        self.assertGreater(refused, len(models))
        self.assertTrue(rf.independent('gpt-6-sol', 'gpt-6-luna'))
        self.assertTrue(rf.independent('openai/gpt-6-sol', 'gpt-5.6-sol'))
        self.assertFalse(rf.independent('openai/gpt-6-sol', 'GPT-6-SOL'))

    # --- recording and the overlay ------------------------------------------------------

    def test_record_switch_and_once_per_milestone(self):
        state = run_state(['sol=gpt-6-luna', 'terra=gpt-6-astra'])
        settings = copy.deepcopy(state['settings'])
        entry = self.switch(state)
        self.assertEqual(ENTRY_KEYS, set(entry))
        self.assertTrue(re.fullmatch(r'rf-[0-9a-f]{12}', entry['id']))
        self.assertEqual((None, None, '001/tester-01', '/run/archived/001/tester-01.jsonl'),
                         (entry['ended_at'], entry['end_reason'], entry['attempt_id'], entry['events']))
        self.assertEqual([entry], state['route_fallbacks'])
        self.assertNotIn('sol', state['sessions'])
        self.assertEqual(('sol', 'thread-1'), (state['session_rotations'][-1]['role'],
                                               state['session_rotations'][-1]['old_session']))
        self.assertEqual({'kind': 'route_fallback', 'actor': 'runner', 'id': entry['id'], 'role': 'sol', 'stage': 'sol',
                          'status': 'PAUSED_BUDGET', 'from_model': 'gpt-5.6-sol', 'to_model': 'gpt-6-luna',
                          'events': entry['events']},
                         {k: v for k, v in state['user_events'][-1].items() if k != 'at'})
        self.assertEqual(settings, state['settings'])
        decision = self.decide(copy.deepcopy(state) | {'route_fallbacks': []})
        replay = rf.record_switch(state, decision, attempt_id='001/tester-01', events_path='elsewhere')
        self.assertEqual(entry['id'], replay['id'])
        self.assertEqual(1, len(state['route_fallbacks']))
        self.assertEqual(1, len(state['user_events']))
        round_trip = json.loads(json.dumps(state))
        self.assertEqual(rf.dispatch_route(copy.deepcopy(state), 'sol', 'sol', out=QUIET),
                         rf.dispatch_route(round_trip, 'sol', 'sol', out=QUIET))

        self.assertEqual('already_switched', self.decide(state)['reason'])
        state['route_fallbacks'][0].update(ended_at='2026-10-04T00:00:00+00:00', end_reason='route_changed')
        self.assertEqual('already_switched', self.decide(state)['reason'])
        self.assertEqual('switch', self.decide(state, stopped(state, 'terra'))['action'])
        move_to(state, 'M2')
        self.assertEqual('switch', self.decide(state)['action'])
        with self.assertRaises(ValueError):
            rf.record_switch(state, {'action': 'pause'}, attempt_id='x', events_path='y')

    def test_dispatch_route_lifecycle(self):
        state = run_state()
        sol = copy.deepcopy(state['settings']['roles']['sol'])
        entry = self.switch(state)
        printed = []
        route = rf.dispatch_route(state, 'sol', 'sol', out=printed.append)
        self.assertEqual({**entry['to'], 'fallback_id': entry['id']}, route)
        self.assertEqual(route, rf.dispatch_route(state, 'sol_report_repair', 'sol', out=printed.append))
        self.assertIsNone(rf.dispatch_route(state, 'terra', 'terra', out=printed.append))
        self.assertIsNone(rf.dispatch_route(state, 'astra_review', 'completion', out=printed.append))
        self.assertIsNone(rf.dispatch_route(state, 'astra_checkpoint', 'completion', out=printed.append))
        self.assertEqual([], printed)
        self.assertEqual(sol, state['settings']['roles']['sol'])
        state['sessions']['sol'] = 'luna-thread'
        move_to(state, 'M2')
        self.assertIsNone(rf.active(state, 'sol'))
        self.assertIsNone(state['route_fallbacks'][0]['ended_at'], 'active() never mutates')
        self.assertIsNone(rf.dispatch_route(state, 'sol', 'sol', out=printed.append))
        self.assertEqual('milestone_complete', state['route_fallbacks'][0]['end_reason'])
        self.assertIsNotNone(state['route_fallbacks'][0]['ended_at'])
        self.assertNotIn('sol', state['sessions'])
        self.assertEqual(('sol', 'luna-thread', 'Model fallback ended: milestone complete'),
                         tuple(state['session_rotations'][-1][k] for k in ('role', 'old_session', 'reason')))
        self.assertEqual({'kind': 'route_fallback_ended', 'actor': 'runner', 'id': entry['id'], 'role': 'sol',
                          'reason': 'milestone_complete'}, {k: v for k, v in state['user_events'][-1].items() if k != 'at'})
        self.assertEqual(['Tester: back on gpt-5.6-sol (model fallback ended: milestone complete)'], printed)
        self.assertEqual(sol, state['settings']['roles']['sol'])
        ended = copy.deepcopy(state)
        rf.dispatch_route(state, 'terra', 'terra', out=printed.append)
        self.assertEqual(ended, state, 'an ended overlay is never ended twice')

    def test_supersede_and_malformed(self):
        changes = {
            'route_changed': lambda s: s['settings']['roles']['sol'].update(model='glm-5.3'),
            'pinned': lambda s: s['settings']['roles']['sol'].update(model_pinned=True),
            'list_changed': lambda s: s['settings']['route_fallback']['roles']['sol'][0].update(model='gpt-6-astra'),
        }
        for reason, change in changes.items():
            with self.subTest(reason=reason):
                state = run_state()
                self.switch(state)
                change(state)
                before = copy.deepcopy(state)
                self.assertIsNone(rf.active(state, 'sol'))
                self.assertEqual(state['settings']['roles'], rf.effective_routes(state))
                self.assertEqual(before, state)
                self.assertIsNone(rf.dispatch_route(state, 'sol', 'sol', out=QUIET))
                self.assertEqual(reason, state['route_fallbacks'][0]['end_reason'])
        state = run_state()
        self.switch(state)
        state['parent_run'] = '/parent'
        before = copy.deepcopy(state)
        self.assertIsNone(rf.dispatch_route(state, 'sol', 'sol', out=QUIET))
        self.assertIsNone(rf.active(state, 'sol'))
        self.assertEqual(before, state)
        for key, value in (('route_fallbacks', {}), ('route_fallbacks', [{'id': 'rf-1'}]),
                           ('settings', {**state['settings'], 'route_fallback': {'version': 2, 'roles': {}}}),
                           ('settings', {**state['settings'], 'route_fallback': {'version': 1, 'roles': {'glm': []}}})):
            broken = {**run_state(), key: value}
            for call in (lambda: rf.dispatch_route(broken, 'sol', 'sol', out=QUIET), lambda: rf.active(broken, 'sol'),
                         lambda: rf.decide(broken, stopped(run_state()), raised='PAUSED_BUDGET',
                                           classified='PAUSED_BUDGET', events=QUOTA, source_changed=False),
                         lambda: rf.view(broken)):
                with self.subTest(key=key, value=value):
                    with self.assertRaises(support.Paused) as caught:
                        call()
                    self.assertEqual(rf.STATUS, caught.exception.status)
                    self.assertIn('no provider will launch', str(caught.exception))

    # --- read side ----------------------------------------------------------------------

    def test_view_and_summary(self):
        self.assertIsNone(rf.view(run_state(fallback=())))
        state = run_state()
        self.assertEqual({'configured': {'sol': [{'model': 'gpt-6-luna', 'reasoning_effort': None, 'plan': None,
                                                  'billing': None}]},
                          'switches': [], 'active': {}, 'last_refusal': None}, rf.view(state))
        entry = self.switch(state)
        projection = rf.view(state)
        self.assertEqual(('Tester', True), (projection['switches'][0]['job'], projection['switches'][0]['active']))
        self.assertEqual({'sol': {'id': entry['id'], 'model': 'gpt-6-luna', 'since': entry['at'], 'stage': 'sol'}},
                         projection['active'])
        before = copy.deepcopy(state)
        projection['switches'][0]['to']['model'] = 'changed'
        projection['configured']['sol'].clear()
        self.assertEqual(before, state)
        self.assertEqual(["Model fallback: Tester gpt-5.6-sol -> gpt-6-luna after PAUSED_BUDGET at the Tester stage "
                          "(milestone M1); still active at completion."], rf.summary_lines(state))
        move_to(state, 'M2')
        projection = rf.view(state)
        self.assertEqual((False, {}), (projection['switches'][0]['active'], projection['active']))
        rf.dispatch_route(state, 'sol', 'sol', out=QUIET)
        refused = self.decide(state, source_changed=True)
        self.assertTrue(rf.record_refusal(state, refused, attempt_id='002/tester-01', events_path='/e.jsonl'))
        self.assertFalse(rf.record_refusal(state, refused, attempt_id='002/tester-01', events_path='/e.jsonl'))
        self.assertEqual(1, sum(e['kind'] == 'route_fallback_refused' for e in state['user_events']))
        last = rf.view(state)['last_refusal']
        self.assertEqual(('sol', 'Tester', 'sol', 'PAUSED_BUDGET', 'source_changed', [], '002/tester-01'),
                         tuple(last[k] for k in ('role', 'job', 'stage', 'status', 'reason', 'skipped', 'attempt_id')))
        lines = rf.summary_lines(state)
        self.assertEqual(["Model fallback: Tester gpt-5.6-sol -> gpt-6-luna after PAUSED_BUDGET at the Tester stage "
                          "(milestone M1); back on gpt-5.6-sol from the next milestone.",
                          'Model fallback not used for Tester: source_changed'], lines)
        for line in lines:
            self.assertIsNone(re.search(r'(?<![-\w])sol\b', line), line)
        with self.assertRaises(ValueError):
            rf.record_refusal(state, {'action': 'switch'}, attempt_id='x', events_path='y')

    def test_reviewer_routing_names_the_checkpoint_tester(self):
        # Under reviewer routing astra_checkpoint runs the Tester job on the completion route
        # (units.autoplanner.route_for), so a completion fallback applies and every line says Tester.
        state = run_state(fallback=('completion=gpt-6-luna',))
        state['settings']['workflow'] = {'mode': 'glm_first_v1'}
        decision = self.decide(state, stopped(state, 'astra_checkpoint'))
        self.assertEqual(('switch', 'completion', 'Tester'), (decision['action'], decision['role'], decision['job']))
        self.assertEqual('Tester: quota stop on gpt-6-sol (PAUSED_BUDGET); attempt archived; continuing on your '
                         'fallback gpt-6-luna for the rest of milestone M1', rf.message(decision))
        rf.record_switch(state, decision, attempt_id='001/tester-01', events_path='/e.jsonl')
        self.assertEqual('Tester', rf.view(state)['switches'][0]['job'])
        self.assertEqual(['Model fallback: Tester gpt-6-sol -> gpt-6-luna after PAUSED_BUDGET at the Tester stage '
                          '(milestone M1); still active at completion.'], rf.summary_lines(state))
        printed = []
        move_to(state, 'M2')
        rf.dispatch_route(state, 'astra_checkpoint', 'completion', out=printed.append)
        self.assertEqual(['Tester: back on gpt-6-sol (model fallback ended: milestone complete)'], printed)
        refused = self.decide(state, stopped(state, 'astra_checkpoint'), source_changed=True)
        self.assertEqual('Tester: quota stop on gpt-6-sol; model fallback not used (source_changed)', rf.message(refused))
        rf.record_refusal(state, refused, attempt_id='002/tester-01', events_path='/e.jsonl')
        self.assertEqual('Tester', rf.view(state)['last_refusal']['job'])
        # Without reviewer routing the same stage is the Completion Reviewer everywhere.
        state = run_state(fallback=('completion=gpt-6-luna',))
        self.assertEqual('Completion Reviewer', self.decide(state, stopped(state, 'astra_checkpoint'))['job'])


if __name__ == '__main__':
    unittest.main()
