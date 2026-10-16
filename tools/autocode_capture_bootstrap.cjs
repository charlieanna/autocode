/* Trusted fixed transport. Never loads the supplied fixture or application. */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const http = require('node:http');
const {spawn} = require('node:child_process');
const {createHash} = require('node:crypto');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const requireFact = (value, reason) => { if (!value) throw new Error(`Native capture IPC: ${reason}`); };
const MACH = 'org.chromium.Chromium.MachPortRendezvousServer.';
// The shell expands only its own PID. All other values are positional literals.
// exec preserves that actual child identity through sandbox-exec and Chromium.
const BROWSER_EXEC = String.raw`policy=$1; shift; policy="$policy
(allow mach-register (global-name \"org.chromium.Chromium.MachPortRendezvousServer.$$\"))
(allow mach-lookup (global-name \"org.chromium.Chromium.MachPortRendezvousServer.$$\"))
(deny network*)"; exec /usr/bin/sandbox-exec -p "$policy" "$@"`;
const MAX = 16 * 1024 * 1024;
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

function descriptor(value) {
  requireFact(value && Number.isSafeInteger(value.fd) && value.fd > 4, 'invalid inherited IPC descriptor');
  const actual = fs.fstatSync(value.fd, {bigint: true});
  requireFact(sameSocket(actual, value), 'inherited IPC identity changed; actual stat=' + statFacts(actual));
}

