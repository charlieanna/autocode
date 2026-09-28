"""A default can recover; explicit limits and operator inputs stay authoritative."""
import json
from pathlib import Path
import unittest

import autocode as runner, autocode_resolver_human as human
from . import test_subprocess


class BudgetIntegrationTests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ('--engine', 'codex')

    def test_default_limit_extends_once_for_verified_milestone_progress(self):
        self.env['AUTOCODE_FIXTURE_MODE'] = 'milestones'
        self.launch(['Build greeting and goodbye', '--no-chat'], 2)
        run, state = self.saved()
        self.assertEqual('runner_default', state['settings']['budget_origins']['iteration_ceiling'])
        # A deliberately small trusted deployment default makes the full
        # exhaustion/recovery flow observable without fifteen fixture cycles.
        state['settings']['limits']['iteration_ceiling'] = 1
        runner.normalize_human_boundary(state, run)
        (run / 'state.json').write_text(json.dumps(state))
        self.launch(['--run-dir', str(run), '--chat'], 0, answers='CLI\nyes\n')
        _, finished = self.saved()
        self.assertEqual('TASK_COMPLETE', finished['status'])
        self.assertEqual(2, finished['settings']['limits']['iteration_ceiling'])
        extensions = finished['resolver']['budget_extensions']
        self.assertEqual(1, len(extensions))
        self.assertEqual(('iteration_ceiling', 1, 2),
                         (extensions[0]['kind'], extensions[0]['from'], extensions[0]['to']))
        self.assertTrue(any(row.get('runner_owned') and row.get('decision', {}).get('action') == 'extend_default_budget'
                            for row in finished['stages']))
        self.assertFalse(any(entry['identity']['proposal']['scope'] == 'operational_exhaustion'
                             for entry in finished['resolver']['human_escalations'].values()))
        self.assertEqual(2, sum(row['stage'] == 'terra' for row in finished['stages']))

    def test_explicit_abbreviated_cli_limit_remains_protected(self):
        self.env['AUTOCODE_FIXTURE_MODE'] = 'milestones'
        self.launch(['Build greeting and goodbye', '--chat', '--max-iter', '1'], 2, answers='CLI\nyes\n')
        _, state = self.saved()
        self.assertEqual('user_explicit', state['settings']['budget_origins']['iteration_ceiling'])
        self.assertEqual(1, state['settings']['limits']['iteration_ceiling'])
        self.assertNotIn('budget_extensions', state.get('resolver', {}))
        self.assertEqual('operational_exhaustion', human.current(state)['scope'])
