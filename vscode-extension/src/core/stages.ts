/**
 * Each stage's model and reasoning level for new chats (`autocode stages list --json`).
 *
 * The core owns the list (docs/task-run.md#stage-choices-for-clients): the
 * extension shows each stage's label, what it does and the advice for it, and
 * one dropdown of the models the stage may run with one of the levels that
 * model takes. Saving takes only the stage's own `set.next_command`, with
 * `MODEL` and `EFFORT` replaced by a listed model and one of its levels; an
 * option the view marks unavailable is shown with its reason and never sent.
 */

export interface StageOption {
  model: string;
  effortChoices: string[];
  family: string | null;
  context: number | null;
  account: string | null;
  unavailable: string | null;
}

export interface StageRoute { model: string | null; effort: string | null }

export interface Stage {
  id: string;
  label: string;
  does: string;
  advice: string;
  default: StageRoute & { offered: boolean };
  chosen: StageRoute | null;
  options: StageOption[];
  set: string[];
  reset: string[] | null;
}

export interface StageView {
  backend: string;
  label: string;
  optionsStop: string | null;
  stages: Stage[];
}

const text = (value: unknown): string => (typeof value === "string" ? value : "");
const textOrNull = (value: unknown): string | null => (typeof value === "string" && value ? value : null);
const words = (value: unknown): string[] | null =>
  Array.isArray(value) && value.every((word) => typeof word === "string") ? (value as string[]) : null;

function route(value: unknown): StageRoute | null {
  if (!value || typeof value !== "object") return null;
  const row = value as Record<string, unknown>;
  return { model: textOrNull(row.model), effort: textOrNull(row.effort) };
}

/** Read the version-1 view; a malformed stage or option is dropped rather than guessed at. */
export function readStages(raw: string): StageView {
  const parsed = JSON.parse(raw) as Record<string, unknown>;
  if (parsed.version !== 1 || !Array.isArray(parsed.stages)) throw new Error("Update AutoCode to choose each stage's model.");
  const stages: Stage[] = [];
  for (const value of parsed.stages as Record<string, unknown>[]) {
    const set = words((value?.set as Record<string, unknown> | undefined)?.next_command);
    if (typeof value?.id !== "string" || !set) continue;
    const options: StageOption[] = [];
    for (const option of Array.isArray(value.options) ? (value.options as Record<string, unknown>[]) : []) {
      const choices = words(option?.effort_choices);
      if (typeof option?.model !== "string" || !choices) continue;
      options.push({
        model: option.model,
        effortChoices: choices,
        family: textOrNull(option.family),
        context: typeof option.context === "number" ? option.context : null,
        account: textOrNull(option.account),
        unavailable: textOrNull(option.unavailable),
      });
    }
    const fallback = route(value.default) ?? { model: null, effort: null };
    stages.push({
      id: value.id,
      label: text(value.label) || value.id,
      does: text(value.does),
      advice: text(value.advice),
      default: { ...fallback, offered: (value.default as Record<string, unknown> | undefined)?.offered === true },
      chosen: route(value.chosen),
      options,
      set,
      reset: words((value.reset as Record<string, unknown> | null | undefined)?.next_command),
    });
  }
  return { backend: text(parsed.backend), label: text(parsed.label) || text(parsed.backend), optionsStop: textOrNull(parsed.options_stop), stages };
}

/** The stage's own set command with a listed model and one of its levels, or an error saying why not. */
export function fillSet(stage: Stage, model: string, effort: string): string[] {
  const option = stage.options.find((row) => row.model === model);
  if (!option) throw new Error(`The ${stage.label} is not offered ${model}.`);
  if (option.unavailable) throw new Error(`The ${stage.label} cannot run ${model}: ${option.unavailable}.`);
  if (!option.effortChoices.includes(effort)) throw new Error(`${model} takes ${option.effortChoices.join(", ")}.`);
  if (stage.set.filter((word) => word === "MODEL").length !== 1 || stage.set.filter((word) => word === "EFFORT").length !== 1) {
    throw new Error("The stage view names no choice the extension can make; update AutoCode.");
  }
  return stage.set.map((word) => (word === "MODEL" ? model : word === "EFFORT" ? effort : word));
}

/** What the stage runs now, in words: the person's choice, else its default. */
export function currentRoute(stage: Stage): string {
  if (stage.chosen?.model) return `${stage.chosen.model} at ${stage.chosen.effort ?? "its own default level"} (your choice)`;
  if (stage.default.model && !stage.default.offered) return "chosen from your accounts when a chat starts";
  return `${stage.default.model ?? "its default"} at ${stage.default.effort ?? "its own default level"} (default)`;
}

/** `--backend KEY` when the person chose the engine or provider new chats run on; otherwise the core picks it as a new run would. */
export function backendArgs(runOptions: string[], routeKey: string): string[] {
  return runOptions.some((word) => /^--(?:engine|provider)(?:=|$)/.test(word)) ? ["--backend", routeKey] : [];
}
