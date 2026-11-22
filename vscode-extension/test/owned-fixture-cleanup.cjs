const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

function privateHostRoot(prefix) {
  // macOS uses a long per-user temp path. Electron's socket and the warm CLI
  // socket both need to fit in its 103-byte Unix socket path limit.
  const base = os.platform() === 'darwin' ? '/private/tmp' : os.tmpdir();
  return fs.realpathSync(fs.mkdtempSync(path.join(base, prefix)));
}

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

module.exports = { privateHostRoot, privateHostEnvironment, ownedWarmHostSteps, settleFixture };
