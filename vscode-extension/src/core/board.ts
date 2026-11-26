/**
 * The Tasks board's data: every run of the project (`autocode runs --json`) and its issue queue
 * (`autocode issue queue status --json`, `autocode issue list --json`), read and grouped.
 *
 * Pure functions from what the CLI printed to what the board shows. The core decides what each run
 * needs, which issue starts next and how many work at once (owner decision 2026-11-26, AGENTS.md);
 * the board only groups rows, and takes a command only when the core named it in the exact shape
 * checked here. Anything else is a gap in AutoCode, never filled in with a guess.
 */

import { stageWords, statusWords } from "./status-words";

/** One row of `autocode runs --json`. */
export interface BoardRun {
  runDir: string;
  workspace: string;
  title: string | null;
  bucket: string;
  availability: string;
  status: string | null;
  statusLabel: string | null;
  needs: string | null;
  needsReason: string | null;
  nextStage: string | null;
  nextStageLabel: string | null;
  readyToContinue: boolean;
  reason: string | null;
  updatedAt: string | null;
}

/** An issue's run, as the queue's views give it. */
export interface IssueRun {
  ref: string;
  number: number;
  title: string | null;
  url: string | null;
  runDir: string | null;
  branch: string | null;
  prUrl: string | null;
  /** `issue pr REF --project P --open`, when the core offers it: the run completed and has no pull request. */
  openPr: string[] | null;
}

export interface QueueEntry {
  ref: string;
  number: number | null;
  state: string;
  error: string | null;
}

export interface QueueStatus {
  project: string;
  maxActive: number;
  runnerActive: boolean;
  working: number | null;
  entries: QueueEntry[];
  issues: IssueRun[];
  /** `issue queue run --project P`, when issues wait and no scheduler is running. */
  nextCommand: string[] | null;
}

export interface OpenIssue {
  ref: string;
  number: number;
  title: string;
  url: string | null;
  labels: string[];
  queueState: string | null;
  run: IssueRun | null;
}

export interface IssueListing {
  repository: string;
  project: string;
  issues: OpenIssue[];
  queue: QueueStatus;
  /** `issue queue add --project P --max-active LIMIT ISSUES`, checked word for word. */
  queueAdd: string[] | null;
  defaultMaxActive: number;
}

const str = (value: unknown): string | null => (typeof value === "string" && value ? value : null);
const words = (value: unknown): string[] | null =>
  Array.isArray(value) && value.every((word) => typeof word === "string") ? (value as string[]) : null;
const same = (left: readonly string[] | null, right: readonly string[]): boolean =>
  left !== null && left.length === right.length && left.every((word, index) => word === right[index]);

function parse(raw: string, what: string): Record<string, unknown> {
  let parsed: unknown;
  try { parsed = JSON.parse(raw); } catch { throw new Error(`${what} did not return JSON`); }
  if (!parsed || typeof parsed !== "object") throw new Error(`${what} did not return an object`);
  return parsed as Record<string, unknown>;
}

/** `autocode runs [--all] --json`. A row missing the pointers the board opens is left out. */
export function readRuns(raw: string): BoardRun[] {
  const parsed = parse(raw, "autocode runs");
  if (parsed.schema !== 1) throw new Error(`Unsupported runs list version: ${String(parsed.schema)}`);
  const rows = Array.isArray(parsed.runs) ? (parsed.runs as unknown[]) : [];
  return rows.flatMap((row) => {
    if (!row || typeof row !== "object") return [];
    const r = row as Record<string, unknown>;
    const runDir = str(r.run_dir);
    const workspace = str(r.workspace);
    if (!runDir || !workspace) return [];
    return [{
      runDir, workspace, title: str(r.title), bucket: str(r.bucket) ?? "unreadable",
      availability: str(r.availability) ?? "unknown", status: str(r.status), statusLabel: str(r.status_label),
      needs: str(r.needs), needsReason: str(r.needs_reason), nextStage: str(r.next_stage),
      nextStageLabel: str(r.next_stage_label), readyToContinue: r.ready_to_continue === true,
      reason: str(r.reason), updatedAt: str(r.updated_at),
    }];
  });
}

