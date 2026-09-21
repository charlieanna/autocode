"""Check dashboard commands against the real runner without starting any run.

Uses the bundled runner, a sibling Autocode checkout, or AUTOCODE_RUNNER_SOURCE
for a separate checkout. Parsing stops before runner.main can create
files, register work, inspect credentials, or launch providers.
"""
import argparse
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_console import Console


class ParsedArguments(Exception):
    def __init__(self, args):
        self.args_namespace = args


class JointPlanningContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        explicit = os.environ.get('AUTOCODE_RUNNER_SOURCE')
        candidates = ([Path(explicit)] if explicit else [
            Path(__file__).resolve().parents[2] / 'autocode.py',
            Path(__file__).resolve().parents[2] / 'autocode/tools/autocode.py',
            Path.cwd() / 'tools/autocode.py',
        ])
        cls.runner_source = next((path.resolve() for path in candidates if path.is_file()), None)
        if cls.runner_source is None:
            raise unittest.SkipTest('Set AUTOCODE_RUNNER_SOURCE to test the real dashboard/runner contract')
        spec = importlib.util.spec_from_file_location('_dashboard_runner_contract', cls.runner_source)
        cls.runner = importlib.util.module_from_spec(spec)
        with patch.object(sys, 'path', [str(cls.runner_source.parent), *sys.path]):
            spec.loader.exec_module(cls.runner)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve() / 'project'
        (self.workspace / '.git').mkdir(parents=True)
        self.console = Console([self.workspace], self.runner_source, lambda: None)
        self.addCleanup(self.console.pool.shutdown, wait=True)
        # Keep the real enqueue/command construction, but never run its worker.
        worker = patch.object(self.console.pool, 'submit')
        worker.start()
        self.addCleanup(worker.stop)
        catalogue = patch.object(self.console.catalogue, 'fetch', return_value={
            'usable': True, 'error': None,
            'models': ['zai-coding-plan/glm-5.3', 'zai-coding-plan/glm-5.3-flash',
                       'openai/gpt-6-astra', 'openai/gpt-5.6-sol'],
        })
        catalogue.start()
        self.addCleanup(catalogue.stop)
        # Any accidental process launch makes these contract tests fail closed.
        for name in ('Popen', 'run'):
            process = patch.object(self.runner.subprocess, name,
                                   side_effect=AssertionError('Contract tests must not launch subprocesses'))
            process.start()
            self.addCleanup(process.stop)

    def create_settings(self, **payload):
        action = self.console.create({'project': str(self.workspace), 'goal': 'Plan a small feature', **payload})
        command = action['command']
        parse_args = argparse.ArgumentParser.parse_args

        def stop_after_parse(parser, *args, **kwargs):
            raise ParsedArguments(parse_args(parser, *args, **kwargs))

        # Exercise exactly the production parser and its defaults. The sentinel
        # prevents execution of even the runner's post-parse setup code.
        with patch.object(sys, 'argv', command[1:]), \
                patch.object(argparse.ArgumentParser, 'parse_args', stop_after_parse):
            with self.assertRaises(ParsedArguments) as parsed:
                self.runner.main()
        args = parsed.exception.args_namespace
        self.assertFalse(args.chat, 'Browser commands must never wait for terminal input')
        self.assertEqual(self.workspace, args.workspace)
        self.assertEqual('Plan a small feature', args.task)
        with patch.object(self.runner.opencode, 'local_settings', return_value={'engine': 'opencode'}), \
                patch.object(self.runner.support, 'local_settings', return_value={'auth_mode': 'ChatGPT'}) as codex_login:
            settings = self.runner.configure(args, {'workspace': str(self.workspace), 'iteration': 0})
        if settings['engine'] == 'opencode':
            codex_login.assert_not_called()
        self.assertFalse((self.workspace / '.autocode').exists())
        return settings

    def assert_joint_routes(self, settings, *, glm='zai-coding-plan/glm-5.3', terra='zai-coding-plan/glm-5.3'):
        self.assertTrue(settings['joint_planning'])
        self.assertEqual('opencode', settings['engine'])
        self.assertEqual({'opencode'}, set(settings['transport_identities']))
        state = {'settings': settings}
        expected_stages = {
            'astra_discovery': ('glm', 'opencode', glm),
            'astra_challenge': ('astra', 'opencode', 'openai/gpt-6-astra'),
            'glm_revise': ('glm', 'opencode', glm),
            'astra_finalize': ('astra', 'opencode', 'openai/gpt-6-astra'),
            'terra': ('terra', 'opencode', terra),
            'sol': ('sol', 'opencode', 'openai/gpt-5.6-sol'),
        }
        for stage, (expected_role, expected_engine, expected_model) in expected_stages.items():
            with self.subTest(stage=stage):
                role = self.runner.planning.role_for(state, stage)
                self.assertEqual(expected_role, role)
                self.assertEqual(expected_engine, self.runner.planning.engine_for(settings, role))
                self.assertEqual(expected_model, settings['roles'][role]['model'])
                self.assertIsNone(settings['roles'][role]['provider'])

    def test_default_browser_creation_routes_discovery_to_glm_and_review_to_astra(self):
        self.assert_joint_routes(self.create_settings())

    def test_explicit_role_choices_survive_real_runner_configuration(self):
        settings = self.create_settings(
            engine='opencode', glm_model='zai-coding-plan/glm-5.3-flash',
            astra_model='gpt-6-astra', terra_model='zai-coding-plan/glm-5.3-flash', sol_model='gpt-5.6-sol')
        self.assert_joint_routes(settings, glm='zai-coding-plan/glm-5.3-flash', terra='zai-coding-plan/glm-5.3-flash')

    def test_explicit_opencode_model_ids_are_preserved_without_double_prefixes(self):
        settings = self.create_settings(
            engine='opencode', astra_model='openai/gpt-6-astra', sol_model='openai/gpt-5.6-sol')
        self.assert_joint_routes(settings)

    def test_explicit_legacy_codex_choice_does_not_inherit_opencode_default(self):
        settings = self.create_settings(engine='codex')
        self.assertEqual('codex', settings['engine'])
        self.assertFalse(settings.get('joint_planning', False))
        self.assertEqual('astra', self.runner.planning.role_for({'settings': settings}, 'astra_discovery'))
        for role in ('astra', 'terra', 'sol'):
            self.assertEqual('codex', self.runner.planning.engine_for(settings, role))
        self.assertNotIn('glm', settings['roles'])


if __name__ == '__main__':
    unittest.main()
