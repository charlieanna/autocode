'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { overlaps } = require('../src/interval.js');

test('test_c1_touching_intervals_are_disjoint', () => {
  for (const [left, right] of [[[1, 2], [2, 3]], [[-2, 0], [0, 4]], [[0.5, 1.25], [1.25, 1.5]]]) {
    assert.equal(overlaps(left, right), false);
    assert.equal(overlaps(right, left), false);
  }
  assert.equal(overlaps([1, 2.01], [2, 3]), true);
});

test('test_c2_empty_interval_never_overlaps', () => {
  for (const empty of [[2, 2], [-0.5, -0.5], [0, 0]]) {
    const enclosing = [empty[0] - 1, empty[1] + 1];
    assert.equal(overlaps(empty, enclosing), false);
    assert.equal(overlaps(enclosing, empty), false);
    assert.equal(overlaps(empty, empty), false);
  }
});
