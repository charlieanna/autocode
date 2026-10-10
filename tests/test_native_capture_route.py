"""Real Node route controls with a fake server/parent, never browser authority."""

import fcntl
import json
import os
import shutil
import socket
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("node"), "Node is required for native route controls")
class NativeCaptureRouteTests(unittest.TestCase):
    def test_exact_targets_fresh_sequences_bounds_and_poison(self):
        module = Path(__file__).resolve().parents[1] / "tools/autocode_native_capture_browser.cjs"
        script = r"""
const assert = require('node:assert/strict'), fs = require('node:fs'), http = require('node:http');
const os = require('node:os'), path = require('node:path'), crypto = require('node:crypto');
const original = http.Server.prototype.listen;
http.Server.prototype.listen = function(_options, callback) { callback(); return this; };
const {install} = require(process.argv[1]);
const dirs = [];
const cdpInputs = JSON.parse(process.argv[2]);
const net = require('node:net');
function setup(targets, preview, port = 43210) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'native-route-control-')); dirs.push(root);
  const requests = path.join(root, 'requests'), responses = path.join(root, 'responses');
  fs.mkdirSync(requests); fs.mkdirSync(responses);
  const fd = fs.openSync(path.join(root, 'lease'), 'wx');
  const info = fs.fstatSync(fd), nonce = 'controlled-original';
  const cdp = {nonce, socket: cdpInputs.shift()};
  let api;try {api = install({native_transport:{cdp, listener:{fd, device:info.dev, inode:info.ino, mode:info.mode,
    address:['127.0.0.1', port]}, nonce, requests, responses},
    timeout_ms:1000, ...(preview===undefined?{}:{preview_origin:preview}), assets:targets.map(url => ({url, path:'unchanged'}))});} catch(error){fs.closeSync(fd);throw error;}
  const channel = net.connect({host:'127.0.0.1', port:80});
  const server = new http.Server(); server.address = () => ({address:'127.0.0.1', port});
  server.listen(0, '127.0.0.1', () => {});
  let fulfilled = 0;
  async function fetch(target, id, {method='GET', body=null, host='127.0.0.1', hash=false, fail=false}={}) {
    const raw = Buffer.from('immutable approved bytes');
    if (id !== null) fs.writeFileSync(path.join(responses, `${id}.json`), JSON.stringify({
      nonce, sequence:id, asset:target, status:200, content_type:'application/json',
      body:raw.toString('base64'), sha256:hash ? 'bad' : crypto.createHash('sha256').update(raw).digest('hex')}));
    await api.route({request:() => ({url:() => target.startsWith('http')?target:`http://${host}:${port}${target}`,
      method:() => method, postData:() => body}), fulfill:async value => {
        if(fail) throw new Error('actual fulfill failure');
        assert.deepEqual(value.body,raw); fulfilled++;
      }});
    if(id !== null) assert.deepEqual(JSON.parse(fs.readFileSync(path.join(requests,`${id}.json`))),
      {nonce, sequence:id, asset:target});
  }
  return {api, fetch, requests, get fulfilled(){return fulfilled;}, close(){api.restore();channel.destroy();fs.closeSync(fd);}};
}
(async () => {
  const target = '/api/run?workspace=%2Fowned%2Fapp&run=saved';
  const one = setup([target]);
  try {
    await one.fetch(target,0); await one.fetch(target,1);
    for(const altered of ['/api/run?run=saved&workspace=%2Fowned%2Fapp',
      '/api/run?workspace=/owned/app&run=saved', target+'&extra=1', target+'#fragment'])
      await assert.rejects(one.fetch(altered,null), /escaped/);
    await assert.rejects(one.fetch(target,null,{method:'POST'}),/escaped/);
    await assert.rejects(one.fetch(target,null,{body:'not empty'}),/escaped/);
    await assert.rejects(one.fetch(target,null,{host:'localhost'}),/escaped/);
    assert.equal(one.fulfilled,2); assert.deepEqual(one.api.verify().assets,[target]);
    for(let id=2;id<64;id++) await one.fetch(target,id);
    await assert.rejects(one.fetch(target,null),/request bound/);
    assert.equal(fs.readdirSync(one.requests).length,64);
  } finally {one.close();}
  const many = setup(['/a','/b','/c']);
  try {
    for(let id=0;id<128;id++) await many.fetch(['/a','/b','/c'][id%3],id);
    await assert.rejects(many.fetch('/c',null),/request bound/);
    assert.equal(many.fulfilled,128); assert.equal(fs.readdirSync(many.requests).length,128);
  } finally {many.close();}
  for(const failure of [{hash:true},{fail:true}]) {
    const broken = setup(['/']);
    try {
      await assert.rejects(broken.fetch('/',0,failure),/body changed|fulfill failure/);
      await assert.rejects(broken.fetch('/',null),/poisoned/);
      assert.throws(() => broken.api.verify(),/poisoned/);
      assert.equal(fs.readdirSync(broken.requests).length,1);
    } finally {broken.close();}
  }
  const virtual='http://127.0.0.1:3000', targetPreview=virtual+'/native-preview/index.html';
  for(const [origin,targets] of [[undefined,[targetPreview]],['https://127.0.0.1:3000',[targetPreview]],
    ['http://localhost:3000',[targetPreview]],['http://127.0.0.2:3000',[targetPreview]],
    ['http://127.0.0.1:3001',[targetPreview]],[virtual,['/']],
    [virtual,[virtual+'/%2e%2e/private']],[virtual,[targetPreview+'#fragment']]])
    assert.throws(()=>setup(targets,origin),/Native capture:/);
  assert.throws(()=>setup([targetPreview],virtual,3000),/collides/);
  const preview = setup(['/',targetPreview,virtual+'/style.css'],virtual);
  try {
    await preview.fetch('/',0);await preview.fetch(targetPreview,1);
    assert.throws(()=>preview.api.verify(),/incomplete/);
    for(const target of ['https://127.0.0.1:3000/native-preview/index.html','http://localhost:3000/native-preview/index.html',
      'http://127.0.0.2:3000/native-preview/index.html','http://127.0.0.1:3001/native-preview/index.html',
      virtual+'/other',targetPreview+'?extra=1',targetPreview+'#fragment',virtual+'/%2e%2e/private'])
      await assert.rejects(preview.fetch(target,null),/escaped/);
    for(let id=2;id<128;id++) await preview.fetch(['/',targetPreview,virtual+'/style.css'][id%3],id);
    assert.equal(preview.api.verify().assets.length,3);
    await assert.rejects(preview.fetch(targetPreview,null),/request bound/);
  } finally {preview.close();}
  const poisonedPreview=setup([targetPreview],virtual);
  try {
    await assert.rejects(poisonedPreview.fetch(targetPreview,0,{hash:true}),/body changed/);
    await assert.rejects(poisonedPreview.fetch('/',null),/poisoned/);
  } finally {poisonedPreview.close();}
  console.log('EXACT_ROUTE_CONTROLS_PASS');
})().finally(() => {
  http.Server.prototype.listen = original;
  for(const root of dirs) fs.rmSync(root,{recursive:true,force:true});
}).catch(error => {console.error(error);process.exitCode=1;});
"""
        sockets, facts = [], []
        try:
            for _ in range(15):
                parent, original = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
                child = socket.socket(fileno=fcntl.fcntl(original.fileno(), fcntl.F_DUPFD_CLOEXEC, 10))
                original.close()
                sockets.extend((parent, child))
                info = os.fstat(child.fileno())
                facts.append({"fd": child.fileno(), "device": info.st_dev, "inode": info.st_ino, "mode": info.st_mode})
            result = subprocess.run(
                [shutil.which("node"), "-e", script, str(module), json.dumps(facts)],
                pass_fds=tuple(row["fd"] for row in facts),
                capture_output=True,
                text=True,
                timeout=20,
            )
        finally:
            for item in sockets:
                item.close()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), "EXACT_ROUTE_CONTROLS_PASS")
