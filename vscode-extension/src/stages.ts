/** Each stage's model and reasoning level for new chats: one dropdown per stage, from the public AutoCode CLI. */
import * as vscode from "vscode";
import { randomBytes } from "node:crypto";
import { invoke, invokeRead, tail, STAGES_TIMEOUT_MS } from "./core/cli";
import { backendArgs, currentRoute, fillProbe, fillSet, modelArgs, probeOutcome, readEstimate, readStages, runListCommand, runRoute, sameEstimate, type ProbeEstimate, type Stage, type StageRoute, type StageView } from "./core/stages";
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
 * The chat box's Models picker: a stage, then one of the models the core offers it at one of that
 * model's levels, from the list the core gave (`stages list --json`). Model and level are one
 * searchable list ("sonnet · high"), with what the stage runs now first, so a change is two picks.
 * A model the list marks unavailable is not offered; the caller fills only the stage's own named
 * command with the pick, so the core checks it again. `now` says what the stage runs in this chat
 * and `current` which model and level that is.
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
  const items = routeItems(stage, route);
  if (!items.length) return undefined;
  const pick = await vscode.window.showQuickPick(items,
    { placeHolder: `${stage.label} model and level ${scope} (now ${now(stage)}); type to filter`, matchOnDescription: true });
  return pick ? { stage, model: pick.model, effort: pick.effort } : undefined;
}

/** Every model the stage may run at each of its levels, what it runs now first. */
export function routeItems(stage: Stage, route: StageRoute | null): { label: string; description?: string; model: string; effort: string }[] {
  const items = stage.options.filter((option) => !option.unavailable).flatMap((option) => option.effortChoices.map((effort) => {
    const running = option.model === route?.model && (effort === route?.effort || (!route?.effort && option.effortChoices.length === 1));
    return { label: `${option.model} · ${effort}`, model: option.model, effort, running,
      description: [running ? "current" : "", option.family ?? ""].filter(Boolean).join(" · ") || undefined };
  }));
  items.sort((a, b) => Number(b.running) - Number(a.running));
  return items.map(({ running: _running, ...item }) => item);
}

export class StagesPanel implements vscode.Disposable {
  private panel?: vscode.WebviewPanel;
  private view?: StageView;
  /** Set while the panel shows one started chat's stages instead of new chats'. */
  private target?: RunStages;
  private busy = false;
  private error = "";
  private note = "";
  private savedStage: string | null = null;
  private generation = 0;
  private readSequence = 0;
  private viewKey = "";
  /** The estimate last shown per stage, for the exact models it priced: a run must match it. */
  private estimates = new Map<string, { models: string[]; estimate: ProbeEstimate }>();
  /** Stages whose probe is running now: it can take hours, so it runs beside the panel, not inside its busy flag. */
  private measuring = new Set<string>();

  /** `lists` is the new-chat list the chat shows: this panel opens on it and keeps what it reads there. */
  constructor(private readonly lists?: StageListCache) {}

  async show(target?: RunStages): Promise<void> {
    this.generation++;
    this.estimates.clear();
    this.target = target;
    this.viewKey = stageModelsCall().key;
    this.view = target ? undefined : this.lists?.peek(stageModelsCall().key); this.error = ""; this.note = "";
    if (this.panel) this.panel.reveal();
    else {
      const panel = vscode.window.createWebviewPanel("autocode.stages", "AutoCode Stage Models", vscode.ViewColumn.One, { enableScripts: true, localResourceRoots: [] });
      this.panel = panel;
      panel.webview.html = stagesHtml();
      panel.onDidDispose(() => { if (this.panel === panel) { this.panel = undefined; this.generation++; } });
      panel.webview.onDidReceiveMessage((message) => void this.receive(message));
    }
    this.panel.title = target ? "AutoCode: This Chat's Models" : "AutoCode Stage Models";
    this.post();
    await this.refresh();
  }

  dispose(): void { this.panel?.dispose(); }

