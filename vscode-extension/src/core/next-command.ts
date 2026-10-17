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
  kind: "answer" | "model";
}

/** The decisions the command needs before it can run, in order. */
export function openDecisions(argv: string[]): OpenDecision[] {
  const found: OpenDecision[] = [];
  for (const word of argv) {
    const [question, separator, placeholder] = splitOnce(word, "=");
    if (!separator || (placeholder !== "ANSWER" && placeholder !== "MODEL")) continue;
    found.push({ questionId: question, placeholder, kind: placeholder === "MODEL" ? "model" : "answer" });
  }
  return found;
}

/**
 * The first placeholder the status view cannot supply, or null.
 *
 * Unlike a pending decision this is never the client's to fill, so a preview
 * names it the same way running the command would refuse it.
 */
export function findUnsupplied(argv: string[]): string | null {
  return argv.find((word) => UNSUPPLIED.includes(word)) ?? null;
}

/**
 * Replace the `QUESTION=ANSWER` / `QUESTION=MODEL` placeholders with a person's words.
 *
 * A placeholder the view cannot supply is a gap: `answers` only ever carries
 * what a person chose, so a client never invents a decision to get past it.
 */
export function applyDecisions(argv: string[], answers: Record<string, string>): string[] {
  return argv.map((word) => {
    if (UNSUPPLIED.includes(word)) {
      throw new ClientGap(`the command leaves ${word} open, which the status view does not supply`);
    }
    const [question, separator, placeholder] = splitOnce(word, "=");
    if (!separator || (placeholder !== "ANSWER" && placeholder !== "MODEL")) return word;
    const value = answers[question];
    if (value === undefined) {
      throw new ClientGap(`${question} is a decision for a person; none was given`);
    }
    return `${question}=${value}`;
  });
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
