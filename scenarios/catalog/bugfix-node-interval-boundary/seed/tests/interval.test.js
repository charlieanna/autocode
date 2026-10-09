'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { overlaps } = require('../src/interval.js');

test('test_base_positive_intersection_and_containment', () => {
  assert.equal(overlaps([1, 4], [3, 6]), true);
  assert.equal(overlaps([-5, 5], [-2, 2]), true);
  assert.equal(overlaps([1, 4], [1, 4]), true);
});

test('test_base_separated_intervals_and_preserved_inputs', () => {
  const left = Object.freeze([-3, -1]);
  const right = Object.freeze([1, 2]);
  assert.equal(overlaps(left, right), false);
  assert.equal(overlaps(right, left), false);
  assert.deepEqual(left, [-3, -1]);
  assert.deepEqual(right, [1, 2]);
});

test('test_base_invalid_intervals_raise_type_error', () => {
  for (const bad of [null, [1], [1, 2, 3], [2, 1], [0, Infinity], [NaN, 2], ['1', 2], new Array(2)]) {
    assert.throws(() => overlaps(bad, [0, 3]), TypeError);
    assert.throws(() => overlaps([0, 3], bad), TypeError);
  }
});