  private post(): void {
    const now = this.target ? runRoute : currentRoute;
    const view = this.view && { ...this.view, stages: this.view.stages.map((stage) => ({ ...stage, now: now(stage) })) };
    const estimates = Object.fromEntries(this.estimates);
    void this.panel?.webview.postMessage({ view, busy: this.busy, error: this.error, note: this.note, savedStage: this.savedStage, scope: this.target ? "run" : "new", generation: this.generation,
      estimates, measuring: [...this.measuring] });
    this.savedStage = null;
  }

  private async refresh(): Promise<boolean> {
    const generation = this.generation;
    const sequence = ++this.readSequence;
    const panel = this.panel;
    const target = this.target;
    const call = stageModelsCall();
    // Bounded like every other stage read: this panel holds a busy flag across it, and a wedged
    // list would leave every button on it disabled until the window reloaded.
    let result;
    try {
      result = target
        ? await invokeRead({ ...readSettings(), cwd: target.cwd }, target.list, {}, STAGES_TIMEOUT_MS)
        : await invokeRead(call.settings, call.argv, {}, STAGES_TIMEOUT_MS);
    } catch (error) {
      if (generation !== this.generation || sequence !== this.readSequence || panel !== this.panel) return false;
      throw error;
    }
    if (generation !== this.generation || sequence !== this.readSequence || panel !== this.panel) return false;
    if (result.code !== 0) throw new Error(tail(result));
    this.view = readStages(result.stdout);
    this.viewKey = call.key;
    if (!target) this.lists?.put(call.key, result.stdout);
    this.post();
    return true;
  }

  /**
   * What measuring `models` for the stage can cost: its own `estimate` command, filled with the ticked
   * candidates. Nothing runs; the panel shows the estimate the run button then binds to.
   */
  private async estimate(stage: Stage, models: string[], generation = this.generation): Promise<ProbeEstimate> {
    if (!stage.probe) throw new Error(`The ${stage.label} has no probe in this AutoCode.`);
    const argv = fillProbe(stage.probe, stage.probe.estimate, models);
    const result = await invokeRead(inFolder(readSettings()), argv, {}, STAGES_TIMEOUT_MS);
    if (generation !== this.generation) throw new Error("The model settings changed; review the selection again.");
    if (result.code !== 0) throw new Error(tail(result));
    const estimate = readEstimate(result.stdout);
    this.estimates.set(stage.id, { models: [...models], estimate });
    return estimate;
  }

