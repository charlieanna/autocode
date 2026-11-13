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
  /** Absent on older cores. A blocked controller needs setup before any placement can start. */
  controller?: {
    ready: boolean;
    detail: string;
    action: { label: string; nextCommand: string[] } | null;
  };
}

/** What a chat that has chosen nothing yet takes, and what an AutoCode without the list did. */
export const NEW_BRANCH = "new_branch";
export const LEGACY_PLACEMENT = ["--in-place"] as const;

/** The read failed because this AutoCode has no `worktrees` command, not because the list could not be read. */
export class NoPlaceList extends Error {}

/**
 * Whether a failed `worktrees list` came from an AutoCode without the command: its main parser takes
 * `worktrees` as the task and refuses `list` (exit 2, `unrecognized arguments`). Any other failure (a
 * timeout, a crash, a refusal by the command itself) is not that, and must not put a chat in the folder.
 */
export function lacksPlaceList(result: { code: number | null; stderr: string }): boolean {
  return result.code === 2 && /^usage: /m.test(result.stderr) && result.stderr.includes("unrecognized arguments")
    && !/^usage: \S+ worktrees\b/m.test(result.stderr);
}

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
  let controller: WorktreeList["controller"];
  if (parsed.controller !== undefined) {
    if (!parsed.controller || typeof parsed.controller !== "object"
      || typeof (parsed.controller as Record<string, unknown>).ready !== "boolean"
      || typeof (parsed.controller as Record<string, unknown>).detail !== "string") {
      throw new Error("AutoCode printed unreadable controller setup; update AutoCode.");
    }
    const row = parsed.controller as Record<string, unknown>;
    const expected = ["worktrees", "list", "--workspace", text(parsed.folder), "--json"];
    const offered = Array.isArray(row.actions) ? row.actions.find((item: unknown) => {
      if (!item || typeof item !== "object") return false;
      const action = item as Record<string, unknown>;
      return action.id === "select_cli" && typeof action.label === "string" && action.label.length > 0
        && JSON.stringify(action.next_command) === JSON.stringify(expected);
    }) as Record<string, unknown> | undefined : undefined;
    controller = {
      ready: row.ready === true, detail: text(row.detail),
      action: offered ? { label: text(offered.label), nextCommand: expected } : null,
    };
  }
  return { folder: text(parsed.folder), branch: textOrNull(parsed.branch), defaultPlace, places, controller };
}

/** A person's choice: a place, and for `branch` the branch they picked. */
export interface PlaceChoice { place: string; branch?: string }

/** The place and its start flags for a choice, or an error saying why it cannot start there now. */
export function chosenPlacement(list: WorktreeList, choice: PlaceChoice | undefined): { place: Place; args: string[]; label: string } {
  if (list.controller && !list.controller.ready) throw new Error(list.controller.detail);
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
