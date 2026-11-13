/**
 * The run header at the top of a chat: the pipeline, where the run is, and its details.
 *
 * The same reading as the terminal chat's `/details`, `/why` and `/spend` and its Pipeline panel
 * (`autocode_chat_panel`, `autocode_chat_recoveries`, `autocode_chat_spend`, `autocode_chat_cost`,
 * `autocode_chat_verification`, `autocode_chat_stage_policy`), from the public status view
 * alone: `summary`, `explanation`, `pipeline`, `recoveries`, `run_metrics`, `cost`, `usage`,
 * `efficiency`, `routes`, `escalations`, `route_assignments`, `chat.now`, `chat.log`,
 * `verification`, `delivery`, `stage_policy` and `stage_decisions`. Every figure and sentence is a
 * value the core wrote; this module only words and orders them. A stage's time, tokens and spend
 * are the meter's reading of that stage's own attempts (`core/meter.ts`); beyond that it never adds
 * up spend, decides anything about the run, or shows a command or flag. Job names come from the view (the pipeline's rows
 * carry the core's own names); the only words kept here are the planning path's coverage terms,
 * as the terminal chat words them. Display only: nothing here is an action.
 */

import { duration, money as stageMoney, runTotal, stages as meterStages, tokens as stageTokens, totalText, type StageRow } from "./meter";

type Row = Record<string, unknown>;

/** Where a pipeline job stands, as the view's `pipeline[].state` says. */
export type StepState = "working" | "next" | "ran" | "skipped" | "pending" | "not_run";

export interface PipelineStep {
  label: string;
  state: StepState;
  mark: string;
  /** Model, level, time, tokens, spend and runs, or why it was skipped, as one line (the row's tooltip). */
  detail: string;
  /** What the working stage is doing now (`chat.now`), empty otherwise. */
  now: string;
  /** The stage table's cells: the model without its provider, the level, and what the stage's own attempts used. */
  model: string;
  level: string;
  /** The whole route as the view names it (`provider/model level`), for a reader who needs it. */
  route: string;
  time: string;
  tokens: string;
  cost: string;
  /** How many times the stage launched, and how many of those failed (rejected, timed out, interrupted or exited nonzero). */
  attempts: number;
  failed: number;
  /** Report repairs: rewrites of a rejected report, counted apart from the stage's own launches. */
  repairs: number;
  /** The same in words, empty for a stage that launched once: "4 attempts, 3 failed", "ran 3 times", "2 report repairs". */
  runs: string;
  /** Why the newest failed attempt did not succeed, while the stage retries or after it failed. */
  why: string;
}

/** The stage table's last row: the run's working time, tokens and accounted spend, as the core totals them. */
export interface StageTotal {
  time: string;
  tokens: string;
  cost: string;
}

export interface DetailSection {
  id: "where" | "worktree" | "pipeline" | "wrong" | "spend" | "usage" | "routes" | "verification" | "path" | "decisions";
  title: string;
  summary: string;
  /** One line each; a line that starts with two spaces belongs to the line above it, and four to that one's own sub-line. */
  lines: string[];
}

export interface RunHeader {
  /** The view's `summary.state`: needs_you, working, done or stopped. */
  state: string;
  label: string;
  sentence: string;
  /** One row per stage: the view's pipeline, with any other job the run launched (a model preflight) where it ran. */
  pipeline: PipelineStep[];
  /** The stage table's total row, or null before the run has used anything. */
  total: StageTotal | null;
  /** What the working stage is doing now, always shown under the stage table; empty when nothing is said. */
  current: string;
  /** The collapsed header's short facts: how the spend splits and what went wrong. */
  facts: string[];
  /** Where the chat changes files, always shown: its own worktree and branch, or the person's folder. */
  place: string;
  sections: DetailSection[];
}

export const STEP_MARKS: Record<StepState, string> = { working: "●", next: "›", ran: "✓", skipped: "–", pending: "·", not_run: "·" };
const STEP_STATES = new Set(Object.keys(STEP_MARKS));

const mapping = (value: unknown): Row => (value && typeof value === "object" && !Array.isArray(value) ? value as Row : {});
const rows = (value: unknown): Row[] => (Array.isArray(value) ? value.filter((row) => row && typeof row === "object" && !Array.isArray(row)) as Row[] : []);
const isNumber = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value) && value >= 0;
const isCount = (value: unknown): value is number => Number.isInteger(value) && (value as number) >= 0;
const text = (value: unknown): string => (typeof value === "string" ? value.split(/\s+/).filter(Boolean).join(" ") : "");
const words = (value: unknown, fallback = "unknown"): string => (text(value) || fallback).replace(/_/g, " ");
const money = (value: unknown): string => (isNumber(value) ? `$${value.toFixed(2)}` : "unknown");
const percent = (value: unknown): string => (isNumber(value) ? `${Math.round(value * 100)}%` : "?");
const counted = (count: number, one: string, many: string): string => `${count} ${count === 1 ? one : many}`;
const FLAG = /(^|\s)--[a-z]/;
const capitalize = (value: string): string => value.charAt(0).toUpperCase() + value.slice(1);

function tokens(value: unknown): string {
  if (!isNumber(value)) return "unknown";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return String(Math.trunc(value));
}

