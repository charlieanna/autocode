/** Reading the extension's settings into a CLI launch. */

import * as vscode from "vscode";
import type { CliOptions } from "./core/cli";

export interface Settings extends CliOptions {
  pollMs: number;
  confirmPersonOnly: boolean;
  /** Engine and model flags for new chats; saved runs retain their own configuration. */
  runOptions: string[];
}

export function readSettings(): Settings {
  const config = vscode.workspace.getConfiguration("autocode");
  const provider = config.get<string>("provider", "").trim();
  const extra = config.get<string[]>("extraOptions", []);
  return {
    command: config.get<string>("cliPath", "autocode").trim() || "autocode",
    prefix: config.get<string[]>("cliArgs", []),
    runOptions: [
      ...(provider ? ["--provider", provider] : []),
      ...extra.filter((word) => typeof word === "string" && word.length > 0),
    ],
    pollMs: Math.max(1, config.get<number>("pollSeconds", 5)) * 1000,
    confirmPersonOnly: config.get<boolean>("confirmPersonOnly", true),
  };
}

/** The engines the core's `--engine` accepts (tools/autocode_args.py). */
export const ENGINES = ["opencode", "codex", "claude"] as const;
export type Engine = (typeof ENGINES)[number];

/**
 * Split extraOptions into flags a backend owns and everything else.
 *
 * Route overrides belong to a backend, so switching backends swaps only these.
 */
export function partitionOptions(options: string[]): { shared: string[]; routes: string[] } {
  const result: string[] = [];
  const routes: string[] = [];
  for (let i = 0; i < options.length; i++) {
    const flag = options[i]!.split("=", 1)[0]!;
    if (/^--(?:provider|engine|model|reasoning-effort|pin-model-role|role-transport|builder-strong-model|allow-uncontained-tools|(?:astra|terra|sol|completion|glm|requirements|plan-reviewer|resolver|investigator)-(?:model|reasoning-effort))$/.test(flag)) {
      // provider is owned by the picker, never a hidden extraOptions override.
      if (flag !== "--provider") routes.push(options[i]!);
      if (!options[i]!.includes("=") && flag !== "--allow-uncontained-tools") {
        i++;
        if (flag !== "--provider" && i < options.length) routes.push(options[i]!);
      }
    } else result.push(options[i]!);
  }
  return { shared: result, routes };
}

/**
 * Shared interpretation of launch selection: distinguishes engine identity from backend/route identity.
 * Evaluates flags left-to-right with CLI precedence (later arguments override earlier ones,
 * supporting both `--flag value` and `--flag=value` forms).
 */
export function launchTarget(settings: Settings): { engine: string; routeKey: string } {
  let explicitEngine: Engine | undefined;
  let explicitProvider: string | undefined;

  for (let i = 0; i < settings.runOptions.length; i++) {
    const word = settings.runOptions[i]!;
    if (word === "--engine" && i + 1 < settings.runOptions.length) {
      const val = settings.runOptions[++i]!;
      if ((ENGINES as readonly string[]).includes(val)) explicitEngine = val as Engine;
    } else if (word.startsWith("--engine=")) {
      const val = word.slice("--engine=".length);
      if ((ENGINES as readonly string[]).includes(val)) explicitEngine = val as Engine;
    } else if (word === "--provider" && i + 1 < settings.runOptions.length) {
      explicitProvider = settings.runOptions[++i]!.trim();
    } else if (word.startsWith("--provider=")) {
      explicitProvider = word.slice("--provider=".length).trim();
    }
  }

  const engine = explicitEngine || explicitProvider || "opencode";

  let routeKey: string;
  if (explicitEngine === "codex") {
    routeKey = "codex";
  } else if (explicitEngine === "claude") {
    routeKey = "claude";
  } else {
    routeKey = explicitProvider || "opencode";
  }

  return { engine, routeKey };
}

/** The engine or registered provider new chats will run on; the core's default is opencode. */
export function engineOf(settings: Settings): string {
  return launchTarget(settings).engine;
}

/** The backend identity for route persistence and Connections panel selection. */
export function routeKeyOf(settings: Settings): string {
  return launchTarget(settings).routeKey;
}

/** The provider or engine name to display when confirming live model spend. */
export function spendTarget(settings: Settings): string {
  const target = launchTarget(settings);
  if (target.engine === "codex") return "codex";
  const configProvider = vscode.workspace.getConfiguration("autocode").get<string>("provider", "").trim();
  if (configProvider) return configProvider;
  if (target.engine === "claude") return "claude";
  return "its default";
}

/**
 * Filter out contaminated routes belonging to a different backend identity.
 * For example, OpenCode routes must never contain `--engine codex` or `--engine=codex`.
 */
export function sanitizeRoutes(routes: string[], key?: string): string[] {
  if (key === "opencode") {
    return routes.filter((word, i) => {
      if (word === "--engine=codex") return false;
      if (word === "--engine" && routes[i + 1] === "codex") return false;
      if (word === "codex" && routes[i - 1] === "--engine") return false;
      return true;
    });
  }
  return routes;
}

/**
 * Set the engine for new chats, keeping the flags each rule depends on valid.
 *
 * `--engine codex` refuses a provider, and `--engine claude` is the built-in
 * Claude Code connection, so the provider setting moves with the choice.
 * Existing chats keep their saved engine either way.
 */
export async function applyEngine(engine: Engine): Promise<void> {
  const config = vscode.workspace.getConfiguration("autocode");
  const options = config.get<string[]>("extraOptions", []);
  const { shared, routes } = partitionOptions(options);
  const withoutEngine = routes.filter((word, index) =>
    word !== "--engine" && !word.startsWith("--engine=") && routes[index - 1] !== "--engine");
  const target = vscode.workspace.workspaceFolders?.length ? vscode.ConfigurationTarget.Workspace : vscode.ConfigurationTarget.Global;
  await config.update("extraOptions", [...shared, ...withoutEngine, "--engine", engine], target);
  if (engine === "codex") await config.update("provider", "", target);
  if (engine === "claude") await config.update("provider", "claude", target);
}
