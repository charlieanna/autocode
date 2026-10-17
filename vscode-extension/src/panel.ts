/**
 * The run detail panel: what the status view says, and the action it names.
 *
 * The webview renders from a plain state object with `textContent`, never by
 * interpolating model-authored text into HTML — a plan or a finding is
 * untrusted text.
 */

import * as vscode from "vscode";
import { ClientGap, leavesForPerson } from "./core/next-command";
import type { AcceptanceCriterion, Question, StatusView } from "./core/view";
import { approvalToken, needsSummary, pauseLabel, planForApproval, progressLine, usageLine } from "./core/view";
import type { RunController, RunRef } from "./controller";
import { markdown, type TextNode } from "./chat-markdown";
import { chatStyles, installChatBehavior } from "./upstream/kilo-chat";
import { telemetry, type Telemetry } from "./core/telemetry";

export interface Decision {
  questionId: string;
  kind: "answer" | "model";
  text: string;
  options: string[];
  suggested: string | null;
}

export interface PanelState {
  chat?: StatusView["chat"];
  formattedMessages?: Record<string, TextNode[]>;
  workspace: string;
  runDir: string;
  status: string;
  done: boolean;
  headline: string;
  line: string;
  needsYou: string | null;
  workflow: string | null;
  turn: number | null;
  iteration: number | null;
  phase: string | null;
  nextStage: string | null;
  task: string | null;
  usage: string | null;
  telemetry: Telemetry | null;
  pause: string | null;
  needKind: string | null;
  needSummary: string | null;
  needReason: string | null;
  tasks: { id: string; label: string; state: string; current: boolean }[];
  requirements: { id: string; criterion: string; state: string }[];
  requirementCounts: { checked: number; failed: number; unchecked: number; awaitingReview: number } | null;
  problems: { id: string; finding: string; severity: string; blocking: boolean }[];
  criteria: { id: string; criterion: string }[];
  decisions: Decision[];
  /** Draft answers belong to this request and these questions, not just their IDs. */
  decisionContext: string;
  gap: string | null;
  commandText: string;
  personOnly: boolean;
  taking: boolean;
  /** The plan the current token asks to approve, only when the view shows it. */
  plan: { revision: number | null; criteria: AcceptanceCriterion[]; constraints: string[]; boundaries: string[] } | null;
  /** The token the approval is bound to; the webview echoes it back so a stale click cannot approve. */
  token: string | null;
  /** False when the action would be an approval of a plan this client never displayed. */
  canApprove: boolean;
}

export class RunPanel {
  static readonly viewType = "autocode.runDetail";
  private static readonly open = new Map<string, RunPanel>();
  private static last: RunPanel | undefined;

  /** The run a person last opened, which is the one the status bar follows. */
  static lastRun(): RunRef | undefined {
    return RunPanel.last?.run;
  }

  static disposeAll(): void {
    for (const panel of [...RunPanel.open.values()]) panel.dispose();
  }

  private timer: NodeJS.Timeout | undefined;
  private taking = false;

  private constructor(
    private readonly panel: vscode.WebviewPanel,
    private readonly run: RunRef,
    private readonly controller: RunController,
  ) {
    this.panel.onDidDispose(() => this.dispose());
    this.panel.onDidChangeViewState(() => {
      if (this.panel.visible) {
        void this.update();
        this.start();
      } else this.stop();
    });
    this.panel.webview.onDidReceiveMessage((message) => void this.onMessage(message));
  }

  static show(column: vscode.ViewColumn, run: RunRef, controller: RunController): RunPanel {
    const key = `${run.workspace}\n${run.runDir}`;
    const existing = RunPanel.open.get(key);
    if (existing) {
      RunPanel.last = existing;
      existing.panel.reveal(column);
      return existing;
    }
    const panel = vscode.window.createWebviewPanel(RunPanel.viewType, titleOf(run), column, {
      enableScripts: true,
      retainContextWhenHidden: true,
      localResourceRoots: [],
    });
    const instance = new RunPanel(panel, run, controller);
    RunPanel.open.set(key, instance);
    RunPanel.last = instance;
    panel.webview.html = panelHtml();
    void instance.update();
    instance.start();
    return instance;
  }