/** `05:41:01` from a UTC ISO time, as the run log writes it; empty when unknown. */
function clock(value: unknown): string {
  return typeof value === "string" && value.length >= 19 ? value.slice(11, 19) : "";
}

// ---- Pipeline: the view's `pipeline` rows, with each job's own attempts ----------------------

/** A job's launched attempts as the meter reads them (`usage.accounting.attempts`), several stages of one job together. */
interface JobUse {
  model: string; level: string; seconds: number | null; tokens: number; tokensComplete: boolean; spent: number | null;
  costComplete: boolean;
  attempts: number; failed: number; repairs: number; why: string; active: boolean;
}

function jobUse(view: Row, now: number): Map<string, JobUse> {
  const used = new Map<string, JobUse>();
  for (const row of meterStages(view, now) as StageRow[]) {
    const seen = used.get(row.job);
    const add = (a: number | null, b: number | null) => (a === null ? b : b === null ? a : a + b);
    used.set(row.job, {
      // The newest attempt's route: the model a job that has no saved route actually launched on.
      model: row.model || seen?.model || "",
      level: row.level || seen?.level || "",
      seconds: add(seen?.seconds ?? null, row.seconds),
      tokens: (seen?.tokens ?? 0) + row.tokens,
      tokensComplete: (seen?.tokensComplete ?? true) && row.tokensComplete,
      spent: add(seen?.spent ?? null, row.spentUsd),
      costComplete: (seen?.costComplete ?? true) && row.costComplete,
      attempts: (seen?.attempts ?? 0) + row.attempts,
      failed: (seen?.failed ?? 0) + row.failedAttempts,
      repairs: (seen?.repairs ?? 0) + row.repairs,
      why: row.why || seen?.why || "",
      active: Boolean(seen?.active) || row.state === "working" || row.state === "retrying",
    });
  }
  return used;
}

/** What each working agent is doing now (`chat.now`), by the job it names first (`Builder · task M2`). */
function nowFor(view: Row, label: string): string {
  const found = rows(mapping(view.chat).now).filter((row) => typeof row.agent === "string" && (row.agent === label || row.agent.startsWith(`${label} ·`)));
  return found.length ? text(found[found.length - 1]!.text) : "";
}

/** The newest message the working job wrote (`chat.log`), as the terminal's Stage panel shows it. */
function latestFor(view: Row, label: string): string {
  const found = rows(mapping(view.chat).log).filter((row) => row.kind === "message" && typeof row.agent === "string"
    && (row.agent === label || row.agent.startsWith(`${label} ·`)) && text(row.text));
  return found.length ? text(found[found.length - 1]!.text).replace(/ \[partial\]$/, "") : "";
}

const shortModel = (model: string): string => (model.includes("/") ? model.slice(model.lastIndexOf("/") + 1) : model);

/** A stage's row from the view's pipeline state and the meter's reading of its own attempts. */
function stepOf(label: string, state: StepState, row: Row, use: JobUse | undefined, view: Row): PipelineStep {
  const model = text(row.model) || use?.model || "";
  const level = text(row.reasoning_effort) || use?.level || "";
  const step: PipelineStep = {
    label, state, mark: STEP_MARKS[state], detail: "", now: state === "working" ? nowFor(view, label) : "",
    model: shortModel(model), level, route: [model, level].filter(Boolean).join(" "),
    time: "", tokens: "", cost: "", attempts: use?.attempts ?? 0, failed: use?.failed ?? 0, repairs: use?.repairs ?? 0, runs: "", why: "",
  };
  if (state === "skipped") {
    step.detail = "skipped" + (text(row.reason) ? `: ${text(row.reason)}` : "");
    return step;
  }
  if (use?.seconds && use.seconds >= 1) step.time = duration(use.seconds);
  if (use?.tokens) step.tokens = `${stageTokens(use.tokens)}${use.tokensComplete ? "" : "+"}`;
  const spent = use ? use.spent : (isNumber(row.spent_usd) ? row.spent_usd : null);
  const complete = use ? use.costComplete : row.spent_complete !== false;
  if (spent !== null && (spent > 0 || use || typeof row.spent_complete === "boolean")) {
    step.cost = `${stageMoney(spent)}${complete ? "" : "+"}`;
  }
  // A stage that ran again after succeeding (a second plan-review round) is not a retry, and a report
  // repair only rewrote a rejected report: it is never another run of the stage.
  const again = use && use.attempts > 1 ? (use.failed ? `${use.attempts} attempts, ${use.failed} failed` : `ran ${use.attempts} times`) : "";
  const repaired = use?.repairs ? counted(use.repairs, "report repair", "report repairs") : "";
  step.runs = [again, repaired].filter(Boolean).join(", ");
  if (use?.failed && use.why) step.why = use.why;
  step.detail = [step.model, step.level, step.time, step.tokens && `${step.tokens} tokens`, step.cost, step.runs].filter(Boolean).join(" · ");
  return step;
}

/**
 * One row per stage: the view's pipeline in its order, and any other job the run launched (a model
 * preflight, an Investigator) at the place it first ran, so every launched attempt is on the table once.
 */
