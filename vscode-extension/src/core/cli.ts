/**
 * The one place the extension starts the `autocode` CLI.
 *
 * Every call is an argument array with no shell, as `docs/registry-api.md`
 * requires of a client, and issued tokens go in the child's environment rather
 * than argv because a process command line is world-readable.
 */

import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { createHash } from "node:crypto";
import { resolve } from "node:path";
import { StringDecoder } from "node:string_decoder";

/** What a CLI call returned. Mirrors the fields `TaskRun` keeps for a caller. */
export interface CliResult {
  code: number | null;
  stdout: string;
  stderr: string;
  /** AutoCode exits 2 both for a pause awaiting input and for a rejection; the message tells them apart. */
  rejected: boolean;
}

export interface CliOptions {
  /** Path to the `autocode` executable, or a command that runs it from a checkout. */
  command: string;
  /** Leading arguments before AutoCode's own, for `python tools/autocode.py` style launches. */
  prefix: string[];
  cwd?: string;
  env?: Record<string, string>;
}

export function buildArgv(options: CliOptions, args: string[]): { file: string; args: string[] } {
  return { file: options.command, args: [...options.prefix, ...args] };
}

/** Run one CLI call and collect its output. */
export function invoke(options: CliOptions, args: string[], env: Record<string, string> = {}, timeoutMs?: number): Promise<CliResult> {
  return stream(options, args, env, undefined, EXIT_GRACE_MS, timeoutMs);
}

/**
 * A mutating CLI action exceeded its deadline and was stopped; its result is unknown.
 *
 * Its message is shown to a person as it is (the Stage Models panel, a chat's error line), so it
 * names the deadline only. The command it stopped stays on the error for logs, never in the text.
 */
export class ActionDeadlineExceeded extends Error {
  constructor(readonly timeoutMs: number, readonly file: string, readonly args: string[]) {
    super(`AutoCode did not finish within ${formatDeadline(timeoutMs)} and was stopped, so its result is unknown.`);
    this.name = "ActionDeadlineExceeded";
  }
}

function formatDeadline(timeoutMs: number): string {
  const seconds = Math.max(1, Math.round(timeoutMs / 1000));
  return seconds < 60 || seconds % 60 ? `${seconds}s` : `${seconds / 60} minute${seconds === 60 ? "" : "s"}`;
}

/** A read's UI deadline passed; its configured CLI has not yet been collected. */
export class ReadDeadlineExceeded extends Error {
  constructor(timeoutMs: number, readonly collected: Promise<void>, pending = true) {
    super(`AutoCode did not finish this read within ${timeoutMs / 1000}s.${pending ? " Waiting for its reader to finish before reading again." : ""}`);
    this.name = "ReadDeadlineExceeded";
  }
}

interface ReadLease {
  error?: ReadDeadlineExceeded;
  child?: ChildProcessWithoutNullStreams;
  collected: Promise<void>;
  release(): void;
}

// Healthy preflights remain fresh. Only an expired, uncollected reader prevents
// another identical launch; a UI timeout never proves a wrapper's child stopped.
const readLeases = new Map<string, Set<ReadLease>>();

/** A public read with a UI deadline, retaining its reader until natural exit. */
export function invokeRead(options: CliOptions, args: string[], env: Record<string, string> = {}, timeoutMs = STATUS_TIMEOUT_MS): Promise<CliResult> {
  const environment = launchEnv({ ...(options.env ?? {}), ...env });
  const key = createHash("sha256").update(JSON.stringify([buildArgv(options, args), resolve(options.cwd ?? process.cwd()),
    Object.entries(environment).filter(([, value]) => value !== undefined).sort(([left], [right]) => left.localeCompare(right))])).digest("hex");
  const leases = readLeases.get(key) ?? new Set<ReadLease>();
  const held = [...leases].find((lease) => lease.error);
  if (held) return Promise.reject(held.error);
  let collected!: () => void;
  const lease: ReadLease = {
    collected: new Promise<void>((done) => { collected = done; }),
    release: () => {
      leases.delete(lease);
      lease.child = undefined;
      if (!leases.size && readLeases.get(key) === leases) readLeases.delete(key);
      collected();
    },
  };
  leases.add(lease);
  readLeases.set(key, leases);
  return execute(options, args, env, undefined, EXIT_GRACE_MS, timeoutMs, lease, environment);
}

/**
 * Run one CLI call, reporting output as it arrives.
 *
 * A rejection is a `usage:` or `Input rejected:` exit 2, which must not be
 * mistaken for a pause awaiting input (docs/task-run.md).
 */
/** After the child exits, how long its pipes may stay open (a detached
 * grandchild can hold them forever) before the action is complete anyway. */
