/**
 * The run header at the top of a chat: the pipeline, where the run is, and its details.
 *
 * The same reading as the terminal chat's `/details`, `/why` and `/spend` and its Pipeline panel
 * (`autocode_chat_panel`, `autocode_chat_recoveries`, `autocode_chat_spend`,
 * `autocode_chat_verification`, `autocode_chat_stage_policy`), from the public status view
 * alone: `summary`, `explanation`, `pipeline`, `recoveries`, `run_metrics`, `verification`,
 * `delivery`, `stage_policy` and `stage_decisions`. Every figure and sentence is a value the
 * core wrote; this module only words and orders them. It never adds up spend, decides anything
 * about the run, or shows a command or flag. Job names come from the view (the pipeline's rows
 * carry the core's own names); the only words kept here are the planning path's coverage terms,
 * as the terminal chat words them. Display only: nothing here is an action.
 */

type Row = Record<string, unknown>;

/** Where a pipeline job stands, as the view's `pipeline[].state` says. */
export type StepState = "working" | "next" | "ran" | "skipped" | "pending" | "not_run";

export interface PipelineStep {
  label: string;
  state: StepState;
  mark: string;
  /** Model, level and spend, or why it was skipped. */
  detail: string;
}

export interface DetailSection {
  id: "where" | "pipeline" | "wrong" | "spend" | "verification" | "path" | "decisions";
  title: string;
  summary: string;
  /** One line each; a line that starts with two spaces belongs to the line above it. */
  lines: string[];
}

export interface RunHeader {
  /** The view's `summary.state`: needs_you, working, done or stopped. */
  state: string;
  label: string;
  sentence: string;
  pipeline: PipelineStep[];
  /** The collapsed header's short facts: spend and what went wrong. */
  facts: string[];
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

// ---- Pipeline: the view's `pipeline` rows -------------------------------------------------

export function pipelineSteps(view: Row): PipelineStep[] {
  return rows(view.pipeline).flatMap((row) => {
    const label = text(row.label);
    const state = row.state as StepState;
    if (!label || !STEP_STATES.has(state)) return [];
    let detail: string;
    if (state === "skipped") detail = "skipped" + (text(row.reason) ? `: ${text(row.reason)}` : "");
    else {
      const model = text(row.model);
      const parts = [model.includes("/") ? model.slice(model.lastIndexOf("/") + 1) : model, text(row.reasoning_effort)];
      if (isNumber(row.spent_usd) && row.spent_usd > 0) parts.push(money(row.spent_usd));
      detail = parts.filter(Boolean).join(" · ");
    }
    return [{ label, state, mark: STEP_MARKS[state], detail }];
  });
}

function pipelineSection(steps: PipelineStep[]): DetailSection | null {
  if (!steps.length) return null;
  const working = steps.find((step) => step.state === "working");
  const next = steps.find((step) => step.state === "next");
  const ran = steps.filter((step) => step.state === "ran").length;
  const summary = working ? `${working.label} working` : next ? `${next.label} next` : `${ran} of ${steps.length} ran`;
  return { id: "pipeline", title: "Pipeline", summary, lines: steps.map((step) => `${step.mark} ${step.label}${step.detail ? ` · ${step.detail}` : ""}`) };
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
  return { state, label, sentence, section: { id: "where", title: "Where the run is", summary: label, lines: lines.filter(Boolean) } };
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
  return `${text(row.job) || "Job unreported"} ${money(row.spent_usd)}${known} · ${percent(row.share)}${again}`;
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
  const repeated = rows(waste.repeated_jobs);
  if (repeated.length) line += "; ran again: " + repeated.map((row) => `${text(row.job)} ×${row.attempts}`).join(", ");
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

/** The short spend line: the Builder's share against checking, or the total across jobs. */
export function spendSummary(view: Row): string | null {
  const metrics = metricsOf(view);
  if (!metrics) return null;
  const single = mapping(metrics.single_model);
  if (isNumber(single.builder_share) && single.builder_share <= 1) {
    return `Builder ${percent(single.builder_share)} · checking ${percent(1 - single.builder_share)}`;
  }
  return `${money(metrics.spent_usd)} across ${rows(metrics.by_job).length} jobs`;
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
  lines.push(`  Builder ${money(single.builder_usd)} (${percent(single.builder_share)}); planning and checking ${money(single.checking_usd)}`);
  const bought = checked(mapping(single.produced));
  lines.push(`  What that bought: ${bought.length ? bought.join("; ") : "nothing recorded yet"}`);
  if (text(single.basis)) lines.push(`  ${text(single.basis)}.`);
  return { id: "spend", title: "Spend", summary: spendSummary(view) ?? money(metrics.spent_usd), lines };
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
export function runHeader(view: unknown): RunHeader | null {
  const source = mapping(view);
  if (!Object.keys(source).length) return null;
  const steps = pipelineSteps(source);
  const now = where(source);
  const facts: string[] = [];
  const spend = spendSummary(source);
  if (spend) facts.push(spend);
  const wrong = recoveries(source).length;
  if (wrong) facts.push(`${wrong} ${wrong === 1 ? "thing" : "things"} went wrong, handled`);
  const sections = [
    now.section,
    pipelineSection(steps),
    wrongSection(source),
    spendSection(source),
    verificationSection(source),
    pathSection(source),
    decisionsSection(source),
  ].filter((section): section is DetailSection => section !== null);
  return { state: now.state, label: now.label, sentence: now.sentence, pipeline: steps, facts, sections };
}
