'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { byId, sortByScore } = require('../src/records.js');

test('test_hidden_numeric_order_and_stable_tie_groups', () => {
  const records = Object.freeze([
    Object.freeze({ id: 'a', score: 10 }), Object.freeze({ id: 'b', score: -2.5 }),
    Object.freeze({ id: 'c', score: 2 }), Object.freeze({ id: 'd', score: 10 }),
    Object.freeze({ id: 'e', score: -2.5 }), Object.freeze({ id: 'f', score: 2 }),
  ]);
  const ascending = sortByScore(records);
  const descending = sortByScore(records, 'desc');
  assert.deepEqual(ascending.map((row) => row.id), ['b', 'e', 'c', 'f', 'a', 'd']);
  assert.deepEqual(descending.map((row) => row.id), ['a', 'd', 'c', 'f', 'b', 'e']);
  assert.notEqual(ascending, records);
  assert.equal(ascending[0], records[1]);
  assert.deepEqual(records.map((row) => row.id), ['a', 'b', 'c', 'd', 'e', 'f']);
  assert.deepEqual(byId(records).map((row) => row.id), ['a', 'b', 'c', 'd', 'e', 'f']);
});

test('test_hidden_validation_singleton_and_empty_contracts', () => {
  const row = Object.freeze({ id: 'only', score: -0 });
  const singleton = Object.freeze([row]);
  assert.deepEqual(sortByScore(singleton), [row]);
  assert.equal(sortByScore(singleton)[0], row);
  const empty = Object.freeze([]);
  assert.deepEqual(sortByScore(empty, 'desc'), []);
  assert.notEqual(sortByScore(empty), empty);
  for (const bad of [undefined, 'rows', [false], new Array(1), [{ id: 'x' }], [{ id: 'x', score: NaN }], [{ id: 'x', score: -Infinity }]]) {
    assert.throws(() => sortByScore(bad), TypeError);
  }
  for (const direction of ['', null, false, 'ASC']) {
    assert.throws(() => sortByScore([], direction), RangeError);
  }
});