export function pipelineSteps(view: Row, now = Date.now()): PipelineStep[] {
  const used = jobUse(view, now);
  const listed = rows(view.pipeline).filter((row) => text(row.label) && STEP_STATES.has(row.state as string));
  const named = new Set(listed.map((row) => text(row.label)));
  const order = [...used.keys()];
  const extra = (job: string): PipelineStep => {
    const use = used.get(job)!;
    return stepOf(job, use.active && view.done !== true ? "working" : "ran", {}, use, view);
  };
  const steps: PipelineStep[] = [];
  const placed = new Set<string>();
  for (const row of listed) {
    const label = text(row.label);
    // A job the pipeline does not list, launched before this one, goes in front of it.
    const at = order.indexOf(label);
    if (at >= 0) for (const job of order.slice(0, at)) if (!named.has(job) && !placed.has(job)) { steps.push(extra(job)); placed.add(job); }
    steps.push(stepOf(label, row.state as StepState, row, used.get(label), view));
  }
  // The rest ran after every listed stage that ran: after the last stage that has run or works now.
  const rest = order.filter((job) => !named.has(job) && !placed.has(job)).map(extra);
  if (rest.length) {
    let last = -1;
    steps.forEach((step, index) => { if (step.state === "ran" || step.state === "working") last = index; });
    steps.splice(last + 1, 0, ...rest);
  }
  return steps;
}

/** The detail beside a stage in Show details: what the table leaves out, its whole route and its attempts. */
function stageDetail(step: PipelineStep): string {
  if (step.state === "skipped") return `${step.label}: ${step.detail}`;
  const parts: string[] = [];
  if (step.route) parts.push(step.state === "pending" || step.state === "next" || step.state === "not_run" ? `will run on ${step.route}` : step.route);
  if (step.runs) parts.push(step.why ? `${step.runs}, the last ${step.why === "answer rejected" ? "with its answer rejected" : step.why}` : step.runs);
  return parts.length ? `${step.label}: ${parts.join("; ")}` : step.label;
}

function pipelineSection(view: Row, steps: PipelineStep[]): DetailSection | null {
  if (!steps.length) return null;
  const working = steps.find((step) => step.state === "working");
  const next = steps.find((step) => step.state === "next");
  const ran = steps.filter((step) => step.state === "ran").length;
  const summary = working ? `${working.label} working` : next ? `${next.label} next` : `${ran} of ${steps.length} ran`;
  const lines: string[] = [];
  for (const step of steps) {
    lines.push(`${step.mark} ${stageDetail(step)}`);
    if (step.state !== "working") continue;
    if (step.now) lines.push(`  Now: ${step.now}`);
    const latest = latestFor(view, step.label);
    if (latest) lines.push(`  Latest: ${latest}`);
  }
  return { id: "pipeline", title: "Stages", summary, lines };
}

/** What the working stage is doing now, under the stage table; its model and usage are on its row. */
function currentLine(steps: PipelineStep[]): string {
  const step = steps.find((row) => row.state === "working" && row.now);
  return step ? `${step.label}: ${step.now}` : "";
}

/** The table's total row: the run's working time and its accounted tokens and spend, each a floor while incomplete. */
function stageTotal(view: Row): StageTotal | null {
  const seconds = executionSeconds(view);
  const total = runTotal(view);
  if (seconds === null && !total) return null;
  const cost = total?.spentUsd === null || !total ? "" : `${stageMoney(total.spentUsd)}${total.costComplete ? "" : "+"}`;
  const used = total?.tokens ? `${stageTokens(total.tokens)}${total.tokensComplete ? "" : "+"}` : "";
  return { time: seconds === null ? "" : duration(seconds), tokens: used, cost };
}

// ---- Where the run is: `summary` and `explanation` -----------------------------------------

function where(view: Row): { state: string; label: string; sentence: string; section: DetailSection } {
  const summary = mapping(view.summary);
  const progress = mapping(view.progress);
  const state = text(summary.state) || (view.done === true ? "done" : "working");
  const label = text(summary.label) || capitalize(words(view.status, "Starting").toLowerCase());
  const sentence = text(summary.sentence) || text(progress.headline) || text(progress.line);
  const lines = [sentence];
  // `what_it_means` is said for a person; `what_happened` names the internal status and
  // `what_the_command_does` names CLI flags, which a client never shows.
  const means = text(mapping(view.explanation).what_it_means);
  if (means && means !== sentence) lines.push(means);
  const reason = text(mapping(view.needs).reason);
  if (reason && !lines.includes(reason) && state === "needs_you") lines.push(`Waiting for: ${reason}`);
  if (view.done !== true && String(view.status ?? "").startsWith("PAUSED")) {
    // A stopped step: the core's own reason and next action, then the cause it saved. A line that
    // names a flag is left out, since a client never shows a command to type.
    const notice = text(mapping(view.chat).notice);
    if (notice && !lines.includes(notice)) lines.push(notice);
    const saved = text(mapping(view.recovery).saved_reason);
    if (saved && !FLAG.test(saved) && !lines.some((line) => line.includes(saved))) lines.push(`Recorded reason: ${saved}`);
  }
  return { state, label, sentence, section: { id: "where", title: "Where the run is", summary: label, lines: lines.filter(Boolean) } };
}

// ---- Where it changes files: `worktree` -------------------------------------------------------

