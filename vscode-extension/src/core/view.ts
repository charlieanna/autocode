/**
 * Typed reads over the AutoCode status view (`docs/task-run.md`) and the
 * browser registry (`docs/registry-api.md`).
 *
 * The status view is a contract: fields may be added, and existing fields keep
 * their names. So every read here tolerates a missing field rather than
 * assuming one, and nothing reaches for `state.json`.
 *
 * No VS Code API, so it is unit-testable in-process.
 */

import { usageDisplay, type BudgetCost, type ModelRoute, type Usage } from "./telemetry";

export interface Needs {
  kind: string;
  token?: string;
  reason?: string;
  action?: string | null;
  questions?: Question[];
  criteria?: { id: string; criterion?: string }[];
  question?: string;
  resolver_scope?: string;
  resolver_token?: string;
  resolver_request_id?: string;
  request_id?: string;
  route?: Route;
  /** A level a person named that the model does not declare (#2057): the levels it does. */
  effort?: EffortNeed;
  retry_action?: string;
  job_retry_token?: string;
  new_run_required?: boolean;
  edit_required?: boolean;
  feedback_action?: string | null;
  command_inputs?: IntegerCommandInput[];
  /** False only when the core offers a clean requested-pause resume; the person still clicks Resume. */
  confirmation_required?: boolean;
}

/**
 * A person's finite number for one exact core-named command (#2044). `command` is present when
 * next_command is not that command: the view then names the raise separately.
 */
export interface IntegerCommandInput {
  id: string;
  kind: "integer";
  flag: string;
  placeholder: "N";
  arg_index: 1 | 2;
  label: string;
  current: number;
  minimum: number;
  maximum: number;
  used?: number;
  elapsed?: number;
  command?: string;
}

export interface Question {
  id: string;
  question: string;
  why?: string;
  options?: string[];
  proposed_default?: string | null;
}

export interface Route {
  question_id: string;
  role: string;
  job?: string;
  current_model?: string;
  engine?: string;
  cause?: string;
  stopped_model?: string;
  candidates?: string[];
}

export interface EffortNeed {
  question_id: string;
  role: string;
  job?: string;
  model?: string;
  requested?: string;
  declared: string[];
  nearest?: string | null;
}

export interface Progress {
  line?: string;
  headline?: string;
  stage?: string;
  needs_you?: string;
  tasks?: { done: number; total: number; known?: boolean; items?: ProgressTask[] };
  requirements?: {
    checked?: number;
    failed?: number;
    unchecked?: number;
    awaiting_review?: number;
    reviewed?: number;
    total?: number;
    items?: { id: string; criterion?: string; label?: string; state: string }[];
  };
  problems?: { open?: number; items?: { id: string; finding: string; severity?: string; blocking?: boolean }[] };
  for_earlier_request?: boolean;
}

export interface ProgressTask {
  number?: number;
  id: string;
  label?: string;
  state: string;
  current?: boolean;
}

/** A criterion carries both its verification method and its human-review requirement. */
export interface AcceptanceCriterion {
  id: string;
  criterion: string;
  verification_method: string;
  human_review: boolean;
}

/** The structured approval projection `docs/task-run.md` describes as `displayed_plan`. */
export interface DisplayedPlan {
  revision?: number;
  hash?: string;
  token?: string;
  acceptance_criteria?: AcceptanceCriterion[];
  constraints?: string[];
  permission_boundaries?: string[];
  scope_exclusions?: string[];
  git_delivery?: { kind: "atomic_commit"; message: string } | null;
}

/** A named checkpoint action, in the same shape as a connection action. */
export interface CheckpointAction {
  id: string;
  label: string;
  next_command: string[];
}

export interface CodeCheckpointRow {
  id: string;
  task_id?: string;
  iteration?: number | null;
  at?: string | null;
  label?: string | null;
  milestone_id?: string | null;
  members?: { milestone_id: string; changed_files: string[] }[] | null;
  changed_files?: string[];
  source_revision?: string;
  commit?: string;
  available?: boolean;
  reason?: string | null;
  recorded_check?: { verdict?: string; at?: string; scope?: string } | null;
  actions?: CheckpointAction[];
}

