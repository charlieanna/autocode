// Decode the final Anthropic Messages wire bytes, never a predicted SDK prompt.
// Protocol: https://platform.claude.com/docs/en/build-with-claude/streaming
// These pure decoders confer no source, parent, native or image-review authority.
import { createHash, randomUUID } from 'node:crypto';
import { readFileSync, realpathSync, statSync } from 'node:fs';
import { createConnection } from 'node:net';
import { fileURLToPath } from 'node:url';
import { isAbsolute, sep } from 'node:path';
import { spawn } from 'node:child_process';

export const MODEL = 'claude-opus-5-5';
const sha = value => createHash('sha256').update(value).digest('hex');
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const need = (condition, reason) => { if (!condition) throw new Error('Kilo image delivery: ' + reason); };
const nonempty = value => typeof value === 'string' && value.length > 0;

function utf8(value, limit) {
  need(typeof value === 'string' || value instanceof Uint8Array, 'missing_wire_bytes');
  const raw = typeof value === 'string' ? Buffer.from(value, 'utf8') : Buffer.from(value);
  need(raw.length > 0 && raw.length <= limit, 'wire_size_limit');
  return { raw, text: new TextDecoder('utf-8', { fatal: true }).decode(raw) };
}

const crcTable = Array.from({ length: 256 }, (_, value) => {
  for (let i = 0; i < 8; i++) value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
  return value >>> 0;
});
function crc32(bytes) {
  let crc = 0xffffffff;
  for (const value of bytes) crc = crcTable[(crc ^ value) & 255] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}
function png(data) {
  need(data.length <= 8 * 1024 * 1024 && data.subarray(0, 8).equals(Buffer.from('89504e470d0a1a0a', 'hex')), 'invalid_png');
  let at = 8, header = false, pixels = false, end = false;
  while (at < data.length) {
    need(at + 12 <= data.length && !end, 'truncated_png');
    const length = data.readUInt32BE(at), next = at + 12 + length;
    need(next <= data.length, 'truncated_png');
    const kind = data.toString('ascii', at + 4, at + 8);
    need(crc32(data.subarray(at + 4, at + 8 + length)) === data.readUInt32BE(at + 8 + length), 'png_crc');
    if (!header) {
      need(kind === 'IHDR' && length === 13, 'png_header');
      const width = data.readUInt32BE(at + 8), height = data.readUInt32BE(at + 12);
      need(width > 0 && height > 0 && width <= 1536 && height <= 1536 && width * height <= 2359296, 'png_dimensions');
      header = true;
    } else need(kind !== 'IHDR', 'duplicate_png_header');
    if (kind === 'IDAT') pixels = true;
    if (kind === 'IEND') { need(length === 0 && pixels && next === data.length, 'png_end'); end = true; }
    at = next;
  }
  need(header && pixels && end, 'incomplete_png');
}

export function decodeRequest(value, { requireImages = true } = {}) {
  need(typeof requireImages === 'boolean', 'invalid_image_requirement');
  const { raw, text } = utf8(value, 32 * 1024 * 1024);
  const body = JSON.parse(text), images = [];
  need(object(body) && body.model === MODEL && body.stream === true, 'wrong_messages_model_or_stream');
  need(Number.isSafeInteger(body.max_tokens) && body.max_tokens > 0, 'missing_token_bound');
  need(body.output_config?.effort === 'high' && body.thinking?.type === 'adaptive', 'wrong_reasoning_effort');
  need(Array.isArray(body.messages) && body.messages.length > 0, 'missing_messages');
  function content(blocks, toolResult = false) {
    if (typeof blocks === 'string') return;
    need(Array.isArray(blocks), 'unsupported_content');
    for (const block of blocks) {
      need(object(block), 'unsupported_content');
      if (block.type === 'text') need(typeof block.text === 'string', 'invalid_text');
      else if (block.type === 'image') {
        const source = block.source;
        need(object(source) && source.type === 'base64' && source.media_type === 'image/png'
          && typeof source.data === 'string' && /^[A-Za-z0-9+/]+={0,2}$/.test(source.data), 'unsupported_image_source');
        const bytes = Buffer.from(source.data, 'base64');
        need(bytes.toString('base64') === source.data, 'noncanonical_image_base64');
        png(bytes);
        images.push({ sha256: sha(bytes), mime: 'image/png', bytes: bytes.length });
      } else if (block.type === 'tool_result' && !toolResult) {
        need(nonempty(block.tool_use_id), 'invalid_tool_result');
        content(block.content, true);
      } else if (block.type === 'tool_use' && !toolResult) {
        need(nonempty(block.id) && nonempty(block.name) && object(block.input), 'invalid_tool_input');
      } else if (block.type === 'thinking' && !toolResult) {
        need(typeof block.thinking === 'string' && nonempty(block.signature), 'invalid_thinking');
      } else if (block.type === 'redacted_thinking' && !toolResult) need(nonempty(block.data), 'invalid_redacted_thinking');
      else need(false, 'unsupported_content_block');
    }
  }
  for (const message of body.messages) {
    need(object(message) && ['user', 'assistant'].includes(message.role), 'unsupported_message_role');
    content(message.content);
  }
  need(!requireImages || images.length > 0, 'missing_final_images');
  return { body_sha256: sha(raw), serialized_model: body.model, reasoning_effort: 'high',
    payload_complete: true, images, request_wire_format: 'anthropic_messages' };
}

