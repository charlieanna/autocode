const assert = require('node:assert/strict');
const {test} = require('node:test');
const {reviewedChoice} = require('./requirements-standing-confirmation.cjs');

test('only the reviewed planning confirmation can be accepted', () => {
  const expected = {title: 'Prepare a reviewed plan?', detail: 'Use the saved limits.', label: 'Prepare plan'};
  const options = {message: expected.title, detail: expected.detail, buttons: ['Cancel', '&Prepare plan']};
  assert.equal(reviewedChoice(options, expected), 1);
  for (const changed of [
    {...options, message: 'Other task?'}, {...options, detail: 'Different limits.'},
    {...options, buttons: ['Cancel', 'Other']}, {...options, buttons: ['Prepare plan', 'Prepare plan']},
    {...options, buttons: ['Prepare plan', 'Other']},
  ]) assert.throws(() => reviewedChoice(changed, expected), /Unexpected confirmation/);
});
