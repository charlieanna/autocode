import assert from "node:assert/strict";
import test from "node:test";
import { telemetry, usageDisplay, type Quantity, type Usage } from "../src/core/telemetry";

const quantity = (value: number, complete = true): Quantity => ({
  value: complete ? value : null, known: value, complete, known_items: 1,
});

test("native token counters include cache and reasoning once, with partial coverage visible", () => {
  // Public counter shape and known totals from the retained Kilo qualification.
  const display = usageDisplay({ accounting: { tokens: {
    input_tokens: quantity(137806, false), output_tokens: quantity(11341, false),
    cached_input_tokens: quantity(51904, false), reasoning_output_tokens: quantity(7867, false),
  } } });
  assert.equal(display.tokens, "≥149,147 tokens (partial)");
  assert.equal(display.details.find((row) => row.label === "Cache read")?.value, "≥51,904 (partial)");
  assert.equal(display.details.find((row) => row.label === "Reasoning")?.value, "≥7,867 (partial)");
});

test("unknown native accounting never falls back to misleading saved-stage totals or free cost", () => {
  const unknown = { value: null, known: 0, complete: false, known_items: 0 };
  const display = usageDisplay({ tokens: 9000, cost_usd: { reported: 5, complete: true }, accounting: {
    tokens: { input_tokens: unknown, output_tokens: unknown }, cost: { reported_usd: quantity(0, false) },
  } });
  assert.equal(display.tokens, "Tokens not reported");
  assert.equal(display.cost, "Cost not reported");
  assert.equal(display.details.find((row) => row.label === "Provider-reported cost")?.value, "Not reported");
  assert.equal(display.details.find((row) => row.label === "Input (includes cache)")?.value, "Not reported");
});

test("reported costs and historical estimates stay separate, with small nonzero costs visible", () => {
  const display = usageDisplay({ accounting: { cost: {
    reported_usd: quantity(0.25, false), historical_estimated_usd: quantity(0.00001, false),
  } } });
  assert.equal(display.cost, "$0.2500 (partial) reported · <$0.0001 (partial) historical est.");
  assert.match(display.details.find((row) => row.label === "Cost basis")!.value, /not current prices or your token-plan bill/);
});

test("older structured token views retain estimated cost when reported subtotal is zero", () => {
  const display = usageDisplay({ tokens: { input_tokens: 1000, output_tokens: 200, cached_input_tokens: 500 },
    cost_usd: { reported: 0, estimated: 0.093995, complete: false }, active_stage: "terra" });
  assert.equal(display.tokens, "≥1,200 tokens (partial)");
  assert.equal(display.cost, "$0.0940 (partial) historical est.");
  assert.ok(!display.cost.includes("$0.00"));
});

test("model and variant come from the actual attempt, and a retained finished attempt is historical", () => {
  const view = { done: false, provider: "kilocode", usage: { accounting: { attempts: [{
    model: "xiaomi-token-plan-sgp/mimo-v2.6-pro", active: true,
    launch_route: { model: "older-route", reasoning_effort: "high", engine: "opencode" },
  }] } } };
  assert.deepEqual(telemetry(view).models, [{ job: "Stage not reported", model: "xiaomi-token-plan-sgp/mimo-v2.6-pro", variant: "high", provider: "kilocode" }]);
  assert.equal(telemetry(view).modelLabel, "Current attempt");
  const usage: Usage = view.usage;
  usage.accounting!.attempts![0]!.finished_at = "2026-10-17T00:51:55Z";
  assert.equal(telemetry({ ...view, usage }).modelLabel, "Last used");
});

test("parallel models remain visible and missing variants are not invented", () => {
  const result = telemetry({ done: false, usage: { accounting: {
    attempts: [
      { model: "model-a", active: true, launch_route: { reasoning_effort: "medium" } },
      { model: "model-b", active: true },
      { model: "not-a-model", active: true, runner_owned: true },
    ], parallel_workers_pending: ["M1"],
  } } });
  assert.equal(result.modelLabel, "Current attempts");
  assert.deepEqual(result.models.map((row) => row.variant), ["medium", "Not reported"]);
  assert.match(result.details.at(-1)!.value, /do not include all live worker usage/);
});

test("completed known zero tokens are distinct from unknown usage", () => {
  assert.equal(usageDisplay({ accounting: { tokens: { input_tokens: quantity(0), output_tokens: quantity(0) } } }).tokens, "0 tokens");
  assert.equal(usageDisplay().tokens, "Tokens not reported");
});

