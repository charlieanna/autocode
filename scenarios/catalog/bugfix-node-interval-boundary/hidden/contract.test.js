'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { overlaps } = require('../src/interval.js');

test('test_hidden_half_open_intersection_grid', () => {
  const points = [-2, -0.25, 0, 0.25, 2];
  const intervals = points.flatMap((start) => points.filter((end) => end >= start).map((end) => [start, end]));
  for (const left of intervals) {
    for (const right of intervals) {
      const expected = Math.max(left[0], right[0]) < Math.min(left[1], right[1]);
      assert.equal(overlaps(left, right), expected, JSON.stringify({ left, right }));
    }
  }
});

test('test_hidden_input_preservation_and_validation', () => {
  const left = Object.freeze([1.25, 2.5]);
  const right = Object.freeze([2.5, 4.75]);
  assert.equal(overlaps(left, right), false);
  assert.deepEqual(left, [1.25, 2.5]);
  assert.deepEqual(right, [2.5, 4.75]);
  for (const invalid of [{ 0: 1, 1: 2, length: 2 }, [], new Array(2), [0, -Infinity], [undefined, 3], [3, 2]]) {
    assert.throws(() => overlaps(invalid, [0, 1]), TypeError);
  }
});
