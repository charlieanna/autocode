/**
 * Driving one AutoCode run through the CLI only.
 *
 * The rule from issue #884: the extension may do nothing the headless
 * conformance client (`scenarios/harness/conformance.py`, #1012) cannot do
 * through `TaskRun` and the status view. So every action here starts from
 * `view.next_command`, and a command the view cannot fully name is reported as
 * a gap in AutoCode rather than filled in with a guess.
 */

import * as vscode from "vscode";
import { realpath } from "node:fs/promises";
import { invoke, readStatus, stream, tail, type CliResult } from "./core/cli";
import {
  applyDecisions,
  changesetAction,
  changesetStatusArgs,
  commandInputs,
  findUnsupplied,
  isAdvancing,
  openDecisions,
  parseNamedCommand,
  ClientGap,
  type OpenDecision,
} from "./core/next-command";
import { readChangesetSummary, readRegistryList, readStatusPayload, approvalToken, planForApproval, type ChangesetSummary, type CheckpointAction, type RegistryList, type StatusView } from "./core/view";
import { FULL_START_ARGS, readRunModes, type RunModes } from "./core/modes";
import { readSettings, type Settings } from "./settings";

export interface RunRef {
  workspace: string;
  runDir: string;
}

/** What the view named, worked out before anything runs so a person can see it. */
export interface PreparedAction {
  argv: string[];
  env: Record<string, string>;
  advancing: boolean;
}

/** A preview of the named action: what it would run, and what it still needs from a person. */
export interface ActionPlan {
  env: Record<string, string>;
  advancing: boolean;
  decisions: OpenDecision[];
  /** The command as AutoCode formatted it, for copying into a terminal. */
  shown: string;
  /** A placeholder the view cannot supply: a gap in AutoCode, never the client's to fill. */
  unsupplied: string | null;
}

export class RunController {
  constructor(private readonly log: vscode.OutputChannel) {}

  private readonly polling = new Map<string, { generation: number; request: Promise<StatusView> }>();
  private readonly mutations = new Map<string, number>();

  private get settings(): Settings {
    return readSettings();
  }

  private runKey(run: RunRef): string {
    return JSON.stringify([run.workspace, run.runDir]);
  }

  private pollKey(run: RunRef, settings: Settings): string {
    return JSON.stringify([this.runKey(run), settings.command, settings.prefix, settings.cwd ?? null,
      Object.entries(settings.env ?? {}).sort(([left], [right]) => left.localeCompare(right))]);
  }

  /** UI polling only: share a pending read, never a completed status or action preflight. */
  pollStatus(run: RunRef): Promise<StatusView> {
    const runKey = this.runKey(run);
    const generation = this.mutations.get(runKey) ?? 0;
    const key = this.pollKey(run, this.settings);
    const pending = this.polling.get(key);
    if (pending?.generation === generation) return pending.request;
    const superseded = () => (this.mutations.get(runKey) ?? 0) !== generation || this.pollKey(run, this.settings) !== key;
    const retire = () => {
      if (this.polling.get(key)?.request === request) this.polling.delete(key);
    };
    const refresh = () => {
      // Retire before joining a replacement: settings A -> B -> A must not
      // let the old A and B promises join each other in a cycle.
      retire();
      return this.pollStatus(run);
    };
    const request: Promise<StatusView> = this.status(run).then((view) => {
      // A command can finish while an older poll is still reading. Its old
      // result must not replace the new boundary or its decision/token offer.
      return superseded() ? refresh() : view;
    }, (error: unknown) => {
      if (superseded()) return refresh();
      throw error;
    }).finally(retire);
    this.polling.set(key, { generation, request });
    return request;
  }

  private async mutating<T>(runs: RunRef[], command: () => Promise<T>): Promise<T> {
    const keys = [...new Set(runs.map((run) => this.runKey(run)))];
    const invalidate = () => {
      for (const key of keys) this.mutations.set(key, (this.mutations.get(key) ?? 0) + 1);
    };
    invalidate();
    try {
      return await command();
    } finally {
      invalidate();
    }
  }

