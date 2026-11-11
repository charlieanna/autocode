/** Order the run list by last activity, so the chats a person used most recently come first. */

import type { RegistryRun } from "./view";

/** How many chats the Recent section shows, across every project. */
export const RECENT_LIMIT = 10;

/** When the run last wrote its checkpoint, in milliseconds, or null when the registry did not say. */
export function activity(run: RegistryRun): number | null {
  if (!run.updatedAt) return null;
  const when = Date.parse(run.updatedAt);
  return Number.isNaN(when) ? null : when;
}

/**
 * Newest activity first. A run without a time sorts after every run with one, and runs
 * the registry gave no time for keep their directory order, newest name first.
 */
export function byLatest(a: RegistryRun, b: RegistryRun): number {
  const left = activity(a);
  const right = activity(b);
  if (left !== right) {
    if (left === null) return 1;
    if (right === null) return -1;
    return right - left;
  }
  return a.runDir === b.runDir ? 0 : b.runDir > a.runDir ? 1 : -1;
}

/** The chats to list under Recent: the newest available runs of every project. */
export function recentRuns(runs: readonly RegistryRun[], limit = RECENT_LIMIT): RegistryRun[] {
  return runs
    .filter((run) => run.availability === "available" && activity(run) !== null)
    .sort(byLatest)
    .slice(0, limit);
}

/** How long ago a run was last active, as a person reads it: "just now", "5m ago", "3d ago". */
export function relativeTime(run: RegistryRun, now: number): string {
  const when = activity(run);
  if (when === null) return "";
  const seconds = Math.max(0, Math.floor((now - when) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(when).toISOString().slice(0, 10);
}
