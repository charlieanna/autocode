"""Model fallback on a provider quota (issue #184), end to end through the CLI with the fake Codex."""
import json
import unittest

from goal_fixtures import assert_operational_wait
from . import test_subprocess


class QuotaFallbackCliTests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ('--engine', 'codex')

    def trace(self):
        path = self.root / 'launch-trace.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_quota_pauses_as_today_without_fallback(self):
        self.env.update(AUTOCODE_FIXTURE_MODE='milestones', AUTOCODE_FIXTURE_QUOTA_STAGE='sol',
                        AUTOCODE_FIXTURE_QUOTA_MODEL='gpt-5.6-sol',
                        AUTOCODE_FIXTURE_LAUNCH_TRACE=str(self.root / 'launch-trace.jsonl'))
        self.launch(['Build greeting and goodbye', '--chat', '--terra-model', 'gpt-5.6-terra',
                     '--sol-model', 'gpt-5.6-sol'], 2, answers='CLI\nyes\n')
        run, state = self.saved()
        assert_operational_wait(self, state, 'PAUSED_BUDGET')
        self.assertEqual('sol', state['active_stage']['stage'])
        self.assertEqual([{'stage': 'sol', 'milestone': 'M1', 'model': 'gpt-5.6-sol', 'resumed': False,
                           'outcome': 'quota'}], [row for row in self.trace() if row['stage'] == 'sol'])
        self.assertEqual(['M1'], [row['milestone'] for row in self.trace() if row['stage'] == 'terra'])
        status = json.loads(self.launch(['--run-dir', str(run), '--status'], 0).stdout)
        self.assertNotIn('route_fallbacks', status)
        self.assertNotIn('route_fallback', state['settings'])
        self.assertNotIn('route_fallbacks', state)


if __name__ == '__main__':
    unittest.main()
