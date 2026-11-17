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

/** A model Stage models offers to measure for a stage: ticked when the free shortlist suggests it. */
export interface ProbeCandidate {
  model: string;
  effort: string | null;
  family: string | null;
  billing: string | null;
  ticked: boolean;
  reason: string | null;
  /** What an earlier probe found, or null when it was never measured. */
  measured: { qualified: boolean; reason: string; date: string | null } | null;
}

/** The stage's measure-and-approve offer (`probe`): `MEASURE` and `CAP` are the only words a client fills. */
export interface StageProbe {
  candidates: ProbeCandidate[];
  estimate: string[];
  /** Null when the probe cannot run yet; `stop` says why. */
  run: string[] | null;
  stop: string | null;
}

/** What measuring the ticked models can cost, as `autocode probe --json` prints it before anything runs. */
export interface ProbeEstimate { calls: number; quickCalls: number; maxUsd: number | null; basis: string }

export interface ConfiguredStageRoute extends StageRoute { source: string }

export interface Stage {
  id: string;
  label: string;
  does: string;
  advice: string;
  default: StageRoute & { offered: boolean };
  chosen: StageRoute | null;
  /** Explicit project or workspace-option route overriding global Stage models and defaults, or null. */
  configured: ConfiguredStageRoute | null;
  options: StageOption[];
  set: string[];
  reset: string[] | null;
  /** A new chat's own pick for this stage (`--chat-stage`), never saved; null when it takes the saved choice or default. */
  chat: StageRoute | null;
  /** The list with this stage picked for one new chat: `MODEL` and `EFFORT` filled as `set` is. */
  chatSet: string[] | null;
  /** The list without this stage's pick for the chat, or null with none. */
  chatReset: string[] | null;
  /** Stage models' offer to measure this stage's models, or null for a stage without a probe. */
  probe: StageProbe | null;
}

/** How a new chat starts with its picks: the stage flags to add to its start, and the list that names them. */
export interface ChatStart { startArgs: string[]; list: string[]; changeable: boolean; reason: string | null }

/** A model the backend's accounts serve, for the "models AutoCode may use" tick list. */
export interface AllowedModelOption {
  model: string;
  family: string | null;
  account: string | null;
  billing: string | null;
  /** The free shortlist, or a model a stage runs now. */
  suggested: boolean;
  /** Ticked when the list opens: the saved list, or the suggestion while there is none. */
  ticked: boolean;
  reason: string | null;
}

/** Which models AutoCode may use on the backend (`models`): `MODELS` is the only word a client fills. */
export interface AllowedModels {
  /** The person's list, in their order, or null for every model the accounts serve. */
  allowed: string[] | null;
  options: AllowedModelOption[];
  set: string[];
  /** Lets AutoCode use every model again; null while there is no list. */
  clear: string[] | null;
}

export interface StageView {
  backend: string;
  label: string;
  optionsStop: string | null;
  stages: Stage[];
  /** A started run's list (`stages list --run-dir`): false while a stage works, with the reason. */
  changeable: boolean;
  reason: string | null;
  /** A new chat's list (`stages list --json`, with any `--chat-stage` picks); null from an older AutoCode. */
  chat: ChatStart | null;
  /** Which models AutoCode may use; null for a started run's list or an older AutoCode. */
  models: AllowedModels | null;
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

function configuredRoute(value: unknown): ConfiguredStageRoute | null {
  const base = route(value);
  if (!base?.model) return null;
  const row = value as Record<string, unknown>;
  return { ...base, source: textOrNull(row.source) ?? "project" };
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
      configured: configuredRoute(value.configured),
      options,
      set,
      reset: words((value.reset as Record<string, unknown> | null | undefined)?.next_command),
      chat: route(value.chat),
      chatSet: chatListCommand((value.chat_set as Record<string, unknown> | null | undefined)?.next_command),
      chatReset: chatListCommand((value.chat_reset as Record<string, unknown> | null | undefined)?.next_command),
      probe: readProbe(value.probe),
    });
  }
  const chat = parsed.chat as Record<string, unknown> | null | undefined;
  const list = chatListCommand(chat?.next_command);
  const startArgs = stageFlags(chat?.start_args);
  return {
    backend: text(parsed.backend), label: text(parsed.label) || text(parsed.backend), optionsStop: textOrNull(parsed.options_stop), stages,
    changeable: parsed.changeable !== false, reason: textOrNull(parsed.reason),
    chat: list && startArgs ? { list, startArgs, changeable: chat?.changeable !== false, reason: textOrNull(chat?.reason) } : null,
    models: readModels(parsed.models),
  };
}

