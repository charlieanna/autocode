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
