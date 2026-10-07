'use strict';

// Test-only benchmark adapter. These existing behaviors must remain passing.
const test = require('node:test');
const assert = require('node:assert/strict');
const {diagnose} = require(process.env.ARENA_TYPESCRIPT_HELPER);
const union = `type Msg = {kind: 'n'; value: number} | {kind: 's'; value: string};`;

test('test_guard_ordinary_object_narrowing', () => {
  const diagnostics = diagnose(union + `
    function f(input: Msg) {
      if (input.kind === 'n') {
        const n: number = input.value;
        input.value.toFixed();
      } else {
        const s: string = input.value;
        input.value.toUpperCase();
      }
    }
  `);
  assert.deepEqual(diagnostics, []);
});

test('test_guard_wrong_payload_method', () => {
  const diagnostics = diagnose(union + `
    function f(input: Msg) {
      if (input.kind === 'n') input.value.toUpperCase();
    }
  `);
  assert.deepEqual(diagnostics.map(d => d.code), [2339]);
  assert.match(diagnostics[0].message, /toUpperCase/);
  assert.match(diagnostics[0].message, /number/);
});

test('test_guard_mutable_bindings', () => {
  const diagnostics = diagnose(union + `
    function f(input: Msg) {
      let {kind, value} = input;
      if (kind === 'n') value.toFixed();
    }
  `);
  assert.deepEqual(diagnostics.map(d => d.code), [2339]);
  assert.match(diagnostics[0].message, /toFixed/);
  assert.match(diagnostics[0].message, /string/);
});