/** `code_checkpoints` in the status view: saved code steps and their named actions. */
export interface CodeCheckpoints {
  version?: number;
  blocked_reason?: string | null;
  rows?: CodeCheckpointRow[];
  /** While a Builder step runs: the display-only diff of what it has changed so far (`checkpoint --working`). */
  working?: CheckpointAction | null;
  continuation?: Record<string, unknown> | null;
  restores?: Record<string, unknown>[] | null;
}

/** Descriptions of saved recovery actions; execution still uses next_command. */
export interface Recovery {
  token?: string;
  category?: string;
  title?: string;
  what_happened?: string;
  retained?: string;
  actions?: { kind?: string; label?: string; effect?: string; args?: string[] }[];
  [key: string]: unknown;
}

export interface StatusView {
  schema?: number;
  /** The run's effective workspace: may differ from a client handle named before bootstrap relocation. */
  workspace?: string | null;
  /** The run's effective directory, paired with `workspace`. */
  run_dir?: string | null;
  status: string;
  done: boolean;
  phase?: string | null;
  next_stage?: string | null;
  iteration?: number | null;
  turn?: number | null;
  workflow?: string | null;
  task?: string;
  stop_reason?: string | null;
  pause_category?: string | null;
  pause_category_label?: string | null;
  next_command?: string | null;
  needs?: Needs | null;
  displayed_plan?: DisplayedPlan | null;
  progress?: Progress | null;
  usage?: Usage | null;
  cost?: BudgetCost | null;
  provider?: string | null;
  routes?: Record<string, ModelRoute> | null;
  evidence?: { outcome?: string; acceptance?: { id: string; criterion: string; status: string }[] } | null;
  recovery?: Recovery | null;
  liveness?: { kind?: string } | null;
  /** Fresh birth-bound invocation inspection, independent of workflow-stage liveness. */
  controller?: { kind?: string; checked?: boolean; alive?: boolean | null } | null;
  current_task?: { id?: string; objective?: string } | null;
  code_checkpoints?: CodeCheckpoints | null;
  /** What the request spans beyond this repository; `linked` lists the other runs of its change set (#2066). */
  scope?: { linked?: LinkedRun[] | null; [key: string]: unknown } | null;
  /** The pause the view offers while the run works (#1598); a client runs only its `next_command`. */
  pause?: { label?: string; effect?: string; requested?: boolean; next_command?: string[] | null } | null;
  chat?: {
    messages: { id: string; role: string; text: string; streaming?: boolean }[];
    phase?: "requirements" | null;
    notice?: string | null;
    /** Core-authored activity and recent attempt descriptions; display text only. */
    progress?: string[];
    /** The running attempt's recent commands, edits and tool calls; display only, never proof. */
    activity?: ChatActivity[];
    /** Live agent log: reasoning and messages beside those tool marks (docs/task-run.md). */
    log?: ChatLogEntry[];
    review?: { question_id: string; answer: string; label: string; summary?: string;
      spend_confirmation?: { title: string; detail: string; label: string; match?: "trim_lower" } } | null;
    context: string;
    input: { kind: "feedback" | "follow_up" | "resolver_response"; label: string; next_command: string } | null;
  };
}

/** One `chat.activity` event: what the running step did, never whether it was right. */
export interface ChatActivity {
  kind: "command" | "edit" | "tool";
  text: string;
  status: "running" | "done" | "failed";
  paths?: string[];
}

/** One `chat.log` entry: what a running agent thinks, says or does. Display only. */
export interface ChatLogEntry {
  id: string;
  agent: string;
  kind: "thinking" | "message" | "command" | "edit" | "tool";
  text: string;
  status?: "running" | "done" | "failed";
  paths?: string[];
}

/** One other run of this run's change set, as `view.scope.linked` lists it. */
export interface LinkedRun {
  changeset: string;
  target: string;
  role: "source" | "member";
  workspace: string;
  run_dir: string | null;
}

