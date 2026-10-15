// Derive the complete Messages body from an authenticated original V3 source.
// The parent authenticates the source peer and the pinned createAnthropic export.
// Accepting these arguments proves neither SDK provenance nor native readiness.
import { createHash } from 'node:crypto';

export const MODEL = 'claude-opus-5-5';
export const SDK_VERSION = '3.0.111';
export const PUBLIC_API_KEY = 'autocode-source-derivation-public-placeholder';
// Exact builtin Anthropic loader configuration, distinct from V3 call headers.
export const BUILTIN_ANTHROPIC_HEADERS = Object.freeze({
  'anthropic-beta': 'interleaved-thinking-2025-05-14,fine-grained-tool-streaming-2025-05-14',
});
const BASE_URL = 'https://api.anthropic.com/v1';
const LIMIT = 32 * 1024 * 1024;
const sha = value => createHash('sha256').update(value).digest('hex');
const need = (condition, reason) => { if (!condition) throw new Error('Kilo request derivation: ' + reason); };
const hash = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const own = (value, reason) => {
  need(value !== null && typeof value === 'object' && !Array.isArray(value)
    && [Object.prototype, null].includes(Object.getPrototypeOf(value)), reason);
  for (const key of Reflect.ownKeys(value)) {
    need(typeof key === 'string' && !['__proto__', 'constructor', 'prototype'].includes(key), reason);
    const d = Object.getOwnPropertyDescriptor(value, key);
    need(d && 'value' in d && d.enumerable, reason);
  }
};
const keys = (value, allowed, reason, exact = false) => {
  own(value, reason);
  need(Object.keys(value).every(key => allowed.includes(key))
    && (!exact || Object.keys(value).length === allowed.length), reason);
};

