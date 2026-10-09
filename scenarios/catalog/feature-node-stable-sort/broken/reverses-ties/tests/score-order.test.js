'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { sortByScore } = require('../src/records.js');

test('test_c1_numeric_score_order_in_both_directions', () => {
  const records = [{ id: 'ten', score: 10 }, { id: 'two', score: 2 }, { id: 'negative', score: -4 }, { id: 'half', score: 0.5 }];
  assert.deepEqual(sortByScore(records).map((row) => row.id), ['negative', 'half', 'two', 'ten']);
  assert.deepEqual(sortByScore(records, 'desc').map((row) => row.id), ['ten', 'two', 'half', 'negative']);
  for (const bad of [null, {}, [null], [{ id: 7, score: 1 }], [{ id: 'x', score: '2' }], [{ id: 'x', score: Infinity }]]) {
    assert.throws(() => sortByScore(bad), TypeError);
  }
  assert.throws(() => sortByScore(records, 'sideways'), RangeError);
});

test('test_c2_stable_ties_and_caller_data_preserved', () => {
  const records = Object.freeze([
    Object.freeze({ id: 'first', score: 3 }), Object.freeze({ id: 'low', score: 1 }),
    Object.freeze({ id: 'second', score: 3 }), Object.freeze({ id: 'third', score: 3 }),
  ]);
  assert.deepEqual(sortByScore(records).map((row) => row.id), ['low', 'first', 'second', 'third']);
  const descending = sortByScore(records, 'desc');
  assert.deepEqual(descending.map((row) => row.id), ['first', 'second', 'third', 'low']);
  assert.equal(descending[0], records[0]);
  assert.equal(descending[1], records[2]);
  assert.deepEqual(records.map((row) => row.id), ['first', 'low', 'second', 'third']);
  const empty = [];
  assert.deepEqual(sortByScore(empty), []);
  assert.notEqual(sortByScore(empty), empty);
});