  private start(): void {
    this.stop();
    const seconds = Math.max(1, vscode.workspace.getConfiguration("autocode").get<number>("pollSeconds", 5));
    this.timer = setInterval(() => void this.update(), seconds * 1000);
  }

  private stop(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = undefined;
  }

  dispose(): void {
    this.stop();
    RunPanel.open.delete(`${this.run.workspace}\n${this.run.runDir}`);
  }

  async refresh(): Promise<void> {
    await this.update();
  }

  private async update(): Promise<void> {
    if (!this.panel.visible) return;
    let view: StatusView;
    try {
      view = await this.controller.status(this.run);
    } catch (error) {
      this.post({ ...emptyState(this.run), gap: messageOf(error) });
      return;
    }
    this.post(stateFor(view, this.run, this.controller, this.taking));
  }

  private post(state: PanelState): void {
    this.panel.title = titleOf(this.run, state);
    void this.panel.webview.postMessage({ type: "state", state });
  }

  private async onMessage(message: { type?: string; answers?: Record<string, string>; token?: string | null }): Promise<void> {
    if (message.type === "refresh") {
      await this.update();
      return;
    }
    if (message.type !== "take") return;
    let view: StatusView;
    try {
      view = await this.controller.status(this.run);
    } catch (error) {
      void vscode.window.showErrorMessage(`AutoCode: ${messageOf(error)}`);
      return;
    }
    if (leavesForPerson(view.needs ?? null) && confirmPersonOnly()) {
      const proceed = await vscode.window.showWarningMessage(
        `AutoCode stopped this run for a person to decide (${view.needs?.kind}). Taking its named action applies your answer to that decision.`,
        { modal: true, detail: needsSummary(view) },
        "Take it anyway",
      );
      if (proceed !== "Take it anyway") return;
    }
    this.taking = true;
    this.post(stateFor(view, this.run, this.controller, true));
    try {
      await this.controller.take(view, this.run, message.answers ?? {}, message.token ?? null);
    } catch (error) {
      void vscode.window.showErrorMessage(`AutoCode: ${messageOf(error)}`);
    } finally {
      this.taking = false;
      await this.update();
    }
  }
}

function titleOf(run: RunRef, state?: PanelState): string {
  const name = run.runDir.split("/").filter(Boolean).pop() ?? run.runDir;
  return state?.headline ? `${trim(name, 22)} · ${trim(state.headline, 34)}` : trim(name, 40);
}