const MODELS = "MODELS";

/** The allowed-models offer, when its commands are `stages models` with one `MODELS` (and `--allow-all` to clear); else null. */
function readModels(value: unknown): AllowedModels | null {
  if (!value || typeof value !== "object") return null;
  const row = value as Record<string, unknown>;
  const isModels = (argv: string[] | null): argv is string[] => !!argv && argv[0] === "stages" && argv[1] === "models";
  const set = words((row.set as Record<string, unknown> | null | undefined)?.next_command);
  if (!isModels(set) || set.filter((word) => word === MODELS).length !== 1) return null;
  let clear = words((row.clear as Record<string, unknown> | null | undefined)?.next_command);
  if (!isModels(clear) || !clear.includes("--allow-all") || clear.includes(MODELS)) clear = null;
  const options: AllowedModelOption[] = [];
  for (const item of Array.isArray(row.options) ? (row.options as Record<string, unknown>[]) : []) {
    if (typeof item?.model !== "string" || !item.model) continue;
    options.push({
      model: item.model, family: textOrNull(item.family), account: textOrNull(item.account), billing: textOrNull(item.billing),
      suggested: item.suggested === true, ticked: item.ticked === true, reason: textOrNull(item.reason),
    });
  }
  const allowed = Array.isArray(row.allowed) && row.allowed.every((model) => typeof model === "string") ? (row.allowed as string[]) : null;
  return { allowed, options, set, clear };
}

/**
 * The allowed-models command with `MODELS` replaced by `--allow MODEL` for each picked model, in the
 * order given: only models the offer lists, never a typed one.
 */
export function fillModels(models: AllowedModels, picked: string[]): string[] {
  const unknown = picked.filter((model) => !models.options.some((row) => row.model === model));
  if (unknown.length) throw new Error(`Your accounts do not serve ${unknown.join(", ")}.`);
  const chosen = [...new Set(picked)];
  if (!chosen.length) throw new Error("Tick at least one model AutoCode may use.");
  return models.set.flatMap((word) => (word === MODELS ? chosen.flatMap((model) => ["--allow", model]) : [word]));
}

const MEASURE = "MEASURE";
const CAP = "CAP";

/** A probe offer whose commands are `autocode probe` with one `MEASURE`, and `CAP` once in the run; else null. */
function readProbe(value: unknown): StageProbe | null {
  if (!value || typeof value !== "object") return null;
  const row = value as Record<string, unknown>;
  const once = (argv: string[], word: string): boolean => argv.filter((w) => w === word).length === 1;
  const estimate = words((row.estimate as Record<string, unknown> | null | undefined)?.next_command);
  if (!estimate || estimate[0] !== "probe" || !once(estimate, MEASURE) || estimate.includes(CAP)) return null;
  let run = words((row.run as Record<string, unknown> | null | undefined)?.next_command);
  if (run && (run[0] !== "probe" || !once(run, MEASURE) || !once(run, CAP))) run = null;
  const candidates: ProbeCandidate[] = [];
  for (const item of Array.isArray(row.candidates) ? (row.candidates as Record<string, unknown>[]) : []) {
    if (typeof item?.model !== "string" || !item.model) continue;
    const found = item.measured as Record<string, unknown> | null | undefined;
    candidates.push({
      model: item.model, effort: textOrNull(item.effort), family: textOrNull(item.family), billing: textOrNull(item.billing),
      ticked: item.ticked === true, reason: textOrNull(item.reason),
      measured: found && typeof found === "object" ? { qualified: found.qualified === true, reason: text(found.reason), date: textOrNull(found.date) } : null,
    });
  }
  return { candidates, estimate, run, stop: textOrNull(row.stop) };
}

/**
 * A probe command with `MEASURE` replaced by `--measure MODEL LEVEL` for each picked candidate, in the
 * offer's order, and `CAP` by the cap: only candidates the offer lists, never a typed model.
 */
