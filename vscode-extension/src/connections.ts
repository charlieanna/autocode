/** Connection metadata and named actions come from the public AutoCode CLI. */
import * as vscode from "vscode";
import { randomBytes } from "node:crypto";
import { invoke, tail } from "./core/cli";
import { partitionOptions, readSettings, routeKeyOf, sanitizeRoutes } from "./settings";

interface ConnectionAction { id: string; label: string; next_command: string[] }
interface Connection { id: string; label: string; connection_status: string; detail: string; actions: ConnectionAction[] }

/** Route overrides belong to a backend. Budgets and other run options remain shared. */
export function withoutRoutes(options: string[]): string[] {
  return partitionOptions(options).shared;
}

export class ConnectionsPanel implements vscode.Disposable {
  private panel?: vscode.WebviewPanel;
  private rows: Connection[] = [];
  private busy = false;
  private error = "";
  constructor(private readonly storage: vscode.Memento) {}

  async show(): Promise<void> {
    if (this.panel) this.panel.reveal();
    else {
      const panel = vscode.window.createWebviewPanel("autocode.connections", "AutoCode Connections", vscode.ViewColumn.One, { enableScripts: true, localResourceRoots: [] });
      this.panel = panel;
      panel.webview.html = connectionsHtml();
      panel.onDidDispose(() => { if (this.panel === panel) this.panel = undefined; });
      panel.webview.onDidReceiveMessage((message) => void this.receive(message));
    }
    await this.refresh();
  }

  dispose(): void { this.panel?.dispose(); }

  private post(): void {
    void this.panel?.webview.postMessage({ connections: this.rows, busy: this.busy, error: this.error,
      selected: routeKeyOf(readSettings()) });
  }

  private async refresh(): Promise<void> {
    const result = await invoke(readSettings(), ["connections", "list", "--json"]);
    if (result.code !== 0) throw new Error(tail(result));
    const data = JSON.parse(result.stdout);
    if (data.version !== 1 || !Array.isArray(data.connections)) throw new Error("Update AutoCode to use Connections.");
    this.rows = data.connections;
    this.post();
  }

  private async receive(message: { type?: string; provider?: string; action?: string }): Promise<void> {
    if (this.busy) return;
    this.busy = true; this.error = ""; this.post();
    try {
      if (message.type === "ready" || message.type === "refresh") { await this.refresh(); return; }
      const row = this.rows.find((item) => item.id === message.provider);
      if (!row) return;
      if (message.type === "select") {
        if (["unavailable", "setup_required", "sdk_required"].includes(row.connection_status)) throw new Error("Finish this backend's setup before selecting it.");
        await this.select(row.id);
        return;
      }
      // The webview supplies only an action ID. It cannot supply executable,
      // arguments or credentials. Re-read the core offer before executing it.
      await this.refresh();
      const action = this.rows.find((item) => item.id === row.id)?.actions.find((item) => item.id === message.action);
      if (message.type !== "action" || !action) return;
      const settings = readSettings();
      if (action.id === "login" || action.id === "setup" || action.id === "add") {
        // VS Code launches AutoCode directly as the terminal process, with an
        // argument array. No sendText(), shell interpolation or auth output log.
        vscode.window.createTerminal({ name: `AutoCode: ${row.label} setup`, shellPath: settings.command,
          shellArgs: [...settings.prefix, ...action.next_command], env: settings.env }).show();
      } else if (action.id === "check") {
        const result = await invoke(settings, action.next_command);
        if (result.code !== 0) throw new Error(tail(result));
        const checked = JSON.parse(result.stdout) as Connection;
        if (checked.id !== row.id) throw new Error("Connection check returned a different backend.");
        this.rows = this.rows.map((item) => item.id === checked.id ? checked : item);
      }
    } catch (error) { this.error = error instanceof Error ? error.message : String(error); }
    finally { this.busy = false; this.post(); }
  }

