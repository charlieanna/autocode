/**
 * AutoCode for VS Code.
 *
 * A thin client (issue #884, roadmap #1006): it starts the `autocode` CLI and
 * shows the status view, and it may do nothing the headless conformance client
 * (#1012) cannot do through `TaskRun`. It never reads `state.json`, never
 * imports runner internals, and never launches a model without a person asking.
 */

import * as vscode from "vscode";
import { statusLine } from "./statusBar";
import { RunController, type RunRef } from "./controller";
import { RunPanel } from "./panel";
import { RunsProvider, type RunItem } from "./runsView";
import { ChatView } from "./chat";
import { ConnectionsPanel } from "./connections";
import { StagesPanel } from "./stages";
import { readSettings, spendTarget } from "./settings";

type RunTarget = RunRef | RunItem;

export function activate(context: vscode.ExtensionContext): void {
  const log = vscode.window.createOutputChannel("AutoCode", { log: true });
  const controller = new RunController(log);
  const runs = new RunsProvider(controller);
  const connections = new ConnectionsPanel(context.workspaceState);
  const stagePanel = new StagesPanel();
  let active: RunRef | undefined;
  let watched: RunRef | undefined;
  let statusSequence = 0;

  const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 90);

  /** True when two run pointers identify the same workspace and run directory. */
  const sameRun = (a?: RunRef, b?: RunRef): boolean =>
    Boolean(a && b && a.workspace === b.workspace && a.runDir === b.runDir);

  /** The run the panel or the tree last named, or the one a command was invoked on. */
  const targetOf = (passed?: RunTarget): RunRef | undefined => {
    if (!passed) return active;
    const run = "run" in passed ? passed.run : passed;
    return run.workspace && run.runDir ? { workspace: run.workspace, runDir: run.runDir } : undefined;
  };

  const showRun = (run: RunRef): void => {
    active = run;
    watched = run;
    chat.show(run);
    void refresh();
  };

  const updateStatus = async (): Promise<void> => {
    const sequence = ++statusSequence;
    if (!watched) {
      status.hide();
      return;
    }
    const currentWatched = watched;
    try {
      const view = await controller.pollStatus(currentWatched);
      if (sequence !== statusSequence || !watched || !sameRun(watched, currentWatched)) {
        return;
      }
      const line = statusLine(view);
      status.text = line.text;
      status.tooltip = line.tooltip;
      status.command = { command: "autocode.openRun", title: "Open Run", arguments: [currentWatched] };
      status.show();
    } catch {
      if (sequence === statusSequence && sameRun(watched, currentWatched)) {
        status.hide();
      }
    }
  };

  const refresh = async (): Promise<void> => {
    await runs.refresh();
    await updateStatus();
    await chat.refresh();
  };

  const chat = new ChatView(controller, context.workspaceState, {
    pickWorkspace, confirmStart: confirmSpend,
    changed: () => { void refresh(); },
    selected: (run) => {
      active = run;
      if (run) watched = run;
      void updateStatus();
    },
  });

  context.subscriptions.push(
    log,
    runs,
    status,
    chat,
    connections,
    stagePanel,
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("autocode")) {
        void refresh();
      }
    }),
    vscode.commands.registerCommand("autocode.connections", () => connections.show().catch((error) => {
      void vscode.window.showErrorMessage(`AutoCode Connections: ${messageOf(error)}`);
    })),
    vscode.commands.registerCommand("autocode.stages", () => stagePanel.show().catch((error) => {
      void vscode.window.showErrorMessage(`AutoCode Stage Models: ${messageOf(error)}`);
    })),
    vscode.window.registerWebviewViewProvider("autocode.chat", chat, { webviewOptions: { retainContextWhenHidden: true } }),
    vscode.window.createTreeView("autocode.runs", { treeDataProvider: runs, showCollapseAll: true }),
    vscode.commands.registerCommand("autocode.showChat", () => chat.focus()),
    vscode.commands.registerCommand("autocode.startRun", async () => {
      chat.show();
    }),
    vscode.commands.registerCommand("autocode.openDetails", (run?: RunTarget) => {
      const target = targetOf(run);
      if (!target) return void vscode.window.showInformationMessage("Select a run first.");
      RunPanel.show(vscode.ViewColumn.One, target, controller, refresh);
    }),

    vscode.commands.registerCommand("autocode.refresh", () => refresh()),

    vscode.commands.registerCommand("autocode.openRun", (run?: RunTarget) => {
      const target = targetOf(run);
      if (!target) return void vscode.window.showInformationMessage("Select a run first.");
      showRun(target);
    }),

    vscode.commands.registerCommand("autocode.takeAction", (run?: RunTarget) => {
      const target = targetOf(run);
      if (!target) return void vscode.window.showInformationMessage("Select a run first.");
      showRun(target);
    }),

    vscode.commands.registerCommand("autocode.showCliCommand", async (run?: RunTarget) => {
      const target = targetOf(run);
      if (!target) return void vscode.window.showInformationMessage("Select a run first.");
      try {
        const view = await controller.status(target);
        await vscode.env.clipboard.writeText(controller.commandText(view, target));
        void vscode.window.showInformationMessage(
          "Copied the command AutoCode named. Its issued token travels in the environment, not the command line.",
        );
      } catch (error) {
        void vscode.window.showErrorMessage(`AutoCode: ${messageOf(error)}`);
      }
    }),

    vscode.commands.registerCommand("autocode.continueRun", async (run?: RunTarget) => {
      const target = targetOf(run);
      if (!target) return void vscode.window.showInformationMessage("Select a run first.");
      const proceed = await vscode.window.showWarningMessage(
        "Continue this run? AutoCode will launch its next stage on the configured provider, which spends your model allowance.",
        { modal: true },
        "Continue",
      );
      if (proceed !== "Continue") return;
      try {
        const view = await controller.status(target);
        await controller.take(view, target);
      } catch (error) {
        void vscode.window.showErrorMessage(`AutoCode: ${messageOf(error)}`);
      }
      await refresh();
    }),

    vscode.commands.registerCommand("autocode.watchRun", (run?: RunTarget) => {
      const target = targetOf(run);
      if (!target) return void vscode.window.showInformationMessage("Select a run first.");
      if (sameRun(watched, target)) {
        watched = undefined;
      } else {
        watched = target;
        active = target;
      }
      void updateStatus();
    }),

    vscode.commands.registerCommand("autocode.showLog", () => log.show()),
  );

  const poll = setInterval(() => void refresh(), pollSeconds() * 1000);
  context.subscriptions.push({ dispose: () => clearInterval(poll) });

  void refresh();
}

