/** Sidebar conversation over the public status view and its named actions.
 *
 * One window holds several chats at once (#1558): each tab is one run or one
 * fresh not-yet-started chat. Every open run tab polls (#1566); every tab keeps
 * its own draft (keyed by tab key in the webview), status and errors.
 */
import * as vscode from "vscode";
import { randomBytes } from "node:crypto";
import { RunController, type CheckpointComparison, type RunRef } from "./controller";
import { requiresConfirmation } from "./core/next-command";
import { chosenMode, type RunModes } from "./core/modes";
import type { StatusView } from "./core/view";
import { transitionNotice } from "./notify";
import { applyEngine, engineOf, ENGINES, readSettings, type Engine } from "./settings";
import { confirmRaise, numericActionLabel, numericConfirmation, numericRecoveryEffect, panelHtml, planningSpendConfirmation, stateFor } from "./panel";

// While a reply is being written the view is read this often, so each new extract
// shows at once. A reply that has not changed for STREAM_QUIET_POLLS reads (a model
// running tools between messages) is read at the ordinary pace again. Same shape as
// the terminal chat's STREAM_POLL_SECONDS / STREAM_QUIET_POLLS.
const STREAM_POLL_SECONDS = 0.25;
const STREAM_QUIET_POLLS = 8;

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
  /** A number typed into the bound's separately named raise (#2044). */
  value?: string;
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
  launching: boolean;
  /** Compare results this tab already holds, by checkpoint id. */
  readonly comparisons: Map<string, CheckpointComparison>;
}

function newTab(key: string, run?: RunRef): ChatTab {
  return { key, run, error: "", readError: "", pendingBrief: "", starting: false, launching: false, comparisons: new Map() };
}

