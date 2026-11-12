/**
 * What each stage of a run did and what it used: a display of the public status view's own
 * attempt and cost records, the same reading as the terminal chat's `autocode_chat_meter`.
 *
 * Every figure is a value the core wrote: an attempt's route, start, duration, outcome and
 * attributed tokens (`usage.accounting.attempts`), a stage's accounted spend
 * (`cost.breakdown.by_stage`), the running stage's spend so far (`cost.running_usd`) and the
 * run's accounted total (`cost.spent_usd`). The only arithmetic is adding one stage's own
 * attempts together, adding the running stage's spend to the run's, and adding separate chats'
 * run totals for the session line. Display only: nothing here chooses a route or spends.
 */

export type StageState = "done" | "working" | "failed" | "retrying";

export interface StageRow {
  stage: string;
  /** The core's screen name for the job (`attempt.job`), never a client-side table. */
  job: string;
  /** Model and reasoning level of the newest attempt. */
  route: string;
  /** The same route's model and level apart, for a client that shows them separately. */
  model: string;
  level: string;
  state: StageState;
  /** How many times the stage itself launched; a report repair is counted apart (`repairs`). */
  attempts: number;
  /** How many of those launches were rejected, timed out, interrupted or exited nonzero. */
  failedAttempts: number;
  /** Report repairs: attempts that only rewrote a rejected report (`original_stage`), never another run of the stage. */
  repairs: number;
  seconds: number | null;
  tokens: number;
  tokensComplete: boolean;
  spentUsd: number | null;
  /** Why the newest attempt (or, while one runs, the one before it) did not succeed. */
  why: string;
}

export interface RunTotal {
  spentUsd: number | null;
  inheritedUsd: number;
  capUsd: number | null;
  tokens: number;
  /** Each part is a floor while anything it covers is not reported yet. */
  costComplete: boolean;
  tokensComplete: boolean;
}

export interface FinishSummary {
  headline: string;
  requirements: { checked: number; total: number } | null;
  files: string[];
  seconds: number | null;
  total: RunTotal | null;
}

type Row = Record<string, unknown>;

const mapping = (value: unknown): Row => (value && typeof value === "object" && !Array.isArray(value) ? value as Row : {});
const rows = (value: unknown): Row[] => (Array.isArray(value) ? value.filter((row) => row && typeof row === "object" && !Array.isArray(row)) : []);
const isNumber = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value) && value >= 0;
const text = (value: unknown): string => (typeof value === "string" ? value.trim() : "");

