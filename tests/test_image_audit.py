"""Actual plugin activity controls; these do not qualify native configuration."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(shutil.which('node'), 'Existing Node runtime needed')
class ImageAuditLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.audit = self.directory / 'audit.jsonl'
        self.fetch_marker = self.directory / 'fetch-called'
        self.plugin = Path(__file__).resolve().parents[1] / 'tools' / 'autocode_image_delivery.mjs'
        self.options = {'path': str(self.audit), 'attempt_id': 'actual-plugin-control',
                        'binding_sha256': 'a' * 64, 'max_requests': 1,
                        'reviewer': {'provider': 'opencode', 'model': 'openai/gpt-6-sol', 'agent': 'validator'}}

    def run_plugin(self, script, *, options=None, fetch=None):
        prefix = """
            import {pathToFileURL} from 'node:url';
            import {existsSync, writeFileSync} from 'node:fs';
            const [plugin, audit, marker] = process.argv.slice(1);
            const input = {sessionID:'review-session', agent:'validator', message:{id:'review-user'},
              model:{providerID:'openai',id:'gpt-6-sol',capabilities:{input:{image:true}}}};
        """
        stub = fetch or "globalThis.fetch = async () => {writeFileSync(marker, 'unexpected'); process.exit(99);};"
        load = "const {default: load} = await import(pathToFileURL(plugin)); const hooks = await load();"
        environment = {'PATH': os.environ.get('PATH', ''),
                       'AUTOCODE_IMAGE_AUDIT': json.dumps(self.options if options is None else options)}
        return subprocess.run([shutil.which('node'), '--input-type=module', '-e', prefix + stub + load + script,
                               str(self.plugin), str(self.audit), str(self.fetch_marker)],
                              env=environment, capture_output=True, text=True, timeout=10)

    def rows(self):
        rows = [json.loads(line) for line in self.audit.read_text().splitlines()]
        self.assertEqual(list(range(len(rows))), [row['sequence'] for row in rows])
        self.assertEqual('audit_start', rows[0]['type'])
        self.assertEqual(1, sum(row['type'] == 'audit_start' for row in rows))
        self.assertEqual(hashlib.sha256(self.plugin.read_bytes()).hexdigest(), rows[0]['plugin_sha256'])
        self.assertEqual(0o600, self.audit.stat().st_mode & 0o777)
        return rows

    def test_loading_and_unrelated_configuration_activity_do_not_consume_audit(self):
        result = self.run_plugin("""
            await hooks.event({event:{type:'session.created',properties:{}}});
            await hooks.event({event:{type:'message.updated',properties:{info:{role:'user'}}}});
            await hooks.event({event:{type:'message.part.updated',properties:{part:{type:'text'}}}});
            if (existsSync(audit)) process.exit(98);
            const response = await fetch('https://metadata.invalid/models');
            if (await response.text() !== 'metadata') process.exit(97);
            await fetch('https://metadata.invalid/oauth/token',
              {method:'POST',body:'grant_type=refresh_token&refresh_token=CONTROL_SECRET'});
            if (existsSync(audit)) process.exit(96);
        """, fetch="globalThis.fetch = async () => new Response('metadata');")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(self.audit.exists())
        self.assertFalse(self.fetch_marker.exists())

    def test_first_chat_activity_starts_exactly_one_audit_before_context(self):
        result = self.run_plugin("""
            if (existsSync(audit)) process.exit(98);
            await hooks['chat.headers'](input, {headers:{}});
            await hooks['chat.headers'](input, {headers:{}});
        """)
        self.assertEqual(0, result.returncode, result.stderr)
        rows = self.rows()
        self.assertEqual(['audit_start', 'review_context', 'review_context'], [row['type'] for row in rows])
        self.assertEqual(self.options['binding_sha256'], rows[0]['binding_sha256'])
        self.assertEqual(self.options['reviewer'], rows[0]['reviewer'])
        self.assertNotEqual(rows[1]['request_id'], rows[2]['request_id'])
        self.assertFalse(self.fetch_marker.exists())

    def test_first_native_activity_starts_audit_without_fabricating_request(self):
        result = self.run_plugin("""
            await hooks.event({event:{type:'message.updated',properties:{info:{role:'assistant',
              sessionID:'review-session',id:'assistant',parentID:'review-user',providerID:'openai',
              modelID:'gpt-6-sol',agent:'validator'}}}});
            await hooks.event({event:{type:'message.part.updated',properties:{part:{type:'step-start',
              sessionID:'review-session',messageID:'assistant',id:'start'}}}});
        """)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(['audit_start', 'native_message', 'native_start'], [row['type'] for row in self.rows()])
        self.assertFalse(self.fetch_marker.exists())

    def test_first_denied_activity_is_audited_and_stops_before_fetch(self):
        cases = {
            'unknown_review_nonce': "await fetch('https://model.invalid/custom', {headers:{'x-autocode-image-request':'unknown'}});",
            'unattributed_inference': "await fetch('https://model.invalid/responses', {method:'POST',body:'{}'});",
            'unclassified_request_body': "await fetch('https://model.invalid/custom', {method:'POST',body:'unclassified'});",
            'unexpected_review_context': "await hooks['chat.headers']({...input, agent:'builder'}, {headers:{}});",
        }
        for reason, script in cases.items():
            with self.subTest(reason=reason):
                self.audit = self.directory / (reason + '.jsonl')
                self.options['path'] = str(self.audit)
                result = self.run_plugin(script + 'process.exit(98);')
                self.assertEqual(77, result.returncode, result.stderr)
                rows = self.rows()
                self.assertEqual(['audit_start', 'request_denied'], [row['type'] for row in rows])
                self.assertEqual(reason, rows[1]['reason'])
                self.assertEqual(0, rows[1]['admitted_requests'])
                self.assertFalse(self.fetch_marker.exists())

    def test_existing_audit_is_preserved_and_cannot_be_reused_on_first_activity(self):
        original = b'previous attempt remains unchanged\n'
        self.audit.write_bytes(original)
        result = self.run_plugin("""
            await hooks['chat.headers'](input, {headers:{}});
            await fetch('https://model.invalid/responses');
        """)
        self.assertEqual(78, result.returncode, result.stderr)
        self.assertEqual(original, self.audit.read_bytes())
        self.assertFalse(self.fetch_marker.exists())

    def test_second_loaded_instance_cannot_open_the_same_audit(self):
        result = self.run_plugin("""
            const second = await load();
            if (existsSync(audit)) process.exit(98);
            await hooks['chat.headers'](input, {headers:{}});
            await second['chat.headers'](input, {headers:{}});
            process.exit(97);
        """)
        self.assertEqual(78, result.returncode, result.stderr)
        self.assertEqual(['audit_start', 'review_context'], [row['type'] for row in self.rows()])
        self.assertFalse(self.fetch_marker.exists())

    def test_invalid_options_fail_during_setup_before_audit_or_fetch(self):
        result = self.run_plugin('process.exit(98);', options={**self.options, 'max_requests': 0})
        self.assertEqual(78, result.returncode, result.stderr)
        self.assertFalse(self.audit.exists())
        self.assertFalse(self.fetch_marker.exists())


if __name__ == '__main__':
    unittest.main()