function worktree(view: Row): { label: string; section: DetailSection | null } {
  const place = mapping(view.worktree);
  const label = text(place.label);
  if (!label) return { label: "", section: null };
  const lines = [text(place.sentence)];
  const path = text(place.path);
  const folder = text(place.folder);
  if (path) lines.push(place.kind === "worktree" ? `Worktree: ${path}` : `Folder: ${path}`);
  lines.push(`Branch: ${text(place.branch) || "none (detached HEAD)"}`);
  if (place.kind === "moving") lines.push(`From planning on: ${text(place.moves_to_branch) || "a new branch named from the task"}`);
  if (folder && folder !== path) lines.push(`Your folder: ${folder}`);
  return { label, section: { id: "worktree", title: "Where it works", summary: label, lines: lines.filter(Boolean) } };
}

// ---- What went wrong: `recoveries` (the terminal's /why) ------------------------------------

function recoveries(view: Row): Row[] {
  return rows(view.recoveries).filter((row) => typeof row.id === "string" && typeof row.headline === "string" && typeof row.action === "string");
}

function wrongSection(view: Row): DetailSection | null {
  const found = recoveries(view);
  if (!found.length) return null;
  const lines: string[] = [];
  for (const row of found) {
    lines.push([clock(row.at), text(row.headline)].filter(Boolean).join(" "));
    if (text(row.problem)) lines.push(`  What went wrong: ${text(row.problem)}`);
    lines.push(`  Then: ${text(row.action)}`);
    if (text(row.detail)) lines.push(`  What it found: ${text(row.detail)}`);
  }
  return { id: "wrong", title: "What went wrong", summary: `${found.length} so far, each handled by AutoCode`, lines };
}

// ---- Spend: `run_metrics` (the terminal's /spend) -------------------------------------------

function metricsOf(view: Row): Row | null {
  const metrics = mapping(view.run_metrics);
  return metrics.schema === 1 && isNumber(metrics.spent_usd) && metrics.spent_usd > 0 ? metrics : null;
}

function jobLine(row: Row): string {
  const again = isCount(row.attempts) && row.attempts > 1 ? `, ${row.attempts} runs` : "";
  const known = row.complete === true ? "" : " (known part)";
  // Each job's own time beside its share of the cost, so a share is never read as a share of the time.
  let took = isNumber(row.seconds) ? duration(row.seconds) : "";
  if (took && row.seconds_complete !== true) took += " so far";
  return `${text(row.job) || "Job unreported"} ${money(row.spent_usd)}${known} · ${percent(row.share)} of cost${took ? ` · ${took}` : ""}${again}`;
}

function cacheLine(tokenRow: Row): string | null {
  if (!isNumber(tokenRow.input) || !tokenRow.input) return null;
  const parts = [`cache reads ${percent(tokenRow.cache_read_share)} of input`];
  if (isNumber(tokenRow.cache_write_share)) parts.push(`cache writes ${percent(tokenRow.cache_write_share)}`);
  return parts.join(", ") + (tokenRow.complete === true ? "" : " (known part)");
}

function wasteLine(waste: Row): string {
  const failed = waste.failed_attempts;
  let line = !isCount(failed) || !failed ? "No failed attempts"
    : `${failed} failed attempt${failed === 1 ? "" : "s"} ${money(waste.failed_usd)}${waste.failed_usd_complete === true ? "" : " known"}`;
  // A report repair rewrote a rejected report; it is not another run of the job.
  const repeated = rows(waste.repeated_jobs);
  const again = repeated.map((row) => [text(row.job), isCount(row.runs) ? row.runs : row.attempts] as const)
    .filter(([, runs]) => isCount(runs) && runs > 1).map(([job, runs]) => `${job} ×${runs}`);
  const repaired = repeated.filter((row) => isCount(row.report_repairs) && row.report_repairs)
    .map((row) => `${text(row.job)} ×${row.report_repairs}`);
  if (again.length) line += "; ran again: " + again.join(", ");
  if (repaired.length) line += "; report repairs: " + repaired.join(", ");
  return line;
}

function checked(produced: Row): string[] {
  const parts: string[] = [];
  if (isCount(produced.criteria_requested) && produced.criteria_requested) {
    parts.push(`${isCount(produced.criteria_accepted) ? produced.criteria_accepted : 0}/${produced.criteria_requested} criteria accepted`);
  }
  if (isCount(produced.fail_to_pass_tests) && produced.fail_to_pass_tests) parts.push(`${counted(produced.fail_to_pass_tests, "test fails", "tests fail")} before, pass after`);
  if (isCount(produced.clean_replay_checks) && produced.clean_replay_checks) parts.push(`${counted(produced.clean_replay_checks, "check", "checks")} rerun clean`);
  if (isCount(produced.mutants_tried) && produced.mutants_tried) parts.push(`${isCount(produced.mutants_killed) ? produced.mutants_killed : 0}/${produced.mutants_tried} broken lines caught`);
  if (isCount(produced.blocking_findings) && produced.blocking_findings) parts.push(counted(produced.blocking_findings, "blocking review finding", "blocking review findings"));
  if (isCount(produced.problems_handled) && produced.problems_handled) parts.push(`${counted(produced.problems_handled, "problem", "problems")} handled`);
  return parts;
}

/**
 * The short spend line: what share of the cost (never of the time) went to the Builder, planning and the
 * rest, or the total across jobs. "Builder 18% · checking 82%" counted planning as checking (#3268).
 */
