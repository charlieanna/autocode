/**
 * The Tasks board: every AutoCode run of the project in one editor tab, grouped by what it needs, and
 * a picker that starts many GitHub issues at once (owner decision 2026-11-26, AGENTS.md).
 *
 * A thin client like the rest of the extension. It reads `runs --json`, `issue queue status --json` and,
 * for the picker, `issue list --json`. It acts on a run only through the commands the Runs list already
 * has (open its chat, continue it), so every decision still happens in that run's chat against its own
 * status view. It takes the queue's named `add` filled with what the person ticked, the queue's named
 * `run`, and an issue's named `open_pr` when the person presses it. The core decides which issue starts
 * next and how many work at once.
 */

import vscode = require("vscode");
import { randomBytes } from "node:crypto";
import { invoke, invokeRead, stream, tail, STAGES_TIMEOUT_MS, STATUS_TIMEOUT_MS } from "./core/cli";
import {
  boardRows, groupCounts, queueAddArgs, readIssueListing, readQueueStatus, readRuns, startable, GROUPS,
  type BoardRow, type IssueListing, type QueueStatus,
} from "./core/board";
import { chosenMode, FULL_START_ARGS, readRunModes } from "./core/modes";
import { readSettings, spendTarget } from "./settings";

/** The chat's remembered run mode for new chats (chat.ts): issues start in the same mode. */
const MODE_KEY = "autocode.chat.mode";
const SCOPE_KEY = "autocode.board.scope";

type Message =
  | { type: "ready" | "refresh" | "pick" | "closePicker" | "resumeQueue" }
  | { type: "scope"; all: boolean }
  | { type: "open" | "continue"; key: string }
  | { type: "openPr"; key: string }
  | { type: "start"; refs: string[]; limit: number };

export class TasksBoard implements vscode.Disposable {
  private panel?: vscode.WebviewPanel;
  private rows: BoardRow[] = [];
  private queues: QueueStatus[] = [];
  private listing?: IssueListing;
  private error = "";
  private notice = "";
  private busy = "";
  private timer?: NodeJS.Timeout;
  private sequence = 0;
  /** The schedulers this window started, by project: one each, and the core's lock refuses a second. */
  private readonly schedulers = new Map<string, Promise<unknown>>();

  constructor(
    private readonly storage: vscode.Memento,
    private readonly log: vscode.OutputChannel,
    private readonly folder: () => string | undefined,
  ) {}

  private get all(): boolean { return this.storage.get<boolean>(SCOPE_KEY) === true; }

  async show(): Promise<void> {
    if (this.panel) { this.panel.reveal(); await this.refresh(); return; }
    const panel = vscode.window.createWebviewPanel("autocode.board", "AutoCode Tasks", vscode.ViewColumn.One,
      { enableScripts: true, localResourceRoots: [], retainContextWhenHidden: true });
    this.panel = panel;
    panel.iconPath = new vscode.ThemeIcon("checklist");
    panel.webview.html = boardHtml();
    panel.onDidDispose(() => {
      if (this.panel !== panel) return;
      this.panel = undefined;
      this.listing = undefined;
      clearTimeout(this.timer);
    });
    panel.onDidChangeViewState(() => { if (panel.visible) void this.refresh(); });
    panel.webview.onDidReceiveMessage((message: Message) => void this.receive(message));
  }

  dispose(): void { clearTimeout(this.timer); this.panel?.dispose(); }

  private post(): void {
    const projects = this.queues.map((queue) => {
      const waiting = queue.entries.filter((entry) => entry.state === "queued" || entry.state === "starting").length;
      return { project: queue.project, waiting, maxActive: queue.maxActive, working: queue.working,
        runnerActive: queue.runnerActive, resumable: Boolean(queue.nextCommand) };
    });
    void this.panel?.webview.postMessage({
      rows: this.rows, counts: groupCounts(this.rows), groups: GROUPS, all: this.all, queues: projects,
      error: this.error, notice: this.notice, busy: this.busy, hasFolder: Boolean(this.folder()),
      picker: this.listing && {
        repository: this.listing.repository, defaultMaxActive: this.listing.queue.maxActive || this.listing.defaultMaxActive,
        offered: Boolean(this.listing.queueAdd),
        issues: this.listing.issues.map((issue) => ({ ref: issue.ref, number: issue.number, title: issue.title,
          labels: issue.labels, startable: startable(issue),
          state: issue.run ? "Has a run" : issue.queueState === "failed" ? "Could not start" : issue.queueState ? "Queued" : "" })),
      },
    });
  }

