import assert from "node:assert/strict";
import test from "node:test";

import { runHeader } from "../src/core/details";
import { runTotal, sessionTotal, totalText } from "../src/core/meter";
import type { StatusView } from "../src/core/view";
import { statusLine } from "../src/statusBar";

const quantity = (value: number) => ({ value, known: value, complete: true });
const unpriced = { usage_missing: 0, model_unknown: 0, rate_unavailable: 3, models_without_rate: ["custom/model"] };

function planning(cost: Record<string, unknown> = {}): StatusView {
  return {
    status: "AWAITING_GOAL_APPROVAL", done: false,
    summary: { state: "needs_you", label: "Plan ready", sentence: "Review the plan." },
    needs: { kind: "approve_plan" },
    pipeline: [{ label: "Planner", state: "ran", spent_usd: null, spent_complete: false, model: "custom/model" }],
    usage: { accounting: {
      complete: true, active_attempts: 0,
      tokens: { input_tokens: quantity(3_000_000), output_tokens: quantity(100_000) },
      attempts: [{ stage: "draft_plan", job: "Planner", exit_code: 0, duration_seconds: 30,
        launch_route: { model: "custom/model" },
        attributed_tokens: { input_tokens: quantity(3_000_000), output_tokens: quantity(100_000) } }],
    } },
    cost: { spent_usd: null, running_usd: null, complete: false, unpriced_stages: 3,
      cap_usd: null, reported_usd: null, estimated_usd: null,
      breakdown: { unpriced, by_stage: [{ stage: "draft_plan", spent_usd: null, complete: false }] },
      ...cost },
  } as unknown as StatusView;
}

test("unavailable dollars stay explicit beside independently known tokens in every cost surface", () => {
  const view = planning();
  assert.equal(totalText(runTotal(view)), "Cost unavailable · 3.1M tokens");
  const explanation = statusLine(view).tooltip;
  assert.match(explanation, /no usable provider-reported cost/i);
  assert.match(explanation, /no saved price.*custom\/model/i);
  assert.match(explanation, /provider.*(?:usage|billing) page/i);
  assert.doesNotMatch(explanation, /free|\$0(?:\D|$)/i, "do not imply a missing charge is zero");
  const line = statusLine(view);
  assert.match(line.text, /Cost unavailable · 3\.1M tokens/);
  assert.match(line.tooltip, /no saved price.*custom\/model/i);
  const header = runHeader(view)!;
  assert.equal(header.pipeline[0]!.cost, "Unavailable");
  assert.equal(header.total?.cost, "Cost unavailable");
  const detail = header.sections.find((row) => row.id === "usage")!;
  assert.match(detail.lines.join("\n"), /no saved price.*custom\/model/i);
});

test("the core's missing-usage, missing-model and unknown older-core cases are explained without guessing", () => {
  for (const [reason, expected] of [
    [{ ...unpriced, usage_missing: 3, rate_unavailable: 0, models_without_rate: [] }, /token usage.*(?:missing|incomplete|insufficient)/i],
    [{ ...unpriced, model_unknown: 3, rate_unavailable: 0, models_without_rate: [] }, /model.*(?:not recorded|unknown|missing)/i],
    [undefined, /reason.*not (?:reported|supplied)/i],
  ] as const) {
    const view = planning({ breakdown: { unpriced: reason } });
    const shown = statusLine(view).tooltip;
    assert.match(shown, expected);
    assert.doesNotMatch(shown, /custom\/model/, "a launch route alone does not establish which price or receipt is missing");
  }
});

test("partial costs keep the known subtotal and distinguish provider amounts from dated estimates", () => {
  const view = planning({ spent_usd: 0.3, reported_usd: 0.25, estimated_usd: 0.05, prices_as_of: "2026-11-19" });
  assert.equal(totalText(runTotal(view)), "$0.30+ · 3.1M tokens");
  const shown = statusLine(view).tooltip;
  assert.match(shown, /partial.*known subtotal/i);
  assert.match(shown, /Provider-reported: \$0\.25/);
  assert.match(shown, /Price-table estimate: \$0\.05.*2026-11-19/);
  assert.match(shown, /not an invoice/);
});

test("complete provider amounts, estimates and measured zero keep their own basis", () => {
  for (const [cost, expected, absent] of [
    [{ spent_usd: 0.25, reported_usd: 0.25 }, /Provider-reported: \$0\.25/, /Price-table estimate:/],
    [{ spent_usd: 0.25, estimated_usd: 0.25 }, /Price-table estimate: \$0\.25/, /Provider-reported:/],
    [{ spent_usd: 0 }, /complete/i, /(?:Provider-reported|Price-table estimate):/],
  ] as const) {
    const view = planning({ ...cost, complete: true, unpriced_stages: 0, breakdown: {} });
    const shown = statusLine(view).tooltip;
    assert.match(shown, expected);
    assert.doesNotMatch(shown, absent);
    assert.doesNotMatch(totalText(runTotal(view)), /unavailable|\+/i);
    assert.match(shown, /not an invoice/);
  }
});

test("an unpriced attempt is still visible when it has no token measurements", () => {
  const view = planning();
  (view as unknown as { usage: unknown }).usage = {};
  assert.equal(totalText(runTotal(view)), "Cost unavailable");
  assert.equal(runTotal({}), null, "an unread or unstarted chat has no cost receipt");
});

test("empty accounting sums are not presented as provider billing or an estimate", () => {
  const view = planning({ spent_usd: 0, complete: true, unpriced_stages: 0, breakdown: {} });
  (view as unknown as { usage: unknown }).usage = { accounting: {
    cost: { reported_usd: { ...quantity(0), known_items: 0 }, historical_estimated_usd: { ...quantity(0), known_items: 0 } },
  } };
  const detail = runHeader(view)!.sections.find((row) => row.id === "usage")!;
  assert.doesNotMatch(detail.lines.join("\n"), /Provider-reported:|Historical comparison estimate:/);
  assert.equal(totalText(runTotal(view)), "$0", "the core's known zero remains visible");
});

test("complete dollar receipts do not become partial because token attribution is incomplete", () => {
  const view = planning({ spent_usd: 0.5, reported_usd: 0.5, complete: true, unpriced_stages: 0, breakdown: {} });
  (view as unknown as { usage: unknown }).usage = { accounting: { complete: false,
    tokens: { input_tokens: { value: null, known: 3_000_000, complete: false }, output_tokens: quantity(100_000) },
  } };
  const detail = runHeader(view)!.sections.find((row) => row.id === "usage")!;
  assert.equal(detail.lines[0], "Accounted total: $0.50");
  assert.equal(totalText(runTotal(view)), "$0.50 · 3.1M+ tokens");
});

test("one unpriced chat cannot erase another chat's known subtotal", () => {
  const known = runTotal(planning({ spent_usd: 0.25, complete: true }));
  const unknown = runTotal(planning());
  assert.equal(totalText(sessionTotal([known, unknown])), "$0.25+ · 6.2M tokens");
  assert.equal(totalText(sessionTotal([unknown])), "Cost unavailable · 3.1M tokens");
});

test("unexplained carried-over cost stays unknown rather than borrowing a current model's price", () => {
  const view = planning({ spent_usd: 1, inherited_usd: 1, unpriced_stages: 4 });
  const shown = statusLine(view).tooltip;
  assert.match(shown, /reason was not reported for 1 attempt with unavailable cost/i);
  assert.match(shown, /known subtotal/i);
  assert.doesNotMatch(shown, /Price-table estimate:/);
});
