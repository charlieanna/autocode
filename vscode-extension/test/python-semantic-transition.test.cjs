const assert = require('node:assert/strict');
const { test } = require('node:test');
const { settledAfterApproval } = require('./python-semantic-transition.cjs');

test('only the exact previously reviewed approval remains a benign pre-action view', () => {
  const plan = { token: 'r2:plan', revision: 2, review_markdown: 'reviewed' };
  const old = { status: 'WAITING_FOR_USER', needs: { kind: 'approve_plan', token: plan.token }, displayed_plan: plan };
  assert.equal(settledAfterApproval(old, old), false);
  assert.equal(settledAfterApproval(old, old, true), true, 'an approval that returns after the transition is a new stop');
  assert.equal(settledAfterApproval({ ...old, needs: { ...old.needs, token: 'r3:new' } }, old), true);
  assert.equal(settledAfterApproval({ ...old, displayed_plan: { ...plan, review_markdown: 'changed' } }, old), true);
  assert.equal(settledAfterApproval({ ...old, status: 'PAUSED_RETRY', stop_reason: 'blocked' }, old), true);
  assert.equal(settledAfterApproval({ status: 'PAUSED_RETRY', needs: { kind: 'resume' }, next_command: 'resume' }, old), true);
  assert.equal(settledAfterApproval({ status: 'WAITING_FOR_USER', needs: { kind: 'answer' } }, old), true);
  assert.equal(settledAfterApproval({ status: 'RUNNING', needs: { kind: 'continue' } }, old), false);
  assert.equal(settledAfterApproval({ status: 'TASK_COMPLETE', done: true }, old), true);
});
