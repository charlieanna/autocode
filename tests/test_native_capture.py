"""Real Chromium acquisition with controlled planning, never image acceptance.

The opt-in runtime manifest supplies exact existing Node/Playwright/Chromium
files. No runtime installation, model call or legacy receipt adoption occurs.
"""
from pathlib import Path
import json
import os
import sys
import tempfile
import unittest

import autocode_native_capture as capture
import autocode_source_scope as source_scope
import autocode_util as util
import autocode_visual_evidence as evidence
import autocode_visual_profile as profile
from tests.test_native_check_execution import _ControlledNativeCheckFixture


class NativeCaptureTests(_ControlledNativeCheckFixture, unittest.TestCase):
    def setUp(self):
        if sys.platform != 'darwin':
            self.skipTest('Native Chromium containment requires Darwin')
        manifest = os.environ.get('AUTOCODE_NATIVE_CAPTURE_RUNTIME')
        if not manifest:
            self.skipTest('Explicit exact-file native Chromium runtime manifest is required')
        self.runtime_bytes = Path(manifest).read_bytes()
        runtime = json.loads(self.runtime_bytes)
        retained = os.environ.get('AUTOCODE_NATIVE_CAPTURE_TEST_ROOT')
        if retained:
            self.root = Path(retained).resolve()
            self.root.mkdir(mode=0o700, exist_ok=False)
        else:
            temporary = tempfile.TemporaryDirectory(prefix='native-capture-control-')
            self.addCleanup(temporary.cleanup)
            self.root = Path(temporary.name).resolve()
        self.case = {'id': 'only', 'route': '/', 'state': 'ready',
            'viewport': {'width': 320, 'height': 240, 'device_scale_factor': 1},
            'implementation_paths': ['page.html', 'preview.html', 'fixture.cjs', 'capture.json']}
        (self.root / 'page.html').write_text('<!doctype html><html data-ready="yes"><meta charset="utf-8"><title>Owned capture</title><style>html,body{margin:0;background:#171b32;color:white}main{padding:32px;font:24px sans-serif}</style><main>Original approved source</main><iframe id="owned-preview" src="http://127.0.0.1:3000/native-preview/index.html"></iframe></html>')
        (self.root / 'preview.html').write_text('<!doctype html><html><body><h1>Owned preview bytes</h1></body></html>')
        (self.root / 'fixture.cjs').write_text('''const fs=require('node:fs'),http=require('node:http'),path=require('node:path');
let server;
exports.setup=async({page})=>{
 const original=path.resolve(process.cwd(),'../../../..');
 const facts={};
 for(const [key,name,write] of [['original-source','tracked.txt',false],['private','.autocode/secret',false],['git','.git/config',false],['original-write','tracked.txt',true],['copy-write','page.html','copy']]) {
  const target=write==='copy'?path.resolve(name):path.join(original,name);
  try {if(write)fs.writeFileSync(target,'unauthorized');else fs.readFileSync(target);facts[key]='ALLOWED';}
  catch(error){facts[key]=error.code;}
 }
 fs.writeFileSync(path.resolve('../writable/output/kernel.json'),JSON.stringify(facts),{flag:'wx'});
 server=http.createServer((request,response)=>{const file=request.url==='/'?'page.html':request.url==='/native-preview/index.html'?'preview.html':null;if(!file){response.writeHead(404);response.end();return;}response.writeHead(200,{'content-type':'text/html; charset=utf-8'});response.end(fs.readFileSync(file));});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 await page.goto('http://127.0.0.1:'+server.address().port+'/',{waitUntil:'load'});
 const heading=page.frameLocator('#owned-preview').locator('h1');await heading.waitFor();
 if(await heading.textContent()!=='Owned preview bytes')throw Error('Original preview did not render');
};
exports.teardown=async()=>{if(server)await new Promise(resolve=>server.close(resolve));};
''')
        config = {'case': evidence.case_binding(self.case), 'reference_hash': 'e' * 64,
            'fixture': 'fixture.cjs', 'inputs': ['page.html', 'preview.html'],
            'preview_origin': 'http://127.0.0.1:3000',
            'assets': [{'url': '/', 'path': 'page.html'},
                       {'url': 'http://127.0.0.1:3000/native-preview/index.html', 'path': 'preview.html'}],
            'ready': [{'selector': 'html', 'attribute': 'data-ready', 'equals': 'yes'}],
            'build_command': [], 'playwright_module': runtime['playwright_module'], 'executable': runtime['browser']}
        (self.root / 'capture.json').write_text(json.dumps(config))
        self.prepare(self.root)
        (self.root / '.autocode/secret').write_text('must stay private')
        runtime_path = self.root / '.autocode/capture-runtime.json'
        runtime_path.write_bytes(self.runtime_bytes); runtime_path.chmod(0o400)
        declaration = {'version': 1, 'mode': 'isolated-builtin-openai', 'opencode_version': '1.18.33',
            'model': 'openai/gpt-6-sol', 'reasoning_effort': 'high', 'sdk_path': '.autocode/controlled-sdk',
            'sdk_manifest_sha256': 'a'*64, 'transport_identity_sha256': 'b'*64,
            'executable_sha256': 'c'*64, 'max_requests': 2,
            'image_limits': {'width': 320, 'height': 240, 'pixels': 76800, 'bytes': 1048576},
            'capture_runtime': {'path': '.autocode/capture-runtime.json', 'sha256': util.file_hash(runtime_path)}}
        self.state['settings']['design_manifest'].update(manifest_hash='e'*64, body={'version': 1, 'cases': [self.case]})
        self.state['goal_contract']['body']['constraints'].append(profile.MARKER+json.dumps(declaration))
        owned = ['ignored.txt', 'future.txt', 'page.html', 'preview.html', 'fixture.cjs', 'capture.json']
        self.state['goal_contract']['body']['milestones'][0]['affected_paths'] = owned
        self.state['goal_contract']['body']['initial_task']['affected_paths'] = owned
        self.declare([self.command])

    def test_original_approval_real_chromium_capture_and_kernel_boundary(self):
        current = self.approve()
        execution = capture.NativeCaptureExecution(self.context, self.state, persist=self.sink.write)
        captured = execution.all_cases()
        self.assertEqual(set(captured), {'only'})
        result = captured['only']
        body, _refs = evidence.verify(self.state, **result, case=self.case, current=current)
        browser = util.read_object(self.root / body['artifacts']['browser']['path'])
        self.assertEqual(browser['status'], 'CAPTURED')
        self.assertEqual(browser['errors'], [])
        self.assertEqual(set(browser['native_transport']['assets']),
            {'/', 'http://127.0.0.1:3000/native-preview/index.html'})
        preview = next(row for row in browser['responses'] if row['asset'].startswith('http://127.0.0.1:3000/'))
        self.assertEqual(preview['sha256'], current['files']['preview.html'])
        self.assertEqual(browser['responses'][0]['sha256'], current['files']['page.html'])
        pins = self.owner.authenticate_captures(self.state, snapshot=current, captures=captured)
        self.assertIn(result['capture_ref'], pins)
        rows = self.owner._rows()
        finished = [r['body'] for r in rows if r['kind']=='check-finished']
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0]['result']['exit_code'], 0)
        self.assertFalse(finished[0]['result']['timed_out'])
        prepared = next(r['body'] for r in rows if r['kind']=='check-prepared')
        kernel = util.read_object(Path(prepared['boundary']['scratch'])/'writable/output/kernel.json')
        self.assertEqual(set(kernel), {'original-source','private','git','original-write','copy-write'})
        self.assertTrue(all(value in ('EPERM','EACCES') for value in kernel.values()), kernel)
        self.assertEqual(source_scope.snapshot(self.root,self.state),current)
        self.assertEqual(self.original_origin_path.read_bytes(),self.original_origin)
        self.assertEqual(sum(r['kind']=='capture-published' for r in rows),1)
        self.assertFalse(any(r['kind'] in ('prepared','review-committed','checkpoint-committed') for r in rows))