export const EXIT_GRACE_MS = 2000;

/** UI read deadline; failing the read does not establish reader collection. */
export const STATUS_TIMEOUT_MS = 30_000;

/** A stage list lists the backend's models: seconds on OpenCode. After this the read fails and says so. */
export const STAGES_TIMEOUT_MS = 60_000;

/** A public read that takes longer than this writes its phases to the read log (#3849). */
export const READ_SLOW_MS = 5000;

/** Where slow and deadline-missing reads write their phases: the extension's output channel. */
export interface ReadLog {
  appendLine(line: string): void;
}

let readLog: ReadLog | undefined;

/** Set (or, with undefined, clear) the log that slow reads write their phases to. */
export function setReadLog(sink: ReadLog | undefined): void {
  readLog = sink;
}

/**
 * When each phase of one public read happened, in Date.now() milliseconds.
 *
 * A slow read is diagnosable only by which phase took the time: the process start, the
 * core's first byte, its exit, or the pipes closing after it (#3849).
 */
class ReadPhases {
  private readonly started = Date.now();
  private spawned?: number;
  private firstOutput?: number;
  private exited?: number;
  private closed?: number;
  private delivered?: number;
  private missed?: number;
  private reported = false;

  constructor(private readonly args: string[]) {}

  spawn(): void { this.spawned = Date.now(); }
  output(): void { this.firstOutput ??= Date.now(); }
  exit(): void { this.exited ??= Date.now(); }

  /** The pipes closed: a reader that missed its deadline is collected now, so say how it ended. */
  close(): void {
    this.closed ??= Date.now();
    this.collected();
  }

  /** The read's result reached its caller; a slow one writes its phases. */
  deliver(): void {
    this.delivered = Date.now();
    if (this.delivered - this.started > READ_SLOW_MS) {
      this.write(`AutoCode read slow (${this.delivered - this.started}ms)`);
    }
  }

  /** The deadline passed with the reader still running: write the phases so far. */
  miss(timeoutMs: number): void {
    this.missed = timeoutMs;
    this.write(`AutoCode read missed its ${timeoutMs}ms deadline`);
  }

  /** A reader that missed its deadline has ended: write its full phases once. */
  collected(): void {
    if (this.missed === undefined || this.reported) return;
    this.reported = true;
    this.write(`AutoCode read that missed its ${this.missed}ms deadline ended`);
  }

  private write(headline: string): void {
    const at = (time: number | undefined): string => (time === undefined ? "-" : `${time - this.started}ms`);
    const line = `${headline}: ${this.args.join(" ")} | spawned ${at(this.spawned)}, first output ${at(this.firstOutput)}, `
      + `exited ${at(this.exited)}, closed ${at(this.closed)}, delivered ${at(this.delivered)}`;
    try {
      readLog?.appendLine(line);
    } catch {
      // A diagnostic sink that fails must not fail the read it describes.
    }
  }
}

/** Each call is a new process that re-hashes the whole workspace; the warm host (#1707) keeps that off the poll loop. */
function launchEnv(explicit: Record<string, string>): NodeJS.ProcessEnv {
  const merged: NodeJS.ProcessEnv = { ...process.env, ...explicit };
  // Windows file times prove nothing about content, so the core never reuses a hash there.
  if (process.platform !== "win32" && merged.AUTOCODE_SOURCE_HOST === undefined) {
    merged.AUTOCODE_SOURCE_HOST = "1";
  }
  return merged;
}

export function stream(
  options: CliOptions,
  args: string[],
  env: Record<string, string> = {},
  onOutput?: (chunk: string) => void,
  exitGraceMs = EXIT_GRACE_MS,
  timeoutMs?: number,
): Promise<CliResult> {
  return execute(options, args, env, onOutput, exitGraceMs, timeoutMs);
}