function terminal(message) {
  need(object(message) && message.type === 'message' && message.role === 'assistant'
    && nonempty(message.id) && message.model === MODEL && message.error == null
    && message.stop_sequence == null && Array.isArray(message.content), 'invalid_messages_response');
  const calls = [], texts = [];
  for (const block of message.content) {
    need(object(block), 'invalid_output_block');
    if (block.type === 'text') { need(typeof block.text === 'string', 'invalid_output_text'); texts.push(block.text); }
    else if (block.type === 'tool_use') {
      need(nonempty(block.id) && nonempty(block.name) && object(block.input), 'invalid_tool_call'); calls.push(block.id);
    } else if (block.type === 'thinking') need(typeof block.thinking === 'string' && nonempty(block.signature), 'invalid_thinking');
    else if (block.type === 'redacted_thinking') need(nonempty(block.data), 'invalid_redacted_thinking');
    else need(false, 'unsupported_output_block');
  }
  need(new Set(calls).size === calls.length, 'duplicate_tool_call');
  need((message.stop_reason === 'tool_use' && calls.length > 0)
    || (message.stop_reason === 'end_turn' && calls.length === 0 && texts.join('').length > 0), 'unsuccessful_stop_reason');
  return { response_id: message.id, response_model: message.model,
    finish_reason: calls.length ? 'tool_calls' : 'stop', tool_call_ids: calls,
    output_text_sha256: sha(texts.join('')) };
}

export function decodeResponse(value) {
  const { raw, text } = utf8(value, 16 * 1024 * 1024);
  let result;
  if (text.trimStart().startsWith('{')) result = { ...terminal(JSON.parse(text)), wire_format: 'json' };
  else {
    const normalized = text.replace(/\r\n/g, '\n');
    need(normalized.endsWith('\n\n'), 'truncated_sse');
    let message = null, delta = false, stopped = false;
    const blocks = new Map();
    for (const frame of normalized.split('\n\n')) {
      if (!frame.trim()) continue;
      const data = []; let event = null;
      for (const line of frame.split('\n')) {
        if (line.startsWith('event:')) { need(event === null, 'duplicate_sse_event'); event = line.slice(6).trim(); }
        else if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
        else need(line.startsWith(':') || line.startsWith('id:') || line.startsWith('retry:'), 'invalid_sse');
      }
      if (!data.length) continue;
      need(!stopped, 'data_after_message_stop');
      const row = JSON.parse(data.join('\n'));
      need(object(row) && event === row.type && row.type !== 'error' && row.error == null, 'foreign_or_error_sse');
      if (row.type === 'ping') continue;
      if (row.type === 'message_start') {
        need(message === null && object(row.message) && row.message.type === 'message'
          && row.message.role === 'assistant' && nonempty(row.message.id) && row.message.model === MODEL
          && Array.isArray(row.message.content) && row.message.content.length === 0
          && row.message.stop_reason == null && row.message.stop_sequence == null, 'invalid_message_start');
        message = { ...row.message, content: [] };
      } else {
        need(message !== null, 'event_before_message_start');
        if (row.type === 'content_block_start') {
          need(!delta && Number.isSafeInteger(row.index) && row.index === blocks.size
            && object(row.content_block), 'invalid_block_start');
          const block = { ...row.content_block }, kind = block.type;
          need(['text', 'tool_use', 'thinking', 'redacted_thinking'].includes(kind), 'unsupported_output_block');
          if (kind === 'text') need(typeof block.text === 'string', 'invalid_output_text');
          if (kind === 'tool_use') need(nonempty(block.id) && nonempty(block.name) && object(block.input), 'invalid_tool_call');
          if (kind === 'thinking') need(typeof block.thinking === 'string', 'invalid_thinking');
          if (kind === 'redacted_thinking') need(nonempty(block.data), 'invalid_redacted_thinking');
          blocks.set(row.index, { block, closed: false, partial: '' });
        } else if (row.type === 'content_block_delta') {
          const entry = blocks.get(row.index), change = row.delta;
          need(!delta && entry && !entry.closed && object(change), 'invalid_block_delta');
          if (entry.block.type === 'text' && change.type === 'text_delta') {
            need(typeof change.text === 'string', 'invalid_text_delta'); entry.block.text += change.text;
          } else if (entry.block.type === 'tool_use' && change.type === 'input_json_delta') {
            need(typeof change.partial_json === 'string' && Object.keys(entry.block.input).length === 0, 'invalid_tool_delta');
            entry.partial += change.partial_json;
          } else if (entry.block.type === 'thinking' && change.type === 'thinking_delta') {
            need(typeof change.thinking === 'string', 'invalid_thinking_delta'); entry.block.thinking += change.thinking;
          } else if (entry.block.type === 'thinking' && change.type === 'signature_delta') {
            need(nonempty(change.signature) && (entry.block.signature === undefined || typeof entry.block.signature === 'string'), 'invalid_signature_delta');
            entry.block.signature = (entry.block.signature ?? '') + change.signature;
          } else need(false, 'foreign_block_delta');
        } else if (row.type === 'content_block_stop') {
          const entry = blocks.get(row.index);
          need(!delta && entry && !entry.closed, 'invalid_block_stop');
          if (entry.block.type === 'tool_use' && entry.partial) {
            entry.block.input = JSON.parse(entry.partial); need(object(entry.block.input), 'invalid_tool_arguments');
          }
          entry.closed = true;
        } else if (row.type === 'message_delta') {
          need(!delta && [...blocks.values()].every(entry => entry.closed) && object(row.delta)
            && ['end_turn', 'tool_use'].includes(row.delta.stop_reason) && row.delta.stop_sequence == null, 'invalid_message_delta');
          message.stop_reason = row.delta.stop_reason; message.stop_sequence = null; delta = true;
        } else if (row.type === 'message_stop') {
          need(delta && [...blocks.values()].every(entry => entry.closed), 'incomplete_message_stop');
          message.content = [...blocks.values()].map(entry => entry.block);
          result = { ...terminal(message), wire_format: 'sse' }; stopped = true;
        } else need(false, 'unsupported_sse_event');
      }
    }
    need(stopped && result, 'missing_message_stop');
  }
  return { ...result, response_body_sha256: sha(raw), response_bytes: raw.length, complete: true };
}

