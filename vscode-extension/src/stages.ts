/** Each stage's model and reasoning level for new chats: one dropdown per stage, from the public AutoCode CLI. */
import * as vscode from "vscode";
import { randomBytes } from "node:crypto";
import { invoke, tail } from "./core/cli";
import { backendArgs, currentRoute, fillSet, readStages, runListCommand, runRoute, type Stage, type StageRoute, type StageView } from "./core/stages";
import { listKey, type StageListCache } from "./core/stage-cache";
import { readSettings, routeKeyOf, type Settings } from "./settings";

/**
 * The CLI options for a stage command, run in the folder new chats start in.
 *
 * `autocode stages` resolves the backend and lists the models for its working directory, as a new
 * run does for its workspace (the project's `.autocode/config.toml` engine, a project's model
 * listing). Run from the editor's own directory, it could show and save the choices of another
 * backend than the one new chats here run on. With several folders the chat asks which one; the
 * panel takes the first.
 */
export function inFolder(settings: Settings): Settings {
  const folder = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  return folder ? { ...settings, cwd: folder } : settings;
}

/** A started chat's own stages: the list its status view names (`stage_models.next_command`) and its folder. */
export interface RunStages { list: string[]; cwd: string }

/** The run a command argument names, or undefined for new chats' stage models. */
export function runStages(value: unknown): RunStages | undefined {
  if (!value || typeof value !== "object") return undefined;
  const row = value as Record<string, unknown>;
  const list = runListCommand(row.list);
  return list && typeof row.cwd === "string" && row.cwd ? { list, cwd: row.cwd } : undefined;
}

/** A stage, a model its list offers and one of that model's levels, as the person picked them. */
export interface StagePick { stage: Stage; model: string; effort: string }

/**
 * The chat box's Models picker: a stage, then a model the core offers it, then one of that model's
 * levels, from the list the core gave (`stages list --json`). A model the list marks unavailable is
 * not offered; the caller fills only the stage's own named command with the pick, so the core checks
 * it again. `now` says what the stage runs in this chat and `current` which model and level that is.
 */
export async function pickStageModel(view: StageView, now: (stage: Stage) => string,
  current: (stage: Stage) => StageRoute | null, scope: string): Promise<StagePick | undefined> {
  const stages = view.stages.filter((stage) => stage.options.some((option) => !option.unavailable));
  if (!stages.length) {
    void vscode.window.showInformationMessage(view.optionsStop ? `Models cannot be listed: ${view.optionsStop}` : "No stage offers another model.");
    return undefined;
  }
  const stagePick = await vscode.window.showQuickPick(
    stages.map((stage) => ({ label: stage.label, description: now(stage), detail: stage.does, stage })),
    { placeHolder: `Choose a stage to change ${scope}`, matchOnDescription: true });
  if (!stagePick) return undefined;
  const stage = stagePick.stage;
  const route = current(stage);
  const modelPick = await vscode.window.showQuickPick(
    stage.options.filter((option) => !option.unavailable).map((option) => ({
      label: option.model, model: option.model,
      description: [option.model === route?.model ? "current" : "", option.family ?? ""].filter(Boolean).join(" · ") || undefined,
    })),
    { placeHolder: `${stage.label} model ${scope} (now ${now(stage)})`, matchOnDescription: true });
  if (!modelPick) return undefined;
  const option = stage.options.find((row) => row.model === modelPick.model);
  const levels = option?.effortChoices ?? [];
  if (!levels.length) return undefined;
  const keep = modelPick.model === route?.model ? route?.effort : null;
  const effortPick = levels.length === 1 ? { level: levels[0]! } : await vscode.window.showQuickPick(
    levels.map((level) => ({ label: level, level, description: level === keep ? "current" : undefined })),
    { placeHolder: `${stage.label} reasoning level on ${modelPick.model}` });
  if (!effortPick) return undefined;
  return { stage, model: modelPick.model, effort: effortPick.level };
}

export class StagesPanel implements vscode.Disposable {
  private panel?: vscode.WebviewPanel;
  private view?: StageView;
  /** Set while the panel shows one started chat's stages instead of new chats'. */
  private target?: RunStages;
  private busy = false;
  private error = "";
  private note = "";

  /** `lists` is the new-chat list the chat shows: this panel opens on it and keeps what it reads there. */
  constructor(private readonly lists?: StageListCache) {}

  async show(target?: RunStages): Promise<void> {
    this.target = target;
    this.view = target ? undefined : this.lists?.peek(newChatCall().key); this.error = ""; this.note = "";
    if (this.panel) this.panel.reveal();
    else {
      const panel = vscode.window.createWebviewPanel("autocode.stages", "AutoCode Stage Models", vscode.ViewColumn.One, { enableScripts: true, localResourceRoots: [] });
      this.panel = panel;
      panel.webview.html = stagesHtml();
      panel.onDidDispose(() => { if (this.panel === panel) this.panel = undefined; });
      panel.webview.onDidReceiveMessage((message) => void this.receive(message));
    }
    this.panel.title = target ? "AutoCode: This Chat's Models" : "AutoCode Stage Models";
    await this.refresh();
  }

  dispose(): void { this.panel?.dispose(); }

  private post(): void {
    const now = this.target ? runRoute : currentRoute;
    const view = this.view && { ...this.view, stages: this.view.stages.map((stage) => ({ ...stage, now: now(stage) })) };
    void this.panel?.webview.postMessage({ view, busy: this.busy, error: this.error, note: this.note, scope: this.target ? "run" : "new" });
  }

