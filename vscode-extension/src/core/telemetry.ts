/** Read-only model and usage display from the public task-run accounting view. */
export interface Quantity {
  value?: number | null;
  known?: number;
  complete?: boolean;
  known_items?: number;
}

type TokenKey = "input_tokens" | "output_tokens" | "cached_input_tokens" | "cache_write_tokens" | "reasoning_output_tokens";

export interface Attempt {
  stage?: string | null;
  job?: string | null;
  model?: string;
  engine?: string;
  active?: boolean;
  finished_at?: string | null;
  interrupted?: boolean;
  timed_out?: boolean;
  rejected?: boolean;
  runner_owned?: boolean;
  usage_basis?: string;
  launch_route?: { model?: string; engine?: string; provider?: string | null; reasoning_effort?: string | null };
}

export interface ModelRoute {
  job?: string | null;
  model?: string | null;
  engine?: string | null;
  provider?: string | null;
  reasoning_effort?: string | null;
}

interface ModelDisplay {
  job: string;
  model: string;
  variant: string;
  provider: string;
}

export interface Usage {
  stages?: number;
  active_stage?: string | null;
  tokens?: number | Partial<Record<TokenKey, number | null>>;
  cost_usd?: { reported?: number; estimated?: number; complete?: boolean };
  unknown_stages?: number;
  partial_stages?: number;
  accounting?: {
    attempts?: Attempt[];
    tokens?: Partial<Record<TokenKey, Quantity>>;
    cost?: { reported_usd?: Quantity; historical_estimated_usd?: Quantity };
    parallel_workers_pending?: string[];
  };
}

/** Existing core accounting used for the money cap; prices and policy stay in the core. */
export interface BudgetCost {
  spent_usd?: number | null;
  cap_usd?: number | null;
  unpriced_stages?: number;
  complete?: boolean;
}

export interface UsageDisplay {
  tokens: string;
  cost: string;
  details: { label: string; value: string }[];
}

export interface Telemetry extends UsageDisplay {
  modelLabel: string;
  models: ModelDisplay[];
  configuredModels: ModelDisplay[];
  stagesUsed: (ModelDisplay & { attempts: number; active: boolean })[];
}

const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value) && value >= 0;
const count = (value: number) => value.toLocaleString();
const usd = (value: number) => value > 0 && value < 0.0001 ? "<$0.0001" : `$${value.toFixed(value > 0 && value < 1 ? 4 : 2)}`;

function measured(quantity?: Quantity): { value: number; complete: boolean } | null {
  if (quantity?.complete && finite(quantity.value)) return { value: quantity.value, complete: true };
  if (finite(quantity?.known) && (quantity.known > 0 || (quantity.known_items ?? 0) > 0)) {
    return { value: quantity.known, complete: false };
  }
  return null;
}

function displayQuantity(quantity: Quantity | undefined, money = false): string {
  const item = measured(quantity);
  if (!item || money && item.value === 0 && !item.complete) return "Not reported";
  return `${item.complete || money ? "" : "≥"}${money ? usd(item.value) : count(item.value)}${item.complete ? "" : " (partial)"}`;
}

function historicalUsageDisplay(usage?: Usage | null): UsageDisplay {
  const result: UsageDisplay = { tokens: "Tokens not reported", cost: "Cost not reported", details: [] };
  if (!usage) return result;
  const accounting = usage.accounting;
  const legacyPartial = Boolean(usage.active_stage || usage.unknown_stages || usage.partial_stages);
  const quantities: Partial<Record<TokenKey, Quantity>> = accounting?.tokens ?? {};
  if (!accounting && usage.tokens && typeof usage.tokens === "object") {
    for (const [key, value] of Object.entries(usage.tokens)) {
      if (finite(value)) quantities[key as TokenKey] = { value: legacyPartial ? null : value, known: value, known_items: 1, complete: !legacyPartial };
    }
  }
  const input = measured(quantities.input_tokens), output = measured(quantities.output_tokens);
  if (input || output) {
    const complete = input?.complete && output?.complete;
    // Cache and reasoning are already included in input/output; never add them twice.
    result.tokens = `${complete ? "" : "≥"}${count((input?.value ?? 0) + (output?.value ?? 0))} tokens${complete ? "" : " (partial)"}`;
  } else if (!accounting && finite(usage.tokens)) result.tokens = `${count(usage.tokens)} tokens`;
  for (const [key, label] of [
    ["input_tokens", "Input (includes cache)"], ["output_tokens", "Output (includes reasoning)"],
    ["cached_input_tokens", "Cache read"], ["cache_write_tokens", "Cache write"], ["reasoning_output_tokens", "Reasoning"],
  ] as const) result.details.push({ label, value: displayQuantity(quantities[key]) });

  const costs = accounting?.cost;
  const legacy = usage.cost_usd;
  const legacyCost = (value?: number): Quantity | undefined => finite(value) && value > 0
    ? { value: legacy?.complete ? value : null, known: value, known_items: 1, complete: legacy?.complete === true } : undefined;
  const reported = accounting ? costs?.reported_usd : legacyCost(legacy?.reported);
  const estimated = accounting ? costs?.historical_estimated_usd : legacyCost(legacy?.estimated);
  const report = measured(reported), estimate = measured(estimated);
  const parts = [];
  if (report && (report.value > 0 || report.complete && !estimate?.value)) {
    parts.push(`${displayQuantity(reported, true)} reported`);
  }
  if (estimate && estimate.value > 0) parts.push(`${displayQuantity(estimated, true)} historical est.`);
  if (parts.length) result.cost = parts.join(" · ");
  result.details.push(
    { label: "Provider-reported cost", value: displayQuantity(reported, true) },
    { label: "Historical cost estimate", value: displayQuantity(estimated, true) },
    { label: "Cost basis", value: "Reported cost and historical estimates are separate. Estimates use the core’s comparison rates, not current prices or your token-plan bill." },
  );
  if (!accounting && legacyPartial) result.details.push({ label: "Coverage", value: "Saved-stage usage only; active, interrupted or unreported usage may be missing." });
  if (accounting?.parallel_workers_pending?.length) result.details.push({ label: "Coverage", value: "Parallel workers are still pending; these totals do not include all live worker usage." });
  return result;
}

