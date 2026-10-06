import copy
import json
from pathlib import Path
import unittest
import autocode_builder_policy as policy
import autocode_dispatch as dispatch
from . import test_build_blackbox as bb
from providers import command, opencode

CLAUDE_TOML = Path(__file__).resolve().parents[1] / 'examples' / 'claude-provider' / 'claude.toml'
CLAUDE_CONFIG = command.tomllib.loads(CLAUDE_TOML.read_text())
CLAUDE_PROVIDER = command.CommandProvider(CLAUDE_CONFIG, CLAUDE_TOML)


class PolicyTests(unittest.TestCase):
    def state(self):
        return {'settings': {'engine': 'codex', 'builder_retry': dict(policy.DEFAULTS),
                'roles': {'terra': {'model': 'gpt-6-luna', 'reasoning_effort': 'medium'}}},
                'goal_contract': {'hash': 'approved'},
                'current_task': {'id': 'task1', 'milestone_id': 'M1'}, 'status': 'RUNNING'}

    def test_retry_escalate_pause_is_durable_idempotent_and_bounded(self):
        state = self.state()
        self.assertEqual('retry', policy.failure(state, 'e1', 'failure'))
        state = json.loads(json.dumps(state))
        self.assertEqual('retry', policy.failure(state, 'e1', 'failure'))
        self.assertEqual('escalate', policy.failure(state, 'e2', 'failure'))
        self.assertEqual('gpt-6-sol', state['settings']['roles']['terra']['model'])
        self.assertEqual('pause', policy.failure(state, 'e3', 'failure'))
        with self.assertRaises(policy.s.Paused): policy.guard(state)
        self.assertEqual(3, len(state['builder_retry_decisions']))

    def test_pins_and_custom_providers_are_not_overridden(self):
        for engine in ('codex', 'opencode'):
            for override in ({'model_pinned': True}, {'provider': 'custom'}, {'provider': 'claude'}):
                with self.subTest(engine=engine, override=override):
                    state = self.state()
                    state['settings']['roles']['terra'].update(engine=engine, **override)
                    original = copy.deepcopy(state['settings']['roles']['terra'])
                    policy.failure(state, 'e1', 'failure')
                    self.assertEqual('pause', policy.failure(state, 'e2', 'failure'))
                    self.assertEqual(original, state['settings']['roles']['terra'])

    def test_native_strong_model_is_bare_and_preserves_transport(self):
        for strong in ('openai/gpt-6-sol', 'gpt-6-sol'):
            with self.subTest(strong=strong):
                state = self.state()
                state['settings']['builder_retry']['strong_model'] = strong
                state['settings']['roles']['terra'].update(engine='codex', provider='openai')
                self.assertEqual('retry', policy.failure(state, 'e1', 'failure'))
                self.assertEqual('escalate', policy.failure(state, 'e2', 'failure'))
                self.assertEqual({'model': 'gpt-6-sol', 'reasoning_effort': 'xhigh',
                                  'engine': 'codex', 'provider': 'openai'},
                                 state['settings']['roles']['terra'])
                self.assertEqual(strong, state['settings']['builder_retry']['strong_model'])
                self.assertEqual('gpt-6-sol', state['builder_retry_decisions'][-1]['selected_model'])

    def test_builder_role_engine_takes_precedence_over_run_engine(self):
        for run_engine, role_engine, expected in (
                ('opencode', 'codex', 'gpt-6-sol'),
                ('codex', 'opencode', 'openai/gpt-6-sol')):
            with self.subTest(run_engine=run_engine, role_engine=role_engine):
                state = self.state()
                state['settings']['engine'] = run_engine
                state['settings']['roles']['terra']['engine'] = role_engine
                policy.failure(state, 'e1', 'failure')
                self.assertEqual('escalate', policy.failure(state, 'e2', 'failure'))
                self.assertEqual(expected, state['settings']['roles']['terra']['model'])
                self.assertEqual(role_engine, state['settings']['roles']['terra']['engine'])
                self.assertEqual(run_engine, state['settings']['engine'])

    def test_native_normalization_does_not_strip_another_provider_prefix(self):
        state = self.state()
        state['settings']['builder_retry']['strong_model'] = 'custom/gpt-6-sol'
        policy.failure(state, 'e1', 'failure')
        self.assertEqual('escalate', policy.failure(state, 'e2', 'failure'))
        self.assertEqual('custom/gpt-6-sol', state['settings']['roles']['terra']['model'])

    def test_a_provider_that_lists_its_models_without_the_strong_one_retries_once_more_then_pauses(self):
        claude = CLAUDE_PROVIDER
        with self.assertRaisesRegex(ValueError, 'is not a claude model'):
            policy.configured('openai/gpt-6-sol', claude)
        self.assertEqual('claude-opus-5-5', policy.configured('claude-opus-5-5', claude)['strong_model'])
        state = self.state(); state['settings']['builder_retry'] = policy.configured(None, claude)
        self.assertEqual('retry', policy.failure(state, 'e1', 'failure'))
        self.assertEqual('retry', policy.failure(state, 'e2', 'failure'))
        policy.guard(state)
        self.assertEqual('pause', policy.failure(state, 'e3', 'failure'))
        self.assertEqual('gpt-6-luna', state['settings']['roles']['terra']['model'])
        self.assertIn('offers no stronger Builder model', state['stop_reason'])
        with self.assertRaises(policy.s.Paused): policy.guard(state)

    def test_providers_that_do_not_list_models_keep_the_default_strong_model(self):
        self.assertEqual(policy.DEFAULTS, policy.configured(None, opencode))
        unlisted = command.CommandProvider({**CLAUDE_CONFIG, 'models': None}, CLAUDE_TOML)
        self.assertEqual(policy.DEFAULTS['strong_model'], policy.configured(None, unlisted)['strong_model'])

    def test_new_milestone_restores_normal_route_and_new_budget(self):
        state = self.state(); policy.failure(state,'e1','f'); policy.failure(state,'e2','f')
        state['current_task'] = {'id':'task2','milestone_id':'M2'}
        policy.guard(state)
        self.assertEqual('gpt-6-luna',state['settings']['roles']['terra']['model'])
        self.assertEqual('retry',policy.failure(state,'e3','f'))

    def test_new_contract_preserves_operator_pinned_route(self):
        state = self.state()
        policy.guard(state)
        selected = {'model': 'openai/gpt-6-sol', 'reasoning_effort': 'max',
                    'engine': 'opencode', 'provider': None, 'model_pinned': True}
        state['settings']['roles']['terra'] = copy.deepcopy(selected)
        state['goal_contract']['hash'] = 'reapproved'
        state = json.loads(json.dumps(state))
        policy.guard(state)
        self.assertEqual(selected, state['settings']['roles']['terra'])
        self.assertEqual(selected, policy.lane(state)['initial_route'])

    def default_routes(self):
        state = self.state()
        state['settings']['engine'] = 'opencode'
        state['settings']['roles'] = {
            'terra': {'model': 'zai-coding-plan/glm-5.3', 'reasoning_effort': 'medium'},
            'sol': {'model': 'openai/gpt-6-sol', 'reasoning_effort': 'high'},
            'completion': {'model': 'openai/gpt-6-sol', 'reasoning_effort': 'medium'}}
        return state

    def test_escalated_builder_is_checked_by_glm_until_the_next_milestone(self):
        state = self.default_routes()
        dispatch.enforce_cross_model_verification(state)
        policy.failure(state, 'e1', 'f')
        self.assertEqual('escalate', policy.failure(state, 'e2', 'f'))
        roles = state['settings']['roles']
        self.assertEqual(('openai/gpt-6-sol', 'xhigh'), (roles['terra']['model'], roles['terra']['reasoning_effort']))
        self.assertEqual(('zai-coding-plan/glm-5.3', 'high'), (roles['sol']['model'], roles['sol']['reasoning_effort']))
        self.assertEqual(('zai-coding-plan/glm-5.3', 'medium'),
                         (roles['completion']['model'], roles['completion']['reasoning_effort']))
        self.assertEqual({'sol': 'zai-coding-plan/glm-5.3', 'completion': 'zai-coding-plan/glm-5.3'},
                         state['builder_retry_decisions'][-1]['checker_models'])
        dispatch.enforce_cross_model_verification(state)
        state = json.loads(json.dumps(state))
        state['current_task'] = {'id': 'task2', 'milestone_id': 'M2'}
        policy.guard(state)
        self.assertEqual(self.default_routes()['settings']['roles'], state['settings']['roles'])
        dispatch.enforce_cross_model_verification(state)

    def test_a_run_saved_before_checker_model_existed_still_moves_its_checkers(self):
        state = self.default_routes()
        del state['settings']['builder_retry']['checker_model']
        policy.failure(state, 'e1', 'f')
        self.assertEqual('escalate', policy.failure(state, 'e2', 'f'))
        roles = state['settings']['roles']
        self.assertEqual(policy.DEFAULTS['checker_model'], roles['sol']['model'])
        self.assertEqual(policy.DEFAULTS['checker_model'], roles['completion']['model'])
        dispatch.enforce_cross_model_verification(state)

    def test_checkers_are_not_moved_onto_the_strong_model_itself(self):
        state = self.default_routes()
        state['settings']['builder_retry']['checker_model'] = policy.DEFAULTS['strong_model']
        policy.failure(state, 'e1', 'f')
        self.assertEqual('escalate', policy.failure(state, 'e2', 'f'))
        self.assertNotIn('checker_models', state['builder_retry_decisions'][-1])
        with self.assertRaises(policy.s.Paused) as raised:
            dispatch.enforce_cross_model_verification(state)
        self.assertEqual('PAUSED_CROSS_MODEL', raised.exception.status)

    def test_a_new_run_refuses_a_strong_model_that_is_its_checker_model(self):
        self.assertEqual(policy.DEFAULTS, policy.configured())
        self.assertEqual('openai/gpt-6-luna', policy.configured('openai/gpt-6-luna')['strong_model'])
        with self.assertRaisesRegex(ValueError, 'checked by its own model'):
            policy.configured(policy.DEFAULTS['checker_model'])

    def test_a_climbed_checker_moves_to_a_glm_effort(self):
        state = self.default_routes()
        state['settings']['roles']['sol']['reasoning_effort'] = 'max'
        policy.failure(state, 'e1', 'f'); policy.failure(state, 'e2', 'f')
        self.assertEqual('high', state['settings']['roles']['sol']['reasoning_effort'])
        state['current_task'] = {'id': 'task2', 'milestone_id': 'M2'}
        policy.guard(state)
        self.assertEqual('max', state['settings']['roles']['sol']['reasoning_effort'])

    def test_a_pinned_checker_is_not_moved_and_the_cross_model_guard_still_pauses(self):
        state = self.default_routes()
        state['settings']['roles']['sol']['model_pinned'] = True
        policy.failure(state, 'e1', 'f')
        self.assertEqual('escalate', policy.failure(state, 'e2', 'f'))
        self.assertEqual('openai/gpt-6-sol', state['settings']['roles']['sol']['model'])
        self.assertEqual('zai-coding-plan/glm-5.3', state['settings']['roles']['completion']['model'])
        with self.assertRaises(policy.s.Paused) as raised:
            dispatch.enforce_cross_model_verification(state)
        self.assertEqual('PAUSED_CROSS_MODEL', raised.exception.status)

    def test_native_builder_and_pinned_opencode_checker_alias_still_pause(self):
        state = self.state()
        checker = {'model': 'openai/gpt-6-sol', 'reasoning_effort': 'high',
                   'engine': 'opencode', 'provider': 'openai', 'model_pinned': True}
        state['settings']['roles']['sol'] = copy.deepcopy(checker)
        dispatch.enforce_cross_model_verification(state)
        policy.failure(state, 'e1', 'failure')
        self.assertEqual('escalate', policy.failure(state, 'e2', 'failure'))
        self.assertEqual('gpt-6-sol', state['settings']['roles']['terra']['model'])
        self.assertEqual(checker, state['settings']['roles']['sol'])
        with self.assertRaises(policy.s.Paused) as raised:
            dispatch.enforce_cross_model_verification(state)
        self.assertEqual('PAUSED_CROSS_MODEL', raised.exception.status)
        self.assertIn('Builder/Tester', str(raised.exception))

    def test_native_builder_leaves_bare_checker_collision_for_guard(self):
        for override in ({}, {'model_pinned': True}, {'provider': 'custom'}):
            with self.subTest(override=override):
                state = self.state()
                checker = {'model': 'gpt-6-sol', 'reasoning_effort': 'high',
                           'engine': 'codex', **override}
                state['settings']['roles']['completion'] = copy.deepcopy(checker)
                policy.failure(state, 'e1', 'failure')
                self.assertEqual('escalate', policy.failure(state, 'e2', 'failure'))
                self.assertEqual(checker, state['settings']['roles']['completion'])
                self.assertNotIn('checker_models', state['builder_retry_decisions'][-1])
                with self.assertRaises(policy.s.Paused) as raised:
                    dispatch.enforce_cross_model_verification(state)
                self.assertEqual('PAUSED_CROSS_MODEL', raised.exception.status)
                self.assertIn('Builder/Completion Reviewer', str(raised.exception))

    def test_native_builder_still_swaps_compatible_opencode_checker(self):
        state = self.state()
        state['settings']['roles']['sol'] = {'model': 'openai/gpt-6-sol', 'reasoning_effort': 'medium',
                                           'engine': 'opencode', 'provider': 'openai'}
        policy.failure(state, 'e1', 'failure')
        self.assertEqual('escalate', policy.failure(state, 'e2', 'failure'))
        self.assertEqual('gpt-6-sol', state['settings']['roles']['terra']['model'])
        self.assertEqual({'model': policy.DEFAULTS['checker_model'], 'reasoning_effort': 'medium',
                          'engine': 'opencode', 'provider': 'openai'},
                         state['settings']['roles']['sol'])
        self.assertEqual({'sol': policy.DEFAULTS['checker_model']},
                         state['builder_retry_decisions'][-1]['checker_models'])
        dispatch.enforce_cross_model_verification(state)

    def test_a_parallel_builder_defers_its_stronger_attempt_to_the_parent(self):
        parent = self.default_routes()
        worker = copy.deepcopy(parent)
        worker['parent_run'] = '/runs/parent'
        policy.failure(worker, 'e1', 'f')
        self.assertEqual('defer', policy.failure(worker, 'e2', 'f'))
        self.assertEqual(self.default_routes()['settings']['roles'], worker['settings']['roles'])
        self.assertEqual(policy.SERIAL, worker['status'])
        self.assertIn('makes that attempt serially', worker['stop_reason'])
        # Restarting or retrying the worker cannot make the attempt there.
        worker = json.loads(json.dumps(worker))
        with self.assertRaises(policy.s.Paused) as raised:
            policy.guard(worker)
        self.assertEqual(policy.SERIAL, raised.exception.status)
        # The parent's own lane for the milestone was opened before the batch ran.
        policy.guard(parent)
        policy.adopt(parent, worker)
        policy.adopt(parent, worker)
        self.assertEqual(['retry', 'defer'], [d['action'] for d in parent['builder_retry_decisions']])
        self.assertTrue(policy.failed_before(parent, 'M1'))
        self.assertFalse(policy.failed_before(parent, 'M2'))
        parent = json.loads(json.dumps(parent))
        policy.guard(parent)
        roles = parent['settings']['roles']
        self.assertEqual(('openai/gpt-6-sol', 'xhigh'), (roles['terra']['model'], roles['terra']['reasoning_effort']))
        self.assertEqual('zai-coding-plan/glm-5.3', roles['sol']['model'])
        self.assertEqual('zai-coding-plan/glm-5.3', roles['completion']['model'])
        dispatch.enforce_cross_model_verification(parent)
        policy.guard(parent)
        self.assertEqual(['retry', 'defer', 'escalate'], [d['action'] for d in parent['builder_retry_decisions']])
        # The budget carried over: the next failure pauses, and the next milestone restores the routes.
        self.assertEqual('pause', policy.failure(parent, 'e3', 'f'))
        parent['current_task'] = {'id': 'task2', 'milestone_id': 'M2'}
        policy.guard(parent)
        self.assertEqual(self.default_routes()['settings']['roles'], parent['settings']['roles'])

    def test_a_deferred_attempt_does_not_override_a_builder_pinned_since(self):
        parent, worker = self.default_routes(), self.default_routes()
        worker['parent_run'] = '/runs/parent'
        policy.failure(worker, 'e1', 'f'); policy.failure(worker, 'e2', 'f')
        policy.adopt(parent, worker)
        parent['settings']['roles']['terra']['model_pinned'] = True
        with self.assertRaises(policy.s.Paused) as raised:
            policy.guard(parent)
        self.assertEqual('PAUSED_BUILDER_RETRY_LIMIT', raised.exception.status)
        self.assertEqual(self.default_routes()['settings']['roles']['sol'], parent['settings']['roles']['sol'])
        self.assertEqual('zai-coding-plan/glm-5.3', parent['settings']['roles']['terra']['model'])

    def test_configured_strong_model_retains_opencode_transport(self):
        state=self.state(); state['settings']['engine']='opencode'
        state['settings']['builder_retry']['strong_model']='gpt-6-sol'
        policy.failure(state,'e1','f'); policy.failure(state,'e2','f')
        self.assertEqual('openai/gpt-6-sol',state['settings']['roles']['terra']['model'])
        self.assertEqual('xhigh',state['settings']['roles']['terra']['reasoning_effort'])


