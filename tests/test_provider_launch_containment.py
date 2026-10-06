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
                # Available at setup, failed at launch: the pause names the explicit way on (#413).
                self.assertIn('--allow-uncontained-tools', str(caught.exception))

    def test_saved_opt_out_launches_uncontained_and_says_so_on_the_stage_record(self):
        # #413: only the saved settings flag opts out; the declared tool commands are then never computed.
        commands = mock.Mock(return_value=['make test'])
        self.adapter.launch.return_value = (['opencode', 'run'], {}, {})
        _, _, _, worker = launch.prepare(**self.options, tool_commands=commands,
                                         settings={'allow_uncontained_tools': True})
        self.assertNotIn('containment', self.adapter.launch.call_args.kwargs)
        commands.assert_not_called()
        record = launch.stage_record(worker)
        self.assertIs(True, record['uncontained_tools'])
        self.assertIsNone(record['tool_containment'])
        self.assertEqual('OpenCode tool permissions and workspace snapshot checks; no OS sandbox; '
                         'kernel containment waived by --allow-uncontained-tools', record['isolation'])
        _, _, _, planner = launch.prepare(**{**self.options, 'planning': True},
                                          settings={'allow_uncontained_tools': True})
        self.assertNotIn('uncontained_tools', launch.stage_record(planner))  # planning is never contained

    def test_without_the_saved_opt_out_the_stage_stays_contained(self):
        for settings in (None, {}, {'allow_uncontained_tools': 'true'}, {'allow_uncontained_tools': 1}):
            with self.subTest(settings=settings):
                _, _, _, worker = launch.prepare(**self.options, settings=settings,
                                                 tool_commands=lambda: ['make test'])
                self.assertEqual(['make test'], self.adapter.launch.call_args.kwargs['containment']['tool_commands'])
                record = launch.stage_record(worker)
                self.assertNotIn('uncontained_tools', record)
                self.assertEqual(self.policy, record['tool_containment'])
                self.assertEqual('Kernel-constrained native shell; other tools disabled', record['isolation'])

    def test_handoff_changes_only_the_capture_example_not_old_evidence(self):
        data = {'workspace': str(self.root), 'old_receipt': '.autocode/evidence/accepted.json'}
        text = 'Example --output .autocode/evidence/<unique-name>.json\nCURRENT HANDOFF DATA\n' + json.dumps(data)
        updated = launch.containment_prompt(text, {'tool_containment': self.policy})
        parsed = json.loads(updated.split('\nCURRENT HANDOFF DATA\n')[1])
        self.assertEqual(data['old_receipt'], parsed['old_receipt'])
        self.assertEqual(self.policy, parsed['tool_containment'])
        self.assertIn(self.policy['scratch'], updated)
        self.assertNotIn('--output .autocode/evidence/<unique-name>.json', updated)

    def test_every_capture_example_names_the_one_location_a_contained_stage_can_write(self):
        # Live self-build 2026-10-06: a contained Validator's prompt named the run directory (COMMON) and the
        # scratch (provider contract) for the same receipts; the sandbox lets it write only the scratch.
        import autocode_check_replay as check_replay
        import autocode_support as support
        from providers import opencode
        prompt = opencode.prompt_for_schema(support.COMMON + check_replay.VALIDATOR_NOTE + '\nCURRENT HANDOFF DATA\n'
                                            + json.dumps({'workspace': str(self.root)}), {}, self.root / 'events.jsonl')
        updated = launch.containment_prompt(prompt, {'tool_containment': self.policy})
        instructions = updated.split('\nCURRENT HANDOFF DATA\n')[0]
        scratch_example = '--output ' + self.policy['scratch'] + '/evidence-<unique-name>.json'
        self.assertEqual(2, instructions.count(scratch_example))
        self.assertEqual(2, instructions.count('<unique-name>.json'))
        self.assertIn('replaces any other evidence or scratch directory named above', instructions)

    def test_changed_policy_is_a_prelaunch_hold_not_report_repair(self):
        with mock.patch.object(containment, 'verify', side_effect=RuntimeError('changed policy')):
            with self.assertRaises(util.Paused) as caught:
                launch.verify_containment({'tool_containment': self.policy})
        self.assertEqual('PAUSED_TOOL_CONTAINMENT', caught.exception.status)
        launch.verify_containment({})


if __name__ == '__main__':
    unittest.main()
