// The one wait the Extension Host suites share (#4215).
//
// Each suite had its own copy of `until`, and every copy read its deadline only after a probe
// returned. A probe is usually a Playwright call into a webview frame (`frame.evaluate`,
// `frame.$`) that has no timeout of its own, so a frame that stopped answering kept the wait, the
// test and the CI job open until GitHub cancelled the job six hours later, with no screen kept.
// Here each probe gets only the time left before the deadline: a probe that does not answer in
// time ends the wait with an error that names what was awaited.

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** ``probe()``, or an error once ``ms`` pass without an answer. The probe itself is left to settle. */
function bounded(probe, ms) {
  let timer;
  const late = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`the probe did not answer within ${ms} ms`)), ms);
  });
  return Promise.race([Promise.resolve().then(probe), late]).finally(() => clearTimeout(timer));
}

/** Poll ``probe`` until it returns something truthy, for at most ``timeout`` milliseconds in all. */
async function until(what, probe, timeout = 60_000) {
  const deadline = Date.now() + timeout;
  let last;
  for (;;) {
    try {
      last = await bounded(probe, Math.max(deadline - Date.now(), 1));
      if (last) return last;
    } catch (error) {
      last = error;
    }
    if (Date.now() >= deadline) {
      throw new Error(`Timed out waiting for ${what}; last: ${last instanceof Error ? last.message : JSON.stringify(last)}`);
    }
    await sleep(Math.min(250, Math.max(deadline - Date.now(), 0)));
  }
}

module.exports = { sleep, until };
