import assert from "node:assert/strict";
import test from "node:test";
import { usageDisplay, type BudgetCost, type Quantity, type Usage } from "../src/core/telemetry";

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

test("completed known zero tokens are distinct from unknown usage", () => {
  assert.equal(usageDisplay({ accounting: { tokens: { input_tokens: quantity(0), output_tokens: quantity(0) } } }).tokens, "0 tokens");
  assert.equal(usageDisplay().tokens, "Tokens not reported");
});

test("parallel workers still pending say the totals leave out live worker usage", () => {
  const display = usageDisplay({ accounting: { parallel_workers_pending: ["M1"] } });
  assert.match(display.details.at(-1)!.value, /do not include all live worker usage/);
});

test("cap accounting is primary while the distinct historical estimate stays secondary", () => {
  const view = { done: false, cost: { spent_usd: 1.966683, cap_usd: 2, complete: false, unpriced_stages: 10 },
    usage: { accounting: { cost: { historical_estimated_usd: quantity(1.5335513, false) } } } };
  const before = structuredClone(view);
  const display = usageDisplay(view.usage, view.cost);
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
    { cost: { spent_usd: 0, running_usd: 0.102963, cap_usd: null, complete: false, unpriced_stages: 0 },
      expected: "Cost counted toward cap: at least $0.00 · no configured cap (partial) · running stage $0.1030 so far" },
    { cost: { spent_usd: 0.25, complete: true, unpriced_stages: 1 },
      expected: "Cost counted toward cap: at least $0.2500 · cap not reported (partial; 1 unpriced stage)" },
  ];
  for (const row of cases) {
    assert.equal(usageDisplay(null, row.cost as BudgetCost).cost, row.expected);
  }
});

test("older cores without cap accounting keep the explicitly historical display", () => {
  const usage: Usage = { accounting: { cost: { historical_estimated_usd: quantity(1.53, false) } } };
  assert.equal(usageDisplay(usage).cost, "$1.53 (partial) historical est.");
  assert.equal(usageDisplay().cost, "Cost not reported");
});
