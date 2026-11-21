'use strict';

const assert = require('node:assert/strict');
const { test } = require('node:test');
const { openHistoryRow } = require('./host/open-history.cjs');

function rowWith(outcomes) {
  const calls = [];
  return {
    calls,
    async isVisible() { return false; },
    async waitFor() {
      calls.push('wait');
      if (!outcomes.shift()) {
        const error = new Error('row did not appear');
        error.name = 'TimeoutError';
        throw error;
      }
    },
    async click() { calls.push('open'); },
  };
}

test('a detached chat is reacquired only if its Runs row did not appear', async () => {
  const row = rowWith([false, true]);
  let frames = 0;
  await openHistoryRow(row, async () => ({
    async dispatchEvent(selector, type) {
      assert.equal(selector, '#history');
      assert.equal(type, 'click');
      if (++frames === 1) throw new Error('Frame was detached');
    },
  }), 'fixture');
  assert.equal(frames, 2);
  assert.deepEqual(row.calls, ['wait', 'wait', 'open']);
});

test('a detached chat that already opened Runs does not press History again', async () => {
  const row = rowWith([true]);
  let frames = 0;
  await openHistoryRow(row, async () => ({
    async dispatchEvent() { frames++; throw new Error('Frame was detached'); },
  }), 'fixture');
  assert.equal(frames, 1);
  assert.deepEqual(row.calls, ['wait', 'open']);
});

test('unrelated chat errors remain errors and do not open a row', async () => {
  const row = rowWith([]);
  await assert.rejects(
    openHistoryRow(row, async () => ({ async dispatchEvent() { throw new Error('permission denied'); } }), 'fixture'),
    /permission denied/,
  );
  assert.deepEqual(row.calls, []);
});

test('missing Runs rows fail after bounded attempts', async () => {
  const row = rowWith([false, false, false]);
  let frames = 0;
  await assert.rejects(
    openHistoryRow(row, async () => ({ async dispatchEvent() { frames++; } }), 'fixture'),
    /Runs row for fixture did not appear/,
  );
  assert.equal(frames, 3);
  assert.deepEqual(row.calls, ['wait', 'wait', 'wait']);
});