const nonceHeader = 'x-autocode-image-request';
const publicApiKey = 'autocode-source-derivation-public-placeholder';
const hex = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const fields = (value, allowed) => object(value) && Object.keys(value).every(key => allowed.includes(key));

function channel(options) {
  // Parent rechecks the original absolute deadline, supervised peer and source
  // pins. This capability only locates its Unix socket; it grants no authority.
  return (payload, signal) => new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(new Error('Kilo source relay aborted')); return; }
    let bytes = 0, closed = false; const chunks = [];
    const frame = Buffer.from(JSON.stringify({ ...payload, token: options.token }) + '\n');
    if (frame.length > 32 * 1024 * 1024) { reject(new Error('Kilo source frame exceeds bound')); return; }
    const socket = createConnection({ path: options.socket });
    const finish = (error, value) => {
      if (closed) return; closed = true;
      signal?.removeEventListener('abort', abort);
      socket.destroy(); error ? reject(error) : resolve(value);
    };
    const abort = () => finish(new Error('Kilo source relay aborted'));
    signal?.addEventListener('abort', abort, { once: true });
    socket.setTimeout(options.rpc_timeout_seconds * 1000, () => finish(new Error('Kilo source relay deadline')));
    socket.on('connect', () => socket.write(frame));
    socket.on('error', () => finish(new Error('Kilo source relay unavailable')));
    socket.on('data', data => {
      bytes += data.length;
      if (bytes > 32 * 1024 * 1024) { finish(new Error('Kilo source reply exceeds bound')); return; }
      chunks.push(data);
    });
    socket.on('end', () => {
      try {
        const raw = Buffer.concat(chunks), text = new TextDecoder('utf-8', { fatal: true }).decode(raw);
        need(text.endsWith('\n') && text.split('\n').length === 2, 'ambiguous_source_reply');
        const value = JSON.parse(text); need(object(value) && value.ok === true, 'original_source_refused');
        finish(null, value);
      } catch { finish(new Error('Kilo source relay refused')); }
    });
  });
}

const conformanceErrorCodes = new Set(['EACCES','EPERM','ETIMEDOUT','ENETUNREACH','EHOSTUNREACH',
  'ECONNREFUSED','ECONNRESET','EPIPE','ENOENT','EIO','EINVAL','EAGAIN','ENOMEM','ENOSYS',
  'EADDRNOTAVAIL','EADDRINUSE','ENOTCONN']);
