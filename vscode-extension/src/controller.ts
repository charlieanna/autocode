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
import { invoke, readStatus, stream, tail, type CliResult } from "./core/cli";
import {
  applyDecisions,
  findUnsupplied,
  isAdvancing,
  openDecisions,
  parseNamedCommand,
  ClientGap,
  type OpenDecision,
} from "./core/next-command";
import { readRegistryList, readStatusPayload, approvalToken, type RegistryList, type StatusView } from "./core/view";
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

  private get settings(): Settings {
    return readSettings();
  }

  /** The per-user run pointers. Read-only: it starts nothing and changes nothing. */
  async list(): Promise<RegistryList> {
    const result = await invoke(this.settings, ["registry", "list", "--json"]);
    if (result.code !== 0) throw new Error(tail(result));
    return readRegistryList(result.stdout);
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
  async start(workspace: string, brief: string): Promise<RunRef> {
    const runDir = freshRunDir(workspace);
    const args = [brief, "--in-place", "--no-chat", "--new-run-dir", runDir, ...this.settings.runOptions];
    this.log.show(true);
    this.log.appendLine(`$ autocode ${summarize(brief)} --in-place --no-chat --new-run-dir ${runDir}`);
    const result = await stream(this.settings, args, {}, (chunk) => this.log.append(chunk));
    if (result.code !== 0 && result.code !== 2) throw new Error(tail(result));
    this.log.appendLine(`[autocode exited ${result.code}]`);
    return { workspace, runDir };
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
      const parsed = parseNamedCommand(shown, { runDir: run.runDir, workspace: run.workspace });
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
    return {
      env,
      shown,
      advancing: isAdvancing(argv),
      decisions: openDecisions(argv),
      unsupplied: findUnsupplied(argv),
    };
  }

  /** The named action with a person's decisions filled in, ready to run. */
  prepare(view: StatusView, run: RunRef, answers: Record<string, string>): PreparedAction {
    const { argv, env } = this.named(view, run);
    const filled = applyDecisions(argv, answers);
    const advancing = isAdvancing(filled);
    return { argv: advancing ? [...filled, ...this.settings.runOptions] : filled, env, advancing };
  }

  /**
   * Run the named action.
   *
   * Answers and approvals launch no model on their own; only a command that
   * advances the run carries the client's engine and model options.
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
    const action = this.prepare(view, run, answers);
    const args = [...action.argv, "--workspace", run.workspace, "--run-dir", run.runDir];
    this.log.appendLine(`$ autocode ${args.map(redact).join(" ")}`);
    const result = await stream(this.settings, args, action.env, (chunk) => this.log.append(chunk));
    this.log.appendLine(`[autocode exited ${result.code}]`);
    if (result.rejected) throw new Error(`AutoCode rejected the command:\n${tail(result)}`);
    return result;
  }

  /** The command a person can paste into a terminal instead of letting the client run it. */
  commandText(view: StatusView, run: RunRef): string {
    return this.named(view, run).shown || `autocode --workspace ${run.workspace} --run-dir ${run.runDir} --no-chat`;
  }
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
