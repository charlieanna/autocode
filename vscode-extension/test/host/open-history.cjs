'use strict';

/** Open the Runs row even if the chat frame is replaced while History is pressed. */
async function openHistoryRow(row, getChat, label) {
  for (let attempt = 0; attempt < 3; attempt++) {
    if (await row.isVisible()) {
      await row.click();
      return;
    }
    const frame = await getChat();
    try {
      await frame.dispatchEvent('#history', 'click', {}, { timeout: 5_000 });
    } catch (error) {
      if (!/frame (?:was )?detached/i.test(String(error))) throw error;
    }
    try {
      await row.waitFor({ state: 'visible', timeout: 5_000 });
    } catch (error) {
      if (error?.name !== 'TimeoutError') throw error;
      continue;
    }
    await row.click();
    return;
  }
  throw new Error(`The Runs row for ${label} did not appear after opening History`);
}

module.exports = { openHistoryRow };
