/** A run's status and next stage in a person's words, for every surface that names them. */

// Only for a core older than `status_label`: the core's own words come first wherever it sends them.
const OLDER_CORE_STATUS: Record<string, string> = {
  RUNNING: "Working",
  DISCOVERING: "Working",
  COMPLETE: "Finished",
  TASK_COMPLETE: "Finished",
  AWAITING_GOAL_APPROVAL: "Plan ready for your approval",
  WAITING_FOR_USER: "Waiting for your answer",
};

export function sentenceCase(code: string): string {
  const spaced = code.replace(/_/g, " ").trim().toLowerCase();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** A run's status as a person reads it: the core's `label`, else plain words, never the raw code. */
export function statusWords(status: string | null | undefined, label?: string | null): string {
  if (label) return label;
  if (!status) return "Status unknown";
  if (OLDER_CORE_STATUS[status]) return OLDER_CORE_STATUS[status];
  if (status === "PAUSED" || status.startsWith("PAUSED_") || status === "BLOCKED_HUMAN") return "Paused";
  return sentenceCase(status);
}

/** The next stage as a person reads it: the core's job name, else the stage's code name in words. */
export function stageWords(stage: string | null | undefined, label?: string | null): string | null {
  if (label) return label;
  return stage ? sentenceCase(stage) : null;
}