const conformanceSignals = new Set(['SIGTERM','SIGKILL','SIGABRT','SIGSEGV','SIGBUS','SIGILL',
  'SIGINT','SIGHUP','SIGPIPE']);
function conformanceFacts(event, error = null, exitCode = null, signal = null) {
  let code, errno;
  try { code = error?.code; errno = error?.errno; }
  catch { return { event: 'check' }; }
  const facts = { event: ['check','connect','timeout','error','throw','close'].includes(event) ? event : 'check' };
  if (conformanceErrorCodes.has(code)) facts.code = code;
  if (Number.isSafeInteger(errno) && Math.abs(errno) <= 4095) facts.errno = errno;
  if (Number.isSafeInteger(exitCode) && exitCode >= 0 && exitCode <= 255) facts.exit_code = exitCode;
  if (conformanceSignals.has(signal)) facts.signal = signal;
  return facts;
}
function conformanceFailure(event, error = null, exitCode = null, signal = null) {
  const failure = new Error('Kilo client conformance refused');
  failure.conformance = conformanceFacts(event, error, exitCode, signal);
  return failure;
}
function conformanceDiagnostic(phase, error) {
  const fallback = { version: 1, phase: ['HELLO','TCP','FOREIGN','RESULT'].includes(phase) ? phase : 'HELLO',
    event: 'check' };
  try {
    const attached = error?.conformance, facts = object(attached) ? attached : error;
    const event = facts?.event, exitCode = facts?.exit_code, signal = facts?.signal;
    return { ...fallback, ...conformanceFacts(event, facts, exitCode, signal) };
  } catch { return fallback; }
}

function conformanceRpc(options, payload) {
  return new Promise((resolve, reject) => {
    let complete = false, bytes = 0; const chunks = [];
    let socket;
    try { socket = createConnection({ path: options.socket }); }
    catch (error) { reject(conformanceFailure('throw', error)); return; }
    const finish = (error, value) => {
      if (complete) return; complete = true; socket.destroy();
      error ? reject(error) : resolve(value);
    };
    socket.setTimeout(options.rpc_timeout_seconds * 1000, () => finish(conformanceFailure('timeout')));
    socket.on('connect', () => socket.write(JSON.stringify({ ...payload, token: options.token }) + '\n'));
    socket.on('error', error => finish(conformanceFailure('error', error)));
    socket.on('data', data => {
      bytes += data.length;
      if (bytes > 4096) { finish(conformanceFailure('check')); return; }
      chunks.push(data);
    });
    socket.on('end', () => {
      try {
        const text = new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks));
        need(text.endsWith('\n') && text.split('\n').length === 2, 'ambiguous_client_conformance_reply');
        const reply = JSON.parse(text); need(object(reply), 'invalid_client_conformance_reply'); finish(null, reply);
      } catch (error) { finish(conformanceFailure('check', error)); }
    });
  });
}

