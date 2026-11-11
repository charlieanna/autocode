import assert from "node:assert/strict";
import test from "node:test";

import { pipelineSteps, runHeader, spendSummary } from "../src/core/details";

// The same readings as the terminal chat's /details, /why and /spend (tests/test_chat_recoveries.py,
// tests/test_chat_spend.py, tests/test_chat_stage_policy.py): both clients word one view alike.

function step(job: string, label: string, state: string, extra: Record<string, unknown> = {}) {
  return { job, label, state, model: null, reasoning_effort: null, spent_usd: null, reason: null, ...extra };
}

const PIPELINE = [
  step("requirements", "Requirements", "ran", { model: "claude-sonnet-5-5", reasoning_effort: "medium", spent_usd: 0.08 }),
  step("planner", "Planner", "ran", { model: "openrouter/claude-opus-5-5", reasoning_effort: "high", spent_usd: 0.42 }),
  step("plan_reviewer", "Plan Reviewer", "skipped", { reason: "approved at the first review" }),
  step("builder", "Builder", "working", { model: "claude-opus-5-5", reasoning_effort: "medium", spent_usd: 0 }),
  step("tester", "Tester", "pending", { model: "glm-5.3" }),
  step("completion", "Completion Reviewer", "pending"),
];

const METRICS = {
  schema: 1,
  spent_usd: 2.5,
  complete: true,
  by_job: [
    { job: "Builder", attempts: 2, spent_usd: 1.5, complete: true, share: 0.6 },
    { job: "Planner", attempts: 1, spent_usd: 1, complete: false, share: 0.4 },
    { job: "Tester", attempts: 1, spent_usd: 0, complete: true, share: 0 },
  ],
  tokens: { input: 1_200_000, new: 300_000, cache_write: 10_000, cache_read: 900_000, output: 4_200, complete: true, cache_read_share: 0.75, cache_write_share: 0.01 },
  waste: { failed_attempts: 1, failed_usd: 0.3, failed_usd_complete: true, repeated_jobs: [{ job: "Builder", attempts: 2 }], report_repair_usd: 0.05 },
  single_model: {
    builder_usd: 1.5,
    builder_share: 0.6,
    checking_usd: 1,
    basis: "The Builder's spend stands in for one model building it alone",
    produced: { criteria_requested: 3, criteria_accepted: 2, fail_to_pass_tests: 1, mutants_tried: 4, mutants_killed: 3, problems_handled: 2 },
  },
};

function view(extra: Record<string, unknown> = {}) {
  return {
    status: "RUNNING",
    done: false,
    summary: { state: "working", label: "Working", sentence: "Builder working." },
    explanation: {
      what_happened: "The run is RUNNING.",
      what_it_means: "Work is in progress; no new decision is needed while the controller is active.",
      what_the_command_does: "Use --status to follow progress and inspect controller liveness.",
    },
    pipeline: PIPELINE,
    recoveries: [],
    run_metrics: { schema: 1, spent_usd: 0, complete: true, by_job: [] },
    stage_policy: null,
    stage_decisions: [],
    ...extra,
  };
}

const section = (header: ReturnType<typeof runHeader>, id: string) => header?.sections.find((row) => row.id === id);

test("the pipeline shows each job the view lists, with its model, level and spend, and why it was skipped", () => {
  const steps = pipelineSteps(view());
  assert.deepEqual(steps.map((row) => [row.mark, row.label, row.detail]), [
    ["✓", "Requirements", "claude-sonnet-5-5 · medium · $0.08"],
    ["✓", "Planner", "claude-opus-5-5 · high · $0.42"],
    ["–", "Plan Reviewer", "skipped: approved at the first review"],
    ["●", "Builder", "claude-opus-5-5 · medium"],
    ["·", "Tester", "glm-5.3"],
    ["·", "Completion Reviewer", ""],
  ]);
  // A row the client cannot read is left out rather than guessed at.
  assert.deepEqual(pipelineSteps({ pipeline: [{ label: "Builder", state: "exploded" }, { state: "ran" }, "x"] }), []);
  assert.deepEqual(pipelineSteps({}), []);
  const header = runHeader(view());
  assert.equal(section(header, "pipeline")?.summary, "Builder working");
  assert.equal(section(runHeader(view({ pipeline: [step("tester", "Tester", "next")] })), "pipeline")?.summary, "Tester next");
});

