/* Trusted Linux branch command builder. Never loads the application/fixture. */
'use strict';
const fs = require('node:fs');
const {createHash} = require('node:crypto');
const requireFact = (value, reason) => { if (!value) throw new Error(`Native Linux capture: ${reason}`); };
const TRANSPORT = 'linux-bwrap-inherited-cdp-v1';
function command(policy, expectedHash, executable, args, branch) {
  const bytes = fs.readFileSync(policy);
  requireFact(createHash('sha256').update(bytes).digest('hex') === expectedHash, 'original policy changed');
  const plan = JSON.parse(bytes.toString('utf8'));
  requireFact(Object.keys(plan).sort().join(',') === 'arguments,filter,fixture_filter,kernel,kind,listener_fd,trampoline,transport,version' &&
    plan.version === 'native-linux-bwrap-v1' && plan.kind === 'native-capture' && plan.transport === TRANSPORT,
    'unsupported original policy');
  requireFact(branch === 'browser' || branch === 'fixture', 'unknown owned capture branch');
  const filter = branch === 'browser' ? plan.filter : plan.fixture_filter;
  requireFact(Object.keys(filter).sort().join(',') === 'fd,path,sha256' &&
    Number.isInteger(filter.fd) && filter.fd >= 5 && filter.fd <= 9 && filter.fd === plan.filter.fd,
    'invalid private filter slot');
  requireFact(createHash('sha256').update(fs.readFileSync(filter.path)).digest('hex') === filter.sha256,
    'original syscall filter changed');
  requireFact(Array.isArray(plan.arguments) && plan.arguments.every(v => typeof v === 'string') &&
    typeof executable === 'string' && Array.isArray(args) && args.every(v => typeof v === 'string'),
    'invalid branch arguments');
  const script = `filter=$1; shift; exec "$@" ${filter.fd}< "$filter"`;
  return [plan.trampoline, ['-c', script, 'native-owned-capture', filter.path,
    plan.kernel, ...plan.arguments, '--', executable, ...args]];
}
module.exports = {command, TRANSPORT};
