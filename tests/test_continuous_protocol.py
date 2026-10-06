"""Portable conversation handoff and scoped Planner policy, without providers."""
from copy import deepcopy
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import autocode_conversation as protocol
import autocode_conversation_ingress as ingress
import autocode_planner_contract as contract
import autocode_planner_routes as routes
import autocode_configure as configure
import autocode_builder_policy as builder_policy
import autocode_escalation as escalation
import autocode_goals as goals
import autocode_goal_lifecycle as lifecycle
import autocode_support as support
import autopilot
import autocode_planning as planning
import autocode_milestones as milestones
import model_catalogue
from providers import opencode
from tools.dashboard import planner_dispatch, conversation_transport
from tests import test_goals as goal_fixtures
from tests import test_planning as planning_fixtures
from goal_fixtures import body


def conversation():
    return {
        'id': 'a' * 32, 'title': 'Greeting CLI', 'created_at': '2026-09-30T00:00:00Z',
        'messages': [{'id': 'message-1', 'role': 'user', 'speaker': 'You',
                      'text': 'Build a greeting CLI', 'created_at': '2026-09-30T00:00:00Z',
                      'status': 'saved', 'client_request_id': 'request-1',
                      'logical_turn_id': 'turn-1', 'in_reply_to': None}],
        'drafts': [], 'requirements': {'revisions': [], 'provenance': []},
        'configured_routes': {'requirements_gatherer': routes.MANDATED_ROUTES['requirements_gatherer']},
    }


def structured():
    return {'contract_version': 1, 'kind': contract.STRUCTURED_DRAFT_KIND,
            'goal': 'Greeting CLI', 'requirements': ['Print a greeting'],
            'milestones': ['Implement greeting'], 'parallelism': [], 'unresolved_questions': [],
            'source_revision': {'requirements_revision': 1, 'logical_turn_id': 'turn-1'},
            'attribution': {'role': 'planner', 'model': routes.SOL_PLANNER_MODEL, 'reasoning_effort': 'high'},
            'freshness': {'state': 'fresh', 'updated_at': '2026-09-30T00:00:00Z'}}


