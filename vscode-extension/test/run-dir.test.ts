import assert from "node:assert/strict";
import test from "node:test";

import { freshRunDir } from "../src/core/run-dir";

function inZone<T>(zone: string, body: () => T): T {
  const saved = process.env.TZ;
  process.env.TZ = zone;
  try {
    return body();
  } finally {
    if (saved === undefined) delete process.env.TZ;
    else process.env.TZ = saved;
  }
}

test("a new chat's run folder carries the local date and time, not the UTC date", () => {
  // 23:30 on 11 November in California is already 12 November in UTC.
  const name = inZone("America/Los_Angeles", () =>
    freshRunDir("/ws", new Date(Date.parse("2026-11-12T07:30:05Z")), () => 0.5),
  );
  assert.equal(name, "/ws/.autocode/runs/20261111-233005-vscode-800000");
});

test("the run folder keeps AutoCode's own shape, which `runs` reads back as a time", () => {
  const name = inZone("Asia/Kolkata", () => freshRunDir("/ws", new Date(Date.parse("2026-01-02T03:04:05Z"))));
  assert.match(name, /^\/ws\/\.autocode\/runs\/20260102-083405-vscode-[0-9a-f]{6}$/);
});