test("where the run is uses the core's summary and plain meaning, never its status code or flags", () => {
  const header = runHeader(view())!;
  assert.deepEqual([header.state, header.label, header.sentence], ["working", "Working", "Builder working."]);
  const where = section(header, "where")!;
  assert.deepEqual(where.lines, ["Builder working.", "Work is in progress; no new decision is needed while the controller is active."]);
  const shown = JSON.stringify(header);
  assert.doesNotMatch(shown, /RUNNING|--status/);
  const waiting = runHeader(view({
    status: "WAITING_FOR_USER",
    summary: { state: "needs_you", label: "Needs you", sentence: "Answer the open question." },
    needs: { kind: "answer", reason: "Requirements asks which database to use" },
  }))!;
  assert.equal(waiting.state, "needs_you");
  assert.ok(section(waiting, "where")!.lines.includes("Waiting for: Requirements asks which database to use"));
  // An older core without a summary still says where the run is.
  const older = runHeader({ status: "PAUSED_TIME_LIMIT", done: false, progress: { headline: "Paused at the time limit" } })!;
  assert.deepEqual([older.label, older.sentence], ["Paused time limit", "Paused at the time limit"]);
  assert.equal(runHeader(null), null);
});

test("a stopped step's details say the core's reason, next action and saved cause, never a flag", () => {
  const notice = "I couldn’t finish this step. The step failed. Your work so far is saved; continue to try this step again.";
  const stopped = (saved: string) => runHeader(view({
    status: "PAUSED_JOB_FAILURE",
    summary: { state: "stopped", label: "Stopped", sentence: "The step stopped." },
    needs: { kind: "resume", reason: saved },
    chat: { notice },
    recovery: { what_happened: "The step failed.", saved_reason: saved },
  }))!;
  const where = section(stopped("Builder exited with code 2"), "where")!;
  assert.ok(where.lines.includes(notice));
  assert.ok(where.lines.includes("Recorded reason: Builder exited with code 2"));
  const flagged = section(stopped("Run --abandon-stage 001/builder-01 first"), "where")!;
  assert.ok(flagged.lines.includes(notice));
  assert.ok(!flagged.lines.includes("Recorded reason: Run --abandon-stage 001/builder-01 first"));
  assert.doesNotMatch(JSON.stringify(flagged), /--abandon-stage/);
  // A running run carries no stopped-step lines.
  assert.ok(!section(runHeader(view({ chat: { notice } }))!, "where")!.lines.includes(notice));
});

test("what went wrong lists every recovery with what AutoCode did, as /why does", () => {
  const recoveries = [
    { id: "a", kind: "rejected", at: "2026-11-02T05:33:44.837705+00:00", headline: "Investigator answer rejected (2 times)",
      problem: "A reproduced bug needs exactly one of probe or untestable", action: "AutoCode did not accept it.", by: "Investigator", detail: null },
    { id: "b", kind: "investigated", at: "2026-11-02T05:40:00+00:00", headline: "Builder stopped making progress",
      problem: null, action: "AutoCode called the Investigator.", by: "Investigator", detail: "The fixture path was wrong." },
    { id: "c", headline: "no action recorded" },
  ];
  const header = runHeader(view({ recoveries }))!;
  assert.deepEqual(section(header, "wrong")!.lines, [
    "05:33:44 Investigator answer rejected (2 times)",
    "  What went wrong: A reproduced bug needs exactly one of probe or untestable",
    "  Then: AutoCode did not accept it.",
    "05:40:00 Builder stopped making progress",
    "  Then: AutoCode called the Investigator.",
    "  What it found: The fixture path was wrong.",
  ]);
  assert.equal(section(header, "wrong")!.summary, "2 so far, each handled by AutoCode");
  assert.ok(header.facts.includes("2 things went wrong, handled"));
  assert.equal(section(runHeader(view()), "wrong"), undefined, "nothing went wrong: no section");
});

test("spend restates run_metrics as /spend does, and says nothing before anything is spent", () => {
  const header = runHeader(view({ run_metrics: METRICS }))!;
  assert.equal(spendSummary(view({ run_metrics: METRICS })), "Builder 60% · checking 40%");
  assert.ok(header.facts.includes("Builder 60% · checking 40%"));
  assert.deepEqual(section(header, "spend")!.lines, [
    "Where the money went: $2.50",
    "  Builder $1.50 · 60%, 2 runs",
    "  Planner $1.00 (known part) · 40%",
    "  Tokens: input 1.2M, new 300.0k, cache write 10.0k, cache read 900.0k, output 4.2k",
    "  Cache reads 75% of input, cache writes 1%",
    "Spent again",
    "  1 failed attempt $0.30; ran again: Builder ×2",
    "  Report repairs $0.05",
    "Compared with one model building it alone",
    "  Builder $1.50 (60%); planning and checking $1.00",
    "  What that bought: 2/3 criteria accepted; 1 test fails before, pass after; 3/4 broken lines caught; 2 problems handled",
    "  The Builder's spend stands in for one model building it alone.",
  ]);
  const noShare = { ...METRICS, single_model: { ...METRICS.single_model, builder_share: null } };
  assert.equal(spendSummary(view({ run_metrics: noShare })), "$2.50 across 3 jobs");
  assert.equal(section(runHeader(view()), "spend"), undefined);
  assert.equal(spendSummary(view({ run_metrics: { ...METRICS, schema: 2 } })), null, "an unknown schema is not guessed at");
  assert.deepEqual(runHeader(view())!.facts, []);
});

