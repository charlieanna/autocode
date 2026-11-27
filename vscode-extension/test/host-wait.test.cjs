// The Extension Host suites' shared wait (test/host/wait.cjs, #4215). A webview probe with no
// timeout of its own once held the nightly extension job for six hours; the wait must end at its
// deadline whether or not the probe answers.

const assert = require('node:assert/strict');
const { test } = require('node:test');
const { until } = require('./host/wait.cjs');

const never = () => new Promise(() => {});

test('a probe that never answers ends the wait at its deadline, naming what was awaited', { timeout: 5_000 }, async () => {
  const started = Date.now();
  await assert.rejects(until('a frame that stopped answering', never, 200),
    /Timed out waiting for a frame that stopped answering; last: the probe did not answer within \d+ ms/);
  assert.ok(Date.now() - started < 2_000, 'the wait ended near its deadline');
});

test('a probe that answers only after the deadline does not stretch the wait', { timeout: 5_000 }, async () => {
  const started = Date.now();
  const slow = () => new Promise((resolve) => setTimeout(() => resolve('late'), 3_000).unref());
  await assert.rejects(until('a slow probe', slow, 200), /Timed out waiting for a slow probe/);
  assert.ok(Date.now() - started < 2_000, 'the slow probe was cut at the deadline');
});

test('the wait returns the first truthy answer and retries a probe that threw', { timeout: 5_000 }, async () => {
  let calls = 0;
  const value = await until('the third answer', async () => {
    calls += 1;
    if (calls === 1) throw new Error('not yet');
    return calls === 3 ? { ready: true } : null;
  }, 3_000);
  assert.deepEqual(value, { ready: true });
  assert.equal(calls, 3);
});

test('a falsy answer at the deadline reports the last answer', { timeout: 5_000 }, async () => {
  await assert.rejects(until('a ready flag', () => false, 100), /Timed out waiting for a ready flag; last: false/);
});
