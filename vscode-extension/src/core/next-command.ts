/**
 * Reading of `view.needs` and `view.next_command` for a thin AutoCode client.
 *
 * Mirrors the headless conformance client (`scenarios/harness/conformance.py`,
 * issue #1012): the client takes the one action the status view names, and
 * fills only the decisions the view leaves to a person. Where the view names
 * no action it can take, that is a gap in the CLI or the status view, reported
 * as `ClientGap` and never worked around here.
 *
 * No VS Code API and no Node built-ins, so it is unit-testable in-process.
 */

import type { EffortNeed, IntegerCommandInput, StatusView } from "./view";

export class ClientGap extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ClientGap";
  }
}

/** Placeholders `next_command` leaves open for a decision the view does not supply. */
export const UNSUPPLIED = ["GOAL_FILE", "N", "WHAT CHANGED", "TOKEN", "RUN_DIR", "--explain"];

/** Flags that continue the run, and so take the client's engine and model options. */
export const ADVANCING = ["--no-chat", "--resume-paused"];

/** Needs that are an honest stop: a person decides, a client may not answer for them. */
export const PERSON_ONLY_KINDS = ["resume", "retry_job", "recover_source"];

/** Resolver scopes that stop the run because it could not continue safely. */
export const PERSON_ONLY_SCOPES = ["blocker", "operational_exhaustion"];

export interface NamedCommand {
  /** Issued tokens travel in the child's environment; `/proc/<pid>/cmdline` is world-readable. */
  env: Record<string, string>;
  /** Arguments after the `autocode` executable, with this run's own paths re-supplied by the caller. */
  argv: string[];
}

/**
 * Split a shell-style command line into words.
 *
 * `next_command` is formatted for a person to read and paste, so quoting has to
 * survive for the client to execute it without a shell.
 */
export function splitCommandLine(text: string): string[] {
  const words: string[] = [];
  let current = "";
  let started = false;
  let quote: "'" | '"' | null = null;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quote === "'") {
      if (ch === "'") quote = null;
      else current += ch;
      continue;
    }
    if (quote === '"') {
      if (ch === '"') quote = null;
      else if (ch === "\\" && i + 1 < text.length && (text[i + 1] === '"' || text[i + 1] === "\\")) current += text[++i];
      else current += ch;
      continue;
    }
    if (ch === "'" || ch === '"') {
      quote = ch;
      started = true;
      continue;
    }
    if (ch === "\\" && i + 1 < text.length) {
      current += text[++i];
      started = true;
      continue;
    }
    if (ch === " " || ch === "\t" || ch === "\n") {
      if (started) words.push(current);
      current = "";
      started = false;
      continue;
    }
    current += ch;
    started = true;
  }
  if (started) words.push(current);
  if (quote !== null) throw new ClientGap(`unbalanced quote in: ${text}`);
  return words;
}

/**
 * `view.next_command` as an argument vector plus the token environment.
 *
 * The executable, `--run-dir` and `--workspace` come off: the caller names its
 * own run, and a command that points at a different one is a gap rather than a
 * command to run against this run.
 */
export function parseNamedCommand(
  text: string,
  own: { runDir: string; workspace: string },
): NamedCommand {
  const words = splitCommandLine(text);
  const env: Record<string, string> = {};
  while (true) {
    const head = words[0];
    if (head === undefined || head === "autocode" || !/^[A-Za-z_][A-Za-z0-9_]*=/.test(head)) break;
    const [name, ...rest] = head.split("=");
    env[name as string] = rest.join("=");
    words.shift();
  }
  if (words.length === 0) throw new ClientGap(`no command in ${JSON.stringify(text)}`);
  if (words[0] !== "autocode") {
    throw new ClientGap(`next_command does not start with autocode: ${JSON.stringify(text)}`);
  }
  const argv = words.slice(1);
  for (const flag of ["--run-dir", "--workspace"] as const) {
    const index = argv.indexOf(flag);
    if (index < 0) continue;
    const named = argv[index + 1];
    if (named === undefined) throw new ClientGap(`${flag} has no value in next_command`);
    argv.splice(index, 2);
    const expected = flag === "--run-dir" ? own.runDir : own.workspace;
    if (named !== expected) {
      throw new ClientGap(`next_command names ${flag} ${named}, not this run's ${expected}`);
    }
  }
  return { env, argv };
}

/** A decision the named command leaves for a person: `--answer QUESTION=ANSWER`. */
export interface OpenDecision {
  questionId: string;
  placeholder: string;
  kind: "answer" | "model" | "effort" | "integer";
  input?: IntegerCommandInput;
}

/** A person's words for `QUESTION=PLACEHOLDER`, and the decision each is (autocode_client_actions.PLACEHOLDERS). */
const PLACEHOLDERS = new Map<string, "answer" | "model" | "effort">([["ANSWER", "answer"], ["MODEL", "model"], ["EFFORT", "effort"]]);