  private async refresh(): Promise<void> {
    const call = newChatCall();
    const result = this.target
      ? await invoke({ ...readSettings(), cwd: this.target.cwd }, this.target.list)
      : await invoke(call.settings, call.argv);
    if (result.code !== 0) throw new Error(tail(result));
    this.view = readStages(result.stdout);
    if (!this.target) this.lists?.put(call.key, result.stdout);
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
      const target = this.target;
      if (target && !this.view?.changeable) throw new Error(this.view?.reason ?? "This chat's models cannot change now.");
      const result = await invoke(target ? { ...readSettings(), cwd: target.cwd } : inFolder(readSettings()), argv);
      if (result.code !== 0) throw new Error(tail(result));
      // A run's change prints a sentence, not the view: read the run's list again.
      if (target) await this.refresh();
      else {
        this.view = readStages(result.stdout);
        // New chats show the saved choice: their list is read again in the background.
        this.lists?.expire();
      }
      this.note = target
        ? `This chat runs the ${stage.label} on ${message.model} at ${message.effort} from its next launch.`
        : message.type === "set"
        ? `New chats run the ${stage.label} on ${message.model} at ${message.effort}.`
        : `The ${stage.label} is back to its default.`;
    } catch (error) { this.error = error instanceof Error ? error.message : String(error); }
    finally { this.busy = false; this.post(); }
  }
}

/** The list new chats here read (`stages list --json`), where it runs, and its key in the shared cache. */
export function newChatCall(): { settings: Settings; argv: string[]; key: string } {
  const settings = inFolder(readSettings());
  const argv = ["stages", "list", "--json", ...backendArgs(settings.runOptions, routeKeyOf(settings))];
  return { settings, argv, key: listKey(settings.cwd, settings.command, settings.prefix, argv) };
}

export function stagesHtml(): string {
  const nonce = randomBytes(18).toString("hex");
  return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}'"><style nonce="${nonce}">
body{font:var(--vscode-font-size) var(--vscode-font-family);color:var(--vscode-foreground);background:var(--vscode-editor-background);max-width:760px;padding:20px;margin:auto}article{border:1px solid var(--vscode-panel-border);border-radius:8px;margin:16px 0;padding:16px}button{background:var(--vscode-button-background);color:var(--vscode-button-foreground);border:0;padding:8px 12px;margin:4px 8px 4px 0;cursor:pointer}button:disabled{opacity:.5;cursor:default}select{background:var(--vscode-dropdown-background);color:var(--vscode-dropdown-foreground);border:1px solid var(--vscode-dropdown-border);padding:4px;margin:4px 8px 4px 0;max-width:100%}.muted{opacity:.8;line-height:1.5}#error{color:var(--vscode-errorForeground)}h2{font-size:1.15em}</style></head><body>
<h1 id="title">Stage models</h1><p id="intro">Choose the model and reasoning level each stage of a new chat runs. Every new chat uses these; chats already started keep their own.</p><button id="refresh">Refresh</button><p id="error" role="alert"></p><p id="note" aria-live="polite"></p><p id="stop" class="muted"></p><main id="stages" aria-live="polite"></main>
<script nonce="${nonce}">const vscode=acquireVsCodeApi();const main=document.getElementById('stages');
document.getElementById('refresh').onclick=()=>vscode.postMessage({type:'refresh'});
function text(tag,value,cls){const node=document.createElement(tag);node.textContent=value;if(cls)node.className=cls;return node;}
window.addEventListener('message',({data})=>{main.replaceChildren();if(data.scope==='run'){document.getElementById('title').textContent="This chat's models";document.getElementById('intro').textContent="Choose the model and reasoning level each stage of this chat runs from its next launch. New chats keep using Stage models.";}document.getElementById('error').textContent=data.error||'';document.getElementById('note').textContent=data.note||'';document.getElementById('refresh').disabled=data.busy;const view=data.view;if(!view)return;
const locked=view.changeable===false;document.getElementById('stop').textContent=view.optionsStop?'Models cannot be listed: '+view.optionsStop:locked?(view.reason||'These models cannot change now.'):'Backend: '+view.label;
for(const stage of view.stages){const card=document.createElement('article');card.append(text('h2',stage.label),text('p',stage.does,'muted'),text('p','Advice: '+stage.advice,'muted'),text('p','Now: '+stage.now));
const models=document.createElement('select');models.setAttribute('aria-label',stage.label+' model');const levels=document.createElement('select');levels.setAttribute('aria-label',stage.label+' reasoning level');
const current=(stage.chosen&&stage.chosen.model)||stage.default.model;const currentEffort=(stage.chosen&&stage.chosen.effort)||stage.default.effort;
for(const option of stage.options){const item=document.createElement('option');item.value=option.model;item.textContent=option.model+(option.family?' · '+option.family:'')+(option.unavailable?' — not available: '+option.unavailable:'');item.disabled=!!option.unavailable;item.selected=option.model===current;models.append(item);}
const fill=()=>{levels.replaceChildren();const option=stage.options.find((row)=>row.model===models.value);for(const level of option?option.effortChoices:[]){const item=document.createElement('option');item.value=level;item.textContent=level;item.selected=level===currentEffort;levels.append(item);}};
models.onchange=fill;fill();const save=document.createElement('button');save.textContent='Save';save.disabled=data.busy||locked||!stage.options.length;save.onclick=()=>vscode.postMessage({type:'set',stage:stage.id,model:models.value,effort:levels.value});card.append(models,levels,save);
if(stage.reset){const reset=document.createElement('button');reset.textContent='Use the default';reset.disabled=data.busy;reset.onclick=()=>vscode.postMessage({type:'reset',stage:stage.id});card.append(reset);}main.append(card);}});vscode.postMessage({type:'ready'});</script></body></html>`;
}
