import assert from "node:assert/strict";
import test from "node:test";

import { duration, finishSummary, money, runTotal, sessionTotal, stageLines, stages, struggling, tokens, totalText } from "../src/core/meter";
import { statusLine } from "../src/statusBar";
import type { StatusView } from "../src/core/view";

// The same cases as the terminal chat's tests/test_chat_meter.py: both clients read one view alike.
const NOW = Date.parse("2026-11-02T01:10:00Z");

function attempt(stage: string, job: string, extra: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    stage,
    job,
    launch_route: { model: "gpt-x", reasoning_effort: "high" },
    duration_seconds: 30,
    exit_code: 0,
    attributed_tokens: {
      input_tokens: { value: 1000, known: 1000, complete: true },
      output_tokens: { value: 200, known: 200, complete: true },
    },
    ...extra,
  };
}

function view(attempts: Record<string, unknown>[], options: { spent?: number; byStage?: unknown[]; inherited?: number; complete?: boolean; cap?: number; extra?: Record<string, unknown> } = {}) {
  const complete = options.complete ?? true;
  return {
    status: "RUNNING",
    done: false,
    usage: {
      accounting: {
        attempts,
        tokens: {
          input_tokens: { value: 150_000, known: 150_000, complete: true },
          output_tokens: { value: 34_000, known: 34_000, complete },
        },
      },
    },
    cost: {
      spent_usd: options.spent ?? 0.42,
      inherited_usd: options.inherited ?? null,
      cap_usd: options.cap ?? null,
      complete,
      breakdown: { by_stage: options.byStage ?? [] },
    },
    ...options.extra,
  } as unknown as StatusView;
}

test("each stage shows its model, time, tokens and cost", () => {
  const rows = stages(view([
    attempt("requirements_gather", "Requirements"),
    attempt("terra", "Builder", { launch_route: { model: "gpt-terra", reasoning_effort: "medium" } }),
  ], { byStage: [{ stage: "terra", spent_usd: 0.12 }] }), NOW);
  assert.deepEqual(rows.map((row) => row.job), ["Requirements", "Builder"]);
  assert.deepEqual(stageLines(rows[1]!), { head: "Builder · 30s · $0.12", detail: "gpt-terra medium · 1.2k tokens" });
  assert.equal(rows[1]!.state, "done");
});

test("a stage with no model shows no cost or model row", () => {
  const plain = attempt("orchestrator", "Orchestrator", { launch_route: {}, attributed_tokens: {} });
  const [row] = stages(view([plain], { byStage: [{ stage: "orchestrator", spent_usd: 0 }] }), NOW);
  assert.deepEqual(stageLines(row!), { head: "Orchestrator · 30s", detail: "" });
});

test("a retry is struggling and says why the last attempt failed", () => {
  const shown = view([
    attempt("terra", "Builder", { rejected: true }),
    attempt("terra", "Builder", { active: true, duration_seconds: null, started_at: "2026-11-02T01:06:00Z" }),
  ]);
  const [row] = stages(shown, NOW);
  assert.deepEqual([row!.state, row!.attempts, row!.seconds], ["retrying", 2, 270]);
  assert.equal(struggling(shown, NOW), "Builder · attempt 2 · 4m30s · last one answer rejected");
});

test("a report repair is the repaired stage's next attempt, and its cost is added", () => {
  const rejected = attempt("sol", "Tester", { rejected: true });
  const repairing = attempt("sol_report_repair", "Tester", {
    original_stage: "sol", active: true, duration_seconds: null, started_at: "2026-11-02T01:09:30Z",
  });
  const shown = view([rejected, attempt("resolver", "Resolver"), repairing]);
  assert.deepEqual(stages(shown, NOW).map((row) => row.job), ["Tester", "Resolver"]);
  assert.equal(struggling(shown, NOW), "Tester · attempt 2 · 1m00s · last one answer rejected");
  const repaired = { ...repairing, active: false, duration_seconds: 18 };
  const costs = [{ stage: "sol", spent_usd: 0.25 }, { stage: "sol_report_repair", spent_usd: 0.1 }];
  const tester = stages(view([rejected, attempt("resolver", "Resolver"), repaired], { byStage: costs }), NOW)[0]!;
  assert.deepEqual([tester.state, tester.attempts, tester.spentUsd], ["done", 2, 0.35]);
  assert.equal(stageLines(tester).head, "Tester · 48s · $0.35 · attempt 2");
});

test("a first attempt at work is not struggling", () => {
  const shown = view([attempt("terra", "Builder", { active: true, duration_seconds: null, started_at: "2026-11-02T01:09:00Z" })]);
  assert.equal(stages(shown, NOW)[0]!.state, "working");
  assert.equal(struggling(shown, NOW), "");
});

