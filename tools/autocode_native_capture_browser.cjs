/* Capability transport only. The original Python owner supplies task authority. */
'use strict';
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const net = require('node:net');
const {createHash} = require('node:crypto');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const requireFact = (value, reason) => { if (!value) throw new Error(`Native capture: ${reason}`); };

const MAX_TARGET_REQUESTS = 64, MAX_CAPTURE_REQUESTS = 128;

// Darwin dev_t is signed int32; compare exact bigint kernel stat values.
// Reject out-of-range declarations rather than normalizing arbitrary authority.
function sameSocket(actual, expected) {
  if (![expected.device, expected.inode, expected.mode].every(Number.isSafeInteger) ||
      expected.inode < 0 || expected.mode < 0 || expected.mode > 65535) return false;
  if (process.platform === 'darwin' && (expected.device < -2147483648 || expected.device > 2147483647)) return false;
  const device = process.platform === 'darwin' ? BigInt.asIntN(32, actual.dev) : actual.dev;
  return actual.isSocket() && device === BigInt(expected.device) &&
    actual.ino === BigInt(expected.inode) && actual.mode === BigInt(expected.mode);
}
const statFacts = actual => JSON.stringify({device: String(actual.dev), inode: String(actual.ino), mode: String(actual.mode)});

function install(config) {
  const value = config.native_transport;
  requireFact(value && Object.keys(value).sort().join(',') === 'cdp,listener,nonce,requests,responses', 'invalid parent transport');
  const cdp = value.cdp;
  requireFact(cdp && cdp.nonce === value.nonce && cdp.socket && Number.isSafeInteger(cdp.socket.fd) &&
    cdp.socket.fd > 4, 'invalid original CDP capability');
  const cdpIdentity = fs.fstatSync(cdp.socket.fd, {bigint: true});
  requireFact(sameSocket(cdpIdentity, cdp.socket), 'inherited CDP identity changed; actual stat=' + statFacts(cdpIdentity));
  const originalConnect = net.connect, originalCreateConnection = net.createConnection;
  let cdpConnected = false;
  const connect = function(options) {
    requireFact(!cdpConnected && options && options.host === '127.0.0.1' &&
      Number(options.port) === 80 && !options.path && !options.socketPath && !options.localAddress && !options.localPort,
      'only the single inherited CDP channel is supported');
    cdpConnected = true;
    const socket = new net.Socket({fd: cdp.socket.fd, readable: true, writable: true});
    process.nextTick(() => socket.emit('connect'));
    return socket;
  };
  const lease = value.listener;
  requireFact(lease && Number.isInteger(lease.fd) && lease.fd > 2 &&
    Array.isArray(lease.address) && lease.address[0] === '127.0.0.1' &&
    Number.isInteger(lease.address[1]) && lease.address[1] > 0, 'invalid original listener');
  const identity = fs.fstatSync(lease.fd);
  requireFact(identity.dev === lease.device && identity.ino === lease.inode && identity.mode === lease.mode,
    'inherited listener identity changed');
  const origin = `http://127.0.0.1:${lease.address[1]}`;
  const preview = config.preview_origin === undefined ? null : config.preview_origin;
  requireFact(preview === null || preview === 'http://127.0.0.1:3000', 'invalid exact preview origin');
  requireFact(preview !== origin, 'preview collides with original primary origin');
  let previewAssets = 0;
  for (const asset of config.assets) {
    const primary = asset.url.startsWith('/');
    requireFact(primary || (preview && asset.url.startsWith(preview + '/')), 'undeclared absolute preview asset');
    const target = primary ? asset.url : asset.url.slice(preview.length);
    requireFact(target.startsWith('/') && !target.startsWith('//') && !target.includes('#') &&
      !target.includes('\\') && !decodeURIComponent(target.split('?')[0]).split('/').includes('..'),
      'invalid literal preview target');
    if (!primary) previewAssets++;
  }
  requireFact(preview === null || previewAssets > 0, 'declared preview has no immutable asset');
  const original = http.Server.prototype.listen;
  let selected = null, sequence = 0;
  const seen = new Set();
  const counts = new Map();
  let poisoned = false;
  const listen = function(...args) {
    requireFact(selected === null && this instanceof http.Server && args.length === 3 && args[0] === 0 &&
      args[1] === '127.0.0.1' && typeof args[2] === 'function', 'fixture listener must be one exact ephemeral IPv4 server');
    selected = this;
    return original.call(this, {fd: lease.fd}, args[2]);
  };
  const restore = () => { http.Server.prototype.listen = original; net.connect = originalConnect; net.createConnection = originalCreateConnection; };
  const deadline = Date.now() + config.timeout_ms;
  async function route(route) {
    requireFact(!poisoned, 'acquisition was poisoned by a consumed failure');
    const request = route.request();
    const requested = request.url();
    const url = new URL(requested);
    const primary = requested.startsWith(origin + '/');
    const requestedOrigin = primary ? origin : preview;
    const target = primary ? requested.slice(origin.length) : requested;
    requireFact(requestedOrigin && url.origin === requestedOrigin && url.protocol === 'http:' &&
      url.hostname === '127.0.0.1' && !url.username && !url.password && !url.hash &&
      requested === requestedOrigin + url.pathname + url.search &&
      request.method() === 'GET' && request.postData() === null &&
      config.assets.some(asset => asset.url === target),
      'request escaped the exact original case/lease/assets');
    requireFact(sequence < MAX_CAPTURE_REQUESTS && (counts.get(target) || 0) < MAX_TARGET_REQUESTS,
      'capture or exact target request bound reached');
    const id = sequence++;
    counts.set(target, (counts.get(target) || 0) + 1);
    try {
      const packet = {nonce: value.nonce, sequence: id, asset: target};
      const temporary = path.join(value.requests, `${id}.tmp`);
      fs.writeFileSync(temporary, JSON.stringify(packet), {flag: 'wx'});
      fs.renameSync(temporary, path.join(value.requests, `${id}.json`));
      const output = path.join(value.responses, `${id}.json`);
      while (!fs.existsSync(output)) {
        requireFact(Date.now() < deadline, 'parent asset response exceeded original capture deadline');
        await new Promise(resolve => setTimeout(resolve, 10));
      }
      requireFact(fs.lstatSync(output).isFile() && !fs.lstatSync(output).isSymbolicLink(), 'aliased parent response');
      const response = JSON.parse(fs.readFileSync(output, 'utf8'));
      requireFact(response.nonce === value.nonce && response.sequence === id && response.asset === target &&
        response.status === 200 && typeof response.content_type === 'string', 'foreign parent response');
      const raw = Buffer.from(response.body, 'base64');
      requireFact(hash(raw) === response.sha256, 'parent response body changed');
      requireFact(!poisoned, 'acquisition was poisoned by another consumed failure');
      await route.fulfill({status: response.status, body: raw,
        headers: {'content-type': response.content_type, 'content-length': String(raw.length)}});
      seen.add(target);
    } catch (error) {
      poisoned = true;
      throw error;
    }
  }
  try {
    net.connect = connect; net.createConnection = connect; http.Server.prototype.listen = listen;
  } catch (error) { restore(); throw error; }
  return {route, restore, connect(chromium) {
    return chromium.connectOverCDP('ws://127.0.0.1:80/' + value.nonce, {timeout: config.timeout_ms});
  }, verify() {
    requireFact(!poisoned, 'acquisition was poisoned by a consumed failure');
    requireFact(cdpConnected, 'original inherited CDP channel was not used');
    requireFact(selected && selected.address() && selected.address().address === '127.0.0.1' &&
      selected.address().port === lease.address[1], 'fixture did not use its actual original listener');
    requireFact(seen.size === config.assets.length, 'incomplete broker asset coverage');
    return {nonce: value.nonce, listener: lease, assets: [...seen]};
  }};
}
module.exports = {install};
