const assert = require('node:assert/strict');
const { test } = require('node:test');
const path = require('node:path');
const { privateHostEnvironment, ownedWarmHostSteps, settleFixture } = require('./owned-fixture-cleanup.cjs');

test('owned cleanup attempts every step and retains the fixture on failure', async () => {
  const calls = [];
  const original = new Error('editor assertion');
  await assert.rejects(settleFixture([
    ['close editor', () => { calls.push('close'); throw new Error('close failed'); }],
    ['stop host', () => { calls.push('stop'); throw new Error('stop failed'); }],
    ['check host', () => calls.push('check')],
  ], () => calls.push('remove'), original), (error) => {
    assert.ok(error instanceof AggregateError);
    assert.equal(error.errors.length, 3);
    assert.equal(error.errors[0], original);
    assert.match(error.errors[1].message, /close failed/);
    assert.match(error.errors[2].message, /stop failed/);
    return true;
  });
  assert.deepEqual(calls, ['close', 'stop', 'check']);
});

test('owned cleanup removes the fixture only after every successful step', async () => {
  const calls = [];
  await settleFixture([['close', () => calls.push('close')], ['stop', () => calls.push('stop')]],
    () => calls.push('remove'));
  assert.deepEqual(calls, ['close', 'stop', 'remove']);
});

test('early setup failure cannot stop an inherited host but still closes the editor', async () => {
  const calls = [];
  const error = new Error('fixture setup failed');
  const inherited = { AUTOCODE_HOME: '/an/inherited/operator/registry', HOME: '/home/user', CODEX_HOME: '/codex/user' };
  const env = privateHostEnvironment(inherited, '/disposable/root');
  assert.equal(env.AUTOCODE_HOME, path.join('/disposable/root', 'registry'));
  assert.equal(env.XDG_CONFIG_HOME, path.join('/disposable/root', 'config'));
  assert.equal(env.HOME, inherited.HOME);
  assert.equal(env.CODEX_HOME, inherited.CODEX_HOME);
  assert.equal(inherited.AUTOCODE_HOME, '/an/inherited/operator/registry');
  await assert.rejects(settleFixture([
    ['close editor', () => calls.push('close')],
    ...ownedWarmHostSteps(false, () => calls.push('stop'), () => calls.push('check')),
  ], () => calls.push('remove'), error), AggregateError);
  assert.deepEqual(calls, ['close']);
});

test('claimed warm host is stopped and checked before fixture removal', async () => {
  const calls = [];
  await settleFixture(ownedWarmHostSteps(true, () => calls.push('stop'), () => calls.push('check')),
    () => calls.push('remove'));
  assert.deepEqual(calls, ['stop', 'check', 'remove']);
});
