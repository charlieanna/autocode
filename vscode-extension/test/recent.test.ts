import assert from "node:assert/strict";
import test from "node:test";

import { byLatest, recentRuns, relativeTime } from "../src/core/recent";
import type { RegistryRun } from "../src/core/view";

const NOW = Date.parse("2026-11-11T12:00:00Z");

function run(name: string, updatedAt: string | null, extra: Partial<RegistryRun> = {}): RegistryRun {
  return {
    id: name, workspace: "/ws", runDir: `/ws/.autocode/runs/${name}`, taskId: null,
    availability: "available", updatedAt, ...extra,
  };
}

test("chats sort by last activity, not by directory name or project", () => {
  const runs = [
    run("20261101-old-dir-but-used-today", "2026-11-11T11:00:00Z"),
    run("20261110-new-dir-idle", "2026-11-10T09:00:00Z"),
    run("20261105-untimed-b", null),
    run("20261109-untimed-a", null),
  ];
  assert.deepEqual([...runs].sort(byLatest).map((item) => item.id), [
    "20261101-old-dir-but-used-today", "20261110-new-dir-idle", "20261109-untimed-a", "20261105-untimed-b",
  ]);
});

test("Recent lists the newest available chats of every project, bounded", () => {
  const runs = [
    run("a", "2026-11-11T08:00:00Z", { workspace: "/one" }),
    run("b", "2026-11-11T10:00:00Z", { workspace: "/two" }),
    run("gone", "2026-11-11T11:59:00Z", { availability: "workspace_missing" }),
    run("untimed", null),
    run("c", "2026-11-11T09:00:00Z", { workspace: "/one" }),
  ];
  assert.deepEqual(recentRuns(runs).map((item) => item.id), ["b", "c", "a"]);
  assert.deepEqual(recentRuns(runs, 2).map((item) => item.id), ["b", "c"]);
});

test("relative time reads the way a person says it", () => {
  const at = (iso: string | null) => relativeTime(run("x", iso), NOW);
  assert.equal(at("2026-11-11T11:59:30Z"), "just now");
  assert.equal(at("2026-11-11T12:00:30Z"), "just now");
  assert.equal(at("2026-11-11T11:55:00Z"), "5m ago");
  assert.equal(at("2026-11-11T09:00:00Z"), "3h ago");
  assert.equal(at("2026-11-08T12:00:00Z"), "3d ago");
  assert.equal(at("2026-09-01T12:00:00Z"), "2026-09-01");
  assert.equal(at(null), "");
  assert.equal(at("not a time"), "");
});
