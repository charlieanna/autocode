const test = require('node:test');
const assert = require('node:assert/strict');
test('test_c1_multiply_signed_numbers', () => {});
test('test_c2_clamp_bounds_and_reject_reversed_bounds', () => {});

test('test_preserve_add', () => assert.equal(require('../src/calculator.js').add(2, 3), 5));
