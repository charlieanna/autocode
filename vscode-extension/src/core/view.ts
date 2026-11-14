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
  milestones?: { id: string; objective: string; acceptance_criteria?: string[] }[];
  end_to_end_flow?: string[];
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
  /** Whether this started run's stage models can change now, and the read-only list whose `set` arrays change one. */
  stage_models?: { changeable: boolean; reason: string | null; next_command: string[] } | null;
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
  pause?: {
    label?: string; effect?: string; requested?: boolean; next_command?: string[] | null;
    /** The same pause, also ending the running step now (#3263); absent from an older runner. */
    stop_now?: { label?: string; effect?: string; requested?: boolean; next_command?: string[] | null };
  } | null;
  /** Ready to go on, but another run holds the folder its next step needs: display text, never authority. */
  checkout_wait?: { run_dir?: string; since?: string | null; text?: string } | null;
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
    /** Beside the log: what each working agent is doing now ("searching the code"). Display only. */
    now?: { agent: string; text: string }[];
    review?: { question_id: string; answer: string; label: string; summary?: string;
      spend_confirmation?: { title: string; detail: string; label: string; match?: "trim_lower" } } | null;
    context: string;
    /** `message` is offered while the run works: saved for the next stage boundary (#3259). */
    input: { kind: "feedback" | "follow_up" | "resolver_response" | "message"; label: string; next_command: string } | null;
  };
}

/** One `chat.activity` event: what the running step did, never whether it was right. */
export interface ChatActivity {
  kind: "command" | "edit" | "tool";
  text: string;
  status: "running" | "done" | "failed";
  paths?: string[];
  /** For a tool: a phrase that groups repeats of it ("Searched code"). */
  summary?: string;
}

