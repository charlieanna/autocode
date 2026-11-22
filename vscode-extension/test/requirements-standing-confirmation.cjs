// The host fixture accepts only the spend confirmation it has just reviewed.
function reviewedChoice(options, expected) {
  if (!options || options.message !== expected.title || options.detail !== expected.detail || !Array.isArray(options.buttons)) {
    throw new Error('Unexpected confirmation in Requirements standing fixture');
  }
  const buttons = options.buttons.map((button) => typeof button === 'string' ? button.replace(/&/g, '') : '');
  const matches = buttons.flatMap((button, index) => button === expected.label ? [index] : []);
  if (matches.length !== 1 || buttons.some((button) => button !== expected.label && button !== 'Cancel')) {
    throw new Error('Unexpected confirmation choices in Requirements standing fixture');
  }
  return matches[0];
}

// This function and reviewedChoice are passed to Playwright's Electron
// evaluator as trusted source; neither depends on a CommonJS global there.
function installReviewedDialog(dialog, input, state) {
  const choice = Function(`return (${input.choiceSource})`)();
  state.__standingConfirmationCount = 0;
  dialog.showMessageBox = async (...args) => {
    const options = args.length === 1 ? args[0] : args[1];
    const response = choice(options, input.expected);
    state.__standingConfirmationCount++;
    return {response, checkboxChecked: false};
  };
}

module.exports = {reviewedChoice, installReviewedDialog};
