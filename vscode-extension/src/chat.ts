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
import { isWorking, RunStarting, waitsForFolder, type StatusView } from "./core/view";
import { transitionNotice, waitsForPerson } from "./notify";
import { runTotal, sessionTotal, totalText } from "./core/meter";
import { applyEngine, engineOf, ENGINES, readSettings, type Engine } from "./settings";
import { invoke, tail, STAGES_TIMEOUT_MS } from "./core/cli";
import { chatRoute, chatSource, fillChatSet, fillSet, readStages, runListCommand, runRoute, type StageView } from "./core/stages";
import { StageListCache } from "./core/stage-cache";
import { inFolder, newChatCall, pickStageModel } from "./stages";
import { chosenPlacement, LEGACY_PLACEMENT, readWorktrees, type PlaceChoice, type WorktreeList } from "./core/worktrees";
import { confirmRaise, numericActionLabel, numericConfirmation, numericRecoveryEffect, panelHtml, planningSpendConfirmation, stateFor } from "./panel";

// While a reply is being written the view is read this often, so each new extract
// shows at once. A reply that has not changed for STREAM_QUIET_POLLS reads (a model
// running tools between messages) is read at the ordinary pace again. Same shape as
// the terminal chat's STREAM_POLL_SECONDS / STREAM_QUIET_POLLS.
const STREAM_POLL_SECONDS = 0.25;
const STREAM_QUIET_POLLS = 8;
// At most this many named continues after one action of the person's; each must move the run on.
const ADVANCE_LIMIT = 8;

/** What a continue that made progress changes, as TaskRun.advance_until_input compares it. */
function advanceIdentity(view: StatusView): string {
  return JSON.stringify([view.status, view.next_stage ?? null, view.iteration ?? null, view.phase ?? null, view.run_dir ?? null]);
}

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
  /** Where a new chat works: a place id from its worktree list, and a branch for `branch`. */
  place?: string;
  branch?: string;
  /** A new chat's own stage pick: the stage ID, a model and a level from its list. */
  stage?: string;
  model?: string;
  effort?: string;
  checkpoint?: string;
  action?: "compare" | "step" | "restore";
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
  /** What a run's start is doing before its first status view, in the core's words (#3079). */
  startingLabel: string;
  /** Compare results this tab already holds, by checkpoint id. */
  readonly comparisons: Map<string, CheckpointComparison>;
  /** A chat that has not started: the stages it will run, with its own picks (`stages list --json`). */
  stages?: StageView;
  stagesError: string;
  stagesBusy: boolean;
  /** The pick the core is checking now (`chat_set`/`chat_reset`), shown in its row until the list answers. */
  stagesPick?: { stage: string; model?: string; effort?: string };
  /** A started chat's Models chip while it reads the chat's list or sends a change: what it is doing. */
  modelsBusy?: string;
  /** A chat that has not started: where it may work (`worktrees list --json`), and the person's pick. */
  places?: WorktreeList;
  placesError: string;
  placeChoice?: PlaceChoice;
  /** Set while `stages` is the list every new chat here shares (no picks of its own): its cache key. */
  sharedStages?: string;
  /** While a confirmation dialog is open: its title, so the chat says what it waits for. */
  confirming: string;
}

