/** Sidebar conversation over the public status view and its named actions. */
import * as vscode from "vscode";
import { RunController, type RunRef } from "./controller";
import { leavesForPerson } from "./core/next-command";
import { needsSummary, type StatusView } from "./core/view";
import { panelHtml, stateFor } from "./panel";

interface ChatMessage {
  type?: string;
  text?: string;
  runKey?: string;
  context?: string;
  decisionContext?: string;
  token?: string | null;
  answers?: Record<string, string>;
}

interface Actions {
  pickWorkspace(): Promise<string | undefined>;
  confirmStart(): Promise<boolean>;
  changed(): void;
  selected(run: RunRef): void;
}

export class ChatView implements vscode.WebviewViewProvider, vscode.Disposable {
  private view: vscode.WebviewView | undefined;
  private run: RunRef | undefined;
  private status: StatusView | undefined;
  private error = "";
  private readError = "";
  private starting = false;
  private pendingBrief = "";
  private readonly busy = new Set<string>();
  private sequence = 0;
  private refreshing = false;
  private readonly timer: NodeJS.Timeout;

  constructor(private readonly controller: RunController, private readonly storage: vscode.Memento, private readonly actions: Actions) {
    this.run = storage.get<RunRef>("autocode.chat.run");
    const seconds = Math.max(1, vscode.workspace.getConfiguration("autocode").get<number>("pollSeconds", 5));
    this.timer = setInterval(() => void this.refresh(), seconds * 1000);
  }

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    view.webview.options = { enableScripts: true, localResourceRoots: [] };
    view.webview.onDidReceiveMessage((message: ChatMessage) => void this.receive(message));
    view.onDidChangeVisibility(() => { if (view.visible) void this.refresh(); });
    view.onDidDispose(() => { if (this.view === view) this.view = undefined; });
    view.webview.html = panelHtml(true);
    if (this.run) this.actions.selected(this.run);
    void this.refresh();
  }

  show(run?: RunRef): void {
    this.sequence++;
    this.run = run;
    this.status = undefined;
    this.pendingBrief = "";
    this.error = "";
    this.readError = "";
    void this.storage.update("autocode.chat.run", run);
    if (run) this.actions.selected(run);
    void this.focus();
    this.post();
    void this.refresh();
  }

  async focus(): Promise<void> {
    await vscode.commands.executeCommand("setContext", "autocode.showHistory", false);
    await vscode.commands.executeCommand("autocode.chat.focus");
  }

  dispose(): void { clearInterval(this.timer); }

  private key(run = this.run): string { return run ? `${run.workspace}\n${run.runDir}` : ""; }

  private post(): void {
    const busy = this.starting || this.busy.has(this.key());
    void this.view?.webview.postMessage({
      type: "chat", runKey: this.key(), busy, error: this.error || this.readError, pendingBrief: this.pendingBrief,
      state: this.run && this.status ? stateFor(this.status, this.run, this.controller, busy) : null,
    });
  }

  async refresh(): Promise<void> {
    if (!this.view?.visible || this.refreshing) return;
    const run = this.run;
    const sequence = this.sequence;
    if (!run) { this.post(); return; }
    this.refreshing = true;
    try {
      const status = await this.controller.status(run);
      if (sequence === this.sequence) { this.status = status; this.readError = ""; }
    } catch (error) {
      if (sequence === this.sequence && !this.starting) this.readError = messageOf(error);
    } finally {
      this.refreshing = false;
      if (sequence === this.sequence) this.post();
      else void this.refresh();
    }
  }

  private async receive(message: ChatMessage): Promise<void> {
    if (message.type === "connections") { await vscode.commands.executeCommand("autocode.connections"); return; }
    if (message.type === "history") {
      await vscode.commands.executeCommand("setContext", "autocode.showHistory", true);
      await vscode.commands.executeCommand("autocode.runs.focus");
      return;
    }
    if (message.type === "details") { if (this.run) await vscode.commands.executeCommand("autocode.openDetails", this.run); return; }
    if (message.type === "ready" || message.type === "refresh") { await this.refresh(); return; }
    if (message.type === "new") { if (!this.starting) this.show(); return; }
    if (message.type !== "send" && message.type !== "take") return;
    if (message.runKey !== this.key() || this.starting || this.busy.has(this.key())) return;
    if (!this.run) {
      if (message.type === "send" && typeof message.text === "string" && message.text.trim()) await this.start(message.text);
      return;
    }
    const run = this.run;
    const key = this.key(run);
    this.busy.add(key);
    this.error = "";
    this.post();
    try {
      const view = await this.controller.status(run);
      if (message.type === "send") {
        if (typeof message.text !== "string") return;
        await this.controller.sendMessage(view, run, message.text, message.context ?? "");
        void this.view?.webview.postMessage({ type: "sent", runKey: key, text: message.text });
        await this.advanceAfterInput(run);
      } else {
        const state = stateFor(view, run, this.controller);
        if (message.decisionContext !== state.decisionContext) throw new Error("The requested action changed; review it before continuing");
        if (leavesForPerson(view.needs ?? null) && vscode.workspace.getConfiguration("autocode").get("confirmPersonOnly", true)) {
          const answer = await vscode.window.showWarningMessage(
            "AutoCode is waiting for your recovery decision.", { modal: true, detail: needsSummary(view) }, "Continue",
          );
          if (answer !== "Continue") return;
        }
        await this.controller.take(view, run, message.answers ?? {}, message.token ?? null);
        // These buttons explicitly say "and continue". Never infer another
        // answer or approval, or release a newly encountered person-only stop.
        if (view.needs?.kind === "approve_plan" || state.decisions.length) await this.advanceAfterInput(run);
      }
    } catch (error) {
      if (this.key() === key) this.error = messageOf(error);
      else void vscode.window.showErrorMessage(`AutoCode (${run.runDir}): ${messageOf(error)}`);
    } finally {
      this.busy.delete(key);
      this.actions.changed();
      await this.refresh();
    }
  }

  private async advanceAfterInput(run: RunRef): Promise<void> {
    const view = await this.controller.status(run);
    if (!view.done && view.needs?.kind === "continue") await this.controller.take(view, run);
  }

  private async start(text: string): Promise<void> {
    this.starting = true;
    this.error = "";
    this.post();
    try {
      const workspace = await this.actions.pickWorkspace();
      if (!workspace || !(await this.actions.confirmStart())) return;
      await this.controller.start(workspace, text, (run) => {
        this.run = run;
        this.status = undefined;
        this.sequence++;
        this.pendingBrief = text;
        void this.storage.update("autocode.chat.run", run);
        this.actions.selected(run);
        // Clear only the submitted draft, preserving a message typed meanwhile.
        void this.view?.webview.postMessage({ type: "sent", runKey: "", text });
        this.post();
      });
    } catch (error) { this.error = messageOf(error); }
    finally {
      this.starting = false;
      this.actions.changed();
      await this.refresh();
      this.post();
    }
  }
}

function messageOf(error: unknown): string { return error instanceof Error ? error.message : String(error); }
