"""Owned-file structural controls, not model/transport/visual acceptance claims."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import autocode_contract_identity as contract
import autocode_tool_containment as containment
import autocode_util as util
import autocode_visual_profile as profile
from tests.visual_capture_fixtures import png


class VisualProfileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='visual-profile-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.run = self.root / '.autocode' / 'runs' / 'owned'
        self.directory = self.run / 'sol.visual'
        self.directory.mkdir(parents=True)
        self.sdk = self.root / '.autocode' / 'visual-sdk'
        self.sdk.mkdir()
        self.executable = self.root / '.autocode' / 'opencode'
        self.executable.write_text('#!/bin/sh\nexit 99\n')
        self.executable.chmod(0o700)
        dependencies = {'@opencode-ai/plugin': '1.18.33'}
        (self.sdk / 'package.json').write_text(json.dumps({'private': True, 'dependencies': dependencies}))
        packages = {'': {'dependencies': dependencies}}
        for name in ('plugin', 'sdk'):
            key = 'node_modules/@opencode-ai/' + name
            directory = self.sdk / key
            (directory / 'dist').mkdir(parents=True)
            (directory / 'package.json').write_text(json.dumps({'name': '@opencode-ai/' + name, 'version': '1.18.33'}))
            (directory / 'dist' / 'index.js').write_text('// structural fixture; no model or SDK implementation\n')
            packages[key] = {'version': '1.18.33', 'resolved': 'https://registry.npmjs.org/' + name,
                             'integrity': 'sha512-structural-test-not-a-real-package'}
        (self.sdk / 'package-lock.json').write_text(json.dumps({'lockfileVersion': 3, 'packages': packages}))
        self.manifest()
        control = self.root / '.autocode' / ('tool-containment-' + 'a' * 32)
        control.mkdir()
        (control / 'scratch').mkdir()
        (control / 'shell').write_text('#!/bin/sh\nexit 99\n')
        (control / 'policy.sb').write_text(containment.policy(self.root, control / 'scratch'))
        (control / 'conformance.json').write_text('{"test_only_structural_fixture":true}\n')
        self.boundary = {'version': 1, 'platform': 'darwin', 'native_version': '1.18.33',
                         'workspace': str(self.root), 'allow_write': False, 'tools': ['bash'],
                         'scratch': str(control / 'scratch')}
        for name, filename in (('shell', 'shell'), ('profile', 'policy.sb'), ('conformance', 'conformance.json')):
            path = control / filename
            path.chmod(0o500 if name == 'shell' else 0o400)
            self.boundary[name] = str(path)
            self.boundary[name + '_sha256'] = util.file_hash(path)
        self.plugin = self.directory / 'image-delivery.mjs'
        self.plugin.write_bytes(Path(profile.__file__).with_name('autocode_image_delivery.mjs').read_bytes())
        self.images = []
        for kind in ('reference', 'candidate'):
            path = self.directory / (kind + '.png')
            png(path, 2, 1)
            self.images.append({'case_id': 'one', 'kind': kind, 'path': str(path), 'sha256': util.file_hash(path),
                                'mime': 'image/png', 'width': 2, 'height': 1, 'bytes': path.stat().st_size})
        self.permissions = {'*': 'deny', 'bash': {'*': 'allow', 'git *': 'deny', 'npm *': 'ask'}, 'external_directory': 'deny'}
        self.config = {'$schema': 'https://opencode.ai/config.json', 'share': 'disabled', 'autoupdate': False,
                       'shell': self.boundary['shell'], 'plugin': [self.plugin.as_uri()],
                       'agent': {'autocode_sol': {'mode': 'primary', 'model': 'openai/gpt-6-sol',
                                                'permission': deepcopy(self.permissions)}}}
        self.env = {'PATH': '/usr/bin:/bin', 'HOME': '/original-home-not-opened',
                    'XDG_CONFIG_HOME': '/original-config-not-opened', 'XDG_DATA_HOME': '/original-data-not-opened',
                    'XDG_CACHE_HOME': '/original-cache-not-opened', 'XDG_STATE_HOME': '/original-state-not-opened',
                    'OPENCODE_TEST_MANAGED_CONFIG_DIR': '/original-managed-not-opened',
                    'OPENCODE_CONFIG_CONTENT': json.dumps(self.config), 'SHELL': self.boundary['shell'],
                    'AUTOCODE_TOOL_CONTAINMENT': json.dumps(self.boundary), 'NODE_OPTIONS': '--require malicious.js',
                    'UNRELATED_SECRET': 'must-not-survive', 'AUTOCODE_IMAGE_AUDIT': json.dumps({
                        'path': str(self.directory / 'delivery.jsonl'), 'attempt_id': 'owned-attempt', 'binding_sha256': 'b' * 64})}
        self.base = [str(self.executable), 'run', '--dir', str(self.root), '--format', 'json', '--agent', 'autocode_sol',
                     '--model', 'openai/gpt-6-sol', '--variant', 'high', '--title', 'owned visual review']
        self.command = [*self.base, *(value for row in self.images for value in ('--file', row['path']))]
        identity = {'engine': 'opencode', 'version': '1.18.33', 'identity_version': 2, 'executable': str(self.executable),
                    'config_hashes': {'/original-config-not-opened/opencode/opencode.json': None},
                    'environment_config_hashes': {'OPENCODE_CONFIG_CONTENT': None, 'OPENCODE_CONFIG_DIR': None}}
        self.declaration = {'version': 1, 'mode': 'isolated-builtin-openai', 'opencode_version': '1.18.33',
                            'model': 'openai/gpt-6-sol', 'reasoning_effort': 'high',
                            'sdk_path': '.autocode/visual-sdk', 'sdk_manifest_sha256': util.file_hash(self.sdk / profile.MANIFEST),
                            'transport_identity_sha256': util.digest(identity), 'executable_sha256': util.file_hash(self.executable),
                            'max_requests': 2, 'image_limits': {'width': 2, 'height': 1, 'pixels': 2, 'bytes': 4096}}
        self.state = {'workspace': str(self.root), 'settings': {'engine': 'opencode', 'provider': 'opencode',
                      'roles': {'sol': {'engine': 'opencode', 'model': 'openai/gpt-6-sol', 'reasoning_effort': 'high'}},
                      'transport_identity': identity}, 'goal_contract': {'task_id': 'one', 'revision': 1,
                      'approval_status': 'approved', 'body': {'constraints': [], 'open_blocking_questions': []}}}
        self.approve()
        self.worker = {'engine': 'opencode', 'provider': 'opencode', 'configured': False, 'role': 'sol',
                       'model': 'openai/gpt-6-sol', 'planning': False, 'provider_session': None,
                       'command': self.base, 'environment': deepcopy(self.env), 'tool_containment': self.boundary}
        self.worker['environment']['OPENCODE_CONFIG_CONTENT'] = json.dumps({key: value for key, value in self.config.items() if key != 'plugin'})
        # Platform selection only: no mocked authority callback or model receipt.
        self.platform = patch.object(containment.sys, 'platform', 'darwin')
        self.platform.start()
        self.addCleanup(self.platform.stop)

    def manifest(self):
        path = self.sdk / profile.MANIFEST
        if path.exists():
            path.chmod(0o600)
        files = {item.relative_to(self.sdk).as_posix(): util.file_hash(item)
                 for item in self.sdk.rglob('*') if item.is_file() and item.name != profile.MANIFEST}
        path.write_text(json.dumps({'version': 1, 'files': files}, sort_keys=True))
        path.chmod(0o400)

    def approve(self, *, constraints=None):
        saved = self.state['goal_contract']
        saved['body']['constraints'] = constraints if constraints is not None else [profile.MARKER + json.dumps(self.declaration)]
        saved['hash'] = util.digest({key: saved[key] for key in ('task_id', 'revision', 'body')})
        saved['approval_event'] = {'actor': 'user_cli', 'token': contract.token(saved)}
        self.state['user_events'] = [deepcopy(saved['approval_event'])]

    def launch(self, callback=None):
        callback = callback or profile.authority(self.state, self.worker, self.root, self.run)
        return callback(command=self.command, env=self.env, plugin_path=self.plugin, directory=self.directory,
                        images=self.images, reviewer={'provider': 'opencode', 'model': 'openai/gpt-6-sol'})

    def test_owned_prebootstrap_prepares_pinned_isolation_without_mutating_inputs(self):
        before = deepcopy((self.command, self.env, self.state, self.worker))
        original_read = Path.open
        opened = []
        def only_owned(path, *args, **kwargs):
            opened.append(str(path))
            self.assertFalse(str(path).startswith('/original-'))
            self.assertNotEqual('auth.json', path.name)
            return original_read(path, *args, **kwargs)
        with patch.object(Path, 'open', only_owned):
            result = self.launch()
        self.assertTrue(opened)
        self.assertEqual(before, (self.command, self.env, self.state, self.worker))
        self.assertEqual(self.command, result['command'])
        child = result['environment']
        self.assertNotIn('NODE_OPTIONS', child)
        self.assertNotIn('UNRELATED_SECRET', child)
        self.assertNotIn('OPENCODE_CONFIG_DIR', child)
        self.assertEqual('/original-data-not-opened', child['XDG_DATA_HOME'])
        for key in ('HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME', 'OPENCODE_TEST_MANAGED_CONFIG_DIR', 'TMPDIR'):
            self.assertTrue(Path(child[key]).is_relative_to(self.directory))
            self.assertEqual(0o700, Path(child[key]).stat().st_mode & 0o777)
        config = json.loads(child['OPENCODE_CONFIG_CONTENT'])
        self.assertEqual(self.permissions, config['agent']['autocode_sol']['permission'])
        self.assertEqual(list(self.permissions['bash']), list(config['agent']['autocode_sol']['permission']['bash']))
        self.assertEqual(self.boundary['shell'], config['shell'])
        self.assertEqual([self.plugin.as_uri()], config['plugin'])
        self.assertEqual(['openai'], config['enabled_providers'])
        self.assertEqual(child['OPENCODE_CONFIG_CONTENT'] + '\n', (self.directory / 'opencode-final.json').read_text())
        audit = json.loads(child['AUTOCODE_IMAGE_AUDIT'])
        self.assertEqual(2, audit['max_requests'])
        self.assertEqual({'provider': 'opencode', 'model': 'openai/gpt-6-sol', 'agent': 'autocode_sol'}, audit['reviewer'])
        self.assertFalse(Path(audit['path']).exists())
        self.assertEqual(util.digest(child), result['child_identity']['environment_sha256'])
        for path, expected in result['evidence_hashes'].items():
            self.assertTrue(Path(path).is_relative_to(self.run))
            self.assertEqual(expected, util.file_hash(path))
        proof = json.loads((self.directory / 'profile.json').read_bytes())
        self.assertFalse(proof['globally_wider_permissions'])
        self.assertEqual('NOT_VERIFIED', proof['visual_acceptance'])

    def test_profile_requires_unique_strict_json_and_operator_approval(self):
        for constraints in ([], [profile.MARKER + '{}'] * 2,
                            [profile.MARKER + '{"version":1,"version":1}'],
                            [profile.MARKER + '{"version":NaN}'],
                            [profile.MARKER + '[]'], [profile.MARKER + json.dumps({**self.declaration, 'provider': 'custom'})]):
            with self.subTest(constraints=constraints):
                self.approve(constraints=constraints)
                with self.assertRaises(ValueError):
                    self.launch()
        self.approve()
        self.state['user_events'] = []
        with self.assertRaisesRegex(ValueError, 'operator contract approval'):
            self.launch()

    def test_profile_mutation_needs_new_approval_even_after_authority_creation(self):
        callback = profile.authority(self.state, self.worker, self.root, self.run)
        self.declaration['max_requests'] = 1
        self.state['goal_contract']['body']['constraints'] = [profile.MARKER + json.dumps(self.declaration)]
        with self.assertRaisesRegex(ValueError, 'operator contract approval'):
            self.launch(callback)
        self.state['goal_contract']['revision'] += 1
        self.approve()
        self.assertEqual(1, json.loads(self.launch(callback)['environment']['AUTOCODE_IMAGE_AUDIT'])['max_requests'])

    def test_wrong_model_effort_provider_and_unapproved_request_caps(self):
        original = deepcopy(self.declaration)
        for key, value in (('model', 'openai/another'), ('reasoning_effort', 'medium'), ('model', 'custom/reviewer'),
                           ('mode', 'custom'), ('opencode_version', '1.18.34'), ('max_requests', 9),
                           ('max_requests', 0), ('max_requests', -1), ('max_requests', True), ('max_requests', 1.5)):
            with self.subTest(key=key, value=value):
                self.declaration = {**original, key: value}
                self.approve()
                with self.assertRaises(ValueError):
                    self.launch()

    def test_source_sdk_mutation_missing_bootstrap_and_writable_manifest_decline(self):
        path = self.sdk / 'node_modules/@opencode-ai/plugin/dist/index.js'
        path.write_text('mutated dependency')
        with self.assertRaisesRegex(ValueError, 'dependency bytes changed'):
            self.launch()
        self.manifest()
        with self.assertRaisesRegex(ValueError, 'manifest changed'):
            self.launch()
        self.declaration['sdk_manifest_sha256'] = util.file_hash(self.sdk / profile.MANIFEST)
        self.approve()
        (self.sdk / profile.MANIFEST).chmod(0o600)
        with self.assertRaisesRegex(ValueError, 'read-only prebootstrap'):
            self.launch()
        (self.sdk / profile.MANIFEST).unlink()
        with self.assertRaisesRegex(ValueError, 'read-only prebootstrap'):
            self.launch()

    def test_reapproved_sdk_contents_are_copied_without_refreshing_old_authority(self):
        path = self.sdk / 'node_modules/@opencode-ai/plugin/dist/index.js'
        path.write_text('// explicitly approved replacement structural fixture\n')
        self.manifest()
        with self.assertRaisesRegex(ValueError, 'manifest changed'):
            self.launch()
        self.declaration['sdk_manifest_sha256'] = util.file_hash(self.sdk / profile.MANIFEST)
        self.state['goal_contract']['revision'] += 1
        self.approve()
        result = self.launch()
        copied = self.directory / 'config/opencode/node_modules/@opencode-ai/plugin/dist/index.js'
        self.assertEqual(util.file_hash(path), result['evidence_hashes'][str(copied)])

    def test_unpinned_or_wrong_dependency_lock_declines_even_with_fresh_manifest(self):
        package = self.sdk / 'package.json'
        package.write_text('{"dependencies":{"@opencode-ai/plugin":"^1.18.33"}}')
        self.manifest()
        self.declaration['sdk_manifest_sha256'] = util.file_hash(self.sdk / profile.MANIFEST)
        self.approve()
        with self.assertRaisesRegex(ValueError, 'pin exactly'):
            self.launch()

    def test_sdk_is_owned_and_does_not_follow_external_or_credential_links(self):
        self.declaration['sdk_path'] = '/external/sdk'
        self.approve()
        with self.assertRaisesRegex(ValueError, 'workspace-owned'):
            self.launch()
        self.declaration['sdk_path'] = '.autocode/linked-sdk'
        (self.root / self.declaration['sdk_path']).symlink_to(self.sdk, target_is_directory=True)
        self.approve()
        with self.assertRaisesRegex(ValueError, 'symlinked'):
            self.launch()
        self.declaration['sdk_path'] = '.autocode/visual-sdk'
        self.approve()
        path = self.sdk / 'node_modules/@opencode-ai/plugin/dist/index.js'
        path.unlink()
        path.symlink_to(self.executable)
        with self.assertRaisesRegex(ValueError, 'escapes'):
            self.launch()

    def test_unpinned_dependency_and_auth_named_file_rejected_before_read(self):
        path = self.sdk / 'node_modules/auth.json'
        path.write_text('DO NOT READ')
        read_bytes = Path.read_bytes
        def no_auth(candidate):
            self.assertNotEqual(path, candidate)
            return read_bytes(candidate)
        with patch.object(Path, 'read_bytes', no_auth), self.assertRaisesRegex(ValueError, 'credential/config'):
            self.launch()
        path.unlink()
        (self.sdk / 'node_modules/unpinned.js').write_text('unpinned')
        with self.assertRaisesRegex(ValueError, 'Unpinned SDK'):
            self.launch()

    def test_custom_configs_credentials_and_tools_decline_without_reading_them(self):
        original = deepcopy(self.env)
        for key, value in (('OPENAI_API_KEY', 'secret'), ('OPENAI_BASE_URL', 'https://custom.invalid'),
                           ('OPENCODE_CONFIG', '/external/config.json'), ('OPENCODE_CONFIG_DIR', '/external'),
                           ('OPENCODE_PERMISSION', '{"*":"allow"}')):
            with self.subTest(key=key):
                self.env = {**original, key: value}
                with self.assertRaises(ValueError):
                    self.launch()
        self.env = original
        for addition in ({'provider': {'openai': {'options': {'apiKey': 'secret'}}}}, {'mcp': {}}, {'tools': {'edit': True}}):
            with self.subTest(addition=addition):
                self.env['OPENCODE_CONFIG_CONTENT'] = json.dumps({**self.config, **addition})
                with self.assertRaises(ValueError):
                    self.launch()

    def test_saved_external_config_or_settings_auth_fields_are_unsupported(self):
        self.state['settings']['roles']['sol']['apiKey'] = 'secret'
        with self.assertRaisesRegex(ValueError, 'Credential/endpoint'):
            self.launch()
        del self.state['settings']['roles']['sol']['apiKey']
        identity = self.state['settings']['transport_identity']
        identity['config_hashes']['/do-not-open/global.json'] = 'c' * 64
        self.declaration['transport_identity_sha256'] = util.digest(identity)
        self.approve()
        with self.assertRaisesRegex(ValueError, 'custom/global configuration'):
            self.launch()

    def test_actual_command_is_fresh_exact_route_and_owned_attachments(self):
        original = self.command[:]
        for suffix in (['--session', 'old'], ['--model', 'openai/other'], ['--agent', 'other'], ['--auto']):
            with self.subTest(suffix=suffix):
                self.command = [*self.base, *suffix, *original[len(self.base):]]
                with self.assertRaises(ValueError):
                    self.launch()
        self.command = original
        self.executable.write_text('#!/bin/sh\nexit 0\n')
        with self.assertRaisesRegex(ValueError, 'executable changed'):
            self.launch()

    def test_image_and_audit_caps_are_checked_before_creating_private_config(self):
        audit = json.loads(self.env['AUTOCODE_IMAGE_AUDIT'])
        for cap in (3, 9, 0, True):
            with self.subTest(cap=cap):
                self.env['AUTOCODE_IMAGE_AUDIT'] = json.dumps({**audit, 'max_requests': cap})
                with self.assertRaisesRegex(ValueError, 'cap exceeds'):
                    self.launch()
                self.assertFalse((self.directory / 'home').exists())
        self.env['AUTOCODE_IMAGE_AUDIT'] = json.dumps(audit)
        self.images[0]['width'] = 3
        with self.assertRaisesRegex(ValueError, 'no-transform bounds'):
            self.launch()

    def test_contained_denies_cannot_be_replaced_by_mutated_stage_configuration(self):
        changed = deepcopy(self.config)
        changed['agent']['autocode_sol']['permission']['bash'] = {'*': 'allow'}
        self.env['OPENCODE_CONFIG_CONTENT'] = json.dumps(changed)
        with self.assertRaisesRegex(ValueError, 'differs from the actual contained worker'):
            self.launch()

    def test_approved_cap_replaces_the_unqualified_runtime_default(self):
        audit = json.loads(self.env['AUTOCODE_IMAGE_AUDIT'])
        self.env['AUTOCODE_IMAGE_AUDIT'] = json.dumps({**audit, 'max_requests': 1})
        result = self.launch()
        self.assertEqual(2, json.loads(result['environment']['AUTOCODE_IMAGE_AUDIT'])['max_requests'])

    def test_prepared_cap_stops_helper_before_second_fetch_without_network(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is unavailable; no install is performed')
        self.declaration['max_requests'] = 1
        self.approve()
        result = self.launch()
        script = '''
globalThis.fetch = async () => {
  process.stdout.write('stub-fetch\\n');
  return new Response('{}', {status: 503});
};
const plugin = (await import(process.argv[1])).default;
const hooks = await plugin();
const output = {headers: {}};
await hooks['chat.headers']({sessionID: 'structural-session', message: {id: 'structural-user'},
  agent: 'autocode_sol', model: {providerID: 'openai', id: 'gpt-6-sol',
  capabilities: {input: {image: true}}}}, output);
for (let i = 0; i < 2; i++) {
  await fetch('https://not-contacted.invalid/responses', {method: 'POST', headers: output.headers,
    body: JSON.stringify({model: 'gpt-6-sol', input: []})});
}
'''
        completed = subprocess.run([node, '--input-type=module', '-e', script, self.plugin.as_uri()],
                                   cwd=self.root, env=result['environment'], capture_output=True, text=True, timeout=10)
        self.assertEqual(77, completed.returncode, completed.stderr)
        self.assertEqual('stub-fetch\n', completed.stdout)
        audit = [json.loads(line) for line in (self.directory / 'delivery.jsonl').read_text().splitlines()]
        denied = [row for row in audit if row['type'] == 'request_denied']
        self.assertEqual(1, len(denied))
        self.assertEqual('max_requests_exhausted', denied[0]['reason'])
        self.assertEqual(1, denied[0]['admitted_requests'])

    def test_actual_prebootstrapped_sdk_if_available(self):
        source = Path(__file__).resolve().parents[1] / '.scenario-runs/visual-delivery-capped-v2/config/opencode'
        if not (source / 'package-lock.json').is_file():
            self.skipTest('Local prebootstrapped native SDK fixture is not available; no install is performed')
        shutil.rmtree(self.sdk)
        self.sdk.mkdir()
        # Only known nonsecret package roots; never copy probe data/HOME/auth.
        for name in ('package.json', 'package-lock.json'):
            shutil.copyfile(source / name, self.sdk / name)
        shutil.copytree(source / 'node_modules', self.sdk / 'node_modules', symlinks=True)
        self.manifest()
        self.declaration['sdk_manifest_sha256'] = util.file_hash(self.sdk / profile.MANIFEST)
        self.approve()
        result = self.launch()
        self.assertGreater(len(result['evidence_hashes']), 100)
        self.assertEqual(self.declaration['sdk_manifest_sha256'], util.file_hash(self.directory / 'config/opencode' / profile.MANIFEST))
        self.assertFalse(any(path.is_symlink() for path in (self.directory / 'config/opencode').rglob('*')))


if __name__ == '__main__':
    unittest.main()