// Same ordered, lossless data grammar as original-call-custody canonical().
// SOURCE_COMMIT is already sealed, so raw ['bytes', ...] tags are forbidden.
function decode(node, depth = 0) {
  need(depth <= 80 && Array.isArray(node), 'canonical_depth_or_node');
  const [tag, value] = node;
  if (tag === 'undefined' || tag === 'null') {
    need(node.length === 1, 'canonical_arity');
    return tag === 'null' ? null : undefined;
  }
  if (['string', 'boolean', 'number'].includes(tag)) {
    need(node.length === 2 && typeof value === tag
      && (tag !== 'number' || Number.isFinite(value)), 'canonical_primitive');
    return value;
  }
  need(node.length === 2 && Array.isArray(value), 'canonical_container');
  if (tag === 'array') return value.map(item => decode(item, depth + 1));
  need(tag === 'object', 'canonical_tag');
  const result = Object.create(null);
  for (const pair of value) {
    need(Array.isArray(pair) && pair.length === 2 && typeof pair[0] === 'string'
      && !['__proto__', 'constructor', 'prototype'].includes(pair[0])
      && !Object.hasOwn(result, pair[0]), 'canonical_object_key');
    result[pair[0]] = decode(pair[1], depth + 1);
  }
  return result;
}
function canonical(value) {
  if (value === undefined) return '["undefined"]';
  if (value === null) return '["null"]';
  if (typeof value !== 'object') return JSON.stringify([typeof value, value]);
  if (Array.isArray(value)) return '["array",[' + value.map(canonical).join(',') + ']]';
  return '["object",[' + Object.keys(value).map(key => '[' + JSON.stringify(key) + ','
    + canonical(value[key]) + ']').join(',') + ']]';
}
function unsealFile(fact) {
  keys(fact, ['bytes', 'base64', 'sha256'], 'file_seal_fields', true);
  need(Number.isSafeInteger(fact.bytes) && fact.bytes > 0 && fact.bytes <= 8 * 1024 * 1024
    && typeof fact.base64 === 'string' && hash(fact.sha256), 'file_seal_values');
  const raw = Buffer.from(fact.base64, 'base64');
  need(raw.toString('base64') === fact.base64 && raw.length === fact.bytes
    && sha(raw) === fact.sha256, 'file_seal_integrity');
  return new Uint8Array(raw);
}
function headers(value) {
  own(value, 'headers');
  const names = Object.keys(value).map(key => key.toLowerCase());
  need(new Set(names).size === names.length, 'duplicate_headers');
  for (const [name, text] of Object.entries(value)) {
    need(!/authorization|api.?key|cookie|token|proxy/i.test(name)
      && /^[!#$%&'*+.^_`|~0-9a-z-]+$/i.test(name)
      && typeof text === 'string' && !/[\r\n]/.test(text), 'private_or_invalid_header');
  }
  return value;
}
function finalOptions(final) {
  keys(final, ['prompt', 'maxOutputTokens', 'temperature', 'topP', 'topK', 'tools', 'toolChoice',
    'providerOptions', 'headers', 'includeRawChunks', 'frequencyPenalty', 'presencePenalty',
    'stopSequences', 'seed', 'responseFormat'], 'v3_fields');
  need(Number.isSafeInteger(final.maxOutputTokens) && final.maxOutputTokens > 0
    && final.maxOutputTokens <= 128000, 'token_bound');
  for (const key of ['temperature', 'topP', 'topK', 'frequencyPenalty', 'presencePenalty',
    'stopSequences', 'seed', 'responseFormat']) need(final[key] === undefined, 'unsupported_v3_option:' + key);
  need(final.includeRawChunks === undefined || final.includeRawChunks === false, 'raw_chunks');
  keys(final.providerOptions, ['anthropic'], 'provider_namespace', true);
  const options = final.providerOptions.anthropic;
  keys(options, ['effort', 'thinking', 'cacheControl', 'toolStreaming', 'sendReasoning',
    'disableParallelToolUse', 'structuredOutputMode'], 'anthropic_options');
  keys(options.thinking, ['type', 'display', 'blockBinding'], 'thinking_fields', true);
  keys(options.thinking.blockBinding, ['prefixMismatchBehavior'], 'block_binding_fields', true);
  need(options.effort === 'high' && options.thinking.type === 'adaptive'
    && options.thinking.display === 'summarized'
    && options.thinking.blockBinding.prefixMismatchBehavior === 'drop_block', 'not_genuine_high');
  headers(final.headers);
  keys(final.toolChoice, ['type', 'toolName'], 'tool_choice');
  need(['auto', 'none', 'required', 'tool'].includes(final.toolChoice.type)
    && (final.toolChoice.type !== 'tool' || typeof final.toolChoice.toolName === 'string'), 'tool_choice_type');
  need(Array.isArray(final.tools), 'tools');
  const names = new Set();
  for (const tool of final.tools) {
    keys(tool, ['type', 'name', 'description', 'inputSchema', 'providerOptions'], 'tool_fields');
    need(tool.type === 'function' && typeof tool.name === 'string' && tool.name.length > 0
      && !names.has(tool.name) && typeof tool.description === 'string'
      && tool.providerOptions === undefined, 'tool_definition');
    names.add(tool.name);
    // Schema objects, including objects resembling byte seals, stay untouched.
    need(typeof tool.inputSchema === 'boolean' || (tool.inputSchema !== null
      && typeof tool.inputSchema === 'object' && !Array.isArray(tool.inputSchema)), 'tool_schema');
  }
  need(Array.isArray(final.prompt) && final.prompt.length > 0, 'prompt');
  for (const message of final.prompt) {
    keys(message, ['role', 'content', 'providerOptions'], 'message_fields');
    need(['system', 'user', 'assistant', 'tool'].includes(message.role), 'message_role');
    if (message.role === 'system') { need(typeof message.content === 'string', 'system_content'); continue; }
    need(Array.isArray(message.content), 'message_content');
    for (const part of message.content) {
      own(part, 'prompt_part');
      need(['text', 'reasoning', 'tool-call', 'tool-result', 'file'].includes(part.type), 'part_type');
      if (part.type === 'file') {
        keys(part, ['type', 'mediaType', 'data', 'filename', 'providerOptions'], 'file_fields');
        need(part.mediaType === 'image/png', 'file_media_type');
        part.data = unsealFile(part.data);
      }
      if (part.type === 'tool-call') need(typeof part.toolCallId === 'string' && part.toolCallId.length > 0
        && typeof part.toolName === 'string' && typeof part.input === 'string', 'tool_call');
      if (part.type === 'tool-result' && part.output?.type === 'content') {
        need(Array.isArray(part.output.value), 'tool_content');
        for (const item of part.output.value) {
          own(item, 'tool_content_part');
          need(['text', 'image-data'].includes(item.type), 'unsupported_tool_content');
          if (item.type === 'image-data') {
            need(item.mediaType === 'image/png' && typeof item.data === 'string', 'tool_image_data');
            const raw = Buffer.from(item.data, 'base64');
            need(raw.length > 0 && raw.length <= 8 * 1024 * 1024
              && raw.toString('base64') === item.data, 'tool_image_base64');
          }
        }
      }
    }
  }
  return final;
}

/** Source must already be authenticated by the owner relay. pinnedFactory is
 * the separately authenticated real createAnthropic export, never a module path.
 * No actual wire body is accepted, no IO is performed, and no doStream is called.
 * Header output is exact at model.getHeaders; the parent must authenticate the
 * provider-utils/runtime user-agent suffix separately before dispatch. */
export async function deriveExpectedAnthropicRequest(authenticatedSource, pinnedFactory) {
  const fields = ['canonical_record', 'source_digest', 'owner_digest', 'source_index', 'model_id'];
  keys(authenticatedSource, fields, 'source_envelope', true);
  const envelope = Object.fromEntries(fields.map(key => [key, authenticatedSource[key]]));
  need(typeof envelope.canonical_record === 'string'
    && Buffer.byteLength(envelope.canonical_record) <= LIMIT
    && hash(envelope.source_digest) && sha(envelope.canonical_record) === envelope.source_digest,
  'source_digest');
  need(hash(envelope.owner_digest) && Number.isSafeInteger(envelope.source_index)
    && envelope.source_index >= 0 && envelope.source_index < 16 && envelope.model_id === MODEL, 'source_identity');
  const source = decode(JSON.parse(envelope.canonical_record));
  need(canonical(source) === envelope.canonical_record, 'noncanonical_source');
  keys(source, ['kind', 'owner', 'ownerDigest', 'index', 'prepared', 'historyDigest', 'before', 'final',
    'bindDigest'], 'source_record', true);
  need(source.kind === 'ORIGINAL_V3_SOURCE' && source.ownerDigest === envelope.owner_digest
    && sha(canonical(source.owner)) === envelope.owner_digest && source.index === envelope.source_index
    && hash(source.historyDigest) && hash(source.bindDigest), 'source_correlation');
  const nonPrompt = value => Object.fromEntries(Object.entries(value).filter(([key]) => key !== 'prompt'));
  need(canonical(nonPrompt(source.before)) === canonical(nonPrompt(source.final)), 'source_nonprompt_changed');
  const finalV3 = finalOptions(source.final);
  need(typeof pinnedFactory === 'function', 'sdk_factory');
  const denyFetch = () => { throw new Error('Kilo request derivation: network_forbidden'); };
  const provider = pinnedFactory({ baseURL: BASE_URL, apiKey: PUBLIC_API_KEY,
    headers: BUILTIN_ANTHROPIC_HEADERS, fetch: denyFetch });
  need(typeof provider === 'function' && provider.specificationVersion === 'v3', 'sdk_provider');
  const model = provider(MODEL);
  need(model?.modelId === MODEL && model.specificationVersion === 'v3'
    && model.provider === 'anthropic.messages' && model.config?.provider === 'anthropic.messages'
    && model.config.baseURL === BASE_URL && model.config.fetch === denyFetch
    && model.config.buildRequestUrl === undefined && model.config.transformRequestBody === undefined
    && typeof model.config.headers === 'function'
    && typeof model.getArgs === 'function' && typeof model.getHeaders === 'function'
    && typeof model.getBetasFromHeaders === 'function', 'unsupported_sdk_branch');
  const configHeaders = await model.config.headers();
  keys(configHeaders, ['x-api-key', 'anthropic-version', 'anthropic-beta', 'user-agent'], 'sdk_config_headers', true);
  need(configHeaders['x-api-key'] === PUBLIC_API_KEY
    && configHeaders['anthropic-version'] === '2023-06-01'
    && configHeaders['anthropic-beta'] === BUILTIN_ANTHROPIC_HEADERS['anthropic-beta']
    && configHeaders['user-agent'] === 'ai-sdk/anthropic/' + SDK_VERSION, 'sdk_default_config_headers');
  const userSuppliedBetas = await model.getBetasFromHeaders(finalV3.headers);
  need(userSuppliedBetas instanceof Set, 'sdk_beta_type');
  const expectedInputBetas = new Set([
    ...BUILTIN_ANTHROPIC_HEADERS['anthropic-beta'].toLowerCase().split(','),
    ...(finalV3.headers['anthropic-beta'] ?? '').toLowerCase().split(','),
  ].map(value => value.trim()).filter(Boolean));
  need(JSON.stringify(Array.from(userSuppliedBetas)) === JSON.stringify(Array.from(expectedInputBetas)),
    'sdk_builtin_config_betas');
  const derived = await model.getArgs({ ...finalV3, stream: true, userSuppliedBetas });
  need(derived?.providerOptionsName === 'anthropic' && derived.usedCustomProviderKey === false
    && derived.usesJsonResponseTool === false && Array.isArray(derived.warnings)
    && derived.warnings.length === 0 && derived.betas instanceof Set, 'unsupported_sdk_conversion');
  need(derived.args?.model === MODEL && derived.args.stream === true
    && derived.args.output_config?.effort === 'high' && derived.args.thinking?.type === 'adaptive'
    && derived.args.thinking.display === 'summarized'
    && derived.args.thinking.block_binding?.prefix_mismatch_behavior === 'drop_block', 'sdk_high_postcondition');
  const expectedBody = JSON.stringify({ ...derived.args, stream: true });
  need(Buffer.byteLength(expectedBody) <= LIMIT, 'body_size');
  const sdkHeaders = await model.getHeaders({ betas: derived.betas, headers: finalV3.headers });
  const normalized = Object.fromEntries(Object.entries(sdkHeaders).map(([key, value]) => [key.toLowerCase(), value]));
  const preparedHeaders = Object.fromEntries(Object.entries(finalV3.headers).map(([key, value]) => [key.toLowerCase(), value]));
  need(normalized['x-api-key'] === PUBLIC_API_KEY && normalized['anthropic-version'] === '2023-06-01'
    && normalized['anthropic-beta'] === Array.from(derived.betas).join(',')
    && typeof normalized['user-agent'] === 'string'
    && normalized['user-agent'].length > 0
    && normalized['user-agent'] === (preparedHeaders['user-agent'] ?? configHeaders['user-agent']), 'sdk_default_headers');
  delete normalized['x-api-key'];
  headers(normalized);
  const expectedUserAgentPrefix = normalized['user-agent'];
  delete normalized['user-agent'];
  need(normalized['content-type'] === undefined || normalized['content-type'] === 'application/json', 'content_type');
  normalized['content-type'] = 'application/json';
  return { expectedBody, expectedBodySha256: sha(expectedBody), expectedBodyBytes: Buffer.byteLength(expectedBody),
    expectedHeaders: normalized, expectedBetas: Array.from(derived.betas), expectedUserAgentPrefix,
    headerScope: 'exact_nonsecret_headers_except_provider_utils_runtime_user_agent_suffix',
    sourceSha: envelope.source_digest, ownerDigest: envelope.owner_digest, sourceIndex: envelope.source_index,
    modelId: MODEL, sdkVersion: SDK_VERSION };
}