  /** The per-user run pointers. Read-only: it starts nothing and changes nothing. */
  async list(): Promise<RegistryList> {
    const result = await invoke(this.settings, ["registry", "list", "--json"]);
    if (result.code !== 0) throw new Error(tail(result));
    return readRegistryList(result.stdout);
  }

  /** The run modes the core offers for a new chat. Read-only: it starts nothing. */
  async modes(): Promise<RunModes> {
    const result = await invoke(this.settings, ["modes", "list", "--json"]);
    if (result.code !== 0) throw new Error(tail(result));
    return readRunModes(result.stdout);
  }

  /** `autocode --status`: a read that never rewrites the checkpoint or launches a provider. */
  status(run: RunRef): Promise<StatusView> {
    return readStatus(this.settings, run.workspace, run.runDir, readStatusPayload);
  }

  /**
   * Start a run in the workspace and keep the CLI's own output.
   *
   * The extension names the fresh run directory so it can watch the run it
   * created, not whichever one a parallel session happened to start. AutoCode
   * refuses an existing directory or a symlink before a provider launches.
   */
  async start(workspace: string, brief: string, created?: (run: RunRef) => void, startArgs: readonly string[] = FULL_START_ARGS): Promise<RunRef> {
    // The core resolves the workspace before validating the run destination
    // (autocode_run_destination.reserve); a symlinked folder such as macOS
    // /tmp must not make the two paths disagree (#1555).
    const runWorkspace = await realpath(workspace);
    const runDir = freshRunDir(runWorkspace);
    const settings = this.settings;
    // The chosen run mode's flags, exactly as `modes list` named them; without
    // one, sidebar conversations gather and confirm requirements before planning.
    // Creation flags stay off later actions, which use the run's saved flow.
    const args = [brief, "--in-place", "--no-chat", ...startArgs, ...settings.runOptions, "--workspace", runWorkspace, "--new-run-dir", runDir];
    this.log.appendLine(`$ autocode ${summarize(brief)} --in-place --no-chat --new-run-dir ${runDir}`);
    created?.({ workspace: runWorkspace, runDir });
    const result = await this.mutating([{ workspace: runWorkspace, runDir }], () =>
      stream({ ...settings, cwd: runWorkspace }, args, {}, (chunk) => this.log.append(chunk)));
    if (result.rejected || (result.code !== 0 && result.code !== 2)) throw new Error(tail(result));
    this.log.appendLine(`[autocode exited ${result.code}]`);
    return { workspace: runWorkspace, runDir };
  }

  /**
   * The action `view.needs` names, as AutoCode's own words.
   *
   * An active run has no `next_command`; its `continue` need's action is simply
   * to proceed, which is `--no-chat`.
   */
  private named(view: StatusView, run: RunRef): { argv: string[]; env: Record<string, string>; shown: string } {
    const shown = view.next_command ?? "";
    if (shown) {
      const parsed = parseNamedCommand(shown, effective(view, run));
      return { argv: parsed.argv, env: parsed.env, shown };
    }
    if (view.needs?.kind === "continue") return { argv: ["--no-chat"], env: {}, shown: "" };
    throw new ClientGap(`needs ${JSON.stringify(view.needs?.kind ?? null)} at ${view.status} names no next_command`);
  }

  /**
   * What taking the named action would mean, without taking it.
   *
   * A pending decision is not a gap: it is what the run stopped to ask a person,
   * and the panel turns it into an input. A placeholder the view cannot supply
   * is a gap, and is reported as one here rather than filled in.
   */
  plan(view: StatusView, run: RunRef): ActionPlan {
    const { argv, env, shown } = this.named(view, run);
    const inputs = commandInputs(view, { argv, env });
    return {
      env,
      shown,
      advancing: isAdvancing(argv),
      decisions: openDecisions(argv, inputs),
      unsupplied: findUnsupplied(argv, inputs),
    };
  }

