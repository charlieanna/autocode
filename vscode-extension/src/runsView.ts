/** The Runs side bar: the local run registry, read through the CLI. */

import * as vscode from "vscode";
import type { RunController } from "./controller";
import { byLatest, recentRuns, relativeTime } from "./core/recent";
import type { RegistryRun } from "./core/view";

export interface WorkspaceNode {
  readonly kind: "workspace";
  label: string;
  runs: RunItem[];
  /** The Recent section: the newest chats of every project, above the project groups. */
  recent?: boolean;
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
  ) {
    super(labelFor(run), vscode.TreeItemCollapsibleState.None);
    // A run listed under Recent is also listed under its project; tree ids must differ.
    this.id = recent ? `recent:${run.id}` : run.id;
    this.resourceUri = vscode.Uri.file(run.runDir);
    this.description = describe(run, recent, now);
    this.tooltip = new vscode.MarkdownString(
      [
        `**${run.title ?? run.workspace}**`,
        "",
        run.title ? `- project: \`${run.workspace}\`` : "",
        `- run: \`${run.runDir}\``,
        run.updatedAt ? `- last active: ${new Date(run.updatedAt).toLocaleString()} (${relativeTime(run, now)})` : "",
        `- status: ${run.status ?? "unknown"}`,
        `- phase: ${run.phase ?? "unknown"}`,
        `- next stage: ${run.nextStage ?? "unknown"}`,
        `- availability: ${run.availability}`,
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
  private loading = false;

  async refresh(): Promise<void> {
    if (this.loading) return;
    this.loading = true;
    try {
      const list = await this.controller.list();
      this.nodes = groupByWorkspace(list.runs);
    } catch (error) {
      this.nodes = [placeholder(error)];
    } finally {
      this.loading = false;
      this.changed.fire(undefined);
    }
  }

  getTreeItem(node: RunNode): vscode.TreeItem {
    return node.kind === "workspace" ? workspaceItem(node) : node;
  }

  getChildren(node?: RunNode): RunNode[] {
    if (!node) return this.nodes;
    return node.kind === "workspace" ? node.runs : [];
  }

  dispose(): void {
    this.changed.dispose();
  }
}

/**
 * Recent chats first, then each project's chats, newest first. Projects are ordered by
 * their newest chat, and fold away under a Recent section so the latest chats are on top.
 */
function groupByWorkspace(runs: RegistryRun[], now = Date.now()): RunNode[] {
  const byWorkspace = new Map<string, RegistryRun[]>();
  for (const run of [...runs].sort(byLatest)) {
    const items = byWorkspace.get(run.workspace) ?? [];
    items.push(run);
    byWorkspace.set(run.workspace, items);
  }
  const nodes: RunNode[] = [];
  const recent = recentRuns(runs);
  if (recent.length > 0) {
    const items = recent.map((run) => new RunItem(run, run.workspace, true, now));
    nodes.push({ kind: "workspace", label: "Recent", recent: true, runs: items });
  }
  // Each project's runs are already newest first, so its first run is its latest activity.
  for (const [workspace, items] of [...byWorkspace].sort((a, b) => byLatest(a[1][0]!, b[1][0]!))) {
    nodes.push({
      kind: "workspace",
      label: basename(workspace),
      collapsed: recent.length > 0,
      runs: items.map((run) => new RunItem(run, run.workspace, false, now)),
    });
  }
  if (nodes.length === 0) {
    nodes.push({
      kind: "workspace",
      label: "No registered runs",
      runs: [messageItem("Start a run, or run `autocode registry import` on a folder")],
    });
  }
  return nodes;
}

function workspaceItem(node: WorkspaceNode): vscode.TreeItem {
  const state = node.collapsed ? vscode.TreeItemCollapsibleState.Collapsed : vscode.TreeItemCollapsibleState.Expanded;
  const item = new vscode.TreeItem(node.label, state);
  item.id = node.recent ? "recent" : `workspace:${node.runs[0]?.run.workspace ?? node.label}`;
  item.description = node.recent ? "" : `${node.runs.length}`;
  item.iconPath = new vscode.ThemeIcon(node.recent ? "history" : "folder");
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
    const identity = run.status ?? run.availability;
    const short = run.runDir.split("/").filter(Boolean).slice(-1)[0] ?? "";
    return short ? trim(short, 46) : identity;
  }
  return `${basename(run.workspace)} (${run.availability})`;
}

function describe(run: RegistryRun, recent: boolean, now: number): string {
  if (run.availability !== "available") return run.diagnostic ?? "";
  const when = relativeTime(run, now);
  // Under Recent the project tells chats apart; under a project the status does.
  const stage = run.nextStage ? ` → ${run.nextStage}` : "";
  const detail = recent ? basename(run.workspace) : `${run.status ?? "unknown"}${stage}`;
  return when ? `${when} · ${detail}` : detail;
}

function contextFor(run: RegistryRun): string | undefined {
  if (run.availability !== "available") return undefined;
  const status = run.status ?? "";
  if (/^(RUNNING|BUILDING|WORKING|DISPATCHING|EXECUTING)/i.test(status)) return "autocode.runActive";
  return status ? "autocode.runNeeds" : undefined;
}

function iconFor(run: RegistryRun): vscode.ThemeIcon {
  if (run.availability !== "available") return new vscode.ThemeIcon("dash");
  const status = (run.status ?? "").toUpperCase();
  if (status === "COMPLETE" || status === "COMPLETED") return new vscode.ThemeIcon("check", new vscode.ThemeColor("charts.green"));
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
