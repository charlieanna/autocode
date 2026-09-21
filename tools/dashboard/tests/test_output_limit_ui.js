// A saved output-limit cause is visible in recovery, with the same controls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../dashboard_app.js'), 'utf8');
const context = vm.createContext({});
vm.runInContext(source.slice(source.indexOf('function statusInfo('), source.indexOf('function badge(')), context);
const reason = 'OpenCode exhausted its output token limit (finish reason: length). The attempt is incomplete; review saved work before recovery.';
for (const status of ['PAUSED_UNCERTAIN_STAGE', 'PAUSED_PROVIDER_UNCERTAIN']) {
  const run = {status, stop_reason: reason, questions: [{id: 'old', question: 'Old question'}]};
  const info = context.statusInfo(run);
  assert.equal(info.reason, reason);
  assert.equal(info.group, 'stopped');
  assert.equal(info.label, 'Interrupted');
  assert.equal(info.action, 'Review interruption');
  assert.match(context.statusInfo({status}).reason, /ended without a confirmed result/);
}
// Terminal completion precedence is unchanged by a historical pause reason.
assert.equal(context.statusInfo({status: 'TASK_COMPLETE', stop_reason: reason}).label, 'Completed');
console.log('Output-limit recovery cause and generic fallback passed.');
