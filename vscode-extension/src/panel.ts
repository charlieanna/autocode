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
import { finishSummary, runTotal, stageLines, stages, struggling, totalText, type FinishSummary, type StageRow } from "./core/meter";

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
  plan: { revision: number | null; criteria: AcceptanceCriterion[]; constraints: string[]; boundaries: string[]; exclusions: string[]; delivery: string | null } | null;
  /** The token the approval is bound to; the webview echoes it back so a stale click cannot approve. */
  token: string | null;
  /** False when the action would be an approval of a plan this client never displayed. */
  canApprove: boolean;
  /** This run's change set (`changeset status --json`, #2066), or null when it is in none. */
  changeset?: ChangesetState | null;
  /** The raise the view names apart from next_command for the bound holding the run (#2044), or null. */
  boundRaise: IntegerCommandInput | null;
  /** Each stage's outcome, time, cost, attempts, model and tokens, from the view's own records. */
  meter?: MeterState;
  /** A finished run's closing card, or null while it runs. */
  finish?: FinishSummary | null;
}

/** The stage rows, the struggling warning and the run's cost line the chat shows. */
export interface MeterState {
  stages: (StageRow & { head: string; detail: string })[];
  struggling: string;
  total: string;
}

/** The meter for one status view: display only, every figure the core's own. */
export function meterOf(view: StatusView, now = Date.now()): MeterState {
  return {
    stages: stages(view, now).map((row) => ({ ...row, ...stageLines(row) })),
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
export async function confirmRaise(state: PanelState, decisionContext: string | undefined, value: string): Promise<boolean> {
  const input = state.boundRaise;
  if (!input || decisionContext !== state.decisionContext) {
    throw new ClientGap("The requested action changed; review it before continuing");
  }
  const title = raiseConfirmation(input, value);
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
  #run-cost { margin-right: auto; align-self: center; font-size: .75rem; opacity: .85; white-space: nowrap;
              overflow: hidden; text-overflow: ellipsis; min-width: 0; }
  #run-cost .session { opacity: .75; }
  #stages { flex-shrink: 0; margin: 0 6px 6px; border: 1px solid var(--vscode-panel-border); border-radius: 6px;
            padding: 4px 8px; font-size: .75rem; }
  #stages[hidden] { display: none; }
  #stages-head { display: flex; justify-content: space-between; align-items: baseline; }
  .stages-title { font-weight: 600; text-transform: uppercase; letter-spacing: .05em; opacity: .75; font-size: .7rem; }
  button.link { background: none; border: none; padding: 0; color: var(--vscode-textLink-foreground); font-size: .72rem; }
  button.link[hidden] { display: none; }
  .stage { display: grid; grid-template-columns: 1.4ch 1fr; column-gap: 4px; padding: 2px 0; }
  .stage .mark { grid-row: span 2; }
  .stage .detail { opacity: .7; overflow-wrap: anywhere; }
  .stage.done .mark { color: var(--vscode-charts-green, #3a7d3a); }
  .stage.working .mark { color: var(--vscode-charts-blue, #1a6fb0); }
  .stage.retrying .mark, .stage.retrying .head { color: var(--vscode-editorWarning-foreground, #cc8800); }
  .stage.failed .mark, .stage.failed .head { color: var(--vscode-errorForeground, #a1260d); }
  .struggling { border-left: 3px solid var(--vscode-editorWarning-foreground, #cc8800); padding: 3px 8px; margin: 4px 0;
                color: var(--vscode-editorWarning-foreground, #cc8800); font-size: .85rem; }
  .chat-tab .dot.needs { color: var(--vscode-editorWarning-foreground, #cc8800); }
  .chat-tab .dot.finished { color: var(--vscode-charts-green, #3a7d3a); }
  .welcome { padding: 4px 2px; font-size: .88rem; }
  .welcome p { margin: 4px 0 10px; opacity: .85; }
  .welcome .kinds { display: grid; grid-template-columns: max-content 1fr; gap: 3px 10px; margin: 6px 0 12px; font-size: .82rem; }
  .welcome .kinds b { font-weight: 600; }
  .welcome .try { opacity: .75; font-size: .8rem; margin-bottom: 4px; }
  .welcome button.example { display: block; width: 100%; text-align: left; margin: 4px 0; background: var(--vscode-input-background);
                            color: var(--vscode-foreground); border: 1px solid var(--vscode-panel-border); border-radius: 4px; }
  .welcome button.example:hover { border-color: var(--vscode-focusBorder); }
  .finish .result { font-weight: 600; margin-bottom: 6px; }
  .finish .facts { display: grid; grid-template-columns: max-content 1fr; gap: 3px 10px; font-size: .84rem; margin-bottom: 8px; }
  .finish .facts span:nth-child(odd) { opacity: .7; }
  .finish .files { overflow-wrap: anywhere; }
`;

/** What a new chat can ask for: AutoCode's five workflows, in a person's words. */
export const WELCOME_KINDS: [string, string][] = [
  ["Build", "a feature or change, planned and checked before it is called done"],
  ["Fix", "a bug: find the cause, prove it with a test, then fix it"],
  ["Review", "a change or a branch for problems"],
  ["Design", "something before building it"],
  ["Discuss", "a question about this code, without changing it"],
];

/** Example requests the welcome offers; clicking one only fills the message box. */
export const WELCOME_EXAMPLES = [
  "Fix the failing test in tests/test_api.py",
  "Add a --json option to the export command",
  "Why does sign-in time out on slow networks?",
];

const ICONS = {
  plus: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>`,
  clock: `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.25" /><path d="M12 7.5V12l3 2" /></svg>`,
  panel: `<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="4.5" width="17" height="15" rx="2" /><path d="M15 4.5v15" /></svg>`,
  plug: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3.5v4M15 3.5v4M6.5 7.5h11v3.5a5.5 5.5 0 0 1-11 0V7.5zM12 16.5v4" /></svg>`,
  route: `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="6" cy="6" r="2.25" /><circle cx="18" cy="18" r="2.25" /><path d="M8.25 6H14a3.5 3.5 0 0 1 0 7h-4a3.5 3.5 0 0 0 0 7h5.75" /></svg>`,
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
  .live-log .agent { flex: none; opacity: .75; }
  pre { white-space: pre-wrap; word-break: break-all; font-size: .76rem; background: var(--vscode-textCodeBlock-background);
        padding: 8px; border-radius: 4px; }
  .gap, .notice { border-left: 3px solid var(--vscode-inputValidation-warningBorder, #cc8800);
                  background: var(--vscode-inputValidation-warningBackground, rgba(204,136,0,.12));
                  padding: 8px 10px; margin: 12px 0; font-size: .84rem; }
  .notice { border-left-color: var(--vscode-textLink-foreground, #3794ff); background: transparent; }
  .actions { display: flex; gap: 8px; margin-top: 14px; }
  .muted { opacity: .65; }
  ${chat ? chatStyles : ""}
  ${chat ? "#composer .chip[hidden] { display: none; }" : ""}
  ${chat ? METER_STYLES : ""}
</style>
</head>
<body>
${chat ? `<nav class="chat-toolbar" aria-label="AutoCode chat tools"><span id="run-cost" role="status" aria-label="Run cost"></span>${iconButton("connections", "Connections", ICONS.plug)}${iconButton("history", "History", ICONS.clock)}${iconButton("details", "Run details", ICONS.panel, true)}${iconButton("new-chat", "New chat", ICONS.plus)}</nav><div id="chat-tabs" role="tablist" aria-label="Open chats"></div><div id="thread" tabindex="0" aria-label="Conversation"><div id="thread-content"><div id="messages" role="log" aria-label="Messages"></div>` : ""}
<div id="app" class="muted">${chat ? "Describe what you want to build, fix, review, or discuss." : "Loading…"}</div>
${chat ? `</div></div><button id="latest" type="button" hidden>↓ Latest messages</button>
<section id="stages" aria-label="Stages" hidden><div id="stages-head"><span class="stages-title">Stages</span><button type="button" id="stages-more" class="link" hidden></button></div><div id="stage-rows"></div></section>
<section id="telemetry" aria-label="Models and usage">
<details id="usage-details"><summary id="telemetry-summary"><span class="telemetry-heading">Models &amp; usage</span>
<span id="model-summary">Model not reported</span><span id="usage-summary">Tokens not reported · Cost not reported</span></summary>
<div id="telemetry-body"><div id="model-info"></div><div id="usage-breakdown"></div></div></details>
</section>
<form id="composer">
<label for="message" id="send-hint">Message AutoCode</label>
<textarea id="message" aria-label="Message AutoCode" aria-describedby="keyboard-hint" placeholder="Ask AutoCode to work on this project…"></textarea>
<div id="chat-error" role="alert"></div>
<div class="actions"><button type="button" id="engine-picker" class="chip" title="Engine for new chats" aria-label="Engine for new chats">${ICONS.tune}<span id="engine-name">opencode</span></button><button type="button" id="mode-picker" class="chip" title="Run mode for new chats" aria-label="Run mode for new chats" hidden>${ICONS.route}<span id="mode-name"></span></button><span id="keyboard-hint" class="keyboard-hint">Enter to send · Shift+Enter for newline</span>
<button type="button" id="pause-run" hidden>Pause</button><button type="submit" id="send-message" disabled>Send</button></div></form>` : ""}
<script>
const vscode = acquireVsCodeApi();
const app = document.getElementById('app');
const chatMode = ${chat};
const RAISE_LABEL = ${JSON.stringify(RAISE_LABEL)};
const WELCOME_KINDS = ${JSON.stringify(WELCOME_KINDS)};
const WELCOME_EXAMPLES = ${JSON.stringify(WELCOME_EXAMPLES)};
const STAGE_ROWS = 4;
const STAGE_MARKS = { done: '✓', working: '●', retrying: '↻', failed: '✗' };
let stagesExpanded = false;
const initialWebviewState = vscode.getState?.() || {};
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
let checkpointResult = null;
const tabScroll = Object.create(null);

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

function renderChatProgress(container, state) {
  if (state.meter && state.meter.struggling) {
    const warn = el('div', 'struggling', '⚠ ' + state.meter.struggling);
    warn.id = 'struggling';
    warn.setAttribute('role', 'status');
    container.appendChild(warn);
  }
  const lines = state.chat && Array.isArray(state.chat.progress)
    ? state.chat.progress.filter((line) => typeof line === 'string' && line) : [];
  (lines.length ? lines : ['Thinking…']).forEach((line) => container.appendChild(el('div', 'line', line)));
  // Live agent log (chat.log): what each running agent thinks, says and does. Kept by id
  // across reads like the terminal chat's LiveLog, since the view holds only the newest.
  const logRows = state.chat && Array.isArray(state.chat.log) ? state.chat.log : [];
  for (const row of logRows) {
    if (!row || typeof row.id !== 'string' || typeof row.text !== 'string' || typeof row.agent !== 'string') continue;
    if (!(row.kind in ACTIVITY_MARKS)) continue;
    liveLog.set(row.id, { agent: row.agent, kind: row.kind, text: row.text, status: row.status || '' });
  }
  while (liveLog.size > 500) liveLog.delete(liveLog.keys().next().value);
  const live = [...liveLog.values()].slice(-8);
  if (live.length) {
    // Supersedes the bare activity list: the log already carries those tool marks.
    const list = el('div', 'activity live-log');
    list.setAttribute('aria-label', 'Agent log');
    live.forEach((row) => {
      const mark = row.status === 'failed' ? '✗' : (ACTIVITY_MARKS[row.kind] || '·');
      const line = el('div', 'activity-row ' + (row.status || '') + (row.kind === 'thinking' ? ' thinking' : ''));
      line.appendChild(el('span', 'activity-mark', mark));
      line.appendChild(el('span', 'agent', row.agent));
      line.appendChild(el('span', null, row.text));
      list.appendChild(line);
    });
    container.appendChild(list);
    return;
  }
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
    const patch = el('pre');
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

/** A new chat's welcome: what AutoCode can do, and example requests that only fill the message box. */
function renderWelcome() {
  const box = el('div', 'welcome');
  box.id = 'welcome';
  box.appendChild(el('p', null, 'AutoCode plans the work with you, builds it, and checks it independently before it calls it done.'));
  const kinds = el('div', 'kinds');
  WELCOME_KINDS.forEach(([name, what]) => { kinds.appendChild(el('b', null, name)); kinds.appendChild(el('span', null, what)); });
  box.appendChild(kinds);
  box.appendChild(el('div', 'try', 'Try asking:'));
  WELCOME_EXAMPLES.forEach((example) => {
    const button = el('button', 'example', example);
    button.type = 'button';
    button.addEventListener('click', () => {
      const field = document.getElementById('message');
      field.value = example;
      field.focus();
      saveDraft(); updateComposer(); chatBehavior.resize();
    });
    box.appendChild(button);
  });
  app.appendChild(box);
}

/** The cost line in the toolbar and the stage rows above the usage section. */
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
  const section = document.getElementById('stages');
  if (!section) return;
  const rows = meter ? meter.stages : [];
  section.hidden = !rows.length;
  const key = JSON.stringify([rows, stagesExpanded]);
  if (section.dataset.content === key) return;
  section.dataset.content = key;
  const host = document.getElementById('stage-rows');
  host.textContent = '';
  const shown = stagesExpanded ? rows : rows.slice(-STAGE_ROWS);
  const more = document.getElementById('stages-more');
  more.hidden = rows.length <= STAGE_ROWS;
  more.textContent = stagesExpanded ? 'Show fewer' : (rows.length - STAGE_ROWS) + ' earlier';
  shown.forEach((row) => {
    const item = el('div', 'stage ' + row.state);
    item.dataset.stage = row.stage;
    item.appendChild(el('span', 'mark', STAGE_MARKS[row.state] || '·'));
    item.appendChild(el('span', 'head', row.head));
    if (row.detail) item.appendChild(el('span', 'detail', row.detail));
    host.appendChild(item);
  });
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
    state.actionLabel, state.actionEffect, state.stopped, state.unreadable,
    state.chat && [state.chat.phase, state.chat.notice, state.chat.progress, state.chat.activity, state.chat.log, state.chat.review, state.chat.input && state.chat.input.kind],
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
        if (review.summary) cardContent.appendChild(el('pre', 'understanding', review.summary));
        const approve = el('button', null, review.label);
        approve.id = 'take-action';
        approve.disabled = state.taking || state.working || Boolean(state.unreadable);
        approve.addEventListener('click', () => vscode.postMessage({ type: 'take',
          answers: { [review.question_id]: review.answer }, token: null,
          runKey: state.workspace + '\\n' + state.runDir, decisionContext: state.decisionContext }));
        strip.appendChild(el('span', 'action-prompt', 'AutoCode is waiting for you'));
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
          } else {
            input = document.createElement('input');
            input.type = 'text';
            if (decision.kind === 'integer') { input.inputMode = 'numeric'; input.pattern = '[0-9]*'; input.setAttribute('aria-label', decision.text); }
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
      strip.appendChild(el('span', 'action-prompt', 'AutoCode is waiting for you'));
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
          box.appendChild(el('div', 'why', files + ' file(s) differ from this checkpoint' + (comparison.truncated ? ' · diff truncated' : '')));
          const patch = el('pre');
          patch.textContent = comparison.patch || 'The files match this checkpoint.';
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
      } else {
        input = document.createElement('input');
        input.type = 'text';
        if (decision.kind === 'integer') { input.inputMode = 'numeric'; input.pattern = '[0-9]*'; input.setAttribute('aria-label', decision.text); }
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
  const save = () => { answers[name] = field.value; updateRaiseButton(); };
  field.addEventListener('change', save);
  field.addEventListener('input', save);
  box.appendChild(field);
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
    checkpointCompare[data.checkpoint] = data.error ? { error: data.error } : { comparison: data.comparison };
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
  document.getElementById('send-hint').textContent = !chatEnvelope.selectedRun ? 'Message AutoCode'
    : unreadable ? 'Waiting for the status to read again. You can keep a draft here.'
    : pausing ? 'AutoCode pauses once this step is saved. You can keep a draft here.'
    : input ? label
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
  const signature = JSON.stringify([rows, data.activeKey]);
  if (host.dataset.content === signature) return;
  host.dataset.content = signature;
  host.textContent = '';
  rows.forEach((tab) => {
    const item = el('div', 'chat-tab' + (tab.key === data.activeKey ? ' active' : ''));
    item.role = 'tab';
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
      const close = el('span', 'close', '×');
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
}

function renderChat(data) {
  const thread = document.getElementById('thread');
  document.getElementById('engine-name').textContent = data.engine || 'opencode';
  // The core's run mode for this chat; only a chat that has not started offers one.
  document.getElementById('mode-picker').hidden = !data.mode;
  document.getElementById('mode-name').textContent = data.mode || '';
  const detailsBtn = document.getElementById('details');
  if (detailsBtn) detailsBtn.disabled = !data.selectedRun;
  renderTabs(data);
  const same = chatEnvelope && chatEnvelope.runKey === data.runKey;
  if (!same) {
    if (chatEnvelope) tabScroll[chatEnvelope.runKey] = thread.scrollTop;
    saveDraft();
    const saved = vscode.getState() || {};
    document.getElementById('message').value = (saved.drafts || {})[data.runKey] || '';
    current = null; renderedState = ''; answers = Object.create(null);
    checkpointCompare = Object.create(null); checkpointResult = null; workingCompare = null;
    liveLog.clear();
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
  if (priorFinished !== finishedKey || (liveKey && !priorLive) || (!liveKey && priorLive)) {
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
    // Already showing: rebuilding on every read would drop a click on an example mid-press.
  } else {
    app.textContent = '';
    renderedState = '';
    if (data.busy) {
      app.classList.remove('muted');
      const card = el('div', 'decision-card');
      card.appendChild(el('div', 'speaker', 'AutoCode'));
      card.appendChild(el('div', 'message-content', data.waitingForStart ? 'Waiting to start. Complete setup to continue.' : 'Thinking…'));
      app.appendChild(card);
    } else if (welcoming) {
      app.classList.remove('muted');
      renderWelcome();
    } else {
      app.classList.add('muted');
    }
  }
  renderMeter(data);
  renderTelemetry(data.state && data.state.telemetry);
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

/** A reader scrolled up keeps their place; the Latest button names the step running now (#1711). */
function updateLatestCue(state) {
  const latest = document.getElementById('latest');
  if (!latest) return;
  const running = state && !state.done && state.needKind === 'continue' && state.working && state.chat
    && Array.isArray(state.chat.progress) ? state.chat.progress.find((line) => typeof line === 'string' && line) : '';
  const text = running ? '↓ Now: ' + running : '↓ Latest messages';
  if (latest.textContent !== text) latest.textContent = text;
}

function renderTelemetry(info) {
  const host = document.getElementById('model-info');
  const signature = JSON.stringify(info);
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
  if (configured.length) modelSection('configured-models', 'Configured models', 'Saved role assignments. Several stages can share a model; an assignment does not mean it has run.', configured);
  if (info && info.stagesUsed.length) modelSection('used-models', 'Models used', 'Recorded attempts, including retries and earlier model variants.', info.stagesUsed);
  else {
    host.appendChild(el('span', 'model-placeholder', info ? 'No model attempts reported yet.' : 'Configured models appear when a run starts.'));
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
  disclosure.open = initialWebviewState.modelDetailsOpen === true;
  disclosure.addEventListener('toggle', () => {
    vscode.setState({ ...(vscode.getState() || {}), modelDetailsOpen: disclosure.open });
  });
  document.getElementById('history').addEventListener('click', () => vscode.postMessage({ type: 'history' }));
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
  document.getElementById('stages-more').addEventListener('click', () => {
    stagesExpanded = !stagesExpanded;
    if (chatEnvelope) renderMeter(chatEnvelope);
  });
  document.getElementById('pause-run').addEventListener('click', () => {
    if (chatEnvelope && chatEnvelope.runKey) vscode.postMessage({ type: 'pause', runKey: chatEnvelope.runKey });
  });
  document.getElementById('engine-picker').addEventListener('click', () => vscode.postMessage({ type: 'engine' }));
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