class ProtocolTests(unittest.TestCase):
    def dispatch(self, planner=False):
        row = protocol.new_dispatch(logical_turn_id='turn-1', client_request_id='request-1',
                                    route=routes.MANDATED_ROUTES['planner' if planner else 'requirements_gatherer'])
        if planner:
            row.update(role='planner', requirements_revision=1)
        row = protocol.transition(row, 'DISPATCH_PREPARED')
        row = protocol.transition(row, 'RESULT_CAPTURED', result={'sha256': 'b' * 64})
        return protocol.transition(row, 'REPLY_COMMITTED', reply_commit_id='reply-1')

    def test_handoff_and_idempotent_journal_preserve_dispatch_history(self):
        doc = conversation()
        doc['_dispatches'] = {'turn-1': self.dispatch()}
        doc['_planner_dispatches'] = {'turn-1': self.dispatch(planner=True)}
        payload = protocol.handoff_from_document(doc)
        self.assertEqual(doc['_dispatches'], payload['dispatches'])
        self.assertEqual(doc['_planner_dispatches'], payload['planner_dispatches'])
        self.assertEqual(payload, protocol.validate_handoff(payload))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = protocol.stage_handoff(root, payload)
            run = root / '.autocode/runs/run-1'
            first = protocol.ingest_handoff(run, protocol.load_handoff(path, workspace=root), source_path=path)
            self.assertEqual(first, protocol.ingest_handoff(run, payload))
            self.assertEqual(doc['_planner_dispatches'], protocol.read_journal(run)['conversation']['planner_dispatches'])
            changed = conversation()
            changed['id'] = 'c' * 32
            with self.assertRaisesRegex(ValueError, 'different conversation'):
                protocol.ingest_handoff(run, protocol.handoff_from_document(changed))
            self.assertEqual(first, protocol.read_journal(run))

    def test_legacy_handoff_omits_new_fields_and_retains_digest(self):
        doc = conversation()
        original = protocol.handoff_from_document(doc)
        doc.update(_dispatches={}, _planner_dispatches={})
        self.assertEqual(original, protocol.handoff_from_document(doc))
        self.assertNotIn('dispatches', original)
        self.assertNotIn('planner_dispatches', original)
        self.assertEqual(original, protocol.validate_handoff(original))

    def test_execution_attribution_and_event_references_survive_transfer(self):
        doc = conversation()
        reply = {'id': 'reply-1', 'role': 'assistant', 'speaker': 'Requirements Gatherer',
                 'text': 'Should this print to stdout?', 'created_at': '2026-09-30T00:00:00Z',
                 'status': 'received', 'client_request_id': None,
                 'logical_turn_id': 'reply-turn-1', 'in_reply_to': 'turn-1',
                 'execution': {'role': 'requirements_gatherer', **routes.MANDATED_ROUTES['requirements_gatherer']},
                 'event_refs': ['event:gatherer/turn-1/completed']}
        doc['messages'].append(reply)
        payload = protocol.handoff_from_document(doc)
        self.assertEqual(reply, protocol.validate_handoff(payload)['messages'][-1])
        with tempfile.TemporaryDirectory() as directory:
            protocol.ingest_handoff(directory, payload)
            self.assertEqual(reply, protocol.read_journal(directory)['conversation']['messages'][-1])
        malformed = deepcopy(doc)
        malformed['messages'][-1]['execution']['model'] = 42
        with self.assertRaises(ValueError):
            protocol.handoff_from_document(malformed)
        malformed = deepcopy(doc)
        malformed['messages'][-1]['event_refs'] = [False]
        with self.assertRaises(ValueError):
            protocol.handoff_from_document(malformed)

    def test_tampered_turn_and_revision_fail_even_after_digest_is_recomputed(self):
        doc = conversation()
        doc['_planner_dispatches'] = {'turn-1': self.dispatch(planner=True)}
        payload = protocol.handoff_from_document(doc)
        for field, bad in [('logical_turn_id', 'unknown-turn'), ('requirements_revision', 0),
                           ('state', 'APPROVED'), ('role', 'builder')]:
            candidate = deepcopy(payload)
            candidate['planner_dispatches']['turn-1'][field] = bad
            candidate['digest'] = protocol.digest(candidate)
            with self.subTest(field=field), self.assertRaises(ValueError):
                protocol.validate_handoff(candidate)

    def test_recovery_never_replays_ambiguous_provider_execution(self):
        for state in protocol.DISPATCH_STATES:
            row = {'state': state}
            if state == 'REPLY_COMMITTED':
                expected = 'committed'
            elif state == 'RESULT_CAPTURED':
                expected = 'failed'
            elif state in ('SAVED', 'DISPATCH_PREPARED', 'SAFE_NOT_DISPATCHED'):
                expected = 'retry'
            else:
                expected = 'uncertain'
            with self.subTest(state=state):
                self.assertEqual(expected, protocol.recovery_action(row))
        self.assertEqual('wait', protocol.recovery_action({'state': 'PROCESS_STARTED'}, process_alive=True))
        self.assertEqual('commit_captured', protocol.recovery_action({'state': 'RESULT_CAPTURED'}, captured_valid=True))

    def test_product_change_invalidates_pending_and_current_drafts(self):
        doc = conversation()
        doc['plan_drafts'] = [{'status': state, 'freshness': {'state': 'fresh'}} for state in ('current', 'pending')]
        original = deepcopy(doc)
        contract.record_product_change(doc, detail='Also log greetings')
        self.assertEqual(1, doc['requirements']['revisions'][-1]['revision'])
        self.assertTrue(all(row['freshness']['re_review_required'] for row in doc['plan_drafts']))
        self.assertEqual(original['messages'], doc['messages'])
        self.assertNotIn('approval_status', doc)


