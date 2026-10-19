/** Sidebar conversation over the public status view and its named actions.
 *
 * One window holds several chats at once (#1558): each tab is one run or one
 * fresh not-yet-started chat. Every open run tab polls (#1566); every tab keeps
 * its own draft (keyed by tab key in the webview), status and errors.
 */
import * as vscode from "vscode";
import { randomBytes } from "node:crypto";
import { RunController, type CheckpointComparison, type RunRef } from "./controller";
import { leavesForPerson } from "./core/next-command";
import type { StatusView } from "./core/view";
import { transitionNotice } from "./notify";
import { applyEngine, engineOf, ENGINES, readSettings, type Engine } from "./settings";
import { NUMERIC_RECOVERY_EFFECT, numericConfirmation, panelHtml, stateFor } from "./panel";

interface ChatMessage {
  type?: string;
  text?: string;
  runKey?: string;
  hasSelectedRun?: boolean;
  selectedRun?: unknown;
  context?: string;
  decisionContext?: string;
  token?: string | null;
  answers?: Record<string, string>;
  checkpoint?: string;
  action?: "compare" | "restore";
  workspace?: string;
  runDir?: string;
  /** Tab messages (#1558): the webview addresses tabs by their key. */
  tabKey?: string;
  tabs?: { key?: string; run?: unknown }[];
  activeKey?: string;
}

interface Actions {
  pickWorkspace(): Promise<string | undefined>;
  confirmStart(): Promise<boolean>;
  changed(): void;
  selected(run?: RunRef): void;
}

/** One open chat: a run, or a fresh chat that has not started yet. */
interface ChatTab {
  /** The run key for a run tab; a generated `new-…` key for a fresh chat. */
  key: string;
  run?: RunRef;
  status?: StatusView;
  error: string;
  readError: string;
  pendingBrief: string;
  starting: boolean;
  /** Compare results this tab already holds, by checkpoint id. */
  readonly comparisons: Map<string, CheckpointComparison>;
}

function newTab(key: string, run?: RunRef): ChatTab {
  return { key, run, error: "", readError: "", pendingBrief: "", starting: false, comparisons: new Map() };
}

function tabName(tab: ChatTab): string {
  if (!tab.run) return "New chat";
  const name = tab.run.runDir.split("/").filter(Boolean).pop() ?? tab.run.runDir;
  return name.length > 22 ? `${name.slice(0, 21)}…` : name;
}

export class ChatView implements vscode.WebviewViewProvider, vscode.Disposable {
  private view: vscode.WebviewView | undefined;
  private tabs: ChatTab[] = [newTab("new")];
  private activeKey = "new";
  private readonly legacyRun: RunRef | undefined;
  private hasExplicitSelection = false;
  private migratedLegacySelection = false;
  private readonly busy = new Set<string>();
  /** Wait identities already announced, keyed by run key: a new tab for the same run does not re-announce. */
  private readonly notified = new Map<string, Set<string>>();
  private sequence = 0;
  private refreshing = false;
  private timer: NodeJS.Timeout | undefined;
  private readonly configuration: vscode.Disposable;