/** One run of a change set, as `autocode changeset status --json` shows it. */
export interface ChangesetRun {
  role: "source" | "member";
  target: string;
  workspace: string;
  run_dir: string;
  status: string;
  needs?: string | null;
  plan?: DisplayedPlan | null;
}

/** `autocode changeset status --json` (docs/task-run.md#change-sets-in-clients). */
export interface ChangesetSummary {
  changeset: string;
  runs: ChangesetRun[];
  approval: { set_token: string | null; waiting_on: string[]; next_command: string[] | null };
  verification: {
    waiting_on: string[];
    done: boolean;
    merge_order: string[];
    next_command: string[] | null;
    check?: { command: string[]; in: string } | null;
    result?: { passed: boolean; current: boolean; exit_code?: number | null } | null;
    waiver?: { reason: string; current: boolean } | null;
  };
}

/** Parse `changeset status --json`; its refusal (`{"error": ...}`) or another shape is an error. */
export function readChangesetSummary(raw: string): ChangesetSummary {
  let parsed: Record<string, unknown>;
  try {
    parsed = JSON.parse(raw) as Record<string, unknown>;
  } catch {
    throw new Error("changeset status did not return JSON");
  }
  if (typeof parsed?.error === "string") throw new Error(parsed.error);
  if (typeof parsed?.changeset !== "string" || !Array.isArray(parsed.runs) || !parsed.approval || !parsed.verification) {
    throw new Error("changeset status did not return a change set");
  }
  return parsed as unknown as ChangesetSummary;
}

/** The envelope `autocode --status` prints: the view sits under `"view"`. */
export function readStatusPayload(raw: string): StatusView {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new Error("--status did not return JSON");
  }
  const view = (parsed as Record<string, unknown>)?.view;
  if (!view || typeof view !== "object") throw new Error("--status did not return a status view");
  return view as StatusView;
}

export interface RegistryRun {
  id: string;
  workspace: string;
  runDir: string;
  taskId: string | null;
  availability: string;
  status?: string | null;
  phase?: string | null;
  nextStage?: string | null;
  diagnostic?: string | null;
}

export interface RegistryList {
  storageRoot?: string;
  runs: RegistryRun[];
  diagnostics: string[];
}

/** `autocode registry list --json`, with the pointer fields a run list needs. */
export function readRegistryList(raw: string): RegistryList {
  const parsed = JSON.parse(raw) as Record<string, unknown>;
  const rows = Array.isArray(parsed.runs) ? (parsed.runs as Record<string, unknown>[]) : [];
  return {
    storageRoot: typeof parsed.storage_root === "string" ? parsed.storage_root : undefined,
    runs: rows.map((row) => {
      const nested = (row.diagnostic ?? {}) as Record<string, unknown>;
      return {
        id: String(row.id ?? ""),
        workspace: String(row.workspace ?? ""),
        runDir: String(row.run_dir ?? ""),
        taskId: typeof row.task_id === "string" ? row.task_id : null,
        availability: String(row.availability ?? "unknown"),
        // An available record carries its current summary under `diagnostic`, not beside it;
        // a stale one carries only a `message` there. `docs/registry-api.md` names the fields
        // but not where they sit, so read either placement and never invent one.
        status: firstString(row.status, nested.status),
        phase: firstString(row.phase, nested.phase),
        nextStage: firstString(row.next_stage, nested.next_stage),
        diagnostic: nestedString(row.diagnostic, "message"),
      };
    }),
    diagnostics: Array.isArray(parsed.diagnostics) ? parsed.diagnostics.map(String) : [],
  };
}

/** The first value that is a non-empty string. */
function firstString(...values: unknown[]): string | null {
  for (const value of values) {
    if (typeof value === "string" && value.length > 0) return value;
  }
  return null;
}

/** A string field of a nested object, or null when the shape is not that. */
function nestedString(value: unknown, key: string): string | null {
  if (!value || typeof value !== "object") return null;
  const field = (value as Record<string, unknown>)[key];
  return typeof field === "string" ? field : null;
}