test("a failed attempt is marked with its reason, and a nonzero exit counts as failed", () => {
  const [timedOut] = stages(view([attempt("sol", "Tester", { timed_out: true })]), NOW);
  assert.equal(timedOut!.state, "failed");
  assert.match(stageLines(timedOut!).detail, /timed out/);
  const shown = view([attempt("terra", "Builder", { exit_code: 1 }), attempt("terra", "Builder", { active: true, duration_seconds: 5 })]);
  assert.equal(struggling(shown, NOW), "Builder · attempt 2 · 35s · last one exit 1");
});

test("incomplete tokens are marked as a floor, and runner-owned attempts are not stages", () => {
  const partial = attempt("terra", "Builder", {
    attributed_tokens: { input_tokens: { value: 1000, complete: true }, output_tokens: { value: null, known: 50, complete: false } },
  });
  const rows = stages(view([partial, attempt("runner", "Runner", { runner_owned: true })]), NOW);
  assert.equal(rows.length, 1);
  assert.match(stageLines(rows[0]!).detail, /1\.1k\+ tokens/);
  assert.deepEqual(stages({}, NOW), []);
});

test("the run total is the core's own, with its cap", () => {
  assert.equal(totalText(runTotal(view([]))), "$0.42 · 184k tokens");
  assert.equal(totalText(runTotal(view([], { complete: false }))), "$0.42+ · 184k tokens");
  assert.equal(totalText(runTotal(view([], { spent: 1.21, cap: 5 }))), "$1.21 of $5.00 · 184k tokens");
  assert.equal(totalText(runTotal(view([], { spent: 1.21, cap: 5 })), false), "$1.21 · 184k tokens");
  assert.equal(runTotal({}), null);
});

test("the session adds chats without counting a restored step's spend twice", () => {
  const first = runTotal(view([], { spent: 1.0 }));
  const restored = runTotal(view([], { spent: 1.5, inherited: 1.0 }));
  assert.equal(totalText(sessionTotal([first, restored])), "$1.50 · 368k tokens");
});

test("a chat not read yet makes the session total a floor", () => {
  assert.equal(totalText(sessionTotal([runTotal(view([], { spent: 1.0 })), null])), "$1.00+ · 184k tokens");
  assert.equal(sessionTotal([null, null]), null);
});

test("small amounts and counts read briefly", () => {
  assert.deepEqual([0, 0.0004, 0.00206, 1.25, null].map(money), ["$0", "<$0.001", "$0.00206", "$1.25", "$?"]);
  assert.deepEqual([0.2, 0.254, 0.5].map(money), ["$0.20", "$0.254", "$0.50"], "below a dollar still shows cents");
  assert.deepEqual([950, 1234, 184_000, 3_400_000].map(tokens), ["950", "1.2k", "184k", "3.4M"]);
  assert.deepEqual([9, 245, 3725].map(duration), ["9s", "4m05s", "1h02m"]);
});

test("a finished run's summary names its result, requirements, changed files and stage time", () => {
  const done = view([attempt("terra", "Builder"), attempt("sol", "Tester")], {
    extra: {
      done: true,
      progress: { headline: "Complete; current delivery verified", requirements: { checked: 8, failed: 0, unchecked: 0, awaiting_review: 0 } },
      code_checkpoints: { rows: [{ id: "c1", changed_files: ["greet.py", "README.md"] }, { id: "c2", changed_files: ["greet.py"] }] },
      acceptance_tests: { files: ["test_greet.py"] },
    },
  });
  const summary = finishSummary(done)!;
  assert.equal(summary.headline, "Complete; current delivery verified");
  assert.deepEqual(summary.requirements, { checked: 8, total: 8 });
  assert.deepEqual(summary.files, ["greet.py", "README.md", "test_greet.py"]);
  assert.equal(summary.seconds, 60);
  assert.equal(finishSummary(view([])), null, "a run still working has no closing card");
});

test("the status bar names the working stage, its cost and trouble", () => {
  const working = view([attempt("terra", "Builder", { rejected: true }), attempt("terra", "Builder", { active: true, duration_seconds: 5 })], {
    spent: 1.21, cap: 5, extra: { needs: { kind: "continue" }, liveness: { kind: "supervised" }, progress: { headline: "Building" } },
  });
  const line = statusLine(working, NOW);
  assert.equal(line.text, "$(warning) AutoCode: Building · $1.21 · 184k tokens");
  assert.match(line.tooltip, /Struggling: Builder · attempt 2/);
  const calm = view([attempt("terra", "Builder", { active: true, duration_seconds: 5 })], {
    extra: { needs: { kind: "continue" }, liveness: { kind: "supervised" }, progress: { headline: "Building" } },
  });
  assert.equal(statusLine(calm, NOW).text, "$(sync~spin) AutoCode: Builder · $0.42 · 184k tokens");
  const waiting = view([], { extra: { needs: { kind: "approve_plan" }, progress: { headline: "Plan ready" } } });
  assert.match(statusLine(waiting, NOW).text, /^\$\(bell\) AutoCode: Plan ready/);
  assert.match(statusLine(waiting, NOW).tooltip, /Waiting for you/);
  assert.match(statusLine({ ...waiting, done: true }, NOW).text, /^\$\(check\)/);
});