export function spendSummary(view: Row): string | null {
  const metrics = metricsOf(view);
  if (!metrics) return null;
  const single = mapping(metrics.single_model);
  if (isNumber(single.builder_share) && single.builder_share <= 1) {
    // Planning is named apart from checking (#3268); an older core gives no split, so the rest is "everything else".
    const builder = `Cost: Builder ${percent(single.builder_share)}`;
    if (!isNumber(single.planning_share) || !isNumber(single.review_share)) {
      return `${builder} · everything else ${percent(1 - single.builder_share)}`;
    }
    return `${builder}${single.planning_share ? ` · planning ${percent(single.planning_share)}` : ""} · checking ${percent(single.review_share)}`;
  }
  return `${money(metrics.spent_usd)}${metrics.complete === true ? "" : "+"} across ${rows(metrics.by_job).length} jobs`;
}

function spendSection(view: Row): DetailSection | null {
  const metrics = metricsOf(view);
  if (!metrics) return null;
  const whole = metrics.complete === true ? "" : " (known part; some usage is not recorded)";
  const lines = [`Where the money went: ${money(metrics.spent_usd)}${whole}`];
  for (const row of rows(metrics.by_job)) if (isNumber(row.spent_usd) && row.spent_usd > 0) lines.push(`  ${jobLine(row)}`);
  const tokenRow = mapping(metrics.tokens);
  const split = ([["input", "input"], ["new", "new"], ["cache_write", "cache write"], ["cache_read", "cache read"], ["output", "output"]] as const)
    .map(([key, label]) => `${label} ${tokens(tokenRow[key])}`).join(", ");
  lines.push(`  Tokens: ${split}${tokenRow.complete === true ? "" : " (known part)"}`);
  const cache = cacheLine(tokenRow);
  if (cache) lines.push(`  ${capitalize(cache)}`);
  const waste = mapping(metrics.waste);
  lines.push("Spent again", `  ${wasteLine(waste)}`);
  if (isNumber(waste.report_repair_usd) && waste.report_repair_usd) lines.push(`  Report repairs ${money(waste.report_repair_usd)}`);
  const single = mapping(metrics.single_model);
  lines.push("Compared with one model building it alone");
  lines.push(`  Builder ${money(single.builder_usd)} (${percent(single.builder_share)}); ${isNumber(single.planning_usd)
    ? `planning ${money(single.planning_usd)}; checking ${money(single.review_usd)}`
    : `planning and checking ${money(single.checking_usd)}`}`);
  const bought = checked(mapping(single.produced));
  lines.push(`  What that bought: ${bought.length ? bought.join("; ") : "nothing recorded yet"}`);
  if (text(single.basis)) lines.push(`  ${text(single.basis)}.`);
  return { id: "spend", title: "Spend", summary: spendSummary(view) ?? money(metrics.spent_usd), lines };
}

// ---- Cost and usage: `cost`, `usage.accounting`, `efficiency` (the terminal's /details) ------------

/** An accounting quantity (`{value, known, complete}` or a bare number), with "known" while it is incomplete. */
function quantity(value: unknown, asMoney = false): string {
  let amount: unknown = value;
  let suffix = "";
  if (value && typeof value === "object") {
    const row = value as Row;
    amount = row.complete === true ? row.value : row.known;
    suffix = row.complete === true ? "" : " known";
  }
  if (!isNumber(amount)) return "unknown";
  return (asMoney ? stageMoney(amount) : Math.round(amount).toLocaleString("en-US")) + suffix;
}

/** Why the accounted total is a floor, in the terminal's words (`autocode_chat_cost._gaps`). */
function accountingGaps(view: Row): string[] {
  const cost = mapping(view.cost);
  const accounting = mapping(mapping(view.usage).accounting);
  const gaps: string[] = [];
  if (isCount(cost.unpriced_stages) && cost.unpriced_stages) gaps.push(`${cost.unpriced_stages} unpriced`);
  if (isCount(accounting.active_attempts) && accounting.active_attempts) gaps.push(`${accounting.active_attempts} running`);
  const pending = accounting.parallel_workers_pending;
  if (Array.isArray(pending) && pending.length) gaps.push(counted(pending.length, "worker pending", "workers pending"));
  if (accounting.complete === false) gaps.push("attempt coverage incomplete");
  else if (accounting.complete !== true) gaps.push("attempt coverage unavailable");
  return gaps;
}

const TOKEN_LABELS = [
  ["input_tokens", "Input"],
  ["output_tokens", "Output"],
  ["cached_input_tokens", "Cache reads (in input)"],
  ["cache_write_tokens", "Cache writes (in input)"],
  ["reasoning_output_tokens", "Reasoning (in output)"],
] as const;

/** The token kinds a row reports; one it reports as nothing so far is left out. */
function tokenRows(row: Row): [string, string][] {
  return TOKEN_LABELS.map(([key, label]): [string, string] => [label, quantity(row[key])])
    .filter(([, shown]) => shown !== "unknown" && shown !== "0 known");
}