/** The one line a person reads, falling back to the status code when `progress` is absent. */
export function progressLine(view: StatusView): string {
  return view.progress?.line || view.progress?.headline || view.status;
}

/** Live workflow work, or a checked live controller advancing a no-action boundary. */
export function isWorking(view: StatusView): boolean {
  return ["supervised", "unsupervised"].includes(view.liveness?.kind ?? "")
    || (!view.done && view.needs?.kind === "continue" && view.controller?.kind === "running"
      && view.controller.checked === true && view.controller.alive === true);
}

/** Plain-language description of what the run is waiting for, from the view alone. */
export function needsSummary(view: StatusView, working?: boolean): string {
  const need = view.needs;
  if (!need) return "nothing; the run is complete";
  if (view.progress?.needs_you) return view.progress.needs_you;
  const active = working ?? isWorking(view);
  switch (need.kind) {
    case "approve_plan":
      return "approval of the plan AutoCode displayed";
    case "answer":
      return `${need.questions?.length ?? 0} question(s)`;
    case "review":
      return `review of ${need.criteria?.length ?? 0} acceptance criteria`;
    case "planning_budget":
      return `a decision on more plan-review calls${need.reason ? `: ${need.reason}` : ""}`;
    case "continue":
      return active ? "a stage is currently running" : "nothing; the run can simply proceed";
    case "retry_job":
      return "inspection of a stopped workflow job";
    case "recover_source":
      return "inspection of an attempt with no saved source identity";
    case "dependency":
      return "a producer run to deliver";
    default:
      return need.reason ? `${need.kind}: ${need.reason}` : need.kind;
  }
}

/** A short label for a stopped run's cause, so a person can tell whose decision it is. */
export function pauseLabel(view: StatusView): string | null {
  // Normal user checkpoints (for example, plan approval) can also carry a
  // pause category. Only stopped runs should be presented as unable to continue.
  if (!view.status.startsWith("PAUSED") || !view.pause_category) return null;
  return view.pause_category_label || view.pause_category;
}

/**
 * The token an approval is bound to, when the run is asking for one.
 *
 * `docs/task-run.md` requires automation to bind the token to `needs.token` and
 * to read the structured plan rather than infer approval from prose. A plan
 * revised while a panel sat open carries a different token, so an approval
 * taken then would approve a plan nobody read.
 */
export function approvalToken(view: StatusView): string | null {
  const need = view.needs;
  if (!need || (need.kind !== "approve_plan" && need.kind !== "review")) return null;
  return need.token ?? null;
}

/** Whether `displayed_plan` is the plan the current token asks to approve. */
export function planForApproval(view: StatusView): DisplayedPlan | null {
  const plan = view.displayed_plan;
  if (!plan?.token) return null;
  const asked = approvalToken(view);
  if (asked === null || plan.token !== asked) return null;
  // A matching token is insufficient if this client cannot display the full
  // approval projection. Refuse malformed/older shapes instead of hiding terms.
  if (!Array.isArray(plan.acceptance_criteria) || !plan.acceptance_criteria.every((row) =>
    row && typeof row.id === "string" && typeof row.criterion === "string"
      && typeof row.verification_method === "string" && typeof row.human_review === "boolean"
  )) return null;
  if (![plan.constraints, plan.permission_boundaries, plan.scope_exclusions === undefined ? [] : plan.scope_exclusions].every((rows) =>
    Array.isArray(rows) && rows.every((row) => typeof row === "string")
  )) return null;
  return plan;
}

/** Cost so far, in the words the view supports: unknown cost is not zero. */
export function usageLine(view: StatusView): string | null {
  const usage = view.usage;
  if (!usage && !view.cost) return null;
  const display = usageDisplay(usage, view.cost);
  const parts: string[] = [];
  if (display.tokens !== "Tokens not reported") parts.push(display.tokens);
  if (display.cost !== "Cost not reported") parts.push(display.cost);
  if (!view.cost && typeof usage?.unknown_stages === "number" && usage.unknown_stages > 0) {
    parts.push(`${usage.unknown_stages} stage(s) unknown`);
  }
  return parts.length ? parts.join(" · ") : null;
}
