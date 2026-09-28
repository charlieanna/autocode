"""Stopped provider rate limits are AutoResolver work, never human cleanup."""
import copy
import json
from pathlib import Path
import unittest

import autocode as runner, autocode_support as support
from . import test_autocode


class RateLimitReconciliationTests(unittest.TestCase):
    setUp = test_autocode.RetrofitTest.setUp
    tearDown = test_autocode.RetrofitTest.tearDown

    def attempt(self, *, changed=False, completed=False):
        base = self.run / 'iterations/001/astra_discovery-01'
        base.parent.mkdir(parents=True)
        support.atomic_json(base.with_suffix('.before.json'), support.snapshot(self.root))
        rows = [{'type': 'turn.started'}]
        rows.append({'type': 'turn.completed', 'usage': {}} if completed else
                    {'type': 'error', 'error': {'message': 'rate limit 429'}})
        base.with_suffix('.jsonl').write_text('\n'.join(json.dumps(row) for row in rows) + '\n')
        base.with_suffix('.opencode.json').write_text('{}')
        if changed:
            (self.root / 'unexpected.py').write_text('changed')
        record = {'stage': 'astra_discovery', 'role': 'glm', 'route_role': 'glm', 'iteration': 1,
                  'output': str(base.with_suffix('.json')), 'events': str(base.with_suffix('.jsonl')),
                  'before_ref': str(base.with_suffix('.before.json')), 'permission_config': str(base.with_suffix('.opencode.json')),
                  'finished_at': support.now(), 'exit_code': 1, 'duration_seconds': 1, 'accounted': False,
                  'processes': [{'pid': 2**22-1, 'birth_time': 0.0, 'started': 'missing', 'group': 2**22-1}]}
        self.state.update(active_stage=record, next_stage='astra_discovery', status='RUNNING', sessions={'glm': 'old'})
        return record

    def test_rate_limit_is_archived_and_escalated_without_provider_replay(self):
        self.attempt()
        self.assertTrue(runner.reconcile_rate_limited_stage(self.state, self.run, self.root))
        self.assertNotIn('active_stage', self.state)
        self.assertNotIn('glm', self.state['sessions'])
        self.assertEqual('PAUSED_RATE_LIMIT', self.state['status'])
        self.assertTrue(self.state['stages'][-1]['abandoned'])
        self.assertEqual([], self.state['stages'][-1]['changed_files'])
        error = support.Paused('PAUSED_RATE_LIMIT', self.state['stop_reason'])
        self.assertTrue(runner.resolver_runtime.record_operational_exhaustion(runner, self.state, self.run, error))
        runner.write_json(self.run / 'state.json', self.state)
        public = runner.resolver_human.current(self.state)
        self.assertEqual('operational_exhaustion', public['scope'])
        origin = self.state['resolver']['human_escalations'][public['request_id']]['identity']['proposal']['origin']
        self.assertEqual('PAUSED_RATE_LIMIT', origin['pause_status'])
        self.assertEqual('provider_or_spending_guard', origin['budget']['category'])

    def test_changed_source_never_autoarchives(self):
        self.attempt(changed=True)
        before = copy.deepcopy(self.state)
        with self.assertRaises(support.Paused):
            runner.reconcile_rate_limited_stage(self.state, self.run, self.root)
        self.assertEqual(before, self.state)

    def test_terminal_turn_never_autoarchives(self):
        self.attempt(completed=True)
        before = copy.deepcopy(self.state)
        self.assertFalse(runner.reconcile_rate_limited_stage(self.state, self.run, self.root))
        self.assertEqual(before, self.state)
