/**
 * The run modes AutoCode offers for a new chat (`autocode modes list --json`).
 *
 * The core owns the list (docs/task-run.md#run-modes-for-clients): the
 * extension shows each mode's label and description and passes its
 * `start_args` unchanged. It never adds a mode or a flag of its own.
 */

export interface RunMode {
  id: string;
  label: string;
  description: string;
  startArgs: string[];
  spendConfirmation?: "planning" | "start";
}

export interface RunModes {
  defaultId: string;
  modes: RunMode[];
}

/** What a chat starts with when the CLI lists no modes (an older AutoCode): today's full flow. */
export const FULL_START_ARGS = ["--joint-planning", "--spec-first"] as const;

/** Read the version-1 list; a malformed mode is dropped rather than guessed at. */
export function readRunModes(raw: string): RunModes {
  const parsed = JSON.parse(raw) as Record<string, unknown>;
  if (parsed.schema_version !== 1) throw new Error(`Unsupported run-mode list version: ${String(parsed.schema_version)}`);
  const rows = Array.isArray(parsed.modes) ? (parsed.modes as Record<string, unknown>[]) : [];
  const modes: RunMode[] = [];
  for (const row of rows) {
    const args = row.start_args;
    if (typeof row.id !== "string" || !row.id || typeof row.label !== "string" || !Array.isArray(args)) continue;
    // Only flags: a bare word would be read as a second task or a flag's value.
    if (!args.every((word) => typeof word === "string" && word.startsWith("--"))) continue;
    modes.push({ id: row.id, label: row.label, description: typeof row.description === "string" ? row.description : "", startArgs: args as string[],
      spendConfirmation: row.spend_confirmation === "planning" ? "planning" : "start" });
  }
  const defaultId = typeof parsed.default === "string" && modes.some((mode) => mode.id === parsed.default) ? parsed.default : modes[0]?.id ?? "";
  return { defaultId, modes };
}

/** The mode a new chat uses: the person's choice while the core still lists it, else the core's default. */
export function chosenMode(list: RunModes, chosenId: string | undefined): RunMode | undefined {
  return list.modes.find((mode) => mode.id === chosenId) ?? list.modes.find((mode) => mode.id === list.defaultId);
}
