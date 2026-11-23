/** The Runs side bar: the local run registry, read through the CLI. */

import vscode = require("vscode");
import type { RunController } from "./controller";
import { activity, byLatest, recentRuns, relativeTime } from "./core/recent";
import { stageWords, statusWords, type RegistryRun } from "./core/view";

export interface WorkspaceNode {
  readonly kind: "workspace";
  label: string;
  runs: RunItem[];
  /** Canonical owning project directory used to group worktree runs together. */
  project?: string;
  /** The Recent section: the newest chats of every project, above the project groups. */
  recent?: boolean;
  /** The Archived section, shown only while archived chats are listed. */
  archived?: boolean;
  collapsed?: boolean;
}

export type RunNode = WorkspaceNode | RunItem;

export class RunItem extends vscode.TreeItem {
  readonly kind = "run" as const;

  constructor(
    readonly run: RegistryRun,
    readonly workspace: string,
    recent = false,
    now = Date.now(),
    duplicateNumber?: number,
  ) {
    super(duplicateNumber ? `${duplicateNumber} · ${labelFor(run)}` : labelFor(run), vscode.TreeItemCollapsibleState.None);
    // A run listed under Recent is also listed under its project; tree ids must differ.
    this.id = recent ? `recent:${run.id}` : run.id;
    this.resourceUri = vscode.Uri.file(run.runDir);
    this.description = describe(run, recent, now, Boolean(duplicateNumber));
    const project = projectOf(run);
    this.tooltip = new vscode.MarkdownString(
      [
        `**${run.title ?? project}**`,
        "",
        run.title ? `- project: \`${project}\`` : "",
        project !== run.workspace ? `- worktree: \`${run.workspace}\`` : "",
        `- run: \`${run.runDir}\``,
        run.updatedAt ? `- last active: ${new Date(run.updatedAt).toLocaleString()} (${relativeTime(run, now)})` : "",
        run.availability === "available" ? `- status: ${statusWords(run.status, run.statusLabel)}` : "",
        nextStageText(run) ? `- next stage: ${nextStageText(run)}` : "",
        run.archived ? "- archived: yes" : "",
        run.diagnostic ? `- ${run.diagnostic}` : "",
      ]
        .filter(Boolean)
        .join("\n"),
    );
    this.contextValue = contextFor(run);
    this.command = {
      command: "autocode.openRun",
      title: "Open Run",
      arguments: [{ workspace: run.workspace, runDir: run.runDir }],
    };
    this.iconPath = iconFor(run);
  }
}

export class RunsProvider implements vscode.TreeDataProvider<RunNode> {
  private readonly changed = new vscode.EventEmitter<RunNode[] | undefined>();
  readonly onDidChangeTreeData = this.changed.event;

  constructor(private readonly controller: RunController) {}

  private nodes: RunNode[] = [];
  /** Whether a list has been read yet; until then the tree says it is reading rather than showing nothing. */
  private read = false;
  private readonly duplicateAllocations = new Map<string, Map<string, number>>();
  private loading?: Promise<void>;
  private pending = false;
  private disposed = false;
  private showArchivedState = false;

  /** Whether the History list is including chats the person archived. */
  get showArchived(): boolean {
    return this.showArchivedState;
  }

  async setShowArchived(value: boolean): Promise<void> {
    this.showArchivedState = value;
    await this.refresh();
  }

  async refresh(): Promise<void> {
    if (this.disposed) return;
    if (this.loading) { this.pending = true; return this.loading; }
    this.loading = this.load();
    return this.loading;
  }

  private async load(): Promise<void> {
    try {
      do {
        this.pending = false;
        try {
          const list = await this.controller.list(this.showArchivedState);
          if (!this.pending && !this.disposed) this.nodes = groupByWorkspace(list.runs, Date.now(), this.duplicateAllocations);
        } catch (error) {
          if (!this.pending && !this.disposed) this.nodes = [placeholder(error)];
        }
      } while (this.pending && !this.disposed);
    } finally {
      this.loading = undefined;
      this.read = true;
      if (!this.disposed) this.changed.fire(undefined);
    }
  }