export function fillProbe(probe: StageProbe, command: string[], models: string[], cap?: number): string[] {
  const unknown = models.filter((model) => !probe.candidates.some((row) => row.model === model));
  if (unknown.length) throw new Error(`Not offered to measure: ${unknown.join(", ")}.`);
  const picked = probe.candidates.filter((row) => models.includes(row.model));
  if (!picked.length) throw new Error("Tick at least one model to measure.");
  const argv: string[] = [];
  for (const word of command) {
    if (word === MEASURE) for (const row of picked) argv.push("--measure", row.model, row.effort ?? "default");
    else if (word === CAP) {
      if (cap === undefined || !Number.isFinite(cap) || cap <= 0) throw new Error("The probe needs the estimate's cost to run.");
      argv.push(cap.toFixed(2));
    } else argv.push(word);
  }
  return argv;
}

/** The estimate `autocode probe --json` printed before anything ran, or an error when it is not one. */
export function readEstimate(raw: string): ProbeEstimate {
  const parsed = JSON.parse(raw) as Record<string, unknown>;
  const estimate = parsed.estimate as Record<string, unknown> | undefined;
  if (parsed.ran !== false || !estimate || typeof estimate.calls !== "number") throw new Error("Update AutoCode to measure models.");
  const max = estimate.max_usd;
  return {
    calls: estimate.calls,
    quickCalls: typeof estimate.quick_calls === "number" ? estimate.quick_calls : 0,
    maxUsd: typeof max === "number" && Number.isFinite(max) && max > 0 ? Math.ceil(max * 100 - 1e-9) / 100 : null,
    basis: text(estimate.basis),
  };
}

/** True when a fresh estimate prices exactly the models and cost the person was shown. */
export function sameEstimate(shown: { models: string[]; estimate: ProbeEstimate } | undefined, models: string[],
  fresh: ProbeEstimate, shownCap: number): boolean {
  return !!shown && shown.models.join("\n") === models.join("\n") && shown.estimate.maxUsd === shownCap
    && fresh.maxUsd !== null && fresh.maxUsd === shownCap;
}

/** What a finished probe found, in a sentence per model, from `autocode probe --json`. */
export function probeOutcome(label: string, raw: string): string {
  try {
    const parsed = JSON.parse(raw) as { spent_usd?: unknown; ranking?: unknown; stopped?: unknown };
    const rows = Array.isArray(parsed.ranking) ? (parsed.ranking as Record<string, unknown>[]) : [];
    const lines = rows.map((row) => `${String(row.model)} at ${typeof row.effort === "string" ? row.effort : "its default level"}: ${row.qualified === true ? "qualified" : "not qualified"}, ${String(row.reason ?? "")}`);
    const spent = typeof parsed.spent_usd === "number" ? `$${parsed.spent_usd.toFixed(2)}` : "an unknown amount";
    const stopped = typeof parsed.stopped === "string" && parsed.stopped ? ` Stopped: ${parsed.stopped}.` : "";
    return `Measured the ${label}'s models for ${spent}.${stopped} ${lines.join("; ")}`.trim();
  } catch { return `Measured the ${label}'s models.`; }
}

/** A new chat's read-only list: `stages list`, never a run's or a change. */
function chatListCommand(value: unknown): string[] | null {
  const argv = words(value);
  return argv && argv[0] === "stages" && argv[1] === "list" && !argv.includes("--run-dir") ? argv : null;
}

/** Only stage model and level flags, each with its value: anything else is not a pick and is not passed to a start. */
function stageFlags(value: unknown): string[] | null {
  const argv = words(value);
  if (!argv || argv.length % 2) return null;
  for (let i = 0; i < argv.length; i += 2) {
    if (!/^--[a-z]+(?:-[a-z]+)*-(?:model|reasoning-effort)$/.test(argv[i]!) || argv[i + 1]!.startsWith("-")) return null;
  }
  return argv;
}

/** The stage's own set command with a listed model and one of its levels, or an error saying why not. */
export function fillSet(stage: Stage, model: string, effort: string): string[] {
  return fill(stage, stage.set, model, effort);
}