async function directTcpDenial(target) {
  const started = performance.now();
  let library = null, fd = null, denied = null, failure = null;
  try {
    need(process.platform === 'darwin' && process.arch === 'arm64', 'unsupported_raw_tcp_platform');
    const { dlopen, ptr, read } = await import('bun:ffi');
    library = dlopen('/usr/lib/libSystem.B.dylib', {
      socket: { args: ['i32','i32','i32'], returns: 'i32' },
      setsockopt: { args: ['i32','i32','i32','ptr','u32'], returns: 'i32' },
      getsockopt: { args: ['i32','i32','i32','ptr','ptr'], returns: 'i32' },
      connect: { args: ['i32','ptr','u32'], returns: 'i32' },
      close: { args: ['i32'], returns: 'i32' },
      __error: { args: [], returns: 'ptr' },
    });
    const api = library.symbols;
    const address = new Uint32Array(4), bytes = new Uint8Array(address.buffer);
    bytes.set([16,2,1,187,192,0,2,1]); // Darwin sockaddr_in: fixed 192.0.2.1:443.
    const requested = new Int32Array([1]), observed = new Int32Array([0]), length = new Uint32Array([4]);
    const addressPointer = ptr(address), requestedPointer = ptr(requested);
    const observedPointer = ptr(observed), lengthPointer = ptr(length);
    fd = api.socket(2, 1, 0);
    need(Number.isInteger(fd) && fd >= 0, 'raw_tcp_socket_failed');
    need(api.setsockopt(fd, 6, 0x20, requestedPointer, 4) === 0, 'raw_tcp_timeout_setup_failed');
    need(api.getsockopt(fd, 6, 0x20, observedPointer, lengthPointer) === 0
      && length[0] === 4 && observed[0] === 1, 'raw_tcp_timeout_not_confirmed');
    const errnoPointer = api.__error();
    need(Number.isSafeInteger(errnoPointer) && errnoPointer > 0 && errnoPointer % 4 === 0,
      'raw_tcp_errno_pointer_invalid');
    const result = api.connect(fd, addressPointer, 16);
    const errno = read.i32(errnoPointer); // Immediately, before cleanup, clocks or another FFI call.
    if (result === -1 && (errno === 1 || errno === 13))
      denied = { ...target, status: 'denied', code: errno === 1 ? 'EPERM' : 'EACCES' };
    else if (result === 0) failure = conformanceFailure('connect');
    else failure = conformanceFailure('error', {
      code: errno === 61 ? 'ECONNREFUSED' : undefined,
      errno: Number.isSafeInteger(errno) && errno > 0 && errno <= 4095 ? -errno : undefined,
    });
  } catch (error) { failure = conformanceFailure('throw', error); }
  finally {
    if (Number.isInteger(fd) && fd >= 0) {
      try { if (library.symbols.close(fd) !== 0) failure = conformanceFailure('close'); }
      catch (error) { failure = conformanceFailure('close', error); }
    }
    if (library !== null) {
      try { library.close(); }
      catch (error) { failure = conformanceFailure('close', error); }
    }
  }
  if (failure !== null) throw failure;
  if (performance.now() - started > target.timeout_ms) throw conformanceFailure('timeout');
  need(denied !== null, 'raw_tcp_permission_denial_missing');
  return denied;
}

function sameClientForeignProbe(options, environment) {
  return new Promise((resolve, reject) => {
    let child, expired = false, timer;
    try {
      child = spawn(options.executable, ['debug', 'agent', options.agent], {
        env: { ...environment, ...options.foreign_environment,
          AUTOCODE_KILO_CLIENT_CONFORMANCE: JSON.stringify({ ...options, mode: 'foreign' }) },
        stdio: 'ignore',
      });
      timer = setTimeout(() => { expired = true; child.kill('SIGKILL'); }, options.foreign_timeout_ms);
      child.once('error', error => { clearTimeout(timer); reject(conformanceFailure('error', error)); });
      child.once('close', (code, signal) => {
        clearTimeout(timer);
        if (!expired && code === 0 && signal === null) resolve(0);
        else reject(conformanceFailure(expired ? 'timeout' : 'close', null, code, signal));
      });
    } catch (error) { clearTimeout(timer); reject(conformanceFailure('throw', error)); }
  });
}

