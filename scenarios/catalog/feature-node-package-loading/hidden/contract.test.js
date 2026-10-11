const test = require('node:test');
const assert = require('node:assert/strict');
const api = require('../src/calculator.js');
test('hidden_product_loading_and_behavior', () => {
  assert.equal(api, require('#math'));
  assert.equal(api, require('mapped-calculator/impl'));
  assert.equal(api, require('mapped-calculator'));
  assert.equal(api.add(-2, 7), 5);
  assert.equal(api.multiply(-2.5, 4), -10);
  assert.equal(api.multiply(0, 12), 0);
  assert.equal(api.clamp(-4, -3, 5), -3);
  assert.equal(api.clamp(2.5, -3, 5), 2.5);
  assert.equal(api.clamp(9, -3, 5), 5);
  assert.equal(api.clamp(0, 2, 2), 2);
  assert.throws(() => api.clamp(0, 2, -2), RangeError);
});