  /** The named action with a person's decisions filled in, ready to run. */
  prepare(view: StatusView, run: RunRef, answers: Record<string, string>): PreparedAction {
    const { argv, env } = this.named(view, run);
    const filled = applyDecisions(argv, answers, commandInputs(view, { argv, env }), view.needs?.effort);
    const advancing = isAdvancing(filled);
    // Existing runs own their saved backend, models and limits. Changing the
    // Connections selection for a new chat must not reroute a paused run.
    return { argv: filled, env, advancing };
  }

  /**
   * Run the named action.
   *
   * Answers and approvals launch no model on their own. Advancing uses the
   * run's saved engine and model options.
   *
   * An approval is bound to the plan token a person was shown. A plan revised
   * while the panel sat open has a new token, and approving it unseen is not
   * what the click meant (`docs/task-run.md`: bind the token to `needs.token`).
   */
  async take(
    view: StatusView,
    run: RunRef,
    answers: Record<string, string> = {},
    shownToken?: string | null,
  ): Promise<CliResult> {
    const asked = approvalToken(view);
    if (asked !== null && shownToken !== asked) {
      throw new ClientGap("the plan changed since it was displayed; review the new plan before approving it");
    }
    if (view.needs?.kind === "approve_plan" && !planForApproval(view)) {
      throw new ClientGap("the complete plan is unavailable; review the plan before approving it");
    }
    const action = this.prepare(view, run, answers);
    const own = effective(view, run);
    const args = [...action.argv, "--workspace", own.workspace, "--run-dir", own.runDir];
    this.log.appendLine(`$ autocode ${args.map(redact).join(" ")}`);
    const result = await this.mutating([run, own], () =>
      stream(this.settings, args, action.env, (chunk) => this.log.append(chunk)));
    this.log.appendLine(`[autocode exited ${result.code}]`);
    if (result.rejected) throw new Error(`AutoCode rejected the command:\n${tail(result)}`);
    if (result.code !== 0 && result.code !== 2) throw new Error(tail(result));
    return result;
  }

  /**
   * `changeset status --json` for a run whose view lists linked runs; null when it is in no change
   * set. Read-only: it reads each run's status view and changes nothing (#2066).
   */
  async changeset(view: StatusView, run: RunRef): Promise<ChangesetSummary | null> {
    const args = changesetStatusArgs(view, effective(view, run));
    if (!args) return null;
    const result = await invoke(this.settings, args);
    if (result.code !== 0) {
      try {
        readChangesetSummary(result.stdout);
      } catch (error) {
        throw new Error(error instanceof Error ? error.message : tail(result));
      }
      throw new Error(tail(result));
    }
    return readChangesetSummary(result.stdout);
  }

  /**
   * Take the change set's named set approval or joint check. The set approval is bound to the
   * set token a person was shown with every run's plan: a plan revised since has a new token, and
   * the client refuses before the core would.
   */
  async takeChangeset(view: StatusView, run: RunRef, key: "approval" | "verification", shownToken?: string | null): Promise<CliResult> {
    const summary = await this.changeset(view, run);
    if (!summary) throw new ClientGap("this run is in no change set");
    if (key === "approval" && summary.approval.set_token !== shownToken) {
      throw new ClientGap("a plan in the change set changed since it was displayed; review the plans again");
    }
    const args = changesetAction(summary, key);
    this.log.appendLine(`$ autocode ${args.join(" ")}`);
    const runs = summary.runs.map((row) => ({ workspace: row.workspace, runDir: row.run_dir }));
    const result = await this.mutating([run, ...runs], () => stream(this.settings, args, {}, (chunk) => this.log.append(chunk)));
    this.log.appendLine(`[autocode exited ${result.code}]`);
    if (result.rejected || result.code !== 0) throw new Error(tail(result));
    return result;
  }

  /** The command a person can paste into a terminal instead of letting the client run it. */
  commandText(view: StatusView, run: RunRef): string {
    const own = effective(view, run);
    return this.named(view, run).shown || `autocode --workspace ${own.workspace} --run-dir ${own.runDir} --no-chat`;
  }