/** One `chat.log` entry: what a running agent thinks, says or does. Display only. */
export interface ChatLogEntry {
  id: string;
  agent: string;
  kind: "thinking" | "message" | "command" | "edit" | "tool";
  text: string;
  status?: "running" | "done" | "failed";
  paths?: string[];
  summary?: string;
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

/**
 * `--status` for a run that is still starting and has no checkpoint yet (#3079): what the start is
 * doing, in the core's words. It is not a status view and names no action, so a client only shows it.
 */
export class RunStarting extends Error {
  constructor(readonly label: string) {
    super(`AutoCode is ${label}`);
  }
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
  const starting = (parsed as Record<string, unknown>)?.starting as Record<string, unknown> | null | undefined;
  if (!view && starting && typeof starting === "object") {
    const label = typeof starting.label === "string" && starting.label ? starting.label : "starting the run";
    // The launcher stopped reporting: that is a failed start, not one to keep waiting on.
    if (starting.owner_alive === false) throw new Error(`AutoCode's start stopped while ${label}`);
    throw new RunStarting(label);
  }
  if (!view || typeof view !== "object") throw new Error("--status did not return a status view");
  return view as StatusView;
}

export interface RegistryAction {
  id: string;
  label: string;
  /** The exact command array the registry named, with no placeholder left for a client. */
  nextCommand: string[];
}

export interface RegistryRun {
  id: string;
  workspace: string;
  /** The owning project when `workspace` is a `.autocode/worktrees/<lane>` checkout. */
  projectWorkspace?: string | null;
  runDir: string;
  taskId: string | null;
  availability: string;
  status?: string | null;
  phase?: string | null;
  nextStage?: string | null;
  /** The first line of the run's task: how a person recognizes the chat. */
  title?: string | null;
  /** When the run last wrote its checkpoint, as UTC ISO-8601 (`docs/registry-api.md`). */
  updatedAt?: string | null;
  diagnostic?: string | null;
  /** Hidden from the default Runs list until archived chats are shown. */
  archived?: boolean;
  /** The cleanup commands the registry names for this chat. */
  actions?: RegistryAction[];
}

export interface RegistryList {
  storageRoot?: string;
  runs: RegistryRun[];
  diagnostics: string[];
}

/** `autocode registry list --json`, with the pointer fields a run list needs. */
export function readRegistryList(raw: string): RegistryList {
  let parsed: Record<string, unknown>;
  try {
    parsed = JSON.parse(raw) as Record<string, unknown>;
  } catch {
    throw new Error("registry list did not return JSON");
  }
  const rows = Array.isArray(parsed.runs) ? (parsed.runs as unknown[]) : [];
  return {
    storageRoot: typeof parsed.storage_root === "string" ? parsed.storage_root : undefined,
    // Like a malformed action below, a row missing the pointers a client acts on is left
    // out: one bad entry must not cost the person the whole list.
    runs: rows.flatMap((row) => {
      if (!row || typeof row !== "object") return [];
      const record = row as Record<string, unknown>;
      if (typeof record.id !== "string" || !record.id
        || typeof record.workspace !== "string" || !record.workspace
        || typeof record.run_dir !== "string" || !record.run_dir) return [];
      const nested = (record.diagnostic ?? {}) as Record<string, unknown>;
      return [{
        id: record.id,
        workspace: record.workspace,
        projectWorkspace: firstString(record.project_workspace, nested.project_workspace),
        runDir: record.run_dir,
        taskId: typeof record.task_id === "string" ? record.task_id : null,
        availability: String(record.availability ?? "unknown"),
        // An available record carries its current summary under `diagnostic`, not beside it;
        // a stale one carries only a `message` there. `docs/registry-api.md` names the fields
        // but not where they sit, so read either placement and never invent one.
        status: firstString(record.status, nested.status),
        phase: firstString(record.phase, nested.phase),
        nextStage: firstString(record.next_stage, nested.next_stage),
        title: firstString(record.title, nested.title),
        updatedAt: firstString(record.updated_at, nested.updated_at),
        diagnostic: nestedString(record.diagnostic, "message"),
        archived: record.archived === true,
        actions: registryActions(record.actions),
      }];
    }),
    diagnostics: Array.isArray(parsed.diagnostics) ? parsed.diagnostics.map(String) : [],
  };
}

/** The named cleanup commands of one registry row; a malformed entry is left out. */
function registryActions(value: unknown): RegistryAction[] {
  if (!Array.isArray(value)) return [];
  const result: RegistryAction[] = [];
  for (const item of value) {
    if (!item || typeof item !== "object") continue;
    const row = item as Record<string, unknown>;
    const next = Array.isArray(row.next_command)
      ? row.next_command.filter((word): word is string => typeof word === "string")
      : [];
    if (typeof row.id === "string" && row.id
      && typeof row.label === "string" && row.label
      && next.length) {
      result.push({ id: row.id, label: row.label, nextCommand: next });
    }
  }
  return result;
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

/** Live workflow work, a checked live controller advancing a no-action boundary, or a ready step waiting
 * for another chat to leave its folder (`checkout_wait`): the client goes on with it, the person is not asked. */
export function isWorking(view: StatusView): boolean {
  return ["supervised", "unsupervised"].includes(view.liveness?.kind ?? "")
    || waitsForFolder(view)
    || (!view.done && view.needs?.kind === "continue" && view.controller?.kind === "running"
      && view.controller.checked === true && view.controller.alive === true);
}

/** Another run holds the folder this ready run's next step needs (the core's `checkout_wait`). */
export function waitsForFolder(view: StatusView): boolean {
  return !view.done && view.needs?.kind === "continue" && typeof view.checkout_wait?.text === "string"
    && view.checkout_wait.text.length > 0;
}

/** Plain-language description of what the run is waiting for, from the view alone. */
export function needsSummary(view: StatusView, working?: boolean): string {
  const need = view.needs;
  if (!need) return view.done ? "nothing; the run is complete" : "nothing needed from you";
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
      if (waitsForFolder(view)) return view.checkout_wait!.text!;
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
  const milestones = plan.milestones === undefined ? [] : plan.milestones;
  if (!Array.isArray(milestones) || !milestones.every((row) =>
    row && typeof row.id === "string" && typeof row.objective === "string"
      && (row.acceptance_criteria === undefined || (Array.isArray(row.acceptance_criteria)
        && row.acceptance_criteria.every((id) => typeof id === "string")))
  )) return null;
  const flow = plan.end_to_end_flow === undefined ? [] : plan.end_to_end_flow;
  if (!Array.isArray(flow) || !flow.every((step) => typeof step === "string")) return null;
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
