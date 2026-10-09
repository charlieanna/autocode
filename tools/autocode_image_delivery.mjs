// Stage-local OpenCode plugin. Observe bytes at fetch, not before image transforms.
// No auth loader, endpoint, body, response or permission is replaced. A missing
// hook (including a cached fetch or WebSocket transport) produces no authority.
import { appendFileSync, fsyncSync, openSync, readFileSync } from 'node:fs';
import { createHash, randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';

const hash = (data) => createHash('sha256').update(data).digest('hex');
const header = 'x-autocode-image-request';

function toolCall(call, responses = false) {
  const id = responses ? call?.call_id : call?.id;
  const name = responses ? call?.name : call?.function?.name;
  const args = responses ? call?.arguments : call?.function?.arguments;
  if (typeof id !== 'string' || !id || typeof name !== 'string' || !name || typeof args !== 'string') throw new Error('invalid_tool_call');
  const parsed = JSON.parse(args);
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('invalid_tool_arguments');
  return id;
}

function completedResponse(row) {
  if (!row || typeof row.id !== 'string' || !row.id || row.error != null || row.incomplete_details != null
      || (row.status !== undefined && row.status !== 'completed')) throw new Error('invalid_completion');
  let calls = [], text = '';
  if (row.object === 'response' && row.status === 'completed' && Array.isArray(row.output)) {
    for (const output of row.output) {
      if (output.type === 'function_call') {
        if (output.status !== undefined && output.status !== 'completed') throw new Error('incomplete_tool_call');
        calls.push(toolCall(output, true));
      } else if (output.type === 'message') {
        if (output.role !== 'assistant' || (output.status !== undefined && output.status !== 'completed') || !Array.isArray(output.content)) throw new Error('invalid_output_message');
        for (const content of output.content) {
          if (content.type !== 'output_text' || typeof content.text !== 'string') throw new Error('unsupported_output');
          text += content.text;
        }
      } else if (output.type !== 'reasoning') throw new Error('unsupported_output');
    }
  } else if (row.object === 'chat.completion' && Array.isArray(row.choices) && row.choices.length === 1
      && row.choices[0].index === 0 && row.choices[0].message?.role === 'assistant') {
    const choice = row.choices[0];
    text = choice.message.content ?? '';
    if (typeof text !== 'string') throw new Error('invalid_output_text');
    if (choice.finish_reason === 'tool_calls' && Array.isArray(choice.message.tool_calls) && choice.message.tool_calls.length) {
      calls = choice.message.tool_calls.map(call => {
        if (call.type !== 'function') throw new Error('unsupported_tool_call');
        return toolCall(call);
      });
    } else if (choice.finish_reason !== 'stop' || choice.message.tool_calls?.length) throw new Error('invalid_finish');
  } else throw new Error('invalid_completion');
  if (new Set(calls).size !== calls.length || (!calls.length && !text)) throw new Error('missing_terminal_output');
  return { response_id: row.id, finish_reason: calls.length ? 'tool_calls' : 'stop',
           tool_call_ids: calls, output_text_sha256: hash(text) };
}

function completion(text) {
  const trimmed = text.trim();
  if (trimmed.startsWith('{')) return { ...completedResponse(JSON.parse(trimmed)), wire_format: 'json' };
  const normalized = text.replace(/\r\n/g, '\n');
  if (!normalized.endsWith('\n\n')) throw new Error('truncated_sse');
  let id = null, kind = null, finished = false, done = false, terminal = null, outputText = '';
  const tools = new Map();
  for (const frame of normalized.split('\n\n')) {
    if (!frame.trim()) continue;
    const data = [];
    let event = null;
    for (const line of frame.split('\n')) {
      if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
      else if (line.startsWith('event:')) event = line.slice(6).trim();
      else if (!line.startsWith(':') && !line.startsWith('id:') && !line.startsWith('retry:')) throw new Error('invalid_sse');
    }
    if (!data.length) continue;
    if (done) throw new Error('data_after_done');
    if (data.join('\n') === '[DONE]') {
      if (!finished) throw new Error('premature_done');
      done = true;
      continue;
    }
    const row = JSON.parse(data.join('\n'));
    if (row.error != null || ['error', 'response.failed', 'response.incomplete'].includes(row.type)) throw new Error('provider_error');
    const nextKind = row.object === 'chat.completion.chunk' ? 'chat' : typeof row.type === 'string' && row.type.startsWith('response.') ? 'responses' : null;
    if (!nextKind || (kind && nextKind !== kind)) throw new Error('unsupported_sse');
    kind = nextKind;
    if (event && row.type && event !== row.type) throw new Error('event_mismatch');
    const nextID = kind === 'chat' ? row.id : row.response?.id ?? row.response_id;
    if (nextID !== undefined) {
      if (typeof nextID !== 'string' || !nextID || (id !== null && id !== nextID)) throw new Error('response_id_mismatch');
      id = nextID;
    }
    if (kind === 'chat') {
      if (!Array.isArray(row.choices) || row.choices.length > 1) throw new Error('invalid_choices');
      for (const choice of row.choices) {
        if (finished || choice.index !== 0) throw new Error('invalid_finish');
        const delta = choice.delta;
        if (!delta || typeof delta !== 'object' || (delta.role !== undefined && delta.role !== 'assistant')) throw new Error('invalid_delta');
        if (delta.content != null) {
          if (typeof delta.content !== 'string') throw new Error('invalid_output_text');
          outputText += delta.content;
        }
        if (delta.tool_calls !== undefined) {
          if (!Array.isArray(delta.tool_calls)) throw new Error('invalid_tool_calls');
          for (const call of delta.tool_calls) {
            if (!Number.isSafeInteger(call.index) || call.index < 0 || call.index > 255) throw new Error('invalid_tool_index');
            const entry = tools.get(call.index) ?? {function:{name:'',arguments:''}};
            if (call.id !== undefined) {
              if (typeof call.id !== 'string' || !call.id || (entry.id !== undefined && entry.id !== call.id)) throw new Error('tool_id_mismatch');
              entry.id = call.id;
            }
            if (call.type !== undefined && call.type !== 'function') throw new Error('unsupported_tool_call');
            for (const key of ['name','arguments']) if (call.function?.[key] !== undefined) {
              if (typeof call.function[key] !== 'string') throw new Error('invalid_tool_delta');
              entry.function[key] += call.function[key];
            }
            tools.set(call.index, entry);
          }
        }
        if (choice.finish_reason != null) {
          if (!['stop','tool_calls'].includes(choice.finish_reason)) throw new Error('invalid_finish');
          const indices = [...tools.keys()].sort((a,b) => a-b);
          if (indices.some((value,index) => value !== index)) throw new Error('missing_tool_index');
          const calls = indices.map(index => toolCall(tools.get(index)));
          if (new Set(calls).size !== calls.length || (choice.finish_reason === 'tool_calls') !== (calls.length > 0)
              || (!calls.length && !outputText)) throw new Error('missing_terminal_output');
          terminal = {response_id:id,finish_reason:choice.finish_reason,tool_call_ids:calls,output_text_sha256:hash(outputText)};
          finished = true;
        }
      }
    } else {
      if (finished) throw new Error('data_after_finish');
      if (row.type === 'response.completed') {
        terminal = completedResponse(row.response);
        if (terminal.response_id !== id || id === null) throw new Error('invalid_completion');
        finished = true;
      }
    }
  }
  if (!finished || !id || (kind === 'chat' && !done)) throw new Error('missing_terminal_response');
  return { ...terminal, wire_format: 'sse' };
}

function images(body) {
  const result = [];
  let complete = true;
  function visit(value) {
    if (!value || typeof value !== 'object') return;
    if (value.type === 'input_image' || value.type === 'image_url') {
      const url = value.type === 'input_image' ? value.image_url : value.image_url?.url;
      const match = typeof url === 'string' && /^data:(image\/(?:png|jpeg|webp));base64,([A-Za-z0-9+/]+={0,2})$/.exec(url);
      if (!match) { complete = false; return; }
      const data = Buffer.from(match[2], 'base64');
      if (data.toString('base64') !== match[2]) { complete = false; return; }
      result.push({ sha256: hash(data), mime: match[1], bytes: data.length });
      return;
    }
    for (const item of Object.values(value)) {
      if (Array.isArray(item)) item.forEach(visit);
      else if (item && typeof item === 'object') visit(item);
    }
  }
  // These are the two OpenAI wire formats, not arbitrary strings in a prompt.
  if (Array.isArray(body.input)) body.input.forEach(visit);
  else if (Array.isArray(body.messages)) body.messages.forEach(visit);
  else complete = false;
  return { images: result, payload_complete: complete && result.length > 0 };
}

export default async function ImageDelivery() {
  const invalid = () => { process.stderr.write('Invalid runner image audit configuration\n'); process.exit(78); };
  if (!process.env.AUTOCODE_IMAGE_AUDIT) return {};
  let options;
  try { options = JSON.parse(process.env.AUTOCODE_IMAGE_AUDIT); } catch { invalid(); }
  if (!options || typeof options !== 'object' || Array.isArray(options)) invalid();
  const maxRequests = Object.hasOwn(options, 'max_requests') ? options.max_requests : 1;
  const reviewer = options.reviewer;
  if (typeof options.path !== 'string' || !options.path || typeof options.attempt_id !== 'string'
      || !options.attempt_id || !/^[a-f0-9]{64}$/.test(options.binding_sha256 || '')
      || !Number.isSafeInteger(maxRequests) || maxRequests < 1 || reviewer?.provider !== 'opencode'
      || !['model', 'agent'].every(key => typeof reviewer[key] === 'string' && reviewer[key].length > 0 && reviewer[key].length <= 256)
      || Object.keys(reviewer).some(key => !['provider', 'model', 'agent'].includes(key))) invalid();
  // Keep the descriptor inside the client. It must never be inherited by tools.
  let fd;
  try { fd = openSync(options.path, 'ax', 0o600); } catch { invalid(); }
  let sequence = 0;
  const write = (row) => appendFileSync(fd, JSON.stringify({ sequence: sequence++, at_ms: Date.now(), ...row }) + '\n');
  try { write({ type: 'audit_start', version: 3, attempt_id: options.attempt_id,
          binding_sha256: options.binding_sha256, max_requests: maxRequests, reviewer,
          plugin_sha256: hash(readFileSync(fileURLToPath(import.meta.url))) }); } catch { invalid(); }
  const contexts = new Map();
  let admitted = 0, identity = null;
  const deny = (reason, requestID = null) => {
    try {
      write({ type: 'request_denied', reason, request_id: contexts.has(requestID) ? requestID : null,
              admitted_requests: admitted, max_requests: maxRequests });
      fsyncSync(fd);
    } finally {
      // Stop this owned client, not a retryable exception inside its SDK loop.
      process.exit(77);
    }
  };
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async function auditedFetch(input, init) {
    const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
    const requestID = headers.get(header);
    const context = contexts.get(requestID);
    if (!context) {
      if (requestID !== null) deny('unknown_review_nonce');
      const method = (init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase();
      const pathname = new URL(input instanceof Request ? input.url : input).pathname;
      if (/(?:^|\/)(?:responses|messages|completions|embeddings)(?:\/|$)/.test(pathname)) deny('unattributed_inference');
      if (method !== 'GET' && method !== 'HEAD') {
        const body = typeof init?.body === 'string' ? init.body : init?.body instanceof URLSearchParams ? init.body.toString()
          : input instanceof Request && !init?.body ? await input.clone().text() : null;
        if (body === null) deny('unclassified_request_body');
        let parsed, grant;
        try { parsed = JSON.parse(body); } catch {
          grant = new URLSearchParams(body).get('grant_type');
        }
        if (parsed && ['model', 'input', 'messages', 'prompt', 'contents'].some(key => Object.hasOwn(parsed, key))) deny('unattributed_inference');
        grant ??= parsed?.grant_type;
        // OAuth refresh stays native. Other unattributed POSTs are not free.
        if (!/(?:^|\/)token$/.test(pathname) || !['refresh_token', 'authorization_code'].includes(grant)) deny('unclassified_request_body');
      }
      return originalFetch(input, init);
    }
    if (admitted >= maxRequests) deny('max_requests_exhausted', requestID);
    // Reserve synchronously, before reading a body or yielding to another fetch.
    const admissionIndex = ++admitted;
    let body;
    try {
      body = typeof init?.body === 'string' ? init.body : input instanceof Request && !init?.body ? await input.clone().text() : null;
      if (body === null) throw new Error('Unsupported request body');
      const parsed = JSON.parse(body);
      write({ type: 'request', request_id: requestID, admission_index: admissionIndex, ...context, body_sha256: hash(body),
              serialized_model: typeof parsed.model === 'string' ? parsed.model : null, ...images(parsed) });
    } catch {
      write({ type: 'request_unverified', request_id: requestID, admission_index: admissionIndex });
    }
    try {
      const response = await originalFetch(input, init);
      let clone = null;
      try { clone = response.clone(); } catch { /* Audit failure must not change the provider response. */ }
      write({ type: 'response', request_id: requestID, admission_index: admissionIndex, status: response.status,
              response_id: response.headers.get('x-request-id'),
              content_type: response.headers.get('content-type')?.slice(0, 200) ?? null,
              body_present: response.body != null, cloneable: clone != null });
      // Clone only; the provider's original response and consumption are intact.
      if (clone) void inspect(clone, requestID, admissionIndex).catch(() => write({ type: 'response_unverified', request_id: requestID, admission_index: admissionIndex, reason: 'malformed_or_unreadable_body' }));
      else write({ type: 'response_unverified', request_id: requestID, admission_index: admissionIndex, reason: 'clone_failed' });
      return response;
    } catch (error) {
      write({ type: 'request_failed', request_id: requestID, admission_index: admissionIndex });
      throw error;
    }
  };

  async function inspect(response, requestID, admissionIndex) {
    if (!response.ok || !response.body) {
      write({ type: 'response_unverified', request_id: requestID, admission_index: admissionIndex, reason: !response.ok ? 'http_failure' : 'body_missing' });
      if (response.body) void response.body.cancel().catch(() => {});
      return;
    }
    const reader = response.body.getReader();
    const chunks = [];
    let length = 0;
    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        length += value.length;
        if (length > 16 * 1024 * 1024) {
          void reader.cancel().catch(() => {});
          write({ type: 'response_unverified', request_id: requestID, admission_index: admissionIndex, reason: 'body_size_limit' });
          return;
        }
        chunks.push(value);
      }
      const body = Buffer.concat(chunks);
      const terminal = completion(new TextDecoder('utf-8', { fatal: true }).decode(body));
      write({ type: 'request_complete', request_id: requestID, admission_index: admissionIndex, ...terminal,
              complete: true, response_body_sha256: hash(body), response_bytes: body.length });
    } finally {
      reader.releaseLock();
    }
  }

  return {
    'chat.headers': async (input, output) => {
      const requestID = randomUUID();
      const context = { session_id: input.sessionID, user_message_id: input.message.id,
                        agent: input.agent, provider: 'opencode',
                        model: `${input.model.providerID}/${input.model.id}`, wire_model: input.model.id,
                        image_capable: input.model.capabilities?.input?.image === true };
      if (['provider', 'model', 'agent'].some(key => context[key] !== reviewer[key])) deny('unexpected_review_context');
      if (![context.session_id, context.user_message_id].every(value => typeof value === 'string' && value.length > 0)) deny('missing_review_identity');
      if (identity && (identity.session_id !== context.session_id || identity.user_message_id !== context.user_message_id)) deny('foreign_review_identity');
      identity ??= { session_id: context.session_id, user_message_id: context.user_message_id };
      contexts.set(requestID, context);
      output.headers[header] = requestID;
      write({ type: 'review_context', request_id: requestID, ...context });
    },
    event: async ({ event }) => {
      if (event.type === 'message.updated') {
        const info = event.properties.info;
        if (info.role !== 'assistant') return;
        write({ type: 'native_message', session_id: info.sessionID, message_id: info.id,
                user_message_id: info.parentID, model: `${info.providerID}/${info.modelID}`,
                agent: info.agent, finish: info.finish ?? null });
      }
      if (event.type === 'message.part.updated') {
        const part = event.properties.part;
        if (part.type !== 'step-start' && part.type !== 'step-finish') return;
        write({ type: part.type === 'step-start' ? 'native_start' : 'native_finish',
                session_id: part.sessionID, message_id: part.messageID, part_id: part.id,
                reason: part.reason ?? null });
      }
    },
  };
}
