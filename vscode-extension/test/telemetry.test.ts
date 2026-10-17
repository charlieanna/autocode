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
  assert.deepEqual(telemetry(view).models, [{ model: "xiaomi-token-plan-sgp/mimo-v2.6-pro", variant: "high", provider: "kilocode" }]);
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