function attemptLines(row: Row, number: number): string[] {
  const route = mapping(row.launch_route);
  const model = text(route.model) || text(row.model) || "model unknown";
  const level = text(row.reasoning_effort) || text(route.reasoning_effort);
  const provider = text(route.provider) || text(mapping(row.execution_identity).provider);
  const flags = (["active", "rejected", "interrupted", "timed_out"] as const).filter((key) => row[key] === true)
    .map((key) => (key === "active" ? "running" : key.replace("_", " ")));
  if (row.report_only === true) flags.push("report repair");
  if (Number.isInteger(row.exit_code) && row.exit_code !== 0) flags.push(`exit ${row.exit_code}`);
  if (!flags.length) flags.push(row.finished_at ? "finished" : "outcome unavailable");
  const head = [text(row.job) || "Job unreported", flags.join(", ")];
  if (isNumber(row.duration_seconds) && row.duration_seconds >= 1) head.push(duration(row.duration_seconds));
  const lines = [`${number}. ${head.join(" · ")}`, `  ${[model, level, provider].filter(Boolean).join(" · ")}`];
  const choice = mapping(row.effort_choice);
  if (choice.raised && text(choice.saved) && text(choice.text)) lines.push(`  One level above your ${text(choice.saved)}: ${text(choice.text)}`);
  if (choice.lowered && text(choice.ceiling)) {
    if (choice.reason === "small_clear") lines.push(`  One level below your ${text(choice.ceiling)}: the task is small and clear`);
    else if (text(choice.text)) lines.push(`  Below your ${text(choice.ceiling)}: ${text(choice.text)}`);
  }
  const used = Object.keys(mapping(row.attributed_tokens)).length ? mapping(row.attributed_tokens) : mapping(row.tokens);
  const shown = tokenRows(used);
  if (shown.length) lines.push(`  Tokens: ${shown.map(([label, amount]) => `${label.toLowerCase()} ${amount}`).join(", ")}`);
  const reported = "attributed_reported_usd" in row ? quantity(row.attributed_reported_usd, true)
    : isNumber(row.reported_cost_usd) ? stageMoney(row.reported_cost_usd) : "unknown";
  if (reported !== "unknown" && reported !== "$0 known") lines.push(`  Provider-reported: ${reported}`);
  return lines;
}

/** Wall time by kind of work, from `efficiency.time` (or the older top-level copy). */
function wallTimes(efficiency: Row): Row {
  const time = mapping(efficiency.time);
  for (const key of ["exclusive_wall_seconds_by_category", "observed_exclusive_wall_seconds_by_category"]) {
    const found = mapping(time[key]);
    if (Object.keys(found).length) return found;
    const old = mapping(efficiency[key]);
    if (Object.keys(old).length) return old;
  }
  return {};
}

/** The run's summed execution time as the core recorded it, or null. */
export function executionSeconds(view: Row): number | null {
  const efficiency = mapping(view.efficiency);
  for (const value of [mapping(efficiency.time).summed_execution_seconds, efficiency.summed_execution_seconds]) if (isNumber(value) && value > 0) return value;
  return null;
}

function usageSection(view: Row): DetailSection | null {
  const cost = mapping(view.cost);
  const usage = mapping(view.usage);
  const accounting = mapping(usage.accounting);
  if (!Object.keys(cost).length && !Object.keys(usage).length) return null;
  const gaps = accountingGaps(view);
  const complete = cost.complete === true && isNumber(cost.spent_usd) && !gaps.length;
  const accounted = isNumber(cost.spent_usd) ? stageMoney(cost.spent_usd) : "unknown";
  const lines = [`Accounted ${complete ? "total" : "so far"}: ${accounted}${complete ? "" : ` (${gaps.join(", ") || "coverage unknown"})`}`];
  if (isNumber(cost.running_usd) && cost.running_usd > 0) lines.push(`  Running stage so far: ${stageMoney(cost.running_usd)}`);
  if (isNumber(cost.estimated_usd)) lines.push(`  Price-table estimate: ${stageMoney(cost.estimated_usd)}${text(cost.prices_as_of) ? ` (prices as of ${text(cost.prices_as_of)})` : ""}`);
  const reported = mapping(accounting.cost).reported_usd ?? cost.reported_usd;
  const reportedText = quantity(reported, true);
  if (reportedText !== "unknown" && reportedText !== "$0 known") lines.push(`  Provider-reported: ${reportedText}`);
  // The core's comparison at its own frozen rates, shown only by an older core with no dated
  // price-table estimate: beside that estimate it is a second, different price for the same tokens.
  const historical = quantity(mapping(accounting.cost).historical_estimated_usd, true);
  if (!isNumber(cost.estimated_usd) && historical !== "unknown" && historical !== "$0 known") {
    lines.push(`  Historical comparison estimate: ${historical}`);
  }
  if (isNumber(cost.inherited_usd)) lines.push(`  Carried over from a restored step: ${stageMoney(cost.inherited_usd)}`);
  // An older core without `cost` says nothing about a cap: that is not "none" (nor a free run).
  if (isNumber(cost.cap_usd) && cost.cap_usd > 0) lines.push(`  Spending cap: ${stageMoney(cost.cap_usd)}`);
  else if (cost.cap_usd === null || cost.cap_usd === 0) lines.push("  Spending cap: none");
  else if (Object.keys(cost).length) lines.push("  Spending cap: not reported");
  for (const row of rows(mapping(cost.breakdown).by_billing)) {
    if (row.billing !== "subscription") continue;
    lines.push(`  On your subscription: ${stageMoney(isNumber(row.spent_usd) ? row.spent_usd : null)} as API-equivalent pricing, not an invoice`);
  }
  const tokenRow = Object.keys(accounting).length ? mapping(accounting.tokens) : mapping(usage.tokens);
  const tokenLines = tokenRows(tokenRow);
  if (tokenLines.length) lines.push("Tokens", ...tokenLines.map(([label, amount]) => `  ${label}: ${amount}`));
  const efficiency = mapping(view.efficiency);
  const wall = wallTimes(efficiency);
  const categories = Object.entries(mapping(efficiency.by_category)).sort(([a], [b]) => a.localeCompare(b));
  const timeLines: string[] = [];
  for (const [name, value] of categories) {
    const data = mapping(value);
    const parts: string[] = [];
    if (isCount(data.attempts) && data.attempts) parts.push(counted(data.attempts, "attempt", "attempts"));
    const usedTokens = mapping(data.tokens);
    const [input, output] = [quantity(usedTokens.input_tokens), quantity(usedTokens.output_tokens)];
    if (input !== "unknown" && input !== "0") parts.push(`${input} in`);
    if (output !== "unknown" && output !== "0") parts.push(`${output} out`);
    if (isNumber(wall[name]) && wall[name] >= 1) parts.push(duration(wall[name] as number));
    if (parts.length) timeLines.push(`  ${capitalize(words(name))}: ${parts.join(", ")}`);
  }
  if (timeLines.length) lines.push("Time and tokens by kind of work", ...timeLines);
  const seconds = executionSeconds(view);
  if (seconds !== null) lines.push(`Total execution time: ${duration(seconds)}`);
  const attempts = rows(accounting.attempts).filter((row) => row.runner_owned !== true && row.usage_basis !== "runner");
  if (attempts.length) {
    lines.push("Attempts");
    attempts.forEach((row, index) => lines.push(...attemptLines(row, index + 1).map((line) => `  ${line}`)));
    if (attempts.some((row) => row.unobserved_usage_possible === true || row.request_coverage_complete === false)) {
      lines.push("  Some attempts' usage may be unreported.");
    }
  }
  lines.push("Estimates and provider reports are not an invoice.");
  // The same total as the toolbar's cost line: accounted spend with the running stage's so far.
  const summary = totalText(runTotal(view)) || `${accounted}${complete ? "" : "+"}`;
  return { id: "usage", title: "Cost and usage", summary, lines };
}