export function deactivate(): void {
  RunPanel.disposeAll();
}

/** A workspace to run in: the folder you picked, or the one VS Code has open. */
async function pickWorkspace(): Promise<string | undefined> {
  const folders = vscode.workspace.workspaceFolders ?? [];
  if (folders.length === 0) {
    const picked = await vscode.window.showWorkspaceFolderPick({ placeHolder: "Run AutoCode in which folder?" });
    return picked?.uri.fsPath;
  }
  if (folders.length === 1) return folders[0]!.uri.fsPath;
  const picked = await vscode.window.showQuickPick(
    folders.map((folder) => ({ label: folder.name, description: folder.uri.fsPath, fsPath: folder.uri.fsPath })),
    { placeHolder: "Run AutoCode in which folder?" },
  );
  return picked?.fsPath;
}

/**
 * Modes that can plan immediately confirm spend at launch. Full chat defers
 * this decision to the core-named Requirements review before planning (#1612).
 */
async function confirmSpend(): Promise<boolean> {
  const provider = spendTarget(readSettings());
  const choice = await vscode.window.showWarningMessage(
    `Start a live AutoCode run through provider "${provider}"? It spends your model allowance and can run for a long time.`,
    { modal: true },
    "Start it",
  );
  return choice === "Start it";
}

function pollSeconds(): number {
  return Math.max(1, vscode.workspace.getConfiguration("autocode").get<number>("pollSeconds", 5));
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