function newTab(key: string, run?: RunRef): ChatTab {
  return { key, run, error: "", readError: "", pendingBrief: "", starting: false, launching: false, startingLabel: "", comparisons: new Map(),
    stagesError: "", stagesBusy: false, placesError: "", confirming: "" };
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
  /** Tabs whose message for the next boundary is being saved; it never waits on `busy` (#3259). */
  private readonly messaging = new Set<string>();
  /** Wait identities already announced, keyed by run key: a new tab for the same run does not re-announce. */
  private readonly notified = new Map<string, Set<string>>();
  private sequence = 0;
  private refreshing: Promise<void> | null = null;
  private timer: NodeJS.Timeout | undefined;
  private disposed = false;
  /** Last streaming extract per tab and how many reads it stayed unchanged (the TUI's pace). */
  private readonly streamText = new Map<string, string>();
  private readonly streamQuiet = new Map<string, number>();
  private readonly configuration: vscode.Disposable;
  /** The core's run modes for new chats; null when this AutoCode lists none (an older CLI). */
  private modes: RunModes | null | undefined;

  /** Kept for the editor's restart, so a new chat shows its models before the CLI answers. */
  private readonly stageLists: StageListCache;
  private readonly stageListsChanged: { dispose(): void };

  constructor(private readonly controller: RunController, private readonly storage: vscode.Memento, private readonly actions: Actions,
    stageLists?: StageListCache) {
    this.stageLists = stageLists ?? new StageListCache(storage);
    this.stageListsChanged = this.stageLists.onChange((key) => this.sharedStagesChanged(key));
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
    view.onDidChangeVisibility(() => {
      if (!view.visible) return;
      this.ensureChatStages(this.active);
      void this.refresh();
    });
    view.onDidDispose(() => { if (this.view === view) this.view = undefined; });
    view.webview.html = panelHtml(true);
    if (this.active.run) this.actions.selected(this.active.run);
    void this.refresh();
  }

  /** Open (or focus) a tab for `run`; without one, open another fresh chat tab. */
  show(run?: RunRef): void {
    if (run) {
      // A chat that moved to its own worktree (`--build-in-worktree`) is listed where it is now, while
      // its tab keeps the folder path it started with: the core answers both, so one tab holds it.
      const existing = this.tabs.find((tab) => tab.key === runKey(run))
        ?? this.tabs.find((tab) => tab.status?.workspace === run.workspace && tab.status?.run_dir === run.runDir);
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

  /** Drop a deleted run's tab; a fresh chat replaces the only tab. */
  forget(run: RunRef): void {
    const key = runKey(run);
    if (!this.tabs.some((tab) => tab.key === key)) return;
    if (this.tabs.length === 1) {
      this.tabs = [newTab("new")];
      this.activeKey = "new";
      this.hasExplicitSelection = false;
      this.post(true);
      void this.refresh();
      return;
    }
    this.close(key);
  }

  private activate(key: string): void {
    if (!this.tabs.some((tab) => tab.key === key)) return;
    this.sequence++;
    this.activeKey = key;
    this.hasExplicitSelection = true;
    this.actions.selected(this.active.run);
    void this.focus();
    this.post(true);
    this.ensureChatStages(this.active);
    if (!this.active.run && !this.active.places) void this.loadPlaces(this.active);
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
    // Only open tabs count. A tab that went away mid-reply (closed, deleted as the only tab, or replaced
    // when the webview restores its tabs) is never read again to quiet it, so it would hold every
    // chat at the streaming pace for the rest of the window's life.
    for (const key of [...this.streamText.keys()]) {
      if (this.tabs.some((tab) => tab.key === key)) continue;
      this.streamText.delete(key);
      this.streamQuiet.delete(key);
    }
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
    this.stageListsChanged.dispose();
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
      type: "chat", runKey: tab.key, busy, pausing: this.pausing.has(tab.key), messaging: this.messaging.has(tab.key),
      error: tab.error || tab.readError, pendingBrief: tab.pendingBrief,
      startingLabel: tab.status ? "" : tab.startingLabel,
      // A dialog or picker that holds a chat's start, named in the chat until it is answered.
      waitingOn: tab.run ? "" : tab.confirming,
      selectedRun: tab.run ?? null, persistSelection,
      tabs: this.tabs.map((row) => ({
        key: row.key, name: tabName(row), busy: row.starting || this.busy.has(row.key), run: row.run ?? null,
        // Never offered while this client is acting on the tab: its own action is not a wait.
        needs: !this.busy.has(row.key) && !row.starting && waitsForPerson(row.status),
        finished: row.status?.done === true,
      })),
      session: this.sessionCost(),
      activeKey: this.activeKey,
      // The chip names the engine the way its own picker does ("Codex", not "codex").
      engine: ENGINE_LABELS[engineOf(readSettings()) as Engine] ?? engineOf(readSettings()),
      // Only a chat that has not started chooses a mode; a run keeps the flow it started with.
      mode: !tab.run && this.modes ? chosenMode(this.modes, this.modeChoice)?.label ?? null : null,
      state: tab.run && tab.status
        ? { ...stateFor(tab.status, tab.run, this.controller, busy), unreadable: Boolean(tab.readError), confirming: tab.confirming || null } : null,
      chatStages: tab.run ? null : chatStagesFor(tab),
      chatPlace: tab.run ? null : chatPlaceFor(tab),
      models: modelsChip(tab),
    });
  }

  /**
   * The chat box's Models chip: a stage, a model and a level for this chat alone. A chat that has
   * not started fills its stage's `chat_set` (nothing saved); a started chat fills the `set` of the
   * list its status view names (`stage_models.next_command`), which the core takes only between
   * stages. Either way the core checks the pick, a checker's independence included.
   */
  private async pickModels(): Promise<void> {
    const tab = this.active;
    if (!tab.run) {
      if (tab.starting || tab.stagesBusy) return;
      if (!tab.stages) await this.loadChatStages(tab);
      const view = tab.stages;
      if (tab.run || !view?.chat) return;
      const pick = await pickStageModel(view, chatRoute, (stage) => stage.chat ?? stage.chosen ?? stage.default, "for this chat");
      if (pick && this.active === tab) await this.chatStage({ type: "chat_stage", stage: pick.stage.id, model: pick.model, effort: pick.effort });
      return;
    }
    const run = tab.run;
    const list = runListCommand(tab.status?.stage_models?.next_command);
    if (!list || !tab.status?.stage_models?.changeable || this.busy.has(tab.key) || tab.modelsBusy) return;
    const settings = { ...readSettings(), cwd: run.workspace };
    tab.error = "";
    // Reading the list takes seconds on some backends: the chip says so instead of seeming to do nothing.
    tab.modelsBusy = "Reading models…";
    this.post();
    try {
      const listed = await invoke(settings, list, {}, STAGES_TIMEOUT_MS);
      if (listed.code !== 0) throw new Error(tail(listed));
      const view = readStages(listed.stdout);
      if (!view.changeable) throw new Error(view.reason ?? "This chat's models cannot change now.");
      tab.modelsBusy = "Choosing…";
      this.post();
      const pick = await pickStageModel(view, runRoute, (stage) => stage.chosen ?? stage.default, "for this chat");
      if (!pick || tab.run !== run) return;
      tab.modelsBusy = "Changing…";
      this.post();
      const result = await invoke(settings, fillSet(pick.stage, pick.model, pick.effort), {}, STAGES_TIMEOUT_MS);
      if (result.code !== 0) throw new Error(tail(result));
      void vscode.window.showInformationMessage(`This chat runs the ${pick.stage.label} on ${pick.model} at ${pick.effort} from its next stage.`);
    } catch (error) {
      tab.error = messageOf(error);
    } finally {
      // A cancelled pick returns early: the chip still comes back.
      tab.modelsBusy = undefined;
      this.post();
    }
    await this.refresh();
  }

  /** Show a chat that has not started its stages: the shared list at once, read again only when stale. */
  private ensureChatStages(tab: ChatTab): void {
    if (!tab.run && (!tab.stages || tab.sharedStages)) void this.loadChatStages(tab);
  }

  /**
   * Read the stages a chat that has not started will run: its own list again when it has picks
   * (`chat.next_command`), else the new-chat list for the backend new chats here use.
   */
  private async loadChatStages(tab: ChatTab, command?: string[]): Promise<void> {
    if (tab.run || tab.stagesBusy) return;
    const shared = newChatCall();
    const argv = command ?? (tab.sharedStages ? undefined : tab.stages?.chat?.list) ?? shared.argv;
    if (sameWords(argv, shared.argv)) { await this.showSharedStages(tab, shared); return; }
    tab.stagesBusy = true;
    tab.stagesError = "";
    this.post();
    try {
      tab.stages = await readChatStages(argv);
      tab.sharedStages = undefined;
    } catch (error) {
      tab.stagesError = messageOf(error);
    } finally {
      tab.stagesBusy = false;
      this.post();
    }
  }

  /** Read where a chat that has not started may work; an AutoCode without the list leaves it unset. */
  private async loadPlaces(tab: ChatTab): Promise<void> {
    if (tab.run) return;
    try {
      tab.places = await readPlaces();
      tab.placesError = "";
    } catch (error) {
      tab.places = undefined;
      tab.placesError = messageOf(error);
    }
    this.post();
  }

  /** The person's pick of where this chat works, from the list it shows; checked again at the start. */
  private choosePlace(message: ChatMessage): void {
    const tab = this.active;
    if (tab.run || tab.starting || !tab.places) return;
    const place = tab.places.places.find((row) => row.id === message.place);
    if (!place) return;
    const branch = typeof message.branch === "string" && message.branch ? message.branch : undefined;
    tab.placeChoice = { place: place.id, ...(branch ? { branch } : {}) };
    this.post();
  }

  /**
   * The list every new chat here shares, without waiting: the latest one is shown at once and read
   * again in the background when stale; only a folder and backend never read waits for the CLI.
   * A failed background read keeps the list shown; the next one tries again.
   */
  private async showSharedStages(tab: ChatTab, shared: NewChatList): Promise<void> {
    const cached = this.stageLists.peek(shared.key);
    if (cached) {
      tab.stages = cached;
      tab.sharedStages = shared.key;
      tab.stagesError = "";
      this.post();
      if (this.stageLists.fresh(shared.key) || this.stageLists.loading(shared.key)) return;
    } else {
      tab.stagesBusy = true;
      tab.stagesError = "";
      this.post();
    }
    try {
      const view = await this.stageLists.load(shared.key, () => readListText(shared));
      if (!tab.run && (!tab.stages || tab.sharedStages === shared.key)) { tab.stages = view; tab.sharedStages = shared.key; }
    } catch (error) {
      if (!cached) tab.stagesError = messageOf(error);
    } finally {
      if (!cached) tab.stagesBusy = false;
      this.post();
    }
  }

  /** A shared list was read again (`key`), or every list went stale (""): fresh chats show the new one. */
  private sharedStagesChanged(key: string): void {
    if (!key) { if (this.active.sharedStages) void this.loadChatStages(this.active); return; }
    const view = this.stageLists.peek(key);
    if (!view) return;
    let changed = false;
    for (const tab of this.tabs) {
      if (tab.run || tab.starting || tab.sharedStages !== key) continue;
      tab.stages = view;
      changed = true;
    }
    if (changed) this.post();
  }

  /** A pick for this chat alone: only the stage's own `chat_set` (or `chat_reset`) from the list it shows. */
  private async chatStage(message: ChatMessage): Promise<void> {
    const tab = this.active;
    const stage = tab.stages?.stages.find((row) => row.id === message.stage);
    if (tab.run || tab.starting || tab.stagesBusy || !stage) return;
    let command: string[] | null;
    try {
      command = message.type === "chat_stage_reset" ? stage.chatReset
        : fillChatSet(stage, String(message.model ?? ""), String(message.effort ?? ""));
    } catch (error) {
      tab.stagesError = messageOf(error);
      this.post();
      return;
    }
    if (!command) return;
    // The list takes seconds on some backends (OpenCode lists its accounts' models): keep the pick
    // in its row, marked as being checked, rather than the old model under a greyed-out dropdown.
    tab.stagesPick = message.type === "chat_stage_reset" ? { stage: stage.id }
      : { stage: stage.id, model: String(message.model), effort: String(message.effort) };
    try {
      await this.loadChatStages(tab, command);
    } finally {
      tab.stagesPick = undefined;
      this.post();
    }
  }

  /** Every open run's accounted total added up, shown only when two or more chats hold runs. */
  private sessionCost(): string {
    const runs = this.tabs.filter((tab) => tab.run);
    if (runs.length < 2) return "";
    return totalText(sessionTotal(runs.map((tab) => (tab.status ? runTotal(tab.status) : null))), false);
  }

  /** One poll cycle. A caller that awaits this joins the cycle already running, never a no-op. */
  refresh(): Promise<void> {
    if (this.refreshing) return this.refreshing;
    const running = this.runRefresh();
    this.refreshing = running;
    return running;
  }

  private async runRefresh(): Promise<void> {
    const sequence = this.sequence;
    try {
      // Tabs read in parallel and posted as each lands: one slow or hung --status
      // must not freeze the others' display until its timeout (#1711 timing gaps).
      await Promise.all([...this.tabs].filter((tab) => tab.run).map(async (tab) => {
        try {
          const status = await this.controller.pollStatus(tab.run!);
          tab.startingLabel = "";
          const before = tab.status;
          if (this.tabs.includes(tab)) {
            this.announce(tab, status);
            void this.continueAfterWait(tab, before, status);
          }
        } catch (error) {
          if (error instanceof RunStarting) {
            // Still starting: say what it is doing, not that the status could not be read.
            tab.startingLabel = error.label;
            tab.readError = "";
          } else if (tab.launching && !tab.status) {
            // This window's own start command is still running and has not saved the run yet. A read
            // before the core reserves the directory, or a core older than its launch note (#3079),
            // answers "--run-dir ... has no state.json": the run is starting, not unreadable. A start
            // that fails says so itself when its command returns (start()).
            tab.startingLabel ||= "starting the run";
            tab.readError = "";
          } else if (sequence === this.sequence && !tab.starting) tab.readError = messageOf(error);
        }
        if (sequence === this.sequence) this.post();
      }));
    } finally {
      this.refreshing = null;
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
    if (message.type === "stage_models") { await this.stageModels(); return; }
    if (message.type === "models") { await this.pickModels(); return; }
    if (message.type === "chat_stage" || message.type === "chat_stage_reset") { await this.chatStage(message); return; }
    if (message.type === "chat_stages") { await this.loadChatStages(this.active); return; }
    if (message.type === "chat_place") { this.choosePlace(message); return; }
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
      // The stages first: they show from the cache at once, and a read does not wait on the modes.
      this.ensureChatStages(this.active);
      await this.loadModes();
      if (!this.active.run && !this.active.places) void this.loadPlaces(this.active);
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
      const before = engineOf(readSettings());
      await pickEngine();
      if (engineOf(readSettings()) !== before) {
        // Another backend offers other models: a chat that has not started takes its list afresh.
        for (const tab of this.tabs) if (!tab.run) { tab.stages = undefined; tab.sharedStages = undefined; tab.stagesError = ""; }
        void this.loadChatStages(this.active);
      }
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
    if (message.type === "send" && typeof message.text === "string" && message.text.trim()
      && message.runKey === tab.key && tab.run && !tab.starting && tab.status?.chat?.input?.kind === "message") {
      // The run works and the view takes a message for its next boundary (#3259): saved at once,
      // never held behind the stage this window is driving.
      await this.queueMessage(tab, message.text, message.context ?? "");
      return;
    }
    if (message.type === "send" && typeof message.text === "string" && message.text.trim()
      && (message.runKey !== tab.key || tab.starting || this.busy.has(tab.key))) {
      // A dropped send must never look like it was taken: the person's text stays in the
      // composer, and the chat says why nothing happened.
      const why = tab.starting || this.busy.has(tab.key)
        ? "AutoCode is still working on your last message; send this again in a moment."
        : "This chat changed before your message was sent; send it again.";
      tab.error = why;
      this.post();
      return;
    }
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
        try {
          if (await confirmRaise(stateFor(view, run, this.controller), message.decisionContext, value,
            (title) => { tab.confirming = title; this.post(); })) {
            await this.controller.raise(view, run, value);
          }
        } finally {
          tab.confirming = "";
          this.post();
        }
      } else {
        const state = stateFor(view, run, this.controller);
        if (message.decisionContext !== state.decisionContext) throw new Error("The requested action changed; review it before continuing");
        const numeric = numericConfirmation(state, message.answers ?? {});
        const planning = planningSpendConfirmation(view, message.answers ?? {});
        if (numeric || planning || (requiresConfirmation(view.needs ?? null) && vscode.workspace.getConfiguration("autocode").get("confirmPersonOnly", true))) {
          const button = numeric ? numericActionLabel(state) : planning?.label ?? "Continue";
          // The dialog is the only place the question shows: say in the chat that one is open,
          // or a dialog that misses the person's attention reads as a dead button.
          tab.confirming = numeric ?? planning?.title ?? (state.actionLabel ? `${state.actionLabel}?` : "Continue AutoCode?");
          this.post();
          let answer: string | undefined;
          try {
            answer = await vscode.window.showWarningMessage(
              tab.confirming, { modal: true,
                detail: numeric ? numericRecoveryEffect(state) : planning?.detail ?? state.actionEffect ?? view.chat?.notice ?? "Open Run details to review what needs attention before continuing." }, button,
            );
          } finally {
            tab.confirming = "";
            this.post();
          }
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
      tab.confirming = "";
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

  /**
   * Go on with the run after the person's own action, as the headless client does (TaskRun.advance_until_input):
   * take the named continue while the run needs nothing else and each one moves it on. One continue can end at
   * a ready boundary that only needs the next, such as moving to the chat's own worktree before planning;
   * stopping after the first left the person a bare Continue to press. A run waiting for another chat to leave
   * its folder (`checkout_wait`) is left to the poll, which continues it once the folder is free.
   */
  private async advanceAfterInput(run: RunRef): Promise<void> {
    let view = await this.controller.status(run);
    for (let taken = 0; taken < ADVANCE_LIMIT; taken += 1) {
      if (view.done || view.needs?.kind !== "continue" || isWorking(view)) return;
      // After the first, only a running run's ready boundary is gone on with; a stop is the person's to see.
      if (taken > 0 && view.status !== "RUNNING") return;
      const before = advanceIdentity(view);
      await this.controller.take(view, run);
      view = await this.controller.status(run);
      if (advanceIdentity(view) === before) return;
    }
  }

  /**
   * The run waited for another chat to leave its folder and now no longer does: take its named continue,
   * the one the person's own action had started. Without this the chat dropped its progress card for a
   * bare Continue that the person had to press until the folder happened to be free (2026-11-11).
   */
  private async continueAfterWait(tab: ChatTab, before: StatusView | undefined, now: StatusView): Promise<void> {
    if (!before || !waitsForFolder(before) || waitsForFolder(now) || !tab.run) return;
    if (now.done || now.needs?.kind !== "continue" || isWorking(now) || this.busy.has(tab.key) || tab.starting) return;
    const run = tab.run;
    const key = tab.key;
    this.busy.add(key);
    tab.error = "";
    this.post();
    try {
      await this.advanceAfterInput(run);
    } catch (error) {
      if (this.activeKey === key) tab.error = messageOf(error);
      else void vscode.window.showErrorMessage(`AutoCode (${run.runDir}): ${messageOf(error)}`);
    } finally {
      this.busy.delete(key);
      this.actions.changed();
      void this.refresh();
    }
  }

  /**
   * Save a message typed while the run works, through the view's named `message` action (#3259). The
   * stage this window may be driving goes on; the run reads the message at its next boundary. A run no
   * one is driving, ready at a boundary, is continued so that boundary reads it.
   */
  private async queueMessage(tab: ChatTab, text: string, context: string): Promise<void> {
    const run = tab.run;
    const key = tab.key;
    if (!run || this.messaging.has(key)) return;
    this.messaging.add(key);
    tab.error = "";
    this.post();
    try {
      // The view the person typed against, not a fresh read: a status read can take seconds on a busy
      // host, and the core checks the message's admission itself when it saves it.
      const view = tab.status;
      if (!view) throw new Error("The run's status is not read yet; send this again in a moment.");
      await this.controller.queueMessage(view, run, text, context, `message-${randomBytes(8).toString("hex")}`);
      void this.view?.webview.postMessage({ type: "sent", runKey: key, text });
    } catch (error) {
      if (this.activeKey === key) tab.error = messageOf(error);
      else void vscode.window.showErrorMessage(`AutoCode (${run.runDir}): ${messageOf(error)}`);
      this.messaging.delete(key);
      this.post();
      return;
    }
    this.messaging.delete(key);
    this.post();
    if (this.busy.has(key) || tab.starting) { await this.refresh(); return; }
    this.busy.add(key);
    this.post();
    try {
      await this.advanceAfterInput(run);
    } catch (error) {
      if (this.activeKey === key) tab.error = messageOf(error);
      else void vscode.window.showErrorMessage(`AutoCode (${run.runDir}): ${messageOf(error)}`);
    } finally {
      this.busy.delete(key);
      this.actions.changed();
      await this.refresh();
    }
  }

  /** Ask the working run to pause after its current step: the view's named pause, never a kill (#1598). */
  /** Open this chat's own stage models: the list its latest status view names, run in its folder. */
  private async stageModels(): Promise<void> {
    const tab = this.active;
    const list = tab.status?.stage_models?.next_command;
    if (!tab.run || !list) return;
    await vscode.commands.executeCommand("autocode.stages", { list, cwd: tab.run.workspace });
  }

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
      if (message.action === "compare" || message.action === "step") {
        const comparison = message.action === "compare"
          ? await this.controller.compareCheckpoint(run, checkpointId)
          : await this.controller.stepCheckpoint(run, checkpointId);
        // A step view carries no restore token; never keep it where a restore would reuse it.
        if (message.action === "compare") tab.comparisons.set(checkpointId, comparison);
        void this.view?.webview.postMessage({ type: "checkpoint_compare", runKey: key, checkpoint: checkpointId,
          kind: message.action, comparison });
        return;
      }
      if (message.action !== "restore") return;
      let comparison = tab.comparisons.get(checkpointId);
      if (!comparison) {
        comparison = await this.controller.compareCheckpoint(run, checkpointId);
        tab.comparisons.set(checkpointId, comparison);
        void this.view?.webview.postMessage({ type: "checkpoint_compare", runKey: key, checkpoint: checkpointId,
          kind: "compare", comparison });
      }
      if (comparison.blocked_reason) throw new Error(comparison.blocked_reason);
      const label = (tab.status?.code_checkpoints?.rows ?? []).find((row) => row.id === checkpointId)?.label ?? checkpointId;
      const files = comparison.changed_files?.length ?? 0;
      const question = `Restore "${label}" on a new branch?`;
      // The dialog is the only place the question shows; name it in the chat while it
      // pends, like the take path, so a parked dialog cannot read as a dead button.
      tab.confirming = question;
      this.post();
      let proceed: string | undefined;
      try {
        proceed = await vscode.window.showWarningMessage(
          question,
          { modal: true,
            detail: `${files} file(s) differ from this checkpoint. The current branch and later work stay intact; every milestone needs fresh verification, and the new candidate stays paused until you resume it.` },
          "Restore",
        );
      } finally {
        tab.confirming = "";
        this.post();
      }
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
      // The action is over: say so at once. The result message re-rendered with the state
      // that still said it was taking, and until this post the step's buttons stayed
      // disabled and swallowed clicks, for as long as the next status read took.
      this.post();
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

  /**
   * The placement flags this chat starts with: the chosen place's own `start_args`, read again in the folder
   * it starts in, after the person confirms a place that warns. An AutoCode without the list works in the
   * folder, as every chat did before it. Undefined when the person declined.
   */
  private async placement(tab: ChatTab, read: { list: WorktreeList } | { error: unknown }): Promise<string[] | undefined> {
    if ("error" in read) {
      if (tab.places) throw new Error(`Where this chat works: ${messageOf(read.error)}`);
      return [...LEGACY_PLACEMENT];
    }
    const list = read.list;
    tab.places = list;
    const { place, args } = chosenPlacement(list, tab.placeChoice);
    if (place.warning) {
      const warning = place.warning;
      const proceed = await this.dialog(tab, "Start this chat in your folder? Answer the dialog that opened.", () => vscode.window.showWarningMessage(
        "Start this chat in your folder?",
        { modal: true, detail: warning },
        "Start in this folder",
      ));
      if (proceed !== "Start in this folder") return undefined;
    }
    return args;
  }

  /**
   * Wait on a dialog or picker that holds a chat's start, saying in the chat which one is open and
   * what to do (`title`): one that opens out of sight must not leave the chat looking stuck.
   */
  private async dialog<T>(tab: ChatTab, title: string, open: () => Thenable<T> | Promise<T>): Promise<T> {
    if (!title) return open();
    tab.confirming = title;
    this.post();
    try {
      return await open();
    } finally {
      tab.confirming = "";
      this.post();
    }
  }

  private async start(tab: ChatTab, text: string): Promise<void> {
    if (tab.starting) return;
    tab.starting = true;
    tab.error = "";
    // Until the run exists the chat says what it is doing, and names any dialog that holds the
    // start, so a first message never reads as a setup step the person has to find.
    tab.startingLabel = "starting your chat";
    this.post();
    const launchSequence = this.sequence;
    const freshKey = tab.key;
    let allocatedRun: RunRef | undefined;
    let allocatedKey = "";
    try {
      const workspace = await this.dialog(tab, (vscode.workspace.workspaceFolders?.length ?? 0) > 1
        ? "Choose the folder this chat runs in from the list that opened." : "", () => this.actions.pickWorkspace());
      if (!workspace || this.sequence !== launchSequence) return;
      // The modes (so the flags come from the CLI that starts the run), this chat's own model picks
      // and where it works are read again here, at once: each is a CLI call, and in turn they made a
      // first message wait several seconds before anything started.
      const picks = tab.stages?.chat?.startArgs.length ? tab.stages.chat.list : undefined;
      const [, fresh, places] = await Promise.all([
        this.loadModes(),
        picks ? readChatStages(picks, workspace).catch((error: unknown) => {
          throw new Error(`This chat's models: ${messageOf(error)}`);
        }) : undefined,
        readPlaces(workspace).then((list) => ({ list }), (error: unknown) => ({ error })),
      ]);
      if (this.sequence !== launchSequence) return;
      // This chat's own picks, checked again where it starts: a saved choice made since can clash.
      let stageArgs: string[] = [];
      if (fresh) {
        tab.stages = fresh;
        stageArgs = fresh.chat?.startArgs ?? [];
      }
      const mode = this.modes ? chosenMode(this.modes, this.modeChoice) : undefined;
      if (mode?.spendConfirmation !== "planning") {
        const confirmed = await this.dialog(tab, "Start a live AutoCode run? Answer the dialog that opened.", () => this.actions.confirmStart());
        if (!confirmed || this.sequence !== launchSequence) return;
      }
      // Where it works, read in the folder it starts in: a branch another chat took meanwhile is refused here.
      const placement = await this.placement(tab, places);
      if (!placement || this.sequence !== launchSequence) return;
      tab.launching = true;
      tab.startingLabel = "";
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
      }, mode?.startArgs, stageArgs, placement);
      if (allocatedRun) {
        try {
          const view = await this.controller.status(allocatedRun);
          if (this.sequence === launchSequence && this.active.key === allocatedKey) {
            this.active.status = view;
            this.active.pendingBrief = "";
          }
          await this.advanceAfterInput(allocatedRun);
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
      tab.confirming = "";
      if (!tab.status) tab.startingLabel = "";
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

/** Where a new chat in `cwd` (or the window's folder) may work, as the core lists it. */
async function readPlaces(cwd?: string): Promise<WorktreeList> {
  const settings = readSettings();
  const folder = cwd ?? vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  const argv = ["worktrees", "list", "--json", ...(folder ? ["--workspace", folder] : [])];
  // Bounded like the stages list: a start awaits this read, and a wedged list would leave
  // the chat saying "starting" forever with nothing for the person to answer.
  const result = await invoke(folder ? { ...settings, cwd: folder } : settings, argv, {}, STAGES_TIMEOUT_MS);
  if (result.code !== 0) throw new Error(tail(result));
  return readWorktrees(result.stdout);
}

/** What the webview shows for a chat that has not started: the places, the pick, and its warning. */
function chatPlaceFor(tab: ChatTab) {
  const list = tab.places;
  if (!list) return { places: [], error: tab.placesError ? "This AutoCode cannot say where a chat works; it works in your folder. Update AutoCode." : "", chosen: null, branch: null, warning: null, folder: "" };
  const chosen = tab.placeChoice?.place ?? list.defaultPlace;
  const place = list.places.find((row) => row.id === chosen);
  // A branch the list no longer offers (merged and gone) is not a pick the picker may claim: the
  // summary would name it while the select has nothing to show, and no start can take it.
  const wanted = tab.placeChoice?.branch ?? null;
  const branch = wanted && place?.branches.some((row) => row.name === wanted) ? wanted : null;
  return {
    folder: list.folder,
    error: "",
    chosen,
    branch,
    warning: place?.warning ?? null,
    places: list.places.map((row) => ({
      id: row.id, label: row.label, does: row.does, warning: row.warning,
      branches: row.branches.map((branch) => ({ name: branch.name, unavailable: branch.unavailable })),
    })),
  };
}

/** A new chat's stage list: `command` (one the list named) or the plain list, in `cwd` or the window's folder. */
async function readChatStages(command: string[] | undefined, cwd?: string): Promise<StageView> {
  const settings = readSettings();
  const argv = command ?? newChatCall().argv;
  const result = await invoke(cwd ? { ...settings, cwd } : inFolder(settings), argv, {}, STAGES_TIMEOUT_MS);
  if (result.code !== 0) throw new Error(tail(result));
  return readStages(result.stdout);
}

/** The list every new chat in this window starts from: the call, where it runs and its cache key. */
type NewChatList = ReturnType<typeof newChatCall>;

async function readListText(list: NewChatList): Promise<string> {
  const result = await invoke(list.settings, list.argv, {}, STAGES_TIMEOUT_MS);
  if (result.code !== 0) throw new Error(tail(result));
  return result.stdout;
}

function sameWords(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((word, index) => word === b[index]);
}

/** What the webview shows for a chat that has not started: each stage, what it will run, and its choices. */
function chatStagesFor(tab: ChatTab) {
  const view = tab.stages;
  return {
    busy: tab.stagesBusy,
    error: tab.stagesError,
    label: view?.label ?? "",
    optionsStop: view?.optionsStop ?? null,
    // An AutoCode without per-chat picks still lists the stages; it only offers no change.
    changeable: Boolean(view?.chat),
    stages: (view?.stages ?? []).map((stage) => {
      const pick = tab.stagesPick?.stage === stage.id ? tab.stagesPick : undefined;
      // A reset being checked shows what the stage falls back to; a pick shows the pick.
      const route = pick?.model ? { model: pick.model, effort: pick.effort ?? null }
        : (pick ? null : stage.chat) ?? stage.chosen ?? (stage.default.offered ? stage.default : null);
      return {
        id: stage.id, label: stage.label, does: stage.does, now: chatRoute(stage), source: chatSource(stage),
        picked: Boolean(stage.chat), checking: Boolean(pick),
        model: route?.model ?? "", effort: route?.effort ?? "",
        options: stage.options.map((option) => ({ model: option.model, family: option.family, unavailable: option.unavailable, effortChoices: option.effortChoices })),
        resettable: Boolean(stage.chatReset),
      };
    }),
  };
}

/**
 * The chat box's Models chip, or null where the core offers no change: a chat that has not started
 * with a list naming per-chat picks, or a started chat whose status view names its own list.
 */
function modelsChip(tab: ChatTab): { label: string; disabled: boolean; title: string } | null {
  if (!tab.run) {
    if (!tab.stages?.chat || !tab.stages.stages.length) return null;
    const picked = tab.stages.stages.filter((stage) => stage.chat).length;
    if (tab.stagesPick) return { label: "Models · checking…", disabled: true, title: "Checking this chat's pick" };
    // A greyed-out chip says why on its face, not only on hover: it was a dead "Models" button.
    if (tab.starting) return { label: "Models · starting…", disabled: true, title: "This chat is starting with the models it shows" };
    if (tab.stagesBusy) return { label: "Models · loading…", disabled: true, title: "Reading the models this backend offers" };
    return { label: picked ? `Models · ${picked} changed` : "Models", disabled: false,
      title: "Change a stage's model and reasoning level for this chat only" };
  }
  const offer = tab.status?.stage_models;
  if (!offer || !runListCommand(offer.next_command)) return null;
  if (tab.modelsBusy) return { label: `Models · ${tab.modelsBusy}`, disabled: true, title: tab.modelsBusy };
  // The core offers a change only between stages (`stage_models`); its reason is the tooltip.
  if (!offer.changeable) return { label: "Models · after this stage", disabled: true, title: offer.reason ?? "This chat's models cannot change now." };
  return { label: "Models", disabled: false, title: "Change a stage's model and reasoning level for this chat from its next stage" };
}

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
    { label: "Stage models…", description: "each stage's model and reasoning level", command: "autocode.stages" },
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
  if (picked.command === "autocode.connections" || picked.command === "autocode.stages") return void vscode.commands.executeCommand(picked.command);
  if (picked.command) return void vscode.commands.executeCommand(picked.command, "autocode");
}

function asRunRef(value: unknown): RunRef | undefined {
  if (!value || typeof value !== "object") return undefined;
  const candidate = value as Partial<RunRef>;
  if (typeof candidate.workspace !== "string" || !candidate.workspace.trim()) return undefined;
  if (typeof candidate.runDir !== "string" || !candidate.runDir.trim()) return undefined;
  return { workspace: candidate.workspace, runDir: candidate.runDir };
}