  /** The optional message action is named by the core, never inferred from chat text. */
  async sendMessage(view: StatusView, run: RunRef, text: string, shownContext: string): Promise<void> {
    if (!text.trim()) throw new ClientGap("Enter a message first");
    const input = view.chat?.input;
    if (!input || view.chat?.context !== shownContext) {
      throw new ClientGap("This conversation changed; review its current state before sending");
    }
    const parsed = parseNamedCommand(input.next_command, effective(view, run));
    const flag = input.kind === "feedback" ? "--feedback" : input.kind === "follow_up" ? "--follow-up" : null;
    const need = view.needs;
    const expected = ["--resolver-request", need?.resolver_request_id, "--resolver-token", "-",
      "--resolver-response", "provide_information", "--resolver-message", "MESSAGE"];
    const operational = input.kind === "resolver_response" && need?.kind === "answer"
      && ["blocker", "operational_exhaustion"].includes(need.resolver_scope ?? "") && !need.route
      && !!need.resolver_request_id && !!need.resolver_token
      && JSON.stringify(parsed.argv) === JSON.stringify(expected)
      && Object.keys(parsed.env).length === 1 && parsed.env.AUTOCODE_RESOLVER_TOKEN === need.resolver_token;
    // Fill only the advertised text. Operational replies carry the current
    // request binding; they cannot grant a retry or resume a pause themselves.
    if (!operational && (!flag || parsed.argv.length !== 2 || parsed.argv[0] !== flag || parsed.argv[1] !== "MESSAGE"
      || Object.keys(parsed.env).length)) throw new ClientGap("Unsupported message action from AutoCode");
    const own = effective(view, run);
    const result = await this.mutating([run, own], () => stream({ ...this.settings, cwd: run.workspace },
      [...parsed.argv.slice(0, -1), text, "--workspace", own.workspace, "--run-dir", own.runDir], parsed.env));
    if (result.code !== 0) throw new Error(tail(result));
  }

  /** The pause the view named (#1598), checked against its exact grammar, with this request's id. */
  async pause(run: RunRef, requestId: string): Promise<void> {
    const view = await this.status(run);
    const command = view.pause?.next_command;
    if (!view.pause || view.pause.requested || !Array.isArray(command)) {
      throw new ClientGap("AutoCode offers no pause for this run right now");
    }
    const own = effective(view, run);
    const expected = ["intervention", "submit", "--workspace", own.workspace, "--run-dir", own.runDir,
      "--request-id", "REQUEST_ID", "--kind", "pause", "--json"];
    if (JSON.stringify(command) !== JSON.stringify(expected)) throw new ClientGap("Unsupported pause action from AutoCode");
    const result = await this.mutating([run, own], () =>
      invoke(this.settings, command.map((word) => (word === "REQUEST_ID" ? requestId : word))));
    if (result.code !== 0) throw new Error(tail(result));
  }

  /** The checkpoint action the view named, checked against the exact grammar before anything runs. */
  private namedCheckpointAction(view: StatusView, run: RunRef, checkpointId: string, actionId: "compare" | "restore"): CheckpointAction {
    const row = (view.code_checkpoints?.rows ?? []).find((item) => item.id === checkpointId);
    const action = row?.actions?.find((item) => item.id === actionId);
    if (!row || !action) {
      throw new ClientGap(`AutoCode named no ${actionId} action for saved code change ${checkpointId}`);
    }
    const flag = actionId === "compare" ? "--compare" : "--restore";
    const own = effective(view, run);
    const expected = ["checkpoint", "--workspace", own.workspace, "--run-dir", own.runDir, flag, checkpointId,
      ...(actionId === "restore" ? ["--expected-token", "-", "--request-id", "REQUEST_ID"] : [])];
    if (JSON.stringify(action.next_command) !== JSON.stringify(expected)) {
      throw new ClientGap("Unsupported checkpoint action from AutoCode");
    }
    return action;
  }