  getTreeItem(node: RunNode): vscode.TreeItem {
    return node.kind === "workspace" ? workspaceItem(node) : node;
  }

  getChildren(node?: RunNode): RunNode[] {
    // The registry read can take seconds with many chats: an empty tree then looks like no chats at all.
    if (!node) return this.read ? this.nodes : [readingItem()];
    return node.kind === "workspace" ? node.runs : [];
  }

  dispose(): void {
    this.disposed = true;
    this.changed.dispose();
  }
}

/**
 * Recent chats first, then each project's chats, newest first. Projects are ordered by
 * their newest chat, and fold away under a Recent section so the latest chats are on top.
 */
function groupByWorkspace(runs: RegistryRun[], now = Date.now(), allocations = new Map<string, Map<string, number>>()): RunNode[] {
  const byWorkspace = new Map<string, RegistryRun[]>();
  for (const run of [...runs].sort(byLatest)) {
    const project = projectOf(run);
    const items = byWorkspace.get(project) ?? [];
    items.push(run);
    byWorkspace.set(project, items);
  }
  const nodes: RunNode[] = [];
  // Compare the actual displayed labels, including titles truncated at the same point.
  // Keep assigned numbers when a new matching chat enters the list.
  const duplicates = duplicateNumbers(runs, allocations);
  // Archived chats stay out of Recent; a visible Archived section lists them when asked for.
  const recent = recentRuns(runs.filter((run) => !run.archived));
  if (recent.length > 0) {
    const items = recent.map((run) => new RunItem(run, run.workspace, true, now, duplicates.get(run.runDir)));
    nodes.push({ kind: "workspace", label: "Recent", recent: true, runs: items });
  }
  const archived = runs.filter((run) => run.archived);
  if (archived.length > 0) {
    const items = archived.map((run) => new RunItem(run, run.workspace, true, now, duplicates.get(run.runDir)));
    nodes.push({ kind: "workspace", label: "Archived", archived: true, runs: items });
  }
  // Each project's runs are already newest first, so its first run is its latest activity.
  for (const [project, items] of [...byWorkspace].sort((a, b) => byLatest(a[1][0]!, b[1][0]!))) {
    nodes.push({
      kind: "workspace",
      label: basename(project),
      project,
      collapsed: recent.length > 0,
      runs: items.map((run) => new RunItem(run, run.workspace, false, now, duplicates.get(run.runDir))),
    });
  }
  if (nodes.length === 0) {
    nodes.push({
      kind: "workspace",
      label: "No registered runs",
      runs: [messageItem("Start a run from the AutoCode chat")],
    });
  }
  return nodes;
}

function duplicateNumbers(runs: RegistryRun[], allocations: Map<string, Map<string, number>>): Map<string, number> {
  const groups = new Map<string, RegistryRun[]>();
  for (const run of runs) {
    if (run.availability !== "available" || !run.title) continue;
    const label = labelFor(run);
    const group = groups.get(label) ?? [];
    group.push(run);
    groups.set(label, group);
  }
  const result = new Map<string, number>();
  for (const [label, group] of groups) {
    if (group.length < 2) continue;
    const assigned = allocations.get(label) ?? new Map<string, number>();
    group.sort((a, b) => a.runDir < b.runDir ? -1 : a.runDir > b.runDir ? 1 : 0);
    for (const run of group) {
      if (!assigned.has(run.runDir)) assigned.set(run.runDir, assigned.size + 1);
      result.set(run.runDir, assigned.get(run.runDir)!);
    }
    allocations.set(label, assigned);
  }
  return result;
}

function workspaceItem(node: WorkspaceNode): vscode.TreeItem {
  const state = node.collapsed ? vscode.TreeItemCollapsibleState.Collapsed : vscode.TreeItemCollapsibleState.Expanded;
  const item = new vscode.TreeItem(node.label, state);
  item.id = node.recent ? "recent" : node.archived ? "archived" : `workspace:${node.project ?? node.runs[0]?.run.workspace ?? node.label}`;
  item.description = node.recent ? "" : `${node.runs.length}`;
  item.iconPath = new vscode.ThemeIcon(node.recent ? "history" : node.archived ? "archive" : "folder");
  return item;
}

