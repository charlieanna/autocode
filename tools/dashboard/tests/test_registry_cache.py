"""Registry polling stays bounded when refresh or discovery outlasts the TTL."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_console import Console


class RegistryCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        environment = patch.dict(os.environ, {'AUTOCODE_HOME': str(self.root / 'home'),
                                             'AUTOCODE_DASHBOARD_HOME': str(self.root / 'home/dashboard')})
        environment.start()
        self.addCleanup(environment.stop)
        self.workspace = self.root / 'project'
        (self.workspace / '.git').mkdir(parents=True)
        self.runs = [self.make_run('run-' + str(index)) for index in range(3)]
        self.unregistered = self.make_run('unregistered')
        self.registry = {
            'operation': 'list', 'registry_version': 1,
            'workspaces': [{'workspace': str(self.workspace)}],
            'runs': [{'workspace': str(self.workspace), 'run_dir': str(run), 'availability': 'available'}
                     for run in self.runs],
        }
        self.console = Console([], self.root / 'unused-runner.py', lambda: None, registry_ttl=2)
        self.addCleanup(self.console.pool.shutdown, wait=True)
        self.now = 100.0
        self.command_delay = 0.0
        self.failure = None
        clock = patch('dashboard_backend.time.monotonic', side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        command = patch.object(self.console, '_json_command', side_effect=self.registry_command)
        self.command = command.start()
        self.addCleanup(command.stop)

    def make_run(self, name):
        run = self.workspace / '.autocode/runs' / name
        run.mkdir(parents=True)
        (run / 'state.json').write_text(json.dumps({
            'workspace': str(self.workspace), 'task': name, 'status': 'PAUSED_INTERVENTION',
        }))
        return run

    def registry_command(self, args, timeout=4):
        self.now += self.command_delay
        operation = args[1]
        if operation == self.failure:
            return None, {'message': 'fixture registry timeout', 'uncertain': True}
        if operation == 'location':
            return {'operation': 'location', 'registry_version': 1,
                    'registry_path': str(self.root / 'registry.json')}, None
        self.assertEqual('list', operation)
        return copy.deepcopy(self.registry), None

    def test_slow_refresh_is_fresh_when_published(self):
        self.command_delay = 1.5  # Combined refresh exceeds the two-second TTL.
        first = self.console._registered()
        self.assertEqual(103.0, first['at'])
        self.assertIs(first, self.console._registered())
        self.assertEqual(2, self.command.call_count)
        self.now += 2
        self.assertIsNot(first, self.console._registered())
        self.assertEqual(4, self.command.call_count)

    def test_slow_failures_receive_the_same_reuse_window(self):
        for operation, expected_calls in [('location', 1), ('list', 2)]:
            with self.subTest(operation=operation):
                self.console.registry_cache['at'] = 0
                self.command.reset_mock()
                self.failure = operation
                self.command_delay = 3
                first = self.console._registered()
                self.assertEqual('fixture registry timeout', first['error'])
                self.assertEqual(self.now, first['at'])
                self.assertIs(first, self.console._registered())
                self.assertEqual(expected_calls, self.command.call_count)

    def test_discovery_reuses_one_registry_after_traversal_crosses_ttl(self):
        original = self.console.run_for

        def slow_run_for(workspace, raw, **kwargs):
            self.now += 3
            return original(workspace, raw, **kwargs)

        with patch.object(self.console, 'run_for', side_effect=slow_run_for):
            found = self.console.discover()
        self.assertEqual({str(run) for run in self.runs}, {row['run'] for row in found})
        self.assertEqual(2, self.command.call_count)
        # The traversal's snapshot does not extend the cache lifetime for later callers.
        self.console._registered()
        self.assertEqual(4, self.command.call_count)

    def test_discovery_still_rechecks_checkpoint_identity(self):
        original = self.console.run_for
        changed = self.runs[1]

        def changed_run_for(workspace, raw, **kwargs):
            self.now += 3
            if raw == str(changed):
                (changed / 'state.json').write_text(json.dumps({'workspace': str(self.root / 'other')}))
            return original(workspace, raw, **kwargs)

        with patch.object(self.console, 'run_for', side_effect=changed_run_for):
            found = self.console.discover()
        self.assertEqual({str(run) for run in self.runs if run != changed}, {row['run'] for row in found})
        self.assertEqual(2, self.command.call_count)

    def test_dashboard_snapshot_pins_registry_across_slow_composite_read(self):
        original = self.console.run_for

        def slow_run_for(workspace, raw, **kwargs):
            self.now += 3
            return original(workspace, raw, **kwargs)

        with patch.object(self.console, 'run_for', side_effect=slow_run_for):
            snapshot = self.console.dashboard_snapshot()
        self.assertEqual({str(run) for run in self.runs}, {row['run'] for row in snapshot['runs']})
        self.assertEqual(2, self.command.call_count)


if __name__ == '__main__':
    unittest.main()
