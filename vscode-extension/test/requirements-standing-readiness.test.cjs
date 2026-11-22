const assert = require('node:assert/strict');
const {test} = require('node:test');
const {conventionsVisible} = require('./requirements-standing-readiness.cjs');

test('visible Requirements conventions use DOM text despite uppercase display and line wrapping', async () => {
  const expected = [{text: 'exception convention'}, {text: 'JSON convention'}];
  const rows = new Map(expected.map(({text}) => [text, {
    visible: true, raw: 'reported convention, not independently verified or approved',
  }]));
  const frame = {
    locator: () => ({filter: ({hasText}) => ({
      count: async () => Number(rows.has(hasText)),
      isVisible: async () => rows.get(hasText).visible,
      textContent: async () => rows.get(hasText).raw,
      innerText: async () => rows.get(hasText).raw.toUpperCase().replace(' VERIFIED', '\nVERIFIED'),
    })}),
    $: async () => ({isVisible: async () => true}),
  };
  const oldLabel = await frame.locator('.assumption-row').filter({hasText: expected[0].text}).innerText();
  assert.equal(oldLabel.includes('reported convention, not independently verified or approved'), false,
    'the prior rendered-text predicate fails on the observed casing');
  assert.equal(await conventionsVisible(frame, expected), true);
  rows.get(expected[1].text).visible = false;
  assert.equal(await conventionsVisible(frame, expected), false);
  rows.get(expected[1].text).visible = true;
  rows.get(expected[1].text).raw = 'confirmed';
  assert.equal(await conventionsVisible(frame, expected), false);
  rows.delete(expected[1].text);
  assert.equal(await conventionsVisible(frame, expected), false);
});
