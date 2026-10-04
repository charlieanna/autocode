"""Native editor-schema bootstrap is inert; tool and permission definitions are not."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import tests  # noqa: F401 - runtime import path
import autocode as runner
import autopilot
from providers import opencode


class InertNativeConfiguration(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / '.git').mkdir()
        self.path = self.root / 'opencode.jsonc'
        self.env = {'HOME': str(self.root / 'home'), 'PATH': '/usr/bin',
                    'OPENCODE_TEST_MANAGED_CONFIG_DIR': str(self.root / 'managed')}

    def identity(self):
        with mock.patch.object(opencode.env_prep, 'resolve_executable', return_value='/native/opencode'), \
                mock.patch.object(opencode.env_prep, 'preflight_run', return_value=SimpleNamespace(returncode=0, stdout='1.18.33\n')):
            return opencode.local_settings(self.root, env=self.env)

    def test_native_schema_only_initialization_does_not_change_configuration(self):
        original = self.identity()
        for content in ({}, {'$schema': 'https://opencode.ai/config.json'}):
            self.path.write_text(json.dumps(content, indent=2) + '\n')
            self.assertEqual(original, self.identity())
            self.assertFalse(opencode.transport_drift(self.identity(), original))

    def test_real_settings_and_nonstandard_schema_metadata_still_change_identity(self):
        original = self.identity()
        for value in ({'permission': {'bash': 'deny'}}, {'model': 'different'}, {'plugin': []},
                      {'$schema': 'https://opencode.ai/config.json', 'provider': {}},
                      {'$schema': 'https://other.invalid/schema'}, [], None):
            with self.subTest(value=value):
                self.path.write_text(json.dumps(value))
                self.assertTrue(opencode.transport_drift(self.identity(), original))

    def test_deleted_known_route_refuses_before_dispatch_or_accounting(self):
        definitions = self.root / '.opencode' / 'agents' / 'autocode_sol.md'
        for path, content in ((self.path, '{"model":"fixture/reviewer"}'),
                              (definitions, '---\nmodel: fixture/reviewer\n---\nReview')):
            with self.subTest(path=path):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
                saved = self.identity()
                self.assertIsNotNone(saved['config_hashes'][str(path)])
                path.unlink()
                current = self.identity()
                self.assertTrue(opencode.transport_drift(current, saved))
                state = {'status': 'RUNNING', 'next_stage': 'sol', 'stages': [], 'settings': {
                    'roles': {'sol': {'engine': 'opencode', 'model': 'fixture/reviewer'}},
                    'transport_identities': {'opencode': saved}}}
                before = copy.deepcopy(state)
                dispatch = mock.Mock(side_effect=AssertionError('Deleted route must not dispatch'))
                apply, persist = mock.Mock(), mock.Mock()
                with mock.patch.object(runner, 'opencode', opencode), \
                        mock.patch.object(opencode, 'local_settings', return_value=current), \
                        mock.patch.object(opencode, 'check_subscription_routes'), \
                        self.assertRaises(runner.support.Paused) as caught:
                    autopilot.drive(state, dispatch, apply=apply, persist=persist,
                        before=lambda value: runner.check_joint_transports(value, self.root))
                self.assertEqual('PAUSED_TRANSPORT_CHANGED', caught.exception.status)
                dispatch.assert_not_called()
                apply.assert_not_called()
                persist.assert_not_called()
                self.assertEqual(before, state)

    def test_agent_and_tool_definitions_are_never_inert(self):
        original = self.identity()
        for directory in ('agents', 'tools'):
            path = self.root / '.opencode' / directory / 'opencode.json'
            path.parent.mkdir(parents=True)
            path.write_text('{"$schema":"https://opencode.ai/config.json"}')
            current = self.identity()
            self.assertIsNotNone(current['config_hashes'][str(path)])
            self.assertTrue(opencode.transport_drift(current, original))

    def test_overlapping_configuration_root_keeps_definition_authority(self):
        directory = self.root / '.opencode/agents'
        directory.mkdir(parents=True)
        path = directory / 'opencode.json'
        path.write_text('{}')
        self.env['OPENCODE_CONFIG_DIR'] = str(directory)
        self.assertIsNotNone(self.identity()['config_hashes'][str(path)])

    def test_symlink_and_jsonc_source_are_not_silently_normalized(self):
        target = self.root / 'target.json'
        target.write_text('{}')
        self.path.symlink_to(target)
        self.assertIsNotNone(self.identity()['config_hashes'][str(self.path)])
        self.path.unlink()
        self.path.write_text('// user-owned configuration\n{}')
        self.assertIsNotNone(self.identity()['config_hashes'][str(self.path)])

    def test_old_raw_stub_hash_requires_exact_current_bytes_and_preserves_checkpoint(self):
        self.path.write_text('{\n  "$schema": "https://opencode.ai/config.json"\n}')
        current = self.identity()
        checkpoint = copy.deepcopy(current)
        checkpoint['config_hashes'][str(self.path)] = hashlib.sha256(self.path.read_bytes()).hexdigest()
        original = copy.deepcopy(checkpoint)
        self.assertFalse(opencode.transport_drift(current, checkpoint))
        self.assertEqual(original, checkpoint)
        self.path.write_text('{"permission":{"bash":"allow"}}')
        self.assertTrue(opencode.transport_drift(current, checkpoint))
        self.assertTrue(opencode.transport_drift(self.identity(), checkpoint))

    def test_executable_and_environment_changes_still_pause(self):
        original = self.identity()
        for key, value in (('executable', '/other/opencode'), ('version', '1.18.34')):
            changed = {**original, key: value}
            self.assertTrue(opencode.transport_drift(changed, original))
        self.env['OPENCODE_PERMISSION'] = '{}'
        self.assertTrue(opencode.transport_drift(self.identity(), original))


if __name__ == '__main__':
    unittest.main()
