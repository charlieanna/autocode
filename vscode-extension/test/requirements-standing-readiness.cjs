// Match the actual DOM text, not CSS text-transform or wrapping in innerText.
async function conventionsVisible(frame, expected) {
  for (const item of expected) {
    const row = frame.locator('.assumption-row').filter({hasText: item.text});
    if (!await row.count() || !await row.isVisible() ||
      !(await row.textContent()).includes('reported convention, not independently verified or approved')) return false;
  }
  const action = await frame.$('#take-action');
  return Boolean(action && await action.isVisible());
}

module.exports = {conventionsVisible};