// ---- Routes: `routes`, `escalations`, `route_assignments` -------------------------------------

function routeText(value: unknown): string {
  const route = mapping(value);
  return [text(route.model) || "model unknown", text(route.reasoning_effort), text(route.provider) || text(route.engine)].filter(Boolean).join(" · ");
}

function routesSection(view: Row): DetailSection | null {
  const lines: string[] = [];
  for (const value of Object.values(mapping(view.routes))) {
    const route = mapping(value);
    if (Object.keys(route).length) lines.push(`${text(route.job) || "Job unreported"}: ${routeText(route)}`);
  }
  for (const [key, title] of [["escalations", "Moved up"], ["route_assignments", "Route changes"]] as const) {
    const found = rows(view[key]);
    if (!found.length) continue;
    lines.push(title);
    for (const row of found) {
      const from = row.from && typeof row.from === "object" ? routeText(row.from) : words(row.from);
      const to = row.to && typeof row.to === "object" ? routeText(row.to) : words(row.to);
      lines.push(`  ${text(row.job) || "Job unreported"}: ${from} → ${to}; ${words(row.trigger || row.via, "reason unreported")}`);
      if (text(row.outcome)) lines.push(`  Outcome: ${words(row.outcome)}`);
    }
  }
  if (!lines.length) return null;
  return { id: "routes", title: "Models for the next attempts", summary: `${Object.keys(mapping(view.routes)).length} routes`, lines };
}

// ---- Verification: `verification`, `delivery`, `review_result` -------------------------------

function reviewResult(view: Row): Row {
  if (view.workflow !== "review") return {};
  const result = mapping(view.review_result);
  return Object.keys(result).length ? result : mapping(mapping(view.evidence).workflow_result);
}

function gate(view: Row): boolean | null {
  const verified = mapping(view.delivery).verified_complete;
  if (typeof verified === "boolean") return verified;
  const current = mapping(mapping(view.efficiency).delivery).current_completion;
  return typeof current === "boolean" ? current : null;
}

function verificationSection(view: Row): DetailSection | null {
  const result = reviewResult(view);
  const review = Object.keys(result).length > 0;
  // Before a run finishes its delivery is always "not verified", which says nothing yet.
  const settled = review || view.done === true || view.status === "TASK_COMPLETE" || view.status === "COMPLETE";
  if (!settled) return null;
  if (review) {
    const lines = [text(result.summary) || "No summary recorded.", `Review inspection: ${words(result.freshness)}`, `Report: ${words(result.artifact)}`];
    for (const reason of Array.isArray(result.reasons) ? result.reasons : []) if (text(reason)) lines.push(text(reason));
    return { id: "verification", title: "Review result", summary: `Recorded ${words(result.outcome)}`, lines };
  }
  const report = mapping(view.verification);
  const verdict = gate(view);
  const summary = verdict === true ? "Verified by AutoCode's completion check" : verdict === false ? "Not verified" : "Unknown";
  const lines = [`Report: ${words(report.freshness)}`];
  for (const reason of Array.isArray(report.reasons) ? report.reasons : []) if (text(reason)) lines.push(text(reason));
  for (const row of rows(report.coverage)) {
    const pending = row.human_acceptance_pending === true ? "; your acceptance pending" : "";
    lines.push(`${words(row.id)}: recorded ${words(row.recorded_status)}; coverage ${words(row.state)}${pending}`);
    if (text(row.criterion)) lines.push(`  ${text(row.criterion)}`);
    for (const ref of Array.isArray(row.evidence_refs) ? row.evidence_refs : []) if (text(ref)) lines.push(`  Evidence: ${text(ref)}`);
  }
  if (mapping(view.code_checkpoints).working) lines.push("Working changes still need verification.");
  const check = mapping(view.runner_check);
  if (text(check.summary)) lines.push(`Runner check: ${text(check.summary)}`);
  return { id: "verification", title: "Verification", summary, lines };
}

