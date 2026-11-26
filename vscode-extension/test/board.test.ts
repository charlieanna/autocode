import assert from "node:assert/strict";
import test from "node:test";

import { boardRows, groupCounts, queueAddArgs, readIssueListing, readQueueStatus, readRuns, startable } from "../src/core/board";

const P = "/work/widgets";
const ISSUES = `${P}/.autocode/issues`;

function runs(rows: object[]): string {
  return JSON.stringify({ schema: 1, scope: "workspace", workspace: P, counts: {}, runs: rows });
}

const RUNS = runs([
  { run_dir: `${ISSUES}/acme-widgets-7/.autocode/runs/r7`, workspace: `${ISSUES}/acme-widgets-7`, title: "Resolve GitHub issue acme/widgets#7",
    bucket: "needs you", availability: "available", status: "AWAITING_GOAL_APPROVAL", status_label: "Plan ready for your approval",
    needs: "approve_plan", ready_to_continue: false, updated_at: "2026-11-26T10:00:00" },
  { run_dir: `${P}/.autocode/runs/chat`, workspace: P, title: "Add dark mode", bucket: "needs you", availability: "available",
    status: "RUNNING", needs: "continue", ready_to_continue: true },
  { run_dir: `${ISSUES}/acme-widgets-8/.autocode/runs/r8`, workspace: `${ISSUES}/acme-widgets-8`, title: "Resolve issue 8",
    bucket: "working", availability: "available", status: "RUNNING", next_stage: "builder", next_stage_label: "Builder" },
  { run_dir: `${ISSUES}/acme-widgets-9/.autocode/runs/r9`, workspace: `${ISSUES}/acme-widgets-9`, title: "Resolve issue 9",
    bucket: "settled", availability: "available", status: "TASK_COMPLETE", status_label: "Finished" },
  { run_dir: `${P}/.autocode/runs/broken`, workspace: P, bucket: "unreadable", availability: "checkpoint_malformed", reason: "bad json" },
  { workspace: P, bucket: "working" },
]);

const QUEUE = JSON.stringify({
  schema_version: 1, project: P, max_active: 3, runner_active: false, working: 1,
  entries: [
    { ref: "acme/widgets#7", number: 7, state: "started" },
    { ref: "acme/widgets#10", number: 10, state: "queued" },
    { ref: "acme/widgets#11", number: 11, state: "failed", error: "GET returned 404" },
  ],
  issues: [
    { ref: "acme/widgets#9", number: 9, title: "Fix the footer", run_dir: `${ISSUES}/acme-widgets-9/.autocode/runs/r9`,
      branch: "autocode/issue-9-fix-the-footer", pr_url: null,
      actions: { open_pr: { label: "Open pull request", next_command: ["issue", "pr", "acme/widgets#9", "--project", P, "--open"] } } },
    { ref: "acme/widgets#7", number: 7, title: "Add a greeting", run_dir: `${ISSUES}/acme-widgets-7/.autocode/runs/r7/`,
      branch: "autocode/issue-7-add-a-greeting", pr_url: null, actions: {} },
  ],
  next_command: ["issue", "queue", "run", "--project", P],
});

test("runs are grouped by what they need, with the core's words and one action each", () => {
  const rows = boardRows(readRuns(RUNS), [readQueueStatus(QUEUE, P)]);
  const by = Object.fromEntries(rows.map((row) => [row.title, row]));
  assert.deepEqual(groupCounts(rows), { needs: 2, working: 1, queued: 2, done: 1, unreadable: 1 });
  const seven = rows.find((row) => row.issue?.number === 7)!;
  assert.equal(seven.group, "needs");
  assert.equal(seven.status, "Plan ready for your approval");
  assert.deepEqual(seven.action, { kind: "open", label: "Review plan" });
  assert.equal(seven.issue?.branch, "autocode/issue-7-add-a-greeting");
  assert.deepEqual(by["Add dark mode"]!.action, { kind: "continue", label: "Continue" });
  assert.equal(by["Resolve issue 8"]!.stage, "Builder");
  assert.deepEqual(by["Resolve issue 9"]!.action, { kind: "open_pr", label: "Open pull request" });
  assert.equal(rows.find((row) => row.group === "unreadable")!.status, "bad json");
  const queued = rows.filter((row) => row.group === "queued");
  assert.deepEqual(queued.map((row) => [row.issue?.number, row.status, row.run]), [
    [10, "Waiting for a free slot", null], [11, "Could not start", null],
  ]);
  assert.equal(queued[1]!.detail, "GET returned 404");
});