function readIssueRun(value: unknown, project: string): IssueRun | null {
  if (!value || typeof value !== "object") return null;
  const r = value as Record<string, unknown>;
  const ref = str(r.ref);
  if (!ref || typeof r.number !== "number") return null;
  const actions = (r.actions && typeof r.actions === "object" ? r.actions : {}) as Record<string, unknown>;
  const openPr = words((actions.open_pr as Record<string, unknown> | undefined)?.next_command ?? null);
  return {
    ref, number: r.number, title: str(r.title), url: str(r.url), runDir: str(r.run_dir), branch: str(r.branch),
    prUrl: str(r.pr_url),
    openPr: same(openPr, ["issue", "pr", ref, "--project", project, "--open"]) ? openPr : null,
  };
}

function readQueue(r: Record<string, unknown>, project: string): QueueStatus {
  const next = words(r.next_command ?? null);
  const entries = Array.isArray(r.entries) ? (r.entries as Record<string, unknown>[]) : [];
  const issues = Array.isArray(r.issues) ? (r.issues as unknown[]) : [];
  return {
    project,
    maxActive: typeof r.max_active === "number" ? r.max_active : 0,
    runnerActive: r.runner_active === true,
    working: typeof r.working === "number" ? r.working : null,
    entries: entries.flatMap((entry) => {
      const ref = str(entry?.ref);
      const state = str(entry?.state);
      return ref && state ? [{ ref, state, number: typeof entry.number === "number" ? entry.number : null, error: str(entry.error) }] : [];
    }),
    issues: issues.flatMap((item) => readIssueRun(item, project) ?? []),
    nextCommand: same(next, ["issue", "queue", "run", "--project", project]) ? next : null,
  };
}

/** `autocode issue queue status --project P --json`: no GitHub read, so the board polls it. */
export function readQueueStatus(raw: string, project: string): QueueStatus {
  const parsed = parse(raw, "autocode issue queue status");
  if (parsed.schema_version !== 1) throw new Error(`Unsupported issue queue version: ${String(parsed.schema_version)}`);
  return readQueue(parsed, project);
}

/** `autocode issue list --project P --json`: the repository's open issues, for the picker. */
export function readIssueListing(raw: string, project: string): IssueListing {
  const parsed = parse(raw, "autocode issue list");
  if (parsed.schema_version !== 1) throw new Error(`Unsupported issue list version: ${String(parsed.schema_version)}`);
  const issues = Array.isArray(parsed.issues) ? (parsed.issues as Record<string, unknown>[]) : [];
  const add = (parsed.queue_add ?? {}) as Record<string, unknown>;
  const template = words(add.next_command ?? null);
  return {
    repository: str(parsed.repository) ?? "",
    project,
    issues: issues.flatMap((item) => {
      const ref = str(item?.ref);
      if (!ref || typeof item.number !== "number") return [];
      return [{
        ref, number: item.number, title: str(item.title) ?? "", url: str(item.url),
        labels: (words(item.labels ?? null) ?? []).filter(Boolean),
        queueState: str(item.queue_state), run: readIssueRun(item.run, project),
      }];
    }),
    queue: readQueue((parsed.queue ?? {}) as Record<string, unknown>, project),
    queueAdd: same(template, ["issue", "queue", "add", "--project", project, "--max-active", "LIMIT", "ISSUES"]) ? template : null,
    defaultMaxActive: typeof add.default_max_active === "number" && add.default_max_active >= 1 ? add.default_max_active : 3,
  };
}

/** Whether an open issue can be started: it has no run and is not waiting on the queue. */
export function startable(issue: OpenIssue): boolean {
  return !issue.run && (issue.queueState === null || issue.queueState === "failed");
}

/**
 * The queue's named `add`, filled with the issues the person ticked and the limit they chose, then `--`
 * and the options a new chat starts with. Refuses anything the listing did not offer.
 */
export function queueAddArgs(listing: IssueListing, refs: readonly string[], limit: number, options: readonly string[]): string[] {
  if (!listing.queueAdd) throw new Error("This AutoCode names no issue queue; update it to start issues from VS Code.");
  if (!Number.isSafeInteger(limit) || limit < 1) throw new Error("How many at once must be a whole number, 1 or more.");
  const offered = new Map(listing.issues.map((issue) => [issue.ref, issue]));
  const chosen = [...new Set(refs)];
  if (!chosen.length) throw new Error("Tick at least one issue to start.");
  for (const ref of chosen) {
    const issue = offered.get(ref);
    if (!issue || !startable(issue)) throw new Error(`${ref} is not an issue the list offered to start.`);
  }
  const args = listing.queueAdd.flatMap((word) => (word === "LIMIT" ? [String(limit)] : word === "ISSUES" ? chosen : [word]));
  return options.length ? [...args, "--", ...options] : args;
}