  /**
   * Run the stage's probe on exactly the ticked models within the estimate the person was shown: the
   * button press is their one approval of that spend. The estimate is read again first, and a run whose
   * models or cost no longer match what was shown is refused rather than started.
   */
  private async measure(stage: Stage, models: string[], shownCap: number, generation: number): Promise<void> {
    if (!stage.probe?.run) throw new Error(stage.probe?.stop ?? `The ${stage.label} cannot be measured now.`);
    if (this.measuring.has(stage.id)) return;
    const shown = this.estimates.get(stage.id);
    const estimate = await this.estimate(stage, models, generation);
    if (!sameEstimate(shown, models, estimate, shownCap)) {
      throw new Error("The estimate changed since it was shown. Check it and press Measure again.");
    }
    const argv = fillProbe(stage.probe, stage.probe.run, models, estimate.maxUsd ?? undefined);
    this.measuring.add(stage.id);
    this.note = `Measuring ${models.length} model${models.length === 1 ? "" : "s"} for the ${stage.label}, up to $${(estimate.maxUsd ?? 0).toFixed(2)}. This can take a while.`;
    void vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: `AutoCode: measuring ${stage.label} models` }, async () => {
      try {
        const result = await invoke(inFolder(readSettings()), argv);
        if (result.code !== 0) throw new Error(tail(result));
        this.note = probeOutcome(stage.label, result.stdout);
        this.estimates.delete(stage.id);
        this.lists?.expire();
        await this.refresh();
      } catch (error) { this.error = error instanceof Error ? error.message : String(error); }
      finally { this.measuring.delete(stage.id); this.post(); }
    });
  }

  private async receive(message: { type?: string; stage?: string; model?: string; effort?: string; models?: unknown; cap?: unknown; generation?: number }): Promise<void> {
    if (this.busy) return;
    if (message.type !== "ready" && message.generation !== this.generation) return;
    const generation = this.generation;
    this.busy = true; this.error = ""; this.note = ""; this.post();
    try {
      if (message.type === "ready" || message.type === "refresh") { await this.refresh(); return; }
      if (this.viewKey !== stageModelsCall().key) {
        await this.refresh();
        throw new Error("The backend or workspace changed. Review these models before changing them.");
      }
      // The webview supplies only a stage ID, a model and a level. Re-read the core's
      // view and fill only its own named command, so nothing it does not offer is sent.
      if (!(await this.refresh()) || generation !== this.generation) return;
      const stage = this.view?.stages.find((row) => row.id === message.stage);
      if (!stage) return;
      const models = Array.isArray(message.models) ? message.models.filter((m): m is string => typeof m === "string") : [];
      if (message.type === "estimate") {
        if (models.length) await this.estimate(stage, models, generation);
        else this.estimates.delete(stage.id);
        return;
      }
      if (message.type === "measure") { await this.measure(stage, models, Number(message.cap), generation); return; }
      let argv: string[] | null = null;
      if (message.type === "set") argv = fillSet(stage, String(message.model ?? ""), String(message.effort ?? ""));
      else if (message.type === "reset") argv = stage.reset;
      if (!argv) return;
      const target = this.target;
      if (target && !this.view?.changeable) throw new Error(this.view?.reason ?? "This chat's models cannot change now.");
      const result = await invoke(target ? { ...readSettings(), cwd: target.cwd } : inFolder(readSettings()), argv, {}, STAGES_TIMEOUT_MS);
      if (result.code !== 0) throw new Error(tail(result));
      if (generation !== this.generation) return;
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
      this.savedStage = stage.id;
    } catch (error) { if (generation === this.generation) this.error = error instanceof Error ? error.message : String(error); }
    finally { this.busy = false; this.post(); }
  }
}

function listCall(extraFor: (settings: Settings) => string[] = () => []): { settings: Settings; argv: string[]; key: string } {
  const settings = inFolder(readSettings());
  const argv = ["stages", "list", "--json", ...backendArgs(settings.runOptions, routeKeyOf(settings)), ...extraFor(settings)];
  return { settings, argv, key: listKey(settings.cwd, settings.command, settings.prefix, argv) };
}

/** The global Stage models list (`stages list --json`), without a workspace's temporary new-run model overrides. */
export function stageModelsCall(): { settings: Settings; argv: string[]; key: string } {
  return listCall();
}

/** The list new chats here read (`stages list --json`), where it runs, and its key in the shared cache. */
export function newChatCall(): { settings: Settings; argv: string[]; key: string } {
  return listCall((settings) => modelArgs(settings.runOptions));
}

export function stagesHtml(): string {
  const nonce = randomBytes(18).toString("hex");
  return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}'"><style nonce="${nonce}">
