/**
 * Where a new chat works: `autocode worktrees list --json` (docs/task-run.md#worktrees-for-clients).
 *
 * The core lists the places (a new worktree and branch, an existing branch, or the folder itself) with the
 * start flags each takes, and says when one is not offered or needs a warning. The extension shows them,
 * passes the chosen place's `start_args` unchanged, and refuses anything outside the three placements a
 * start may carry, so a list can never smuggle another option into a launch.
 */

export interface BranchChoice {
  name: string;
  /** Null when the branch is not offered; `unavailable` says why. */
  startArgs: string[] | null;
  unavailable: string | null;
}

export interface Place {
  id: string;
  label: string;
  does: string;
  /** The start flags for this place; empty for `branch`, whose rows carry their own. */
  startArgs: string[] | null;
  /** Shown to the person, who confirms before the chat starts here. */
  warning: string | null;
  branches: BranchChoice[];
}

export interface WorktreeList {
  folder: string;
  branch: string | null;
  defaultPlace: string;
  places: Place[];
}

/** What a chat that has chosen nothing yet takes, and what an AutoCode without the list did. */
export const NEW_BRANCH = "new_branch";
export const LEGACY_PLACEMENT = ["--in-place"] as const;

const text = (value: unknown): string => (typeof value === "string" ? value : "");
const textOrNull = (value: unknown): string | null => (typeof value === "string" && value ? value : null);

/** Only the placement flags a start may carry: a new branch, one existing branch, or the folder. */
export function placementArgs(value: unknown): string[] | null {
  if (!Array.isArray(value) || !value.every((word) => typeof word === "string")) return null;
  const argv = value as string[];
  if (argv.length === 1 && (argv[0] === "--build-in-worktree" || argv[0] === "--in-place")) return [...argv];
  if (argv.length === 3 && argv[0] === "--build-in-worktree" && argv[1] === "--worktree-branch"
    && argv[2]!.length > 0 && !argv[2]!.startsWith("-")) return [...argv];
  return null;
}

export function readWorktrees(raw: string): WorktreeList {
  let parsed: Record<string, unknown>;
  try {
    parsed = JSON.parse(raw) as Record<string, unknown>;
  } catch {
    throw new Error("AutoCode printed an unreadable worktree list.");
  }
  if (!parsed || typeof parsed !== "object" || parsed.schema !== 1 || !Array.isArray(parsed.places)) {
    throw new Error("AutoCode printed a worktree list this extension does not read; update the extension.");
  }
  const places: Place[] = [];
  for (const item of parsed.places as unknown[]) {
    if (!item || typeof item !== "object") continue;
    const row = item as Record<string, unknown>;
    const id = text(row.id);
    if (!id) continue;
    const branches: BranchChoice[] = Array.isArray(row.branches)
      ? (row.branches as unknown[]).flatMap((value) => {
        if (!value || typeof value !== "object") return [];
        const branch = value as Record<string, unknown>;
        const name = text(branch.name);
        if (!name) return [];
        const startArgs = placementArgs(branch.start_args);
        const unavailable = textOrNull(branch.unavailable) ?? (startArgs ? null : "AutoCode names no way to start on it.");
        return [{ name, startArgs: unavailable ? null : startArgs, unavailable }];
      })
      : [];
    const startArgs = row.start_args === undefined ? null : placementArgs(row.start_args);
    if (!startArgs && !branches.length && id !== "branch") continue; // a place the extension cannot start
    places.push({ id, label: text(row.label) || id, does: text(row.does), startArgs, warning: textOrNull(row.warning), branches });
  }
  if (!places.length) throw new Error("AutoCode listed no place a chat can start; update AutoCode.");
  const defaultPlace = places.some((place) => place.id === parsed.default) ? text(parsed.default) : places[0]!.id;
  return { folder: text(parsed.folder), branch: textOrNull(parsed.branch), defaultPlace, places };
}

/** A person's choice: a place, and for `branch` the branch they picked. */
export interface PlaceChoice { place: string; branch?: string }

/** The place and its start flags for a choice, or an error saying why it cannot start there now. */
export function chosenPlacement(list: WorktreeList, choice: PlaceChoice | undefined): { place: Place; args: string[]; label: string } {
  const place = list.places.find((row) => row.id === (choice?.place ?? list.defaultPlace))
    ?? list.places.find((row) => row.id === list.defaultPlace)!;
  if (place.branches.length || place.id === "branch") {
    const branch = place.branches.find((row) => row.name === choice?.branch);
    if (!branch) throw new Error("Pick the branch this chat works on.");
    if (!branch.startArgs) throw new Error(`The branch ${branch.name} is not available: ${branch.unavailable ?? "AutoCode does not offer it"}`);
    return { place, args: branch.startArgs, label: `branch ${branch.name}` };
  }
  if (!place.startArgs) throw new Error(`${place.label} cannot be started from this extension; update AutoCode.`);
  return { place, args: place.startArgs, label: place.label };
}
