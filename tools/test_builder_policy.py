import copy
import json
from pathlib import Path
import unittest
from . import autocode_builder_policy as policy
from . import test_build_blackbox as bb


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
        for override in ({'model_pinned': True}, {'provider': 'custom'}):
            state = self.state(); state['settings']['roles']['terra'].update(override)
            policy.failure(state,'e1','failure')
            self.assertEqual('pause',policy.failure(state,'e2','failure'))
            self.assertEqual('gpt-6-luna',state['settings']['roles']['terra']['model'])

    def test_new_milestone_restores_normal_route_and_new_budget(self):
        state = self.state(); policy.failure(state,'e1','f'); policy.failure(state,'e2','f')
        state['current_task'] = {'id':'task2','milestone_id':'M2'}
        policy.guard(state)
        self.assertEqual('gpt-6-luna',state['settings']['roles']['terra']['model'])
        self.assertEqual('retry',policy.failure(state,'e3','f'))

    def test_configured_strong_model_retains_opencode_transport(self):
        state=self.state(); state['settings']['engine']='opencode'
        state['settings']['builder_retry']['strong_model']='gpt-6-sol'
        policy.failure(state,'e1','f'); policy.failure(state,'e2','f')
        self.assertEqual('openai/gpt-6-sol',state['settings']['roles']['terra']['model'])
        self.assertEqual('high',state['settings']['roles']['terra']['reasoning_effort'])


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
        self.assertEqual(['gpt-6-luna','gpt-6-luna','gpt-6-sol'],[r['model'] for r in self.events() if r['milestone']=='M1'])
        self.assertNotEqual('TASK_COMPLETE',self.state()['status'])

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
            self.build(); self.candidate()
            self.invoke('autoreview',['--run-dir',str(self.run),'--no-chat'])
            self.assertEqual('FAIL',self.state()['validation']['verdict'])
            self.invoke('autoresolver',['--run-dir',str(self.run),'--no-chat'],2 if attempt==2 else 0)
        self.assertEqual(['gpt-6-luna','gpt-6-luna','gpt-6-sol'],[r['model'] for r in self.events()])
        self.assertEqual('PAUSED_BUILDER_RETRY_LIMIT',self.state()['status'])
        self.assertEqual(['retry','escalate','pause'],[r['action'] for r in self.state()['builder_retry_decisions']])

    def test_serial_noop_uses_same_bounded_policy(self):
        self.seed(bb.plan([([], 'Version prints 1', ['version.py'])],
            {'M1':{'version.py':'print(1)\n'}}, {'M1':'import version'}, 'Print version'))
        self.env['BUILD_AUDIT_FAULT']='retry_exhausted'
        self.build(2)
        self.assertEqual('PAUSED_BUILDER_RETRY_LIMIT',self.state()['status'])
        self.assertEqual(['gpt-6-luna','gpt-6-luna','gpt-6-sol'],[r['model'] for r in self.events()])
        self.assertNotIn('implementation',self.state())
        self.assertNotIn('autocode',self.state().get('unit_handoffs',{}))
