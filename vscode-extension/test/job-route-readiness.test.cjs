const assert = require('node:assert/strict');
const { test } = require('node:test');
const { routeDecisionReady } = require('./job-route-readiness.cjs');

const progress = 'Review the model and reasoning level for the Design Reviewer, then retry it.';
const reason = "The Design Reviewer's saved model settings changed. Review the model and reasoning level before retrying.";

test('route host waits for the visible field and both current explanations', () => {
  assert.equal(routeDecisionReady({ visible: true, text: 'Opening this chat…' }), false);
  assert.equal(routeDecisionReady({ visible: false, text: `${progress} ${reason}` }), false);
  assert.equal(routeDecisionReady({ visible: true, text: progress }), false);
  assert.equal(routeDecisionReady({ visible: true, text: reason }), false);
  assert.equal(routeDecisionReady({ visible: true, text: `${progress} ${reason}` }), true);
});
