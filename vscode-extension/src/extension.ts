/**
 * AutoCode for VS Code.
 *
 * A thin client (issue #884, roadmap #1006): it starts the `autocode` CLI and
 * shows the status view, and it may do nothing the headless conformance client
 * (#1012) cannot do through `TaskRun`. It never reads `state.json`, never
 * imports runner internals, and never launches a model without a person asking.
 */

import * as vscode from "vscode";
import { RunController, type RunRef } from "./controller";
import { RunPanel } from "./panel";
import { RunsProvider, type RunItem } from "./runsView";

type RunTarget = RunRef | RunItem;

export function activate(context: vscode.ExtensionContext): void {
  const log = vscode.window.createOutputChannel("AutoCode", { log: true });
  const controller = new RunController(log);
  const runs = new RunsProvider(controller);
  let active: RunRef | undefined;

  const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 90);

  /** The run the panel or the tree last named, or the one a command was invoked on. */
  const targetOf = (passed?: RunTarget): RunRef | undefined => {
    if (!passed) return active;
    const run = "run" in passed ? passed.run : passed;
    return run.workspace && run.runDir ? { workspace: run.workspace, runDir: run.runDir } : undefined;
  };

  const showRun = (run: RunRef): void => {
    active = run;
    RunPanel.show(vscode.ViewColumn.One, run, controller);
    void refresh();
  };

  const updateStatus = async (): Promise<void> => {
    if (!active) {
      status.hide();
      return;
    }
    try {
      const view = await controller.status(active);
      status.text = `$(autorenew) AutoCode: ${view.progress?.headline ?? view.status}`;
      status.tooltip = view.progress?.line ?? view.status;
      status.command = { command: "autocode.openRun", title: "Open Run", arguments: [active] };
      status.show();
    } catch {
      status.hide();
    }
  };

  const refresh = async (): Promise<void> => {
    await runs.refresh();
    await updateStatus();
  };

  context.subscriptions.push(
    log,
    runs,
    status,
    vscode.window.createTreeView("autocode.runs", { treeDataProvider: runs, showCollapseAll: true }),
    vscode.commands.registerCommand("autocode.startRun", async () => {
      const folder = await pickWorkspace();
      if (!folder) return;
      const brief = await vscode.window.showInputBox({
        title: `Start an AutoCode run in ${basename(folder)}`,
        prompt: "The task, in your words. AutoCode recognizes the kind of job from it.",
        placeHolder: "Fix the failing test in tests/test_login.py",
        ignoreFocusOut: true,
      });
      if (!brief) return;
      if (!(await confirmSpend())) return;
      try {
        showRun(await controller.start(folder, brief));
      } catch (error) {
        void vscode.window
          .showErrorMessage(`AutoCode could not start the run: ${messageOf(error)}`, "Open output")
          .then((choice) => (choice === "Open output" ? log.show() : undefined));
      }
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
      if (!target) return;
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
      if (!target) return;
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
      if (target) active = target;
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
 * Starting a run launches real models, so a person confirms the spend first.
 *
 * This mirrors what `--i-authorize-live-model-spend` asks of an agent: the
 * extension never decides on its own to start paid work.
 */
async function confirmSpend(): Promise<boolean> {
  const provider = vscode.workspace.getConfiguration("autocode").get<string>("provider", "").trim() || "its default";
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

function basename(path: string): string {
  return path.split("/").filter(Boolean).pop() ?? path;
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
