const path = require('node:path');

function privateHostEnvironment(inherited, root) {
  return { ...inherited, AUTOCODE_HOME: path.join(root, 'registry'), XDG_CONFIG_HOME: path.join(root, 'config') };
}

function ownedWarmHostSteps(claimed, stop, check) {
  const owned = (action) => () => {
    if (!claimed) return;
    return action();
  };
  return [['stop warm host', owned(stop)], ['check warm host', owned(check)]];
}

// Attempt every owned cleanup before removing the fixture, retaining all errors.
async function settleFixture(steps, remove, testError) {
  const errors = testError ? [testError] : [];
  for (const [label, action] of steps) {
    try { await action(); } catch (error) { errors.push(new Error(`${label}: ${error.message}`, { cause: error })); }
  }
  if (!errors.length) {
    try { await remove(); } catch (error) { errors.push(new Error(`remove fixture: ${error.message}`, { cause: error })); }
  }
  if (errors.length) throw new AggregateError(errors, 'Editor test or owned cleanup failed');
}

module.exports = { privateHostEnvironment, ownedWarmHostSteps, settleFixture };