  private async select(provider: string): Promise<void> {
    const config = vscode.workspace.getConfiguration("autocode");
    const settings = readSettings();
    const current = routeKeyOf(settings);
    if (provider === current) return;
    const options = config.get<string[]>("extraOptions", []);
    // Preserve each backend's exact token-plan overrides, including when the
    // person switches away and back. New backends use their own role defaults.
    const saved = this.storage.get<Record<string, string[]>>("autocode.connection.routes") ?? {};

    // Recover from previous bug where opencode routes were contaminated with codex
    if (saved.opencode?.some((w) => w === "--engine=codex" || w === "codex")) {
      if (!saved.codex || saved.codex.length === 0) {
        saved.codex = saved.opencode.filter((w, i) =>
          w === "--engine=codex" || (w === "--engine" && saved.opencode![i + 1] === "codex") || (w === "codex" && saved.opencode![i - 1] === "--engine")
        );
      }
      saved.opencode = sanitizeRoutes(saved.opencode, "opencode");
    }

    saved[current] = sanitizeRoutes(partitionOptions(options).routes, current);
    const targetRoutes = sanitizeRoutes(saved[provider] ?? [], provider);
    saved[provider] = targetRoutes;
    const next = [...withoutRoutes(options), ...targetRoutes];
    await this.storage.update("autocode.connection.routes", saved);
    const target = vscode.workspace.workspaceFolders?.length ? vscode.ConfigurationTarget.Workspace : vscode.ConfigurationTarget.Global;
    await config.update("extraOptions", next, target);
    try { await config.update("provider", provider, target); }
    catch (error) { await config.update("extraOptions", options, target); throw error; }
  }
}

export function connectionsHtml(): string {
  const nonce = randomBytes(18).toString("hex");
  return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}'"><style nonce="${nonce}">
body{font:var(--vscode-font-size) var(--vscode-font-family);color:var(--vscode-foreground);background:var(--vscode-editor-background);max-width:760px;padding:20px;margin:auto}article{border:1px solid var(--vscode-panel-border);border-radius:8px;margin:16px 0;padding:16px}button{background:var(--vscode-button-background);color:var(--vscode-button-foreground);border:0;padding:8px 12px;margin:4px 8px 4px 0;cursor:pointer}button:disabled{opacity:.5;cursor:default}.detail{line-height:1.5}#error{color:var(--vscode-errorForeground)}h2{font-size:1.15em}</style></head><body>
<h1>Connections</h1><p>Choose the backend for new chats. Sign-in opens that backend’s own login in a terminal. Credentials stay with the backend.</p><p>Existing chats keep their saved backend and models. Each backend’s model overrides are preserved when you switch back.</p><button id="refresh">Refresh backends</button><p id="error" role="alert"></p><main id="connections" aria-live="polite"></main>
<script nonce="${nonce}">const vscode=acquireVsCodeApi();const main=document.getElementById('connections');
document.getElementById('refresh').onclick=()=>vscode.postMessage({type:'refresh'});
window.addEventListener('message',({data})=>{main.replaceChildren();document.getElementById('error').textContent=data.error||'';document.getElementById('refresh').disabled=data.busy;
for(const row of data.connections||[]){const card=document.createElement('article');const heading=document.createElement('h2');heading.textContent=row.label+(data.selected===row.id?' · Selected':'');card.append(heading);const detail=document.createElement('p');detail.className='detail';detail.textContent=row.detail;card.append(detail);
const select=document.createElement('button');select.textContent=data.selected===row.id?'Selected for new chats':'Use for new chats';select.disabled=data.busy||data.selected===row.id||['unavailable','setup_required','sdk_required'].includes(row.connection_status);select.onclick=()=>vscode.postMessage({type:'select',provider:row.id});card.append(select);
for(const action of row.actions){const button=document.createElement('button');button.textContent=action.label;button.disabled=data.busy;button.onclick=()=>vscode.postMessage({type:'action',provider:row.id,action:action.id});card.append(button);}main.append(card);}});vscode.postMessage({type:'ready'});</script></body></html>`;
}
