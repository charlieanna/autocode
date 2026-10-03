"""Real loopback Chromium capture checks; opt in with an installed Playwright module.

No model or Figma calls. Regular guard/CLI tests require no browser installation.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid

import autocode_util as util
import autocode_visual_evidence as evidence

ROOT = Path(__file__).resolve().parents[1]
MODULE = os.environ.get('AUTOCODE_TEST_PLAYWRIGHT')
FIXTURE = r'''
const http = require('node:http');
const fs = require('node:fs');
let server;
exports.setup = async ({page}) => {
  const bytes = fs.readFileSync(fs.existsSync('served.html') ? 'served.html' : 'index.html');
  server = http.createServer((req, res) => { res.writeHead(200, {'content-type': 'text/html'}); res.end(bytes); });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  await page.goto(`http://127.0.0.1:${server.address().port}/screen`);
};
exports.teardown = async () => {
  if (server) { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
  if (fs.existsSync('change-during-capture')) fs.writeFileSync('index.html', 'changed during teardown');
};
'''


@unittest.skipUnless(MODULE, 'Set AUTOCODE_TEST_PLAYWRIGHT to an installed Playwright module for real browser checks')
class RealCaptureCliTests(unittest.TestCase):
    def setUp(self):
        retained = os.environ.get('AUTOCODE_TEST_CAPTURE_EVIDENCE')
        if retained:
            self.root = Path(retained).resolve() / (self._testMethodName + '-' + uuid.uuid4().hex[:8])
            self.root.mkdir(parents=True)
        else:
            temp = tempfile.TemporaryDirectory(prefix='visual-capture-')
            self.addCleanup(temp.cleanup)
            self.root = Path(temp.name).resolve()
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        subprocess.run(['git', '-C', str(self.root), '-c', 'user.name=T', '-c', 'user.email=t@example.test',
                        'commit', '-q', '--allow-empty', '-m', 'base'], check=True)
        (self.root / 'index.html').write_text('<!doctype html><body data-state="ready" style="background:#113355;color:white">Version A</body>')
        (self.root / 'fixture.cjs').write_text(FIXTURE)
        self.case = {'id': 'screen', 'state': 'ready', 'route': '/screen',
                     'viewport': {'width': 320, 'height': 180, 'device_scale_factor': 1}}
        self.config = {'reference_hash': 'a' * 64, 'case': self.case, 'fixture': 'fixture.cjs', 'inputs': [],
                       'assets': [{'url': '/screen', 'path': 'index.html'}], 'build_command': [],
                       'ready': [{'selector': 'body', 'attribute': 'data-state', 'equals': 'ready'}],
                       'playwright_module': MODULE}
        if os.environ.get('AUTOCODE_TEST_CHROMIUM'):
            self.config['executable'] = os.environ['AUTOCODE_TEST_CHROMIUM']
        self.write_config()
        self.state = {'workspace': str(self.root), 'settings': {'design_manifest': {
            'manifest_hash': 'a' * 64, 'body': {'cases': [self.case]}}}}

    def write_config(self):
        util.atomic_json(self.root / 'capture.json', self.config)

    def capture(self, expected=0):
        result = subprocess.run([sys.executable, str(ROOT / 'tools/autocode.py'), 'visual-capture',
            '--config', 'capture.json', '--timeout', '30'], cwd=self.root, capture_output=True, text=True, timeout=40)
        self.assertEqual(expected, result.returncode, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_real_a_then_b_selects_only_current_image_and_preserves_history(self):
        a = self.capture()
        original = Path(a['candidate_ref']).read_bytes()
        (self.root / 'index.html').write_text('<!doctype html><body data-state="ready" style="background:#aa5522">Version B</body>')
        b = self.capture()
        self.assertNotEqual(original, Path(b['candidate_ref']).read_bytes())
        self.assertEqual(original, Path(a['candidate_ref']).read_bytes())
        with self.assertRaisesRegex(ValueError, 'Stale implementation capture'):
            evidence.verify(self.state, a['capture_ref'], a['capture_sha256'], case=self.case)
        selected = evidence.context(self.state, util.snapshot(self.root))
        self.assertEqual([b['capture_ref']], [row['capture_ref'] for row in selected['current']])
        self.assertIsNone(b['visual_acceptance'])

    def test_browser_serving_old_bytes_is_refused_even_when_disk_source_is_current(self):
        (self.root / 'served.html').write_text('<!doctype html><body data-state="ready">Old deployed version</body>')
        self.capture(2)
        failures = list((self.root / '.autocode/captures').glob('*/browser.json'))
        self.assertEqual(1, len(failures))
        self.assertIn('Served asset differs', ' '.join(util.read_object(failures[0])['errors']))
        self.assertEqual([], list((self.root / '.autocode/captures').glob('*/manifest.json')))

    def test_source_change_during_capture_cannot_publish_a_usable_manifest(self):
        (self.root / 'change-during-capture').write_text('yes')
        result = self.capture(2)
        self.assertIn('Source changed during capture', result['reason'])
        self.assertEqual([], list((self.root / '.autocode/captures').glob('*/manifest.json')))
        self.assertTrue(list((self.root / '.autocode/captures').glob('*/candidate.png')))

    def test_failed_setup_retains_diagnostics_and_cannot_publish_a_capture(self):
        (self.root / 'fixture.cjs').write_text(FIXTURE.replace('const bytes =', "throw new Error('Fixture not ready');\n  const bytes ="))
        self.capture(2)
        attempts = list((self.root / '.autocode/captures').iterdir())
        self.assertEqual(1, len(attempts))
        self.assertTrue((attempts[0] / 'failure.json').is_file())
        self.assertIn('Fixture not ready', ' '.join(util.read_object(attempts[0] / 'browser.json')['errors']))
        self.assertFalse((attempts[0] / 'manifest.json').exists())

    def test_generated_assets_require_a_fresh_build_and_remain_bound_afterwards(self):
        (self.root / '.gitignore').write_text('dist/\n')
        (self.root / 'build.py').write_text('from pathlib import Path\np=Path("dist"); p.mkdir(exist_ok=True)\n(p/"index.html").write_bytes(Path("index.html").read_bytes())\n')
        (self.root / 'fixture.cjs').write_text(FIXTURE.replace("fs.existsSync('served.html') ? 'served.html' : 'index.html'", "'dist/index.html'"))
        self.config['assets'][0]['path'] = 'dist/index.html'
        self.config['build_command'] = [sys.executable, 'build.py']
        self.write_config()
        capture = self.capture()
        evidence.verify(self.state, capture['capture_ref'], capture['capture_sha256'], case=self.case)
        (self.root / 'dist/index.html').write_text('mutated generated assets')
        with self.assertRaisesRegex(ValueError, 'Capture input changed'):
            evidence.verify(self.state, capture['capture_ref'], capture['capture_sha256'], case=self.case)
        self.config['build_command'] = []
        self.write_config()
        self.assertIn('require a fresh build_command', self.capture(2)['reason'])