class PolicyBlackbox(unittest.TestCase):
    setUp = bb.BuildBlackbox.setUp
    command = bb.BuildBlackbox.command
    invoke = bb.BuildBlackbox.invoke
    seed = bb.BuildBlackbox.seed
    state = bb.BuildBlackbox.state
    events = bb.BuildBlackbox.events
    build = bb.BuildBlackbox.build
    candidate = bb.BuildBlackbox.candidate

    def test_ordinary_retry_keeps_luna_and_successful_siblings(self):
        self.seed(); self.env['BUILD_AUDIT_FAULT']='retry_success'; self.build(); self.candidate()
        self.assertEqual(['gpt-6-luna']*2,[r['model'] for r in self.events() if r['milestone']=='M1'])
        self.assertEqual(1,len([r for r in self.events() if r['milestone']=='M2']))

    def test_strong_retry_uses_sol_high_and_integration_still_requires_review(self):
        self.seed(); self.env['BUILD_AUDIT_FAULT']='escalate_success'; self.build(); self.candidate()
        strong = 'gpt-6-sol'
        self.assertEqual(['gpt-6-luna','gpt-6-luna',strong],[r['model'] for r in self.events() if r['milestone']=='M1'])
        self.assertNotEqual('TASK_COMPLETE',self.state()['status'])

    def after_parallel_pair(self):
        """M1 and M2 can be built in parallel; M3 needs both. The checkers run the strong model."""
        spec = bb.independent()
        spec['contract']['milestones'][2]['depends_on'] = ['M1', 'M2']
        self.seed(spec, checker=policy.DEFAULTS['strong_model'])
        self.env['BUILD_AUDIT_FAULT'] = 'escalate_success'
        return self.state()['settings']['roles']

    def review(self):
        self.invoke('autoreview', ['--run-dir', str(self.run), '--no-chat'])

    def models(self, milestone):
        return [e['model'] for e in self.events() if e['milestone'] == milestone]

    def checkers(self, stage):
        return [(e['milestone'], e['model']) for e in self.events(stage=stage)]

    def test_parallel_strong_retry_runs_serially_after_its_siblings_and_another_model_checks_it(self):
        strong, glm = policy.DEFAULTS['strong_model'], policy.DEFAULTS['checker_model']
        routes = self.after_parallel_pair()
        self.build()
        # M2 is integrated on its own; M1 left its stronger attempt to the parent run.
        self.assertEqual(1, len(self.candidate()['implementation']['builder_reports']))
        batch = self.state()['orchestration_history'][-1]
        self.assertEqual(['M1'], batch['deferred'])
        self.assertEqual({'M1': policy.SERIAL, 'M2': 'BUILT'}, {w['milestone_id']: w['status'] for w in batch['workers']})
        self.assertFalse((self.project / 'server/health.py').exists())
        self.review()
        self.build(); self.candidate()
        self.assertEqual(['gpt-6-luna', 'gpt-6-luna', 'gpt-6-sol'], self.models('M1'))
        self.assertEqual(['retry', 'defer', 'escalate'], [d['action'] for d in self.state()['builder_retry_decisions']])
        self.review()
        self.build(); self.candidate()
        self.assertEqual(['gpt-6-luna'], self.models('M3'))
        self.assertEqual(routes, self.state()['settings']['roles'])
        self.review()
        # No model checked its own work: GPT-6 Sol checked Luna's builds, GLM checked GPT-6 Sol's.
        for stage in ('sol', 'astra_review'):
            self.assertEqual([('M2', strong), ('M1', glm), ('M3', strong)], self.checkers(stage))

    def test_when_every_parallel_builder_defers_the_parent_builds_each_serially(self):
        strong, glm = 'gpt-6-sol', policy.DEFAULTS['checker_model']
        self.after_parallel_pair()
        self.env['BUILD_AUDIT_FAULT_MILESTONES'] = 'M1,M2'
        self.build(); self.candidate()
        batch = self.state()['orchestration_history'][-1]
        self.assertEqual(('DEFERRED', ['M1', 'M2']), (batch['status'], batch['deferred']))
        self.assertEqual((['gpt-6-luna', 'gpt-6-luna', strong], ['gpt-6-luna'] * 2), (self.models('M1'), self.models('M2')))
        self.review()
        self.build(); self.candidate()
        self.assertEqual(['gpt-6-luna', 'gpt-6-luna', strong], self.models('M2'))
        self.review()
        for stage in ('sol', 'astra_review'):
            self.assertEqual([('M1', glm), ('M2', glm)], self.checkers(stage))

    def test_exhaustion_resume_cannot_reset_budget_or_claim_built(self):
        self.seed(); self.env['BUILD_AUDIT_FAULT']='retry_exhausted'; self.build(2)
        before = self.events()
        worker=next(w for w in self.state()['orchestration_batch']['workers'] if w['milestone_id']=='M1')
        child=json.loads((Path(worker['run_dir'])/'state.json').read_text())
        self.assertEqual('PAUSED_BUILDER_RETRY_LIMIT',child['status'])
        self.assertEqual(['retry','escalate','pause'],[r['action'] for r in child['builder_retry_decisions']])
        self.assertNotIn('implementation',child)
        self.build(2,extra=['--resume-paused','--retry-builder','M1'])
        self.assertEqual(before,self.events())
        self.assertNotIn('autocode',self.state().get('unit_handoffs',{}))

    def test_independent_failure_resolver_retries_escalates_then_pauses(self):
        spec=bb.plan([([], 'MAX_RETRIES must be 3', ['retry.py'])],
            {'M1': {'retry.py':'MAX_RETRIES = 5\n'}},
            {'M1':'from retry import MAX_RETRIES; assert MAX_RETRIES == 3'},
            'Preserve exactly three retries')
        self.seed(spec); self.env['BUILD_AUDIT_FAULT']='validation_fails'
        for attempt in range(3):
            if attempt == 2:
                previous = self.events()
                self.build(2)
                self.assertIn('No causal progress', self.state()['stop_reason'])
                self.assertEqual(previous, self.events())
                self.build(extra=['--resume-paused', '--retry-failed-stage'])
            else:
                self.build()
            self.candidate()
            self.invoke('autoreview',['--run-dir',str(self.run),'--no-chat'])
            self.assertEqual('FAIL',self.state()['validation']['verdict'])
            args = ['--run-dir', str(self.run), '--no-chat']
            if attempt == 1:
                previous = self.events(stage='astra_resolve')
                self.invoke('autoresolver', args, 2)
                self.assertIn('No causal progress', self.state()['stop_reason'])
                self.assertEqual(previous, self.events(stage='astra_resolve'))
                args += ['--resume-paused', '--retry-failed-stage']
            self.invoke('autoresolver', args, 2 if attempt==2 else 0)
        self.assertEqual(['gpt-6-luna','gpt-6-luna','gpt-6-sol'],[r['model'] for r in self.events()])
        self.assertEqual('PAUSED_BUILDER_RETRY_LIMIT',self.state()['status'])
        self.assertEqual(['retry','escalate','pause'],[r['action'] for r in self.state()['builder_retry_decisions']])
        self.assertEqual(2, len(self.events(stage='astra_resolve')))

    def test_serial_noop_uses_same_bounded_policy(self):
        self.seed(bb.plan([([], 'Version prints 1', ['version.py'])],
            {'M1':{'version.py':'print(1)\n'}}, {'M1':'import version'}, 'Print version'))
        self.env['BUILD_AUDIT_FAULT']='retry_exhausted'
        self.build(2)
        self.assertEqual('PAUSED_BUILDER_RETRY_LIMIT',self.state()['status'])
        self.assertEqual(['gpt-6-luna','gpt-6-luna','gpt-6-sol'],[r['model'] for r in self.events()])
        self.assertNotIn('implementation',self.state())
        self.assertNotIn('autocode',self.state().get('unit_handoffs',{}))