  constructor(private readonly controller: RunController, private readonly storage: vscode.Memento, private readonly actions: Actions) {
    // Keep the pre-multi-window selection only for one-time migration. The
    // open tabs now live in this window's webview state; workspaceState is
    // shared by windows opened on the same workspace.
    this.legacyRun = storage.get<RunRef>("autocode.chat.run");
    this.startPolling();
    this.configuration = vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration("autocode.pollSeconds")) this.startPolling();
    });
  }

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    view.webview.options = { enableScripts: true, localResourceRoots: [] };
    view.webview.onDidReceiveMessage((message: ChatMessage) => void this.receive(message));
    view.onDidChangeVisibility(() => { if (view.visible) void this.refresh(); });
    view.onDidDispose(() => { if (this.view === view) this.view = undefined; });
    view.webview.html = panelHtml(true);
    if (this.active.run) this.actions.selected(this.active.run);
    void this.refresh();
  }

  /** Open (or focus) a tab for `run`; without one, open another fresh chat tab. */
  show(run?: RunRef): void {
    if (run) {
      const existing = this.tabs.find((tab) => tab.key === runKey(run));
      if (existing) this.activate(existing.key);
      else {
        // The boot placeholder ("new", never used) is adopted by the first run
        // opened; explicit "+" tabs (new-…) stay until closed.
        this.tabs = this.tabs.filter((tab) => tab.key !== "new");
        const tab = newTab(runKey(run), run);
        this.tabs.push(tab);
        this.activate(tab.key);
      }
      return;
    }
    const tab = newTab(`new-${randomBytes(4).toString("hex")}`);
    this.tabs.push(tab);
    this.activate(tab.key);
  }

  private activate(key: string): void {
    if (!this.tabs.some((tab) => tab.key === key)) return;
    this.sequence++;
    this.activeKey = key;
    this.hasExplicitSelection = true;
    this.actions.selected(this.active.run);
    void this.focus();
    this.post(true);
    void this.refresh();
  }

  private close(key: string): void {
    const index = this.tabs.findIndex((tab) => tab.key === key);
    if (index < 0 || this.tabs.length === 1) return;
    this.tabs.splice(index, 1);
    if (this.activeKey === key) {
      const neighbor = this.tabs[Math.min(index, this.tabs.length - 1)] ?? this.tabs[0]!;
      this.activate(neighbor.key);
    } else {
      this.post(true);
    }
  }

  async focus(): Promise<void> {
    await vscode.commands.executeCommand("setContext", "autocode.showHistory", false);
    await vscode.commands.executeCommand("autocode.chat.focus");
  }

  private startPolling(): void {
    if (this.timer) clearInterval(this.timer);
    const seconds = Math.max(1, vscode.workspace.getConfiguration("autocode").get<number>("pollSeconds", 5));
    this.timer = setInterval(() => void this.refresh(), seconds * 1000);
  }

  dispose(): void {
    if (this.timer) clearInterval(this.timer);
    this.configuration.dispose();
  }

  private get active(): ChatTab { return this.tabs.find((tab) => tab.key === this.activeKey) ?? this.tabs[0]!; }

  /** The run the active tab holds; the window's status bar follows it. */
  get run(): RunRef | undefined { return this.active.run; }

  private post(persistSelection = false): void {
    if (!this.view?.visible) return;
    const tab = this.active;
    const busy = tab.starting || this.busy.has(tab.key);
    void this.view?.webview.postMessage({
      type: "chat", runKey: tab.key, busy, error: tab.error || tab.readError, pendingBrief: tab.pendingBrief,
      selectedRun: tab.run ?? null, persistSelection,
      tabs: this.tabs.map((row) => ({ key: row.key, name: tabName(row), busy: row.starting || this.busy.has(row.key), run: row.run ?? null })),
      activeKey: this.activeKey,
      engine: engineOf(readSettings()),
      state: tab.run && tab.status
        ? { ...stateFor(tab.status, tab.run, this.controller, busy), unreadable: Boolean(tab.readError) } : null,
    });
  }

  async refresh(): Promise<void> {
    if (this.refreshing) return;
    const sequence = this.sequence;
    this.refreshing = true;
    try {
      for (const tab of [...this.tabs]) {
        if (!tab.run) continue;
        try {
          const status = await this.controller.status(tab.run);
          if (this.tabs.includes(tab)) this.announce(tab, status);
        } catch (error) {
          if (sequence === this.sequence && !tab.starting) tab.readError = messageOf(error);
        }
      }
    } finally {
      this.refreshing = false;
      if (sequence === this.sequence) this.post();
      else void this.refresh();
    }
  }

  /** Land the tab's fresh status and announce a new person-wait or a finished run once per run. */
  private announce(tab: ChatTab, status: StatusView): void {
    const notice = transitionNotice(tab.status, status);
    tab.status = status;
    tab.readError = "";
    if (!notice || !tab.run) return;
    const run = tab.run;
    const key = runKey(run);
    let seen = this.notified.get(key);
    if (!seen) { seen = new Set(); this.notified.set(key, seen); }
    if (seen.has(notice.identity)) return;
    seen.add(notice.identity);
    if (vscode.workspace.getConfiguration("autocode").get<boolean>("notify", true) === false) return;
    void (async () => {
      const choice = await vscode.window.showInformationMessage(
        `AutoCode: ${tabName(tab)} is waiting for you - ${notice.tail}`, "Open chat",
      );
      if (choice === "Open chat") this.show(run);
    })();
  }

  private async receive(message: ChatMessage): Promise<void> {
    if (message.type === "connections") { await vscode.commands.executeCommand("autocode.connections"); return; }
    if (message.type === "history") {
      await vscode.commands.executeCommand("setContext", "autocode.showHistory", true);
      await vscode.commands.executeCommand("autocode.runs.focus");
      return;
    }
    if (message.type === "details") { if (this.active.run) await vscode.commands.executeCommand("autocode.openDetails", this.active.run); return; }
    if (message.type === "ready") {
      this.restoreTabs(message);
      if (!this.migratedLegacySelection) {
        this.migratedLegacySelection = true;
        await this.storage.update("autocode.chat.run", undefined);
      }
      this.sequence++;
      for (const tab of this.tabs) tab.status = undefined;
      if (this.active.run) this.actions.selected(this.active.run);
      this.post(true);
      await this.refresh();
      return;
    }
    if (message.type === "refresh") { await this.refresh(); return; }
    if (message.type === "select_tab" && message.tabKey) { this.activate(message.tabKey); return; }
    if (message.type === "close_tab" && message.tabKey) { this.close(message.tabKey); return; }
    if (message.type === "checkpoint") { await this.checkpoint(message); return; }
    if (message.type === "checkpoint_open") {
      const opened = asRunRef({ workspace: message.workspace, runDir: message.runDir });
      if (opened) this.show(opened);
      return;
    }
    if (message.type === "engine") {
      await pickEngine();
      this.post();
      return;
    }
    if (message.type === "new") { if (!this.active.starting) this.show(); return; }
    if (message.type !== "send" && message.type !== "take") return;
    const tab = this.active;
    if (message.runKey !== tab.key || tab.starting || this.busy.has(tab.key)) return;
    if (!tab.run) {
      if (message.type === "send" && typeof message.text === "string" && message.text.trim()) await this.start(tab, message.text);
      return;
    }
    const run = tab.run;
    const key = tab.key;
    this.busy.add(key);
    tab.error = "";
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
        const numeric = numericConfirmation(state, message.answers ?? {});
        if (numeric || (leavesForPerson(view.needs ?? null) && vscode.workspace.getConfiguration("autocode").get("confirmPersonOnly", true))) {
          const button = numeric ? "Set total and continue" : "Continue";
          const answer = await vscode.window.showWarningMessage(
            numeric ?? (state.actionLabel ? `${state.actionLabel}?` : "Continue AutoCode?"), { modal: true,
              detail: numeric ? NUMERIC_RECOVERY_EFFECT : state.actionEffect ?? view.chat?.notice ?? "Open Run details to review what needs attention before continuing." }, button,
          );
          if (answer !== button) return;
        }
        await this.controller.take(view, run, message.answers ?? {}, message.token ?? null);
        // These buttons explicitly say "and continue". Never infer another
        // answer or approval, or release a newly encountered person-only stop.
        if (view.needs?.kind === "approve_plan" || state.decisions.some((row) => row.kind !== "integer")) await this.advanceAfterInput(run);
      }
    } catch (error) {
      if (this.activeKey === key) tab.error = messageOf(error);
      else void vscode.window.showErrorMessage(`AutoCode (${run.runDir}): ${messageOf(error)}`);
    } finally {
      this.busy.delete(key);
      this.actions.changed();
      await this.refresh();
    }
  }

  /** Tabs remembered by this window's webview; an older state held one selectedRun. */
  private restoreTabs(message: ChatMessage): void {
    if (this.hasExplicitSelection) {
      this.hasExplicitSelection = false;
      return;
    }
    const saved = (message.tabs ?? [])
      .map((row) => ({ key: typeof row.key === "string" ? row.key : "", run: asRunRef(row.run) }))
      .filter((row) => row.key && (row.run || row.key.startsWith("new")));
    if (saved.length) {
      this.tabs = saved.map((row) => newTab(row.key, row.run));
      this.activeKey = this.tabs.some((tab) => tab.key === message.activeKey)
        ? message.activeKey! : this.tabs[0]!.key;
      return;
    }
    const restored = message.hasSelectedRun ? asRunRef(message.selectedRun) : this.legacyRun;
    if (restored) {
      this.tabs = [newTab(runKey(restored), restored)];
      this.activeKey = this.tabs[0]!.key;
    }
  }

  private async advanceAfterInput(run: RunRef): Promise<void> {
    const view = await this.controller.status(run);
    if (!view.done && view.needs?.kind === "continue") await this.controller.take(view, run);
  }

  /**
   * A saved code change the person acted on. Compare is a read; restore is a
   * person's confirmed decision, always gated on the comparison it quotes.
   */
  private async checkpoint(message: ChatMessage): Promise<void> {
    const tab = this.active;
    const run = tab.run;
    const checkpointId = message.checkpoint ?? "";
    if (!run || !checkpointId || message.runKey !== tab.key || this.busy.has(tab.key)) return;
    const key = tab.key;
    this.busy.add(key);
    try {
      if (message.action === "compare") {
        const comparison = await this.controller.compareCheckpoint(run, checkpointId);
        tab.comparisons.set(checkpointId, comparison);
        void this.view?.webview.postMessage({ type: "checkpoint_compare", runKey: key, checkpoint: checkpointId, comparison });
        return;
      }
      if (message.action !== "restore") return;
      let comparison = tab.comparisons.get(checkpointId);
      if (!comparison) {
        comparison = await this.controller.compareCheckpoint(run, checkpointId);
        tab.comparisons.set(checkpointId, comparison);
        void this.view?.webview.postMessage({ type: "checkpoint_compare", runKey: key, checkpoint: checkpointId, comparison });
      }
      if (comparison.blocked_reason) throw new Error(comparison.blocked_reason);
      const label = (tab.status?.code_checkpoints?.rows ?? []).find((row) => row.id === checkpointId)?.label ?? checkpointId;
      const files = comparison.changed_files?.length ?? 0;
      const proceed = await vscode.window.showWarningMessage(
        `Restore "${label}" on a new branch?`,
        { modal: true,
          detail: `${files} file(s) differ from this checkpoint. The current branch and later work stay intact; every milestone needs fresh verification, and the new candidate stays paused until you resume it.` },
        "Restore",
      );
      if (proceed !== "Restore") return;
      const requestId = await this.checkpointRequestId(key);
      const result = await this.controller.restoreCheckpoint(run, checkpointId, comparison.expected_token ?? "", requestId);
      tab.comparisons.delete(checkpointId);
      await this.clearCheckpointRequest(key);
      void this.view?.webview.postMessage({ type: "checkpoint_result", runKey: key, checkpoint: checkpointId, result });
    } catch (error) {
      void this.view?.webview.postMessage({ type: "checkpoint_compare", runKey: key, checkpoint: checkpointId, error: messageOf(error) });
    } finally {
      this.busy.delete(key);
      this.actions.changed();
      await this.refresh();
    }
  }

  /** A stable request id per run: replaying it reconciles a candidate instead of creating another. */
  private async checkpointRequestId(key: string): Promise<string> {
    const saved = this.storage.get<Record<string, string>>("autocode.checkpoint.requests") ?? {};
    const existing = saved[key];
    if (existing && /^[A-Za-z0-9_-]{8,100}$/.test(existing)) return existing;
    const id = `vscode-${randomBytes(12).toString("hex")}`;
    saved[key] = id;
    await this.storage.update("autocode.checkpoint.requests", saved);
    return id;
  }

  private async clearCheckpointRequest(key: string): Promise<void> {
    const saved = this.storage.get<Record<string, string>>("autocode.checkpoint.requests") ?? {};
    if (!(key in saved)) return;
    delete saved[key];
    await this.storage.update("autocode.checkpoint.requests", saved);
  }

  private async start(tab: ChatTab, text: string): Promise<void> {
    if (tab.starting) return;
    tab.starting = true;
    tab.error = "";
    this.post();
    const launchSequence = this.sequence;
    const freshKey = tab.key;
    let allocatedRun: RunRef | undefined;
    let allocatedKey = "";
    try {
      const workspace = await this.actions.pickWorkspace();
      if (!workspace || this.sequence !== launchSequence) return;
      const confirmed = await this.actions.confirmStart();
      if (!confirmed || this.sequence !== launchSequence) return;
      await this.controller.start(workspace, text, (run) => {
        allocatedRun = run;
        allocatedKey = runKey(run);
        this.busy.add(allocatedKey);
        tab.starting = false;
        if (this.sequence === launchSequence) {
          // The fresh tab becomes this run's tab. If that run already has a
          // tab, fold into it instead of creating a duplicate key.
          const existing = this.tabs.find((row) => row.key === allocatedKey && row !== tab);
          const target = existing ?? tab;
          if (existing) {
            this.tabs.splice(this.tabs.indexOf(tab), 1);
            target.pendingBrief = text;
          } else {
            tab.key = allocatedKey;
            tab.run = run;
            tab.status = undefined;
            tab.pendingBrief = text;
          }
          this.activeKey = allocatedKey;
          this.actions.selected(run);
          void this.view?.webview.postMessage({ type: "sent", runKey: freshKey, text });
          this.post(true);
        }
      });
      if (allocatedRun) {
        try {
          const view = await this.controller.status(allocatedRun);
          if (this.sequence === launchSequence && this.active.key === allocatedKey) {
            this.active.status = view;
            this.active.pendingBrief = "";
          }
          if (!view.done && view.needs?.kind === "continue") {
            await this.controller.take(view, allocatedRun);
          }
        } catch {
          // Status read failure handled on next poll
        }
      }
    } catch (error) {
      const errMessage = messageOf(error);
      let established = false;
      if (allocatedRun) {
        try {
          await this.controller.status(allocatedRun);
          established = true;
        } catch {
          established = false;
        }
      }
      const tabNow = this.tabs.find((row) => row.key === (allocatedKey || freshKey)) ?? tab;
      if (!established) {
        if (allocatedKey) this.busy.delete(allocatedKey);
        if (this.sequence === launchSequence) {
          tabNow.run = undefined;
          tabNow.status = undefined;
          tabNow.key = freshKey;
          this.activeKey = freshKey;
          tabNow.pendingBrief = text;
          this.actions.selected(undefined);
          tabNow.error = errMessage;
          void this.view?.webview.postMessage({ type: "restore_draft", runKey: freshKey, text });
          this.post(true);
        }
      } else {
        if (this.sequence === launchSequence) {
          tabNow.error = errMessage;
        } else {
          void vscode.window.showErrorMessage(`AutoCode (${allocatedRun?.runDir}): ${errMessage}`);
        }
      }
    } finally {
      tab.starting = false;
      if (allocatedKey) this.busy.delete(allocatedKey);
      this.actions.changed();
      if (this.sequence === launchSequence) {
        await this.refresh();
        this.post();
      }
    }
  }
}

