const assert = require('node:assert/strict');
const Module = require('node:module');
const { test } = require('node:test');
const { editor } = require('./helpers/editor.cjs');

test('the extension loads the host API without copying its exports', (t) => {
  let enumerations = 0;
  const api = new Proxy(editor().vscode, {
    ownKeys(target) {
      enumerations++;
      return Reflect.ownKeys(target);
    },
  });
  const load = Module._load;
  t.mock.method(Module, '_load', function (name, ...args) {
    return name === 'vscode' ? api : load.call(this, name, ...args);
  });
  const { activate } = require('../out-test/src/extension.js');
  assert.equal(typeof activate, 'function');
  assert.equal(enumerations, 0, 'importing the extension must not enumerate the shared host API');
});
