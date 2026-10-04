"""The public launch seam must fail before models if its tool boundary is absent."""
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import tests  # noqa: F401 - runtime import path
import autocode_provider_launch as launch
import autocode_tool_containment as containment
import autocode_util as util


class LaunchContainment(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.policy = {'version': 1, 'workspace': str(self.root), 'scratch': str(self.root / '.autocode/stage/scratch')}
        self.adapter = SimpleNamespace(NAME='opencode', CONFIGURED=False,
            launch=mock.Mock(return_value=(['opencode', 'run'],
                {'AUTOCODE_TOOL_CONTAINMENT': json.dumps(self.policy)}, {})))
        self.options = dict(engine='opencode', adapter=self.adapter, role='validator', route_role='sol',
            workspace=self.root, run_dir=self.root / '.autocode/runs/one', session=None,
            model='openai/gpt-6-sol', effort='high', allow_write=False, planning=False,
            report=self.root / 'output.json', schema=self.root / 'schema.json', prompt_file=self.root / 'prompt.txt',
            sandbox='read-only', transport_args=[], chatgpt=True, provider=None)

    def test_real_native_launch_requests_boundary_and_preserves_model(self):
        _, _, _, worker = launch.prepare(**self.options)
        args, kwargs = self.adapter.launch.call_args
        self.assertEqual(('openai/gpt-6-sol', 'high', False), args[4:7])
        self.assertEqual(self.policy, worker['tool_containment'])
        self.assertIn(str(self.options['run_dir']), kwargs['containment']['protected_paths'])
        self.assertNotIn(str(Path.home()), kwargs['containment']['read_roots'])

    def test_planning_and_command_preview_do_not_create_a_shell_boundary(self):
        for changes in ({'planning': True}, {'enforce_tool_boundary': False}):
            with self.subTest(changes=changes):
                launch.prepare(**{**self.options, **changes})
                self.assertNotIn('containment', self.adapter.launch.call_args.kwargs)

    def test_fresh_contained_session_does_not_change_the_event_protocol(self):
        _, _, _, worker = launch.prepare(**{**self.options, 'session': 'previous-session'})
        self.assertIsNone(self.adapter.launch.call_args.args[3])
        self.assertIsNone(worker['provider_session'])
        self.assertIn('fresh_session_reason', worker)
        _, _, _, simulated = launch.prepare(**{**self.options, 'session': 'previous-session',
                                               'enforce_tool_boundary': False})
        self.assertEqual('previous-session', self.adapter.launch.call_args.args[3])
        self.assertEqual('previous-session', simulated['provider_session'])

    def test_configured_provider_does_not_inherit_an_unproven_kernel_claim(self):
        self.adapter.CONFIGURED = True
        self.adapter.launch.return_value = (['custom-provider'], {}, {})
        _, _, _, worker = launch.prepare(**self.options)
        self.assertNotIn('containment', self.adapter.launch.call_args.kwargs)
        self.assertNotIn('tool_containment', worker)

    def test_failed_or_timed_out_conformance_pauses_before_launch(self):
        for error in (RuntimeError('native boundary unavailable'), ValueError('unsafe root'),
                      subprocess.TimeoutExpired(['opencode', 'debug'], 1)):
            with self.subTest(error=type(error).__name__):
                self.adapter.launch.side_effect = error
                with self.assertRaises(util.Paused) as caught:
                    launch.prepare(**self.options)
                self.assertEqual('PAUSED_TOOL_CONTAINMENT', caught.exception.status)

    def test_handoff_changes_only_the_capture_example_not_old_evidence(self):
        data = {'workspace': str(self.root), 'old_receipt': '.autocode/evidence/accepted.json'}
        text = 'Example --output .autocode/evidence/<unique-name>.json\nCURRENT HANDOFF DATA\n' + json.dumps(data)
        updated = launch.containment_prompt(text, {'tool_containment': self.policy})
        parsed = json.loads(updated.split('\nCURRENT HANDOFF DATA\n')[1])
        self.assertEqual(data['old_receipt'], parsed['old_receipt'])
        self.assertEqual(self.policy, parsed['tool_containment'])
        self.assertIn(self.policy['scratch'], updated)
        self.assertNotIn('--output .autocode/evidence/<unique-name>.json', updated)

    def test_changed_policy_is_a_prelaunch_hold_not_report_repair(self):
        with mock.patch.object(containment, 'verify', side_effect=RuntimeError('changed policy')):
            with self.assertRaises(util.Paused) as caught:
                launch.verify_containment({'tool_containment': self.policy})
        self.assertEqual('PAUSED_TOOL_CONTAINMENT', caught.exception.status)
        launch.verify_containment({})


if __name__ == '__main__':
    unittest.main()