// This callback supplies observations to the original parent. It creates no
// source capsule, image audit or inference authority; only the parent can
// accept actual peer/kernel facts and close its original supervised tree.
export function clientConformanceHooks(options, controlled = null) {
  const io = controlled ?? { env: process.env, pid: process.pid, executable: process.execPath,
    read: readFileSync, realpath: realpathSync, directory: path => statSync(path).isDirectory(),
    rpc: conformanceRpc, tcp: directTcpDenial, foreign: sameClientForeignProbe,
    refuse: diagnostic => {
      process.stderr.write('Kilo client conformance refused\n');
      process.stderr.write('KILO_CLIENT_CONFORMANCE_FAILURE ' + JSON.stringify(diagnostic) + '\n');
      process.exit(78);
    } };
  const keys = ['version','mode','nonce','socket','token','executable','executable_sha256','plugin_sha256',
    'config_sha256','agent','model','tcp','rpc_timeout_seconds','foreign_timeout_ms','foreign_environment'];
  need(fields(options, keys) && Object.keys(options).length === keys.length && options.version === 1
    && ['root','foreign'].includes(options.mode) && hex(options.nonce) && hex(options.token)
    && isAbsolute(options.socket) && Buffer.byteLength(options.socket) < 104
    && isAbsolute(options.executable) && hex(options.executable_sha256) && hex(options.plugin_sha256)
    && hex(options.config_sha256) && nonempty(options.agent) && options.model === 'anthropic/' + MODEL
    && options.rpc_timeout_seconds === 10 && options.foreign_timeout_ms === 15000,
    'invalid_client_conformance_binding');
  need(fields(options.tcp, ['host','port','timeout_ms']) && Object.keys(options.tcp).length === 3
    && options.tcp.host === '192.0.2.1' && options.tcp.port === 443 && options.tcp.timeout_ms === 2000,
    'invalid_client_conformance_tcp');
  const directories = ['HOME','XDG_CONFIG_HOME','XDG_DATA_HOME','XDG_CACHE_HOME','XDG_STATE_HOME',
    'TMPDIR','OPENCODE_TEST_MANAGED_CONFIG_DIR','KILO_CONFIG_DIR'];
  need(fields(options.foreign_environment, directories)
    && Object.keys(options.foreign_environment).length === directories.length,
    'invalid_client_conformance_foreign_environment');
  for (const key of directories) {
    const path = options.foreign_environment[key];
    need(typeof path === 'string' && isAbsolute(path) && io.realpath(path) === path && io.directory(path)
      && (options.mode === 'foreign' ? io.env[key] === path : path.startsWith(io.env.TMPDIR + sep)),
      'foreign_client_conformance_directory');
  }
  need(io.executable === options.executable && io.realpath(options.executable) === options.executable
    && sha(io.read(options.executable)) === options.executable_sha256
    && sha(io.read(fileURLToPath(import.meta.url))) === options.plugin_sha256
    && typeof io.env.KILO_CONFIG_CONTENT === 'string' && sha(io.env.KILO_CONFIG_CONTENT) === options.config_sha256,
    'changed_client_conformance_inputs');
  let invoked = false;
  return { config: async config => {
    let phase = 'HELLO';
    try {
      need(!invoked, 'replayed_client_conformance_config'); invoked = true;
      need(object(config) && config.model === options.model && config.small_model === options.model
        && Array.isArray(config.enabled_providers) && config.enabled_providers.length === 1
        && config.enabled_providers[0] === 'anthropic' && object(config.provider)
        && Object.keys(config.provider).length === 1 && object(config.provider.anthropic)
        && (config.provider.anthropic.npm === undefined || config.provider.anthropic.npm === '@ai-sdk/anthropic')
        && config.provider.anthropic.options?.apiKey === publicApiKey
        && config.provider.anthropic.options?.baseURL === 'https://api.anthropic.com/v1'
        && config.agent?.[options.agent]?.model === options.model
        && config.agent[options.agent].mode === 'primary', 'foreign_client_conformance_config');
      const noInference = async () => { throw new Error('Kilo client conformance never performs inference'); };
      config.provider.anthropic.options.fetch = noInference;
      if (controlled === null) globalThis.fetch = noInference;
      const reply = await io.rpc(options, { op: 'CLIENT_CONFORMANCE_HELLO', nonce: options.nonce, mode: options.mode });
      need(reply.nonce === options.nonce && reply.peer_pid === io.pid
        && Number.isFinite(reply.peer_birth) && reply.peer_birth > 0,
        'foreign_client_conformance_parent');
      if (options.mode === 'foreign') {
        need(reply.ok === false && reply.kind === 'CLIENT_CONFORMANCE_FOREIGN_PEER_DENIED',
          'foreign_client_peer_was_not_refused');
        return;
      }
      need(reply.ok === true && reply.kind === 'CLIENT_CONFORMANCE_PARENT_PONG', 'client_unix_probe_not_accepted');
      phase = 'TCP';
      const tcp = await io.tcp(options.tcp);
      need(tcp.host === options.tcp.host && tcp.port === options.tcp.port && tcp.status === 'denied'
        && ['EACCES','EPERM'].includes(tcp.code), 'client_tcp_probe_not_denied');
      phase = 'FOREIGN';
      const foreignExit = await io.foreign(options, io.env);
      need(foreignExit === 0, 'client_foreign_probe_failed');
      phase = 'RESULT';
      const ack = await io.rpc(options, { op: 'CLIENT_CONFORMANCE_RESULT', nonce: options.nonce,
        tcp: { host: tcp.host, port: tcp.port, status: tcp.status, code: tcp.code }, foreign_exit: foreignExit });
      need(ack.ok === true && ack.kind === 'CLIENT_CONFORMANCE_COMPLETE' && ack.nonce === options.nonce,
        'client_conformance_parent_did_not_close');
    } catch (error) { io.refuse(conformanceDiagnostic(phase, error)); throw error; }
  } };
}