  /** Reread the runs and the queue, then again after the poll interval while the tab is open. */
  async refresh(): Promise<void> {
    const panel = this.panel;
    if (!panel) return;
    clearTimeout(this.timer);
    const sequence = ++this.sequence;
    try {
      const settings = readSettings();
      const folder = this.folder();
      const runsArgs = this.all || !folder ? ["runs", "--all", "--json"] : ["runs", "--workspace", folder, "--json"];
      const [runs, queue] = await Promise.all([
        invokeRead(folder ? { ...settings, cwd: folder } : settings, runsArgs, {}, STAGES_TIMEOUT_MS),
        // The queue is optional: an older AutoCode, or a folder that is not a GitHub checkout, has none.
        folder ? invokeRead({ ...settings, cwd: folder }, ["issue", "queue", "status", "--project", folder, "--json"], {},
          STATUS_TIMEOUT_MS).catch(() => undefined) : Promise.resolve(undefined),
      ]);
      if (this.panel !== panel || sequence !== this.sequence) return;
      if (runs.code !== 0) throw new Error(tail(runs));
      this.queues = [];
      if (folder && queue?.code === 0) {
        try { this.queues = [readQueueStatus(queue.stdout, folder)]; } catch (error) {
          this.log.appendLine(`AutoCode Tasks: the issue queue could not be read: ${messageOf(error)}`);
        }
      } else if (folder && queue) {
        this.log.appendLine(`AutoCode Tasks: no issue queue for ${folder}: ${tail(queue)}`);
      }
      this.rows = boardRows(readRuns(runs.stdout), this.queues);
      this.error = "";
    } catch (error) {
      if (this.panel !== panel || sequence !== this.sequence) return;
      this.error = messageOf(error);
    }
    this.post();
    if (this.panel === panel && panel.visible) {
      this.timer = setTimeout(() => void this.refresh(), Math.max(readSettings().pollMs, 3000));
    }
  }

  private async receive(message: Message): Promise<void> {
    const row = "key" in message ? this.rows.find((item) => item.key === message.key) : undefined;
    try {
      switch (message.type) {
        case "ready": case "refresh": await this.refresh(); return;
        case "scope": await this.storage.update(SCOPE_KEY, message.all); await this.refresh(); return;
        case "open": if (row?.run) await vscode.commands.executeCommand("autocode.openRun", row.run); return;
        case "continue":
          if (row?.run) await vscode.commands.executeCommand("autocode.continueRun", row.run);
          await this.refresh();
          return;
        case "openPr": await this.openPr(row); return;
        case "pick": await this.pick(); return;
        case "closePicker": this.listing = undefined; this.post(); return;
        case "start": await this.start(message.refs, message.limit); return;
        case "resumeQueue": await this.resumeQueue(); return;
      }
    } catch (error) {
      this.error = messageOf(error);
      this.busy = "";
      this.post();
    }
  }

  /** Read the repository's open issues for the picker; it can take a few seconds on GitHub. */
  private async pick(): Promise<void> {
    const folder = this.folder();
    if (!folder) throw new Error("Open a folder that is a clone of a GitHub repository first.");
    this.busy = "Reading the repository's open issues…";
    this.error = "";
    this.post();
    const result = await invokeRead({ ...readSettings(), cwd: folder }, ["issue", "list", "--project", folder, "--json"], {},
      STAGES_TIMEOUT_MS);
    this.busy = "";
    if (result.code !== 0) throw new Error(tail(result));
    this.listing = readIssueListing(result.stdout, folder);
    this.post();
  }

  /** Queue the ticked issues and start the project's scheduler; each issue then works until it needs you. */
  private async start(refs: string[], limit: number): Promise<void> {
    const listing = this.listing;
    const folder = this.folder();
    if (!listing || !folder || listing.project !== folder) throw new Error("Read the issues again before starting them.");
    const settings = readSettings();
    // Issues start the way a new chat does: its run mode's flags and the saved options for new chats.
    let modeArgs: readonly string[] = FULL_START_ARGS;
    try {
      const modes = await invokeRead(settings, ["modes", "list", "--json"], {}, STATUS_TIMEOUT_MS);
      if (modes.code === 0) modeArgs = chosenMode(readRunModes(modes.stdout), this.storage.get<string>(MODE_KEY))?.startArgs ?? FULL_START_ARGS;
    } catch { /* an AutoCode without run modes starts the full flow */ }
    const args = queueAddArgs(listing, refs, limit, [...modeArgs, ...settings.runOptions]);
    const provider = spendTarget(settings);
    const proceed = await vscode.window.showWarningMessage(
      `Start ${refs.length} issue${refs.length === 1 ? "" : "s"}, at most ${limit} working at once?`,
      { modal: true, detail: `Each issue gets its own branch, worktree and chat, and runs through ${provider === "its default" ? "the default provider" : `provider "${provider}"`}, which spends your model allowance. Each one still asks you its questions and waits for you to approve its plan.` },
      "Start issues",
    );
    if (proceed !== "Start issues") return;
    this.busy = "Queueing the issues…";
    this.post();
    this.log.appendLine(`$ autocode ${args.join(" ")}`);
    const added = await invoke({ ...settings, cwd: folder }, args);
    this.log.append(added.stdout);
    if (added.code !== 0) throw new Error(tail(added));
    this.listing = undefined;
    this.busy = "";
    this.notice = `Queued ${refs.length} issue${refs.length === 1 ? "" : "s"}. They start as slots free up; each one shows here as it does.`;
    await this.refresh();
    await this.resumeQueue();
  }

