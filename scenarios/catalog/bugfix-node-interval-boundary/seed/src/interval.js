'use strict';

function validate(interval) {
  if (!Array.isArray(interval) || interval.length !== 2 ||
      !Number.isFinite(interval[0]) || !Number.isFinite(interval[1]) || interval[0] > interval[1]) {
    throw new TypeError('interval must be a finite ordered [start, end] pair');
  }
}

function overlaps(left, right) {
  validate(left);
  validate(right);
  return left[0] <= right[1] && right[0] <= left[1];
}

module.exports = { overlaps };