function messageItem(text: string): RunItem {
  const run: RegistryRun = {
    id: `message-${text}`,
    workspace: "",
    runDir: "",
    taskId: null,
    availability: "message",
  };
  const item = new RunItem(run, "");
  item.label = text;
  item.description = "";
  item.iconPath = new vscode.ThemeIcon("info");
  item.contextValue = undefined;
  item.command = undefined;
  return item;
}

function readingItem(): RunItem {
  const item = messageItem("Reading chats…");
  item.iconPath = new vscode.ThemeIcon("loading~spin");
  return item;
}

function placeholder(error: unknown): RunItem {
  const text = error instanceof Error ? error.message : String(error);
  const item = messageItem("AutoCode could not list runs");
  item.description = firstLine(text);
  item.iconPath = new vscode.ThemeIcon("error");
  return item;
}

function labelFor(run: RegistryRun): string {
  if (run.availability === "available" && run.title) return trim(run.title, 60);
  if (run.availability === "available" || run.availability === "message") {
    const identity = run.availability === "available" ? statusWords(run.status, run.statusLabel) : "Message";
    const short = run.runDir.split("/").filter(Boolean).slice(-1)[0] ?? "";
    return short ? trim(short, 46) : identity;
  }
  return `${basename(projectOf(run))} (${run.availability})`;
}

function describe(run: RegistryRun, recent: boolean, now: number, exactTime = false): string {
  const archived = run.archived ? " · archived" : "";
  if (run.availability !== "available") return (run.diagnostic ?? "") + archived;
  const timestamp = activity(run);
  const when = exactTime && timestamp !== null ? new Date(timestamp).toLocaleString() : relativeTime(run, now);
  // Under Recent the project tells chats apart; under a project the status does.
  const next = nextStageText(run);
  const stage = next ? ` → ${next}` : "";
  const detail = recent ? basename(projectOf(run)) : `${statusWords(run.status, run.statusLabel)}${stage}`;
  return (when ? `${when} · ${detail}` : detail) + archived;
}

/** The next stage's job name, or nothing once the run is finished. */
function nextStageText(run: RegistryRun): string | null {
  if (/^(TASK_)?COMPLETED?$/.test(run.status ?? "")) return null;
  return stageWords(run.nextStage, run.nextStageLabel);
}

function projectOf(run: RegistryRun): string {
  if (run.projectWorkspace) return run.projectWorkspace;
  const match = /^(.+)\/\.autocode\/worktrees\/[^/]+$/.exec(run.workspace);
  return match?.[1] || run.workspace;
}

function contextFor(run: RegistryRun): string | undefined {
  const actions = run.actions ?? [];
  if (run.archived) {
    return run.availability === "available" ? "autocode.runArchived" : "autocode.runArchivedStale";
  }
  if (run.availability !== "available") return actions.length ? "autocode.runStale" : undefined;
  const status = run.status ?? "";
  if (/^(RUNNING|BUILDING|WORKING|DISPATCHING|EXECUTING)/i.test(status)) return "autocode.runActive";
  return status || actions.length ? "autocode.runNeeds" : undefined;
}

function iconFor(run: RegistryRun): vscode.ThemeIcon {
  if (run.archived) return new vscode.ThemeIcon("archive");
  if (run.availability !== "available") return new vscode.ThemeIcon("dash");
  const status = (run.status ?? "").toUpperCase();
  if (status === "COMPLETE" || status === "COMPLETED" || status === "TASK_COMPLETE") {
    return new vscode.ThemeIcon("check", new vscode.ThemeColor("charts.green"));
  }
  if (status.startsWith("PAUSED") || status.startsWith("AWAITING")) {
    return new vscode.ThemeIcon("report", new vscode.ThemeColor("charts.orange"));
  }
  return new vscode.ThemeIcon("sync", new vscode.ThemeColor("charts.blue"));
}

function basename(path: string): string {
  return path.split("/").filter(Boolean).pop() ?? path;
}

function firstLine(text: string): string {
  return text.split("\n")[0] ?? text;
}

function trim(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}
