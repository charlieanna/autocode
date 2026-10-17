/**
 * The one place the extension starts the `autocode` CLI.
 *
 * Every call is an argument array with no shell, as `docs/registry-api.md`
 * requires of a client, and issued tokens go in the child's environment rather
 * than argv because a process command line is world-readable.
 */

import { spawn } from "node:child_process";

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
export function invoke(options: CliOptions, args: string[], env: Record<string, string> = {}): Promise<CliResult> {
  return stream(options, args, env);
}

/**
 * Run one CLI call, reporting output as it arrives.
 *
 * A rejection is a `usage:` or `Input rejected:` exit 2, which must not be
 * mistaken for a pause awaiting input (docs/task-run.md).
 */
export function stream(
  options: CliOptions,
  args: string[],
  env: Record<string, string> = {},
  onOutput?: (chunk: string) => void,
): Promise<CliResult> {
  const { file, args: argv } = buildArgv(options, args);
  return new Promise((resolve, reject) => {
    const child = spawn(file, argv, {
      cwd: options.cwd,
      env: { ...process.env, ...(options.env ?? {}), ...env },
      shell: false,
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk: Buffer) => {
      stdout += chunk.toString();
      onOutput?.(chunk.toString());
    });
    child.stderr.on("data", (chunk: Buffer) => {
      stderr += chunk.toString();
      onOutput?.(chunk.toString());
    });
    child.on("error", (error: NodeJS.ErrnoException) => {
      const hint = error.code === "ENOENT" ? `Is ${file} on PATH? Set "autocode.cliPath" in settings.` : error.message;
      reject(new Error(`${file} could not start: ${hint}`));
    });
    child.on("close", (code) => {
      resolve({ code, stdout, stderr, rejected: isRejection(code, stderr) });
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
  const result = await invoke(options, ["--status", "--workspace", workspace, "--run-dir", runDir]);
  if (result.code !== 0) throw new Error(tail(result));
  return parse(result.stdout);
}

/** The most useful line a failed call said. */
export function tail(result: CliResult): string {
  const text = (result.stderr || result.stdout || "").trim();
  if (!text) return `autocode exited ${result.code}`;
  return text.split("\n").slice(-6).join("\n");
}
