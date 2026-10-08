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
# A Claude config written before [builder_retry] existed.
OLDER_CLAUDE_CONFIG = {key: value for key, value in CLAUDE_CONFIG.items() if key != 'builder_retry'}
OLDER_CLAUDE_PROVIDER = command.CommandProvider(OLDER_CLAUDE_CONFIG, CLAUDE_TOML)


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
        self.assertIn('Execution failure:', state['stop_reason'])
        self.assertIn('human decision or replanning required', state['stop_reason'])
        with self.assertRaises(policy.s.Paused): policy.guard(state)
        self.assertEqual(3, len(state['builder_retry_decisions']))

    def test_nonexecution_classes_never_spend_or_mutate_the_builder_route(self):
        for kind, action in (('plan', 'replan'), ('operational', 'recover'), ('unknown', 'investigate')):
            with self.subTest(kind=kind):
                state = self.state()
                state['sessions'] = {'terra': 'retained-session'}
                original = copy.deepcopy(state)
                self.assertEqual(action, policy.failure(state, 'e1', 'observed failure', classification=kind))
                self.assertEqual(action, policy.failure(state, 'e2', 'observed failure', classification=kind))
                self.assertEqual(original['settings'], state['settings'])
                self.assertEqual(original['sessions'], state['sessions'])
                self.assertNotIn('builder_retries', state)
                self.assertEqual([kind, kind], [row['failure_class'] for row in state['builder_retry_decisions']])
                state = json.loads(json.dumps(state))
                self.assertEqual(action, policy.failure(state, 'e1', 'observed failure', classification=kind))
                self.assertEqual(2, len(state['builder_retry_decisions']))

    def test_classification_observations_do_not_reset_or_exhaust_execution_allowance(self):
        state = self.state()
        self.assertEqual('investigate', policy.failure(state, 'e1', 'ambiguous', classification='unknown'))
        self.assertEqual('retry', policy.failure(state, 'e1', 'verified code failure', classification='execution'))
        self.assertEqual('recover', policy.failure(state, 'e2', 'permission denied', classification='operational'))
        self.assertEqual('gpt-6-luna', state['settings']['roles']['terra']['model'])
        self.assertEqual('escalate', policy.failure(state, 'e3', 'verified code failure', classification='execution'))
        self.assertEqual(['e1', 'e3'], policy.lane(state)['failures'])
        state = json.loads(json.dumps(state))
        self.assertEqual('escalate', policy.failure(state, 'e3', 'verified code failure', classification='execution'))
        self.assertEqual(4, len(state['builder_retry_decisions']))

    def test_operational_classification_cannot_clear_existing_exhaustion(self):
        state = self.state()
        for evidence in ('e1', 'e2', 'e3'):
            policy.failure(state, evidence, 'execution failure')
        original = copy.deepcopy(state['builder_retries'])
        self.assertEqual('recover', policy.failure(state, 'e4', 'cleanup uncertain', classification='operational'))
        self.assertEqual(original, state['builder_retries'])
        with self.assertRaises(policy.s.Paused):
            policy.guard(state)

    def test_execution_replay_observes_current_gate_without_charging_again(self):
        state = self.state()
        self.assertEqual('retry', policy.failure(state, 'e1', 'code failure'))
        self.assertEqual('escalate', policy.failure(state, 'e2', 'code failure'))
        state = json.loads(json.dumps(state))
        self.assertEqual('escalate', policy.failure(state, 'e1', 'code failure'))
        self.assertEqual(2, len(state['builder_retry_decisions']))
        self.assertEqual('pause', policy.failure(state, 'e3', 'code failure'))
        self.assertEqual('pause', policy.failure(state, 'e1', 'code failure'))
        self.assertEqual(['e1', 'e2', 'e3'], policy.lane(state)['failures'])
        self.assertEqual(3, len(state['builder_retry_decisions']))

    def test_replayed_uncertainty_cannot_restore_any_execution_allowance(self):
        state = self.state()
        policy.failure(state, 'e1', 'ambiguous', classification='unknown')
        policy.failure(state, 'e1', 'verified code failure', classification='execution')
        policy.failure(state, 'e2', 'verified code failure', classification='execution')
        policy.failure(state, 'e3', 'verified code failure', classification='execution')
        state = json.loads(json.dumps(state))
        original = copy.deepcopy(state)
        self.assertEqual('investigate', policy.failure(state, 'e1', 'ambiguous', classification='unknown'))
        self.assertEqual(original, state)
        with self.assertRaises(policy.s.Paused):
            policy.guard(state)

    def test_unknown_class_name_cannot_mutate_authority(self):
        state = self.state()
        original = copy.deepcopy(state)
        with self.assertRaises(ValueError):
            policy.failure(state, 'e1', 'failure', classification='escalate')
        self.assertEqual(original, state)

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
        claude = OLDER_CLAUDE_PROVIDER
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
        unlisted = command.CommandProvider({**OLDER_CLAUDE_CONFIG, 'models': None}, CLAUDE_TOML)
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


