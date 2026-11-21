const assert = require('node:assert/strict');
const { test } = require('node:test');
const { afterApproval, settledAfterApproval } = require('./python-semantic-transition.cjs');

test('only the exact previously reviewed approval remains a benign pre-action view', () => {
  const plan = { token: 'r2:plan', revision: 2, review_markdown: 'reviewed' };
  const old = { status: 'WAITING_FOR_USER', needs: { kind: 'approve_plan', token: plan.token }, displayed_plan: plan };
  assert.equal(settledAfterApproval(old, old), false);
  const pending = { status: old.status, needs: null, stop_reason: null, next_command: null, progress: { continuing: true } };
  for (const status of [old.status, 'PAUSED_RETRY', 'RUNNING']) {
    assert.equal(settledAfterApproval({ ...pending, status }, old, false), false, `${status}: controller is saving a transition`);
    assert.equal(settledAfterApproval({ ...pending, status }, old, true), false, `${status}: controller remains active after transition`);
  }
  assert.equal(settledAfterApproval({ ...pending, progress: { continuing: false } }, old, false), true);
  assert.equal(settledAfterApproval({ ...pending, displayed_plan: plan }, old, false), false);
  assert.equal(settledAfterApproval({ ...pending, stop_reason: 'old saved stop' }, old, false), false);
  assert.equal(settledAfterApproval({ ...pending, next_command: ['resume'] }, old, false), true);
  assert.equal(settledAfterApproval({ ...pending, pause: { next_command: ['pause'] } }, old, false), false);
  assert.equal(settledAfterApproval({ ...pending, pause: { stop_now: { next_command: ['stop'] } } }, old, false), false);
  assert.equal(settledAfterApproval({ ...pending, needs: { kind: 'answer' } }, old, false), true);
  assert.equal(settledAfterApproval({ ...pending, needs: { kind: 'approve_plan', token: 'new' }, displayed_plan: { ...plan, token: 'new' } }, old, false), true);
  assert.equal(settledAfterApproval(old, old, true), true, 'an approval that returns after the transition is a new stop');
  assert.equal(settledAfterApproval({ ...old, needs: { ...old.needs, token: 'r3:new' } }, old), true);
  assert.equal(settledAfterApproval({ ...old, displayed_plan: { ...plan, review_markdown: 'changed' } }, old), true);
  assert.equal(settledAfterApproval({ ...old, status: 'PAUSED_RETRY', stop_reason: 'blocked' }, old), true);
  assert.equal(settledAfterApproval({ status: 'PAUSED_RETRY', needs: { kind: 'resume' }, next_command: 'resume' }, old), true);
  assert.equal(settledAfterApproval({ status: 'WAITING_FOR_USER', needs: { kind: 'answer' } }, old), true);
  assert.equal(settledAfterApproval({ status: 'RUNNING', needs: { kind: 'continue' } }, old), false);
  assert.equal(settledAfterApproval({ status: 'TASK_COMPLETE', done: true }, old), true);
});

test('a continuing controller establishes a transition even if the old approval returns', () => {
  const old = { status: 'AWAITING_GOAL_APPROVAL', needs: { kind: 'approve_plan', token: 'r2:plan' },
    displayed_plan: { token: 'r2:plan', review_markdown: 'reviewed' } };
  const continuing = { status: old.status, needs: null, next_command: null, progress: { continuing: true } };
  const returned = afterApproval(old);
  assert.equal(returned(old), false);
  assert.equal(returned(continuing), false);
  assert.equal(returned(old), true, 'a returned approval after active work is a new stop');

  const completes = afterApproval(old);
  assert.equal(completes(old), false);
  assert.equal(completes(continuing), false);
  assert.equal(completes({ status: 'RUNNING', needs: { kind: 'continue' } }), false);
  assert.equal(completes({ status: 'TASK_COMPLETE', done: true }), true);
});
