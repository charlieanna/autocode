/** When a chat tab's run stops for a person, and the identity notifications dedupe by. */
import { isWorking, needsSummary, type Needs, type StatusView } from "./core/view";

export interface WaitNotice {
  /** The done flag, or the need's kind with its distinguishing fields. */
  identity: string;
  tail: string;
  /** What the notification says after the chat's name. */
  outcome: string;
}

/**
 * The words after the chat's name. A `continue` needs no input from the person (the view contract), and a
 * dependency waits on another run, so neither says "waiting for you": the core's own line for them is
 * "nothing needed from you", and the notice said both at once.
 */
function outcomeFor(view: StatusView, tail: string): string {
  if (view.done) return "finished.";
  if (view.needs?.kind === "continue") return "stopped. Nothing is needed from you; open the chat to continue it.";
  if (view.needs?.kind === "dependency") return "is waiting for another run to deliver. Nothing is needed from you.";
  return `is waiting for you - ${tail}`;
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

/** A person-wait or a finished run; `continue` while a live stage or controller advances is work, not a wait. */
function waitNotice(view: StatusView | null | undefined): WaitNotice | null {
  if (!view || typeof view !== "object") return null;
  if (view.done) return { identity: "done", tail: needsSummary(view), outcome: outcomeFor(view, "") };
  const need = view.needs;
  if (!need) return null;
  if (need.kind === "continue" && isWorking(view)) return null;
  const tail = needsSummary(view);
  return { identity: needIdentity(need), tail, outcome: outcomeFor(view, tail) };
}

/** True while the run waits on the person: an open need that is not live work. A finished run is not waiting. */
export function waitsForPerson(view: StatusView | null | undefined): boolean {
  return Boolean(view && typeof view === "object" && !view.done && waitNotice(view));
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
