/** Connection metadata and named actions come from the public AutoCode CLI. */
import * as vscode from "vscode";
import { randomBytes } from "node:crypto";
import { invokeRead, tail, STATUS_TIMEOUT_MS, STAGES_TIMEOUT_MS } from "./core/cli";
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
  private progress = "";
  private readSequence = 0;
  constructor(private readonly storage: vscode.Memento) {}

  async show(): Promise<void> {
    if (this.panel) { this.panel.reveal(); if (this.busy) return; }
    else {
      const panel = vscode.window.createWebviewPanel("autocode.connections", "AutoCode Connections", vscode.ViewColumn.One, { enableScripts: true, localResourceRoots: [] });
      this.panel = panel;
      panel.webview.html = connectionsHtml();
      panel.onDidDispose(() => { if (this.panel === panel) { this.panel = undefined; this.busy = false; this.error = ""; this.readSequence++; } });
      panel.webview.onDidReceiveMessage((message) => void this.receive(message));
    }
    await this.refresh();
  }

  dispose(): void { this.panel?.dispose(); }

  private post(): void {
    void this.panel?.webview.postMessage({ connections: this.rows, busy: this.busy, error: this.error, progress: this.progress,
      selected: routeKeyOf(readSettings()) });
  }

  private async refresh(panel = this.panel): Promise<void> {
    // Bounded like a status poll: a wedged read must fail and say so. Without a limit the
    // busy flag this panel holds would never clear and every button would stay disabled.
    const sequence = ++this.readSequence;
    let result;
    try { result = await invokeRead(readSettings(), ["connections", "list", "--json"], {}, STATUS_TIMEOUT_MS); }
    catch (error) { if (this.panel !== panel || sequence !== this.readSequence) return; throw error; }
    if (this.panel !== panel || sequence !== this.readSequence) return;
    if (result.code !== 0) throw new Error(tail(result));
    const data = JSON.parse(result.stdout);
    if (data.version !== 1 || !Array.isArray(data.connections)) throw new Error("Update AutoCode to use Connections.");
    this.rows = data.connections;
    this.post();
  }

  private async receive(message: { type?: string; provider?: string; action?: string }): Promise<void> {
    const panel = this.panel;
    if (this.busy || !panel) return;
    const label = this.rows.find((row) => row.id === message.provider)?.label;
    this.busy = true; this.error = "";
    this.progress = message.action === "check" && label ? `Checking ${label}…` : message.type === "select" && label
      ? `Selecting ${label}…` : "Refreshing connections…";
    this.post();
    try {
      if (message.type === "ready" || message.type === "refresh") { await this.refresh(panel); return; }
      const row = this.rows.find((item) => item.id === message.provider);
      if (!row) return;
      if (message.type === "select") {
        if (["unavailable", "setup_required", "sdk_required"].includes(row.connection_status)) throw new Error("Finish this backend's setup before selecting it.");
        await this.select(row.id);
        return;
      }
      // The webview supplies only an action ID. It cannot supply executable,
      // arguments or credentials. Re-read the core offer before executing it.
      await this.refresh(panel);
      if (this.panel !== panel) return;
      const action = this.rows.find((item) => item.id === row.id)?.actions.find((item) => item.id === message.action);
      if (message.type !== "action" || !action) return;
      const settings = readSettings();
      if (action.id === "login" || action.id === "setup" || action.id === "add") {
        // VS Code launches AutoCode directly as the terminal process, with an
        // argument array. No sendText(), shell interpolation or auth output log.
        vscode.window.createTerminal({ name: `AutoCode: ${row.label} setup`, shellPath: settings.command,
          shellArgs: [...settings.prefix, ...action.next_command], env: settings.env }).show();
      } else if (action.id === "check") {
        // Core owns up to 45s of backend checking plus cleanup; UI expiry must not kill it.
        const result = await invokeRead(settings, action.next_command, {}, STAGES_TIMEOUT_MS);
        if (this.panel !== panel) return;
        if (result.code !== 0) throw new Error(tail(result));
        const checked = JSON.parse(result.stdout) as Connection;
        if (checked.id !== row.id) throw new Error("Connection check returned a different backend.");
        this.rows = this.rows.map((item) => item.id === checked.id ? checked : item);
      }
    } catch (error) { if (this.panel === panel) this.error = error instanceof Error ? error.message : String(error); }
    finally { if (this.panel === panel) { this.busy = false; this.progress = ""; this.post(); } }
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
*{box-sizing:border-box}body{font:var(--vscode-font-size) var(--vscode-font-family);color:var(--vscode-foreground);background:var(--vscode-editor-background);margin:0}.page{max-width:900px;margin:auto;padding:48px 36px 64px}.eyebrow{color:var(--vscode-descriptionForeground);font-size:.72rem;font-weight:700;letter-spacing:.12em;text-transform:uppercase}.intro{display:flex;align-items:flex-end;justify-content:space-between;gap:24px;margin:12px 0 30px}h1{font-size:2rem;line-height:1.1;letter-spacing:-.035em;margin:0 0 10px}.lead{max-width:570px;color:var(--vscode-descriptionForeground);line-height:1.6;margin:0}.section-head{display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--vscode-panel-border);padding-bottom:12px}.section-head h2{font-size:.9rem;margin:0}.section-head span{color:var(--vscode-descriptionForeground);font-size:.8rem}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,320px),1fr));gap:12px;margin-top:16px}article{display:flex;flex-direction:column;min-width:0;min-height:178px;border:1px solid var(--vscode-panel-border);border-radius:8px;padding:20px;background:var(--vscode-sideBar-background,var(--vscode-editor-background))}article.selected{border-color:var(--vscode-focusBorder)}.card-head{display:flex;align-items:center;justify-content:space-between;gap:12px}.card-head h3{font-size:1.05rem;margin:0;font-weight:600}.badge{color:var(--vscode-badge-foreground);background:var(--vscode-badge-background);border-radius:4px;padding:3px 7px;font-size:.72rem;font-weight:600;white-space:nowrap}.detail{color:var(--vscode-descriptionForeground);line-height:1.5;margin:12px 0 20px;overflow-wrap:anywhere}.actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:auto}button{font:inherit;border:1px solid transparent;border-radius:4px;padding:7px 11px;cursor:pointer;color:var(--vscode-button-secondaryForeground);background:var(--vscode-button-secondaryBackground)}button:hover:not(:disabled){background:var(--vscode-button-secondaryHoverBackground)}button.primary{color:var(--vscode-button-foreground);background:var(--vscode-button-background)}button.primary:hover:not(:disabled){background:var(--vscode-button-hoverBackground)}button:focus-visible{outline:1px solid var(--vscode-focusBorder);outline-offset:2px}button:disabled{opacity:.6;cursor:default}#refresh{white-space:nowrap}#error{color:var(--vscode-errorForeground);line-height:1.5}#error:empty{display:none}.footnote{color:var(--vscode-descriptionForeground);font-size:.8rem;line-height:1.5;margin-top:24px}@media(max-width:540px){.page{padding:28px 18px 40px}.intro{align-items:flex-start;flex-direction:column}article{min-height:0}}</style></head><body>
<div class="page"><div class="eyebrow">AutoCode / Settings</div><header class="intro"><div><h1>Connections</h1><p class="lead">Choose the backend for new chats. Sign in through your backend; your credentials stay there.</p></div><button id="refresh" data-focus-key="refresh">Refresh backends</button></header><div class="section-head"><h2>Available backends</h2><span>New chats</span></div><p id="error" role="alert"></p><p id="progress" role="status"></p><main id="connections" class="grid" aria-live="polite"></main><p class="footnote">Existing chats keep their backend and models. Your model choices are saved for each backend.</p></div>
<script nonce="${nonce}">const vscode=acquireVsCodeApi();const main=document.getElementById('connections');let focusedAction=null;
document.getElementById('refresh').onclick=()=>vscode.postMessage({type:'refresh'});
window.addEventListener('message',({data})=>{const focused=document.activeElement;if(focused&&focused.dataset.focusKey)focusedAction=focused.dataset.focusKey;main.replaceChildren();main.setAttribute('aria-busy',String(!!data.busy));document.getElementById('progress').textContent=data.progress||'';document.getElementById('error').textContent=data.error||'';document.getElementById('refresh').disabled=data.busy;
for(const row of data.connections||[]){const card=document.createElement('article');const selected=data.selected===row.id;card.className=selected?'selected':'';const top=document.createElement('div');top.className='card-head';const heading=document.createElement('h3');heading.textContent=row.label;top.append(heading);if(selected){const badge=document.createElement('span');badge.className='badge';badge.textContent='Selected';top.append(badge);}card.append(top);const detail=document.createElement('p');detail.className='detail';detail.textContent=row.detail;card.append(detail);const actions=document.createElement('div');actions.className='actions';
const select=document.createElement('button');select.dataset.focusKey=row.id+':select';select.className=selected?'':'primary';select.textContent=selected?'Selected for new chats':'Use for new chats';select.disabled=data.busy||selected||['unavailable','setup_required','sdk_required'].includes(row.connection_status);select.onclick=()=>vscode.postMessage({type:'select',provider:row.id});actions.append(select);
for(const action of row.actions){const button=document.createElement('button');button.dataset.focusKey=row.id+':'+action.id;button.textContent=action.label;button.disabled=data.busy;button.onclick=()=>vscode.postMessage({type:'action',provider:row.id,action:action.id});actions.append(button);}card.append(actions);main.append(card);}if(!data.busy&&focusedAction){const buttons=[...document.querySelectorAll('button[data-focus-key]')];const target=buttons.find(b=>b.dataset.focusKey===focusedAction&&!b.disabled)||buttons.find(b=>focusedAction.endsWith(':select')&&b.dataset.focusKey.startsWith(focusedAction.slice(0,-6))&&!b.disabled)||document.getElementById('refresh');target.focus();focusedAction=null;}});vscode.postMessage({type:'ready'});</script></body></html>`;
}
