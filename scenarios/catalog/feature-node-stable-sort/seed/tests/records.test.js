'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { byId } = require('../src/records.js');

test('test_base_identifier_order_and_input_preservation', () => {
  const records = Object.freeze([{ id: 'z', score: 1 }, { id: 'a', score: 9 }, { id: 'm', score: -3 }]);
  const result = byId(records);
  assert.deepEqual(result.map((row) => row.id), ['a', 'm', 'z']);
  assert.deepEqual(records.map((row) => row.id), ['z', 'a', 'm']);
  assert.notEqual(result, records);
  assert.equal(result[0], records[1]);
});

test('test_base_equal_identifiers_keep_original_order', () => {
  const records = [{ id: 'x', score: 3 }, { id: 'x', score: 1 }];
  assert.deepEqual(byId(records), records);
  assert.deepEqual(byId([]), []);
});
