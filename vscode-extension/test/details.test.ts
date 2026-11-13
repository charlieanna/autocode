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
  assert.equal(section(header, "wrong")!.summary, "2 recovery events recorded");
  assert.ok(header.facts.includes("2 recovery events"));
  assert.ok(!JSON.stringify(header).includes("handled"));
  assert.equal(section(runHeader(view()), "wrong"), undefined, "nothing went wrong: no section");
});

test("spend restates run_metrics as /spend does, and says nothing before anything is spent", () => {
  // METRICS is an older core's shape: no planning split, job time or report-repair counts.
  const header = runHeader(view({ run_metrics: METRICS }))!;
  // METRICS has no planning split, as an older core's view: the rest is "everything else", not "checking" (#3268).
  assert.equal(spendSummary(view({ run_metrics: METRICS })), "Cost: Builder 60% · everything else 40%");
  assert.ok(header.facts.includes("Cost: Builder 60% · everything else 40%"));
  assert.deepEqual(section(header, "spend")!.lines, [
    "Where the money went: $2.50",
    "  Builder $1.50 · 60% of cost, 2 runs",
    "  Planner $1.00 (known part) · 40% of cost",
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

test("spend names planning apart from the checking after it (#3268)", () => {
  // The #2066 run read "Builder 18% · checking 82%", though Requirements and the Planner were $2.1 of $3.1.
  const split = { ...METRICS, single_model: { ...METRICS.single_model, builder_usd: 0.56, builder_share: 0.18,
    checking_usd: 2.54, planning_usd: 2.1, review_usd: 0.44, planning_share: 0.68, review_share: 0.14 } };
  assert.equal(spendSummary(view({ run_metrics: split })), "Cost: Builder 18% · planning 68% · checking 14%");
  assert.ok(section(runHeader(view({ run_metrics: split }))!, "spend")!.lines
    .includes("  Builder $0.56 (18%); planning $2.10; checking $0.44"));
  const noPlanning = { ...split, single_model: { ...split.single_model, planning_usd: 0, planning_share: 0, review_usd: 2.54, review_share: 0.82 } };
  assert.equal(spendSummary(view({ run_metrics: noPlanning })), "Cost: Builder 18% · checking 82%", "no planning spend, no planning part");
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

// What the terminal's /details (autocode_chat_cost.details) and its Stage panel add beyond the
// pipeline: each stage's own attempts, what the working stage does now, the run's accounting and
// the saved routes. Every figure comes from the view; nothing is a command.

const STARTED = Date.parse("2026-11-11T08:00:00Z");

function attempt(stage: string, job: string, extra: Record<string, unknown> = {}) {
  return {
    stage, job, attempt_id: `001/${stage}`, model: null,
    launch_route: { model: "claude-sonnet-5-5", reasoning_effort: "high", provider: "anthropic" },
    started_at: "2026-11-11T07:58:00Z", finished_at: "2026-11-11T07:59:00Z", duration_seconds: 60,
    attributed_tokens: { input_tokens: { value: 12_000, known: 12_000, complete: true }, output_tokens: { value: 800, known: 800, complete: true } },
    ...extra,
  };
}

function working(extra: Record<string, unknown> = {}) {
  return view({
    pipeline: [
      // Requirements has no saved route here: its model is the one its attempt launched on.
      step("requirements", "Requirements", "ran"),
      step("planner", "Planner", "working", { model: "claude-opus-5-5", reasoning_effort: "high" }),
      step("plan_reviewer", "Plan Reviewer", "pending", { model: "claude-opus-5-5", reasoning_effort: "medium" }),
    ],
    cost: {
      spent_usd: 0.05, running_usd: 0.12, estimated_usd: 0.05, reported_usd: null, cap_usd: 5, complete: false,
      prices_as_of: "2026-11-09", unpriced_stages: 0,
      breakdown: { by_stage: [{ stage: "requirements_gather", job: "Requirements", spent_usd: 0.05 }], by_billing: [{ billing: "subscription", spent_usd: 0.17 }] },
    },
    usage: {
      accounting: {
        complete: true, active_attempts: 1,
        tokens: { input_tokens: { value: 40_000, known: 40_000, complete: true }, output_tokens: { value: null, known: 2_000, complete: false },
          cached_input_tokens: { value: 30_000, known: 30_000, complete: true }, cache_write_tokens: { value: null, known: 0, complete: false } },
        attempts: [
          attempt("requirements_gather", "Requirements"),
          attempt("draft_plan", "Planner", {
            active: true, finished_at: null, duration_seconds: null, started_at: "2026-11-11T07:59:30Z",
            launch_route: { model: "claude-opus-5-5", reasoning_effort: "high", provider: "anthropic" },
            effort_choice: { raised: true, saved: "medium", text: "the draft touches sensitive paths" },
            attributed_tokens: { input_tokens: { value: null, known: 28_000, complete: false }, output_tokens: { value: null, known: 1_200, complete: false } },
          }),
          attempt("runner_check", "Runner", { runner_owned: true }),
        ],
      },
    },
    efficiency: { time: { summed_execution_seconds: 90, exclusive_wall_seconds_by_category: { planning: 90 } },
      by_category: { planning: { attempts: 2, tokens: { input_tokens: { value: null, known: 40_000 }, output_tokens: { value: null, known: 2_000 } } } } },
    routes: { planner: { job: "Planner", model: "claude-opus-5-5", reasoning_effort: "high", provider: "anthropic" } },
    escalations: [{ job: "Planner", from: { model: "claude-sonnet-5-5" }, to: { model: "claude-opus-5-5" }, trigger: "plan_review_blocked", outcome: "accepted" }],
    chat: {
      now: [{ agent: "Requirements", text: "done" }, { agent: "Planner", text: "reading files" }],
      log: [
        { id: "1", agent: "Planner", kind: "message", text: "Mapping the export path first." },
        { id: "2", agent: "Planner", kind: "message", text: "The CSV writer drops the header row [partial]" },
        { id: "3", agent: "Planner", kind: "command", text: "rg header" },
      ],
    },
    ...extra,
  });
}

test("each stage shows the model, level, time, tokens and spend its own attempts used, and the working one what it does now", () => {
  const steps = pipelineSteps(working(), STARTED);
  assert.deepEqual(steps.map((row) => [row.label, row.model, row.level, row.time, row.tokens, row.cost, row.now]), [
    ["Requirements", "claude-sonnet-5-5", "high", "1m00s", "13k", "$0.05", ""],
    // The running attempt: its time so far, its tokens as a floor and the core's running spend.
    ["Planner", "claude-opus-5-5", "high", "30s", "29k+", "$0.12", "reading files"],
    ["Plan Reviewer", "claude-opus-5-5", "medium", "", "", "", ""],
  ]);
  assert.deepEqual(steps.map((row) => row.detail), [
    "claude-sonnet-5-5 · high · 1m00s · 13k tokens · $0.05",
    "claude-opus-5-5 · high · 30s · 29k+ tokens · $0.12",
    "claude-opus-5-5 · medium",
  ]);
  const header = runHeader(working(), STARTED);
  // Always visible under the stage table: what the working stage is doing; its figures are its row.
  assert.equal(header?.current, "Planner: reading files");
  // The table's total row is the run's own working time, tokens and accounted spend, each a floor while incomplete.
  assert.deepEqual(header?.total, { time: "1m30s", tokens: "42k+", cost: "$0.17+" });
  assert.deepEqual(header?.facts, [], "the working time is the total row now, not a fact");
  const stages = section(header, "pipeline");
  assert.equal(stages?.title, "Stages");
  // Show details adds what the table leaves out: the whole route, and what each stage is doing.
  assert.deepEqual(stages?.lines, [
    "✓ Requirements: claude-sonnet-5-5 high",
    "● Planner: claude-opus-5-5 high",
    "  Now: reading files",
    "  Latest: The CSV writer drops the header row",
    "· Plan Reviewer: will run on claude-opus-5-5 medium",
  ]);
  // Before anything ran there is nothing to say under the table, and no total.
  const before = runHeader(view({ pipeline: [step("requirements", "Requirements", "next", { model: "claude-sonnet-5-5", reasoning_effort: "medium" })] }));
  assert.equal(before?.current, "");
  assert.equal(before?.total, null);
  assert.deepEqual(before?.pipeline.map((row) => [row.mark, row.label, row.model, row.level]), [["›", "Requirements", "claude-sonnet-5-5", "medium"]]);
  assert.equal(runHeader(view({ pipeline: [] }))?.current, "");
});

test("a stage's model is shown without its provider, and the whole route is kept for details", () => {
  // Ankur, 2026-11-11: the Stages list read "xiaomi-token-plan-sgp/mimo-v2.6-pro medium · 599k tokens" on a second line.
  const header = runHeader(view({
    pipeline: [step("requirements", "Requirements", "ran", { model: "xiaomi-token-plan-sgp/mimo-v2.6-pro", reasoning_effort: "medium" })],
  }));
  const [row] = header!.pipeline;
  assert.deepEqual([row!.model, row!.level, row!.route], ["mimo-v2.6-pro", "medium", "xiaomi-token-plan-sgp/mimo-v2.6-pro medium"]);
  assert.equal(section(header, "pipeline")?.lines[0], "✓ Requirements: xiaomi-token-plan-sgp/mimo-v2.6-pro medium");
});

test("a stage that launched more than once counts its attempts and failures, and a retry says why", () => {
  const retried = working({
    pipeline: [step("requirements", "Requirements", "ran"), step("planner", "Planner", "working", { model: "claude-opus-5-5", reasoning_effort: "high" })],
    usage: { accounting: { attempts: [
      attempt("requirements_gather", "Requirements", { rejected: true }),
      attempt("requirements_gather", "Requirements"),
      attempt("draft_plan", "Planner", { timed_out: true }),
      attempt("draft_plan", "Planner", { active: true, duration_seconds: null, started_at: "2026-11-11T07:59:30Z" }),
    ] } },
  });
  const steps = pipelineSteps(retried, STARTED);
  assert.deepEqual(steps.map((row) => [row.label, row.attempts, row.failed, row.runs, row.why]), [
    // It failed once and then succeeded: counted, but no longer retrying.
    ["Requirements", 2, 1, "2 attempts, 1 failed", ""],
    ["Planner", 2, 1, "2 attempts, 1 failed", "timed out"],
  ]);
  assert.deepEqual(section(runHeader(retried, STARTED), "pipeline")?.lines.slice(0, 2), [
    "✓ Requirements: claude-sonnet-5-5 high; 2 attempts, 1 failed",
    "● Planner: claude-opus-5-5 high; 2 attempts, 1 failed, the last timed out",
  ]);
  // Another review round after a success is not a failure.
  const rounds = pipelineSteps(working({
    pipeline: [step("plan_reviewer", "Plan Reviewer", "ran")],
    usage: { accounting: { attempts: [attempt("review_plan", "Plan Reviewer"), attempt("review_plan", "Plan Reviewer")] } },
  }), STARTED);
  assert.deepEqual(rounds.map((row) => [row.attempts, row.failed, row.runs]), [[2, 0, "ran 2 times"]]);
  const single = pipelineSteps(working(), STARTED)[0]!;
  assert.deepEqual([single.attempts, single.runs], [1, ""]);
});

test("a report repair joins the stage it repairs on one row, counted apart from the stage's own runs", () => {
  // The meter groups a repair under the stage it repairs (original_stage); the table must land
  // both attempts on that stage's row, with the repair's own job name or not. A repair only rewrote a
  // rejected report, so "Planner ×9" must not count repairs as runs (live, 2026-11-12: "Tester ×4" was 2 + 2).
  const repaired = working({
    pipeline: [step("plan_reviewer", "Plan Reviewer", "ran"), step("builder", "Builder", "working")],
    usage: { accounting: { attempts: [
      attempt("review_plan", "Plan Reviewer"),
      attempt("sol_report_repair", "Plan Reviewer", { original_stage: "review_plan", rejected: true }),
    ] } },
  });
  const steps = pipelineSteps(repaired, STARTED);
  assert.deepEqual(steps.map((row) => [row.label, row.attempts, row.failed, row.repairs, row.runs]), [
    ["Plan Reviewer", 1, 0, 1, "1 report repair"],
    ["Builder", 0, 0, 0, ""],
  ]);
  const twice = working({
    pipeline: [step("tester", "Tester", "ran")],
    usage: { accounting: { attempts: [
      attempt("write_acceptance_tests", "Tester"),
      attempt("tester", "Tester", { rejected: true }),
      attempt("tester_report_repair", "Tester", { original_stage: "tester", rejected: true }),
      attempt("tester_report_repair", "Tester", { original_stage: "tester" }),
    ] } },
  });
  assert.deepEqual(pipelineSteps(twice, STARTED).map((row) => [row.attempts, row.failed, row.repairs, row.runs]),
    [[2, 1, 2, "2 attempts, 1 failed, 2 report repairs"]]);
});

test("a job the pipeline does not list, such as the model preflight, gets its own row where it ran", () => {
  // Ankur, 2026-11-11: Model Preflight was in the Stages list but not in the header's pipeline.
  const shown = working({
    pipeline: [
      step("requirements", "Requirements", "ran"),
      step("planner", "Planner", "working", { model: "zai-coding-plan/glm-5.3", reasoning_effort: "high" }),
      step("plan_reviewer", "Plan Reviewer", "pending"),
    ],
    usage: { accounting: { attempts: [
      attempt("requirements_gather", "Requirements"),
      attempt("model_preflight", "Model Preflight", { launch_route: { model: "zai-coding-plan/glm-5.3" } }),
      attempt("model_preflight", "Model Preflight", { launch_route: { model: "zai-coding-plan/glm-5.3" } }),
      attempt("draft_plan", "Planner", { active: true, duration_seconds: null, started_at: "2026-11-11T07:59:30Z" }),
      attempt("investigate_stuck", "Investigator", { active: true, duration_seconds: null, started_at: "2026-11-11T07:59:50Z" }),
    ] } },
  });
  const steps = pipelineSteps(shown, STARTED);
  assert.deepEqual(steps.map((row) => [row.label, row.state, row.model, row.time, row.runs]), [
    ["Requirements", "ran", "claude-sonnet-5-5", "1m00s", ""],
    ["Model Preflight", "ran", "glm-5.3", "2m00s", "ran 2 times"],
    ["Planner", "working", "glm-5.3", "30s", ""],
    // Launched after every listed stage that ran, so it comes after the working one.
    ["Investigator", "working", "claude-sonnet-5-5", "10s", ""],
    ["Plan Reviewer", "pending", "", "", ""],
  ]);
  // A view with no pipeline still lists every stage its attempts ran, in the order they first ran.
  const bare = pipelineSteps({ usage: { accounting: { attempts: [attempt("requirements_gather", "Requirements"), attempt("draft_plan", "Planner")] } } }, STARTED);
  assert.deepEqual(bare.map((row) => [row.label, row.state]), [["Requirements", "ran"], ["Planner", "ran"]]);
  // A finished run has nothing working, even an attempt the view still marks active.
  const ended = pipelineSteps({ done: true, usage: { accounting: { attempts: [attempt("draft_plan", "Planner", { active: true })] } } }, STARTED);
  assert.deepEqual(ended.map((row) => row.state), ["ran"]);
});

test("the total row appears once the run has worked or spent, and only with a stage table", () => {
  const timed = runHeader(view({ efficiency: { time: { summed_execution_seconds: 125 } } }));
  assert.deepEqual(timed?.total, { time: "2m05s", tokens: "", cost: "" });
  const spent = runHeader(view({ cost: { spent_usd: 1.5, complete: true }, usage: { accounting: { tokens: {
    input_tokens: { value: 900_000, known: 900_000, complete: true }, output_tokens: { value: 40_000, known: 40_000, complete: true } } } } }));
  assert.deepEqual(spent?.total, { time: "", tokens: "940k", cost: "$1.50" });
  assert.equal(runHeader({ summary: { state: "working" }, cost: { spent_usd: 1, complete: true } })?.total, null, "no stages, no table");
});

test("cost and usage restate the core's accounting, tokens, time and attempts as the terminal's /details does", () => {
  const usage = section(runHeader(working(), STARTED), "usage");
  assert.equal(usage?.summary, "$0.17+ of $5.00 · 42k+ tokens");
  assert.deepEqual(usage?.lines, [
    "Accounted so far: $0.05 (1 running)",
    "  Running stage so far: $0.12",
    "  Price-table estimate: $0.05 (prices as of 2026-11-09)",
    "  Spending cap: $5.00",
    "  On your subscription: $0.17 as API-equivalent pricing, not an invoice",
    "Tokens",
    "  Input: 40,000",
    "  Output: 2,000 known",
    "  Cache reads (in input): 30,000",
    "Time and tokens by kind of work",
    "  Planning: 2 attempts, 40,000 known in, 2,000 known out, 1m30s",
    "Total execution time: 1m30s",
    "Attempts",
    "  1. Requirements · finished · 1m00s",
    "    claude-sonnet-5-5 · high · anthropic",
    "    Tokens: input 12,000, output 800",
    "  2. Planner · running",
    "    claude-opus-5-5 · high · anthropic",
    "    One level above your medium: the draft touches sensitive paths",
    "    Tokens: input 28,000 known, output 1,200 known",
    "Estimates and provider reports are not an invoice.",
  ]);
  const routes = section(runHeader(working(), STARTED), "routes");
  assert.deepEqual(routes?.lines, [
    "Planner: claude-opus-5-5 · high · anthropic",
    "Moved up",
    "  Planner: claude-sonnet-5-5 → claude-opus-5-5; plan review blocked",
    "  Outcome: accepted",
  ]);
  // The core's historical comparison, which only the removed Models & usage section used to show.
  const historical = section(runHeader(view({ usage: { accounting: { cost: {
    historical_estimated_usd: { value: null, known: 1.5335513, complete: false, known_items: 15 } } } } }), STARTED), "usage");
  assert.ok(historical?.lines.includes("  Historical comparison estimate: $1.53 known"), historical?.lines.join("\n"));
  assert.ok(!section(runHeader(working(), STARTED), "usage")?.lines.some((line) => line.includes("Historical")),
    "no estimate, no line");
  // Beside the dated price-table estimate it would be a second, different price for the same tokens.
  const priced = section(runHeader(view({ cost: { spent_usd: 0.41, estimated_usd: 0.41, complete: true },
    usage: { accounting: { cost: { historical_estimated_usd: { value: 0.38, known: 0.38, complete: true, known_items: 6 } } } } }), STARTED), "usage");
  assert.ok(priced?.lines.includes("  Price-table estimate: $0.41"), priced?.lines.join("\n"));
  assert.ok(!priced?.lines.some((line) => line.includes("Historical")), priced?.lines.join("\n"));
  // The cap line says only what `cost` says: an older core without it claims neither a cap nor none.
  const cap = (cost?: Record<string, unknown>) => section(runHeader(view({ ...(cost ? { cost } : {}), usage: { tokens: 10 } }), STARTED), "usage")
    ?.lines.find((line) => line.includes("Spending cap"));
  assert.equal(cap(), undefined);
  assert.equal(cap({ spent_usd: 1, cap_usd: null }), "  Spending cap: none");
  assert.equal(cap({ spent_usd: 1, cap_usd: 0 }), "  Spending cap: none");
  assert.equal(cap({ spent_usd: 1 }), "  Spending cap: not reported");
  assert.equal(cap({ spent_usd: 1, cap_usd: 2 }), "  Spending cap: $2.00");
  // A view with no accounting or routes shows neither section.
  assert.equal(section(runHeader(view()), "usage"), undefined);
  assert.equal(section(runHeader(view()), "routes"), undefined);
});