test("an issue that is starting and already has its run on the board is shown once", () => {
  const queue = JSON.parse(QUEUE);
  queue.entries[1].state = "starting";
  queue.issues.push({ ref: "acme/widgets#10", number: 10, title: "Slow page", run_dir: `${ISSUES}/acme-widgets-10/.autocode/runs/r10`, actions: {} });
  const started = JSON.parse(RUNS);
  started.runs.push({ run_dir: `${ISSUES}/acme-widgets-10/.autocode/runs/r10`, workspace: `${ISSUES}/acme-widgets-10`,
    title: "Resolve issue 10", bucket: "working", availability: "available", status: "RUNNING" });
  const rows = boardRows(readRuns(JSON.stringify(started)), [readQueueStatus(JSON.stringify(queue), P)]);
  assert.deepEqual(rows.filter((row) => row.issue?.number === 10).map((row) => row.group), ["working"]);
});

test("a command the core did not name in its exact shape is not taken", () => {
  const forged = JSON.parse(QUEUE);
  forged.next_command = ["issue", "queue", "run", "--project", "/elsewhere"];
  forged.issues[0].actions.open_pr.next_command = ["issue", "pr", "acme/widgets#9", "--project", P, "--open", "--ready"];
  const queue = readQueueStatus(JSON.stringify(forged), P);
  assert.equal(queue.nextCommand, null);
  assert.equal(queue.issues[0]!.openPr, null);
  const rows = boardRows(readRuns(RUNS), [queue]);
  assert.deepEqual(rows.find((row) => row.issue?.number === 9)!.action, { kind: "open", label: "Open" });
  assert.deepEqual(readQueueStatus(QUEUE, P).nextCommand, ["issue", "queue", "run", "--project", P]);
});

const LISTING = JSON.stringify({
  schema_version: 1, repository: "acme/widgets", project: P,
  issues: [
    { ref: "acme/widgets#12", number: 12, title: "Add search", labels: ["feature"], queue_state: null, run: null, actions: {} },
    { ref: "acme/widgets#11", number: 11, title: "Broken link", labels: [], queue_state: "failed", run: null, actions: {} },
    { ref: "acme/widgets#10", number: 10, title: "Slow page", labels: [], queue_state: "queued", run: null, actions: {} },
    { ref: "acme/widgets#7", number: 7, title: "Add a greeting", labels: [], queue_state: "started",
      run: { ref: "acme/widgets#7", number: 7, run_dir: "r7", actions: {} }, actions: {} },
  ],
  queue: JSON.parse(QUEUE),
  queue_add: { next_command: ["issue", "queue", "add", "--project", P, "--max-active", "LIMIT", "ISSUES"], default_max_active: 3 },
});

test("the picker offers only issues with no run that are not already waiting, and fills the named add", () => {
  const listing = readIssueListing(LISTING, P);
  assert.deepEqual(listing.issues.filter(startable).map((issue) => issue.number), [12, 11]);
  assert.deepEqual(queueAddArgs(listing, ["acme/widgets#12", "acme/widgets#11", "acme/widgets#12"], 2, ["--spec-first", "--provider", "claude"]), [
    "issue", "queue", "add", "--project", P, "--max-active", "2", "acme/widgets#12", "acme/widgets#11", "--", "--spec-first", "--provider", "claude",
  ]);
  assert.deepEqual(queueAddArgs(listing, ["acme/widgets#12"], 1, []), ["issue", "queue", "add", "--project", P, "--max-active", "1", "acme/widgets#12"]);
  assert.throws(() => queueAddArgs(listing, ["acme/widgets#10"], 2, []), /not an issue the list offered/);
  assert.throws(() => queueAddArgs(listing, ["acme/widgets#99"], 2, []), /not an issue the list offered/);
  assert.throws(() => queueAddArgs(listing, ["acme/widgets#12"], 0, []), /whole number/);
  assert.throws(() => queueAddArgs(listing, ["acme/widgets#12"], 1.5, []), /whole number/);
  assert.throws(() => queueAddArgs(listing, [], 2, []), /Tick at least one/);
});

test("an AutoCode that names another add, or none, cannot be filled", () => {
  const changed = JSON.parse(LISTING);
  changed.queue_add.next_command = ["issue", "queue", "add", "--project", P, "--max-active", "LIMIT", "ISSUES", "--", "--yes"];
  assert.throws(() => queueAddArgs(readIssueListing(JSON.stringify(changed), P), ["acme/widgets#12"], 1, []), /names no issue queue/);
  assert.throws(() => readIssueListing(JSON.stringify({ schema_version: 2 }), P), /Unsupported issue list version/);
  assert.throws(() => readRuns("not json"), /did not return JSON/);
});