/** Build the render model, or name the gap where the view offers no actionable command. */
export function stateFor(view: StatusView, run: RunRef, controller: RunController, taking = false): PanelState {
  const progress = view.progress ?? null;
  const need = view.needs ?? null;
  const plan = planShape(view);
  let decisions: Decision[] = [];
  let gap: string | null = null;
  if (!view.done) {
    try {
      const action = controller.plan(view, run);
      decisions = action.decisions.map((open) => describe(open, need, view));
      if (action.unsupplied) {
        gap = `the command leaves ${action.unsupplied} open, which the status view does not supply`;
      }
    } catch (error) {
      gap = error instanceof ClientGap ? error.message : messageOf(error);
    }
  }
  const requirements = progress?.requirements;
  return {
    chat: view.chat,
    formattedMessages: Object.fromEntries((view.chat?.messages ?? []).filter((row) => row.role === "assistant").map((row) => [row.id, markdown(row.text)])),
    workspace: run.workspace,
    runDir: run.runDir,
    status: view.status,
    done: view.done,
    headline: progress?.headline ?? view.status,
    line: progressLine(view),
    needsYou: progress?.needs_you ?? null,
    workflow: view.workflow ?? null,
    turn: view.turn ?? null,
    iteration: view.iteration ?? null,
    phase: view.phase ?? null,
    nextStage: view.next_stage ?? null,
    task: view.task ?? null,
    usage: usageLine(view),
    telemetry: telemetry(view),
    pause: pauseLabel(view),
    needKind: need?.kind ?? null,
    needSummary: view.done ? null : needsSummary(view),
    needReason: need?.reason ?? null,
    tasks: (progress?.tasks?.items ?? []).map((task) => ({
      id: task.id,
      label: task.label ?? task.id,
      state: task.state,
      current: Boolean(task.current),
    })),
    requirements: (requirements?.items ?? []).map((row) => ({
      id: row.id,
      criterion: row.criterion ?? row.label ?? row.id,
      state: row.state,
    })),
    requirementCounts: requirements
      ? {
          checked: requirements.checked ?? 0,
          failed: requirements.failed ?? 0,
          unchecked: requirements.unchecked ?? 0,
          awaitingReview: requirements.awaiting_review ?? 0,
        }
      : null,
    problems: (progress?.problems?.items ?? []).map((finding) => ({
      id: finding.id,
      finding: finding.finding,
      severity: finding.severity ?? "",
      blocking: Boolean(finding.blocking),
    })),
    criteria: (need?.criteria ?? []).map((row) => ({ id: row.id, criterion: row.criterion ?? "" })),
    decisions,
    decisionContext: JSON.stringify([
      run.workspace, run.runDir, view.current_task?.id, view.turn, need?.kind,
      need?.request_id, need?.resolver_token, need?.token, need?.route, decisions,
    ]),
    gap,
    commandText: safeCommandText(view, run, controller),
    personOnly: leavesForPerson(need),
    taking,
    plan,
    token: approvalToken(view),
    canApprove: need?.kind !== "approve_plan" || plan !== null,
  };
}

/** The plan the token asks to approve, only when the view still shows that plan. */
function planShape(view: StatusView): PanelState["plan"] {
  const plan = planForApproval(view);
  if (!plan) return null;
  return {
    revision: plan.revision ?? null,
    criteria: plan.acceptance_criteria ?? [],
    constraints: plan.constraints ?? [],
    boundaries: plan.permission_boundaries ?? [],
  };
}

function describe(
  open: { questionId: string; kind: "answer" | "model" },
  need: StatusView["needs"],
  view: StatusView,
): Decision {
  const question: Question | undefined = need?.questions?.find((row) => row.id === open.questionId);
  const route = need?.route;
  const asksModel = open.kind === "model";
  return {
    questionId: open.questionId,
    kind: open.kind,
    text: question?.question ?? (asksModel && route ? `Name the model ${route.role} continues on` : open.questionId),
    options: asksModel ? [...(route?.candidates ?? [])] : [...(question?.options ?? [])],
    suggested: asksModel ? null : (question?.proposed_default ?? null),
  };
}

function safeCommandText(view: StatusView, run: RunRef, controller: RunController): string {
  try {
    return controller.commandText(view, run);
  } catch {
    return "";
  }
}