function tabName(tab: ChatTab): string {
  if (!tab.run) return "New chat";
  // The request the run is working on reads better than its directory name.
  const request = (tab.status?.task ?? tab.pendingBrief ?? "").split("\n", 1)[0]!.trim();
  const name = request || (tab.run.runDir.split("/").filter(Boolean).pop() ?? tab.run.runDir);
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
  /** Tabs whose pause request is being submitted; a working run keeps `busy` the whole time (#1598). */
  private readonly pausing = new Set<string>();
  /** Wait identities already announced, keyed by run key: a new tab for the same run does not re-announce. */
  private readonly notified = new Map<string, Set<string>>();
  private sequence = 0;
  private refreshing = false;
  private timer: NodeJS.Timeout | undefined;
  private disposed = false;
  /** Last streaming extract per tab and how many reads it stayed unchanged (the TUI's pace). */
  private readonly streamText = new Map<string, string>();
  private readonly streamQuiet = new Map<string, number>();
  private readonly configuration: vscode.Disposable;
  /** The core's run modes for new chats; null when this AutoCode lists none (an older CLI). */
  private modes: RunModes | null | undefined;

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
    this.schedulePoll();
  }

  /** Recursive timeout so a growing reply can poll at the streaming pace (autocode_chat_client.pace). */
  private schedulePoll(): void {
    if (this.disposed) return;
    if (this.timer) clearTimeout(this.timer);
    const seconds = Math.max(1, vscode.workspace.getConfiguration("autocode").get<number>("pollSeconds", 5));
    this.timer = setTimeout(() => {
      this.timer = undefined;
      void this.refresh().finally(() => this.schedulePoll());
    }, this.pace(seconds) * 1000);
  }

  /** The wait before the next read: short while a reply is still changing, else ``seconds``. */
  private pace(seconds: number): number {
    for (const [key, text] of this.streamText) {
      if (text && (this.streamQuiet.get(key) ?? 0) < STREAM_QUIET_POLLS) return Math.min(STREAM_POLL_SECONDS, seconds);
    }
    return seconds;
  }

  /** Track the tab's streaming extract so pace() knows whether it is still growing. */
  private noteStreaming(tab: ChatTab): void {
    const seconds = Math.max(1, vscode.workspace.getConfiguration("autocode").get<number>("pollSeconds", 5));
    const before = this.pace(seconds);
    const live = (tab.status?.chat?.messages ?? []).find((row) => row.streaming)?.text ?? "";
    if (!live) {
      this.streamText.delete(tab.key);
      this.streamQuiet.delete(tab.key);
    } else {
      const quiet = live === this.streamText.get(tab.key) ? (this.streamQuiet.get(tab.key) ?? 0) + 1 : 0;
      this.streamText.set(tab.key, live);
      this.streamQuiet.set(tab.key, quiet);
    }
    // A newly growing (or just finished) extract changes the wait: re-arm the timer.
    if (this.pace(seconds) !== before) this.startPolling();
  }

  dispose(): void {
    this.disposed = true;
    if (this.timer) clearTimeout(this.timer);
    this.timer = undefined;
    this.configuration.dispose();
  }

  /** The mode id the person last chose for new chats; the core's default until then. */
  private get modeChoice(): string | undefined { return this.storage.get<string>(MODE_KEY); }

  /** Read the core's run modes. An AutoCode without `modes` offers no choice: new chats keep the full flow. */
  private async loadModes(): Promise<void> {
    try {
      this.modes = await this.controller.modes();
    } catch {
      this.modes = null;
    }
  }

  /** The run mode for new chats, chosen from the core's list; a started chat keeps its own flow. */
  private async pickMode(): Promise<void> {
    await this.loadModes();
    if (!this.modes?.modes.length) {
      void vscode.window.showInformationMessage("This AutoCode lists no run modes, so new chats use the full flow.");
      return;
    }
    const current = chosenMode(this.modes, this.modeChoice);
    const picked = await vscode.window.showQuickPick(
      this.modes.modes.map((mode) => ({
        label: mode.label,
        description: mode.id === current?.id ? "current" : undefined,
        detail: mode.description,
        id: mode.id,
      })),
      { placeHolder: `Run mode for new chats (current: ${current?.label ?? "Full"})` },
    );
    if (picked) await this.storage.update(MODE_KEY, picked.id);
  }

  private get active(): ChatTab { return this.tabs.find((tab) => tab.key === this.activeKey) ?? this.tabs[0]!; }

  /** The run the active tab holds; the window's status bar follows it. */
  get run(): RunRef | undefined { return this.active.run; }

  private post(persistSelection = false): void {
    if (!this.view?.visible) return;
    const tab = this.active;
    const busy = tab.starting || this.busy.has(tab.key);
    void this.view?.webview.postMessage({
      type: "chat", runKey: tab.key, busy, waitingForStart: tab.starting && !tab.launching, pausing: this.pausing.has(tab.key),
      error: tab.error || tab.readError, pendingBrief: tab.pendingBrief,
      selectedRun: tab.run ?? null, persistSelection,
      tabs: this.tabs.map((row) => ({ key: row.key, name: tabName(row), busy: row.starting || this.busy.has(row.key), run: row.run ?? null })),
      activeKey: this.activeKey,
      engine: engineOf(readSettings()),
      // Only a chat that has not started chooses a mode; a run keeps the flow it started with.
      mode: !tab.run && this.modes ? chosenMode(this.modes, this.modeChoice)?.label ?? null : null,
      state: tab.run && tab.status
        ? { ...stateFor(tab.status, tab.run, this.controller, busy), unreadable: Boolean(tab.readError) } : null,
    });
  }

  async refresh(): Promise<void> {
    if (this.refreshing) return;
    const sequence = this.sequence;
    this.refreshing = true;
    try {
      // Tabs read in parallel: one slow or hung --status must not freeze the others
      // until its timeout (#1711 timing gaps).
      await Promise.all([...this.tabs].filter((tab) => tab.run).map(async (tab) => {
        try {
          const status = await this.controller.pollStatus(tab.run!);
          if (this.tabs.includes(tab)) this.announce(tab, status);
        } catch (error) {
          if (sequence === this.sequence && !tab.starting) tab.readError = messageOf(error);
        }
      }));
    } finally {
      this.refreshing = false;
      if (sequence === this.sequence) this.post();
      else void this.refresh();
    }
  }

  /** Land the tab's fresh status and announce a new person-wait or a finished run once per run. */
  private announce(tab: ChatTab, status: StatusView): void {
    // The per-run identities below dedupe notices. Reconsider a Continue after
    // our action settles: its intermediate checkpoint is not a person-wait.
    const notice = transitionNotice(null, status);
    tab.status = status;
    tab.readError = "";
    this.noteStreaming(tab);
    if (!notice || !tab.run || (status.needs?.kind === "continue" && this.busy.has(tab.key))) return;
    const run = tab.run;
    const key = runKey(run);
    let seen = this.notified.get(key);
    if (!seen) { seen = new Set(); this.notified.set(key, seen); }
    if (seen.has(notice.identity)) return;
    seen.add(notice.identity);
    if (vscode.workspace.getConfiguration("autocode").get<boolean>("notify", true) === false) return;
    void (async () => {
      const outcome = notice.identity === "done" ? "finished." : `is waiting for you - ${notice.tail}`;
      const choice = await vscode.window.showInformationMessage(
        `AutoCode: ${tabName(tab)} ${outcome}`, "Open chat",
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
      await this.loadModes();
      await this.refresh();
      return;
    }
    if (message.type === "refresh") { await this.refresh(); return; }
    if (message.type === "select_tab" && message.tabKey) { this.activate(message.tabKey); return; }
    if (message.type === "close_tab" && message.tabKey) { this.close(message.tabKey); return; }
    if (message.type === "checkpoint") { await this.checkpoint(message); return; }
    if (message.type === "working_diff") { await this.workingDiff(message); return; }
    if (message.type === "pause") { await this.pause(message); return; }
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
    if (message.type === "mode") {
      await this.pickMode();
      this.post();
      return;
    }
    if (message.type === "new") { if (!this.active.starting) this.show(); return; }
    if (message.type !== "send" && message.type !== "take" && message.type !== "raise") return;
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
      } else if (message.type === "raise") {
        // The raise resumes the run itself; nothing else is inferred from it.
        const value = message.value ?? "";
        if (await confirmRaise(stateFor(view, run, this.controller), message.decisionContext, value)) {
          await this.controller.raise(view, run, value);
        }
      } else {
        const state = stateFor(view, run, this.controller);
        if (message.decisionContext !== state.decisionContext) throw new Error("The requested action changed; review it before continuing");
        const numeric = numericConfirmation(state, message.answers ?? {});
        const planning = planningSpendConfirmation(view, message.answers ?? {});
        if (numeric || planning || (requiresConfirmation(view.needs ?? null) && vscode.workspace.getConfiguration("autocode").get("confirmPersonOnly", true))) {
          const button = numeric ? numericActionLabel(state) : planning?.label ?? "Continue";
          const answer = await vscode.window.showWarningMessage(
            numeric ?? planning?.title ?? (state.actionLabel ? `${state.actionLabel}?` : "Continue AutoCode?"), { modal: true,
              detail: numeric ? numericRecoveryEffect(state) : planning?.detail ?? state.actionEffect ?? view.chat?.notice ?? "Open Run details to review what needs attention before continuing." }, button,
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

  /** Ask the working run to pause after its current step: the view's named pause, never a kill (#1598). */
  private async pause(message: ChatMessage): Promise<void> {
    const tab = this.active;
    const run = tab.run;
    if (!run || message.runKey !== tab.key || this.pausing.has(tab.key)) return;
    const key = tab.key;
    this.pausing.add(key);
    tab.error = "";
    this.post();
    try {
      await this.controller.pause(run, `pause-${randomBytes(8).toString("hex")}`);
    } catch (error) {
      if (this.activeKey === key) tab.error = messageOf(error);
      else void vscode.window.showErrorMessage(`AutoCode (${run.runDir}): ${messageOf(error)}`);
    } finally {
      this.pausing.delete(key);
      await this.refresh();
    }
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
    this.post();
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

  /** The running Builder's changes so far: a read, offered only while the view names it. */
  private async workingDiff(message: ChatMessage): Promise<void> {
    const tab = this.active;
    const run = tab.run;
    if (!run || message.runKey !== tab.key) return;
    const key = tab.key;
    try {
      const comparison = await this.controller.workingDiff(run);
      void this.view?.webview.postMessage({ type: "working_diff", runKey: key, comparison });
    } catch (error) {
      void this.view?.webview.postMessage({ type: "working_diff", runKey: key, error: messageOf(error) });
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
      // Read the list again so the flags come from the CLI that starts the run.
      await this.loadModes();
      if (this.sequence !== launchSequence) return;
      const mode = this.modes ? chosenMode(this.modes, this.modeChoice) : undefined;
      if (mode?.spendConfirmation !== "planning") {
        const confirmed = await this.actions.confirmStart();
        if (!confirmed || this.sequence !== launchSequence) return;
      }
      tab.launching = true;
      this.post();
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
      }, mode?.startArgs);
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
      tab.launching = false;
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

const MODE_KEY = "autocode.chat.mode";

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
