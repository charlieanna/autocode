const test = require('node:test');
const assert = require('node:assert/strict');
const {multiply, clamp} = require('../src/calculator.js');
test('test_c1_multiply_signed_numbers', () => {
  assert.equal(multiply(3, 4), 12);
  assert.equal(multiply(-2, 5), -10);
});
test('test_c2_clamp_bounds_and_reject_reversed_bounds', () => {
  assert.equal(clamp(12, 0, 10), 10);
  assert.equal(clamp(-2, 0, 10), 0);
  assert.equal(clamp(4, 0, 10), 4);
  assert.throws(() => clamp(1, 10, 0), RangeError);
});