test("verification shows once the run has finished, from the core's own gate", () => {
  const coverage = [{ id: "AC1", recorded_status: "passed", state: "covered", criterion: "export writes JSON", evidence_refs: ["tests/test_export.py::test_json"] },
    { id: "AC2", recorded_status: "pending", state: "awaiting_review", human_acceptance_pending: true }];
  const running = runHeader(view({ verification: { freshness: "current", coverage } }))!;
  assert.equal(section(running, "verification"), undefined, "a running run is not 'not verified' yet");
  const done = runHeader(view({ status: "TASK_COMPLETE", done: true, delivery: { verified_complete: true }, verification: { freshness: "current", coverage } }))!;
  const shown = section(done, "verification")!;
  assert.equal(shown.summary, "Verified by AutoCode's completion check");
  assert.deepEqual(shown.lines, [
    "Report: current",
    "AC1: recorded passed; coverage covered",
    "  export writes JSON",
    "  Evidence: tests/test_export.py::test_json",
    "AC2: recorded pending; coverage awaiting review; your acceptance pending",
  ]);
  const unverified = runHeader(view({ status: "TASK_COMPLETE", done: true, efficiency: { delivery: { current_completion: false } } }))!;
  assert.equal(section(unverified, "verification")!.summary, "Not verified");
  const review = runHeader(view({ workflow: "review", review_result: { outcome: "changes_requested", summary: "Two blocking findings", freshness: "current", artifact: "review.md", reasons: ["source changed"] } }))!;
  assert.deepEqual([section(review, "verification")!.title, section(review, "verification")!.summary], ["Review result", "Recorded changes requested"]);
  assert.ok(section(review, "verification")!.lines.includes("source changed"));
});

test("the planning path and the stage decisions say what the core decided and why", () => {
  const policy = {
    path: "compact",
    decisions: [{ stage: "draft_plan", decision: "omitted", reason: "the diagnosis is enough", covered_by: ["runner:compact_draft", "review_plan", "something_new"] }],
    reasons: [],
    fallback: { from: "review_plan", reason: "the review blocked the draft", review_call_refunded: true },
  };
  const decisions = [
    { stage: "requirements_gather", job: "requirements", label: "Requirements", decision: "run", launches: 1, effort_choice: null, covered_by: [] },
    { stage: "review_plan", job: "plan_reviewer", label: "Plan Reviewer", decision: "run", launches: 2, covered_by: [],
      effort_choice: { level: "medium", ceiling: "high", lowered: true } },
    { stage: "builder", job: "builder", label: "Builder", decision: "run", launches: 1, covered_by: [],
      effort_choice: { level: "xhigh", saved: "high", raised: true } },
    { stage: "revise_plan", job: "planner", label: "Planner", decision: "skip", reason: "approved at the first review", launches: 0, effort_choice: null, covered_by: ["review_plan"] },
  ];
  const header = runHeader(view({ stage_policy: policy, stage_decisions: decisions }))!;
  assert.deepEqual([section(header, "path")!.summary, ...section(header, "path")!.lines], [
    "Compact: some planning left out",
    "Planner draft: omitted (the diagnosis is enough)",
    "  Covered by: the runner's draft from the diagnosis, the Plan Reviewer, something_new",
    "Fell back at review_plan: the review blocked the draft; the review call was refunded",
  ]);
  const full = runHeader(view({ stage_policy: { path: "full", decisions: [], reasons: ["requested test names need explicit plan bindings"] } }))!;
  assert.deepEqual(section(full, "path")!.lines, ["Kept the full path because:", "  requested test names need explicit plan bindings"]);
  assert.deepEqual([section(header, "decisions")!.summary, ...section(header, "decisions")!.lines], [
    "3 ran · 1 skipped",
    "Requirements: ran once",
    "Plan Reviewer: ran 2 times, at medium, below your high",
    "Builder: ran once, at xhigh, above your high",
    "Planner: skipped (approved at the first review)",
    "  Covered by: the Plan Reviewer",
  ]);
  assert.equal(section(runHeader(view()), "path"), undefined);
  assert.equal(section(runHeader(view()), "decisions"), undefined);
});

test("sections come in a fixed order: where, pipeline, what went wrong, spend, verification, path, decisions", () => {
  const header = runHeader(view({
    status: "TASK_COMPLETE", done: true, delivery: { verified_complete: true }, verification: { freshness: "current" },
    recoveries: [{ id: "a", headline: "Builder sent back", action: "It tried again." }], run_metrics: METRICS,
    stage_policy: { path: "full", reasons: ["x"] }, stage_decisions: [{ label: "Builder", decision: "run", launches: 1 }],
  }))!;
  assert.deepEqual(header.sections.map((row) => row.id), ["where", "pipeline", "wrong", "spend", "verification", "path", "decisions"]);
});
