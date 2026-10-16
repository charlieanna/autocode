/**
 * The run detail panel: what the status view says, and the action it names.
 *
 * The webview renders from a plain state object with `textContent`, never by
 * interpolating model-authored text into HTML — a plan or a finding is
 * untrusted text.
 */

import * as vscode from "vscode";
import { ClientGap, leavesForPerson } from "./core/next-command";
import type { Question, StatusView } from "./core/view";
import { approvalToken, needsSummary, pauseLabel, planForApproval, progressLine, usageLine } from "./core/view";
import type { RunController, RunRef } from "./controller";

export interface Decision {
  questionId: string;
  kind: "answer" | "model";
  text: string;
  options: string[];
  suggested: string | null;
}

export interface PanelState {
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
  gap: string | null;
  commandText: string;
  personOnly: boolean;
  taking: boolean;
  /** The plan the current token asks to approve, only when the view shows it. */
  plan: { revision: number | null; criteria: string[]; constraints: string[]; boundaries: string[] } | null;
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
      if (this.panel.visible) void this.update();
      else this.stop();
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
    panel.webview.html = html();
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
      criterion: row.criterion,
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
function html(): string {
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
</style>
</head>
<body>
<div id="app" class="muted">Loading…</div>
<script>
const vscode = acquireVsCodeApi();
const app = document.getElementById('app');
let current = null;

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
  current = state;
  app.textContent = '';
  app.classList.remove('muted');
  app.appendChild(el('h1', null, state.headline));
  app.appendChild(el('div', 'line', state.line));
  const meta = [
    state.status,
    state.workflow ? state.workflow + ' workflow' : null,
    state.turn ? 'turn ' + state.turn : null,
    state.iteration ? 'iteration ' + state.iteration : null,
    state.nextStage ? 'next: ' + state.nextStage : null,
    state.pause ? 'pause: ' + state.pause : null,
    state.usage,
  ].filter(Boolean);
  app.appendChild(el('div', 'meta', meta.join('  ·  ')));

  if (state.task) {
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
    state.plan.criteria.forEach((text) => app.appendChild(el('div', 'row', text)));
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
      const answers = (current.answers) || {};
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
      input.addEventListener('change', () => { current.answers = current.answers || {}; current.answers[name] = input.value; });
      input.addEventListener('input', () => { current.answers = current.answers || {}; current.answers[name] = input.value; });
      box.appendChild(input);
      form.appendChild(box);
    });
    app.appendChild(form);

    const actions = el('div', 'actions');
    var approving = state.needKind === 'approve_plan';
    var label = state.taking ? 'Taking…' : (approving ? 'Approve this plan' : (state.needKind === 'review' ? 'Accept the reviewed criteria' : 'Take the named action'));
    const take = el('button', null, label);
    var needsModel = state.decisions.some((d) => d.kind === 'model' && !(current.answers || {})[d.questionId]);
    take.disabled = state.taking || needsModel || !state.canApprove;
    take.addEventListener('click', () => {
      vscode.postMessage({ type: 'take', answers: current.answers || {}, token: state.token || null });
    });
    actions.appendChild(take);
    const again = el('button', 'secondary', 'Refresh');
    again.addEventListener('click', () => vscode.postMessage({ type: 'refresh' }));
    actions.appendChild(again);
    app.appendChild(actions);

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

  if (state.commandText) {
    app.appendChild(el('h2', null, 'Command the view named'));
    app.appendChild(el('pre', null, state.commandText));
  }
}

window.addEventListener('message', (event) => {
  const data = event.data;
  if (data && data.type === 'state') render(data.state);
});

vscode.postMessage({ type: 'ready' });
</script>
</body>
</html>`;
}
