/** When a chat tab's run stops for a person, and the identity notifications dedupe by. */
import { needsSummary, type Needs, type StatusView } from "./core/view";

export interface WaitNotice {
  /** The done flag, or the need's kind with its distinguishing fields. */
  identity: string;
  tail: string;
}

function liveStep(view: StatusView): boolean {
  return ["supervised", "unsupervised"].includes(view.liveness?.kind ?? "");
}

function needIdentity(need: Needs): string {
  return JSON.stringify([
    need.kind,
    need.request_id ?? null,
    need.resolver_token ?? null,
    need.resolver_request_id ?? null,
    need.token ?? null,
    need.job_retry_token ?? null,
  ]);
}

/** A person-wait or a finished run; `continue` while a live model step runs is work, not a wait. */
function waitNotice(view: StatusView | null | undefined): WaitNotice | null {
  if (!view || typeof view !== "object") return null;
  if (view.done) return { identity: "done", tail: needsSummary(view) };
  const need = view.needs;
  if (!need) return null;
  if (need.kind === "continue" && liveStep(view)) return null;
  return { identity: needIdentity(need), tail: needsSummary(view) };
}

/** The notice the tab's new status earns over its previous one, or null when the run is only working. */
export function transitionNotice(
  previous: StatusView | null | undefined,
  current: StatusView | null | undefined,
): WaitNotice | null {
  const now = waitNotice(current);
  if (!now) return null;
  const before = waitNotice(previous);
  return before && before.identity === now.identity ? null : now;
}
