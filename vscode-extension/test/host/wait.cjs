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

// How long one frame may take to answer one question while the others are asked (#4215). A webview
// frame VS Code is replacing can stop answering without being detached; asked one after another,
// such a frame held every probe until the deadline, though the frame being looked for answered.
const FRAME_ANSWER_MS = 5_000;

/**
 * The first answer ``read(frame)`` gives that is truthy, from any of ``page``'s frames: every frame
 * is asked at once and each for at most FRAME_ANSWER_MS, so one that does not answer is passed over.
 * Polls like ``until``; returns ``{ frame, value }``.
 */
function answerFrom(page, what, read, timeout = 60_000) {
  return until(what, async () => {
    const frames = page.frames();
    const answers = await Promise.all(
      frames.map((frame) => bounded(() => read(frame), FRAME_ANSWER_MS).catch(() => null)),
    );
    const index = answers.findIndex(Boolean);
    return index < 0 ? null : { frame: frames[index], value: answers[index] };
  }, timeout);
}

/** The frame of ``page`` that holds ``selector`` (a webview: the chat, a Run details panel). */
async function frameWith(page, selector, what = `a webview with ${selector}`, timeout = 60_000) {
  return (await answerFrom(page, what, (frame) => frame.$(selector), timeout)).frame;
}

/**
 * ``read`` evaluated in the frame ``find()`` returns. A direct ``frame.evaluate`` has no timeout of its
 * own, so each try gets FRAME_ANSWER_MS, and a try that fails or does not answer finds the frame again.
 * ``read`` may return any value, falsy ones included.
 */
async function evaluateIn(find, what, read, timeout = 60_000) {
  const attempt = async () => [await (await find()).evaluate(read)];
  const [value] = await until(what, () => bounded(attempt, FRAME_ANSWER_MS), timeout);
  return value;
}

module.exports = { answerFrom, evaluateIn, frameWith, sleep, until };