  /** The named compare: what changed since the saved step, plus the token a restore must carry. */
  async compareCheckpoint(run: RunRef, checkpointId: string): Promise<CheckpointComparison> {
    const view = await this.status(run);
    const action = this.namedCheckpointAction(view, run, checkpointId, "compare");
    const result = await invoke(this.settings, action.next_command);
    if (result.code !== 0) throw new Error(tail(result));
    return JSON.parse(result.stdout) as CheckpointComparison;
  }

  /** The view's named, display-only diff of what the running Builder has changed so far. */
  async workingDiff(run: RunRef): Promise<WorkingComparison> {
    const view = await this.status(run);
    const action = view.code_checkpoints?.working;
    if (!action) throw new ClientGap("AutoCode offers no live diff right now. It is offered while the Builder works.");
    const own = effective(view, run);
    const expected = ["checkpoint", "--workspace", own.workspace, "--run-dir", own.runDir, "--working"];
    if (JSON.stringify(action.next_command) !== JSON.stringify(expected)) {
      throw new ClientGap("Unsupported working-diff action from AutoCode");
    }
    const result = await invoke(this.settings, expected);
    if (result.code !== 0) throw new Error(tail(result));
    return JSON.parse(result.stdout) as WorkingComparison;
  }

  /**
   * The named restore: a person's confirmed decision, bound to the token a
   * fresh compare returned and a stable request id that makes replay safe.
   */
  async restoreCheckpoint(run: RunRef, checkpointId: string, expectedToken: string, requestId: string): Promise<CheckpointRestoreResult> {
    const view = await this.status(run);
    const action = this.namedCheckpointAction(view, run, checkpointId, "restore");
    const argv = action.next_command.map((word) => (word === "REQUEST_ID" ? requestId : word));
    const result = await this.mutating([run, effective(view, run)], () =>
      invoke(this.settings, argv, { AUTOCODE_CHECKPOINT_EXPECTED_TOKEN: expectedToken }));
    if (result.code !== 0) throw new Error(tail(result));
    return JSON.parse(result.stdout) as CheckpointRestoreResult;
  }
}

/** The payload `checkpoint --working` prints: display only, with no token. */
export interface WorkingComparison {
  since?: string;
  changed_files?: string[];
  patch?: string;
  truncated?: boolean;
  compared_at?: string;
}

/** The payload `checkpoint --compare` prints. */
export interface CheckpointComparison {
  checkpoint_id?: string;
  expected_token?: string;
  changed_files?: string[];
  patch?: string;
  truncated?: boolean;
  blocked_reason?: string | null;
  compared_at?: string;
  source_branch?: string | null;
}

/** The payload `checkpoint --restore` prints. */
export interface CheckpointRestoreResult {
  operation?: string;
  checkpoint_id?: string;
  workspace?: string;
  run_dir?: string;
  branch?: string;
  preserved_branch?: string;
  status?: string;
  message?: string;
}

/**
 * The run's effective location as the view names it.
 *
 * A chat that started in a plain, non-empty folder names its run under that
 * folder; AutoCode bootstraps a project inside it and the view reports the
 * project's paths. Named commands carry those, so compare against them.
 */
function effective(view: StatusView, run: RunRef): { workspace: string; runDir: string } {
  return { workspace: view.workspace ?? run.workspace, runDir: view.run_dir ?? run.runDir };
}

/** AutoCode's own run-directory shape, under the workspace's runs directory. */
function freshRunDir(workspace: string): string {
  const stamp = new Date().toISOString().slice(0, 19).replace(/[T:]/g, "-").replace(/-/g, "").slice(0, 8);
  const time = new Date().toTimeString().slice(0, 8).replace(/:/g, "");
  const random = Math.random().toString(16).slice(2, 8);
  return `${workspace}/.autocode/runs/${stamp}-${time}-vscode-${random}`;
}

function summarize(brief: string): string {
  const oneLine = brief.replace(/\s+/g, " ").trim();
  const text = oneLine.length > 60 ? `${oneLine.slice(0, 57)}...` : oneLine;
  return JSON.stringify(text);
}

/** Keep issued tokens out of the log the same way the CLI keeps them out of argv. */
function redact(word: string): string {
  return /TOKEN/i.test(word) ? "-redacted-" : word;
}