// ---- Planning path and stage decisions: `stage_policy`, `stage_decisions` --------------------

/** How `stage_policy` names what covers an omitted stage, in a person's words (autocode_chat_stage_policy.COVERAGE). */
const COVERAGE: Record<string, string> = {
  "runner:compact_draft": "the runner's draft from the diagnosis",
  review_plan: "the Plan Reviewer",
  "person:approve_goal": "your approval of the plan",
  "runner:regression_proof": "the regression proof",
  requirements_gather: "Requirements' own answer",
};

function covered(row: Row): string {
  return (Array.isArray(row.covered_by) ? row.covered_by : []).map(text).filter(Boolean).map((item) => COVERAGE[item] ?? item).join(", ");
}

function pathSection(view: Row): DetailSection | null {
  if (!view.stage_policy || typeof view.stage_policy !== "object" || Array.isArray(view.stage_policy)) return null;
  const record = mapping(view.stage_policy);
  const path = record.path === "compact" || record.path === "full" ? record.path : "unknown";
  const lines: string[] = [];
  for (const row of rows(record.decisions)) {
    const stage = row.stage === "draft_plan" ? "Planner draft" : text(row.stage) || "stage unknown";
    lines.push(`${stage}: ${text(row.decision) || "unknown"}${text(row.reason) ? ` (${text(row.reason)})` : ""}`);
    if (covered(row)) lines.push(`  Covered by: ${covered(row)}`);
  }
  const reasons = (Array.isArray(record.reasons) ? record.reasons : []).map(text).filter(Boolean);
  if (reasons.length) lines.push("Kept the full path because:", ...reasons.map((reason) => `  ${reason}`));
  const fallback = mapping(record.fallback);
  if (Object.keys(fallback).length) {
    lines.push(`Fell back at ${text(fallback.from) || "a review"}: ${text(fallback.reason) || "unknown reason"}`
      + (fallback.review_call_refunded === true ? "; the review call was refunded" : ""));
  }
  const summary = path === "compact" ? "Compact: some planning left out" : path === "full" ? "Full: every stage runs" : "Unknown";
  return { id: "path", title: "Planning path", summary, lines };
}

function decisionsSection(view: Row): DetailSection | null {
  const found = rows(view.stage_decisions);
  if (!found.length) return null;
  const lines: string[] = [];
  let ran = 0;
  let skipped = 0;
  for (const row of found) {
    const label = text(row.label) || text(row.stage) || "stage unknown";
    if (row.decision === "skip") {
      skipped += 1;
      lines.push(`${label}: skipped${text(row.reason) ? ` (${text(row.reason)})` : ""}`);
      if (covered(row)) lines.push(`  Covered by: ${covered(row)}`);
      continue;
    }
    ran += 1;
    const times = isCount(row.launches) && row.launches > 1 ? `${row.launches} times` : "once";
    const choice = mapping(row.effort_choice);
    let level = "";
    if (choice.lowered && text(choice.level) && text(choice.ceiling)) level = `, at ${text(choice.level)}, below your ${text(choice.ceiling)}`;
    else if (choice.raised && text(choice.level) && text(choice.saved)) level = `, at ${text(choice.level)}, above your ${text(choice.saved)}`;
    lines.push(`${label}: ran ${times}${level}`);
  }
  return { id: "decisions", title: "Stages that ran", summary: [`${ran} ran`, skipped ? `${skipped} skipped` : ""].filter(Boolean).join(" · "), lines };
}

// ---- The header ---------------------------------------------------------------------------

/** The run header for one status view, or null before a run has a view. */
export function runHeader(view: unknown, at = Date.now()): RunHeader | null {
  const source = mapping(view);
  if (!Object.keys(source).length) return null;
  const steps = pipelineSteps(source, at);
  const now = where(source);
  const facts: string[] = [];
  const spend = spendSummary(source);
  if (spend) facts.push(spend);
  // The run's working time, tokens and spend are the stage table's total row.
  const wrong = recoveries(source).length;
  if (wrong) facts.push(`${wrong} ${wrong === 1 ? "thing" : "things"} went wrong, handled`);
  const place = worktree(source);
  const sections = [
    now.section,
    place.section,
    pipelineSection(source, steps),
    wrongSection(source),
    spendSection(source),
    usageSection(source),
    routesSection(source),
    verificationSection(source),
    pathSection(source),
    decisionsSection(source),
  ].filter((section): section is DetailSection => section !== null);
  return {
    state: now.state, label: now.label, sentence: now.sentence, pipeline: steps, total: steps.length ? stageTotal(source) : null,
    current: currentLine(steps), facts, place: place.label, sections,
  };
}