/**
 * The numbers a person may type into a core-named command (autocode_command_inputs, #2044): the flag,
 * the row id and label the core gives it, the key `used` is reported under, and whether the saved
 * current value can be fractional. The same table as tools/autocode_client_actions.INTEGER_INPUTS.
 */
const INTEGER_INPUTS: Record<string, [string, string, "used" | "elapsed", boolean]> = {
  "--max-seconds": ["max_seconds", "Total active time (seconds)", "elapsed", true],
  "--max-cost-usd": ["max_cost_usd", "Cost cap (whole USD)", "used", true],
  "--max-iterations": ["max_iterations", "Total iterations", "used", false],
  "--max-milestone-seconds": ["max_milestone_seconds", "Milestone active time (seconds)", "used", true],
  "--max-milestone-replans": ["max_milestone_replans", "Milestone replans", "used", false],
  "--no-progress-limit": ["no_progress_limit", "Unchanged implementation batches allowed", "used", false],
  "--planning-review-call-limit": ["planning_review_call_limit", "Total plan-review calls", "used", false],
  "--grant-recovery": ["grant_recovery", "Additional recoveries", "used", false],
};

function count(value: unknown, fractional: boolean): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value < Number.MAX_SAFE_INTEGER
    && (fractional || Number.isInteger(value));
}

/** `input` when it is a closed number offer for exactly `named`; otherwise a gap. */
export function checkInput(view: StatusView, input: IntegerCommandInput | undefined, named: NamedCommand): IntegerCommandInput {
  const gap = () => new ClientGap("Unsupported or stale numeric recovery command from AutoCode");
  const known = input && typeof input === "object" ? INTEGER_INPUTS[input.flag] : undefined;
  if (!input || !known) throw gap();
  const [id, label, usedKey, fractional] = known;
  const row = input as unknown as Record<string, unknown>;
  const current = row.current, used = row[usedKey];
  const wholeCurrent = fractional && input.flag !== "--max-seconds";
  const bounded = Number.isSafeInteger(input.minimum) && Number.isSafeInteger(input.maximum)
    && input.minimum >= 1 && input.minimum <= input.maximum;
  const limits = input.flag === "--grant-recovery"
    ? bounded && Number.isSafeInteger(used) && (used as number) >= 1 && input.minimum === 1 && input.maximum === used
    : bounded && count(used, fractional) && count(current, wholeCurrent) && input.maximum === Number.MAX_SAFE_INTEGER
      && input.minimum === Math.floor(Math.max(current, used)) + 1;
  const prefix = input.arg_index === 2 ? ["--resume-paused"] : [];
  const indexes = input.flag === "--planning-review-call-limit" ? [1, 2] : [2];
  const token = view.recovery?.token;
  if (!limits || input.id !== id || input.label !== label || input.kind !== "integer" || input.placeholder !== "N"
    || !indexes.includes(input.arg_index) || !count(current, wholeCurrent)
    || (current === 0 && input.flag !== "--max-iterations")
    || view.needs?.new_run_required || view.needs?.edit_required
    || typeof token !== "string" || !token
    || Object.keys(named.env).length !== 1 || named.env.AUTOCODE_EXPECTED_RECOVERY_TOKEN !== token
    || JSON.stringify(named.argv) !== JSON.stringify([...prefix, input.flag, "N", "--expected-recovery-token", "-"])) {
    throw gap();
  }
  return input;
}

function offered(view: StatusView): IntegerCommandInput | null {
  const rows = view.needs?.command_inputs;
  if (rows === undefined) return null;
  if (!Array.isArray(rows) || rows.length !== 1 || !rows[0] || typeof rows[0] !== "object") {
    throw new ClientGap("Unsupported or stale numeric recovery command from AutoCode");
  }
  return rows[0];
}

/** Validate the closed typed slot next_command leaves open; a row naming its own command is a separate offer. */
export function commandInputs(view: StatusView, named: NamedCommand): IntegerCommandInput[] {
  const input = offered(view);
  if (!input || input.command !== undefined) return [];
  return [checkInput(view, input, named)];
}

/** The core's separate raise of the bound holding the run, when next_command is not that raise (#2044). */
export function boundOffer(
  view: StatusView,
  own: { runDir: string; workspace: string },
): { input: IntegerCommandInput; named: NamedCommand } | null {
  const input = offered(view);
  if (!input || input.command === undefined) return null;
  if (typeof input.command !== "string" || !input.command) {
    throw new ClientGap("Unsupported or stale numeric recovery command from AutoCode");
  }
  const named = parseNamedCommand(input.command, own);
  return { input: checkInput(view, input, named), named };
}

