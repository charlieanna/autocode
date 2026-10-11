const test = require('node:test');
const assert = require('node:assert/strict');
const {add} = require('../src/calculator.js');
test('old_add', () => assert.equal(add(2, 3), 5));