function runKey(run: RunRef): string { return `${run.workspace}\n${run.runDir}`; }

function messageOf(error: unknown): string { return error instanceof Error ? error.message : String(error); }

const ENGINE_LABELS: Record<Engine, string> = { opencode: "OpenCode", codex: "Codex", claude: "Claude Code" };

/**
 * The engine new chats run on. This client's own launch option, chosen the same
 * way the Connections backend is: existing chats keep their saved engine.
 */
async function pickEngine(): Promise<void> {
  const current = engineOf(readSettings());
  const choices: Array<{ label: string; description?: string; engine?: Engine; command?: string }> = [
    ...ENGINES.map((engine) => ({
      label: ENGINE_LABELS[engine],
      description: engine === current ? "current" : undefined,
      engine,
    })),
    { label: "Connections…", description: "choose the backend", command: "autocode.connections" },
    { label: "Configure in Settings…", command: "workbench.action.openSettings" },
  ];
  const picked = await vscode.window.showQuickPick(choices, {
    placeHolder: `Engine for new chats (current: ${ENGINE_LABELS[current as Engine] ?? current})`,
  });
  if (!picked) return;
  if (picked.engine) {
    await applyEngine(picked.engine);
    return;
  }
  if (picked.command === "autocode.connections") return void vscode.commands.executeCommand(picked.command);
  if (picked.command) return void vscode.commands.executeCommand(picked.command, "autocode");
}

function asRunRef(value: unknown): RunRef | undefined {
  if (!value || typeof value !== "object") return undefined;
  const candidate = value as Partial<RunRef>;
  if (typeof candidate.workspace !== "string" || !candidate.workspace.trim()) return undefined;
  if (typeof candidate.runDir !== "string" || !candidate.runDir.trim()) return undefined;
  return { workspace: candidate.workspace, runDir: candidate.runDir };
}