function emptyState(run: RunRef): PanelState {
  const base = {
    workspace: run.workspace,
    runDir: run.runDir,
    status: "unknown",
    done: false,
    headline: "unknown",
    line: "",
    needsYou: null,
    workflow: null,
    turn: null,
    iteration: null,
    phase: null,
    nextStage: null,
    task: null,
    usage: null,
    telemetry: null,
    pause: null,
    needKind: null,
    needSummary: null,
    needReason: null,
    tasks: [],
    requirements: [],
    requirementCounts: null,
    problems: [],
    criteria: [],
    decisions: [],
    decisionContext: "",
    commandText: "",
    personOnly: false,
    taking: false,
    plan: null,
    token: null,
    canApprove: true,
  } satisfies Omit<PanelState, "gap">;
  return { ...base, gap: null };
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function confirmPersonOnly(): boolean {
  return vscode.workspace.getConfiguration("autocode").get<boolean>("confirmPersonOnly", true);
}

function trim(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

/** The webview script. It renders with textContent only: untrusted text never becomes markup. */
export function panelHtml(chat = false): string {
  return /* html */ `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline';" />
<style>
  body { font-family: var(--vscode-font-family); padding: 0 16px 32px; color: var(--vscode-foreground); }
  h1 { font-size: 1.05rem; font-weight: 600; margin: 4px 0 12px; }
  h2 { font-size: .78rem; text-transform: uppercase; letter-spacing: .06em; opacity: .75; margin: 22px 0 8px; }
  .line { font-size: .95rem; margin-bottom: 4px; }
  .meta { font-size: .8rem; opacity: .8; }
  .row { display: flex; gap: 8px; align-items: baseline; padding: 3px 0; border-bottom: 1px solid var(--vscode-panel-border); }
  .state { font-size: .72rem; text-transform: uppercase; letter-spacing: .04em; padding: 1px 6px; border-radius: 3px;
           background: var(--vscode-badge-background); color: var(--vscode-badge-foreground); }
  .state.done, .state.checked, .state.reviewed { background: var(--vscode-charts-green, #3a7d3a); }
  .state.failed, .state.blocking { background: var(--vscode-errorForeground, #a1260d); }
  .state.working { background: var(--vscode-charts-blue, #1a6fb0); }
  .counts { display: flex; gap: 14px; font-size: .82rem; margin: 6px 0; }
  button { font: inherit; padding: 4px 12px; border: 1px solid var(--vscode-button-border, transparent);
           background: var(--vscode-button-background); color: var(--vscode-button-foreground); border-radius: 3px; cursor: pointer; }
  button:disabled { opacity: .5; cursor: default; }
  button.secondary { background: var(--vscode-button-secondaryBackground); color: var(--vscode-button-secondaryForeground); }
  input, select { font: inherit; width: 100%; margin-top: 4px; padding: 3px;
                  background: var(--vscode-input-background); color: var(--vscode-input-foreground);
                  border: 1px solid var(--vscode-input-border, transparent); }
  .decision { margin: 10px 0; }
  .decision .q { font-size: .86rem; }
  .why { font-size: .76rem; opacity: .75; }
  pre { white-space: pre-wrap; word-break: break-all; font-size: .76rem; background: var(--vscode-textCodeBlock-background);
        padding: 8px; border-radius: 4px; }
  .gap, .notice { border-left: 3px solid var(--vscode-inputValidation-warningBorder, #cc8800);
                  background: var(--vscode-inputValidation-warningBackground, rgba(204,136,0,.12));
                  padding: 8px 10px; margin: 12px 0; font-size: .84rem; }
  .notice { border-left-color: var(--vscode-textLink-foreground, #3794ff); background: transparent; }
  .actions { display: flex; gap: 8px; margin-top: 14px; }
  .muted { opacity: .65; }
  ${chat ? chatStyles : ""}
</style>
</head>
<body>
${chat ? '<nav class="chat-toolbar"><button id="history" class="secondary">History</button><button id="details" class="secondary">Run details</button><button id="new-chat" class="secondary">New chat</button></nav><div id="thread" tabindex="0" aria-label="Conversation"><div id="thread-content"><div id="messages" role="log" aria-label="Messages"></div>' : ""}
<div id="app" class="muted">${chat ? "Describe what you want to build, fix, review, or discuss." : "Loading…"}</div>
${chat ? `</div></div><button id="latest" type="button" hidden>↓ Latest messages</button>
<section id="telemetry" aria-label="Model and usage">
<details id="usage-details" open><summary id="telemetry-summary"><span class="telemetry-heading">Model &amp; usage</span>
<span id="model-summary">Model not reported</span><span id="usage-summary">Tokens not reported · Cost not reported</span></summary>
<div id="telemetry-body"><div id="model-info"></div><div id="usage-breakdown"></div></div></details>
</section>
<form id="composer">
<label for="message" id="send-hint">Message AutoCode</label>
<textarea id="message" aria-label="Message AutoCode" placeholder="Ask AutoCode to work on this project…"></textarea>
<div id="chat-error" role="alert"></div>
<div class="actions"><span class="keyboard-hint">Enter to send · Shift+Enter for newline</span>
<button type="submit" id="send-message" disabled>Send</button></div></form>` : ""}
<script>
const vscode = acquireVsCodeApi();
const app = document.getElementById('app');
const chatMode = ${chat};
let chatEnvelope = null;
const chatBehavior = chatMode ? (${installChatBehavior.toString()})() : null;
let current = null;
let answers = Object.create(null);
let renderedState = '';

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.appendChild(document.createTextNode(String(text)));
  return node;
}

function section(title, rows) {
  if (rows.length === 0) return;
  app.appendChild(el('h2', null, title));
  rows.forEach((row) => app.appendChild(row));
}

function stateRow(item) {
  const row = el('div', 'row');
  row.appendChild(el('span', 'state ' + item.state, item.state));
  row.appendChild(el('span', null, item.label || item.criterion || item.finding));
  app.appendChild(row);
  return row;
}

function render(state) {
  const serialized = JSON.stringify(state);
  if (serialized === renderedState) return;
  renderedState = serialized;
  const sameRequest = current && current.decisionContext === state.decisionContext;
  const focused = document.activeElement;
  const focusedQuestion = sameRequest && focused && focused.dataset.question;
  const selection = focusedQuestion && focused.tagName === 'INPUT'
    ? [focused.selectionStart, focused.selectionEnd, focused.selectionDirection] : null;
  if (!sameRequest) answers = Object.create(null);
  current = state;
  app.textContent = '';
  app.classList.remove('muted');
  app.appendChild(el('h1', null, state.headline));
  if (!chatMode || state.line !== state.headline) app.appendChild(el('div', 'line', state.line));
  const meta = [
    !chatMode ? state.status : null,
    state.workflow ? state.workflow + ' workflow' : null,
    state.turn ? 'turn ' + state.turn : null,
    !chatMode && state.iteration ? 'iteration ' + state.iteration : null,
    !chatMode && state.nextStage ? 'next: ' + state.nextStage : null,
    state.pause ? 'pause: ' + state.pause : null,
    !chatMode ? state.usage : null,
  ].filter(Boolean);
  app.appendChild(el('div', 'meta', meta.join('  ·  ')));

  if (state.task && !chatMode) {
    app.appendChild(el('h2', null, 'Request'));
    app.appendChild(el('pre', null, state.task));
  }

  if (state.tasks.length) {
    app.appendChild(el('h2', null, 'Tasks'));
    state.tasks.forEach(stateRow);
  }

  if (state.requirements.length) {
    app.appendChild(el('h2', null, 'Requirements'));
    if (state.requirementCounts) {
      const c = state.requirementCounts;
      const counts = el('div', 'counts');
      counts.appendChild(el('span', null, c.checked + ' checked'));
      counts.appendChild(el('span', null, c.failed + ' failed'));
      counts.appendChild(el('span', null, c.awaitingReview + ' awaiting your review'));
      counts.appendChild(el('span', null, c.unchecked + ' unchecked'));
      app.appendChild(counts);
    }
    state.requirements.forEach(stateRow);
  }

  if (state.problems.length) {
    app.appendChild(el('h2', null, 'Open problems'));
    state.problems.forEach((item) => {
      const row = el('div', 'row');
      row.appendChild(el('span', 'state ' + (item.blocking ? 'blocking' : ''), item.severity || 'finding'));
      row.appendChild(el('span', null, item.id + ': ' + item.finding));
      app.appendChild(row);
    });
  }

  if (state.criteria.length) {
    app.appendChild(el('h2', null, 'Criteria to accept'));
    state.criteria.forEach((row) => app.appendChild(el('div', 'row', row.id + ': ' + row.criterion)));
  }

  if (state.plan) {
    app.appendChild(el('h2', null, 'Plan the approval is bound to (revision ' + (state.plan.revision === null ? '?' : state.plan.revision) + ')'));
    app.appendChild(el('div', 'why',
      state.plan.criteria.length + ' acceptance criteria  ·  '
      + state.plan.constraints.length + ' constraints  ·  '
      + state.plan.boundaries.length + ' permission boundaries'));
    state.plan.criteria.forEach((criterion) => {
      const item = el('div', 'decision');
      item.appendChild(el('div', 'q', criterion.id + ': ' + criterion.criterion));
      item.appendChild(el('div', 'why', 'Verification:'));
      item.appendChild(el('pre', null, criterion.verification_method));
      item.appendChild(el('div', 'why', 'Human review: ' + (criterion.human_review ? 'required' : 'not required')));
      app.appendChild(item);
    });
    if (state.plan.constraints.length) {
      app.appendChild(el('h2', null, 'Constraints'));
      state.plan.constraints.forEach((text) => app.appendChild(el('div', 'row', text)));
    }
    if (state.plan.boundaries.length) {
      app.appendChild(el('h2', null, 'What it may do'));
      state.plan.boundaries.forEach((text) => app.appendChild(el('div', 'row', text)));
    }
  }

  if (!state.done) {
    app.appendChild(el('h2', null, 'Waiting for'));
    app.appendChild(el('div', 'line', state.needsYou || state.needSummary || state.needKind || '—'));
    if (state.needReason) app.appendChild(el('div', 'why', state.needReason));
  }

  if (state.gap) {
    app.appendChild(el('h2', null, 'No action the client can take'));
    const box = el('div', 'gap');
    box.appendChild(el('div', null,
      'AutoCode named no action this client is allowed to take. That is a gap in the CLI or the '
      + 'status view (#1012), not something the extension should work around.'));
    box.appendChild(el('pre', null, state.gap));
    app.appendChild(box);
  } else if (!state.done) {
    const form = el('div');
    state.decisions.forEach((decision) => {
      const box = el('div', 'decision');
      box.appendChild(el('div', 'q', decision.text));
      if (decision.suggested) box.appendChild(el('div', 'why', 'proposed: ' + decision.suggested));
      const name = decision.questionId;
      let input;
      if (decision.options.length > 0) {
        input = document.createElement('select');
        const blank = document.createElement('option');
        blank.value = '';
        blank.textContent = 'Choose…';
        input.appendChild(blank);
        decision.options.forEach((option) => {
          const item = document.createElement('option');
          item.value = option;
          item.textContent = option;
          input.appendChild(item);
        });
      } else {
        input = document.createElement('input');
        input.type = 'text';
        if (decision.suggested) input.placeholder = decision.suggested;
      }
      input.dataset.question = name;
      if (answers[name]) input.value = answers[name];
      const save = () => { answers[name] = input.value; updateActionButton(); };
      input.addEventListener('change', save);
      input.addEventListener('input', save);
      box.appendChild(input);
      form.appendChild(box);
    });
    app.appendChild(form);

    const actions = el('div', 'actions');
    var approving = state.needKind === 'approve_plan';
    var label = state.taking ? 'Working…' : (approving ? (chatMode ? 'Approve and build' : 'Approve this plan')
      : (state.needKind === 'review' ? 'Accept the reviewed criteria' : (chatMode ? (state.decisions.length ? 'Send answers and continue' : 'Continue') : 'Take the named action')));
    const take = el('button', null, label);
    take.id = 'take-action';
    take.addEventListener('click', () => {
      vscode.postMessage({ type: 'take', answers: { ...answers }, token: state.token || null,
        runKey: state.workspace + '\\n' + state.runDir, decisionContext: state.decisionContext });
    });
    actions.appendChild(take);
    const again = el('button', 'secondary', 'Refresh');
    again.addEventListener('click', () => vscode.postMessage({ type: 'refresh' }));
    actions.appendChild(again);
    app.appendChild(actions);
    updateActionButton();

    if (!state.canApprove && state.needKind === 'approve_plan') {
      const notice = el('div', 'notice');
      notice.appendChild(el('div', null,
        'AutoCode has not shown this client the plan its approval token binds to, so approving here would '
        + 'approve something you have not read. Review it first:'));
      notice.appendChild(el('pre', null, 'autocode --workspace ' + state.workspace + ' --run-dir ' + state.runDir + ' --status'));
      app.appendChild(notice);
    }

    if (state.personOnly) {
      app.appendChild(el('div', 'notice',
        'This pause is AutoCode asking a person. The extension will ask before acting on it.'));
    }
  }

  if (state.commandText && !chatMode) {
    app.appendChild(el('h2', null, 'Command the view named'));
    app.appendChild(el('pre', null, state.commandText));
  }
  if (focusedQuestion) {
    const input = [...document.querySelectorAll('[data-question]')].find((node) => node.dataset.question === focusedQuestion);
    if (input) {
      input.focus();
      if (selection && input.tagName === 'INPUT') input.setSelectionRange(...selection);
    }
  }
}

function updateActionButton() {
  const take = document.getElementById('take-action');
  if (!take || !current) return;
  const missing = current.decisions.some((decision) => !(answers[decision.questionId] || '').trim());
  take.disabled = current.taking || missing || !current.canApprove;
}

window.addEventListener('message', (event) => {
  const data = event.data;
  if (data && data.type === 'state') render(data.state);
  if (data && data.type === 'chat') renderChat(data);
  if (data && data.type === 'sent' && chatEnvelope && data.runKey === chatEnvelope.runKey) {
    const field = document.getElementById('message');
    if (field.value === data.text) field.value = '';
    saveDraft(); updateComposer(); chatBehavior.resize();
  }
});

function saveDraft() {
  if (!chatMode || !chatEnvelope) return;
  const saved = vscode.getState() || {};
  const drafts = saved.drafts || {};
  drafts[chatEnvelope.runKey] = document.getElementById('message').value;
  vscode.setState({ ...saved, drafts });
}

function updateComposer() {
  if (!chatEnvelope) return;
  const input = chatEnvelope.state && chatEnvelope.state.chat && chatEnvelope.state.chat.input;
  const allowed = !chatEnvelope.runKey || Boolean(input);
  document.getElementById('send-message').disabled = chatEnvelope.busy || !allowed || !document.getElementById('message').value.trim();
  document.getElementById('send-message').textContent = 'Send ↑';
  document.getElementById('send-message').setAttribute('aria-label', input ? input.label + ' and continue' : 'Send message');
  document.getElementById('send-hint').textContent = !chatEnvelope.runKey ? 'Message AutoCode'
    : input ? input.label + ' and continue'
    : chatEnvelope.busy ? 'AutoCode is working. You can keep a draft here.' : 'Use the action above, or start a new chat.';
  document.getElementById('chat-error').textContent = chatEnvelope.error || '';
}

function renderChat(data) {
  const thread = document.getElementById('thread');
  const same = chatEnvelope && chatEnvelope.runKey === data.runKey;
  if (!same) {
    saveDraft();
    const saved = vscode.getState() || {};
    document.getElementById('message').value = (saved.drafts || {})[data.runKey] || '';
    current = null; renderedState = ''; answers = Object.create(null);
  }
  chatEnvelope = data;
  const messages = document.getElementById('messages');
  const rows = data.state && data.state.chat ? data.state.chat.messages
    : data.pendingBrief ? [{ role: 'user', text: data.pendingBrief }] : [];
  const serialized = JSON.stringify(rows);
  if (messages.dataset.content !== serialized) {
    messages.dataset.content = serialized; messages.textContent = '';
    rows.forEach((row) => {
      const item = el('div', 'message ' + (row.role === 'user' ? 'user' : 'assistant'));
      item.appendChild(el('div', 'speaker', row.role === 'user' ? 'You' : 'AutoCode'));
      const body = el('div', 'message-content');
      const formatted = data.state && data.state.formattedMessages && data.state.formattedMessages[row.id];
      if (row.role === 'assistant' && formatted) appendFormatted(body, formatted);
      else body.textContent = row.text;
      item.appendChild(body); messages.appendChild(item);
    });
  }
  if (data.state) render(data.state);
  else { app.textContent = data.busy ? 'Starting AutoCode…' : 'Describe what you want to build, fix, review, or discuss.'; }
  renderTelemetry(data.state && data.state.telemetry);
  updateComposer();
  chatBehavior.resize();
  if (!same) chatBehavior.resume();
}

function renderTelemetry(info) {
  const host = document.getElementById('model-info');
  const signature = JSON.stringify(info);
  if (host.dataset.content === signature) return;
  host.dataset.content = signature;
  host.textContent = '';
  document.getElementById('model-summary').textContent = info && info.models.length
    ? info.models.map((item) => item.model.split('/').pop() + ' · ' + item.variant).join(' / ')
    : 'Model not reported';
  if (!info || !info.models.length) host.appendChild(el('span', 'model-placeholder', info ? 'Model and variant not reported' : 'Model available after the first stage starts'));
  else {
    host.appendChild(el('div', 'model-caption', info.modelLabel));
    info.models.forEach((item) => {
      const model = el('div', 'model-card');
      [['Model', item.model], ['Provider', item.provider], ['Variant', item.variant]].forEach(([label, value]) => {
        const row = el('div', 'telemetry-row');
        row.appendChild(el('span', 'telemetry-label', label));
        row.appendChild(el('span', 'model-value', value));
        model.appendChild(row);
      });
      host.appendChild(model);
    });
  }
  document.getElementById('usage-summary').textContent = info ? info.tokens + ' · ' + info.cost : 'Tokens not reported · Cost not reported';
  const details = document.getElementById('usage-breakdown');
  details.textContent = '';
  if (!info) { details.textContent = 'Usage will appear as AutoCode reports it.'; return; }
  details.appendChild(el('div', 'model-caption', 'Usage for this run'));
  info.details.forEach((item) => {
    const row = el('div', 'telemetry-row');
    row.appendChild(el('span', null, item.label));
    row.appendChild(el('span', null, item.value));
    details.appendChild(row);
  });
}

function appendFormatted(parent, nodes) {
  const allowed = new Set(['p','h2','h3','h4','h5','h6','strong','em','del','code','pre','blockquote','br','hr','ol','ul','li','table','thead','tbody','tr','th','td','a','span']);
  nodes.forEach((node) => {
    const child = node.tag && allowed.has(node.tag) ? document.createElement(node.tag) : document.createTextNode(node.text || '');
    if (node.tag) {
      child.textContent = node.text || '';
      if (node.tag === 'a' && /^https?:\\/\\//i.test(node.href || '')) {
        child.setAttribute('href', node.href); child.setAttribute('rel', 'noreferrer noopener'); child.setAttribute('target', '_blank');
      }
      if (node.children) appendFormatted(child, node.children);
    }
    parent.appendChild(child);
  });
}

if (chatMode) {
  const disclosure = document.getElementById('usage-details');
  const saved = vscode.getState() || {};
  disclosure.open = saved.modelDetailsOpen !== false;
  disclosure.addEventListener('toggle', () => {
    vscode.setState({ ...(vscode.getState() || {}), modelDetailsOpen: disclosure.open });
  });
  document.getElementById('history').addEventListener('click', () => vscode.postMessage({ type: 'history' }));
  document.getElementById('details').addEventListener('click', () => vscode.postMessage({ type: 'details' }));
  document.getElementById('message').addEventListener('input', () => { saveDraft(); updateComposer(); });
  document.getElementById('composer').addEventListener('submit', (event) => {
    event.preventDefault();
    if (document.getElementById('send-message').disabled) return;
    chatBehavior.resume();
    vscode.postMessage({ type: 'send', text: document.getElementById('message').value,
      runKey: chatEnvelope.runKey, context: chatEnvelope.state && chatEnvelope.state.chat && chatEnvelope.state.chat.context });
  });
  document.getElementById('message').addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); document.getElementById('composer').requestSubmit(); }
  });
  document.getElementById('new-chat').addEventListener('click', () => vscode.postMessage({ type: 'new' }));
}

vscode.postMessage({ type: 'ready' });
</script>
</body>
</html>`;
}