async function KiloImageDelivery() {
  if (nonempty(process.env.AUTOCODE_KILO_CLIENT_CONFORMANCE))
    return clientConformanceHooks(JSON.parse(process.env.AUTOCODE_KILO_CLIENT_CONFORMANCE));
  need(nonempty(process.env.AUTOCODE_IMAGE_AUDIT), 'original_source_audit_missing');
  const options = JSON.parse(process.env.AUTOCODE_KILO_SOURCE_RELAY ?? 'null');
  const audit = JSON.parse(process.env.AUTOCODE_IMAGE_AUDIT);
  need(fields(options, ['socket','token','rpc_timeout_seconds','max_requests','reviewer','binding_sha256',
    'attempt_id','plugin_sha256','source_hooks']) && nonempty(options.socket) && options.socket.startsWith('/')
    && Buffer.byteLength(options.socket) < 104 && hex(options.token)
    && Number.isFinite(options.rpc_timeout_seconds) && options.rpc_timeout_seconds > 0 && options.rpc_timeout_seconds <= 600
    && Number.isSafeInteger(options.max_requests) && options.max_requests > 0 && options.max_requests <= 8,
    'invalid_original_source_channel');
  const reviewer = options.reviewer;
  need(fields(reviewer, ['provider','model','agent']) && reviewer.provider === 'kilocode'
    && reviewer.model === 'anthropic/claude-opus-5-5' && nonempty(reviewer.agent)
    && fields(options.source_hooks, ['path','sha256']) && nonempty(options.source_hooks.path)
    && hex(options.source_hooks.sha256) && hex(options.binding_sha256) && nonempty(options.attempt_id),
    'invalid_original_source_binding');
  need(options.plugin_sha256 === sha(readFileSync(fileURLToPath(import.meta.url)))
    && audit.attempt_id === options.attempt_id && audit.binding_sha256 === options.binding_sha256
    && audit.max_requests === options.max_requests && fields(audit.reviewer, ['provider','model','agent'])
    && ['provider','model','agent'].every(key => audit.reviewer[key] === reviewer[key]),
    'foreign_plugin_or_audit_binding');
  need(!Object.getOwnPropertyDescriptor(globalThis, '__autocodeKiloSourceRelay_v1'), 'source_capsule_already_installed');
  const rpc = channel(options), contexts = new Map(), pending = new Map();
  let admitted = 0, identity = null;
  const event = row => rpc({ op: 'NATIVE_EVENT', row });
  const capsule = Object.freeze({
    arm: async payload => {
      need(fields(payload, ['op','canonical_projection']) && payload.op === 'ARM'
        && typeof payload.canonical_projection === 'string', 'invalid_original_arm');
      const reply = await rpc(payload);
      need(reply.kind === 'ARM' && typeof reply.canonical_original_input === 'string', 'invalid_original_arm_reply');
      return { kind: 'ARM', canonical_original_input: reply.canonical_original_input };
    },
    source: async payload => {
      need(fields(payload, ['op','source_digest','canonical_record']) && payload.op === 'SOURCE_COMMIT'
        && hex(payload.source_digest) && typeof payload.canonical_record === 'string', 'invalid_original_source');
      const reply = await rpc(payload);
      need(reply.kind === 'SOURCE_COMMIT' && reply.digest === payload.source_digest && hex(reply.ownerDigest)
        && Number.isSafeInteger(reply.index) && reply.index >= 0 && contexts.has(reply.request_id)
        && !pending.has(reply.request_id), 'invalid_original_source_ack');
      pending.set(reply.request_id, { source_digest: reply.digest, owner_digest: reply.ownerDigest, source_index: reply.index });
      return { kind: 'SOURCE_COMMIT', digest: reply.digest, ownerDigest: reply.ownerDigest, index: reply.index };
    },
  });
  Object.defineProperty(globalThis, '__autocodeKiloSourceRelay_v1', { value: capsule, configurable: false, writable: false });
  // Installed before SDK construction. There is no original Internet fetch
  // fallback, and no child credential; only the exact parent Messages route.
  const auditedFetch = async (input, init) => {
    const request = input instanceof Request ? input : null;
    const url = new URL(request ? request.url : input);
    need(url.href === 'https://api.anthropic.com/v1/messages'
      && (init?.method ?? request?.method ?? 'GET').toUpperCase() === 'POST', 'unqualified_fetch_route');
    const headers = new Headers(init?.headers ?? request?.headers);
    need(headers.get('x-api-key') === publicApiKey
      && !['authorization','proxy-authorization','cookie','set-cookie'].some(key => headers.has(key)), 'private_child_headers');
    const id = headers.get(nonceHeader), source = pending.get(id);
    need(contexts.has(id) && source && admitted < options.max_requests, 'request_without_original_source');
    const admission = ++admitted; pending.delete(id);
    const signal = init?.signal ?? request?.signal;
    const body = typeof init?.body === 'string' ? Buffer.from(init.body) : init?.body instanceof Uint8Array
      ? Buffer.from(init.body) : request && init?.body === undefined ? Buffer.from(await request.clone().arrayBuffer()) : null;
    need(body && body.length <= 32 * 1024 * 1024, 'unsupported_final_request_body');
    decodeRequest(body, { requireImages: false });
    const reply = await rpc({ op: 'REQUEST', path: '/v1/messages', request_id: id, admission_index: admission,
      ...source, headers: Object.fromEntries(headers), body_base64: body.toString('base64'),
      body_sha256: sha(body), body_bytes: body.length }, signal);
    need(reply.kind === 'RESPONSE' && Number.isSafeInteger(reply.status) && reply.status >= 200 && reply.status <= 599
      && object(reply.headers) && typeof reply.body_base64 === 'string' && hex(reply.body_sha256)
      && Number.isSafeInteger(reply.body_bytes) && reply.body_bytes >= 0 && reply.body_bytes <= 16 * 1024 * 1024,
      'invalid_original_response');
    const bytes = Buffer.from(reply.body_base64, 'base64');
    need(bytes.toString('base64') === reply.body_base64 && bytes.length === reply.body_bytes
      && sha(bytes) === reply.body_sha256 && Object.values(reply.headers).every(nonempty), 'changed_original_response');
    return new Response(bytes, { status: reply.status, headers: reply.headers });
  };
  globalThis.fetch = auditedFetch;
  return {
    config: async config => {
      try {
        need(object(config) && Array.isArray(config.enabled_providers)
          && config.enabled_providers.length === 1 && config.enabled_providers[0] === 'anthropic'
          && object(config.provider) && Object.keys(config.provider).every(key => key === 'anthropic')
          && object(config.provider.anthropic), 'foreign_native_provider_config');
        const provider = config.provider.anthropic, options = provider.options;
        need((provider.npm === undefined || provider.npm === '@ai-sdk/anthropic')
          && (config.model === undefined || config.model === 'anthropic/' + MODEL)
          && object(options) && options.apiKey === publicApiKey
          && options.baseURL === 'https://api.anthropic.com/v1'
          && (options.fetch === undefined || options.fetch === auditedFetch), 'foreign_native_sdk_origin');
        // The supported config hook runs before SDK construction. Both lookup
        // paths use the same parent-only fetch, including a factory caching it.
        options.fetch = auditedFetch;
      } catch {
        // The SDK loader logs and ignores hook exceptions. Stop this owned
        // client before it can construct a fallback SDK with another fetch.
        process.stderr.write('Kilo source relay: protected provider config refused\n');
        process.exit(78);
      }
    },
    'chat.headers': async (input, output) => {
      need(input.model?.providerID === 'anthropic' && input.model.id === MODEL
        && input.model.capabilities?.input?.image === true && input.agent === reviewer.agent,
        'foreign_native_review_route');
      const context = { session_id: input.sessionID, user_message_id: input.message.id, agent: input.agent,
        provider: 'kilocode', model: reviewer.model, wire_model: MODEL, image_capable: true };
      need(nonempty(context.session_id) && nonempty(context.user_message_id), 'missing_native_review_identity');
      need(!identity || (identity.session_id === context.session_id && identity.user_message_id === context.user_message_id),
        'foreign_native_review_identity');
      identity ??= context;
      const id = randomUUID(); contexts.set(id, context); output.headers[nonceHeader] = id;
      await event({ type: 'review_context', request_id: id, ...context });
    },
    event: async ({ event: native }) => {
      if (native.type === 'message.updated') {
        const info = native.properties.info; if (info.role !== 'assistant') return;
        await event({ type: 'native_message', session_id: info.sessionID, message_id: info.id,
          user_message_id: info.parentID, model: `${info.providerID}/${info.modelID}`, agent: info.agent,
          finish: info.finish ?? null });
      } else if (native.type === 'message.part.updated') {
        const part = native.properties.part; if (!['step-start','step-finish'].includes(part.type)) return;
        await event({ type: part.type === 'step-start' ? 'native_start' : 'native_finish',
          session_id: part.sessionID, message_id: part.messageID, part_id: part.id, reason: part.reason ?? null });
      }
    },
  };
}

export default { id: 'autocode-kilo-image-delivery', server: KiloImageDelivery };

// Parent parsing of retained exact bytes. This pure command never arms the
// plugin, imports an SDK, contacts the network or grants native authority.
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  need(['request','source-request','response'].includes(process.argv[2]) && process.argv.length === 3, 'invalid_decode_command');
  const chunks = []; let length = 0;
  const limit = process.argv[2] !== 'response' ? 32 * 1024 * 1024 : 16 * 1024 * 1024;
  for await (const chunk of process.stdin) { length += chunk.length; need(length <= limit, 'wire_size_limit'); chunks.push(chunk); }
  const decode = process.argv[2] === 'response' ? decodeResponse
    : value => decodeRequest(value, { requireImages: process.argv[2] !== 'source-request' });
  process.stdout.write(JSON.stringify(decode(Buffer.concat(chunks))) + '\n');
}
