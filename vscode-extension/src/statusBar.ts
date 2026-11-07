/** The window status bar's line for the run the chat shows: its state, current stage and cost. */
import { runTotal, stages, struggling, totalText } from "./core/meter";
import { isWorking, type StatusView } from "./core/view";
import { waitsForPerson } from "./notify";

export interface StatusLine {
  text: string;
  tooltip: string;
}

export function statusLine(view: StatusView, now = Date.now()): StatusLine {
  const headline = view.progress?.headline ?? view.status;
  const trouble = struggling(view, now);
  const running = stages(view, now).filter((row) => row.state === "working" || row.state === "retrying").pop();
  const cost = totalText(runTotal(view), false);
  let icon = "$(autorenew)";
  let what = headline;
  if (view.done) icon = "$(check)";
  else if (waitsForPerson(view)) icon = "$(bell)";
  else if (trouble) icon = "$(warning)";
  else if (running && isWorking(view)) {
    icon = "$(sync~spin)";
    what = running.job;
  }
  const tooltip = [view.progress?.line ?? view.status, waitsForPerson(view) ? "Waiting for you" : "", trouble ? `Struggling: ${trouble}` : "", cost ? `Cost: ${totalText(runTotal(view))}` : ""]
    .filter(Boolean).join("\n");
  return { text: `${icon} AutoCode: ${what}${cost ? ` · ${cost}` : ""}`, tooltip };
}
