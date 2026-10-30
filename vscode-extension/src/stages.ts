/** Each stage's model and reasoning level for new chats: one dropdown per stage, from the public AutoCode CLI. */
import * as vscode from "vscode";
import { randomBytes } from "node:crypto";
import { invoke, tail } from "./core/cli";
import { backendArgs, currentRoute, fillSet, readStages, type StageView } from "./core/stages";
import { readSettings, routeKeyOf } from "./settings";

export class StagesPanel implements vscode.Disposable {
  private panel?: vscode.WebviewPanel;
  private view?: StageView;
  private busy = false;
  private error = "";
  private note = "";

  async show(): Promise<void> {
    if (this.panel) this.panel.reveal();
    else {
      const panel = vscode.window.createWebviewPanel("autocode.stages", "AutoCode Stage Models", vscode.ViewColumn.One, { enableScripts: true, localResourceRoots: [] });
      this.panel = panel;
      panel.webview.html = stagesHtml();
      panel.onDidDispose(() => { if (this.panel === panel) this.panel = undefined; });
      panel.webview.onDidReceiveMessage((message) => void this.receive(message));
    }
    await this.refresh();
  }

  dispose(): void { this.panel?.dispose(); }

  private post(): void {
    const view = this.view && { ...this.view, stages: this.view.stages.map((stage) => ({ ...stage, now: currentRoute(stage) })) };
    void this.panel?.webview.postMessage({ view, busy: this.busy, error: this.error, note: this.note });
  }

  private async refresh(): Promise<void> {
    const settings = readSettings();
    const result = await invoke(settings, ["stages", "list", "--json", ...backendArgs(settings.runOptions, routeKeyOf(settings))]);
    if (result.code !== 0) throw new Error(tail(result));
    this.view = readStages(result.stdout);
    this.post();
  }

  private async receive(message: { type?: string; stage?: string; model?: string; effort?: string }): Promise<void> {
    if (this.busy) return;
    this.busy = true; this.error = ""; this.note = ""; this.post();
    try {
      if (message.type === "ready" || message.type === "refresh") { await this.refresh(); return; }
      // The webview supplies only a stage ID, a model and a level. Re-read the core's
      // view and fill only its own named command, so nothing it does not offer is sent.
      await this.refresh();
      const stage = this.view?.stages.find((row) => row.id === message.stage);
      if (!stage) return;
      let argv: string[] | null = null;
      if (message.type === "set") argv = fillSet(stage, String(message.model ?? ""), String(message.effort ?? ""));
      else if (message.type === "reset") argv = stage.reset;
      if (!argv) return;
      const result = await invoke(readSettings(), argv);
      if (result.code !== 0) throw new Error(tail(result));
      this.view = readStages(result.stdout);
      this.note = message.type === "set"
        ? `New chats run the ${stage.label} on ${message.model} at ${message.effort}.`
        : `The ${stage.label} is back to its default.`;
    } catch (error) { this.error = error instanceof Error ? error.message : String(error); }
    finally { this.busy = false; this.post(); }
  }
}

export function stagesHtml(): string {
  const nonce = randomBytes(18).toString("hex");
  return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}'"><style nonce="${nonce}">
body{font:var(--vscode-font-size) var(--vscode-font-family);color:var(--vscode-foreground);background:var(--vscode-editor-background);max-width:760px;padding:20px;margin:auto}article{border:1px solid var(--vscode-panel-border);border-radius:8px;margin:16px 0;padding:16px}button{background:var(--vscode-button-background);color:var(--vscode-button-foreground);border:0;padding:8px 12px;margin:4px 8px 4px 0;cursor:pointer}button:disabled{opacity:.5;cursor:default}select{background:var(--vscode-dropdown-background);color:var(--vscode-dropdown-foreground);border:1px solid var(--vscode-dropdown-border);padding:4px;margin:4px 8px 4px 0;max-width:100%}.muted{opacity:.8;line-height:1.5}#error{color:var(--vscode-errorForeground)}h2{font-size:1.15em}</style></head><body>
<h1>Stage models</h1><p>Choose the model and reasoning level each stage of a new chat runs. Every new chat uses these; chats already started keep their own.</p><button id="refresh">Refresh</button><p id="error" role="alert"></p><p id="note" aria-live="polite"></p><p id="stop" class="muted"></p><main id="stages" aria-live="polite"></main>
<script nonce="${nonce}">const vscode=acquireVsCodeApi();const main=document.getElementById('stages');
document.getElementById('refresh').onclick=()=>vscode.postMessage({type:'refresh'});
function text(tag,value,cls){const node=document.createElement(tag);node.textContent=value;if(cls)node.className=cls;return node;}
window.addEventListener('message',({data})=>{main.replaceChildren();document.getElementById('error').textContent=data.error||'';document.getElementById('note').textContent=data.note||'';document.getElementById('refresh').disabled=data.busy;const view=data.view;if(!view)return;
document.getElementById('stop').textContent=view.optionsStop?'Models cannot be listed: '+view.optionsStop:'Backend: '+view.label;
for(const stage of view.stages){const card=document.createElement('article');card.append(text('h2',stage.label),text('p',stage.does,'muted'),text('p','Advice: '+stage.advice,'muted'),text('p','Now: '+stage.now));
const models=document.createElement('select');models.setAttribute('aria-label',stage.label+' model');const levels=document.createElement('select');levels.setAttribute('aria-label',stage.label+' reasoning level');
const current=(stage.chosen&&stage.chosen.model)||stage.default.model;const currentEffort=(stage.chosen&&stage.chosen.effort)||stage.default.effort;
for(const option of stage.options){const item=document.createElement('option');item.value=option.model;item.textContent=option.model+(option.family?' · '+option.family:'')+(option.unavailable?' — not available: '+option.unavailable:'');item.disabled=!!option.unavailable;item.selected=option.model===current;models.append(item);}
const fill=()=>{levels.replaceChildren();const option=stage.options.find((row)=>row.model===models.value);for(const level of option?option.effortChoices:[]){const item=document.createElement('option');item.value=level;item.textContent=level;item.selected=level===currentEffort;levels.append(item);}};
models.onchange=fill;fill();const save=document.createElement('button');save.textContent='Save';save.disabled=data.busy||!stage.options.length;save.onclick=()=>vscode.postMessage({type:'set',stage:stage.id,model:models.value,effort:levels.value});card.append(models,levels,save);
if(stage.reset){const reset=document.createElement('button');reset.textContent='Use the default';reset.disabled=data.busy;reset.onclick=()=>vscode.postMessage({type:'reset',stage:stage.id});card.append(reset);}main.append(card);}});vscode.postMessage({type:'ready'});</script></body></html>`;
}
