const assert = require('node:assert/strict');
const {test} = require('node:test');
const {runInNewContext} = require('node:vm');
const {reviewedChoice, installReviewedDialog} = require('./requirements-standing-confirmation.cjs');

test('only the reviewed planning confirmation can be accepted', () => {
  const expected = {title: 'Prepare a reviewed plan?', detail: 'Use the saved limits.', label: 'Prepare plan'};
  const options = {message: expected.title, detail: expected.detail, buttons: ['Cancel', '&Prepare plan']};
  assert.equal(reviewedChoice(options, expected), 1);
  const inElectronEvaluator = runInNewContext(`(${reviewedChoice.toString()})`);
  assert.equal(inElectronEvaluator(options, expected), 1, 'the serialized helper has no CommonJS dependency');
  for (const changed of [
    {...options, message: 'Other task?'}, {...options, detail: 'Different limits.'},
    {...options, buttons: ['Cancel', 'Other']}, {...options, buttons: ['Prepare plan', 'Prepare plan']},
    {...options, buttons: ['Prepare plan', 'Other']},
  ]) {
    assert.throws(() => reviewedChoice(changed, expected), /Unexpected confirmation/);
    assert.throws(() => inElectronEvaluator(changed, expected), /Unexpected confirmation/);
  }
});

test('the Electron evaluator installs the same bounded dialog handler without require', async () => {
  const expected = {title: 'Prepare?', detail: 'Saved limits.', label: 'Prepare plan'};
  const install = runInNewContext(`(${installReviewedDialog.toString()})`);
  const state = {}, dialog = {};
  install(dialog, {choiceSource: reviewedChoice.toString(), expected}, state);
  const value = await dialog.showMessageBox({message: expected.title, detail: expected.detail, buttons: ['Cancel', expected.label]});
  assert.equal(value.response, 1);
  assert.equal(state.__standingConfirmationCount, 1);
  await assert.rejects(dialog.showMessageBox({message: 'Other?', detail: expected.detail, buttons: ['Cancel', expected.label]}));
  assert.equal(state.__standingConfirmationCount, 1);
});
