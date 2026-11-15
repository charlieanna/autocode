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

/** A mutating CLI action exceeded its deadline and was stopped; its result is unknown. */
export class ActionDeadlineExceeded extends Error {
  constructor(timeoutMs: number, file: string, args: string[]) {
    super(`autocode timed out after ${timeoutMs}ms: ${file} ${args.join(" ")}`);
    this.name = "ActionDeadlineExceeded";
  }
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
    if (read) read.child = child;
    let stdout = "";
    let stderr = "";
    let settled = false;
    let grace: NodeJS.Timeout | undefined;
    let timer: NodeJS.Timeout | undefined;
    const finish = (code: number | null) => {
      if (settled) return;
      settled = true;
      if (grace) clearTimeout(grace);
      if (timer) clearTimeout(timer);
      resolve({ code, stdout, stderr, rejected: isRejection(code, stderr) });
    };
    if (timeoutMs && timeoutMs > 0) {
      timer = setTimeout(() => {
        if (settled) return;
        settled = true;
        if (grace) clearTimeout(grace);
        if (read) {
          read.error = new ReadDeadlineExceeded(timeoutMs, read.collected, read.child !== undefined);
          reject(read.error);
        } else {
          child.kill("SIGKILL");
          reject(new ActionDeadlineExceeded(timeoutMs, file, argv));
        }
      }, timeoutMs);
    }
    child.stdout.on("data", (chunk: Buffer) => {
      if (settled) return;
      stdout += chunk.toString();
      onOutput?.(chunk.toString());
    });
    child.stderr.on("data", (chunk: Buffer) => {
      if (settled) return;
      stderr += chunk.toString();
      onOutput?.(chunk.toString());
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
      read?.release();
      if (settled) return;
      grace = setTimeout(() => finish(code), exitGraceMs);
    });
    child.on("close", (code) => { read?.release(); finish(code); });
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
