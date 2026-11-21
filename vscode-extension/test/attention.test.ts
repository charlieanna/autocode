import { strict as assert } from "node:assert";
import { test } from "node:test";
import { waitsForPerson, transitionNotice } from "../src/notify";
import { statusLine } from "../src/statusBar";
import type { StatusView } from "../src/core/view";

test("operational waits can notify without claiming a person is needed", () => {
  for (const kind of ["continue", "dependency"]) {
    const view = { status: "WAITING", done: false, needs: { kind },
      progress: { line: "Nothing needed from you" } } as StatusView;
    assert.equal(waitsForPerson(view), false);
    assert.ok(transitionNotice(null, view));
    assert.doesNotMatch(statusLine(view).tooltip, /Waiting for you/);
  }
});

test("a blocked recovery is not waiting for a person to resume", () => {
  const view = { status: "PAUSED_BUILDER_RETRY_LIMIT", done: false, needs: { kind: "resume" },
    next_command: null, recovery: { category: "blocked", what_happened: "The retry is unavailable." },
    progress: { headline: "Task blocked", line: "Task blocked · nothing needed from you" } } as StatusView;
  assert.equal(waitsForPerson(view), false);
  const notice = transitionNotice(null, view);
  assert.equal(notice?.outcome, "is blocked - The retry is unavailable.");
  assert.doesNotMatch(statusLine(view).tooltip, /Waiting for you|resume/i);
  // A pause with the same kind can change from blocked to an offered action.
  const available = { ...view, recovery: { category: "recovery" }, next_command: "autocode --resume-paused" };
  assert.equal(waitsForPerson(available), true);
  assert.match(transitionNotice(view, available)?.outcome ?? "", /waiting for you/);
});