body{font:var(--vscode-font-size) var(--vscode-font-family);color:var(--vscode-foreground);background:var(--vscode-editor-background);max-width:760px;padding:20px;margin:auto}article{border:1px solid var(--vscode-panel-border);border-radius:8px;margin:16px 0;padding:16px}button{background:var(--vscode-button-background);color:var(--vscode-button-foreground);border:0;padding:8px 12px;margin:4px 8px 4px 0;cursor:pointer}button:disabled{opacity:.5;cursor:default}select{background:var(--vscode-dropdown-background);color:var(--vscode-dropdown-foreground);border:1px solid var(--vscode-dropdown-border);padding:4px;margin:4px 8px 4px 0;max-width:100%}.muted{opacity:.8;line-height:1.5}#error{color:var(--vscode-errorForeground)}h2{font-size:1.15em}</style></head><body>
<h1 id="title">Stage models</h1><p id="intro">Choose the model and reasoning level each stage of a new chat runs. Every new chat uses these; chats already started keep their own.</p><button id="refresh">Refresh</button><p id="error" role="alert"></p><p id="note" aria-live="polite"></p><p id="stop" class="muted"></p><main id="stages" aria-live="polite"></main>
<script nonce="${nonce}">const api=acquireVsCodeApi();let context=null;const vscode={postMessage:message=>api.postMessage({...message,generation:context})};const main=document.getElementById('stages');
document.getElementById('refresh').onclick=()=>vscode.postMessage({type:'refresh'});
let drafts=Object.create(null);let probes=Object.create(null);
function text(tag,value,cls){const node=document.createElement(tag);node.textContent=value;if(cls)node.className=cls;return node;}
window.addEventListener('message',({data})=>{if(context!==data.generation){context=data.generation;drafts=Object.create(null);probes=Object.create(null);}if(data.savedStage)delete drafts[data.savedStage];const focus=document.activeElement&&document.activeElement.getAttribute('aria-label');main.replaceChildren();document.getElementById('title').textContent=data.scope==='run'?"This chat's models":"Stage models";document.getElementById('intro').textContent=data.scope==='run'?"Choose the model and reasoning level each stage of this chat runs from its next launch. New chats keep using Stage models.":"Choose the model and reasoning level each stage of a new chat runs. Every new chat uses these; chats already started keep their own.";document.getElementById('error').textContent=data.error||'';document.getElementById('note').textContent=data.note||'';document.getElementById('refresh').disabled=data.busy;const view=data.view;if(!view)return;
const locked=view.changeable===false;document.getElementById('stop').textContent=view.optionsStop?'Models cannot be listed: '+view.optionsStop:locked?(view.reason||'These models cannot change now.'):'Backend: '+view.label;
for(const stage of view.stages){const card=document.createElement('article');card.append(text('h2',stage.label),text('p',stage.does,'muted'),text('p','Advice: '+stage.advice,'muted'),text('p','Now: '+stage.now));
const models=document.createElement('select');models.setAttribute('aria-label',stage.label+' model');const levels=document.createElement('select');levels.setAttribute('aria-label',stage.label+' reasoning level');
const savedModel=(stage.chosen&&stage.chosen.model)||stage.default.model;const savedEffort=(stage.chosen&&stage.chosen.effort)||stage.default.effort;const draft=drafts[stage.id];const valid=draft&&stage.options.some(o=>o.model===draft.model&&!o.unavailable);const current=valid?draft.model:savedModel;const currentEffort=valid?draft.effort:savedEffort;if(draft&&!valid){delete drafts[stage.id];card.append(text('p','Your unsaved model is no longer available; review this choice.','muted'));}
for(const option of stage.options){const item=document.createElement('option');item.value=option.model;item.textContent=option.model+(option.family?' · '+option.family:'')+(option.unavailable?' — not available: '+option.unavailable:'');item.disabled=!!option.unavailable;item.selected=option.model===current;models.append(item);}
const fill=()=>{levels.replaceChildren();const option=stage.options.find((row)=>row.model===models.value);for(const level of option?option.effortChoices:[]){const item=document.createElement('option');item.value=level;item.textContent=level;item.selected=level===currentEffort;levels.append(item);}};
models.onchange=()=>{fill();drafts[stage.id]={model:models.value,effort:levels.value};};levels.onchange=()=>{drafts[stage.id]={model:models.value,effort:levels.value};};fill();const save=document.createElement('button');save.textContent='Save';save.disabled=data.busy||locked||!stage.options.length;save.onclick=()=>vscode.postMessage({type:'set',stage:stage.id,model:models.value,effort:levels.value});card.append(models,levels,save);
if(stage.reset){const reset=document.createElement('button');reset.textContent='Use the default';reset.disabled=data.busy;reset.onclick=()=>vscode.postMessage({type:'reset',stage:stage.id});card.append(reset);}
if(stage.probe&&data.scope!=='run')card.append(probeSection(stage,data));main.append(card);}if(focus){const target=[...main.querySelectorAll('[aria-label]')].find(n=>n.getAttribute('aria-label')===focus);if(target&&!target.disabled)target.focus();}});
function probeSection(stage,data){const probe=stage.probe;const box=document.createElement('details');const shown=(data.estimates||{})[stage.id];const running=(data.measuring||[]).includes(stage.id);const state=probes[stage.id]||(probes[stage.id]={picked:null,filter:'',open:false});box.open=state.open||!!shown||running;box.append(text('summary','Measure models for this stage'));
box.append(text('p','The ticked models are suggested, at no cost, from what your accounts list. Measuring runs short tasks on each and ranks them for automatic selection. Nothing runs until you press Measure.','muted'));
const filter=document.createElement('input');filter.type='search';filter.placeholder='Filter models';filter.setAttribute('aria-label',stage.label+' models to measure');filter.value=state.filter;box.append(filter);
const list=document.createElement('div');const picked=new Set(state.picked!==null?state.picked:shown?shown.models:probe.candidates.filter((row)=>row.ticked).map((row)=>row.model));const checks=[];
for(const row of probe.candidates){const label=document.createElement('label');label.style.display='block';const check=document.createElement('input');check.type='checkbox';check.value=row.model;check.checked=picked.has(row.model);check.disabled=data.busy||running;checks.push(check);
const facts=[row.effort||'its default level',row.family,row.billing,row.measured?(row.measured.qualified?'measured, qualified':'measured, did not qualify')+(row.measured.date?' on '+row.measured.date:''):'not measured'].filter(Boolean).join(' · ');
label.append(check,document.createTextNode(' '+row.model+' · '+facts));label.title=[row.reason,row.measured&&row.measured.reason].filter(Boolean).join('. ');list.append(label);}
filter.oninput=()=>{state.filter=filter.value;for(const label of list.children)label.style.display=label.textContent.toLowerCase().includes(filter.value.toLowerCase())?'block':'none';};
const ticked=()=>checks.filter((c)=>c.checked).map((c)=>c.value);filter.oninput();for(const check of checks)check.onchange=()=>{state.picked=ticked();vscode.postMessage({type:'estimate',stage:stage.id,models:state.picked});};box.append(list);
const estimate=shown&&shown.estimate;box.append(text('p',estimate?estimate.quickCalls+' to '+estimate.calls+' requests, '+(estimate.maxUsd===null?'cost unknown: a ticked model has no price on record, so it cannot run within a cap':'up to $'+estimate.maxUsd.toFixed(2))+' ('+estimate.basis+')':'Tick models to see what measuring them can cost.','muted'));
const go=document.createElement('button');go.textContent=running?'Measuring…':estimate&&estimate.maxUsd!==null?'Measure '+shown.models.length+' model'+(shown.models.length===1?'':'s')+' (up to $'+estimate.maxUsd.toFixed(2)+')':'Measure';
go.disabled=data.busy||running||!probe.run||!estimate||estimate.maxUsd===null||JSON.stringify(shown.models)!==JSON.stringify(ticked());go.onclick=()=>vscode.postMessage({type:'measure',stage:stage.id,models:shown.models,cap:estimate.maxUsd});for(const check of checks)check.addEventListener('change',()=>{go.disabled=true;});box.append(go);box.ontoggle=()=>{state.open=box.open;if(box.open&&!shown&&!running&&ticked().length)vscode.postMessage({type:'estimate',stage:stage.id,models:ticked()});};if(!probe.run&&probe.stop)box.append(text('p',probe.stop,'muted'));return box;}vscode.postMessage({type:'ready'});</script></body></html>`;
}