/** Digits supplied by the person, within the advertised finite integer bounds. */
export function integerValue(value: string | undefined, input: IntegerCommandInput): string {
  const number = typeof value === "string" && /^[0-9]+$/.test(value) ? Number(value) : NaN;
  if (!Number.isSafeInteger(number) || number < input.minimum || number > input.maximum) {
    throw new ClientGap(`Enter a whole number from ${input.minimum} to ${input.maximum} for ${input.label}`);
  }
  return String(number);
}

/** The decisions the command needs before it can run, in order. */
export function openDecisions(argv: string[], inputs: IntegerCommandInput[] = []): OpenDecision[] {
  const found: OpenDecision[] = inputs.map((input) => ({ questionId: input.id, placeholder: input.placeholder, kind: "integer", input }));
  for (const word of argv) {
    const [question, separator, placeholder] = splitOnce(word, "=");
    const kind = separator ? PLACEHOLDERS.get(placeholder) : undefined;
    if (!kind) continue;
    found.push({ questionId: question, placeholder, kind });
  }
  return found;
}

/**
 * The first placeholder the status view cannot supply, or null.
 *
 * Unlike a pending decision this is never the client's to fill, so a preview
 * names it the same way running the command would refuse it.
 */
export function findUnsupplied(argv: string[], inputs: IntegerCommandInput[] = []): string | null {
  return argv.find((word, index) => UNSUPPLIED.includes(word) && !inputs.some((input) => input.arg_index === index && input.placeholder === word)) ?? null;
}

/**
 * Replace the `QUESTION=ANSWER` / `QUESTION=MODEL` / `QUESTION=EFFORT` placeholders with a person's words.
 *
 * A placeholder the view cannot supply is a gap: `answers` only ever carries
 * what a person chose, so a client never invents a decision to get past it.
 * An effort is one of the levels `effort` (the view's `needs.effort`) declares.
 */
export function applyDecisions(
  argv: string[],
  answers: Record<string, string>,
  inputs: IntegerCommandInput[] = [],
  effort?: EffortNeed,
): string[] {
  return argv.map((word, index) => {
    const input = inputs.find((row) => row.arg_index === index && row.placeholder === word);
    if (input) return integerValue(answers[input.id], input);
    if (UNSUPPLIED.includes(word)) {
      throw new ClientGap(`the command leaves ${word} open, which the status view does not supply`);
    }
    const [question, separator, placeholder] = splitOnce(word, "=");
    if (!separator || !PLACEHOLDERS.has(placeholder)) return word;
    const value = answers[question];
    if (value === undefined) {
      throw new ClientGap(`${question} is a decision for a person; none was given`);
    }
    if (placeholder === "EFFORT") return `${question}=${effortValue(effort, question, value)}`;
    return `${question}=${value}`;
  });
}

/** The levels an `effort-ROLE=EFFORT` slot may take: the ones its model declares (#2057). */
export function effortLevels(effort: EffortNeed | undefined, question: string): string[] {
  const declared = effort?.declared;
  if (effort?.question_id !== question || !Array.isArray(declared) || !declared.length || !declared.every((level) => typeof level === "string" && level)) {
    throw new ClientGap(`${question} asks for an effort the status view does not describe`);
  }
  return [...declared];
}

/** A level a person chose, one its model declares. */
export function effortValue(effort: EffortNeed | undefined, question: string, value: string): string {
  const levels = effortLevels(effort, question);
  const chosen = value.trim().toLowerCase();
  if (!levels.includes(chosen)) throw new ClientGap(`Choose one of ${levels.join(", ")}`);
  return chosen;
}

/** Whether the command continues the run, so it carries the client's options. */
export function isAdvancing(argv: string[]): boolean {
  return argv.some((word) => ADVANCING.includes(word));
}

/**
 * Whether this need is an honest stop a person has to decide.
 *
 * A `blocker` or `operational_exhaustion` request means Resolver could not
 * continue safely; proposing a default for it would be the client choosing
 * what AutoCode stopped to ask.
 */
export function leavesForPerson(need: { kind?: string; resolver_scope?: string } | null): boolean {
  if (!need) return false;
  if (PERSON_ONLY_KINDS.includes(need.kind ?? "")) return true;
  return need.kind === "answer" && PERSON_ONLY_SCOPES.includes(need.resolver_scope ?? "");
}

/** A clean requested pause still waits for a person; its Resume click needs no second dialog. */
export function requiresConfirmation(need: { kind?: string; resolver_scope?: string; confirmation_required?: boolean } | null): boolean {
  return leavesForPerson(need) && !(need?.kind === "resume" && need.confirmation_required === false);
}

/** The action a person takes on their own initiative while a stage runs. */
export function retryAction(need: Record<string, unknown> | null): string | null {
  const value = need?.retry_action;
  return typeof value === "string" && value.length > 0 ? value : null;
}

function splitOnce(text: string, separator: string): [string, boolean, string] {
  const index = text.indexOf(separator);
  if (index < 0) return [text, false, ""];
  return [text.slice(0, index), true, text.slice(index + 1)];
}