class PortableSchemaTests(unittest.TestCase):
    def test_installed_module_validates_without_repository_docs(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / 'installed_package'
            package.mkdir()
            source = Path(contract.__file__)
            module_path = package / source.name
            shutil.copy2(source, module_path)
            schemas = package / 'autocode-schemas'
            schemas.mkdir()
            shutil.copy2(contract.contract_schema_path(), schemas / contract.SCHEMA_RELPATH.name)
            spec = importlib.util.spec_from_file_location('installed_planner_contract_fixture', module_path)
            installed = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(installed)
            self.assertEqual(structured(), installed.validate_structured_draft(structured()))
            invalid = structured()
            invalid['source_revision']['requirements_revision'] = False
            with self.assertRaises(ValueError):
                installed.validate_structured_draft(invalid)

    def test_documented_schema_matches_packaged_production_schema(self):
        documentation = Path(__file__).resolve().parents[1] / contract.SCHEMA_RELPATH
        self.assertEqual(json.loads(documentation.read_text()), contract.load_contract_schema())


class PlannerBoundaryTests(unittest.TestCase):
    def test_structured_result_requires_exact_turn_revision_route_and_freshness(self):
        route = routes.MANDATED_ROUTES['planner']
        value = structured()
        self.assertEqual(value, planner_dispatch.validate_structured_result(
            value, requirements_revision=1, logical_turn_id='turn-1', route=route))
        variants = [({'requirements_revision': 2}, 'source_revision'),
                    ({'logical_turn_id': 'turn-2'}, 'source_revision'),
                    ({'model': 'unrelated-model'}, 'attribution'),
                    ({'state': 'stale'}, 'freshness')]
        for changed, key in variants:
            candidate = deepcopy(value)
            candidate[key].update(changed)
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                planner_dispatch.validate_structured_result(
                    candidate, requirements_revision=1, logical_turn_id='turn-1', route=route)

    def test_provider_adapter_is_tool_free_and_requires_a_terminal_result(self):
        captured = []
        observed = []
        payload = structured()
        started = {'type': 'step_start', 'sessionID': 'session-1',
                   'part': {'id': 'start', 'type': 'step-start'}}
        text_event = {'type': 'text', 'sessionID': 'session-1',
                      'part': {'id': 'draft', 'type': 'text', 'text': json.dumps(payload)}}
        finished = {'type': 'step_finish', 'sessionID': 'session-1',
                    'part': {'id': 'finish', 'type': 'step-finish', 'reason': 'stop'}}
        def capture(command, env, cwd, prompt, **kwargs):
            captured.append((command, env, prompt))
            return 0, '\n'.join(json.dumps(row) for row in (started, text_event, finished))
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(conversation_transport.opencode_transport, 'check_subscription_routes'), \
             patch.object(conversation_transport, '_capture', side_effect=capture):
            reply = planner_dispatch.opencode_planner_provider(
                conversation()['messages'], routes.MANDATED_ROUTES['planner'], Path(directory),
                logical_turn_id='turn-1', requirements_revision=1,
                dispatch_observer=lambda *row: observed.append(row))
        self.assertEqual(payload, json.loads(reply))
        command, env, prompt = captured[0]
        self.assertIn('--pure', command)
        self.assertEqual({'*': 'deny'}, json.loads(env['OPENCODE_PERMISSION']))
        self.assertIn('requirements_revision 1', prompt)
        self.assertIn('provider_events', [row[0] for row in observed])
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(conversation_transport.opencode_transport, 'check_subscription_routes'), \
             patch.object(conversation_transport, '_capture', return_value=(0, json.dumps(text_event))):
            with self.assertRaisesRegex(conversation_transport.ConversationProviderError, 'did not finish'):
                planner_dispatch.opencode_planner_provider(
                    conversation()['messages'], routes.MANDATED_ROUTES['planner'], Path(directory),
                    logical_turn_id='turn-1', requirements_revision=1)


class ProfileTests(unittest.TestCase):
    setUp = planning_fixtures.PlanningTests.setUp

    def fresh(self, handoff=None, **overrides):
        args = planning_fixtures.PlanningTests.configure_args(self, **overrides)
        args.conversation_handoff = handoff
        with patch.object(opencode, 'local_settings', return_value={'engine': 'opencode'}):
            return configure.configure(args, {'workspace': '/fixture', 'iteration': 0},
                                       planning=planning, milestones=milestones, autopilot=autopilot,
                                       opencode=opencode)

    def test_only_handoff_selects_profile_and_builder_remains_sol_high(self):
        ordinary = self.fresh()
        attached = self.fresh('/fixture/handoff.json')
        self.assertEqual(opencode.DEFAULT_MODELS['terra'], ordinary['roles']['terra']['model'])
        self.assertNotIn('conversation_profile', ordinary)
        self.assertEqual('continuous-v1', attached['conversation_profile'])
        for role, policy in routes.RUNNER_POLICY_ROLES.items():
            self.assertEqual(routes.MANDATED_ROUTES[policy]['model'], attached['roles'][role]['model'])
            self.assertEqual(routes.MANDATED_ROUTES[policy]['reasoning_effort'], attached['roles'][role]['reasoning_effort'])
        self.assertEqual('high', attached['builder_retry']['strong_reasoning_effort'])
        self.assertEqual(0, attached['limits']['idle_timeout_seconds'])
        self.assertEqual(0, attached['limits']['stage_timeout_seconds'])

    def test_profile_preserves_explicit_model_effort_retry_and_budget(self):
        for model in ('mimo-token-plan/mimo-v2.6-pro', 'xiaomi-token-plan-sgp/mimo-v2.6-flash',
                      'opencode/future-free', 'openai/gpt-6-astra'):
            with self.subTest(model=model):
                settings = {'engine': 'opencode', 'provider': 'opencode', 'roles': {}, 'limits': {'max_seconds': 45}}
                routes.configure_runner_profile(settings, SimpleNamespace(
                    terra_model=model, terra_reasoning_effort='medium', builder_strong_model=model, max_seconds=45))
                self.assertEqual(model, settings['roles']['terra']['model'])
                self.assertEqual('medium', settings['roles']['terra']['reasoning_effort'])
                self.assertEqual(model, settings['builder_retry']['strong_model'])
                self.assertEqual(45, settings['limits']['max_seconds'])

    def test_conversation_and_visual_routes_preserve_any_explicit_model(self):
        configured = deepcopy(routes.MANDATED_ROUTES)
        configured['requirements_gatherer'].update(model='mimo-token-plan/mimo-v2.6-pro', reasoning_effort='medium')
        configured['planner'].update(model='openai/gpt-6-astra', reasoning_effort='medium')
        self.assertEqual(configured, routes.enforce_conversation_routes(configured))
        visual = {'model': 'opencode/future-flash-free', 'reasoning_effort': 'low', 'engine': 'opencode'}
        self.assertEqual(visual, routes.select_visual_review_route(visual))
        configured['plan_reviewer']['model'] = configured['planner']['model']
        with self.assertRaisesRegex(ValueError, 'must not grade'):
            routes.enforce_conversation_routes(configured)

    def test_failed_profile_builder_retains_high_and_its_authorized_retry(self):
        state = {'settings': self.fresh('/fixture/handoff.json'), 'status': 'RUNNING',
                 'current_task': {'id': 'task-1', 'milestone_id': 'M1'},
                 'goal_contract': {'hash': 'approved-contract'}, 'sessions': {'terra': 'original'}}
        original = deepcopy(state['settings']['roles'])
        for role in original:
            self.assertIsNone(escalation.advance(state, role, trigger='rejected_output'))
        self.assertEqual(original, state['settings']['roles'])
        self.assertNotIn('reasoning_escalations', state)
        self.assertEqual('retry', builder_policy.failure(state, 'failure-1', 'failed check'))
        self.assertEqual('escalate', builder_policy.failure(state, 'failure-2', 'failed check'))
        self.assertIsNone(builder_policy.guard(state))
        self.assertEqual(original, state['settings']['roles'])
        self.assertEqual(['high', 'high'], [row['selected_effort'] for row in state['builder_retry_decisions']])
        # Replaying the same failure cannot grant another attempt.
        self.assertEqual('escalate', builder_policy.failure(state, 'failure-2', 'failed check'))
        self.assertEqual(2, len(state['builder_retry_decisions']))
        self.assertEqual('pause', builder_policy.failure(state, 'failure-3', 'failed check'))
        self.assertEqual(original, state['settings']['roles'])


class IngressTests(unittest.TestCase):
    def test_ingress_is_idempotent_and_uses_verified_project_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = root / '.autocode/worktrees/task'
            child.mkdir(parents=True)
            run = child / '.autocode/runs/run'
            payload = protocol.handoff_from_document(conversation())
            source = protocol.stage_handoff(root, payload)
            state = {'project_workspace': str(root), 'settings': {}, 'goal_contract': {'approval_status': 'draft'}}
            with patch.object(ingress.workspaces, 'metadata', return_value={'project_workspace': str(root)}):
                self.assertTrue(ingress.ingest(state, run, child, source))
                self.assertFalse(ingress.ingest(state, run, child, source, existing_run=True))
            self.assertEqual('draft', state['goal_contract']['approval_status'])
            self.assertEqual(payload['digest'], state['conversation_handoff']['digest'])

    def test_arbitrary_existing_run_cannot_claim_a_new_conversation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = protocol.stage_handoff(root, protocol.handoff_from_document(conversation()))
            run = root / '.autocode/runs/existing'
            with self.assertRaisesRegex(ValueError, 'creating the task'):
                ingress.ingest({}, run, root, source, existing_run=True)
            self.assertFalse(protocol.journal_file(run).exists())


class ExpectedGoalCliTests(unittest.TestCase):
    setUp = goal_fixtures.GoalTests.setUp
    invoke = goal_fixtures.GoalTests.invoke

    def test_fresh_cli_transfers_conversation_without_approving_or_dispatching(self):
        runner = goal_fixtures.runner
        payload = protocol.handoff_from_document(conversation())
        source = protocol.stage_handoff(self.root, payload)
        task = protocol.task_text(payload)
        args = ['autocode', task, '--workspace', str(self.root),
                '--in-place', '--engine', 'opencode', '--no-chat',
                '--conversation-handoff', str(source)]
        with patch.object(sys, 'argv', args), \
             patch.object(support, 'assert_no_legacy_process'), \
             patch.object(opencode, 'local_settings', return_value={'engine': 'opencode'}), \
             patch.object(model_catalogue, 'choose', side_effect=lambda settings, *a, **kw: settings), \
             patch.object(opencode, 'check_models'), patch.object(opencode, 'check_subscription_routes'), \
             patch.object(opencode.tool_containment, 'unavailable', return_value=None), \
             patch.object(runner, 'run_role', side_effect=AssertionError('No provider may launch')), \
             patch.object(runner.build_loop, 'run', return_value=0), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(0, runner.main())
        runs = [p for p in (self.root / '.autocode/runs').iterdir() if p != self.run]
        self.assertEqual(1, len(runs))
        state = support.read(runs[0] / 'state.json')
        self.assertEqual(payload['digest'], state['conversation_handoff']['digest'])
        self.assertEqual(task, state['task'])
        self.assertEqual([row['text'] for row in payload['messages'] if row['role'] == 'user'], goals.source_texts(state))
        self.assertEqual(payload['messages'], protocol.read_journal(runs[0])['conversation']['messages'])
        self.assertFalse(goals.approved(state))
        self.assertEqual([], state['stages'])
        self.assertEqual('high', state['settings']['roles']['terra']['reasoning_effort'])

    def test_stale_build_token_is_rejected_by_cli_before_provider_dispatch(self):
        lifecycle.install_draft(self.state, body(), origin='test')
        token = goals.token(self.state['goal_contract'])
        event = {'kind': 'goal_approval', 'actor': 'user_cli', 'token': token}
        self.state['goal_contract'].update(approval_status='approved', approval_event=event)
        self.state.setdefault('user_events', []).append(event)
        self.assertTrue(goals.approved(self.state))
        self.assertEqual(2, self.invoke('--expected-goal-token', 'r999:' + 'a' * 64, '--no-chat'))
        self.assertIn('approved plan changed', self.stderr)
        self.assertEqual([], self.state['stages'])
        self.assertEqual(token, goals.token(self.state['goal_contract']))
        self.assertFalse(any(row.get('kind') == 'build_start' for row in self.state['user_events']))

    def test_cli_records_build_start_separately_once_for_the_current_approval(self):
        lifecycle.install_draft(self.state, body(), origin='test')
        token = goals.token(self.state['goal_contract'])
        lifecycle.human.evaluate(self.state)
        lifecycle.present(self.state)
        lifecycle.approve(self.state, token)
        approval = deepcopy(self.state['goal_contract']['approval_event'])
        with patch.object(goal_fixtures.runner.build_loop, 'run', return_value=0):
            code = self.invoke('--expected-goal-token', token, '--no-chat')
            self.assertEqual(0, code, self.stderr + self.stdout)
            code = self.invoke('--expected-goal-token', token, '--no-chat')
            self.assertEqual(0, code, self.stderr + self.stdout)
        events = self.state['user_events']
        starts = [row for row in events if row.get('kind') == 'build_start']
        self.assertEqual(1, len(starts))
        self.assertEqual(token, starts[0]['token'])
        self.assertEqual('user_cli', starts[0]['actor'])
        self.assertEqual(approval, self.state['goal_contract']['approval_event'])

    def test_joint_build_requires_the_architect_receipt_for_that_token(self):
        lifecycle.install_draft(self.state, body(), origin='test')
        token = goals.token(self.state['goal_contract'])
        approval = {'kind': 'goal_approval', 'actor': 'user_cli', 'token': token}
        self.state['goal_contract'].update(approval_status='approved', approval_event=approval)
        self.state.setdefault('user_events', []).append(approval)
        self.state['settings']['joint_planning'] = True
        self.state['planning'] = {'final_token': 'r1:older'}
        with self.assertRaisesRegex(ValueError, 'approved plan changed'):
            ingress.record_build_start(self.state, token, token_for=goals.token, is_approved=goals.approved)
        self.assertFalse(any(row.get('kind') == 'build_start' for row in self.state['user_events']))
