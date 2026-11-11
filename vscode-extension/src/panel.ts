/**
 * The run detail panel: what the status view says, and the action it names.
 *
 * The webview renders from a plain state object with `textContent`, never by
 * interpolating model-authored text into HTML — a plan or a finding is
 * untrusted text.
 */

import * as vscode from "vscode";
import { ClientGap, integerValue, leavesForPerson, requiresConfirmation, type OpenDecision } from "./core/next-command";
import type { AcceptanceCriterion, ChangesetSummary, DisplayedPlan, IntegerCommandInput, Question, StatusView } from "./core/view";
import { approvalToken, isWorking, needsSummary, pauseLabel, planForApproval, progressLine, usageLine } from "./core/view";
import type { RunController, RunRef } from "./controller";
import { markdown, type TextNode } from "./chat-markdown";
import { chatStyles, installChatBehavior } from "./upstream/kilo-chat";
import { telemetry, type Telemetry } from "./core/telemetry";
import { finishSummary, runTotal, struggling, totalText, type FinishSummary } from "./core/meter";
import { runHeader, type RunHeader } from "./core/details";

export interface Decision {
  questionId: string;
  kind: "answer" | "model" | "effort" | "integer";
  input?: IntegerCommandInput;
  text: string;
  options: string[];
  suggested: string | null;
}

export const NUMERIC_RECOVERY_EFFECT = "This changes only the total active time. Other saved limits and model routes stay the same. It does not approve a plan.";

/** The button that takes a bound's separately named raise (#2044). */
export const RAISE_LABEL = "Raise limit and continue";

/**
 * How a confirmation names each bound a person may raise: the question for a typed total, and what
 * taking it changes. Keyed by the core's row id (autocode_command_inputs); the rows themselves are
 * validated against the core's table in core/next-command.ts.
 */
const BOUND_WORDING: Record<string, [(total: string) => string, string]> = {
  max_seconds: [(total) => `Set total active time to ${total} seconds and continue?`, "the total active time"],
  max_cost_usd: [(total) => `Set the cost cap to $${total} and continue?`, "the cost cap"],
  max_iterations: [(total) => `Set total iterations to ${total} and continue?`, "the iteration ceiling"],
  max_milestone_seconds: [(total) => `Set the milestone's active time to ${total} seconds and continue?`, "the milestone's active time"],
  max_milestone_replans: [(total) => `Allow ${total} milestone replans and continue?`, "the milestone replan limit"],
  no_progress_limit: [(total) => `Allow ${total} unchanged implementation batches and continue?`, "the unchanged-batch limit"],
};

export interface PanelState {
  numericActionLabel?: string;
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
  /** Whether this chat's own stage models can change now (`view.stage_models`), never its command. */
  stageModels?: { changeable: boolean; reason: string | null } | null;
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
  actionLabel: string | null;
  actionEffect: string | null;
  stopped: { title: string; explanation: string; retained: string | null; newConversation: string | null } | null;
  commandText: string;
  personOnly: boolean;
  taking: boolean;
  /** This client is acting, or AutoCode reports live stage/controller work. */
  working: boolean;
  /** The pause the view offers while the run works (#1598); `requested` once one waits for the step to end. */
  pauseOffer: { label: string; effect: string | null; requested: boolean } | null;
  /** Chat only: the latest status read failed, so this state is an older read and its actions stay disabled (#1593). */
  unreadable?: boolean;
  /** Saved code changes and the actions the view named for them (#1532). */
  workingDiff: { label: string } | null;
  checkpoints: { id: string; label: string; at: string | null; files: number; available: boolean; reason: string | null; verdict: string | null; actions: { id: string; label: string }[] }[];
  checkpointsBlocked: string | null;
  /** The plan the current token asks to approve, only when the view shows it. */
  plan: { revision: number | null; criteria: AcceptanceCriterion[]; constraints: string[]; boundaries: string[]; exclusions: string[]; delivery: string | null; milestones: { id: string; objective: string; acceptance_criteria: string[] }[]; flow: string[] } | null;
  /** The token the approval is bound to; the webview echoes it back so a stale click cannot approve. */
  token: string | null;
  /** False when the action would be an approval of a plan this client never displayed. */
  canApprove: boolean;
  /** This run's change set (`changeset status --json`, #2066), or null when it is in none. */
  changeset?: ChangesetState | null;
  /** The raise the view names apart from next_command for the bound holding the run (#2044), or null. */
  boundRaise: IntegerCommandInput | null;
  /** The struggling warning and the run's cost line, from the view's own records. */
  meter?: MeterState;
  /** A finished run's closing card, or null while it runs. */
  finish?: FinishSummary | null;
  /** The header at the top of the chat: the pipeline, where the run is, and its details. */
  header?: RunHeader | null;
}

/** The struggling warning and the run's cost line the chat shows; each stage's row is the run header's. */
export interface MeterState {
  struggling: string;
  total: string;
}

/** The meter for one status view: display only, every figure the core's own. */
export function meterOf(view: StatusView, now = Date.now()): MeterState {
  return {
    struggling: view.done ? "" : struggling(view, now),
    total: totalText(runTotal(view)),
  };
}

/** A change set as the panel shows it: every run, and the one action `changeset status` names now. */
export interface ChangesetState {
  id: string;
  runs: { target: string; role: string; status: string; needs: string | null }[];
  /** Each run's plan, shown only while the set approval is offered: that approval approves them. */
  plans: { target: string; plan: NonNullable<PanelState["plan"]> }[];
  offer: { key: "approval" | "verification"; label: string; effect: string } | null;
  /** The set token the shown plans make; the webview echoes it back so a stale click approves nothing. */
  token: string | null;
  note: string | null;
}

/** The panel's change-set card for a `changeset status --json` summary. */
export function changesetState(summary: ChangesetSummary): ChangesetState {
  const { approval, verification } = summary;
  const approving = Boolean(approval.next_command?.length);
  let note: string | null = null;
  if (verification.done) note = "Checked together: done. Merge order: " + verification.merge_order.join(", then ");
  else if (!approving && approval.waiting_on.length && verification.waiting_on.length) note = "A set approval waits for a complete plan from: " + approval.waiting_on.join(", ");
  else if (verification.result?.current && !verification.result.passed) note = "The integration check failed. Reopen the run that diverged (autocode changeset reopen) or waive it with a reason (autocode changeset waive).";
  return {
    id: summary.changeset,
    runs: summary.runs.map((row) => ({ target: row.target, role: row.role, status: row.status, needs: row.needs ?? null })),
    plans: approving ? summary.runs.map((row) => ({ target: row.target, plan: planOf(row.plan ?? {}) })) : [],
    offer: approving
      ? { key: "approval", label: `Approve all ${summary.runs.length} plans`, effect: "Approves every plan shown here together, the source run first. A plan revised in any run withdraws this approval." }
      : verification.next_command?.length
        ? { key: "verification", label: "Run the set's check", effect: `Runs ${(verification.check?.command ?? []).join(" ")} in ${verification.check?.in ?? "the named run"}'s workspace, with no shell.` }
        : null,
    token: approving ? approval.set_token : null,
    note,
  };
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
  private readonly configuration: vscode.Disposable;
  private taking = false;
  private onAction?: () => void | Promise<void>;

  private constructor(
    private readonly panel: vscode.WebviewPanel,
    private readonly run: RunRef,
    private readonly controller: RunController,
    onAction?: () => void | Promise<void>,
  ) {
    this.onAction = onAction;
    this.configuration = vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration("autocode.pollSeconds") && this.panel.visible) this.start();
    });
    this.panel.onDidDispose(() => this.dispose());
    this.panel.onDidChangeViewState(() => {
      if (this.panel.visible) {
        void this.update();
        this.start();
      } else this.stop();
    });
    this.panel.webview.onDidReceiveMessage((message) => void this.onMessage(message));
  }

  static show(
    column: vscode.ViewColumn,
    run: RunRef,
    controller: RunController,
    onAction?: () => void | Promise<void>,
  ): RunPanel {
    const key = `${run.workspace}\n${run.runDir}`;
    const existing = RunPanel.open.get(key);
    if (existing) {
      RunPanel.last = existing;
      if (onAction) existing.onAction = onAction;
      existing.panel.reveal(column);
      return existing;
    }
    const panel = vscode.window.createWebviewPanel(RunPanel.viewType, titleOf(run), column, {
      enableScripts: true,
      retainContextWhenHidden: true,
      localResourceRoots: [],
    });
    const instance = new RunPanel(panel, run, controller, onAction);
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
    this.configuration.dispose();
    RunPanel.open.delete(`${this.run.workspace}\n${this.run.runDir}`);
    if (RunPanel.last === this) RunPanel.last = undefined;
  }

  async refresh(): Promise<void> {
    await this.update();
  }

  private async update(): Promise<void> {
    if (!this.panel.visible) return;
    let view: StatusView;
    try {
      view = await this.controller.pollStatus(this.run);
    } catch (error) {
      this.post({ ...emptyState(this.run), gap: messageOf(error) });
      return;
    }
    this.post({ ...stateFor(view, this.run, this.controller, this.taking), changeset: await this.changesetCard(view) });
  }

  /** The change-set card, or null when the run is in none; a failed read is shown, never guessed at. */
  private async changesetCard(view: StatusView): Promise<ChangesetState | null> {
    try {
      const summary = await this.controller.changeset(view, this.run);
      return summary ? changesetState(summary) : null;
    } catch (error) {
      return { id: "", runs: [], plans: [], offer: null, token: null, note: `The change set could not be read: ${messageOf(error)}` };
    }
  }

  /** The change set's named action, confirmed first, bound to the set token the card showed. */
  private async takeChangeset(key: "approval" | "verification", token: string | null): Promise<void> {
    if (this.taking) return;
    this.taking = true;
    try {
      const view = await this.controller.status(this.run);
      const question = key === "approval" ? "Approve every plan of the change set together?" : "Run the change set's integration check now?";
      const button = key === "approval" ? "Approve all" : "Run the check";
      if ((await vscode.window.showWarningMessage(question, { modal: true }, button)) !== button) return;
      // A joint check runs a while: say it is working before it starts, like the take and
      // raise paths, or the card keeps its buttons looking clickable while they are not.
      this.post({ ...stateFor(view, this.run, this.controller, true), changeset: await this.changesetCard(view) });
      await this.controller.takeChangeset(view, this.run, key, token);
    } catch (error) {
      void vscode.window.showErrorMessage(`AutoCode: ${messageOf(error)}`);
    } finally {
      this.taking = false;
      await this.update();
      try {
        await this.onAction?.();
      } catch {
        // Ignored
      }
    }
  }

  /** The bound's separately named raise with the typed number, confirmed against the offer shown (#2044). */
  private async raise(value: string, decisionContext: string | undefined): Promise<void> {
    if (this.taking) return;
    this.taking = true;
    try {
      const view = await this.controller.status(this.run);
      if (!(await confirmRaise(stateFor(view, this.run, this.controller, true), decisionContext, value))) return;
      this.post(stateFor(view, this.run, this.controller, true));
      await this.controller.raise(view, this.run, value);
    } catch (error) {
      void vscode.window.showErrorMessage(`AutoCode: ${messageOf(error)}`);
    } finally {
      this.taking = false;
      await this.update();
      try {
        await this.onAction?.();
      } catch {
        // Ignored
      }
    }
  }

  private post(state: PanelState): void {
    this.panel.title = titleOf(this.run, state);
    void this.panel.webview.postMessage({ type: "state", state });
  }

  private async onMessage(message: {
    type?: string;
    answers?: Record<string, string>;
    token?: string | null;
    decisionContext?: string;
    key?: string;
    value?: string;
  }): Promise<void> {
    if (message.type === "changeset" && (message.key === "approval" || message.key === "verification")) {
      await this.takeChangeset(message.key, message.token ?? null);
      return;
    }
    if (message.type === "chat") {
      await vscode.commands.executeCommand("autocode.openRun", this.run);
      return;
    }
    if (message.type === "new") {
      await vscode.commands.executeCommand("autocode.startRun");
      return;
    }
    if (message.type === "refresh") {
      await this.update();
      return;
    }
    if (message.type === "raise") {
      await this.raise(message.value ?? "", message.decisionContext);
      return;
    }
    if (message.type !== "take") return;
    if (this.taking) return;
    this.taking = true;
    try {
      let view: StatusView;
      try {
        view = await this.controller.status(this.run);
      } catch (error) {
        void vscode.window.showErrorMessage(`AutoCode: ${messageOf(error)}`);
        return;
      }
      const state = stateFor(view, this.run, this.controller, true);
      const numeric = numericConfirmation(state, message.answers ?? {});
      const planning = planningSpendConfirmation(view, message.answers ?? {});
      if ((message.decisionContext || numeric) && message.decisionContext !== state.decisionContext) {
        void vscode.window.showErrorMessage("AutoCode: The requested action changed; review it before continuing");
        return;
      }
      if (numeric || planning || (requiresConfirmation(view.needs ?? null) && confirmPersonOnly())) {
        const button = numeric ? numericActionLabel(state) : planning?.label ?? "Take it anyway";
        const proceed = await vscode.window.showWarningMessage(
          numeric ?? planning?.title ?? `AutoCode stopped this run for a person to decide (${view.needs?.kind}). Taking its named action applies your answer to that decision.`,
          { modal: true, detail: numeric ? numericRecoveryEffect(state) : planning?.detail ?? needsSummary(view) },
          button,
        );
        if (proceed !== button) return;
      }
      this.post(state);
      try {
        await this.controller.take(view, this.run, message.answers ?? {}, message.token ?? null);
      } catch (error) {
        void vscode.window.showErrorMessage(`AutoCode: ${messageOf(error)}`);
      }
    } catch (error) {
      void vscode.window.showErrorMessage(`AutoCode: ${messageOf(error)}`);
    } finally {
      this.taking = false;
      await this.update();
      try {
        await this.onAction?.();
      } catch {
        // Ignored
      }
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
  let actionLabel: string | null = null;
  let actionEffect: string | null = null;
  const stopped = view.recovery?.category === "stopped" ? {
    title: view.recovery.title ?? "Stopped",
    explanation: view.recovery.what_happened ?? "",
    retained: view.recovery.retained ?? null,
    newConversation: view.recovery.actions?.find((row) => row.kind === "new_conversation")?.label ?? null,
  } : null;
  if (!view.done && !stopped) {
    try {
      const action = controller.plan(view, run);
      decisions = action.decisions.map((open) => describe(open, need, view));
      if (action.unsupplied) {
        gap = `the command leaves ${action.unsupplied} open, which the status view does not supply`;
      } else if (!decisions.length && view.recovery?.actions) {
        // A label describes only the exact action next_command names. Recovery
        // descriptions never supply flags or authorize a different action.
        const args = controller.prepare(view, run, {}).argv;
        const recovery = view.recovery.actions.find((row) => Array.isArray(row.args)
          && JSON.stringify(row.args) === JSON.stringify(args));
        actionLabel = recovery?.label ?? null;
        actionEffect = recovery?.effect ?? null;
      }
    } catch (error) {
      gap = error instanceof ClientGap ? error.message : messageOf(error);
    }
  }
  const boundRaise = view.done || stopped ? null : controller.boundRaise(view, run);
  const requirements = progress?.requirements;
  const working = taking || isWorking(view);
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
    stageModels: view.stage_models ? { changeable: view.stage_models.changeable === true, reason: view.stage_models.reason ?? null } : null,
    pause: pauseLabel(view),
    needKind: need?.kind ?? null,
    needSummary: view.done ? null : needsSummary(view, working),
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
    numericActionLabel: numericActionLabel({ decisions }),
    decisionContext: JSON.stringify([
      run.workspace,
      run.runDir,
      view.current_task?.id,
      view.turn,
      need?.kind,
      need?.request_id,
      need?.resolver_token,
      need?.resolver_request_id,
      need?.token,
      need?.job_retry_token,
      view.recovery?.token,
      need?.route,
      decisions,
      view.next_command ?? "",
      boundRaise,
    ]),
    gap,
    actionLabel,
    actionEffect,
    stopped,
    commandText: safeCommandText(view, run, controller),
    personOnly: leavesForPerson(need),
    taking,
    working,
    pauseOffer: view.pause && !view.done ? {
      label: view.pause.label ?? "Pause after this step",
      effect: view.pause.effect ?? null,
      requested: view.pause.requested === true || !Array.isArray(view.pause.next_command),
    } : null,
    checkpoints: (view.code_checkpoints?.rows ?? []).map((row) => ({
      id: row.id,
      label: row.label ?? row.id,
      at: row.at ?? null,
      files: row.changed_files?.length ?? 0,
      available: row.available !== false,
      reason: row.reason ?? null,
      verdict: row.recorded_check?.verdict ?? null,
      actions: (row.actions ?? []).map((action) => ({ id: action.id, label: action.label })),
    })),
    checkpointsBlocked: view.code_checkpoints?.blocked_reason ?? null,
    workingDiff: view.code_checkpoints?.working?.next_command ? { label: view.code_checkpoints.working.label ?? "Show changes so far" } : null,
    plan,
    token: approvalToken(view),
    canApprove: need?.kind !== "approve_plan" || plan !== null,
    boundRaise,
    meter: meterOf(view),
    finish: finishSummary(view),
    header: runHeader(view),
  };
}

