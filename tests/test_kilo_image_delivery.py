"""Pure wire and controlled receipt regressions; no native/model authority."""

import base64
import hashlib
import json
import shutil
import struct
import subprocess
import sys
import types
import unittest
import zlib
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import autocode_kilo_image_delivery as delivery
import autocode_util as util

HELPER = Path(__file__).resolve().parents[1] / "tools/autocode_kilo_image_delivery.mjs"
MODEL = "claude-opus-5-5"


def sha(value):
    return hashlib.sha256(value).hexdigest()


def png():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\0\xff\0\0\xff"))
        + chunk(b"IEND", b"")
    )


def request():
    return {
        "model": MODEL,
        "stream": True,
        "max_tokens": 4096,
        "output_config": {"effort": "high"},
        "thinking": {"type": "adaptive"},
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Inspect both original captures"},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": base64.b64encode(png()).decode(),
                        },
                    },
                ],
            }
        ],
    }


def response():
    return {
        "type": "message",
        "role": "assistant",
        "id": "msg_response",
        "model": MODEL,
        "content": [{"type": "text", "text": '{"status":"PASS"}'}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
    }


def sse(rows):
    return "".join("event: " + row["type"] + "\ndata: " + json.dumps(row) + "\n\n" for row in rows)


def stream():
    message = response() | {"content": [], "stop_reason": None}
    return [
        {"type": "message_start", "message": message},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": '{"status":"PASS"}'}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}},
        {"type": "message_stop"},
    ]


class AnthropicWireTests(unittest.TestCase):
    def decode(self, mode, values):
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node is required for the actual JavaScript decoder")
        source = """
import {readFileSync} from 'node:fs';
const {decodeRequest,decodeResponse}=await import(process.argv[1]);
const input=JSON.parse(readFileSync(0,'utf8'));
const decode=input.mode==='response'?decodeResponse:value=>decodeRequest(value,{requireImages:input.mode!=='source-request'});
console.log(JSON.stringify(input.values.map(value=>{
 try{return {ok:true,value:decode(value)}}catch(e){return {ok:false,error:e.message}}
})));
"""
        result = subprocess.run(
            [node, "--input-type=module", "-e", source, HELPER.as_uri()],
            input=json.dumps({"mode": mode, "values": values}),
            text=True,
            capture_output=True,
            timeout=10,
            check=True,
        )
        return json.loads(result.stdout)

    def test_final_png_bytes_order_and_nested_tool_results(self):
        body = request()
        image = deepcopy(body["messages"][0]["content"][1])
        body["messages"].append(
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call1", "content": [image]}]}
        )
        wire = json.dumps(body)
        parsed = self.decode("request", [wire])[0]
        self.assertTrue(parsed["ok"])
        self.assertEqual(
            [{"sha256": sha(png()), "mime": "image/png", "bytes": len(png())}] * 2, parsed["value"]["images"]
        )
        self.assertEqual(sha(wire.encode()), parsed["value"]["body_sha256"])
        self.assertEqual("anthropic_messages", parsed["value"]["request_wire_format"])

    def test_foreign_route_reasoning_and_image_encodings_refuse(self):
        variants = []
        for key, value in (
            ("model", "other"),
            ("stream", False),
            ("max_tokens", 0),
            ("output_config", {"effort": "max"}),
            ("thinking", {"type": "enabled"}),
        ):
            body = request()
            body[key] = value
            variants.append(body)
        for value in (
            {"type": "url", "url": "https://example.invalid/image.png"},
            {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(png()).decode()},
            {"type": "base64", "media_type": "image/png", "data": "AQ=="},
            {"type": "base64", "media_type": "image/png", "data": base64.b64encode(png()[:-1]).decode()},
            {"type": "base64", "media_type": "image/png", "data": base64.b64encode(png() + b"extra").decode()},
        ):
            body = request()
            body["messages"][0]["content"][1]["source"] = value
            variants.append(body)
        corrupt = bytearray(png())
        corrupt[30] ^= 1
        body = request()
        body["messages"][0]["content"][1]["source"]["data"] = base64.b64encode(corrupt).decode()
        variants.append(body)
        no_image = request()
        no_image["messages"][0]["content"].pop()
        variants.append(no_image)
        for item in self.decode("request", [json.dumps(v) for v in variants]):
            self.assertFalse(item["ok"], item)

    def test_real_messages_and_complete_sse_share_terminal_text(self):
        values = [json.dumps(response()), sse(stream())]
        parsed = self.decode("response", values)
        for item, wire in zip(parsed, values, strict=False):
            self.assertTrue(item["ok"], item)
            self.assertEqual("stop", item["value"]["finish_reason"])
            self.assertEqual(sha(b'{"status":"PASS"}'), item["value"]["output_text_sha256"])
            self.assertEqual(sha(wire.encode()), item["value"]["response_body_sha256"])

    def test_sse_partial_tools_and_fragmented_thinking_signature(self):
        rows = stream()[:1] + [
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "thinking", "thinking": "", "signature": ""},
            },
            {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "reason"}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "signed"}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "_tail"}},
            {"type": "content_block_stop", "index": 0},
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "tool_use", "id": "call_1", "name": "read", "input": {}},
            },
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": '{"path":'},
            },
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": '"file"}'},
            },
            {"type": "content_block_stop", "index": 1},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None}},
            {"type": "message_stop"},
        ]
        item = self.decode("response", [sse(rows)])[0]
        self.assertTrue(item["ok"], item)
        self.assertEqual(["call_1"], item["value"]["tool_call_ids"])
        self.assertEqual("tool_calls", item["value"]["finish_reason"])

    def test_truncated_foreign_error_or_reordered_stream_is_never_complete(self):
        variants = [sse(stream())[:-1], sse(stream()[:-1]), sse(stream() + [{"type": "ping"}])]
        rows = stream()
        rows[1]["index"] = 1
        variants.append(sse(rows))
        rows = stream()
        rows[3]["index"] = 1
        variants.append(sse(rows))
        rows = stream()
        rows[4]["delta"]["stop_reason"] = "max_tokens"
        variants.append(sse(rows))
        rows = stream()
        rows[0]["message"]["model"] = "foreign"
        variants.append(sse(rows))
        variants.append(sse([{"type": "error", "error": {"type": "overloaded_error"}}]))
        message = response()
        message["stop_reason"] = "refusal"
        variants.append(json.dumps(message))
        message = response()
        message["content"] = [{"type": "tool_use", "id": "same", "name": "x", "input": {}}] * 2
        message["stop_reason"] = "tool_use"
        variants.append(json.dumps(message))
        for item in self.decode("response", variants):
            self.assertFalse(item["ok"], item)

    def test_source_only_completion_preserves_high_without_image_credit(self):
        body = request()
        body["messages"][0]["content"].pop()
        wire = json.dumps(body)
        self.assertFalse(self.decode("request", [wire])[0]["ok"])
        source = self.decode("source-request", [wire])[0]
        self.assertTrue(source["ok"], source)
        self.assertEqual([], source["value"]["images"])
        self.assertEqual("high", source["value"]["reasoning_effort"])
        body["output_config"]["effort"] = "max"
        self.assertFalse(self.decode("source-request", [json.dumps(body)])[0]["ok"])

    def test_effective_config_injects_same_fetch_and_refuses_loader_fallback(self):
        # Pure config-hook calls only: no SDK, socket, fetch, native or credential.
        source = """
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
const plugin=await import(process.argv[1]);
const reviewer={provider:'kilocode',model:'anthropic/claude-opus-5-5',agent:'validator'};
const fixed={socket:'/private/tmp/fixture-only.sock',token:'a'.repeat(64),rpc_timeout_seconds:10,max_requests:1,
 reviewer,binding_sha256:'b'.repeat(64),attempt_id:'fixture',
 plugin_sha256:createHash('sha256').update(readFileSync(fileURLToPath(process.argv[1]))).digest('hex'),
 source_hooks:{path:'/owned/fixture-only-custody.ts',sha256:'c'.repeat(64)}};
process.env.AUTOCODE_KILO_SOURCE_RELAY=JSON.stringify(fixed);
process.env.AUTOCODE_IMAGE_AUDIT=JSON.stringify({reviewer,max_requests:1,binding_sha256:fixed.binding_sha256,attempt_id:'fixture'});
const hooks=await plugin.default.server();
const config={enabled_providers:['anthropic'],model:reviewer.model,provider:{anthropic:{options:{
 apiKey:'autocode-source-derivation-public-placeholder',baseURL:'https://api.anthropic.com/v1'}}}};
if(process.argv[2]==='foreign')config.provider.anthropic.options.baseURL='https://example.invalid';
await hooks.config(config);
console.log(JSON.stringify({id:plugin.default.id,same:config.provider.anthropic.options.fetch===globalThis.fetch,
 frozen:Object.isFrozen(globalThis.__autocodeKiloSourceRelay_v1),native:false}));
"""
        node = shutil.which("node")
        result = subprocess.run(
            [node, "--input-type=module", "-e", source, HELPER.as_uri(), "valid"],
            text=True,
            capture_output=True,
            timeout=10,
            check=True,
        )
        self.assertEqual(
            {"id": "autocode-kilo-image-delivery", "same": True, "frozen": True, "native": False},
            json.loads(result.stdout),
        )
        refused = subprocess.run(
            [node, "--input-type=module", "-e", source, HELPER.as_uri(), "foreign"],
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(78, refused.returncode)
        self.assertEqual("", refused.stdout)


class ControlledClientConformanceTests(unittest.TestCase):
    """Callback controls only; no sockets, child clients, models or kernel credit."""

    def probe(self, scenario):
        source = """
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
const {clientConformanceHooks}=await import(process.argv[1]);
const hash=x=>createHash('sha256').update(x).digest('hex');
const bytes=readFileSync(fileURLToPath(process.argv[1]));
const config={model:'anthropic/claude-opus-5-5',small_model:'anthropic/claude-opus-5-5',
 enabled_providers:['anthropic'],provider:{anthropic:{options:{
 apiKey:'autocode-source-derivation-public-placeholder',baseURL:'https://api.anthropic.com/v1'}}},
 agent:{validator:{model:'anthropic/claude-opus-5-5',mode:'primary'}}};
const dirs=['HOME','XDG_CONFIG_HOME','XDG_DATA_HOME','XDG_CACHE_HOME','XDG_STATE_HOME',
 'TMPDIR','OPENCODE_TEST_MANAGED_CONFIG_DIR','KILO_CONFIG_DIR'];
const fixture={version:1,mode:'root',nonce:'1'.repeat(64),token:'2'.repeat(64),
 socket:'/private/tmp/fixture.sock',executable:'/owned/fixture/kilo',executable_sha256:hash('compiled-fixture'),
 plugin_sha256:hash(bytes),config_sha256:hash(JSON.stringify(config)),agent:'validator',
 model:config.model,tcp:{host:'192.0.2.1',port:443,timeout_ms:2000},rpc_timeout_seconds:10,
 foreign_timeout_ms:15000,foreign_environment:Object.fromEntries(dirs.map(k=>[k,'/owned/tmp/foreign/'+k]))};
async function run(change=()=>{}){
 const options=structuredClone(fixture), cfg=structuredClone(config), calls=[];
 const io={env:{TMPDIR:'/owned/tmp',KILO_CONFIG_CONTENT:JSON.stringify(config)},pid:321,
 executable:fixture.executable,read:p=>p===fixture.executable?Buffer.from('compiled-fixture'):bytes,
 realpath:p=>p,directory:()=>true,refuse:()=>{calls.push('refuse')},
 rpc:async(o,p)=>{calls.push(p.op);return p.op==='CLIENT_CONFORMANCE_RESULT'
  ?{ok:true,kind:'CLIENT_CONFORMANCE_COMPLETE',nonce:o.nonce}
  :{ok:o.mode==='root',kind:o.mode==='root'?'CLIENT_CONFORMANCE_PARENT_PONG':'CLIENT_CONFORMANCE_FOREIGN_PEER_DENIED',
    nonce:o.nonce,peer_pid:321,peer_birth:123.5}},
 tcp:async t=>{calls.push('tcp');return {...t,status:'denied',code:'EPERM'}},
 foreign:async()=>{calls.push('foreign');return 0}};
 change({options,cfg,io,calls});
 try{
  const hook=clientConformanceHooks(options,io);await hook.config(cfg);
  let inferenceDenied=false;try{await cfg.provider.anthropic.options.fetch()}catch{inferenceDenied=true}
  return {ok:true,calls,inferenceDenied,keys:Object.keys(hook),
   sourceCapsule:Object.hasOwn(globalThis,'__autocodeKiloSourceRelay_v1')};
 }catch(e){return {ok:false,calls,error:e.message}}
}
let results;
switch(process.argv[2]){
 case 'root':results=[await run()];break;
 case 'negative':results=[];
  for(const code of ['ETIMEDOUT','ENETUNREACH','ECONNREFUSED'])results.push(await run(({io,calls})=>{
   io.tcp=async t=>{calls.push('tcp');return {...t,status:'denied',code}}}));
  results.push(await run(({io,calls})=>{io.foreign=async()=>{calls.push('foreign');return 1}}));
  results.push(await run(({io,calls})=>{io.rpc=async()=>{calls.push('bad-parent');
   return {ok:true,kind:'CLIENT_CONFORMANCE_PARENT_PONG',nonce:'0'.repeat(64),peer_pid:321,peer_birth:123.5}}}));
  results.push(await run(({cfg})=>{cfg.provider.anthropic.options.baseURL='https://example.invalid'}));break;
 case 'foreign':results=[await run(({options,io})=>{
   options.mode='foreign';Object.assign(io.env,options.foreign_environment)}),
  await run(({options,io,calls})=>{options.mode='foreign';Object.assign(io.env,options.foreign_environment);
   io.rpc=async(o,p)=>{calls.push(p.op);return {ok:true,kind:'CLIENT_CONFORMANCE_PARENT_PONG',
    nonce:o.nonce,peer_pid:321,peer_birth:123.5}}})];break;
 case 'pins':results=[];
  for(const mutate of [({options})=>options.plugin_sha256='0'.repeat(64),
   ({options})=>options.executable_sha256='0'.repeat(64),
   ({options})=>options.config_sha256='0'.repeat(64),
   ({options})=>options.foreign_environment.HOME='/outside/home',
   ({options})=>options.tcp.host='127.0.0.1',
   ({options})=>options.mode='inference'])results.push(await run(mutate));break;
 default:throw new Error('Unknown controlled scenario');
}
console.log(JSON.stringify(results));
"""
        node = shutil.which("node")
        self.assertIsNotNone(node)
        result = subprocess.run(
            [node, "--input-type=module", "-e", source, HELPER.as_uri(), scenario],
            text=True,
            capture_output=True,
            timeout=10,
            check=True,
        )
        return json.loads(result.stdout)

    def test_original_callback_orders_controlled_probes_without_source_authority(self):
        result = self.probe("root")[0]
        self.assertTrue(result["ok"])
        self.assertEqual(["CLIENT_CONFORMANCE_HELLO", "tcp", "foreign", "CLIENT_CONFORMANCE_RESULT"], result["calls"])
        self.assertEqual(["config"], result["keys"])
        self.assertTrue(result["inferenceDenied"])
        self.assertFalse(result["sourceCapsule"])

    def test_timeout_route_peer_and_failed_child_cannot_become_kernel_pass(self):
        results = self.probe("negative")
        self.assertEqual(6, len(results))
        for result in results:
            self.assertFalse(result["ok"], result)
            self.assertNotIn("CLIENT_CONFORMANCE_RESULT", result["calls"])

    def test_foreign_callback_requires_explicit_refusal_and_never_spawns(self):
        denied, wrongly_accepted = self.probe("foreign")
        self.assertTrue(denied["ok"])
        self.assertEqual(["CLIENT_CONFORMANCE_HELLO"], denied["calls"])
        self.assertFalse(denied["sourceCapsule"])
        self.assertFalse(wrongly_accepted["ok"])
        self.assertNotIn("tcp", wrongly_accepted["calls"])
        self.assertNotIn("foreign", wrongly_accepted["calls"])

    def test_changed_pins_schema_and_foreign_directories_refuse_before_io(self):
        results = self.probe("pins")
        self.assertEqual(6, len(results))
        for result in results:
            self.assertFalse(result["ok"], result)
            self.assertEqual([], result["calls"])


class ControlledRelayLifetimeTests(unittest.TestCase):
    def test_both_channels_keep_write_side_open_until_framed_reply_then_destroy(self):
        # Replace Node's existing built-in transport binding in one isolated
        # process. No socket, native client, SDK, model or clock wait is used.
        source = r"""
import fs from 'node:fs';
import net from 'node:net';
import {EventEmitter} from 'node:events';
import {syncBuiltinESMExports} from 'node:module';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
const hash=x=>createHash('sha256').update(x).digest('hex');
const helperPath=fileURLToPath(process.argv[1]),helperBytes=fs.readFileSync(helperPath);
const calls=[],sockets=[];
// Node's ESM loader reads the helper itself through fs.readFileSync, by URL.
fs.readFileSync=file=>(path=>path===process.execPath?Buffer.from('controlled-client')
 :path===helperPath?helperBytes:(()=>{throw new Error('Unexpected controlled read')})())(
 file instanceof URL?fileURLToPath(file):file);
fs.realpathSync=path=>path;
fs.statSync=()=>({isDirectory:()=>true});
net.createConnection=()=>{
 const socket=new EventEmitter();socket.writableEnded=false;socket.destroyed=false;
 socket.setTimeout=ms=>{socket.bound=ms};
 socket.destroy=()=>{socket.destroyed=true};
 socket.write=frame=>{
  const text=Buffer.from(frame).toString('utf8'),payload=JSON.parse(text);
  const observation={op:payload.op,framed:text.endsWith('\n')&&text.split('\n').length===2,
   token:payload.token,writableEnded:null,bound:socket.bound};calls.push(observation);
  queueMicrotask(()=>{
   observation.writableEnded=socket.writableEnded;
   const reply=payload.op==='CLIENT_CONFORMANCE_HELLO'
    ?{ok:false,kind:'CLIENT_CONFORMANCE_FOREIGN_PEER_DENIED',nonce:payload.nonce,
      peer_pid:process.pid,peer_birth:123.5}
    :{ok:true,kind:'ARM',canonical_original_input:'controlled-original'};
   const bytes=Buffer.from(JSON.stringify(reply)+'\n');
   socket.emit('data',bytes.subarray(0,7));socket.emit('data',bytes.subarray(7));socket.emit('end');
  });return true;
 };
 socket.end=frame=>{socket.writableEnded=true;return socket.write(frame)};
 sockets.push(socket);queueMicrotask(()=>socket.emit('connect'));return socket;
};
syncBuiltinESMExports();
const helper=await import(process.argv[1]);
const config={model:'anthropic/claude-opus-5-5',small_model:'anthropic/claude-opus-5-5',
 enabled_providers:['anthropic'],provider:{anthropic:{options:{
 apiKey:'autocode-source-derivation-public-placeholder',baseURL:'https://api.anthropic.com/v1'}}},
 agent:{validator:{model:'anthropic/claude-opus-5-5',mode:'primary'}}};
const keys=['HOME','XDG_CONFIG_HOME','XDG_DATA_HOME','XDG_CACHE_HOME','XDG_STATE_HOME',
 'TMPDIR','OPENCODE_TEST_MANAGED_CONFIG_DIR','KILO_CONFIG_DIR'];
const directories=Object.fromEntries(keys.map(key=>[key,'/controlled/'+key]));
Object.assign(process.env,directories);process.env.KILO_CONFIG_CONTENT=JSON.stringify(config);
const options={version:1,mode:'foreign',nonce:'1'.repeat(64),token:'2'.repeat(64),
 socket:'/private/tmp/controlled.sock',executable:process.execPath,
 executable_sha256:hash('controlled-client'),plugin_sha256:hash(helperBytes),
 config_sha256:hash(process.env.KILO_CONFIG_CONTENT),agent:'validator',model:config.model,
 tcp:{host:'192.0.2.1',port:443,timeout_ms:2000},rpc_timeout_seconds:10,
 foreign_timeout_ms:15000,foreign_environment:directories};
process.env.AUTOCODE_KILO_CLIENT_CONFORMANCE=JSON.stringify(options);
const hook=await helper.default.server();await hook.config(config);
const sourceCapsuleAfterConformance=Object.hasOwn(globalThis,'__autocodeKiloSourceRelay_v1');
delete process.env.AUTOCODE_KILO_CLIENT_CONFORMANCE;
const reviewer={provider:'kilocode',model:config.model,agent:'validator'};
const relay={socket:options.socket,token:options.token,rpc_timeout_seconds:10,max_requests:1,
 reviewer,binding_sha256:'b'.repeat(64),attempt_id:'controlled-only',plugin_sha256:hash(helperBytes),
 source_hooks:{path:'/controlled/source-hook.ts',sha256:'c'.repeat(64)}};
process.env.AUTOCODE_KILO_SOURCE_RELAY=JSON.stringify(relay);
process.env.AUTOCODE_IMAGE_AUDIT=JSON.stringify({reviewer,attempt_id:relay.attempt_id,
 binding_sha256:relay.binding_sha256,max_requests:relay.max_requests});
await helper.default.server();
const arm=await globalThis.__autocodeKiloSourceRelay_v1.arm({op:'ARM',canonical_projection:'controlled'});
console.log(JSON.stringify({calls,destroyed:sockets.map(socket=>socket.destroyed),arm,
 sourceCapsuleAfterConformance}));
"""
        node = shutil.which("node")
        self.assertIsNotNone(node)
        result = subprocess.run(
            [node, "--input-type=module", "-e", source, HELPER.as_uri()],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        observed = json.loads(result.stdout)
        self.assertEqual(["CLIENT_CONFORMANCE_HELLO", "ARM"], [row["op"] for row in observed["calls"]])
        for row in observed["calls"]:
            self.assertTrue(row["framed"])
            self.assertEqual("2" * 64, row["token"])
            self.assertEqual(10000, row["bound"])
            self.assertFalse(row["writableEnded"])
        self.assertEqual([True, True], observed["destroyed"])
        self.assertEqual({"kind": "ARM", "canonical_original_input": "controlled-original"}, observed["arm"])
        self.assertFalse(observed["sourceCapsuleAfterConformance"])


class ControlledConformanceDiagnosticTests(unittest.TestCase):
    """Exercise private callback failures with replaced transports, never sockets or clients."""

    def probe(self, scenario):
        source = r"""
import fs from 'node:fs';
import net from 'node:net';
import childProcess from 'node:child_process';
import {EventEmitter} from 'node:events';
import {syncBuiltinESMExports,registerHooks} from 'node:module';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
const scenario=process.argv[2],secret='SENSITIVE:/private/path token=fixture-only';
const hash=x=>createHash('sha256').update(x).digest('hex');
const helperPath=fileURLToPath(process.argv[1]),helperBytes=fs.readFileSync(helperPath);
const error=(code,errno)=>Object.assign(new Error(secret),{code,errno,path:secret});
const calls=[],raw=scenario.startsWith('raw-');
Object.defineProperty(process,'platform',{value:scenario==='raw-platform'?'linux':'darwin'});
Object.defineProperty(process,'arch',{value:'arm64'});
let clock=0;Object.defineProperty(globalThis,'performance',{value:{now:()=>{
 calls.push('clock');return clock++===0?0:['tcp-timeout','raw-timeout'].includes(scenario)?2001:1;
}}});
if(raw){const exit=process.exit;process.exit=code=>{console.log(JSON.stringify(calls));exit(code)}}
const errno=()=>scenario==='tcp-error'?51:scenario==='tcp-unknown'?9999
 :scenario==='raw-refused'?61:scenario==='raw-inprogress'?36
 :scenario.endsWith('-eacces')?13:1;
globalThis.__controlledFFI={
 ptr:value=>value,
 read:{i32:pointer=>{calls.push('read-errno');if(pointer!==4096)throw new Error('Foreign pointer');
  if(scenario==='raw-read-throw')throw error('EIO',-5);return errno()}},
 dlopen:(path,definitions)=>{
  calls.push({library:path,definitions});
  if(scenario==='raw-library-open')throw error('EACCES',-13);
  return {close:()=>{calls.push('close-library');if(scenario==='raw-library-close')throw error('EIO',-5)},
   symbols:{
    socket:(...args)=>{calls.push({socket:args});return ['raw-socket-eperm','raw-socket-eacces'].includes(scenario)?-1:9},
    setsockopt:(fd,level,name,value,length)=>{calls.push({set:[fd,level,name,...value,length]});
     return scenario.startsWith('raw-set-')?-1:0},
    getsockopt:(fd,level,name,value,length)=>{calls.push({get:[fd,level,name,...length]});
     value[0]=scenario==='raw-wrong-value'?2:1;length[0]=scenario==='raw-wrong-length'?8:4;
     return scenario.startsWith('raw-get-')?-1:0},
    __error:()=>{calls.push('errno-pointer');return scenario==='raw-pointer'?0:4096},
    connect:(fd,address,length)=>{calls.push({connect:[fd,...new Uint8Array(address.buffer),length]});
     if(scenario==='tcp-throw')throw error('EINVAL',-22);
     return ['tcp-connect','raw-connected'].includes(scenario)?0:-1},
    close:fd=>{calls.push({close:fd});return scenario==='raw-socket-close'?-1:0},
   }};
 },
};
const ffiURL='data:text/javascript,'+encodeURIComponent('export const dlopen=(...a)=>globalThis.__controlledFFI.dlopen(...a);export const ptr=v=>globalThis.__controlledFFI.ptr(v);export const read={i32:p=>globalThis.__controlledFFI.read.i32(p)};');
registerHooks({resolve:(specifier,context,next)=>specifier==='bun:ffi'
 ?{url:ffiURL,shortCircuit:true}:next(specifier,context)});
// Node's ESM loader reads the helper itself through fs.readFileSync, by URL.
fs.readFileSync=file=>(path=>path===process.execPath?Buffer.from('controlled-client')
 :path===helperPath?helperBytes:(()=>{throw new Error('Unexpected controlled read')})())(
 file instanceof URL?fileURLToPath(file):file);
fs.realpathSync=path=>path;fs.statSync=()=>({isDirectory:()=>true});
globalThis.setTimeout=callback=>{
 const timer={cleared:false};
 if(scenario==='foreign-timeout')queueMicrotask(()=>{if(!timer.cleared)callback()});
 return timer;
};
globalThis.clearTimeout=timer=>{if(timer)timer.cleared=true};
net.createConnection=options=>{
 if(!options.path)throw new Error('Wrapped TCP must not supply raw permission evidence');
 const socket=new EventEmitter();socket.destroy=()=>{};
 socket.setTimeout=(ms,callback)=>{
  if(ms!==(options.path?10000:2000))throw new Error('Changed controlled bound');
  socket.timeout=callback;
 };
 socket.write=frame=>{
  const row=JSON.parse(frame);
  calls.push(row.op);
  if(row.op==='CLIENT_CONFORMANCE_HELLO'&&scenario==='hello-error'){
   queueMicrotask(()=>socket.emit('error',error('ENOTCONN',-57)));return true;
  }
  const reply=row.op==='CLIENT_CONFORMANCE_HELLO'
   ?{ok:true,kind:'CLIENT_CONFORMANCE_PARENT_PONG',nonce:row.nonce,
      peer_pid:process.pid,peer_birth:123.5}
   :{ok:scenario!=='result-refused',kind:'CLIENT_CONFORMANCE_COMPLETE',nonce:row.nonce};
  queueMicrotask(()=>{socket.emit('data',Buffer.from(JSON.stringify(reply)+'\n'));socket.emit('end')});
  return true;
 };
 queueMicrotask(()=>{
  if(options.path)socket.emit('connect');
  else if(scenario==='tcp-timeout')socket.timeout();
  else if(scenario==='tcp-connect')socket.emit('connect');
  else if(scenario==='tcp-error')socket.emit('error',error('ENETUNREACH',-51));
  else if(scenario==='tcp-unknown')socket.emit('error',error(secret,secret));
  else socket.emit('error',error(scenario==='allow-eacces'?'EACCES':'EPERM',-1));
 });
 return socket;
};
childProcess.spawn=(executable,argv,options)=>{
 if(executable!==process.execPath||JSON.stringify(argv)!==JSON.stringify(['debug','agent','validator'])
   ||options.stdio!=='ignore'||JSON.parse(options.env.AUTOCODE_KILO_CLIENT_CONFORMANCE).mode!=='foreign')
  throw new Error('Changed controlled child binding');
 if(scenario==='foreign-throw')throw error('ENOENT',-2);
 const child=new EventEmitter();
 child.kill=signal=>{queueMicrotask(()=>child.emit('close',null,signal));return true};
 if(scenario!=='foreign-timeout')queueMicrotask(()=>{
  if(scenario==='foreign-error')child.emit('error',error('ENOENT',-2));
  else if(scenario==='foreign-close')child.emit('close',78,null);
  else if(scenario==='foreign-signal')child.emit('close',null,'SIGKILL');
  else if(scenario==='foreign-unknown')child.emit('close',9999,secret);
  else child.emit('close',0,null);
 });
 return child;
};
syncBuiltinESMExports();
const config={model:'anthropic/claude-opus-5-5',small_model:'anthropic/claude-opus-5-5',
 enabled_providers:['anthropic'],provider:{anthropic:{options:{
 apiKey:'autocode-source-derivation-public-placeholder',baseURL:'https://api.anthropic.com/v1'}}},
 agent:{validator:{model:'anthropic/claude-opus-5-5',mode:'primary'}}};
const keys=['HOME','XDG_CONFIG_HOME','XDG_DATA_HOME','XDG_CACHE_HOME','XDG_STATE_HOME',
 'TMPDIR','OPENCODE_TEST_MANAGED_CONFIG_DIR','KILO_CONFIG_DIR'];
process.env.TMPDIR='/controlled/tmp';process.env.KILO_CONFIG_CONTENT=JSON.stringify(config);
const options={version:1,mode:'root',nonce:'1'.repeat(64),token:'2'.repeat(64),
 socket:'/private/tmp/controlled.sock',executable:process.execPath,
 executable_sha256:hash('controlled-client'),plugin_sha256:hash(helperBytes),
 config_sha256:hash(process.env.KILO_CONFIG_CONTENT),agent:'validator',model:config.model,
 tcp:{host:'192.0.2.1',port:443,timeout_ms:2000},rpc_timeout_seconds:10,
 foreign_timeout_ms:15000,foreign_environment:Object.fromEntries(keys.map(key=>[key,'/controlled/tmp/foreign/'+key]))};
process.env.AUTOCODE_KILO_CLIENT_CONFORMANCE=JSON.stringify(options);
const helper=await import(process.argv[1]);const hooks=await helper.default.server();
await hooks.config(config);if(raw)console.log(JSON.stringify(calls));console.log('CONTROLLED_CONFORMANCE_COMPLETE');
"""
        node = shutil.which("node")
        self.assertIsNotNone(node)
        return subprocess.run(
            [node, "--input-type=module", "-e", source, HELPER.as_uri(), scenario],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )

    def test_failures_report_only_bounded_phase_event_and_allowlisted_scalars(self):
        expected = {
            "hello-error": {"phase": "HELLO", "event": "error", "code": "ENOTCONN", "errno": -57},
            "tcp-timeout": {"phase": "TCP", "event": "timeout"},
            "tcp-connect": {"phase": "TCP", "event": "connect"},
            "tcp-error": {"phase": "TCP", "event": "error", "errno": -51},
            "tcp-unknown": {"phase": "TCP", "event": "error"},
            "tcp-throw": {"phase": "TCP", "event": "throw", "code": "EINVAL", "errno": -22},
            "foreign-throw": {"phase": "FOREIGN", "event": "throw", "code": "ENOENT", "errno": -2},
            "foreign-error": {"phase": "FOREIGN", "event": "error", "code": "ENOENT", "errno": -2},
            "foreign-close": {"phase": "FOREIGN", "event": "close", "exit_code": 78},
            "foreign-signal": {"phase": "FOREIGN", "event": "close", "signal": "SIGKILL"},
            "foreign-timeout": {"phase": "FOREIGN", "event": "timeout", "signal": "SIGKILL"},
            "foreign-unknown": {"phase": "FOREIGN", "event": "close"},
            "result-refused": {"phase": "RESULT", "event": "check"},
        }
        for scenario, facts in expected.items():
            with self.subTest(scenario=scenario):
                result = self.probe(scenario)
                self.assertEqual(78, result.returncode, result.stderr)
                self.assertEqual("", result.stdout)
                lines = result.stderr.splitlines()
                self.assertEqual(2, len(lines), result.stderr)
                self.assertEqual("Kilo client conformance refused", lines[0])
                self.assertTrue(lines[1].startswith("KILO_CLIENT_CONFORMANCE_FAILURE "))
                diagnostic = json.loads(lines[1].split(" ", 1)[1])
                self.assertEqual({"version": 1, **facts}, diagnostic)
                for forbidden in ["SENSITIVE", "/private/path", "token=", "fixture-only", "1" * 64, "2" * 64]:
                    self.assertNotIn(forbidden, result.stderr)

    def test_only_original_kernel_permission_codes_complete_the_controlled_callback(self):
        for scenario in ["allow-eacces", "allow-eperm"]:
            with self.subTest(scenario=scenario):
                result = self.probe(scenario)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual("CONTROLLED_CONFORMANCE_COMPLETE\n", result.stdout)
                self.assertEqual("", result.stderr)

    def test_raw_fixed_abi_connect_errno_and_cleanup_precede_permission_success(self):
        definitions = {
            "socket": {"args": ["i32", "i32", "i32"], "returns": "i32"},
            "setsockopt": {"args": ["i32", "i32", "i32", "ptr", "u32"], "returns": "i32"},
            "getsockopt": {"args": ["i32", "i32", "i32", "ptr", "ptr"], "returns": "i32"},
            "connect": {"args": ["i32", "ptr", "u32"], "returns": "i32"},
            "close": {"args": ["i32"], "returns": "i32"},
            "__error": {"args": [], "returns": "ptr"},
        }
        for scenario in ["raw-eperm", "raw-eacces"]:
            with self.subTest(scenario=scenario):
                result = self.probe(scenario)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual("", result.stderr)
                trace, complete = result.stdout.splitlines()
                self.assertEqual("CONTROLLED_CONFORMANCE_COMPLETE", complete)
                self.assertEqual(
                    [
                        "CLIENT_CONFORMANCE_HELLO",
                        "clock",
                        {"library": "/usr/lib/libSystem.B.dylib", "definitions": definitions},
                        {"socket": [2, 1, 0]},
                        {"set": [9, 6, 32, 1, 4]},
                        {"get": [9, 6, 32, 4]},
                        "errno-pointer",
                        {"connect": [9, 16, 2, 1, 187, 192, 0, 2, 1, 0, 0, 0, 0, 0, 0, 0, 0, 16]},
                        "read-errno",
                        {"close": 9},
                        "close-library",
                        "clock",
                        "CLIENT_CONFORMANCE_RESULT",
                    ],
                    json.loads(trace),
                )

    def test_raw_setup_result_timeout_and_cleanup_faults_never_become_permission_success(self):
        scenarios = [
            "raw-socket-eperm",
            "raw-socket-eacces",
            "raw-set-eperm",
            "raw-set-eacces",
            "raw-get-eperm",
            "raw-get-eacces",
            "raw-wrong-value",
            "raw-wrong-length",
            "raw-pointer",
            "raw-connected",
            "raw-refused",
            "raw-inprogress",
            "raw-socket-close",
            "raw-library-close",
            "raw-timeout",
            "raw-read-throw",
            "raw-library-open",
            "raw-platform",
        ]
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                result = self.probe(scenario)
                self.assertEqual(78, result.returncode, result.stderr)
                calls = json.loads(result.stdout)
                self.assertNotIn("CLIENT_CONFORMANCE_RESULT", calls)
                self.assertEqual(2, len(result.stderr.splitlines()))
                self.assertEqual("Kilo client conformance refused", result.stderr.splitlines()[0])
                row = json.loads(result.stderr.splitlines()[1].split(" ", 1)[1])
                self.assertEqual("TCP", row["phase"])
                closes = [call for call in calls if isinstance(call, dict) and "close" in call]
                connected = any(isinstance(call, dict) and "connect" in call for call in calls)
                if scenario.startswith(("raw-socket-e", "raw-set-", "raw-get-")) or scenario in [
                    "raw-wrong-value",
                    "raw-wrong-length",
                    "raw-pointer",
                    "raw-library-open",
                    "raw-platform",
                ]:
                    self.assertFalse(connected, calls)
                if scenario.startswith("raw-socket-e") or scenario in ["raw-library-open", "raw-platform"]:
                    self.assertEqual([], closes)
                else:
                    self.assertEqual([{"close": 9}], closes)
                if scenario not in ["raw-library-open", "raw-platform"]:
                    self.assertEqual(1, calls.count("close-library"))
                if scenario == "raw-refused":
                    self.assertEqual(
                        {"version": 1, "phase": "TCP", "event": "error", "code": "ECONNREFUSED", "errno": -61}, row
                    )
                if scenario == "raw-connected":
                    self.assertEqual("connect", row["event"])
                if scenario == "raw-timeout":
                    self.assertEqual("timeout", row["event"])
                    self.assertLess(calls.index("close-library"), len(calls) - 1)
                if scenario in ["raw-socket-close", "raw-library-close"]:
                    self.assertEqual("close", row["event"])

    def test_changing_or_throwing_metadata_is_snapshotted_or_falls_back_before_refusal(self):
        source = r"""
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
const {clientConformanceHooks}=await import(process.argv[1]);
const hash=x=>createHash('sha256').update(x).digest('hex');
const bytes=readFileSync(fileURLToPath(process.argv[1]));
const secret='SENSITIVE:/private/path token=fixture-only';
const config={model:'anthropic/claude-opus-5-5',small_model:'anthropic/claude-opus-5-5',
 enabled_providers:['anthropic'],provider:{anthropic:{options:{
 apiKey:'autocode-source-derivation-public-placeholder',baseURL:'https://api.anthropic.com/v1'}}},
 agent:{validator:{model:'anthropic/claude-opus-5-5',mode:'primary'}}};
const dirs=['HOME','XDG_CONFIG_HOME','XDG_DATA_HOME','XDG_CACHE_HOME','XDG_STATE_HOME',
 'TMPDIR','OPENCODE_TEST_MANAGED_CONFIG_DIR','KILO_CONFIG_DIR'];
const options={version:1,mode:'root',nonce:'1'.repeat(64),token:'2'.repeat(64),
 socket:'/private/tmp/fixture.sock',executable:'/controlled/kilo',executable_sha256:hash('controlled-client'),
 plugin_sha256:hash(bytes),config_sha256:hash(JSON.stringify(config)),agent:'validator',
 model:config.model,tcp:{host:'192.0.2.1',port:443,timeout_ms:2000},rpc_timeout_seconds:10,
 foreign_timeout_ms:15000,foreign_environment:Object.fromEntries(dirs.map(k=>[k,'/controlled/tmp/foreign/'+k]))};
const results=[];
for(const scenario of ['changing','attached','throw-attached','throw-code','throw-errno']){
 const reads={},error=new Error(secret),attached={},diagnostics=[];
 const getter=(target,key,first,throws=false)=>Object.defineProperty(target,key,{get:()=>{
  reads[key]=(reads[key]??0)+1;
  if(throws)throw new Error(secret);
  return reads[key]===1?first:secret;
 }});
 if(scenario==='attached'){
  getter(error,'conformance',attached);
  for(const [key,value] of Object.entries({event:'error',code:'EPERM',errno:-1,exit_code:78,signal:'SIGKILL'}))
   getter(attached,key,value);
 }else{
  getter(error,'conformance',null,scenario==='throw-attached');
  getter(error,'code','ENETUNREACH',scenario==='throw-code');
  getter(error,'errno',-51,scenario==='throw-errno');
 }
 const io={env:{TMPDIR:'/controlled/tmp',KILO_CONFIG_CONTENT:JSON.stringify(config)},pid:321,
  executable:options.executable,read:p=>p===options.executable?Buffer.from('controlled-client'):bytes,
  realpath:p=>p,directory:()=>true,refuse:diagnostic=>diagnostics.push(diagnostic),
  rpc:async()=>({ok:true,kind:'CLIENT_CONFORMANCE_PARENT_PONG',nonce:options.nonce,
    peer_pid:321,peer_birth:123.5}),tcp:async()=>{throw error},foreign:async()=>{throw new Error('unreached')}};
 try{await clientConformanceHooks(options,io).config(structuredClone(config))}catch{}
 results.push({scenario,reads,diagnostics});
}
console.log(JSON.stringify(results));
"""
        node = shutil.which("node")
        self.assertIsNotNone(node)
        result = subprocess.run(
            [node, "--input-type=module", "-e", source, HELPER.as_uri()],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", result.stderr)
        rows = json.loads(result.stdout)
        self.assertEqual(5, len(rows))
        for row in rows:
            self.assertTrue(all(count == 1 for count in row["reads"].values()), row)
            self.assertEqual(1, len(row["diagnostics"]))
            expected = {"version": 1, "phase": "TCP", "event": "check"}
            if row["scenario"] == "changing":
                expected.update(code="ENETUNREACH", errno=-51)
                self.assertEqual({"conformance": 1, "code": 1, "errno": 1}, row["reads"])
            elif row["scenario"] == "attached":
                expected.update(event="error", code="EPERM", errno=-1, exit_code=78, signal="SIGKILL")
                self.assertEqual(
                    {key: 1 for key in ["conformance", "event", "code", "errno", "exit_code", "signal"]}, row["reads"]
                )
            self.assertEqual(expected, row["diagnostics"][0])
        for forbidden in ["SENSITIVE", "/private/path", "token=", "fixture-only"]:
            self.assertNotIn(forbidden, result.stdout)


class ControlledReceiptTests(unittest.TestCase):
    def setUp(self):
        self.binding = {"reviewer": {"provider": "kilocode", "model": "anthropic/" + MODEL}}
        self.images = [{"sha256": sha(png()), "mime": "image/png", "bytes": len(png())}]
        self.hooks = {"path": "/owned/fixture/source-hook.ts", "sha256": "c" * 64}
        self.record = object()  # Controlled pure fixture, never a native receipt.
        self.original_authority_record = self.record
        context = {
            "session_id": "session",
            "user_message_id": "user",
            "agent": "validator",
            "provider": "kilocode",
            "model": "anthropic/" + MODEL,
            "wire_model": MODEL,
            "image_capable": True,
        }
        msg = {
            "session_id": "session",
            "message_id": "assistant",
            "user_message_id": "user",
            "model": context["model"],
            "agent": "validator",
            "finish": None,
        }
        join = {"source_digest": "d" * 64, "owner_digest": "e" * 64, "source_index": 0}
        rows = [
            {
                "type": "audit_start",
                "version": 3,
                "attempt_id": "attempt",
                "binding_sha256": util.digest(self.binding),
                "plugin_sha256": "a" * 64,
                "max_requests": 1,
                "reviewer": self.binding["reviewer"] | {"agent": "validator"},
                "source_hooks": self.hooks,
            },
            {"type": "native_message", **msg},
            {"type": "review_context", "request_id": "r", **context},
            {
                "type": "source_commit",
                "request_id": "r",
                "admission_index": 1,
                **join,
                "source_record_sha256": join["source_digest"],
                "peer_pid": 123,
                "peer_birth": 1.5,
            },
            {
                "type": "request",
                "request_id": "r",
                "admission_index": 1,
                **context,
                **join,
                "body_sha256": "f" * 64,
                "serialized_model": MODEL,
                "reasoning_effort": "high",
                "request_wire_format": "anthropic_messages",
                "payload_complete": True,
                "images": self.images,
            },
            {
                "type": "response",
                "request_id": "r",
                "admission_index": 1,
                "status": 200,
                "body_present": True,
                "cloneable": True,
            },
            {
                "type": "request_complete",
                "request_id": "r",
                "admission_index": 1,
                "response_id": "model-response",
                "response_model": MODEL,
                "complete": True,
                "finish_reason": "stop",
                "tool_call_ids": [],
                "wire_format": "sse",
                "response_bytes": 100,
                "response_body_sha256": "b" * 64,
                "output_text_sha256": sha(b'{"status":"PASS"}'),
            },
            {
                "type": "native_start",
                "session_id": "session",
                "message_id": "assistant",
                "part_id": "start",
                "reason": None,
            },
            {
                "type": "native_finish",
                "session_id": "session",
                "message_id": "assistant",
                "part_id": "finish",
                "reason": "stop",
            },
            {"type": "native_message", **msg, "finish": "stop"},
        ]
        self.rows = [{"sequence": i, "at_ms": 1000 + i, **row} for i, row in enumerate(rows)]
        self.events = self.raw(
            [
                {
                    "type": "step_start",
                    "sessionID": "session",
                    "part": {"id": "start", "sessionID": "session", "messageID": "assistant"},
                },
                {
                    "type": "step_finish",
                    "sessionID": "session",
                    "part": {"id": "finish", "sessionID": "session", "messageID": "assistant", "reason": "stop"},
                },
                {
                    "type": "text",
                    "sessionID": "session",
                    "part": {"id": "text", "messageID": "assistant", "text": '{"status":"PASS"}'},
                },
            ]
        )

    @staticmethod
    def raw(rows):
        return ("".join(json.dumps(row) + "\n" for row in rows)).encode()

    def verify(self, rows=None, *, authority_change=None, report=b'{"status":"PASS"}'):
        raw = self.raw(self.rows if rows is None else rows)

        def controlled_authority(record, **kwargs):
            if record is not self.original_authority_record:
                raise ValueError("A caller dictionary is not source authority")
            authority = {"journal_sha256": sha(raw), "source_hooks": self.hooks, "peer_pid": 123, "peer_birth": 1.5}
            if authority_change:
                authority_change(authority)
            return authority

        module = types.ModuleType("autocode_kilo_source_relay")
        module.require_delivery_authority = controlled_authority
        with patch.dict(sys.modules, {module.__name__: module}):
            return delivery.verify_retained(
                self.record,
                events=self.events,
                report=report,
                images=self.images,
                binding=self.binding,
                audit_path="/owned/fixture/original-audit.jsonl",
                audit_bytes=raw,
                attempt_id="attempt",
                plugin_sha256="a" * 64,
            )

    def test_controlled_joins_retain_original_delivery_receipt_contract(self):
        receipt = self.verify()
        self.assertEqual("final_request_image_delivery", receipt["kind"])
        self.assertEqual("kilocode", receipt["provider"])
        self.assertEqual(self.images, receipt["images"])
        self.assertEqual(sha(self.raw(self.rows)), receipt["evidence_sha256"])

    def test_caller_dictionary_or_changed_parent_journal_hooks_peer_refuse(self):
        original = self.record
        self.record = {"verified": True}
        self.assertRaises(ValueError, self.verify)
        self.record = original
        for change in (
            lambda a: a.update(journal_sha256="0" * 64),
            lambda a: a.update(source_hooks={"path": "foreign", "sha256": "c" * 64}),
            lambda a: a.update(peer_pid=124),
        ):
            with self.subTest(change=change):
                self.assertRaises(ValueError, self.verify, authority_change=change)

    def test_source_ack_order_identity_digest_and_peer_cannot_drift(self):
        variants = []
        for key, value in (("owner_digest", "0" * 64), ("source_index", 1), ("source_digest", "0" * 64)):
            rows = deepcopy(self.rows)
            rows[4][key] = value
            variants.append(rows)
        for key, value in (("source_record_sha256", "0" * 64), ("peer_pid", 124), ("peer_birth", True)):
            rows = deepcopy(self.rows)
            rows[3][key] = value
            variants.append(rows)
        rows = deepcopy(self.rows)
        rows[3], rows[4] = rows[4], rows[3]
        for i, row in enumerate(rows):
            row["sequence"] = i
        variants.append(rows)
        rows = deepcopy(self.rows)
        rows.insert(4, deepcopy(rows[3]))
        for i, row in enumerate(rows):
            row["sequence"] = i
        variants.append(rows)
        for rows in variants:
            with self.subTest(rows=rows[3:5]):
                self.assertRaises(ValueError, self.verify, rows)

    def test_final_image_bytes_high_model_and_native_report_correlate(self):
        for row_index, key, value in (
            (4, "reasoning_effort", "max"),
            (4, "serialized_model", "other"),
            (4, "request_wire_format", "openai"),
            (6, "response_model", "other"),
        ):
            rows = deepcopy(self.rows)
            rows[row_index][key] = value
            self.assertRaises(ValueError, self.verify, rows)
        rows = deepcopy(self.rows)
        rows[4]["images"][0]["bytes"] += 1
        self.assertRaises(ValueError, self.verify, rows)
        self.assertRaises(ValueError, self.verify, report=b'{"status":"FAIL"}')


if __name__ == "__main__":
    unittest.main()