/** Show the cap's own accounting first, without repricing or computing a remaining balance. */
export function usageDisplay(usage?: Usage | null, cost?: BudgetCost | null): UsageDisplay {
  const display = historicalUsageDisplay(usage);
  if (!cost || typeof cost !== "object" || Array.isArray(cost)) return display;
  const known = finite(cost.spent_usd);
  const unpriced = typeof cost.unpriced_stages === "number" && Number.isSafeInteger(cost.unpriced_stages) && cost.unpriced_stages > 0
    ? cost.unpriced_stages : 0;
  const complete = cost.complete === true && known && unpriced === 0;
  const amount = known ? `${complete ? "" : "at least "}${usd(cost.spent_usd!)}` : "unknown";
  const cap = finite(cost.cap_usd) && cost.cap_usd > 0 ? usd(cost.cap_usd)
    : cost.cap_usd === null ? "no configured cap" : "cap not reported";
  const partial = complete ? "" : ` (partial${unpriced ? `; ${count(unpriced)} unpriced stage${unpriced === 1 ? "" : "s"}` : ""})`;
  const limit = finite(cost.cap_usd) && cost.cap_usd > 0 ? ` / ${cap} cap` : ` · ${cap}`;
  display.cost = `Cost counted toward cap: ${amount}${limit}${partial}`;
  display.details.unshift(
    { label: "Cost counted toward cap", value: amount + partial },
    { label: "Cost cap", value: cap },
    { label: "Budget cost basis", value: "The core compares this known subtotal with the cap. Unpriced or unreported usage may be missing. This is not a provider bill." },
  );
  return display;
}

export function telemetry(view: { cost?: BudgetCost | null; usage?: Usage | null; provider?: string | null; routes?: Record<string, ModelRoute> | null; done: boolean }): Telemetry {
  const attempts = (view.usage?.accounting?.attempts ?? []).filter((row) => !row.runner_owned && row.usage_basis !== "runner");
  const current = view.done ? [] : attempts.filter((row) => row.active && !row.finished_at && !row.interrupted && !row.timed_out && !row.rejected);
  const selected = current.length ? current : attempts.slice(-1);
  const describe = (row: Attempt): ModelDisplay => {
    const route = row.launch_route;
    const engine = route?.engine || row.engine;
    return {
      // Human job names are supplied by the core, never a second client-side table.
      job: row.job || row.stage || "Stage not reported",
      model: row.model || route?.model || "Model not reported",
      variant: route?.reasoning_effort || "Not reported",
      provider: route?.provider || (engine === "opencode" ? view.provider : engine) || "Not reported",
    };
  };
  const used = new Map<string, Telemetry["stagesUsed"][number]>();
  for (const row of attempts) {
    const model = describe(row);
    const key = JSON.stringify(model);
    const item = used.get(key) ?? { ...model, attempts: 0, active: false };
    item.attempts++;
    item.active ||= current.includes(row);
    used.set(key, item);
  }
  return {
    ...usageDisplay(view.usage, view.cost),
    modelLabel: current.length ? (current.length > 1 ? "Current attempts" : "Current attempt") : "Last used",
    models: selected.map(describe),
    configuredModels: Object.entries(view.routes ?? {}).filter(([, route]) => route && typeof route === "object").map(([role, route]) => ({
      job: route.job || role,
      model: route.model || "Model not reported",
      variant: route.reasoning_effort || "Not reported",
      provider: route.provider || (route.engine === "opencode" ? view.provider : route.engine) || "Not reported",
    })),
    stagesUsed: [...used.values()],
  };
}
