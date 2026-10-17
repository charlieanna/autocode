/** Reading the extension's settings into a CLI launch. */

import * as vscode from "vscode";
import type { CliOptions } from "./core/cli";

export interface Settings extends CliOptions {
  pollMs: number;
  confirmPersonOnly: boolean;
  /** Engine and model flags the client passes whenever it starts or advances a run. */
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