class ToolProviderPolicyTests(unittest.TestCase):
    """A configured command tool (here the Claude example) names its models itself, verbatim."""

    def state(self, provider=CLAUDE_PROVIDER):
        def route(role, **extra):
            return {'engine': 'opencode', 'provider': None, 'model': provider.DEFAULT_MODELS[role],
                    'reasoning_effort': provider.DEFAULT_REASONING_EFFORTS[role], **extra}
        return {'settings': {'engine': 'opencode', 'provider': provider.NAME,
                             'builder_retry': policy.configured(None, provider),
                             'roles': {**{role: route(role) for role in ('astra', 'terra', 'sol', 'completion', 'glm')},
                                       'plan_reviewer': route('plan_reviewer', model_pinned=True)}},
                'goal_contract': {'hash': 'approved'},
                'current_task': {'id': 'task1', 'milestone_id': 'M1'}, 'status': 'RUNNING'}

    def test_the_builder_escalates_to_the_tools_stronger_model_and_the_validator_moves_until_the_next_milestone(self):
        state = self.state()
        before = copy.deepcopy(state['settings']['roles'])
        self.assertEqual('retry', policy.failure(state, 'e1', 'f'))
        self.assertEqual('escalate', policy.failure(state, 'e2', 'f'))
        roles = state['settings']['roles']
        self.assertEqual({**before['terra'], 'model': 'claude-sonnet-5-5', 'reasoning_effort': 'xhigh'}, roles['terra'])
        self.assertEqual({**before['sol'], 'model': 'claude-opus-5-5'}, roles['sol'])
        self.assertEqual(before['completion'], roles['completion'])
        self.assertEqual({'sol': 'claude-opus-5-5'}, state['builder_retry_decisions'][-1]['checker_models'])
        dispatch.enforce_cross_model_verification(state)
        CLAUDE_PROVIDER.check_models(roles)  # every route is a model the tool serves, spelled as it spells it
        self.assertEqual('pause', policy.failure(state, 'e3', 'f'))
        self.assertEqual('Execution failure: ' + policy.EXHAUSTED, state['stop_reason'])
        state = json.loads(json.dumps(state))
        state['current_task'] = {'id': 'task2', 'milestone_id': 'M2'}
        policy.guard(state)
        self.assertEqual(before, state['settings']['roles'])

    def test_a_parallel_tool_builder_defers_and_the_parent_escalates_with_the_validator_moved(self):
        parent = self.state()
        worker = copy.deepcopy(parent); worker['parent_run'] = '/runs/parent'
        policy.failure(worker, 'e1', 'f')
        self.assertEqual('defer', policy.failure(worker, 'e2', 'f'))
        self.assertIn('stronger model claude-sonnet-5-5,', worker['stop_reason'])
        policy.guard(parent); policy.adopt(parent, worker); policy.guard(parent)
        roles = parent['settings']['roles']
        self.assertEqual(('claude-sonnet-5-5', 'claude-opus-5-5'), (roles['terra']['model'], roles['sol']['model']))
        dispatch.enforce_cross_model_verification(parent)

    def test_a_checker_that_cannot_move_stops_the_escalation_before_any_route_changes(self):
        for name, change in (
                ('pinned Validator', lambda st: st['settings']['roles']['sol'].update(model_pinned=True)),
                ('custom-provider Validator', lambda st: st['settings']['roles']['sol'].update(provider='custom')),
                ('checker model is the strong model',
                 lambda st: st['settings']['builder_retry'].update(checker_model='claude-sonnet-5-5')),
                ('checker model no role uses (a run saved before checker_model existed)',
                 lambda st: st['settings']['builder_retry'].pop('checker_model')),
                ('Validator spells the strong model another way',
                 lambda st: st['settings']['roles']['sol'].update(model='openai/claude-sonnet-5-5'))):
            with self.subTest(name):
                state = self.state(); change(state)
                before = copy.deepcopy(state['settings']['roles'])
                policy.failure(state, 'e1', 'f')
                self.assertEqual('pause', policy.failure(state, 'e2', 'f'))
                self.assertEqual(before, state['settings']['roles'])
                self.assertNotIn('checker_models', state['builder_retry_decisions'][-1])
                self.assertIn('would be checked by its own model', state['stop_reason'])
                with self.assertRaises(policy.s.Paused) as raised:
                    policy.guard(state)
                self.assertEqual('PAUSED_BUILDER_RETRY_LIMIT', raised.exception.status)

    def test_a_new_tool_run_takes_its_policy_from_the_provider_config(self):
        self.assertEqual({**policy.DEFAULTS, 'strong_model': 'claude-sonnet-5-5', 'checker_model': 'claude-opus-5-5'},
                         policy.configured(None, CLAUDE_PROVIDER))
        with self.assertRaisesRegex(ValueError, 'checked by its own model'):
            policy.configured('claude-opus-5-5', CLAUDE_PROVIDER)
        with self.assertRaisesRegex(ValueError, 'is not a claude model'):
            policy.configured('openai/gpt-6-sol', CLAUDE_PROVIDER)
        self.assertEqual('claude-haiku-4-5-20251001',
                         policy.configured('claude-haiku-4-5-20251001', CLAUDE_PROVIDER)['strong_model'])

    def test_a_tool_config_without_a_builder_retry_table_keeps_todays_policy(self):
        older = OLDER_CLAUDE_PROVIDER
        self.assertEqual({**policy.DEFAULTS, 'strong_model': None}, policy.configured(None, older))
        state = self.state(older)
        self.assertEqual(['retry', 'retry', 'pause'], [policy.failure(state, f'e{n}', 'f') for n in (1, 2, 3)])
        self.assertEqual('Execution failure: ' + policy.NO_STRONG_MODEL, state['stop_reason'])
        # The pause names the fix that works on such a tool: the flag alone would move a checker to GLM.
        self.assertIn('[builder_retry] in the provider config', state['stop_reason'])
        self.assertNotIn('--builder-strong-model', state['stop_reason'])

    def test_a_tool_without_a_builder_retry_table_creates_runs_as_before(self):
        # A listed tool that serves GPT-6 Sol but not GLM, with no checker on GPT-6 Sol, still starts runs.
        listed = command.CommandProvider({**OLDER_CLAUDE_CONFIG, 'name': 'oaitool',
                                          'models': [*OLDER_CLAUDE_CONFIG['models'], 'openai/gpt-6-sol']}, CLAUDE_TOML)
        self.assertEqual(policy.DEFAULTS, policy.configured(None, listed))
        self.assertEqual('claude-opus-5-5', policy.configured('claude-opus-5-5', OLDER_CLAUDE_PROVIDER)['strong_model'])

    def test_a_tool_builder_that_already_runs_the_strong_model_makes_its_stronger_attempt_on_it(self):
        # As an OpenAI Builder already on GPT-6 Sol gets its xhigh attempt, a Sonnet Builder gets Sonnet at xhigh,
        # and the Sonnet Tester moves to Opus.
        state = self.state()
        roles = state['settings']['roles']
        roles['terra']['model'] = 'claude-sonnet-5-5'
        policy.failure(state, 'e1', 'f')
        self.assertEqual('escalate', policy.failure(state, 'e2', 'f'))
        self.assertEqual(('claude-sonnet-5-5', 'xhigh', 'claude-opus-5-5'),
                         (roles['terra']['model'], roles['terra']['reasoning_effort'], roles['sol']['model']))
        dispatch.enforce_cross_model_verification(state)

    def test_a_builder_on_the_default_strong_model_keeps_todays_policy_without_a_builder_retry_table(self):
        # An OpenAI Builder already on GPT-6 Sol still gets its xhigh attempt, as before [builder_retry] existed.
        for provider in (None, opencode, command.load('kilocode')):
            with self.subTest(provider=getattr(provider, 'NAME', None)):
                self.assertEqual(policy.DEFAULTS, policy.configured(None, provider))

    def test_a_pause_before_escalating_names_jobs_by_their_screen_names(self):
        state = self.state(); state['settings']['roles']['sol'].update(model_pinned=True)
        policy.failure(state, 'e1', 'f')
        self.assertEqual('pause', policy.failure(state, 'e2', 'f'))
        self.assertIn('a Tester or Completion Reviewer on it is pinned', state['stop_reason'])
        self.assertNotRegex(state['stop_reason'], r'\b(sol|completion|terra|astra)\b')

    def test_a_tool_with_bare_gpt_names_and_the_openai_default_pauses_without_changing_a_route(self):
        # The documented codex_receipts tool: bare GPT names, no models list, so the OpenAI defaults apply.
        receipts = command.CommandProvider({**OLDER_CLAUDE_CONFIG, 'name': 'codex_receipts', 'models': None, 'roles': {
            **{role: {'model': 'gpt-6-sol', 'effort': 'medium'} for role in ('astra', 'sol', 'completion', 'plan_reviewer')},
            'terra': {'model': 'gpt-6-luna', 'effort': 'medium'}, 'glm': {'model': 'gpt-6-luna', 'effort': 'medium'}}},
            CLAUDE_TOML)
        for strong in (None, 'gpt-6-sol'):
            with self.subTest(strong=strong):
                state = self.state(receipts)
                state['settings']['builder_retry'] = policy.configured(strong, receipts)
                before = copy.deepcopy(state['settings']['roles'])
                policy.failure(state, 'e1', 'f')
                self.assertEqual('pause', policy.failure(state, 'e2', 'f'))
                self.assertEqual(before, state['settings']['roles'])
                self.assertIn('would be checked by its own model', state['stop_reason'])

    def test_a_tool_that_names_models_with_a_slash_escalates_as_before(self):
        kilo = command.load('kilocode')
        state = self.state(kilo)
        self.assertEqual(policy.DEFAULTS, state['settings']['builder_retry'])
        state['settings']['roles']['sol']['model'] = 'openai/gpt-6-sol'
        policy.failure(state, 'e1', 'f'); self.assertEqual('escalate', policy.failure(state, 'e2', 'f'))
        roles = state['settings']['roles']
        self.assertEqual(('openai/gpt-6-sol', 'zai-coding-plan/glm-5.3'), (roles['terra']['model'], roles['sol']['model']))

    def test_a_kilocode_builder_given_a_bare_strong_model_gets_the_openai_alias_as_before(self):
        kilo = command.load('kilocode')
        state = self.state(kilo)
        state['settings']['builder_retry'] = policy.configured('gpt-6-sol', kilo)
        policy.failure(state, 'e1', 'f')
        self.assertEqual('escalate', policy.failure(state, 'e2', 'f'))
        self.assertEqual('openai/gpt-6-sol', state['settings']['roles']['terra']['model'])

    def test_a_tool_that_names_models_with_a_slash_keeps_the_old_policy_where_a_checker_cannot_be_proven_safe(self):
        # Before tools had their own kind a kilocode Builder escalated here: a pinned Validator stayed for the
        # cross-model guard to catch, and an unrouted GLM still took the Validator's place. It still does.
        kilo = command.load('kilocode')
        for name, change, validator in (
                ('pinned Validator', lambda roles: roles['sol'].update(model_pinned=True), 'openai/gpt-6-sol'),
                ('checker model no role uses', lambda roles: roles['glm'].update(model='openai/gpt-5.6-sol'),
                 'zai-coding-plan/glm-5.3')):
            with self.subTest(name):
                state = self.state(kilo)
                roles = state['settings']['roles']
                roles['sol']['model'] = 'openai/gpt-6-sol'
                change(roles)
                policy.failure(state, 'e1', 'f')
                self.assertEqual('escalate', policy.failure(state, 'e2', 'f'))
                self.assertEqual(('openai/gpt-6-sol', 'xhigh', validator),
                                 (roles['terra']['model'], roles['terra']['reasoning_effort'], roles['sol']['model']))


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
        self.assertEqual(['investigate', 'retry', 'investigate', 'defer', 'escalate'],
                         [d['action'] for d in self.state()['builder_retry_decisions']])
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
        self.assertEqual(['investigate','retry','investigate','escalate','investigate','pause'],
                         [r['action'] for r in child['builder_retry_decisions']])
        self.assertNotIn('implementation',child)
        self.build(2,extra=['--resume-paused','--retry-builder','M1'])
        self.assertEqual(before,self.events())
        self.assertNotIn('autocode',self.state().get('unit_handoffs',{}))

    def test_independent_failure_resolver_retries_escalates_then_pauses(self):
        spec=bb.plan([([], 'MAX_RETRIES must be 3', ['retry.py'])],
            {'M1': {'retry.py':'MAX_RETRIES = 5\n'}},
            {'M1':'from retry import MAX_RETRIES; assert MAX_RETRIES == 3'},
            'Preserve exactly three retries')
        # Isolate execution exhaustion from the separately covered three-review replan gate.
        self.seed(spec, planner_extra=['--max-milestone-stalled-reviews', '0'])
        status = json.loads(self.invoke('autocode', ['--run-dir', str(self.run), '--status']).stdout)
        self.assertIsNone(status['milestone_checkpoint']['limits']['stalled_reviews'])
        self.assertEqual([], self.events(), 'Configuration must not consume a Builder slot')
        self.env['BUILD_AUDIT_FAULT']='validation_fails'
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
        for attempt in range(3):
            self.build()
            self.assertEqual('investigate_stuck', self.state()['next_stage'])
            self.assertNotIn('implementation', self.state())
            self.invoke('autoresolver', ['--run-dir', str(self.run), '--no-chat'],
                        2 if attempt == 2 else 0)
        self.assertEqual('PAUSED_BUILDER_RETRY_LIMIT',self.state()['status'])
        self.assertEqual(['gpt-6-luna','gpt-6-luna','gpt-6-sol'],[r['model'] for r in self.events()])
        self.assertNotIn('implementation',self.state())
        self.assertNotIn('autocode',self.state().get('unit_handoffs',{}))