async function main() {
  const request = process.argv[2];
  const config = JSON.parse(fs.readFileSync(request, 'utf8'));
  const control = config.native_bootstrap;
  requireFact(control && Object.keys(control).sort().join(',') ===
    'browser_arguments,fixture_script,ipc,policy,policy_sha256,record', 'unknown bootstrap field');
  requireFact(control.fixture_script === path.join(__dirname, 'autocode_visual_browser.cjs'), 'foreign fixture driver');
  descriptor(control.ipc.bridge); descriptor(control.ipc.fixture);
  requireFact(control.ipc.bridge.fd !== control.ipc.fixture.fd &&
    config.native_transport.cdp.nonce === config.native_transport.nonce &&
    JSON.stringify(config.native_transport.cdp.socket) === JSON.stringify(control.ipc.fixture), 'foreign CDP capability');
  const base = fs.readFileSync(control.policy, 'utf8');
  requireFact(hash(base) === control.policy_sha256, 'original kernel policy changed');
  const linux = process.platform === 'linux';
  requireFact(linux || process.platform === 'darwin', 'unsupported native capture platform');
  const linuxKernel = linux ? require('./autocode_native_capture_linux.cjs') : null;
  requireFact(Array.isArray(control.browser_arguments) && control.browser_arguments.every(a => typeof a === 'string') &&
    control.browser_arguments.includes('--remote-debugging-pipe') &&
    !control.browser_arguments.some(a => a.startsWith('--remote-debugging-port') || a === '--single-process'), 'invalid pipe browser arguments');
  // Bundle and all transitive runtime files are original exact-profile inputs.
  const {wsServer} = require(path.join(path.dirname(config.playwright_module), 'lib', 'utilsBundle.js'));
  const history = {version: linux ? 2 : 1, root_pid: process.pid, browser_pid: null, fixture_pid: null,
    policy_sha256: control.policy_sha256,
    ...(linux ? {transport: linuxKernel.TRANSPORT} : {mach_service: null}), browser_exit: null, fixture_exit: null,
    connected: false, nonce: config.native_transport.nonce};
  let browser, fixture, bridge, ws;
  const close = () => { if (ws) ws.close(); if (bridge) bridge.destroy(); };
  try {
    const browserCommand = linux ? linuxKernel.command(control.policy, control.policy_sha256,
      config.executable, control.browser_arguments, 'browser') : ['/bin/sh', ['-c', BROWSER_EXEC,
      'native-owned-browser', base, config.executable, ...control.browser_arguments]];
    browser = spawn(browserCommand[0], browserCommand[1], {
      cwd: process.cwd(), env: process.env, stdio: ['ignore', 'inherit', 'inherit', 'pipe', 'pipe']});
    requireFact(Number.isInteger(browser.pid) && browser.pid > 0, 'browser was not created');
    // Linux records the actual outer bwrap branch roots; the original
    // supervisor owns their namespace children too. It makes no Mach/PID claim.
    history.browser_pid = browser.pid;
    if (!linux) history.mach_service = MACH + browser.pid;
    const web = new wsServer({noServer: true, maxPayload: MAX, perMessageDeflate: false});
    const server = http.createServer((_request, response) => { response.writeHead(403); response.end(); });
    server.on('upgrade', (req, socket, head) => {
      try {
        requireFact(!history.connected && req.method === 'GET' &&
          req.url === '/' + history.nonce && req.headers.host === '127.0.0.1', 'foreign CDP handshake');
        web.handleUpgrade(req, socket, head, connection => {
          history.connected = true; ws = connection;
          connection.on('message', (data, binary) => {
            try {
              requireFact(!binary && data.length <= MAX, 'unsupported CDP message');
              const value = JSON.parse(data.toString('utf8'));
              requireFact(value && typeof value === 'object' && !Array.isArray(value) &&
                Number.isSafeInteger(value.id) && typeof value.method === 'string' && !data.includes(0), 'invalid CDP object');
              browser.stdio[3].write(Buffer.concat([data, Buffer.from([0])]));
            } catch (error) { connection.terminate(); server.emit('error', error); }
          });
          connection.on('error', error => server.emit('error', error));
        });
      } catch (error) { socket.destroy(); server.emit('error', error); }
    });
    let pending = Buffer.alloc(0);
    browser.stdio[4].on('data', chunk => {
      pending = Buffer.concat([pending, chunk]);
      if (pending.length > MAX) { server.emit('error', new Error('CDP output exceeds original finite limit')); return; }
      for (let end; (end = pending.indexOf(0)) !== -1;) {
        const value = pending.subarray(0, end); pending = pending.subarray(end + 1);
        if (!history.connected) { server.emit('error', new Error('CDP output before original connection')); return; }
        // connectOverCDP disconnects after the fixture closes. Only the actual
        // fixture and browser zero exits can make this completed transport.
        if (ws && ws.readyState === 1) ws.send(value.toString('utf8'));
      }
    });
    bridge = new net.Socket({fd: control.ipc.bridge.fd, readable: true, writable: true});
    server.emit('connection', bridge); // No TCP listener, bind, connect or DNS.
    const descriptors = ['ignore', 'inherit', 'inherit'];
    for (const fd of [config.native_transport.listener.fd, control.ipc.fixture.fd]) {
      while (descriptors.length <= fd) descriptors.push('ignore');
      descriptors[fd] = fd;
    }
    const failure = new Promise((_, reject) => {
      server.once('error', reject); browser.once('error', reject);
      browser.stdio[3].once('error', reject); browser.stdio[4].once('error', reject);
    });
    const browserDone = new Promise((resolve, reject) => browser.once('exit', (code, signal) => {
      history.browser_exit = {code, signal};
      if (code !== 0 || signal) reject(new Error('Owned browser failed: ' + JSON.stringify(history.browser_exit)));
      else resolve();
    }));
    const fixtureCommand = linux ? linuxKernel.command(control.policy, control.policy_sha256,
      process.execPath, [control.fixture_script, request], 'fixture') : ['/usr/bin/sandbox-exec',
      ['-f', control.policy, process.execPath, control.fixture_script, request]];
    fixture = spawn(fixtureCommand[0], fixtureCommand[1],
      {cwd: process.cwd(), env: process.env, stdio: descriptors});
    requireFact(Number.isInteger(fixture.pid) && fixture.pid > 0, 'fixture was not created');
    history.fixture_pid = fixture.pid;
    const fixtureDone = new Promise((resolve, reject) => {
      fixture.once('error', reject); fixture.once('exit', (code, signal) => {
        history.fixture_exit = {code, signal};
        if (code !== 0 || signal) reject(new Error('Owned fixture failed: ' + JSON.stringify(history.fixture_exit)));
        else resolve();
      });
    });
    await Promise.race([fixtureDone, failure, browserDone.then(() => { throw new Error('Browser exited before fixture'); })]);
    requireFact(history.connected, 'fixture never used original CDP capability');
    browser.stdio[3].write(JSON.stringify({id: 2147483647, method: 'Browser.close'}) + '\0');
    await Promise.race([browserDone, failure]);
    history.status = 'TRANSPORT_COMPLETE';
  } finally {
    close();
    // Root never assumes cleanup or sends signals: the existing ProcessTree
    // retains all descendants and its unchanged deadline/cleanup authority.
    fs.writeFileSync(control.record, JSON.stringify(history, null, 2) + '\n', {flag: 'wx', mode: 0o400});
  }
}
if (require.main === module) main().catch(error => {
  process.stderr.write(String(error.stack || error) + '\n'); process.exit(2);
});
module.exports = {BROWSER_EXEC, MACH};
