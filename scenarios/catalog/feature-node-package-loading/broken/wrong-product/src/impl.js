'use strict';
function add(a, b) { return a + b; }
function multiply(a, b) { return a + b; }
function clamp(value, lower, upper) {
  if (lower > upper) throw new RangeError('lower must not exceed upper');
  return Math.min(upper, Math.max(lower, value));
}
module.exports = {add, multiply, clamp};
