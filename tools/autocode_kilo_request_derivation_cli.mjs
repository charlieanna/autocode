// Model-free parent bridge. The parent authenticates the complete SDK graph,
// this bridge/deriver, original source peer and bounded deny-network/no-fork
// process. This command alone grants no native/source/dispatch authority.
import { createHash } from 'node:crypto';
import { constants, openSync, fstatSync, readFileSync, closeSync } from 'node:fs';
import { isAbsolute, normalize } from 'node:path';
import { pathToFileURL } from 'node:url';
import { deriveExpectedAnthropicRequest } from './autocode_kilo_request_derivation.mjs';

const SDK_SHA256 = 'ddb900ff09bc3858de30378ab7de490cb3cbcb4c4c04ce20ef5116d0f365d18a';
const LIMIT = 32 * 1024 * 1024;
const need = (condition, reason) => { if (!condition) throw new Error('Kilo derivation bridge: ' + reason); };
function sdkLeaf(path) {
  need(typeof path === 'string' && isAbsolute(path) && normalize(path) === path
    && path.endsWith('/node_modules/@ai-sdk/anthropic/dist/index.mjs'), 'unsupported_sdk_entry');
  const fd = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const before = fstatSync(fd), raw = readFileSync(fd), after = fstatSync(fd);
    need(before.isFile() && before.size === 233810 && raw.length === before.size
      && before.dev === after.dev && before.ino === after.ino && before.size === after.size
      && before.mtimeMs === after.mtimeMs && before.ctimeMs === after.ctimeMs
      && createHash('sha256').update(raw).digest('hex') === SDK_SHA256, 'SDK_entry_pin');
  } finally { closeSync(fd); }
}
async function main() {
  need(process.argv.length === 4 && process.argv[2] === '--sdk-entry', 'exact_parent_arguments_required');
  const entry = process.argv[3];
  sdkLeaf(entry);
  const chunks = []; let length = 0;
  for await (const chunk of process.stdin) {
    length += chunk.length; need(length <= LIMIT, 'source_envelope_bound'); chunks.push(chunk);
  }
  need(length > 0, 'source_envelope_missing');
  const text = new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks));
  const envelope = JSON.parse(text);
  // No duplicate-key/alternate JSON representation can disappear on parsing.
  need(JSON.stringify(envelope) === text, 'noncanonical_envelope_JSON');
  globalThis.fetch = () => { throw new Error('Kilo derivation bridge: network_forbidden'); };
  const { createAnthropic } = await import(pathToFileURL(entry).href);
  sdkLeaf(entry);
  const result = await deriveExpectedAnthropicRequest(envelope, createAnthropic);
  sdkLeaf(entry);
  process.stdout.write(JSON.stringify(result) + '\n');
}
main().catch(() => {
  // Never echo source history, SDK options or environment on refusal.
  process.stderr.write('Kilo request derivation refused\n');
  process.exitCode = 1;
});