export type Group = "needs" | "working" | "queued" | "done" | "unreadable";
export const GROUPS: readonly { id: Group; label: string }[] = [
  { id: "needs", label: "Needs you" },
  { id: "working", label: "Working" },
  { id: "queued", label: "Queued" },
  { id: "done", label: "Done" },
  { id: "unreadable", label: "Cannot be read" },
];

/** What a row's button does: open the run's chat, continue it, open its pull request, or nothing. */
export type RowAction = { kind: "open" | "continue" | "open_pr" | "none"; label: string };

export interface BoardRow {
  key: string;
  group: Group;
  title: string;
  /** `#812` for an issue run. */
  issue: { ref: string; number: number; url: string | null; prUrl: string | null; branch: string | null } | null;
  status: string;
  stage: string | null;
  updatedAt: string | null;
  run: { workspace: string; runDir: string } | null;
  action: RowAction;
  detail: string | null;
}

const BUCKET_GROUP: Record<string, Group> = { "needs you": "needs", working: "working", settled: "done", unreadable: "unreadable" };

const NEEDS_LABEL: Record<string, string> = {
  approve_plan: "Review plan",
  answer: "Answer",
  review: "Review",
};

const normal = (path: string): string => path.replace(/[\\/]+$/, "");

function actionFor(run: BoardRun, group: Group, issue: IssueRun | undefined): RowAction {
  if (group === "needs") {
    if (run.readyToContinue) return { kind: "continue", label: "Continue" };
    return { kind: "open", label: (run.needs && NEEDS_LABEL[run.needs]) || "Open" };
  }
  if (group === "done" && issue?.openPr) return { kind: "open_pr", label: "Open pull request" };
  return { kind: "open", label: "Open" };
}

/** Every row of the board: the runs in their groups, then the issues still waiting on the queue. */
export function boardRows(runs: readonly BoardRun[], queues: readonly QueueStatus[] = []): BoardRow[] {
  const issues = new Map<string, IssueRun>();
  for (const queue of queues) for (const issue of queue.issues) if (issue.runDir) issues.set(normal(issue.runDir), issue);
  const rows: BoardRow[] = runs.map((run) => {
    const group = BUCKET_GROUP[run.bucket] ?? "unreadable";
    const issue = issues.get(normal(run.runDir));
    return {
      key: run.runDir,
      group,
      title: run.title || issue?.title || run.runDir.split(/[\\/]/).filter(Boolean).pop() || run.runDir,
      issue: issue ? { ref: issue.ref, number: issue.number, url: issue.url, prUrl: issue.prUrl, branch: issue.branch } : null,
      status: group === "unreadable" ? (run.reason || run.availability) : statusWords(run.status, run.statusLabel),
      stage: group === "working" ? stageWords(run.nextStage, run.nextStageLabel) : null,
      updatedAt: run.updatedAt,
      run: { workspace: run.workspace, runDir: run.runDir },
      action: actionFor(run, group, issue),
      detail: run.needsReason,
    };
  });
  // A start the queue has not seen return yet is already its own run on the board: one row, not two.
  const shown = new Set(rows.flatMap((row) => (row.issue ? [row.issue.ref] : [])));
  for (const queue of queues) {
    for (const entry of queue.entries) {
      if (entry.state !== "queued" && entry.state !== "starting" && entry.state !== "failed") continue;
      if (shown.has(entry.ref)) continue;
      rows.push({
        key: `queue:${entry.ref}`,
        group: "queued",
        title: entry.ref,
        issue: { ref: entry.ref, number: entry.number ?? 0, url: null, prUrl: null, branch: null },
        status: entry.state === "failed" ? "Could not start" : entry.state === "starting" ? "Starting" : "Waiting for a free slot",
        stage: null,
        updatedAt: null,
        run: null,
        action: { kind: "none", label: "" },
        detail: entry.error,
      });
    }
  }
  return rows;
}

/** How many rows each group has, in board order. */
export function groupCounts(rows: readonly BoardRow[]): Record<Group, number> {
  const counts = { needs: 0, working: 0, queued: 0, done: 0, unreadable: 0 };
  for (const row of rows) counts[row.group] += 1;
  return counts;
}