/** The stage's pick for one new chat, filled the same way; an older AutoCode names none. */
export function fillChatSet(stage: Stage, model: string, effort: string): string[] {
  if (!stage.chatSet) throw new Error("This AutoCode cannot change a new chat's models; update AutoCode.");
  return fill(stage, stage.chatSet, model, effort);
}

function fill(stage: Stage, command: string[], model: string, effort: string): string[] {
  const option = stage.options.find((row) => row.model === model);
  if (!option) throw new Error(`The ${stage.label} is not offered ${model}.`);
  if (option.unavailable) throw new Error(`The ${stage.label} cannot run ${model}: ${option.unavailable}.`);
  if (!option.effortChoices.includes(effort)) throw new Error(`${model} takes ${option.effortChoices.join(", ")}.`);
  if (command.filter((word) => word === "MODEL").length !== 1 || command.filter((word) => word === "EFFORT").length !== 1) {
    throw new Error("The stage view names no choice the extension can make; update AutoCode.");
  }
  return command.map((word) => (word === "MODEL" ? model : word === "EFFORT" ? effort : word));
}

/** What a new chat's stage will run, in words: this chat's pick, explicit config, else what every new chat takes. */
export function chatRoute(stage: Stage): string {
  if (stage.chat?.model) return `${stage.chat.model} at ${stage.chat.effort ?? "its own default level"} (this chat)`;
  if (stage.configured?.model) return `${stage.configured.model} at ${stage.configured.effort ?? "its own default level"} (${stage.configured.source})`;
  return currentRoute(stage).replace(/\(your choice\)$/, "(Stage models)");
}

/**
 * Where a new chat's stage takes its model from, without the model: what sits beside a dropdown
 * that already shows the model and level. Empty when nothing is chosen until the chat starts.
 */
export function chatSource(stage: Stage): string {
  if (stage.chat?.model) return "this chat";
  if (stage.configured?.model) return stage.configured.source;
  if (stage.chosen?.model) return "Stage models";
  if (stage.default.model && !stage.default.offered) return "";
  return "default";
}

/** What the stage runs now, in words: the person's choice, else its default. */
export function currentRoute(stage: Stage): string {
  if (stage.chosen?.model) return `${stage.chosen.model} at ${stage.chosen.effort ?? "its own default level"} (your choice)`;
  if (stage.default.model && !stage.default.offered) return "chosen from your accounts when a chat starts";
  return `${stage.default.model ?? "its default"} at ${stage.default.effort ?? "its own default level"} (default)`;
}

/** What a started chat's stage runs now: its own route, never the new-chat choice. */
export function runRoute(stage: Stage): string {
  const route = stage.chosen ?? stage.default;
  return `${route.model ?? "not reported"} at ${route.effort ?? "its own default level"} (this chat)`;
}

/** The read-only list a chat's status view names for its own stages, or null when it names none. */
export function runListCommand(value: unknown): string[] | null {
  const argv = words(value);
  return argv && argv[0] === "stages" && argv[1] === "list" && argv.includes("--run-dir") ? argv : null;
}

/** `--backend KEY` when the person chose the engine or provider new chats run on; otherwise the core picks it as a new run would. */
export function backendArgs(runOptions: string[], routeKey: string): string[] {
  return runOptions.some((word) => /^--(?:engine|provider)(?:=|$)/.test(word)) ? ["--backend", routeKey] : [];
}

const MODEL_OPTION = /^--(?:single-model|reasoning-effort|builder-strong-model|(?:planner|task-planner|builder|tester|astra|terra|sol|glm|completion|requirements|plan-reviewer|resolver|investigator)-(?:model|reasoning-effort))$/;

/** New-run model and reasoning-level options from settings, passed after `--` to `stages list`. */
export function modelArgs(runOptions: string[]): string[] {
  const found: string[] = [];
  for (let i = 0; i < runOptions.length; i++) {
    const word = runOptions[i]!;
    const flag = word.split("=", 1)[0]!;
    if (!MODEL_OPTION.test(flag)) continue;
    if (word.includes("=")) {
      found.push(word);
    } else if (i + 1 < runOptions.length && !runOptions[i + 1]!.startsWith("-")) {
      found.push(word, runOptions[++i]!);
    }
  }
  return found.length ? ["--", ...found] : [];
}