/** The plan the token asks to approve, only when the view still shows that plan. */
function planShape(view: StatusView): PanelState["plan"] {
  const plan = planForApproval(view);
  return plan ? planOf(plan) : null;
}

function planOf(plan: DisplayedPlan): NonNullable<PanelState["plan"]> {
  return {
    revision: plan.revision ?? null,
    criteria: plan.acceptance_criteria ?? [],
    constraints: plan.constraints ?? [],
    boundaries: plan.permission_boundaries ?? [],
    exclusions: plan.scope_exclusions ?? [],
    delivery: plan.git_delivery ? "Create one local atomic commit: " + plan.git_delivery.message : null,
    milestones: (plan.milestones ?? []).map((m) => ({
      id: m.id,
      objective: m.objective,
      acceptance_criteria: m.acceptance_criteria ?? [],
    })),
    flow: plan.end_to_end_flow ?? [],
  };
}

function describe(
  open: OpenDecision,
  need: StatusView["needs"],
  view: StatusView,
): Decision {
  if (open.kind === "integer" && open.input) return {
    questionId: open.questionId, kind: "integer", input: open.input,
    text: open.input.label, options: [], suggested: null,
  };
  const question: Question | undefined = need?.questions?.find((row) => row.id === open.questionId);
  if (open.kind === "effort") return {
    questionId: open.questionId, kind: "effort",
    text: question?.question ?? `Choose an effort for ${need?.effort?.job ?? open.questionId}`,
    options: [...(need?.effort?.question_id === open.questionId ? need.effort.declared ?? [] : [])],
    suggested: null,
  };
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

/** Only the core-named Requirements answer starts paid planning; corrections stay conversational. */
export function planningSpendConfirmation(view: StatusView, answers: Record<string, string>) {
  const review = view.chat?.review;
  const confirmation = review?.spend_confirmation;
  if (!review || !confirmation) return null;
  // The core declares the same trim/lower match its Requirements answer uses.
  // Python also strips these control separators; argv keeps the original text.
  const normalize = (value: string) => confirmation.match === "trim_lower"
    ? value.replace(/^[\s\u001c-\u001f\u0085]+|[\s\u001c-\u001f\u0085]+$/g, "").toLowerCase() : value;
  const answer = answers[review.question_id];
  return typeof answer === "string" && normalize(answer) === normalize(review.answer) ? confirmation : null;
}

/** A changed allowance always needs explicit confirmation, even if generic pause prompts are disabled. */
export function numericConfirmation(state: PanelState, answers: Record<string, string>): string | null {
  const input = state.decisions.find((row) => row.kind === "integer")?.input;
  if (!input) return null;
  const total = integerValue(answers[input.id], input);
  if (input.id === "grant_recovery") return `Replenish ${total} recovery slot${total === "1" ? "" : "s"} and continue?`;
  if (input.id === "planning_review_call_limit") {
    return `Set the total plan-review allowance to ${total} calls?`;
  }
  return boundQuestion(input, total);
}

/** The confirmation for a bound's separate raise with the typed number; a number out of bounds is a gap. */
export function raiseConfirmation(input: IntegerCommandInput, text: string): string {
  return boundQuestion(input, integerValue(text, input));
}

function boundQuestion(input: IntegerCommandInput, total: string): string {
  const wording = BOUND_WORDING[input.id];
  return wording ? wording[0](total) : `Set ${input.label} to ${total} and continue?`;
}

export function numericActionLabel(state: Pick<PanelState, "decisions">): string {
  const input = state.decisions.find((row) => row.kind === "integer")?.input;
  if (input?.id === "grant_recovery") return "Grant and continue";
  if (input?.id === "planning_review_call_limit") return "Set review allowance";
  return input?.id === "max_seconds" || !input ? "Set total and continue" : RAISE_LABEL;
}

export function numericRecoveryEffect(state: PanelState): string {
  const input = state.decisions.find((row) => row.kind === "integer")?.input;
  if (input?.id === "grant_recovery") return "This replenishes spent recovery slots under the existing fixed allowance. Recovery history is retained; excess is not banked. Other saved limits and model routes stay the same. It does not approve a plan.";
  if (input?.id === "planning_review_call_limit") return "This changes only the plan-review allowance. Used calls stay counted. Inspect the next action after saving; continuing can require a separate Resume. Other saved limits and model routes stay the same. It does not approve a plan.";
  return input ? boundEffect(input) : NUMERIC_RECOVERY_EFFECT;
}

/**
 * Ask the person to confirm a typed raise of the bound the view names now; false when declined.
 * An offer that changed since it was shown, or a number out of its bounds, is refused before asking.
 */
export async function confirmRaise(state: PanelState, decisionContext: string | undefined, value: string,
  onDialog?: (title: string) => void): Promise<boolean> {
  const input = state.boundRaise;
  if (!input || decisionContext !== state.decisionContext) {
    throw new ClientGap("The requested action changed; review it before continuing");
  }
  const title = raiseConfirmation(input, value);
  onDialog?.(title);
  return (await vscode.window.showWarningMessage(title, { modal: true, detail: boundEffect(input) }, RAISE_LABEL)) === RAISE_LABEL;
}

/** What raising a bound changes, and what it leaves alone. */
export function boundEffect(input: IntegerCommandInput): string {
  if (input.id === "max_seconds") return NUMERIC_RECOVERY_EFFECT;
  const noun = BOUND_WORDING[input.id]?.[1] ?? input.label;
  return `This changes only ${noun}, then resumes the run. Other saved limits and model routes stay the same. It does not approve a plan.`;
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
    actionLabel: null,
    actionEffect: null,
    stopped: null,
    commandText: "",
    personOnly: false,
    taking: false,
    working: false,
    pauseOffer: null,
    checkpoints: [],
    workingDiff: null,
    checkpointsBlocked: null,
    plan: null,
    boundRaise: null,
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

/**
 * Toolbar and composer icons: inline SVG in the house style of `media/autocode.svg`
 * (24×24, stroke currentColor). The webview CSP loads no fonts or images, so
 * inline markup is the only icon path; codicon names exist only in host UI.
 */
/** The chat's stage rows, cost line, welcome and closing card. Colors come from the VS Code theme. */
const METER_STYLES = `
  .chat-toolbar { flex-wrap: nowrap; }
  .chat-toolbar button.icon { flex: none; }
  #run-cost { flex: 1 1 auto; align-self: center; font-size: .75rem; opacity: .85; white-space: nowrap;
              overflow: hidden; text-overflow: ellipsis; min-width: 0; }
  #run-cost .session { opacity: .75; }
  #chat-models { flex-shrink: 0; margin: 0 6px 6px; border: 1px solid var(--vscode-panel-border); border-radius: 6px;
                 padding: 4px 8px; font-size: .75rem; max-height: 40vh; overflow-y: auto; }
  #chat-models[hidden] { display: none; }
  #chat-models summary { cursor: pointer; }
  .chat-model { padding: 3px 0; border-top: 1px solid var(--vscode-panel-border); }
  .chat-model:first-of-type { border-top: none; }
  .chat-model .head { display: flex; justify-content: space-between; gap: 6px; }
  .chat-model .now { opacity: .75; overflow-wrap: anywhere; }
  .chat-model.picked .now { opacity: 1; color: var(--vscode-textLink-foreground); }
  .chat-model.checking .now { opacity: 1; font-style: italic; }
  .chat-model .pick { display: flex; gap: 4px; margin-top: 2px; }
  /* Not the form-wide width: 100%, which left the level the whole row and squeezed the model dropdown to nothing. */
  .chat-model select { min-width: 0; flex: 1 1 0; width: auto; margin-top: 0; max-width: 100%; background: var(--vscode-dropdown-background);
                       color: var(--vscode-dropdown-foreground); border: 1px solid var(--vscode-dropdown-border); font-size: .72rem; }
  .chat-model select.level { flex: 0 0 auto; width: auto; }
  #chat-models .chat-models-error { color: var(--vscode-errorForeground); margin: 2px 0; }
  #chat-place { flex-shrink: 0; margin: 0 6px 6px; border: 1px solid var(--vscode-panel-border); border-radius: 6px;
                padding: 4px 8px; font-size: .75rem; }
  #chat-place[hidden] { display: none; }
  #chat-place summary { cursor: pointer; }
  #chat-place-summary { opacity: .8; }
  .chat-place-option { display: grid; grid-template-columns: auto 1fr; column-gap: 6px; padding: 3px 0; align-items: start; }
  .chat-place-option .does { grid-column: 2; opacity: .75; }
  .chat-place-option select { grid-column: 2; margin-top: 2px; max-width: 100%; background: var(--vscode-dropdown-background);
                              color: var(--vscode-dropdown-foreground); border: 1px solid var(--vscode-dropdown-border); font-size: .72rem; }
  #chat-place .chat-place-warning { color: var(--vscode-editorWarning-foreground, #cc8800); margin: 2px 0; }
  #chat-place .chat-place-note { opacity: .75; margin: 2px 0; }
  .stages-title { font-weight: 600; text-transform: uppercase; letter-spacing: .05em; opacity: .75; font-size: .7rem; }
  button.link { background: none; border: none; padding: 0; color: var(--vscode-textLink-foreground); font-size: .72rem; }
  button.link[hidden] { display: none; }
  .struggling { border-left: 3px solid var(--vscode-editorWarning-foreground, #cc8800); padding: 3px 8px; margin: 4px 0;
                color: var(--vscode-editorWarning-foreground, #cc8800); font-size: .85rem; }
  .chat-tab .dot.needs { color: var(--vscode-editorWarning-foreground, #cc8800); }
  .chat-tab .dot.finished { color: var(--vscode-charts-green, #3a7d3a); }
  .welcome { padding: 4px 2px; font-size: .88rem; }
  .welcome p { margin: 4px 0; opacity: .75; }
  .finish .result { font-weight: 600; margin-bottom: 6px; }
  .finish .facts { display: grid; grid-template-columns: max-content 1fr; gap: 3px 10px; font-size: .84rem; margin-bottom: 8px; }
  .finish .facts span:nth-child(odd) { opacity: .7; }
  .finish .files { overflow-wrap: anywhere; }
  #run-header { flex-shrink: 0; margin: 4px 6px 2px; padding: 5px 8px; border: 1px solid var(--vscode-panel-border);
                border-radius: 6px; font-size: .78rem; background: var(--vscode-sideBar-background, transparent); }
  #run-header[hidden], #header-details[hidden] { display: none; }
  .sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
  /* The stage table: one aligned row per stage, the model cut short before a number is. */
  #pipeline { width: 100%; border-collapse: collapse; margin: 4px 0 2px; font-variant-numeric: tabular-nums; }
  #pipeline[hidden] { display: none; }
  #pipeline th { font-weight: 600; text-transform: uppercase; letter-spacing: .05em; opacity: .6; font-size: .62rem;
                 text-align: left; padding: 0 4px 2px; white-space: nowrap; }
  #pipeline td { padding: 1px 4px; white-space: nowrap; vertical-align: baseline; }
  #pipeline .num { text-align: right; }
  #pipeline td.mark { width: 1.6ch; padding-right: 2px; text-align: center; }
  #pipeline .model { width: 100%; max-width: 0; overflow: hidden; text-overflow: ellipsis; }
  #pipeline td.model, #pipeline .sub { opacity: .75; }
  #pipeline .sub { font-size: .7rem; overflow: hidden; text-overflow: ellipsis; }
  #pipeline .sub:empty { display: none; }
  #pipeline .runs { margin-left: 5px; padding: 0 5px; border-radius: 8px; font-size: .64rem; font-weight: 400;
                    background: var(--vscode-badge-background); color: var(--vscode-badge-foreground); }
  #pipeline .runs.failed { background: transparent; color: var(--vscode-editorWarning-foreground, #cc8800);
                           border: 1px solid var(--vscode-editorWarning-foreground, #cc8800); }
  #pipeline tr.retrying .runs { background: var(--vscode-editorWarning-foreground, #cc8800); color: var(--vscode-editor-background, #fff); }
  #pipeline tbody tr.ran td.mark { color: var(--vscode-charts-green, #3a7d3a); }
  #pipeline tbody tr.working td { background: var(--vscode-list-inactiveSelectionBackground, rgba(128, 128, 128, .18)); }
  #pipeline tbody tr.working td:first-child { border-radius: 4px 0 0 4px; }
  #pipeline tbody tr.working td:last-child { border-radius: 0 4px 4px 0; }
  #pipeline tbody tr.working td.mark { color: var(--vscode-charts-blue, #1a6fb0); }
  #pipeline tbody tr.retrying td.mark { color: var(--vscode-editorWarning-foreground, #cc8800); }
  #pipeline tbody tr.working td.name, #pipeline tbody tr.next td.name { font-weight: 600; }
  #pipeline tbody tr.pending, #pipeline tbody tr.not_run, #pipeline tbody tr.skipped { opacity: .55; }
  #pipeline tbody tr.skipped td.name { text-decoration: line-through; }
  #pipeline tfoot td { border-top: 1px solid var(--vscode-panel-border); padding-top: 2px; font-weight: 600; }
  /* A sidebar too narrow for a model column shows the model under the stage's name instead. */
  @media (min-width: 560px) { #pipeline .sub, #pipeline .later { display: none; } }
  @media (max-width: 559px) {
    #pipeline .model { display: none; }
    #pipeline td.later { text-align: right; opacity: .75; max-width: 0; overflow: hidden; text-overflow: ellipsis; }
    #pipeline td.name, #pipeline th.name { width: 100%; max-width: 0; overflow: hidden; text-overflow: ellipsis; }
  }
  /* Narrower still, the time and cost stay and the tokens go; Show details and the toolbar still have them. */
  @media (max-width: 340px) { #pipeline .tokens { display: none; } }
  #header-now { display: flex; gap: 6px; align-items: baseline; }
  #header-label { flex: none; font-size: .68rem; text-transform: uppercase; letter-spacing: .04em; padding: 0 5px; border-radius: 3px;
                  background: var(--vscode-badge-background); color: var(--vscode-badge-foreground); }
  #run-header.needs_you #header-label { background: var(--vscode-editorWarning-foreground, #cc8800); color: var(--vscode-editor-background, #fff); }
  #run-header.done #header-label { background: var(--vscode-charts-green, #3a7d3a); }
  #header-sentence { min-width: 0; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
  #header-facts { opacity: .75; margin-top: 2px; }
  #header-facts:empty { display: none; }
  #header-stage { margin-top: 2px; overflow-wrap: anywhere; opacity: .85; }
  #header-stage:empty { display: none; }
  #header-place { opacity: .8; margin-top: 2px; overflow-wrap: anywhere; }
  #header-place:empty { display: none; }
  #header-toggle { margin-top: 2px; }
  #header-details { max-height: 35vh; overflow-y: auto; margin-top: 4px; border-top: 1px solid var(--vscode-panel-border); padding-top: 4px; }
  .detail-section { margin: 4px 0 8px; }
  .detail-head { display: flex; gap: 8px; align-items: baseline; }
  .detail-title { font-weight: 600; text-transform: uppercase; letter-spacing: .05em; opacity: .75; font-size: .68rem; }
  .detail-summary { opacity: .85; }
  .detail-line { overflow-wrap: anywhere; padding: 1px 0; }
  .detail-line.sub { padding-left: 12px; opacity: .8; }
  .detail-line.sub2 { padding-left: 24px; }
`;

/**
 * A new chat's one welcome line. It was two paragraphs and three example requests on every new chat,
 * which a person who has used AutoCode once reads as noise (owner request 2026-11-11); the message box
 * already says what to type.
 */
export const WELCOME_TEXT = "Describe a change, or ask about this code. Changes are planned for your approval, then built and tested.";

const ICONS = {
  plus: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>`,
  clock: `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.25" /><path d="M12 7.5V12l3 2" /></svg>`,
  panel: `<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="4.5" width="17" height="15" rx="2" /><path d="M15 4.5v15" /></svg>`,
  plug: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3.5v4M15 3.5v4M6.5 7.5h11v3.5a5.5 5.5 0 0 1-11 0V7.5zM12 16.5v4" /></svg>`,
  route: `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="6" cy="6" r="2.25" /><circle cx="18" cy="18" r="2.25" /><path d="M8.25 6H14a3.5 3.5 0 0 1 0 7h-4a3.5 3.5 0 0 0 0 7h5.75" /></svg>`,
  chip: `<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="1.5" /><path d="M10 3.5v3.5M14 3.5v3.5M10 17v3.5M14 17v3.5M3.5 10H7M3.5 14H7M17 10h3.5M17 14h3.5" /></svg>`,
  tune: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h9M19 7h1M4 17h1M11 17h9" /><circle cx="16" cy="7" r="2.25" /><circle cx="8" cy="17" r="2.25" /></svg>`,
};

function iconButton(id: string, label: string, svg: string, disabled = false): string {
  return `<button type="button" id="${id}" class="icon" title="${label}" aria-label="${label}"${disabled ? ' disabled' : ''}>${svg}</button>`;
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
  .activity { margin: 6px 0; font-family: var(--vscode-editor-font-family, monospace); font-size: .82rem; }
  .activity-row { display: flex; gap: 6px; opacity: .85; }
  .activity-row.running { opacity: 1; }
  .activity-row.failed { color: var(--vscode-errorForeground); }
  .activity-row.thinking { opacity: .7; font-style: italic; }
  .activity-mark { width: 1ch; flex: none; }
  .live-log .log-agent { margin-top: 6px; font-family: var(--vscode-font-family); font-size: .76rem; font-weight: 600; opacity: .8; }
  .live-log .log-agent:first-child { margin-top: 0; }
  .live-log .activity-row > :last-child { min-width: 0; }
  .live-log .log-now { font-weight: 400; opacity: .85; }
  .live-log details summary { list-style: none; cursor: pointer; }
  .live-log details summary::-webkit-details-marker { display: none; }
  .live-log details summary::after { content: ' ▸'; opacity: .6; }
  .live-log details[open] summary::after { content: ' ▾'; }
  .live-log details .count { opacity: .75; }
  .live-log .fold-line { margin: 2px 0 2px 2px; padding-left: 6px; border-left: 2px solid var(--vscode-panel-border); white-space: pre-wrap; overflow-wrap: anywhere; }
  .live-log .fold-line.failed { color: var(--vscode-errorForeground); }
  /* Prose and the words that name a group read in the UI font; commands and paths stay monospace. */
  .live-log .activity-row[data-kind="message"], .live-log .activity-row[data-kind="thinking"], .live-log details summary { font-family: var(--vscode-font-family); }
  .live-log .thought .fold-line { font-style: normal; }
  pre { white-space: pre-wrap; word-break: break-all; font-size: .76rem; background: var(--vscode-textCodeBlock-background);
        padding: 8px; border-radius: 4px; }
  /* A saved-code or working diff can be thousands of lines: it scrolls inside the thread, never stretches it. */
  pre.diff { max-height: 40vh; overflow: auto; }
  .gap, .notice { border-left: 3px solid var(--vscode-inputValidation-warningBorder, #cc8800);
                  background: var(--vscode-inputValidation-warningBackground, rgba(204,136,0,.12));
                  padding: 8px 10px; margin: 12px 0; font-size: .84rem; }
  .notice { border-left-color: var(--vscode-textLink-foreground, #3794ff); background: transparent; }
  .actions { display: flex; gap: 8px; margin-top: 14px; }
  .muted { opacity: .65; }
  .range-hint { font-size: .76rem; color: var(--vscode-errorForeground); margin-top: 3px; }
  .range-hint:empty { display: none; }
  ${chat ? chatStyles : ""}
  ${chat ? "#composer .chip[hidden] { display: none; }" : ""}
  ${chat ? METER_STYLES : ""}
</style>
</head>
<body>
${chat ? `<nav class="chat-toolbar" aria-label="AutoCode chat tools"><span id="run-cost" role="status" aria-label="Run cost"></span>${iconButton("connections", "Connections", ICONS.plug)}${iconButton("history", "History", ICONS.clock)}${iconButton("details", "Run details", ICONS.panel, true)}${iconButton("new-chat", "New chat", ICONS.plus)}</nav><div id="chat-tabs" role="tablist" aria-label="Open chats"></div><section id="run-header" aria-label="Run overview" hidden><div id="header-now"><span id="header-label"></span><span id="header-sentence"></span></div><table id="pipeline" aria-label="Stages"><thead><tr><th scope="col"><span class="sr-only">State</span></th><th scope="col" class="name">Stage</th><th scope="col" class="model">Model</th><th scope="col" class="num">Time</th><th scope="col" class="num tokens">Tokens</th><th scope="col" class="num">Cost</th></tr></thead><tbody></tbody><tfoot></tfoot></table><div id="header-stage"></div><div id="header-place"></div><div id="header-facts"></div><button type="button" id="header-toggle" class="link" aria-expanded="false" aria-controls="header-details">Show details</button><div id="header-details" hidden></div></section><div id="thread" tabindex="0" aria-label="Conversation"><div id="thread-content"><div id="messages" role="log" aria-label="Messages"></div>` : ""}
<div id="app" class="muted">${chat ? "Describe what you want to build, fix, review, or discuss." : "Loading…"}</div>
${chat ? `</div></div><button id="latest" type="button" hidden>↓ Latest messages</button>
<details id="chat-place" aria-label="Where this chat works" hidden><summary><span class="stages-title">Where this chat works</span> <span id="chat-place-summary"></span></summary><div id="chat-place-body"></div></details>
<details id="chat-models" aria-label="This chat's models" open hidden><summary><span class="stages-title">This chat's models</span> <span id="chat-models-summary"></span></summary><div id="chat-models-body"></div></details>
<section id="telemetry" aria-label="Models and usage">
<details id="usage-details"><summary id="telemetry-summary"><span class="telemetry-heading">Models &amp; usage</span>
<span id="model-summary">Model not reported</span><span id="usage-summary">Tokens not reported · Cost not reported</span></summary>
<div id="telemetry-body"><div id="model-info"></div><div id="usage-breakdown"></div></div></details>
</section>
<form id="composer">
<label for="message" id="send-hint">Message AutoCode</label>
<textarea id="message" aria-label="Message AutoCode" aria-describedby="keyboard-hint" placeholder="Ask AutoCode to work on this project…"></textarea>
<div id="chat-error" role="alert"></div>
<div class="actions"><button type="button" id="engine-picker" class="chip" title="Engine for new chats" aria-label="Engine for new chats">${ICONS.tune}<span id="engine-name">opencode</span></button><button type="button" id="mode-picker" class="chip" title="Run mode for new chats" aria-label="Run mode for new chats" hidden>${ICONS.route}<span id="mode-name"></span></button><button type="button" id="models-picker" class="chip" title="This chat's models" aria-label="This chat's models" hidden>${ICONS.chip}<span id="models-name">Models</span></button><span id="keyboard-hint" class="keyboard-hint">Enter to send · Shift+Enter for newline</span>
<button type="button" id="pause-run" hidden>Pause</button><button type="submit" id="send-message" disabled>Send</button></div></form>` : ""}
<script>
const vscode = acquireVsCodeApi();
const app = document.getElementById('app');
const chatMode = ${chat};
const RAISE_LABEL = ${JSON.stringify(RAISE_LABEL)};
const WELCOME_TEXT = ${JSON.stringify(WELCOME_TEXT)};
const initialWebviewState = vscode.getState?.() || {};
// The header's details stay open or closed across reads, tabs and a recreated webview.
let headerOpen = initialWebviewState.headerOpen === true;
let chatEnvelope = null;
const chatBehavior = chatMode ? (${installChatBehavior.toString()})() : null;
let current = null;
let answers = Object.create(null);
let renderedState = '';
let checkpointCompare = Object.create(null);
let workingCompare = null;
const ACTIVITY_MARKS = { command: '$', edit: '±', tool: '·', thinking: '∴', message: '»' };
// The live agent log kept by entry id across reads (the terminal chat's LiveLog).
const liveLog = new Map();
// Folded log lines a person opened (a run of thoughts or of like tool calls), by their first entry's id.
const openThoughts = new Set();
let checkpointResult = null;
const tabScroll = Object.create(null);
// The tab strip's keyboard state: the tab keys in order and the selected one.
let tabKeys = [];
let activeTabKey = '';

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

/** The live log as display groups: a run of thoughts is one folded "Thinking" line, and a run of
 * tool calls with the same core-written summary ("Searched code") is one line that counts them. */
function logGroups(rows) {
  const groups = [];
  rows.forEach((row) => {
    const last = groups[groups.length - 1];
    const key = row.kind === 'thinking' ? 'thinking' : row.kind === 'tool' && row.summary ? 'tool:' + row.summary : null;
    if (last && key && last.key === key && last.agent === row.agent) last.rows.push(row);
    else groups.push({ key, agent: row.agent, kind: row.kind, rows: [row] });
  });
  return groups;
}

const MARK_NAMES = { command: 'Ran a command', edit: 'Changed files', tool: 'Used a tool', thinking: 'Reasoning', message: 'Said' };

function logMark(kind, failed) {
  const mark = el('span', 'activity-mark', failed ? '✗' : (ACTIVITY_MARKS[kind] || '·'));
  mark.title = failed ? 'Failed' : (MARK_NAMES[kind] || '');
  mark.setAttribute('aria-label', mark.title);
  return mark;
}

/** One display group of the live log as a line; a folded group opens on a click and stays open. */
function logLine(group) {
  const rows = group.rows;
  const failed = rows.some((row) => row.status === 'failed');
  const running = rows[rows.length - 1].status === 'running';
  const line = el('div', 'activity-row ' + (failed ? 'failed' : running ? 'running' : rows[rows.length - 1].status)
    + (group.kind === 'thinking' ? ' thinking' : ''));
  line.dataset.kind = group.kind;
  line.appendChild(logMark(group.kind, failed));
  const folded = group.kind === 'thinking' || rows.length > 1;
  if (!folded) {
    line.appendChild(el('span', null, rows[0].text));
    return line;
  }
  const streaming = (text) => text.endsWith(' [partial]');
  const fold = el('details', group.kind === 'thinking' ? 'thought' : 'tool-group');
  const label = group.kind === 'thinking'
    ? (rows.some((row) => streaming(row.text)) ? 'Thinking…' : 'Thought')
    : rows[0].summary;
  const summary = el('summary', null, label);
  if (rows.length > 1) summary.appendChild(el('span', 'count', ' ×' + rows.length));
  summary.title = group.kind === 'thinking' ? 'Show what it was thinking' : 'Show each one';
  fold.appendChild(summary);
  rows.forEach((row) => {
    // The core's streaming marker is shown as a tooltip, not as part of the thought.
    const item = el('div', 'fold-line', streaming(row.text) ? row.text.slice(0, -' [partial]'.length) : row.text);
    if (streaming(row.text)) item.title = 'Still arriving';
    if (row.status === 'failed') item.classList.add('failed');
    fold.appendChild(item);
  });
  const id = rows[0].id;
  fold.open = openThoughts.has(id);
  fold.addEventListener('toggle', () => {
    if (fold.open) openThoughts.add(id); else openThoughts.delete(id);
  });
  line.appendChild(fold);
  return line;
}

function renderChatProgress(container, state) {
  if (state.meter && state.meter.struggling) {
    const warn = el('div', 'struggling', '⚠ ' + state.meter.struggling);
    warn.id = 'struggling';
    warn.setAttribute('role', 'status');
    container.appendChild(warn);
  }
  const lines = state.chat && Array.isArray(state.chat.progress)
    ? state.chat.progress.filter((line) => typeof line === 'string' && line) : [];
  // Live agent log (chat.log): what each running agent thinks, says and does. Kept by id
  // across reads like the terminal chat's LiveLog, since the view holds only the newest.
  const logRows = state.chat && Array.isArray(state.chat.log) ? state.chat.log : [];
  for (const row of logRows) {
    if (!row || typeof row.id !== 'string' || typeof row.text !== 'string' || typeof row.agent !== 'string') continue;
    if (!(row.kind in ACTIVITY_MARKS)) continue;
    liveLog.set(row.id, { id: row.id, agent: row.agent, kind: row.kind, text: row.text, status: row.status || '',
      summary: typeof row.summary === 'string' ? row.summary : '' });
  }
  while (liveLog.size > 500) {
    const oldest = liveLog.keys().next().value;
    liveLog.delete(oldest);
    openThoughts.delete(oldest);
  }
  const groups = logGroups([...liveLog.values()].slice(-60)).slice(-10);
  // The log's own agent header says which stage works and what it does now, so the card does not
  // repeat the run's headline ("Requirements working") above it.
  const shown = groups.length ? lines.filter((line) => line !== state.headline) : lines;
  (shown.length || groups.length ? shown : ['Thinking…']).forEach((line) => container.appendChild(el('div', 'line', line)));
  if (groups.length) {
    // Supersedes the bare activity list: the log already carries those tool marks.
    const list = el('div', 'activity live-log');
    list.setAttribute('aria-label', 'Agent log');
    const now = Object.create(null);
    (state.chat && Array.isArray(state.chat.now) ? state.chat.now : []).forEach((row) => {
      if (row && typeof row.agent === 'string' && typeof row.text === 'string') now[row.agent] = row.text;
    });
    // One header per run of steps from the same agent; its newest one says what it does now.
    const lastHeader = Object.create(null);
    let previous = null;
    groups.forEach((group, index) => {
      if (group.agent !== previous) lastHeader[group.agent] = index;
      previous = group.agent;
    });
    previous = null;
    groups.forEach((group, index) => {
      if (group.agent !== previous) {
        previous = group.agent;
        const head = el('div', 'log-agent', group.agent);
        if (lastHeader[group.agent] === index && now[group.agent]) head.appendChild(el('span', 'log-now', ' · ' + now[group.agent]));
        list.appendChild(head);
      }
      list.appendChild(logLine(group));
    });
    container.appendChild(list);
  } else {
    // What the running step does now: core-written display text, never proof that it worked.
    const events = state.chat && Array.isArray(state.chat.activity)
      ? state.chat.activity.filter((row) => row && typeof row.text === 'string' && row.text).slice(-6) : [];
    if (events.length) {
      const list = el('div', 'activity');
      list.setAttribute('aria-label', 'What AutoCode is doing');
      events.forEach((row) => {
        const mark = row.status === 'failed' ? '✗' : (ACTIVITY_MARKS[row.kind] || '·');
        const line = el('div', 'activity-row ' + (row.status || ''));
        line.appendChild(el('span', 'activity-mark', mark));
        line.appendChild(el('span', null, row.text));
        list.appendChild(line);
      });
      container.appendChild(list);
    }
  }
  if (state.workingDiff) {
    const show = el('button', 'secondary', state.workingDiff.label);
    show.id = 'working-diff';
    show.disabled = Boolean(state.unreadable);
    show.addEventListener('click', () => vscode.postMessage({ type: 'working_diff',
      runKey: state.workspace + '\\n' + state.runDir }));
    container.appendChild(show);
  }
  if (!state.workingDiff) return;  // a diff of a step that has ended would be stale
  if (workingCompare && workingCompare.error) {
    container.appendChild(el('div', 'why', workingCompare.error));
  } else if (workingCompare) {
    const comparison = workingCompare.comparison || {};
    const files = (comparison.changed_files || []).length;
    const since = comparison.since === 'start' ? 'the run started' : 'the last saved step';
    container.appendChild(el('div', 'why', files + ' file(s) changed since ' + since + (comparison.truncated ? ' · diff truncated' : '')));
    const patch = el('pre', 'diff');
    patch.textContent = comparison.patch || 'The Builder has not changed any files yet.';
    container.appendChild(patch);
  }
}

/** A finished run's closing card: the result, what it checked and changed, and what it took. */
function renderFinish(state) {
  const finish = state.finish;
  const card = el('div', 'decision-card finish');
  card.id = 'finish-summary';
  card.appendChild(el('div', 'speaker', 'AutoCode · finished'));
  const body = el('div', 'message-content');
  body.appendChild(el('div', 'result', '✓ ' + finish.headline));
  const facts = el('div', 'facts');
  const fact = (label, value, cls) => { facts.appendChild(el('span', null, label)); facts.appendChild(el('span', cls || null, value)); };
  if (finish.requirements) fact('Requirements', finish.requirements.checked + ' of ' + finish.requirements.total + ' checked');
  if (finish.files.length) {
    const shown = finish.files.slice(0, 6).join(', ') + (finish.files.length > 6 ? ' and ' + (finish.files.length - 6) + ' more' : '');
    fact('Changed', finish.files.length + (finish.files.length === 1 ? ' file: ' : ' files: ') + shown, 'files');
  }
  if (finish.seconds !== null) fact('Stage time', formatDuration(finish.seconds));
  if (state.meter && state.meter.total) fact('Cost', state.meter.total);
  if (facts.childNodes.length) body.appendChild(facts);
  const actions = el('div', 'actions');
  if (state.checkpoints && state.checkpoints.length) {
    const show = el('button', null, 'Show changes');
    show.id = 'finish-show-changes';
    show.addEventListener('click', () => {
      const target = document.getElementById('saved-changes');
      if (target && target.scrollIntoView) target.scrollIntoView({ block: 'start', behavior: 'smooth' });
    });
    actions.appendChild(show);
  }
  const details = el('button', 'secondary', 'Run details');
  details.id = 'finish-details';
  details.addEventListener('click', () => vscode.postMessage({ type: 'details' }));
  actions.appendChild(details);
  body.appendChild(actions);
  card.appendChild(body);
  app.appendChild(card);
}

function formatDuration(seconds) {
  const whole = Math.floor(seconds);
  if (whole < 60) return whole + 's';
  if (whole < 3600) return Math.floor(whole / 60) + 'm' + String(whole % 60).padStart(2, '0') + 's';
  return Math.floor(whole / 3600) + 'h' + String(Math.floor((whole % 3600) / 60)).padStart(2, '0') + 'm';
}

/** A new chat's welcome: one line on what AutoCode does. */
function renderWelcome() {
  const box = el('div', 'welcome');
  box.id = 'welcome';
  box.appendChild(el('p', null, WELCOME_TEXT));
  app.appendChild(box);
}

/** The run header at the top of the chat: the pipeline, where the run is, then its details on request. */
function renderHeader(data) {
  const host = document.getElementById('run-header');
  if (!host) return;
  const header = data.state && data.state.header;
  host.hidden = !header;
  if (!header) { host.dataset.content = ''; return; }
  const key = JSON.stringify([header, headerOpen]);
  if (host.dataset.content === key) return;
  host.dataset.content = key;
  host.className = header.state || '';
  const pipeline = document.getElementById('pipeline');
  pipeline.hidden = !header.pipeline.length;
  const body = pipeline.querySelector('tbody');
  body.textContent = '';
  header.pipeline.forEach((step) => {
    // A working stage after a failed attempt is retrying: its badge and mark say so.
    const item = el('tr', step.state + (step.state === 'working' && step.why ? ' retrying' : ''));
    item.dataset.job = step.label;
    item.appendChild(el('td', 'mark', step.state === 'working' && step.why ? '↻' : step.mark));
    const route = step.state === 'skipped' ? 'skipped' : [step.model, step.level].filter(Boolean).join(' ');
    const name = el('td', 'name', step.label);
    if (step.attempts > 1) {
      // A small count; the words are its tooltip and Show details. Amber when an attempt failed.
      const badge = el('span', 'runs' + (step.failed ? ' failed' : ''), '×' + step.attempts);
      badge.title = step.runs;
      badge.setAttribute('aria-label', step.runs);
      name.appendChild(badge);
    }
    // A narrow sidebar has no model column: a stage that used something shows its model under its
    // name, and one that has not (still to come or skipped) beside it, where its figures would be.
    const idle = !step.time && !step.tokens && !step.cost;
    name.appendChild(el('div', 'sub', idle ? '' : route));
    item.appendChild(name);
    item.appendChild(el('td', 'model', route));
    if (idle) {
      const later = el('td', 'later', route);
      later.colSpan = 3;
      item.appendChild(later);
    } else {
      item.appendChild(el('td', 'num time', step.time));
      item.appendChild(el('td', 'num tokens', step.tokens));
      item.appendChild(el('td', 'num cost', step.cost));
    }
    // The whole route and why a retry is running, for a reader who hovers; Show details says the same.
    item.title = [step.label, step.state === 'skipped' ? step.detail : step.route, step.tokens ? step.tokens + ' tokens' : '',
      step.runs, step.why ? 'last one ' + step.why : ''].filter(Boolean).join(' · ');
    if (step.state === 'working') item.setAttribute('aria-current', 'step');
    body.appendChild(item);
  });
  const foot = pipeline.querySelector('tfoot');
  foot.textContent = '';
  if (header.total) {
    const total = el('tr', 'total');
    total.appendChild(el('td', 'mark', ''));
    total.appendChild(el('td', 'name', 'Total'));
    total.appendChild(el('td', 'model', ''));
    total.appendChild(el('td', 'num time', header.total.time));
    total.appendChild(el('td', 'num tokens', header.total.tokens));
    total.appendChild(el('td', 'num cost', header.total.cost));
    foot.appendChild(total);
  }
  document.getElementById('header-label').textContent = header.label;
  document.getElementById('header-sentence').textContent = header.sentence;
  document.getElementById('header-stage').textContent = header.current || '';
  const place = document.getElementById('header-place');
  place.textContent = header.place;
  place.title = (header.sections.find((section) => section.id === 'worktree') || { lines: [] }).lines.join('\\n');
  document.getElementById('header-facts').textContent = header.facts.join(' · ');
  const toggle = document.getElementById('header-toggle');
  toggle.textContent = headerOpen ? 'Hide details' : 'Show details';
  toggle.setAttribute('aria-expanded', headerOpen ? 'true' : 'false');
  const details = document.getElementById('header-details');
  details.hidden = !headerOpen;
  details.textContent = '';
  if (!headerOpen) return;
  header.sections.forEach((section) => {
    const box = el('div', 'detail-section');
    box.dataset.section = section.id;
    const head = el('div', 'detail-head');
    head.appendChild(el('span', 'detail-title', section.title));
    if (section.summary) head.appendChild(el('span', 'detail-summary', section.summary));
    box.appendChild(head);
    section.lines.forEach((line) => {
      // Two spaces belong to the line above; four to that line's own sub-line.
      const depth = line.startsWith('    ') ? 2 : line.startsWith('  ') ? 1 : 0;
      box.appendChild(el('div', 'detail-line' + (depth ? ' sub' : '') + (depth === 2 ? ' sub2' : ''), line.slice(depth * 2)));
    });
    details.appendChild(box);
  });
}

/** The cost line in the toolbar; each stage's model and usage is the run header's stage table. */
function renderMeter(data) {
  const meter = data.state && data.state.meter;
  const cost = document.getElementById('run-cost');
  const costKey = JSON.stringify([meter && meter.total, data.session]);
  if (cost && cost.dataset.content !== costKey) {
    cost.dataset.content = costKey;
    cost.textContent = '';
    if (meter && meter.total) cost.appendChild(el('span', null, meter.total));
    if (data.session) cost.appendChild(el('span', 'session', (meter && meter.total ? ' · ' : '') + 'all chats ' + data.session));
    cost.title = [meter && meter.total ? 'This run: ' + meter.total : '', data.session ? 'All open chats: ' + data.session : '']
      .filter(Boolean).join(' · ');
  }
}

function renderStopped(stopped) {
  app.appendChild(el('h2', null, stopped.title));
  app.appendChild(el('div', 'line', stopped.explanation));
  if (stopped.retained) app.appendChild(el('div', 'why', stopped.retained));
  if (stopped.newConversation) {
    const fresh = el('button', null, stopped.newConversation);
    fresh.id = 'start-new-conversation';
    fresh.addEventListener('click', () => vscode.postMessage({ type: 'new' }));
    app.appendChild(fresh);
  }
}

function render(state) {
  const chatSlice = chatMode ? [
    state.status, state.needKind, state.pause, state.gap, state.canApprove, state.token,
    state.taking, state.working, state.plan, state.criteria, state.decisions, state.decisionContext,
    state.actionLabel, state.actionEffect, state.stopped, state.unreadable, state.confirming,
    state.chat && [state.chat.phase, state.chat.notice, state.chat.progress, state.chat.activity, state.chat.log, state.chat.now, state.chat.review, state.chat.input && state.chat.input.kind],
    state.workingDiff, state.boundRaise, state.done, state.finish,
    state.meter && state.meter.struggling, state.checkpoints,
  ] : state;
  const serialized = JSON.stringify(chatSlice);
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
  if (chatMode && state.unreadable) {
    app.appendChild(el('div', 'notice', 'I couldn’t read the latest status, so actions are paused until it reads again.'));
  }

  if (chatMode && state.stopped) {
    renderStopped(state.stopped);
    return;
  }

  if (chatMode) {
    const card = el('div', 'decision-card');
    const cardContent = el('div', 'message-content');
    card.appendChild(el('div', 'speaker', 'AutoCode'));
    card.appendChild(cardContent);
    const strip = el('div', 'actions action-strip');
    strip.id = 'action-strip';

    if (state.chat && state.chat.input && state.chat.input.kind === 'resolver_response') {
      cardContent.appendChild(el('div', 'notice', 'Reply with what changed or how you want to proceed. Recovery is reviewed before the run can continue.'));
      if (state.boundRaise) renderBoundRaise(cardContent, state);
      app.appendChild(card);
      return;
    }

    if (state.chat && state.chat.phase === 'requirements' && !state.gap
        && (!state.pause || state.needKind === 'answer')
        && (state.chat.review || state.needKind === 'continue')) {
      if (state.chat.review) {
        const review = state.chat.review;
        // The thread already holds the Requirements question (docs/task-run.md: its id starts with
        // requirements-question-), and this summary is that same text: a second copy here showed
        // the whole understanding twice, the card's as a raw block. The card keeps it only when
        // the thread does not show it.
        const asked = (state.chat.messages || []).some((row) => !row.streaming
          && String(row.id || '').indexOf('requirements-question-') === 0);
        if (review.summary && !asked) cardContent.appendChild(el('pre', 'understanding', review.summary));
        const approve = el('button', null, review.label);
        approve.id = 'take-action';
        approve.disabled = state.taking || state.working || Boolean(state.unreadable);
        approve.addEventListener('click', () => vscode.postMessage({ type: 'take',
          answers: { [review.question_id]: review.answer }, token: null,
          runKey: state.workspace + '\\n' + state.runDir, decisionContext: state.decisionContext }));
        strip.appendChild(el('span', 'action-prompt', state.confirming
          ? state.confirming + ' — answer the dialog that opened'
          : 'AutoCode is waiting for you'));
        strip.appendChild(approve);
      } else if (state.needKind === 'continue' && state.working) {
        renderChatProgress(cardContent, state);
      } else if (state.needKind === 'continue') {
        const resume = el('button', null, 'Continue');
        resume.id = 'take-action';
        resume.disabled = Boolean(state.unreadable);
        resume.addEventListener('click', () => vscode.postMessage({ type: 'take', answers: {}, token: null,
          runKey: state.workspace + '\\n' + state.runDir, decisionContext: state.decisionContext }));
        strip.appendChild(el('span', 'action-prompt', 'AutoCode is waiting for you'));
        strip.appendChild(resume);
      }
      if (cardContent.childNodes.length > 0) app.appendChild(card);
      if (strip.childNodes.length > 0) app.appendChild(strip);
      return;
    }

    if (state.chat && state.chat.notice && state.chat.notice !== 'Working on your request…') {
      cardContent.appendChild(el('div', 'notice', state.chat.notice));
    } else if (state.pause) {
      cardContent.appendChild(el('div', 'notice', 'I couldn’t finish this step. Open Run details to see what needs attention.'));
    }

    if (state.criteria.length) {
      cardContent.appendChild(el('h2', null, 'Criteria to accept'));
      state.criteria.forEach((row) => cardContent.appendChild(el('div', 'row', row.criterion)));
    }

    if (state.plan && state.plan.delivery) {
      cardContent.appendChild(el('div', 'row', state.plan.delivery));
    }

    if (state.plan) {
      cardContent.appendChild(el('h2', null, 'Plan to approve'));
      if (state.plan.milestones.length) {
        cardContent.appendChild(el('h2', null, 'Milestones'));
        state.plan.milestones.forEach((milestone) => {
          const box = el('div', 'decision');
          box.appendChild(el('div', 'q', milestone.id + ': ' + milestone.objective));
          if (milestone.acceptance_criteria.length) {
            const criteria = el('div', 'why', 'Criteria: ' + milestone.acceptance_criteria.join(', '));
            box.appendChild(criteria);
          }
          cardContent.appendChild(box);
        });
      }
      if (state.plan.flow.length) {
        cardContent.appendChild(el('h2', null, 'End-to-end flow'));
        state.plan.flow.forEach((step) => cardContent.appendChild(el('div', 'row', step)));
      }
      state.plan.criteria.forEach((criterion) => {
        const item = el('div', 'row');
        item.appendChild(el('span', null, criterion.criterion));
        if (criterion.human_review) item.appendChild(el('span', 'why', 'You check this one yourself'));
        cardContent.appendChild(item);
      });
      if (state.plan.constraints.length) {
        cardContent.appendChild(el('h2', null, 'Constraints'));
        state.plan.constraints.forEach((text) => cardContent.appendChild(el('div', 'row', text)));
      }
      if (state.plan.boundaries.length) {
        cardContent.appendChild(el('h2', null, 'What it may do'));
        state.plan.boundaries.forEach((text) => cardContent.appendChild(el('div', 'row', text)));
      }
      if (state.plan.exclusions.length) {
        cardContent.appendChild(el('h2', null, 'Not covered'));
        state.plan.exclusions.forEach((text) => cardContent.appendChild(el('div', 'row', text)));
      }
    }

    if (state.gap) {
      cardContent.appendChild(el('h2', null, 'No action the client can take'));
      const box = el('div', 'gap');
      box.appendChild(el('div', null,
        'AutoCode named no action this client is allowed to take. That is a gap in the CLI or the '
        + 'status view (#1012), not something the extension should work around.'));
      box.appendChild(el('pre', null, state.gap));
      cardContent.appendChild(box);
    } else if (!state.done && state.needKind === 'continue' && state.working) {
      renderChatProgress(cardContent, state);
    } else if (!state.done) {
      if (state.decisions.length > 0) {
        const form = el('div');
        state.decisions.forEach((decision) => {
          const box = el('div', 'decision');
          box.appendChild(el('div', 'q', decision.text));
          if (decision.input) box.appendChild(el('div', 'why', numericHelp(decision.input)));
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
            // The core's proposed default is preselected: taking it is one click, not a choice plus a click.
            if (decision.suggested && decision.options.indexOf(decision.suggested) >= 0 && !answers[name]) {
              answers[name] = decision.suggested;
            }
          } else {
            input = document.createElement('input');
            input.type = 'text';
            if (decision.kind === 'integer') { input.inputMode = 'numeric'; input.pattern = '[0-9]*'; input.setAttribute('aria-label', decision.text); }
            if (decision.suggested) input.placeholder = decision.suggested;
          }
          input.dataset.question = name;
          if (answers[name]) input.value = answers[name];
          // A number the button will not take says why beside the field, as it is typed.
          const hint = decision.input ? el('div', 'range-hint') : null;
          const save = () => {
            answers[name] = input.value;
            if (hint) hint.textContent = integerHint(input.value, decision.input);
            updateActionButton();
          };
          if (hint) save();
          input.addEventListener('change', save);
          input.addEventListener('input', save);
          input.addEventListener('keydown', enterTakes);
          box.appendChild(input);
          if (hint) box.appendChild(hint);
          form.appendChild(box);
        });
        cardContent.appendChild(form);
      }
      if (state.boundRaise) renderBoundRaise(cardContent, state);

      var approving = state.needKind === 'approve_plan';
      var label = (state.taking || state.working) ? 'Working…' : (approving ? 'Approve and build'
        : (state.needKind === 'review' ? 'Accept the reviewed criteria' : (state.decisions.some((row) => row.kind === 'integer') ? state.numericActionLabel : (state.decisions.length ? 'Send answers and continue' : state.actionLabel || 'Continue'))));
      if (state.actionEffect) cardContent.appendChild(el('div', 'why', state.actionEffect));
      const take = el('button', null, label);
      take.id = 'take-action';
      take.addEventListener('click', () => {
        vscode.postMessage({ type: 'take', answers: { ...answers }, token: state.token || null,
          runKey: state.workspace + '\\n' + state.runDir, decisionContext: state.decisionContext });
      });
      strip.appendChild(el('span', 'action-prompt', state.confirming
        ? state.confirming + ' — answer the dialog that opened'
        : 'AutoCode is waiting for you'));
      strip.appendChild(take);
      const again = el('button', 'secondary', 'Refresh');
      again.addEventListener('click', () => vscode.postMessage({ type: 'refresh' }));
      strip.appendChild(again);

      if (!state.canApprove && state.needKind === 'approve_plan') {
        cardContent.appendChild(el('div', 'notice', 'The plan has not loaded here yet, so there is nothing to approve. Refresh to load it.'));
      }
    }

    if (cardContent.childNodes.length > 0) app.appendChild(card);
    if (strip.childNodes.length > 0) app.appendChild(strip);
    if (state.done && state.finish) renderFinish(state);
    if (chatMode && state.checkpoints && state.checkpoints.length) {
      const savedHeading = el('h2', null, 'Saved code changes');
      savedHeading.id = 'saved-changes';
      app.appendChild(savedHeading);
      if (state.checkpointsBlocked) app.appendChild(el('div', 'why', state.checkpointsBlocked));
      if (state.taking && state.checkpoints.some((cp) => cp.available && cp.actions.length)) {
        app.appendChild(el('div', 'why', 'Saved-code actions are available after the current AutoCode action finishes.'));
      }
      state.checkpoints.forEach((cp) => {
        const box = el('div', 'decision');
        const title = cp.label + (cp.verdict ? ' · ' + cp.verdict : '');
        box.appendChild(el('div', 'q', title));
        if (cp.available) {
          box.appendChild(el('div', 'why', cp.files + ' file(s) changed' + (cp.at ? ' · ' + new Date(cp.at).toLocaleDateString() : '')));
          cp.actions.forEach((action) => {
            const button = el('button', action.id === 'restore' ? null : 'secondary', action.label);
            button.dataset.checkpointAction = action.id;
            button.dataset.checkpointId = cp.id;
            button.disabled = Boolean(state.taking || state.unreadable);
            button.addEventListener('click', () => vscode.postMessage({ type: 'checkpoint', action: action.id,
              checkpoint: cp.id, runKey: state.workspace + '\\n' + state.runDir }));
            box.appendChild(button);
          });
        } else {
          box.appendChild(el('div', 'why', cp.reason || 'The saved source for this step is not retained.'));
        }
        const compare = checkpointCompare[cp.id];
        if (compare && compare.error) {
          box.appendChild(el('div', 'why', 'Comparison failed: ' + compare.error));
        } else if (compare) {
          const comparison = compare.comparison || {};
          const files = (comparison.changed_files || []).length;
          const step = compare.kind === 'step';
          box.appendChild(el('div', 'why', step
            ? files + ' file(s) changed in this step' + (comparison.truncated ? ' · diff truncated' : '')
            : files + ' file(s) differ from this checkpoint' + (comparison.truncated ? ' · diff truncated' : '')));
          const patch = el('pre', 'diff');
          patch.textContent = comparison.patch || (step ? 'This step changed no files.' : 'The files match this checkpoint.');
          box.appendChild(patch);
        }
        if (checkpointResult && checkpointResult.checkpoint === cp.id && checkpointResult.result) {
          const result = checkpointResult.result;
          box.appendChild(el('div', 'notice',
            'Restored on a new branch: ' + (result.branch || 'created') + '. The original branch and later work stay intact.'));
          const open = el('button', 'secondary', 'Open restored candidate');
          open.addEventListener('click', () => vscode.postMessage({ type: 'checkpoint_open',
            workspace: result.workspace, runDir: result.run_dir }));
          box.appendChild(open);
        }
        app.appendChild(box);
      });
    }
    updateActionButton();
    if (focusedQuestion) {
      const input = [...document.querySelectorAll('[data-question]')].find((node) => node.dataset.question === focusedQuestion);
      if (input) {
        input.focus();
        if (selection && input.tagName === 'INPUT') input.setSelectionRange(...selection);
      }
    }
    return;
  }

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
  if (state.stopped) renderStopped(state.stopped);

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

  if (state.plan && state.plan.delivery) {
    app.appendChild(el('div', 'row', state.plan.delivery));
  }

  if (state.plan) {
    app.appendChild(el('h2', null, 'Plan to approve · revision ' + (state.plan.revision === null ? '?' : state.plan.revision)));
    app.appendChild(el('div', 'why',
      state.plan.criteria.length + ' acceptance criteria  ·  '
      + state.plan.constraints.length + ' constraints  ·  '
      + state.plan.boundaries.length + ' permission boundaries'));
    if (state.plan.milestones.length) {
      app.appendChild(el('h2', null, 'Milestones'));
      state.plan.milestones.forEach((milestone) => {
        const box = el('div', 'decision');
        box.appendChild(el('div', 'q', milestone.id + ': ' + milestone.objective));
        if (milestone.acceptance_criteria.length) {
          const criteria = el('div', 'why', 'Criteria: ' + milestone.acceptance_criteria.join(', '));
          box.appendChild(criteria);
        }
        app.appendChild(box);
      });
    }
    if (state.plan.flow.length) {
      app.appendChild(el('h2', null, 'End-to-end flow'));
      state.plan.flow.forEach((step) => app.appendChild(el('div', 'row', step)));
    }
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
    if (state.plan.exclusions.length) {
      app.appendChild(el('h2', null, 'Not covered'));
      state.plan.exclusions.forEach((text) => app.appendChild(el('div', 'row', text)));
    }
  }

  if (state.changeset) renderChangeset(state.changeset, state);

  if (!state.done && !state.stopped) {
    app.appendChild(el('h2', null, 'Waiting for'));
    app.appendChild(el('div', 'line', state.needsYou || state.needSummary || state.needKind || '—'));
    if (state.needReason) app.appendChild(el('div', 'why', state.needReason));
  }

  if (state.chat && state.chat.input && state.chat.input.kind === 'resolver_response' && !state.stopped) {
    app.appendChild(el('div', 'notice', 'The conversation can accept a recovery reply. Sending information does not authorize a retry.'));
    const chat = el('button', null, 'Open conversation');
    chat.id = 'open-recovery-conversation';
    chat.addEventListener('click', () => vscode.postMessage({ type: 'chat' }));
    app.appendChild(chat);
    if (state.boundRaise) renderBoundRaise(app, state);
  } else if (state.gap) {
    app.appendChild(el('h2', null, 'No action the client can take'));
    const box = el('div', 'gap');
    box.appendChild(el('div', null,
      'AutoCode named no action this client is allowed to take. That is a gap in the CLI or the '
      + 'status view (#1012), not something the extension should work around.'));
    box.appendChild(el('pre', null, state.gap));
    app.appendChild(box);
  } else if (!state.done && !state.stopped) {
    const form = el('div');
    state.decisions.forEach((decision) => {
      const box = el('div', 'decision');
      box.appendChild(el('div', 'q', decision.text));
      if (decision.input) box.appendChild(el('div', 'why', numericHelp(decision.input)));
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
        // The core's proposed default is preselected: taking it is one click, not a choice plus a click.
        if (decision.suggested && decision.options.indexOf(decision.suggested) >= 0 && !answers[name]) {
          answers[name] = decision.suggested;
        }
      } else {
        input = document.createElement('input');
        input.type = 'text';
        if (decision.kind === 'integer') { input.inputMode = 'numeric'; input.pattern = '[0-9]*'; input.setAttribute('aria-label', decision.text); }
        if (decision.suggested) input.placeholder = decision.suggested;
      }
      input.dataset.question = name;
      if (answers[name]) input.value = answers[name];
      // A number the button will not take says why beside the field, as it is typed.
      const hint = decision.input ? el('div', 'range-hint') : null;
      const save = () => {
        answers[name] = input.value;
        if (hint) hint.textContent = integerHint(input.value, decision.input);
        updateActionButton();
      };
      if (hint) save();
      input.addEventListener('change', save);
      input.addEventListener('input', save);
      input.addEventListener('keydown', enterTakes);
      box.appendChild(input);
      if (hint) box.appendChild(hint);
      form.appendChild(box);
    });
    app.appendChild(form);

    const actions = el('div', 'actions');
    var approving = state.needKind === 'approve_plan';
    var label = (state.taking || state.working) ? 'Working…' : (approving ? 'Approve this plan'
      : (state.needKind === 'review' ? 'Accept the reviewed criteria' : (state.decisions.some((row) => row.kind === 'integer') ? state.numericActionLabel : state.actionLabel || 'Take the named action')));
    if (state.actionEffect) actions.appendChild(el('div', 'why', state.actionEffect));
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
    if (state.boundRaise) renderBoundRaise(app, state);

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
  if (focusedQuestion) {
    const input = [...document.querySelectorAll('[data-question]')].find((node) => node.dataset.question === focusedQuestion);
    if (input) {
      input.focus();
      if (selection && input.tagName === 'INPUT') input.setSelectionRange(...selection);
    }
  }
}

function renderChangeset(set, state) {
  app.appendChild(el('h2', null, set.id ? 'Change set ' + set.id : 'Change set'));
  set.runs.forEach((row) => app.appendChild(el('div', 'row',
    row.target + ' (' + (row.role === 'source' ? 'source run' : 'linked run') + '): ' + row.status + (row.needs ? ', needs ' + row.needs : ''))));
  set.plans.forEach((entry) => {
    app.appendChild(el('div', 'why', entry.target + ': plan revision ' + (entry.plan.revision === null ? '?' : entry.plan.revision)));
    if (entry.plan.milestones.length) {
      app.appendChild(el('h3', null, 'Milestones'));
      entry.plan.milestones.forEach((milestone) => {
        app.appendChild(el('div', 'row', milestone.id + ': ' + milestone.objective));
        if (milestone.acceptance_criteria.length) {
          app.appendChild(el('div', 'why', 'Criteria: ' + milestone.acceptance_criteria.join(', ')));
        }
      });
    }
    if (entry.plan.flow.length) {
      app.appendChild(el('h3', null, 'End-to-end flow'));
      entry.plan.flow.forEach((step) => app.appendChild(el('div', 'row', step)));
    }
    entry.plan.criteria.forEach((criterion) => {
      app.appendChild(el('div', 'row', criterion.id + ': ' + criterion.criterion));
      app.appendChild(el('pre', null, criterion.verification_method));
      if (criterion.human_review) app.appendChild(el('div', 'why', 'You check this one yourself'));
    });
    for (const [title, rows] of [['Constraints', entry.plan.constraints], ['What it may do', entry.plan.boundaries], ['Not covered', entry.plan.exclusions]]) {
      if (rows.length) {
        app.appendChild(el('h3', null, title));
        rows.forEach((text) => app.appendChild(el('div', 'row', text)));
      }
    }
    if (entry.plan.delivery) app.appendChild(el('div', 'row', entry.plan.delivery));
  });
  if (set.note) app.appendChild(el('div', 'notice', set.note));
  if (set.offer) {
    app.appendChild(el('div', 'why', set.offer.effect));
    const take = el('button', null, set.offer.label);
    take.id = 'changeset-action';
    take.disabled = state.taking || Boolean(state.unreadable);
    take.addEventListener('click', () => vscode.postMessage({ type: 'changeset', key: set.offer.key, token: set.token }));
    app.appendChild(take);
  }
}

/** Why a typed number is not takeable yet: the sentence a disabled button owed beside its field. */
function integerHint(value, input) {
  if (!value) return '';
  const number = /^[0-9]+$/.test(value) ? Number(value) : NaN;
  if (!Number.isSafeInteger(number)) return 'Enter a whole number';
  if (number < input.minimum) return 'Enter at least ' + input.minimum;
  if (number > input.maximum) return 'Enter at most ' + input.maximum;
  return '';
}

/** Enter in an answer field takes the action its own button names, and only while that button could. */
function enterTakes(event) {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return;
  const target = event.target;
  if (!target || target.tagName !== 'INPUT') return;
  const button = String(target.dataset.question || '').indexOf('raise:') === 0
    ? document.getElementById('raise-bound') : document.getElementById('take-action');
  if (!button || button.disabled) return;
  event.preventDefault();
  button.click();
}

function updateActionButton() {
  const take = document.getElementById('take-action');
  if (!take || !current) return;
  const missing = current.decisions.some((decision) => {
    const value = answers[decision.questionId] || '';
    if (decision.kind !== 'integer') return !value.trim();
    const number = /^[0-9]+$/.test(value) ? Number(value) : NaN;
    return !Number.isSafeInteger(number) || number < decision.input.minimum || number > decision.input.maximum;
  });
  take.disabled = current.taking || current.working || Boolean(current.unreadable) || missing || !current.canApprove;
}

// The raise the view names apart from next_command for the bound holding the run (#2044): its own
// number and button, so a typed limit goes only into that command.
function renderBoundRaise(container, state) {
  const input = state.boundRaise;
  const name = 'raise:' + input.id;
  const box = el('div', 'decision');
  box.id = 'bound-raise';
  box.appendChild(el('div', 'q', 'Or raise the limit instead: ' + input.label));
  box.appendChild(el('div', 'why', numericHelp(input)));
  const field = document.createElement('input');
  field.type = 'text'; field.inputMode = 'numeric'; field.pattern = '[0-9]*';
  field.setAttribute('aria-label', input.label);
  field.dataset.question = name;
  if (answers[name]) field.value = answers[name];
  const hint = el('div', 'range-hint');
  const save = () => { answers[name] = field.value; hint.textContent = integerHint(field.value, input); updateRaiseButton(); };
  field.addEventListener('change', save);
  field.addEventListener('input', save);
  field.addEventListener('keydown', enterTakes);
  box.appendChild(field);
  box.appendChild(hint);
  const raise = el('button', null, (state.taking || state.working) ? 'Working…' : RAISE_LABEL);
  raise.id = 'raise-bound';
  raise.addEventListener('click', () => vscode.postMessage({ type: 'raise', value: answers[name] || '',
    runKey: state.workspace + '\\n' + state.runDir, decisionContext: state.decisionContext }));
  box.appendChild(raise);
  container.appendChild(box);
  updateRaiseButton(raise);
}

// The chat card is built before it joins the document, so the first update is given its button.
function updateRaiseButton(button) {
  const raise = button || document.getElementById('raise-bound');
  if (!raise || !current || !current.boundRaise) return;
  const input = current.boundRaise;
  const value = answers['raise:' + input.id] || '';
  const number = /^[0-9]+$/.test(value) ? Number(value) : NaN;
  const valid = Number.isSafeInteger(number) && number >= input.minimum && number <= input.maximum;
  raise.disabled = current.taking || current.working || Boolean(current.unreadable) || !valid;
}

function numericHelp(input) {
  if (input.id === 'grant_recovery') return 'Automatic recovery allowance: ' + input.current
    + '. Counted recoveries used: ' + input.used + '. Enter 1 to ' + input.maximum
    + ' additional recoveries. A grant replenishes spent slots; history is kept.';
  if (input.id === 'planning_review_call_limit') return 'Saved allowance: ' + input.current
    + ' calls. Plan-review calls used: ' + input.used + '. Enter a total of at least ' + input.minimum + ' calls.';
  if (input.id === 'max_cost_usd') return 'Saved cost cap: $' + input.current + '. Known spend: $' + input.used
    + '. Enter a whole-dollar cap of at least $' + input.minimum + '.';
  if (input.id === 'max_iterations') return 'Saved ceiling: ' + input.current + ' iterations. Current iteration: '
    + input.used + '. Enter a total of at least ' + input.minimum + ' iterations.';
  if (input.id === 'max_milestone_seconds') return 'Saved milestone time: ' + input.current + ' seconds. Counted: '
    + input.used + ' seconds. Enter at least ' + input.minimum + ' seconds.';
  if (input.id === 'max_milestone_replans') return 'Replans allowed: ' + input.current + '. Replans used: '
    + input.used + '. Enter at least ' + input.minimum + '.';
  if (input.id === 'no_progress_limit') return 'Unchanged batches allowed: ' + input.current + '. Counted: '
    + input.used + '. Enter at least ' + input.minimum + '.';
  return 'Saved total: ' + input.current + ' seconds. Active time used: ' + input.elapsed
    + ' seconds. Enter a total of at least ' + input.minimum + ' seconds.';
}

window.addEventListener('message', (event) => {
  const data = event.data;
  if (data && data.type === 'state') render(data.state);
  if (data && data.type === 'chat') {
    if (data.persistSelection) {
      vscode.setState?.({ ...(vscode.getState?.() || {}), selectedRun: data.selectedRun || null,
        tabs: data.tabs || [], activeTabKey: data.activeKey || '' });
    }
    renderChat(data);
  }
  if (data && data.type === 'sent' && chatEnvelope && data.runKey === chatEnvelope.runKey) {
    const field = document.getElementById('message');
    if (field.value === data.text) field.value = '';
    saveDraft(); updateComposer(); chatBehavior.resize();
  }
  if (data && data.type === 'restore_draft') {
    const saved = vscode.getState() || {};
    const drafts = saved.drafts || {};
    const key = data.runKey || '';
    if (!drafts[key]) drafts[key] = data.text;
    vscode.setState({ ...saved, drafts });
    const field = document.getElementById('message');
    if (chatEnvelope && key === chatEnvelope.runKey && !field.value.trim() && data.text) {
      field.value = data.text;
      saveDraft(); updateComposer(); chatBehavior.resize();
    }
  }
  if (data && data.type === 'checkpoint_compare') {
    checkpointCompare[data.checkpoint] = data.error ? { error: data.error }
      : { comparison: data.comparison, kind: data.kind === 'step' ? 'step' : 'compare' };
    renderedState = '';
    if (current) render(current);
  }
  if (data && data.type === 'working_diff' && chatEnvelope && data.runKey === chatEnvelope.runKey) {
    workingCompare = data.error ? { error: data.error } : { comparison: data.comparison };
    renderedState = '';
    if (current) render(current);
  }
  if (data && data.type === 'checkpoint_result') {
    checkpointResult = data;
    renderedState = '';
    if (current) render(current);
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
  const unreadable = Boolean(chatEnvelope.state && chatEnvelope.state.unreadable);
  const allowed = !chatEnvelope.selectedRun || (Boolean(input) && !unreadable);
  // While the run works, Pause takes Send's place (#1598); Send stays beside it when the view also takes a message.
  const pause = chatEnvelope.selectedRun && chatEnvelope.state && chatEnvelope.state.pauseOffer;
  const pausing = Boolean(pause && (pause.requested || chatEnvelope.pausing));
  const pauseButton = document.getElementById('pause-run');
  pauseButton.hidden = !pause;
  pauseButton.disabled = !pause || pausing || unreadable;
  pauseButton.textContent = pausing ? 'Pausing after this step…' : 'Pause';
  pauseButton.setAttribute('aria-label', pause ? (pausing ? 'Pausing after this step' : pause.label) : 'Pause');
  pauseButton.title = pause && pause.effect ? pause.effect : '';
  document.getElementById('send-message').hidden = Boolean(pause) && !input;
  document.getElementById('send-message').disabled = chatEnvelope.busy || !allowed || (Boolean(pause) && !input) || !document.getElementById('message').value.trim();
  document.getElementById('send-message').textContent = 'Send ↑';
  const label = input ? input.label + (input.kind === 'resolver_response' ? '' : ' and continue') : 'Send message';
  document.getElementById('send-message').setAttribute('aria-label', label);
  document.getElementById('send-hint').textContent = chatEnvelope.state && chatEnvelope.state.confirming
    ? chatEnvelope.state.confirming + ' — answer the dialog that opened'
    : !chatEnvelope.selectedRun ? 'Message AutoCode'
    : unreadable ? 'Waiting for the status to read again. You can keep a draft here.'
    : pausing ? 'AutoCode pauses once this step is saved. You can keep a draft here.'
    : input ? (chatEnvelope.busy ? 'AutoCode is working on your last message. Keep your draft here and send it after.' : label)
    : (chatEnvelope.busy || (chatEnvelope.state && chatEnvelope.state.working)) ? 'AutoCode is working. You can keep a draft here.'
    : (chatEnvelope.state && chatEnvelope.state.done) ? 'This run is finished. Start a new chat with +.'
    : 'Use the action above, or start a new chat.';
  document.getElementById('chat-error').textContent = chatEnvelope.error || '';
}

/** The open chats (#1558): one tab per run or fresh chat, addressed by tab key. */
function renderTabs(data) {
  const host = document.getElementById('chat-tabs');
  if (!host) return;
  const rows = data.tabs || [{ key: data.runKey || '', name: data.runKey ? 'Chat' : 'New chat' }];
  tabKeys = rows.map((tab) => tab.key);
  activeTabKey = data.activeKey || '';
  const signature = JSON.stringify([rows, data.activeKey]);
  if (host.dataset.content === signature) return;
  host.dataset.content = signature;
  // A selection rebuilds the strip: keyboard focus that was on it moves to the new active tab,
  // not to the body (the composer and everything else keep their focus untouched).
  const stripHadFocus = host.contains(document.activeElement);
  host.textContent = '';
  rows.forEach((tab) => {
    const item = el('div', 'chat-tab' + (tab.key === data.activeKey ? ' active' : ''));
    item.role = 'tab';
    item.tabIndex = tab.key === data.activeKey ? 0 : -1;
    item.setAttribute('aria-selected', tab.key === data.activeKey ? 'true' : 'false');
    const name = el('span', 'name', tab.name);
    item.appendChild(name);
    if (tab.needs) {
      const dot = el('span', 'dot needs', '●');
      dot.title = 'Waiting for you';
      dot.setAttribute('aria-label', 'Waiting for you');
      item.appendChild(dot);
    } else if (tab.busy) item.appendChild(el('span', 'dot', '•'));
    else if (tab.finished) {
      const dot = el('span', 'dot finished', '✓');
      dot.title = 'Finished';
      dot.setAttribute('aria-label', 'Finished');
      item.appendChild(dot);
    }
    if (rows.length > 1) {
      // A real button: the strip's only close affordance must be reachable and pressable by keyboard.
      const close = el('button', 'close', '×');
      close.type = 'button';
      close.title = 'Close chat';
      close.setAttribute('aria-label', 'Close chat ' + tab.name);
      close.addEventListener('click', (event) => {
        event.stopPropagation();
        vscode.postMessage({ type: 'close_tab', tabKey: tab.key });
      });
      item.appendChild(close);
    }
    item.title = tab.name;
    item.addEventListener('click', () => {
      if (tab.key !== data.activeKey) vscode.postMessage({ type: 'select_tab', tabKey: tab.key });
    });
    host.appendChild(item);
  });
  // A new chat opens past the visible strip; keep the chat you are in on screen.
  const active = host.querySelector('.chat-tab.active');
  if (active && active.scrollIntoView) active.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  if (stripHadFocus && active) active.focus();
}

function renderChat(data) {
  const thread = document.getElementById('thread');
  document.getElementById('engine-name').textContent = data.engine || 'opencode';
  // The core's run mode for this chat; only a chat that has not started offers one.
  document.getElementById('mode-picker').hidden = !data.mode;
  document.getElementById('mode-name').textContent = data.mode || '';
  // This chat's models, from the core's own list: hidden where it offers no change (an older AutoCode).
  const models = document.getElementById('models-picker');
  models.hidden = !data.models;
  models.disabled = !data.models || data.models.disabled;
  document.getElementById('models-name').textContent = data.models ? data.models.label : 'Models';
  models.title = data.models ? data.models.title : '';
  const detailsBtn = document.getElementById('details');
  if (detailsBtn) detailsBtn.disabled = !data.selectedRun;
  renderTabs(data);
  const same = chatEnvelope && chatEnvelope.runKey === data.runKey;
  if (!same) {
    if (chatEnvelope) tabScroll[chatEnvelope.runKey] = thread.scrollTop;
    saveDraft();
    const saved = vscode.getState() || {};
    const incoming = (saved.drafts || {})[data.runKey] || '';
    const field = document.getElementById('message');
    // A re-key of a chat that never started must not eat an unsent brief: the host
    // replaces the fresh-chat tab while the person is typing in it.
    const leavingUnstarted = chatEnvelope && !chatEnvelope.selectedRun;
    if (leavingUnstarted && field.value.trim() && !incoming) {
      const drafts = saved.drafts || {};
      drafts[data.runKey] = field.value;
      vscode.setState({ ...saved, drafts });
    } else {
      field.value = incoming;
    }
    current = null; renderedState = ''; answers = Object.create(null);
    checkpointCompare = Object.create(null); checkpointResult = null; workingCompare = null;
    liveLog.clear();
    openThoughts.clear();
  }
  chatEnvelope = data;
  const messages = document.getElementById('messages');
  const welcome = { id: 'welcome', role: 'assistant', text: 'Hi! What would you like to work on today?' };
  const rows = data.state && data.state.chat ? data.state.chat.messages
    : [welcome, ...(data.pendingBrief ? [{ role: 'user', text: data.pendingBrief }] : [])];
  // A start that never reached a run leaves no transcript. The thread must still
  // hold what happened: the greeting, the request, and why it did not start.
  if (!data.state && data.error) rows.push({ role: 'notice', text: data.error });
  // A streaming reply is provisional display content (task-run.md): shown as still
  // written, never as a finished AutoCode message. Its extract updates in place so a
  // growing reply does not rebuild the whole thread (#1528 parity with the TUI).
  const finishedKey = JSON.stringify(rows.filter((row) => !row.streaming));
  const liveRows = rows.filter((row) => row.streaming);
  const liveKey = JSON.stringify(liveRows.map((row) => [row.id, row.text]));
  const priorLive = messages.dataset.live ?? '';
  const priorFinished = messages.dataset.finished ?? '';
  // A live row that appears (or the last one that finishes) has no in-place node
  // either way: JSON.stringify of an empty list is the truthy '[]', so emptiness
  // must be compared as a count, not as truthiness.
  const hadLive = priorLive !== '' && priorLive !== '[]';
  const hasLive = liveRows.length > 0;
  if (priorFinished !== finishedKey || hasLive !== hadLive) {
    messages.dataset.finished = finishedKey;
    messages.dataset.live = liveKey;
    messages.dataset.content = finishedKey + liveKey;
    messages.textContent = '';
    rows.forEach((row) => {
      const notice = row.role === 'notice';
      const streaming = Boolean(row.streaming);
      const item = el('div', 'message ' + (notice ? 'notice' : row.role === 'user' ? 'user' : streaming ? 'assistant streaming' : 'assistant'));
      if (streaming) item.dataset.live = row.id;
      const speaker = notice ? 'Notice' : row.role === 'user' ? 'You' : streaming ? 'AutoCode · writing…' : 'AutoCode';
      item.appendChild(el('div', 'speaker', speaker));
      const body = el('div', 'message-content');
      // Provisional text is shown plain while it grows, as the terminal chat does.
      const formatted = !streaming && data.state && data.state.formattedMessages && data.state.formattedMessages[row.id];
      if (row.role === 'assistant' && formatted) appendFormatted(body, formatted);
      else body.textContent = row.text;
      item.appendChild(body); messages.appendChild(item);
    });
  } else if (liveKey !== priorLive) {
    // Only the growing extract changed: rewrite that body, leave the rest of the thread alone.
    messages.dataset.live = liveKey;
    messages.dataset.content = finishedKey + liveKey;
    for (const row of liveRows) {
      // String concat, not a template literal: this script is itself inside one.
      const item = messages.querySelector('[data-live="' + row.id + '"]');
      const body = item && item.querySelector('.message-content');
      if (body) body.textContent = row.text;
    }
  }
  updateLatestCue(data.state);
  const welcoming = !data.state && !data.busy && !data.selectedRun && !data.pendingBrief && !data.error;
  if (data.state) render(data.state);
  else if (welcoming && document.getElementById('welcome')) {
    // Already showing: nothing in it changes between reads.
  } else {
    app.textContent = '';
    renderedState = '';
    if (data.busy) {
      app.classList.remove('muted');
      const card = el('div', 'decision-card');
      card.appendChild(el('div', 'speaker', 'AutoCode'));
      // Before its run exists a chat is starting, not waiting on setup: it names a dialog that holds it.
      card.appendChild(el('div', 'message-content', data.waitingOn ? data.waitingOn
        : data.startingLabel ? data.startingLabel.charAt(0).toUpperCase() + data.startingLabel.slice(1) + '…' : 'Thinking…'));
      app.appendChild(card);
    } else if (welcoming) {
      app.classList.remove('muted');
      renderWelcome();
    } else {
      app.classList.add('muted');
    }
  }
  renderHeader(data);
  renderChatPlace(data);
  renderChatModels(data);
  renderMeter(data);
  renderTelemetry(data.state && data.state.telemetry, data.state && data.state.stageModels, !data.selectedRun && Boolean(data.models));
  updateComposer();
  chatBehavior.resize();
  if (!same) {
    // Each tab remembers where its reader was (#1558); a first open starts at
    // the latest messages. A remembered position pauses the follow behavior,
    // so a poll cannot yank the reader to the bottom.
    const remembered = tabScroll[data.runKey];
    if (remembered === undefined) chatBehavior.resume();
    else {
      thread.dispatchEvent(new WheelEvent('wheel', { deltaY: -1, bubbles: true, cancelable: true }));
      thread.scrollTop = remembered;
    }
  }
}

/**
 * A chat that has not started: where it will change files, from the core's worktree list. A new worktree
 * and branch is preselected; the folder itself shows the core's warning, which the person confirms again
 * at the start. Only a place's id (and a listed branch) goes back; the core's own start flags are used.
 */
function renderChatPlace(data) {
  const host = document.getElementById('chat-place');
  const info = data.chatPlace;
  host.hidden = !info || data.busy || Boolean(data.selectedRun) || (!info.places.length && !info.error);
  if (host.hidden) { host.dataset.content = ''; return; }
  const key = JSON.stringify(info);
  if (host.dataset.content === key) return;
  host.dataset.content = key;
  const body = document.getElementById('chat-place-body');
  body.textContent = '';
  const chosen = info.places.find((place) => place.id === info.chosen);
  const branchName = chosen && chosen.id === 'branch' ? info.branch : null;
  document.getElementById('chat-place-summary').textContent = info.error ? 'your folder'
    : chosen ? (branchName ? 'branch ' + branchName : chosen.id === 'branch' ? 'pick a branch' : chosen.label) : '';
  if (info.error) { body.appendChild(el('div', 'chat-place-note', info.error)); return; }
  const group = 'chat-place-' + Math.random().toString(16).slice(2);
  info.places.forEach((place) => {
    const row = el('label', 'chat-place-option');
    row.dataset.place = place.id;
    const radio = el('input');
    radio.type = 'radio'; radio.name = group; radio.value = place.id; radio.checked = place.id === info.chosen;
    radio.addEventListener('change', () => {
      const first = place.branches.find((branch) => !branch.unavailable);
      vscode.postMessage({ type: 'chat_place', place: place.id, branch: place.id === 'branch' ? (info.branch || (first && first.name) || '') : '' });
    });
    row.appendChild(radio);
    row.appendChild(el('span', 'label', place.label));
    row.appendChild(el('span', 'does', place.does));
    if (place.id === 'branch') {
      const select = el('select', 'branch');
      select.setAttribute('aria-label', 'Branch this chat works on');
      const prompt = el('option', null, place.branches.length ? 'Pick a branch' : 'No other local branch');
      prompt.value = ''; prompt.disabled = true; prompt.selected = !branchName;
      select.appendChild(prompt);
      place.branches.forEach((branch) => {
        const item = el('option', null, branch.name + (branch.unavailable ? ' — not available: ' + branch.unavailable : ''));
        item.value = branch.name; item.disabled = Boolean(branch.unavailable); item.selected = branch.name === branchName;
        select.appendChild(item);
      });
      select.addEventListener('change', () => vscode.postMessage({ type: 'chat_place', place: 'branch', branch: select.value }));
      row.appendChild(select);
    }
    body.appendChild(row);
  });
  if (info.warning) {
    const warning = el('div', 'chat-place-warning', '⚠ ' + info.warning);
    warning.setAttribute('role', 'note');
    body.appendChild(warning);
  }
}

/**
 * A chat that has not started: the model and level each stage will run, from the core's list, and
 * dropdowns that change them for this chat alone. The extension fills only the stage's own named
 * command with what was picked here; the core checks it and nothing is saved.
 */
function renderChatModels(data) {
  const host = document.getElementById('chat-models');
  const info = data.chatStages;
  host.hidden = !info || data.busy || Boolean(data.selectedRun) || (!info.stages.length && !info.error && !info.busy);
  if (host.hidden) { host.dataset.content = ''; return; }
  const key = JSON.stringify(info);
  if (host.dataset.content === key) return;
  host.dataset.content = key;
  const picked = info.stages.filter((stage) => stage.picked).length;
  document.getElementById('chat-models-summary').textContent = [info.label, picked ? picked + ' changed for this chat' : '']
    .filter(Boolean).join(' · ');
  const body = document.getElementById('chat-models-body');
  body.textContent = '';
  if (info.error) { const error = el('div', 'chat-models-error', info.error); error.setAttribute('role', 'alert'); body.appendChild(error); }
  if (info.busy && !info.stages.length) body.appendChild(el('div', 'model-note', 'Reading the stages…'));
  if (!info.stages.length) return;
  body.appendChild(el('div', 'model-note', info.optionsStop ? 'Models cannot be listed: ' + info.optionsStop
    : info.changeable ? 'Change a stage here for this chat only; Stage models still sets every other new chat.'
    : 'Update AutoCode to change these for one chat; Stage models sets them for new chats.'));
  info.stages.forEach((stage) => {
    const row = el('div', 'chat-model' + (stage.picked ? ' picked' : '') + (stage.checking ? ' checking' : ''));
    row.dataset.stage = stage.id;
    row.title = stage.does;
    const head = el('div', 'head');
    head.appendChild(el('span', 'label', stage.label));
    // The dropdowns already show the model and level: beside them, only where it comes from (or
    // that the core is checking the pick). Without dropdowns the words are the only place it shows.
    const dropdowns = info.changeable && stage.options.length > 0;
    const now = el('span', 'now', stage.checking ? 'Checking…' : dropdowns ? stage.source : stage.now);
    if (dropdowns) now.title = stage.now;
    if (stage.checking) now.setAttribute('role', 'status');
    head.appendChild(now);
    row.appendChild(head);
    if (dropdowns) {
      const pick = el('div', 'pick');
      const models = el('select', 'model'); models.setAttribute('aria-label', stage.label + ' model for this chat');
      const levels = el('select', 'level'); levels.setAttribute('aria-label', stage.label + ' reasoning level for this chat');
      if (!stage.model) {
        const automatic = el('option', null, 'Chosen when the chat starts'); automatic.value = ''; automatic.disabled = true; automatic.selected = true;
        models.appendChild(automatic);
      }
      stage.options.forEach((option) => {
        const item = el('option', null, option.model + (option.family ? ' · ' + option.family : '') + (option.unavailable ? ' — not available: ' + option.unavailable : ''));
        item.value = option.model; item.disabled = Boolean(option.unavailable); item.selected = option.model === stage.model;
        models.appendChild(item);
      });
      const fill = (wanted) => {
        levels.textContent = '';
        const option = stage.options.find((row) => row.model === models.value);
        const choices = option ? option.effortChoices : [];
        choices.forEach((level) => {
          const item = el('option', null, level); item.value = level;
          item.selected = level === (choices.includes(wanted) ? wanted : choices[0]);
          levels.appendChild(item);
        });
      };
      fill(stage.effort || 'default');
      const send = () => { if (models.value && levels.value) vscode.postMessage({ type: 'chat_stage', stage: stage.id, model: models.value, effort: levels.value }); };
      models.addEventListener('change', () => { fill(levels.value || stage.effort); send(); });
      levels.addEventListener('change', send);
      models.disabled = levels.disabled = info.busy;
      pick.appendChild(models); pick.appendChild(levels);
      row.appendChild(pick);
    }
    if (stage.resettable) {
      const reset = el('button', 'link', 'Use Stage models');
      reset.type = 'button'; reset.disabled = info.busy;
      reset.addEventListener('click', () => vscode.postMessage({ type: 'chat_stage_reset', stage: stage.id }));
      row.appendChild(reset);
    }
    body.appendChild(row);
  });
}

/** A reader scrolled up keeps their place; the Latest button names the step running now (#1711). */
function updateLatestCue(state) {
  const latest = document.getElementById('latest');
  if (!latest) return;
  const running = state && !state.done && state.needKind === 'continue' && state.working && state.chat
    && Array.isArray(state.chat.progress) ? state.chat.progress.find((line) => typeof line === 'string' && line) : '';
  const text = running ? '↓ Now: ' + running : '↓ Latest messages';
  if (latest.textContent !== text) latest.textContent = text;
}

function renderTelemetry(info, stageModels, pickable) {
  const host = document.getElementById('model-info');
  const signature = JSON.stringify([info, stageModels, pickable]);
  if (host.dataset.content === signature) return;
  host.dataset.content = signature;
  host.textContent = '';
  const configured = info ? info.configuredModels : [];
  const summary = [];
  if (info && info.models.length) summary.push(info.modelLabel + ': ' + info.models.map((item) => item.job + ' · ' + item.model.split('/').pop() + ' · ' + item.variant).join(' / '));
  if (configured.length) summary.push(configured.length + ' configured roles');
  document.getElementById('model-summary').textContent = summary.join(' · ') || 'Model not reported';
  function modelSection(id, title, note, items) {
    const section = el('div', 'model-section'); section.id = id;
    section.appendChild(el('div', 'model-caption', title));
    if (note) section.appendChild(el('div', 'model-note', note));
    items.forEach((item) => {
      const model = el('div', 'model-card');
      const heading = el('div', 'model-heading');
      heading.appendChild(el('span', 'model-job', item.job));
      heading.appendChild(el('span', 'model-variant', 'Variant: ' + item.variant));
      model.appendChild(heading);
      model.appendChild(el('div', 'model-value', item.model));
      const meta = ['Provider: ' + item.provider];
      if (item.attempts) meta.push(item.attempts + (item.attempts === 1 ? ' attempt' : ' attempts'));
      if (item.active) meta.push('Active');
      model.appendChild(el('div', 'model-meta', meta.join(' · ')));
      section.appendChild(model);
    });
    host.appendChild(section);
  }
  // A run keeps the routes it started with; Stage models changes reach new chats only (docs/task-run.md#stage-choices-for-clients).
  if (configured.length) modelSection('configured-models', "This chat's models", stageModels ? 'Kept from when this chat started until you change one here; Stage models applies to new chats. Several stages can share a model; an assignment does not mean it has run.' : 'Fixed when this chat started; a change in Stage models applies to new chats. Several stages can share a model; an assignment does not mean it has run.', configured);
  if (configured.length && stageModels) {
    // Only the core's own offer: the extension asks for the list the status view names.
    const change = el('button', 'link', "Change this chat's models");
    change.type = 'button'; change.id = 'change-stage-models';
    change.disabled = !stageModels.changeable;
    change.addEventListener('click', () => vscode.postMessage({ type: 'stage_models' }));
    const section = document.getElementById('configured-models');
    section.insertBefore(change, section.children[2] || null);
    if (!stageModels.changeable && stageModels.reason) section.insertBefore(el('div', 'model-note', stageModels.reason), change.nextSibling);
  }
  if (info && info.stagesUsed.length) modelSection('used-models', 'Models used', 'Recorded attempts, including retries and earlier model variants.', info.stagesUsed);
  else {
    // Before a run reports anything, say where its models are chosen now, not where they will be listed.
    host.appendChild(el('span', 'model-placeholder', info ? 'No model attempts reported yet.'
      : pickable ? "Nothing has run yet. Choose this chat's models with Models under the message box."
      : 'Nothing has run yet. A new chat runs the models in Stage models.'));
  }
  document.getElementById('usage-summary').textContent = info ? info.tokens + ' · ' + info.cost : 'Tokens not reported · Cost not reported';
  const details = document.getElementById('usage-breakdown');
  details.textContent = '';
  if (!info) { details.textContent = 'Tokens and cost are shown here once the chat starts.'; return; }
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
  disclosure.open = initialWebviewState.modelDetailsOpen === true;
  disclosure.addEventListener('toggle', () => {
    vscode.setState({ ...(vscode.getState() || {}), modelDetailsOpen: disclosure.open });
  });
  document.getElementById('history').addEventListener('click', () => vscode.postMessage({ type: 'history' }));
  // The tab strip works from the keyboard: arrows (and Home/End) move over the chats and select,
  // the same selection a click makes, with the roving tabindex renderTabs keeps.
  document.getElementById('chat-tabs').addEventListener('keydown', (event) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const host = document.getElementById('chat-tabs');
    const index = tabKeys.indexOf(activeTabKey);
    if (!tabKeys.length || index < 0) return;
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabKeys.length - 1
      : event.key === 'ArrowRight' ? (index + 1) % tabKeys.length : (index - 1 + tabKeys.length) % tabKeys.length;
    if (host.children[next]) host.children[next].focus();
    if (tabKeys[next] !== activeTabKey) vscode.postMessage({ type: 'select_tab', tabKey: tabKeys[next] });
  });
  document.getElementById('connections').addEventListener('click', () => vscode.postMessage({ type: 'connections' }));
  document.getElementById('details').addEventListener('click', () => {
    if (chatEnvelope && chatEnvelope.runKey) vscode.postMessage({ type: 'details' });
  });
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
  document.getElementById('header-toggle').addEventListener('click', () => {
    headerOpen = !headerOpen;
    vscode.setState({ ...(vscode.getState() || {}), headerOpen });
    if (chatEnvelope) renderHeader(chatEnvelope);
  });
  document.getElementById('pause-run').addEventListener('click', () => {
    if (chatEnvelope && chatEnvelope.runKey) vscode.postMessage({ type: 'pause', runKey: chatEnvelope.runKey });
  });
  document.getElementById('engine-picker').addEventListener('click', () => vscode.postMessage({ type: 'engine' }));
  document.getElementById('models-picker').addEventListener('click', () => vscode.postMessage({ type: 'models' }));
  document.getElementById('mode-picker').addEventListener('click', () => vscode.postMessage({ type: 'mode' }));
}

vscode.postMessage({
  type: 'ready',
  hasSelectedRun: chatMode && Object.prototype.hasOwnProperty.call(initialWebviewState, 'selectedRun'),
  selectedRun: chatMode ? initialWebviewState.selectedRun || null : null,
  tabs: chatMode ? initialWebviewState.tabs || [] : [],
  activeKey: chatMode ? initialWebviewState.activeTabKey || '' : '',
});
</script>
</body>
</html>`;
}