/** A dollar amount: cents for a dollar or more, three figures below. */
export function money(value: number | null | undefined): string {
  if (!isNumber(value)) return "$?";
  if (value === 0) return "$0";
  if (value >= 1) return `$${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  if (value < 0.001) return "<$0.001";
  // Three figures, but never fewer than cents: $0.20, $0.254, $0.00206.
  const shown = String(Number(value.toPrecision(3)));
  const decimals = shown.split(".")[1]?.length ?? 0;
  return `$${decimals < 2 ? value.toFixed(2) : shown}`;
}

/** A token count in a few characters: 950, 1.2k, 184k, 3.4M. */
export function tokens(value: number | null | undefined): string {
  if (!isNumber(value)) return "?";
  for (const [size, suffix] of [[1_000_000, "M"], [1_000, "k"]] as const) {
    if (value >= size) {
      const shown = value / size;
      return (shown < 10 ? shown.toFixed(1) : shown.toFixed(0)) + suffix;
    }
  }
  return value.toFixed(0);
}

export function duration(seconds: number | null | undefined): string {
  if (!isNumber(seconds)) return "";
  const whole = Math.floor(seconds);
  if (whole < 60) return `${whole}s`;
  if (whole < 3600) return `${Math.floor(whole / 60)}m${String(whole % 60).padStart(2, "0")}s`;
  return `${Math.floor(whole / 3600)}h${String(Math.floor((whole % 3600) / 60)).padStart(2, "0")}m`;
}

/** An accounting quantity's known amount and whether it is complete; a bare number is complete. */
function known(quantity: unknown): [number | null, boolean] {
  if (quantity && typeof quantity === "object") {
    const row = quantity as Row;
    const amount = row.complete === true ? row.value : row.known;
    return [isNumber(amount) ? amount : null, row.complete === true];
  }
  return [isNumber(quantity) ? quantity : null, isNumber(quantity)];
}

function secondsOf(row: Row, now: number): number | null {
  if (isNumber(row.duration_seconds)) return row.duration_seconds;
  const started = typeof row.started_at === "string" ? Date.parse(row.started_at) : NaN;
  return row.active && Number.isFinite(started) ? Math.max(0, (now - started) / 1000) : null;
}

function failed(row: Row): boolean {
  return Boolean(row.rejected || row.interrupted || row.timed_out
    || (Number.isInteger(row.exit_code) && row.exit_code !== 0));
}

/** A report repair: an attempt the core records against the stage it repairs (`original_stage`). */
function repair(row: Row): boolean {
  const original = text(row.original_stage);
  return Boolean(original) && original !== text(row.stage);
}

function why(row: Row): string {
  if (row.rejected) return "answer rejected";
  if (row.timed_out) return "timed out";
  if (row.interrupted) return "interrupted";
  return Number.isInteger(row.exit_code) && row.exit_code !== 0 ? `exit ${row.exit_code}` : "";
}

function modelOf(row: Row): [string, string] {
  const route = mapping(row.launch_route);
  // The level the attempt ran at comes first: a review below its saved ceiling records it there, and the
  // saved route keeps the ceiling (docs/task-run.md). The terminal chat's meter reads it the same way.
  return [text(route.model) || text(row.model), text(row.reasoning_effort) || text(route.reasoning_effort)];
}

function routeOf(row: Row): string {
  return modelOf(row).filter(Boolean).join(" ");
}

/** One row per stage the run has launched, in the order it first ran. */
export function stages(view: unknown, now = Date.now()): StageRow[] {
  const root = mapping(view);
  const attempts = rows(mapping(mapping(root.usage).accounting).attempts)
    .filter((row) => !row.runner_owned && row.usage_basis !== "runner");
  const cost = mapping(root.cost);
  const spend = new Map(rows(mapping(cost.breakdown).by_stage).map((row) => [row.stage, row]));
  // The running stage is in no finished-stage row yet; its spend so far is the core's `running_usd`.
  const running = isNumber(cost.running_usd) ? cost.running_usd : null;
  const grouped = new Map<string, Row[]>();
  for (const row of attempts) {
    // A report repair (`sol_report_repair`) belongs on the row of the stage it repairs; it is counted apart.
    const stage = text(row.original_stage) || text(row.stage);
    if (!stage) continue;
    grouped.set(stage, [...(grouped.get(stage) ?? []), row]);
  }
  return [...grouped].map(([stage, group]) => {
    const newest = group[group.length - 1]!;
    const state: StageState = newest.active
      ? (group.slice(0, -1).some(failed) ? "retrying" : "working")
      : failed(newest) ? "failed" : "done";
    const seconds = group.map((row) => secondsOf(row, now)).filter((value): value is number => value !== null);
    let used = 0;
    let complete = true;
    for (const row of group) {
      const counted = Object.keys(mapping(row.attributed_tokens)).length ? mapping(row.attributed_tokens) : mapping(row.tokens);
      for (const side of ["input_tokens", "output_tokens"]) {
        const [amount, whole] = known(counted[side]);
        used += amount ?? 0;
        complete &&= whole;
      }
    }
    // A repaired stage's cost is its own stage row and its repair's: separate stages of one ledger.
    const costs = [...new Set(group.map((row) => String(row.stage)))]
      .map((name) => mapping(spend.get(name)).spent_usd).filter(isNumber);
    if (newest.active && running !== null) costs.push(running);
    const working = state === "working" || state === "retrying";
    const runs = group.filter((row) => !repair(row));
    return {
      stage,
      job: text(newest.job) || stage,
      route: routeOf(newest),
      model: modelOf(newest)[0],
      level: modelOf(newest)[1],
      state,
      attempts: runs.length,
      failedAttempts: runs.filter(failed).length,
      repairs: group.length - runs.length,
      seconds: seconds.length ? seconds.reduce((a, b) => a + b, 0) : null,
      tokens: used,
      tokensComplete: complete,
      spentUsd: costs.length ? costs.reduce((a, b) => a + b, 0) : null,
      why: failed(newest) ? why(newest) : working && group.length > 1 ? why(group[group.length - 2]!) : "",
    };
  });
}

/**
 * The working stage retrying after a failed attempt: which attempt, how long, and why the last one
 * failed. A stage running again after a success (another review round) is not struggling.
 */
export function struggling(view: unknown, now = Date.now()): string {
  const working = stages(view, now).filter((row) => row.state === "working" || row.state === "retrying");
  const row = working[working.length - 1];
  if (!row || row.state !== "retrying") return "";
  const newest = rows(mapping(mapping(mapping(view).usage).accounting).attempts)
    .filter((attempt) => attempt.active && (text(attempt.original_stage) || text(attempt.stage)) === row.stage).pop();
  const parts = [row.job, newest && repair(newest) ? `report repair ${row.repairs}` : `attempt ${row.attempts}`];
  if (row.seconds !== null) parts.push(duration(row.seconds));
  // "last one answer rejected" read as a typo (2026-11-11).
  if (row.why) parts.push(row.why === "answer rejected" ? "last answer rejected" : `last one ${row.why}`);
  return parts.join(" · ");
}

/** The run's own accounted spend, cap and tokens, as the core wrote them; null before any. */
export function runTotal(view: unknown): RunTotal | null {
  const root = mapping(view);
  const cost = mapping(root.cost);
  const used = mapping(mapping(mapping(root.usage).accounting).tokens);
  const finished = isNumber(cost.spent_usd) ? cost.spent_usd : null;
  // What the running stage has spent so far; the total stays a floor (`complete` false) until it finishes.
  const spent = isNumber(cost.running_usd) ? (finished ?? 0) + cost.running_usd : finished;
  const [input, inputWhole] = known(used.input_tokens);
  const [output, outputWhole] = known(used.output_tokens);
  if (spent === null && !input && !output) return null;
  return {
    spentUsd: spent,
    inheritedUsd: isNumber(cost.inherited_usd) ? cost.inherited_usd : 0,
    capUsd: isNumber(cost.cap_usd) && cost.cap_usd > 0 ? cost.cap_usd : null,
    tokens: (input ?? 0) + (output ?? 0),
    costComplete: cost.complete === true && spent !== null,
    tokensComplete: inputWhole && outputWhole,
  };
}

/** "$1.21 of $5.00 · 651k tokens", each part with `+` while some of it is not reported yet. */
export function totalText(total: RunTotal | null, withCap = true): string {
  if (!total) return "";
  let spent = total.spentUsd === null ? "$?" : money(total.spentUsd);
  if (!total.costComplete && total.spentUsd !== null) spent += "+";
  if (withCap && total.capUsd !== null) spent += ` of ${money(total.capUsd)}`;
  return total.tokens ? `${spent} · ${tokens(total.tokens)}${total.tokensComplete ? "" : "+"} tokens` : spent;
}

/**
 * Several chats' run totals added together. Each run keeps its own ledger, so the sums do not
 * overlap, except what a run restored from a saved step carries over, which is left out.
 * A chat not read yet makes the total a floor.
 */
export function sessionTotal(totals: (RunTotal | null)[]): RunTotal | null {
  const shown = totals.filter((total): total is RunTotal => total !== null);
  if (!shown.length) return null;
  return {
    spentUsd: shown.some((total) => total.spentUsd === null) ? null
      : shown.reduce((sum, total) => sum + total.spentUsd! - total.inheritedUsd, 0),
    inheritedUsd: 0,
    capUsd: null,
    tokens: shown.reduce((sum, total) => sum + total.tokens, 0),
    costComplete: shown.every((total) => total.costComplete) && shown.length === totals.length,
    tokensComplete: shown.every((total) => total.tokensComplete) && shown.length === totals.length,
  };
}

/** A finished run's closing card: its result, what it checked and changed, and what it took. */
export function finishSummary(view: unknown): FinishSummary | null {
  const root = mapping(view);
  if (root.done !== true) return null;
  const files: string[] = [];
  const named = [
    ...rows(mapping(root.code_checkpoints).rows).flatMap((row) => (Array.isArray(row.changed_files) ? row.changed_files : [])),
    ...(Array.isArray(mapping(root.acceptance_tests).files) ? mapping(root.acceptance_tests).files as unknown[] : []),
  ];
  for (const path of named) if (typeof path === "string" && path && !files.includes(path)) files.push(path);
  const progress = mapping(root.progress);
  const requirements = mapping(progress.requirements);
  const counts = ["checked", "failed", "unchecked", "awaiting_review"].map((key) => requirements[key]);
  const total = counts.every((value) => value === undefined) ? 0 : counts.reduce<number>((sum, value) => sum + (isNumber(value) ? value : 0), 0);
  const ran = stages(view).map((row) => row.seconds).filter((value): value is number => value !== null);
  return {
    headline: text(progress.headline) || text(root.status) || "Finished",
    requirements: total ? { checked: isNumber(requirements.checked) ? requirements.checked : 0, total } : null,
    files,
    seconds: ran.length ? ran.reduce((a, b) => a + b, 0) : null,
    total: runTotal(view),
  };
}