test("all configured roles are visible before launch, including shared models and missing variants", () => {
  const display = telemetry({ done: false, provider: "kilocode", routes: {
    requirements: { job: "Requirements", model: "zai/glm", engine: "opencode", reasoning_effort: "medium" },
    terra: { job: "Builder", model: "xiaomi/mimo", provider: "kilocode", reasoning_effort: "high" },
    sol: { job: "Tester", model: "zai/glm", engine: "opencode" },
    completion: { job: "Completion Reviewer", model: "other", provider: "claude" },
  } });
  assert.deepEqual(display.configuredModels.map((row) => [row.job, row.model, row.variant, row.provider]), [
    ["Requirements", "zai/glm", "medium", "kilocode"], ["Builder", "xiaomi/mimo", "high", "kilocode"],
    ["Tester", "zai/glm", "Not reported", "kilocode"], ["Completion Reviewer", "other", "Not reported", "claude"],
  ]);
  assert.deepEqual(display.models, []);
  assert.deepEqual(display.stagesUsed, []);
});

test("stage history retains every used model and variant independently of future route assignments", () => {
  const result = telemetry({ done: false, routes: { terra: { job: "Builder", model: "future-model" } }, usage: { accounting: { attempts: [
    { job: "Job recognizer", model: "small", finished_at: "t" },
    { job: "Builder", model: "old", launch_route: { reasoning_effort: "medium" }, finished_at: "t" },
    { job: "Builder", model: "old", launch_route: { reasoning_effort: "medium" }, interrupted: true },
    { job: "Builder", model: "new", launch_route: { reasoning_effort: "high" }, active: true },
    { job: "Tester", model: "tester", launch_route: { reasoning_effort: "high" }, active: true, finished_at: "t" },
    { model: "runner", runner_owned: true },
  ] } } });
  assert.deepEqual(result.stagesUsed.map((row) => [row.job, row.model, row.variant, row.attempts, row.active]), [
    ["Job recognizer", "small", "Not reported", 1, false], ["Builder", "old", "medium", 2, false],
    ["Builder", "new", "high", 1, true], ["Tester", "tester", "high", 1, false],
  ]);
  assert.equal(result.models[0]!.model, "new");
  assert.equal(result.configuredModels[0]!.model, "future-model");
});


test("cap accounting is primary while the distinct historical estimate stays secondary", () => {
  const view = { done: false, cost: { spent_usd: 1.966683, cap_usd: 2, complete: false, unpriced_stages: 10 },
    usage: { accounting: { cost: { historical_estimated_usd: quantity(1.5335513, false) } } } };
  const before = structuredClone(view);
  const display = telemetry(view);
  assert.equal(display.cost, "Cost counted toward cap: at least $1.97 / $2.00 cap (partial; 10 unpriced stages)");
  assert.equal(display.details[0]?.label, "Cost counted toward cap");
  assert.equal(display.details.find((row) => row.label === "Historical cost estimate")?.value, "$1.53 (partial)");
  assert.match(display.details.find((row) => row.label === "Budget cost basis")!.value, /not a provider bill/);
  assert.deepEqual(view, before, "displaying cap accounting must not change the core view");
});

test("budget cost distinguishes known zero, unknown, partial and missing cap fields", () => {
  const cases = [
    { cost: { spent_usd: 0.25, cap_usd: 2, complete: true, unpriced_stages: 0 },
      expected: "Cost counted toward cap: $0.2500 / $2.00 cap" },
    { cost: { spent_usd: null, cap_usd: 2, complete: false, unpriced_stages: 10 },
      expected: "Cost counted toward cap: unknown / $2.00 cap (partial; 10 unpriced stages)" },
    { cost: { spent_usd: 0, cap_usd: null, complete: true, unpriced_stages: 0 },
      expected: "Cost counted toward cap: $0.00 · no configured cap" },
    { cost: { spent_usd: 0, cap_usd: 2, complete: false, unpriced_stages: 0 },
      expected: "Cost counted toward cap: at least $0.00 / $2.00 cap (partial)" },
    { cost: { spent_usd: -1, cap_usd: Number.NaN, complete: false },
      expected: "Cost counted toward cap: unknown · cap not reported (partial)" },
    { cost: { spent_usd: 0.25, complete: true, unpriced_stages: 1 },
      expected: "Cost counted toward cap: at least $0.2500 · cap not reported (partial; 1 unpriced stage)" },
  ];
  for (const row of cases) {
    const view = { done: false, cost: row.cost };
    assert.equal(telemetry(view).cost, row.expected);
  }
});

test("older cores without cap accounting keep the explicitly historical display", () => {
  const view = { done: false, usage: { accounting: { cost: { historical_estimated_usd: quantity(1.53, false) } } } };
  assert.equal(telemetry(view).cost, "$1.53 (partial) historical est.");
  assert.equal(telemetry({ done: false }).cost, "Cost not reported");
});