function execute(
  options: CliOptions,
  args: string[],
  env: Record<string, string>,
  onOutput: ((chunk: string) => void) | undefined,
  exitGraceMs: number,
  timeoutMs?: number,
  read?: ReadLease,
  environment?: NodeJS.ProcessEnv,
): Promise<CliResult> {
  const { file, args: argv } = buildArgv(options, args);
  return new Promise((resolve, reject) => {
    // Every public read records its phases; a slow or deadline-missing one writes them (#3849).
    const phases = read ? new ReadPhases(args) : undefined;
    let child: ChildProcessWithoutNullStreams;
    try {
      child = spawn(file, argv, {
        cwd: options.cwd,
        env: environment ?? launchEnv({ ...(options.env ?? {}), ...env }),
        shell: false,
      });
    } catch (error) {
      read?.release();
      reject(error);
      return;
    }
    phases?.spawn();
    if (read) read.child = child;
    let stdout = "";
    let stderr = "";
    // One decoder per stream keeps a multi-byte character split across two reads whole (#3854).
    const stdoutText = new StringDecoder("utf8");
    const stderrText = new StringDecoder("utf8");
    let settled = false;
    let grace: NodeJS.Timeout | undefined;
    let timer: NodeJS.Timeout | undefined;
    const finish = (code: number | null) => {
      if (settled) return;
      settled = true;
      if (grace) clearTimeout(grace);
      if (timer) clearTimeout(timer);
      // An incomplete sequence at the very end decodes as it always did, as one replacement character.
      const outRest = stdoutText.end();
      const errRest = stderrText.end();
      stdout += outRest;
      stderr += errRest;
      if (outRest) onOutput?.(outRest);
      if (errRest) onOutput?.(errRest);
      phases?.deliver();
      resolve({ code, stdout, stderr, rejected: isRejection(code, stderr) });
    };
    if (timeoutMs && timeoutMs > 0) {
      timer = setTimeout(() => {
        if (settled) return;
        settled = true;
        if (grace) clearTimeout(grace);
        if (read) {
          read.error = new ReadDeadlineExceeded(timeoutMs, read.collected, read.child !== undefined);
          phases?.miss(timeoutMs);
          reject(read.error);
        } else {
          child.kill("SIGKILL");
          reject(new ActionDeadlineExceeded(timeoutMs, file, argv));
        }
      }, timeoutMs);
    }
    child.stdout.on("data", (chunk: Buffer) => {
      phases?.output();
      if (settled) return;
      const text = stdoutText.write(chunk);
      stdout += text;
      if (text) onOutput?.(text);
    });
    child.stderr.on("data", (chunk: Buffer) => {
      phases?.output();
      if (settled) return;
      const text = stderrText.write(chunk);
      stderr += text;
      if (text) onOutput?.(text);
    });
    child.on("error", (error: NodeJS.ErrnoException) => {
      if (read && child.pid === undefined) read.release();
      if (settled) return;
      const hint = error.code === "ENOENT" ? `Is ${file} on PATH? Set "autocode.cliPath" in settings.` : error.message;
      settled = true;
      if (grace) clearTimeout(grace);
      if (timer) clearTimeout(timer);
      reject(new Error(`${file} could not start: ${hint}`));
    });
    // A controller that exits at a pause boundary may leave a provider child
    // holding stdio open; the action is over when the process is gone (#1556).
    child.on("exit", (code) => {
      phases?.exit();
      read?.release();
      if (settled) {
        // A reader that missed its deadline and exited is collected even if its pipes stay open.
        if (phases && read?.error) grace = setTimeout(() => phases.collected(), exitGraceMs);
        return;
      }
      // The child ended before its deadline, so the deadline no longer applies: its result is
      // known, and only the pipe grace or the close decides when it is delivered (#3850).
      if (timer) clearTimeout(timer);
      grace = setTimeout(() => finish(code), exitGraceMs);
    });
    child.on("close", (code) => {
      if (settled && grace) clearTimeout(grace);
      phases?.close();
      read?.release();
      finish(code);
    });
  });
}

function isRejection(code: number | null, stderr: string): boolean {
  if (code !== 2) return false;
  return stderr.includes("usage:") || stderr.includes("Input rejected:");
}

/** `autocode --workspace W --run-dir R --status`, as a parsed view. */
export async function readStatus<T>(options: CliOptions, workspace: string, runDir: string, parse: (raw: string) => T): Promise<T> {
  // Use the public TaskRun reader grammar. With --status last, the core's
  // conservative legacy-worker check can mistake polling for another writer.
  // Bounded: a hung --status must not freeze the conversation's poll loop.
  const result = await invokeRead(options, ["--status", "--workspace", workspace, "--run-dir", runDir]);
  if (result.code !== 0) throw new Error(tail(result));
  return parse(result.stdout);
}

/**
 * The most useful line a failed call said.
 *
 * A refused argument comes back from argparse as a usage line and `autocode: error: WHY`. A person
 * reads only the why, so the usage line and the prefix are dropped; the core's words are kept.
 */
export function tail(result: CliResult): string {
  const text = (result.stderr || result.stdout || "").trim();
  if (!text) return `autocode exited ${result.code}`;
  const lines = text.split("\n")
    .filter((line) => !/^usage: /.test(line))
    .map((line) => line.replace(/^[\w.-]+: error: /, ""));
  return (lines.length ? lines : text.split("\n")).slice(-6).join("\n");
}