  /** Take the queue's named `run`: the core's scheduler, kept running by this window until every issue has started. */
  private async resumeQueue(): Promise<void> {
    const folder = this.folder();
    const queue = this.queues.find((item) => item.project === folder);
    if (!folder || !queue?.nextCommand || this.schedulers.has(folder)) return;
    const settings = readSettings();
    this.log.appendLine(`$ autocode ${queue.nextCommand.join(" ")}`);
    const running = stream({ ...settings, cwd: folder }, queue.nextCommand, {}, (chunk) => this.log.append(chunk))
      .then((result) => {
        this.log.appendLine(`[autocode issue queue exited ${result.code}]`);
        if (result.code !== 0) this.error = `The issue queue stopped: ${tail(result)}`;
      })
      .catch((error) => { this.error = `The issue queue stopped: ${messageOf(error)}`; })
      .finally(() => { this.schedulers.delete(folder); void this.refresh(); });
    this.schedulers.set(folder, running);
    await this.refresh();
  }

  /** The issue's named `open_pr`: push its branch and open a draft pull request, after the person confirms. */
  private async openPr(row: BoardRow | undefined): Promise<void> {
    const folder = this.folder();
    const issue = row?.issue && this.queues.flatMap((queue) => queue.issues).find((item) => item.ref === row.issue!.ref);
    if (!folder || !issue?.openPr) throw new Error("AutoCode does not offer a pull request for this run now.");
    const proceed = await vscode.window.showWarningMessage(
      `Open a draft pull request for ${issue.ref}?`,
      { modal: true, detail: `AutoCode commits the finished change on ${issue.branch ?? "its branch"}, pushes it, and opens a draft pull request with the run's evidence. Nothing is merged.` },
      "Open pull request",
    );
    if (proceed !== "Open pull request") return;
    this.busy = `Opening the pull request for ${issue.ref}…`;
    this.post();
    this.log.appendLine(`$ autocode ${issue.openPr.join(" ")}`);
    const result = await stream({ ...readSettings(), cwd: folder }, issue.openPr, {}, (chunk) => this.log.append(chunk));
    this.busy = "";
    if (result.code !== 0) throw new Error(tail(result));
    const url = /https?:\/\/\S+/.exec(result.stdout)?.[0];
    this.notice = url ? `Opened ${url}` : `Opened the pull request for ${issue.ref}.`;
    await this.refresh();
  }
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function boardHtml(): string {
  const nonce = randomBytes(18).toString("hex");
  return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}'"><style nonce="${nonce}">
*{box-sizing:border-box}body{font:var(--vscode-font-size) var(--vscode-font-family);color:var(--vscode-foreground);background:var(--vscode-editor-background);margin:0}
.page{max-width:1100px;margin:auto;padding:28px 28px 48px}header{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:18px}
h1{font-size:1.6rem;margin:0 0 6px;letter-spacing:-.02em}.lead{color:var(--vscode-descriptionForeground);margin:0}.tools{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
button{font:inherit;border:1px solid transparent;border-radius:4px;padding:6px 11px;cursor:pointer;color:var(--vscode-button-secondaryForeground);background:var(--vscode-button-secondaryBackground)}
button:hover:not(:disabled){background:var(--vscode-button-secondaryHoverBackground)}button.primary{color:var(--vscode-button-foreground);background:var(--vscode-button-background)}
button.primary:hover:not(:disabled){background:var(--vscode-button-hoverBackground)}button:disabled{opacity:.6;cursor:default}button:focus-visible,input:focus-visible{outline:1px solid var(--vscode-focusBorder);outline-offset:2px}
.seg{display:inline-flex;border:1px solid var(--vscode-panel-border);border-radius:4px;overflow:hidden}.seg button{border-radius:0;background:transparent;color:var(--vscode-foreground)}.seg button.on{background:var(--vscode-button-secondaryBackground)}
.banner{border:1px solid var(--vscode-panel-border);border-radius:6px;padding:10px 14px;margin:0 0 14px;display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
#error{color:var(--vscode-errorForeground)}#error:empty,#notice:empty,#busy:empty{display:none}#notice,#busy{color:var(--vscode-descriptionForeground)}
section{margin-top:20px}h2{font-size:.8rem;text-transform:uppercase;letter-spacing:.08em;color:var(--vscode-descriptionForeground);margin:0 0 6px;display:flex;gap:8px;align-items:center}
.count{background:var(--vscode-badge-background);color:var(--vscode-badge-foreground);border-radius:8px;padding:0 7px;font-size:.72rem}
table{width:100%;border-collapse:collapse}td{padding:8px 8px;border-top:1px solid var(--vscode-panel-border);vertical-align:top}tr:hover td{background:var(--vscode-list-hoverBackground)}
.title{font-weight:600;overflow-wrap:anywhere}.sub{color:var(--vscode-descriptionForeground);font-size:.85em;margin-top:2px;overflow-wrap:anywhere}.num{color:var(--vscode-textLink-foreground);white-space:nowrap}
td.act{text-align:right;white-space:nowrap;width:1%}td.when{white-space:nowrap;color:var(--vscode-descriptionForeground);width:1%}.empty{color:var(--vscode-descriptionForeground);padding:8px 0}
.picker{border:1px solid var(--vscode-focusBorder);border-radius:6px;padding:14px 16px;margin-bottom:16px}.picker h3{margin:0 0 4px}.picker .list{max-height:360px;overflow:auto;margin:10px 0;border-top:1px solid var(--vscode-panel-border)}
.picker label{display:flex;gap:10px;padding:6px 2px;border-bottom:1px solid var(--vscode-panel-border);align-items:flex-start}.picker label.off{opacity:.6}.picker .foot{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
input[type=number]{width:4.5em;font:inherit;color:var(--vscode-input-foreground);background:var(--vscode-input-background);border:1px solid var(--vscode-input-border,transparent);padding:4px 6px;border-radius:3px}
.tag{font-size:.75em;border:1px solid var(--vscode-panel-border);border-radius:8px;padding:0 6px;margin-left:6px;color:var(--vscode-descriptionForeground)}
@media(max-width:600px){.page{padding:18px 14px}td.when{display:none}}
</style></head><body><div class="page">
<header><div><h1>Tasks</h1><p class="lead">Every AutoCode run, grouped by what it needs. Open one to answer it, approve its plan or follow it.</p></div>
<div class="tools"><div class="seg" role="group" aria-label="Which runs"><button id="mine">This project</button><button id="all">All projects</button></div>
<button id="pick" class="primary">Run GitHub issues…</button><button id="refresh">Refresh</button></div></header>
<p id="error" role="alert"></p><p id="busy" role="status"></p><p id="notice" role="status"></p>
<div id="picker"></div><div id="queues"></div><main id="board" aria-live="polite"></main></div>
<script nonce="${nonce}">
const vscode=acquireVsCodeApi();const el=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e;};
const send=(m)=>vscode.postMessage(m);document.getElementById('refresh').onclick=()=>send({type:'refresh'});document.getElementById('pick').onclick=()=>send({type:'pick'});
document.getElementById('mine').onclick=()=>send({type:'scope',all:false});document.getElementById('all').onclick=()=>send({type:'scope',all:true});
let ticked=new Set();let limitValue=null;
function ago(stamp){if(!stamp)return '';const t=Date.parse(stamp);if(isNaN(t))return '';const m=Math.round((Date.now()-t)/60000);if(m<1)return 'just now';if(m<60)return m+'m ago';const h=Math.round(m/60);if(h<48)return h+'h ago';return Math.round(h/24)+'d ago';}
function renderPicker(p,busy){const box=document.getElementById('picker');box.replaceChildren();if(!p)return;const wrap=el('div','picker');
wrap.append(el('h3',null,'Run issues from '+p.repository));wrap.append(el('div','sub','Each issue you tick gets its own branch, worktree and chat. At most the number below work at once; the rest start as slots free up. A run waiting for your answer or approval does not hold a slot.'));
if(!p.offered){wrap.append(el('p',null,'This AutoCode cannot queue issues. Update it to start issues from here.'));}
const list=el('div','list');if(!p.issues.length)list.append(el('div','empty','No open issues.'));
for(const issue of p.issues){const label=el('label',issue.startable?'':'off');const box=document.createElement('input');box.type='checkbox';box.disabled=!issue.startable||!p.offered;box.checked=ticked.has(issue.ref);
box.onchange=()=>{if(box.checked)ticked.add(issue.ref);else ticked.delete(issue.ref);update();};const text=el('div');const head=el('div');head.append(el('span','num','#'+issue.number+' '));head.append(el('span',null,issue.title));
for(const name of issue.labels)head.append(el('span','tag',name));text.append(head);if(issue.state)text.append(el('div','sub',issue.state));label.append(box,text);list.append(label);}
wrap.append(list);const foot=el('div','foot');const lab=el('label');lab.style.border='none';lab.append(document.createTextNode('At most '));const num=document.createElement('input');num.type='number';num.min='1';num.step='1';num.value=String(limitValue??p.defaultMaxActive);num.oninput=()=>{limitValue=Number(num.value);};
lab.append(num,document.createTextNode(' working at once'));const go=el('button','primary','Start');const cancel=el('button',null,'Cancel');
function update(){go.disabled=busy||!ticked.size||!p.offered;go.textContent=ticked.size?'Start '+ticked.size+' issue'+(ticked.size===1?'':'s'):'Start';}
update();go.onclick=()=>send({type:'start',refs:[...ticked],limit:Number(num.value)});cancel.onclick=()=>{ticked=new Set();limitValue=null;send({type:'closePicker'});};
foot.append(lab,go,cancel);wrap.append(foot);box.append(wrap);}
function renderQueues(queues){const box=document.getElementById('queues');box.replaceChildren();for(const q of queues){if(!q.waiting&&!q.runnerActive)continue;const b=el('div','banner');
b.append(el('div',null,q.waiting+' issue'+(q.waiting===1?'':'s')+' waiting to start · at most '+q.maxActive+' working at once'+(q.working!==null?' · '+q.working+' working now':'')+(q.runnerActive?' · starting them as slots free up':'')));
if(q.resumable){const r=el('button','primary','Resume the queue');r.onclick=()=>send({type:'resumeQueue'});b.append(r);}box.append(b);}}
function row(r){const tr=el('tr');const main=el('td');const t=el('div','title');if(r.issue&&r.issue.number){t.append(el('span','num','#'+r.issue.number+' '));}t.append(document.createTextNode(r.title));main.append(t);
const bits=[r.status];if(r.stage)bits.push('next: '+r.stage);if(r.issue&&r.issue.prUrl)bits.push('PR '+r.issue.prUrl);else if(r.issue&&r.issue.branch)bits.push(r.issue.branch);main.append(el('div','sub',bits.join(' · ')));
if(r.detail)main.append(el('div','sub',r.detail.length>300?r.detail.slice(0,300)+'…':r.detail));const when=el('td','when',ago(r.updatedAt));const act=el('td','act');
if(r.action.kind!=='none'){const b=el('button',r.group==='needs'||r.action.kind==='open_pr'?'primary':'',r.action.label);b.onclick=()=>send({type:r.action.kind==='open'?'open':r.action.kind==='continue'?'continue':'openPr',key:r.key});act.append(b);
if(r.action.kind!=='open'&&r.run){const o=el('button',null,'Open');o.style.marginLeft='6px';o.onclick=()=>send({type:'open',key:r.key});act.append(o);}}
tr.append(main,when,act);return tr;}
window.addEventListener('message',({data})=>{document.getElementById('error').textContent=data.error||'';document.getElementById('busy').textContent=data.busy||'';document.getElementById('notice').textContent=data.notice||'';
document.getElementById('mine').className=data.all?'':'on';document.getElementById('all').className=data.all?'on':'';document.getElementById('pick').disabled=!!data.busy||!data.hasFolder;
if(!data.picker){ticked=new Set();limitValue=null;}renderPicker(data.picker,!!data.busy);renderQueues(data.queues||[]);const board=document.getElementById('board');board.replaceChildren();
for(const g of data.groups){const rows=data.rows.filter(r=>r.group===g.id);if(!rows.length&&g.id!=='needs'&&g.id!=='working')continue;const s=el('section');const h=el('h2',null,g.label);h.append(el('span','count',String(rows.length)));s.append(h);
if(!rows.length){s.append(el('div','empty',g.id==='needs'?'Nothing needs you.':'Nothing is working.'));}else{const table=el('table');for(const r of rows)table.append(row(r));s.append(table);}board.append(s);}});
send({type:'ready'});
</script></body></html>`;
}
