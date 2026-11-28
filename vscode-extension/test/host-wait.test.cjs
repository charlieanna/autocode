// The Extension Host suites' shared wait (test/host/wait.cjs, #4215). A webview probe with no
// timeout of its own once held the nightly extension job for six hours; the wait must end at its
// deadline whether or not the probe answers.

const assert = require('node:assert/strict');
const { test } = require('node:test');
const { evaluateIn, frameWith, until } = require('./host/wait.cjs');

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

/** A stand-in for a Playwright page: frames whose ``$`` and ``evaluate`` answer as scripted. */
function page(frames) {
  return { frames: () => frames };
}

test('a frame that stops answering does not hide the frame being looked for', { timeout: 15_000 }, async () => {
  // Nightly 46089863877: the stock host test hung 300 s; every suite asked the frames one after another.
  const stale = { name: 'stale', $: never };
  const chat = { name: 'chat', $: async (selector) => (selector === '#chat-tabs' ? { element: true } : null) };
  const started = Date.now();
  assert.equal(await frameWith(page([stale, chat]), '#chat-tabs', 'the chat', 20_000), chat);
  assert.ok(Date.now() - started < 10_000, 'found within one frame answer time, not at the deadline');
});

test('a frame that holds nothing is passed over and the wait names what it looked for', { timeout: 5_000 }, async () => {
  const empty = { $: async () => null };
  await assert.rejects(frameWith(page([empty]), '#chat-tabs', 'the chat webview', 300),
    /Timed out waiting for the chat webview/);
});

test('a read in a frame that stops answering finds the frame again and returns a falsy answer', { timeout: 15_000 }, async () => {
  const hung = { evaluate: never };
  const fresh = { evaluate: async (read) => read() };
  const found = [hung, fresh];
  let finds = 0;
  const find = async () => found[Math.min(finds++, found.length - 1)];
  const started = Date.now();
  assert.equal(await evaluateIn(find, 'the chat error', () => '', 20_000), '');
  assert.equal(finds, 2, 'the frame was found again after the first did not answer');
  assert.ok(Date.now() - started < 10_000, 'the hung read was cut after one frame answer time');
});
