// Real recovery renderer with controlled DOM/events; no server or model calls.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('./dashboard_vm');
const source=fs.readFileSync(path.join(__dirname,'../dashboard_app.js'),'utf8');
class Element{
 constructor(tag,text=''){this.tag=tag;this.textContent=text;this.children=[];this.dataset={};this.open=false;this.hidden=false;this.listeners={};}
 append(...rows){this.children.push(...rows);}
 setAttribute(key,value){this[key]=value;}
 addEventListener(kind,fn){this.listeners[kind]=fn;}
 scrollIntoView(){this.scrolled=true;}
 focus(){this.focused=true;}
 querySelector(){return null;}
}
const ids=new Map(),calls=[],notices=[],tabs=[],revealed=[];
const get=id=>{if(!ids.has(id))ids.set(id,new Element('div'));return ids.get(id);};
const context=vm.createContext({console,$:get,n:(tag,text)=>new Element(tag,text),card:()=>new Element('div'),
 button:(label,onclick)=>Object.assign(new Element('button',label),{onclick}),focusKey:element=>element,
 disclosure:(label,key,content)=>{const e=new Element('details',label);e.dataset.key=key;e.append(...content);return e;},
 statusInfo:run=>run.human_request_authorized&&run.human_escalation?.request_token?{group:'attention',label:'Answer needed',reason:'Answer the published question.'}:{group:'stopped',label:'Awaiting Resolver',reason:'No current Resolver request is published.'},
 renderDocument:value=>new Element('pre',JSON.stringify(value)),taskActionBusy:()=>false,unresolvedModelReplacement:()=>false,
 dashboardNotice:message=>notices.push(message),submitTaskAction:(...args)=>calls.push(args),activateTab:tab=>tabs.push(tab),showArtifactPane:tab=>revealed.push(tab),
 startProjectConversation:project=>calls.push(['new',project]),document:{querySelectorAll:()=>[]}});
vm.runInContext('let taskReadError="",latestRun=null,latestData=null;',context);
vm.runInContext(source.slice(source.indexOf('function runProjectWorkspace('),source.indexOf('function taskProjectWorkspace('))
 +source.slice(source.indexOf('function renderRecoveryCard('),source.indexOf('function renderTaskAttention(')),context);
assert.equal(context.runProjectWorkspace({run:'/owned/fixture/run',workspace:'/owned/worktree',project_workspace:'/owned/project'}),'/owned/project','An explicit project identity takes precedence over its execution worktree');
assert.equal(context.runProjectWorkspace({run:'/owned/fixture/run',workspace:'/owned/worktree'}, {conversations:[{attachment:{status:'linked',run:'/owned/fixture/run',project_workspace:'/owned/project'}}]}),'/owned/project','A linked conversation retains its original project identity');
const all=root=>[root,...root.children.flatMap(all)],text=root=>all(root).map(e=>e.textContent).join(' ');
const actions=['resume','abandon','retry_builder','retry_job','retry_report','retry_failed_stage'].map(kind=>({id:kind,kind,label:kind,effect:'The exact inspected action.',args:['--resume-paused']}));
const recovery={version:1,token:'exact',status:'PAUSED_REQUESTED',title:'Task paused',what_happened:'The current step stopped.',retained:'Saved plan and history remain.',failure_groups:[],actions:[{id:'inspect',kind:'inspect',label:'Inspect saved work',effect:'Read only.'},...actions,{id:'feedback',kind:'feedback',label:'Explain what should change',effect:'Draft information.'}]};
const run={run:'/owned/fixture/run',workspace:'/owned/fixture',status:recovery.status,interventions:{mode:'capable',recovery}};
context.fixture=run;vm.runInContext('latestRun=fixture',context);
const host=new Element('div');context.renderRecoveryCard(host,run,recovery);
for(const heading of ['What happened','What is retained','What you can do'])assert.ok(text(host).includes(heading));
const buttons=all(host).filter(e=>e.tag==='button'),inspection=all(host).find(e=>e.tag==='details');
for(const kind of actions.map(a=>a.kind)){
 const button=buttons.find(e=>e.dataset.recoveryAction===kind);assert.equal(button.disabled,true);button.onclick();assert.equal(calls.length,0);
 assert.match(button['aria-describedby'],/recovery-card-inspection/);
}
inspection.open=true;inspection.listeners.toggle();
for(const kind of actions.map(a=>a.kind)){
 const button=buttons.find(e=>e.dataset.recoveryAction===kind);assert.equal(button.disabled,false);button.onclick();
 const call=calls.at(-1);assert.equal(call[1],'recover_pause');assert.equal(call[2].recovery_action,kind);assert.equal(call[2].recovery_token,'exact');
}
assert.equal(calls.length,6,'Each explicit click sends exactly one selected action');
buttons.find(e=>e.dataset.recoveryAction==='inspect').onclick();assert.deepEqual(revealed,['now'],'Inspect reveals Work, opening Details at phone widths');assert.deepEqual(tabs,[]);
get('#change-text').value='Keep my unsent draft';buttons.find(e=>e.dataset.recoveryAction==='feedback').onclick();
assert.equal(get('#change-text').value,'Keep my unsent draft');assert.equal(get('#change-text').focused,true);assert.equal(calls.length,6);
context.fresh={...run,interventions:{...run.interventions,recovery:{...recovery,token:'new'}}};vm.runInContext('latestRun=fresh',context);
buttons.find(e=>e.dataset.recoveryAction==='retry_builder').onclick();assert.equal(calls.length,6);assert.match(notices.at(-1),/pause changed/);
vm.runInContext('taskReadError="offline"',context);inspection.listeners.toggle();assert.ok(buttons.filter(e=>actions.some(a=>a.kind===e.dataset.recoveryAction)).every(e=>e.disabled));
const stopped=new Element('div');context.renderRecoveryCard(stopped,run,{...recovery,actions:[{id:'new',kind:'new_conversation',label:'New conversation',effect:'Keep old history.'}]});
all(stopped).find(e=>e.tag==='button').onclick();assert.deepEqual(calls.at(-1),['new','/owned/fixture']);
const worktree=new Element('div');context.renderRecoveryCard(worktree,{...run,workspace:'/owned/fixture/.autocode/worktrees/task-1',project_workspace:'/owned/fixture'},{...recovery,actions:[{id:'new',kind:'new_conversation',label:'New conversation',effect:'Keep old history.'}]});
all(worktree).find(e=>e.tag==='button').onclick();assert.deepEqual(calls.pop(),['new','/owned/fixture'],'a task worktree starts new work in its project, not the worktree (#809)');
const failures=new Element('div');context.renderRecoveryCard(failures,run,{...recovery,actions:[],failure_groups:Array.from({length:7},(_,i)=>({id:String(i),role:'Builder',count:3,source_revision:'source-'+i,last_reason:i?'different saved error':null}))});
assert.match(text(failures),/3 recorded attempt/);assert.match(text(failures),/2 earlier failure group/);assert.match(text(failures),/No per-attempt explanation was recorded/);
const request={...recovery,category:'request',title:'Review checkpoint',actions:[{id:'decision',kind:'decision',label:'Inspect current request',effect:'No implicit answer.'}]};
for(const authorized of [false,true]){
 const target=new Element('div');context.renderRecoveryCard(target,{...run,human_request_authorized:authorized,human_escalation:{request_token:'current'}},request);
 assert.equal(all(target).some(e=>e.dataset.recoveryAction==='decision'),authorized);
 assert.ok(text(target).includes(authorized?'Answer needed':'Awaiting Resolver'));
 assert.equal(calls.length,7,'Rendering a saved request cannot answer or approve it');
}
const stale=new Element('div');context.renderRecoveryCard(stale,{...run,status:'RUNNING'},{...recovery,token:null,actions:recovery.actions.filter(a=>['inspect','feedback'].includes(a.kind))});
assert.match(text(stale),/Inspect this saved checkpoint/);
assert.doesNotMatch(text(stale),/saved pause/);
assert.match(text(stale),/do not start a new worker/);
console.log('Recovery inspection, exact actions, stale refusal, source-aware history and preserved drafts passed.');

context.openWorkspaceSetup=()=>calls.push(['setup']);
const environment=new Element('div');context.renderRecoveryCard(environment,{...run,status:'PAUSED_TRANSPORT_CHANGED'},{...recovery,cause:'PAUSED_TRANSPORT_CHANGED',actions:[]});
all(environment).find(e=>e.dataset.setupRecovery==='true').onclick();assert.deepEqual(calls.at(-1),['setup']);
assert.match(text(environment),/task stays paused/);

// Execution follows the runner's named flags, never a dashboard list of kinds (#730).
const named={...recovery,token:'named',actions:[
 {id:'retry_future',kind:'retry_future',label:'Future retry',effect:'Runs the flags the runner named.',args:['--resume-paused','--retry-future']},
 {id:'resume',kind:'resume',label:'Resume',effect:'Names no command.'},
]};
context.namedRun={...run,interventions:{...run.interventions,recovery:named}};vm.runInContext('latestRun=namedRun;taskReadError="";',context);
const namedHost=new Element('div');context.renderRecoveryCard(namedHost,context.namedRun,named);
const future=all(namedHost).find(e=>e.dataset.recoveryAction==='retry_future'),bare=all(namedHost).find(e=>e.dataset.recoveryAction==='resume');
assert.equal(future.disabled,true,'An action naming flags waits for inspection like any execution control');
assert.notEqual(bare.disabled,true,'An action naming no flags is not an execution control');
assert.doesNotMatch(bare['aria-describedby'],/recovery-card-inspection/);
const namedInspection=all(namedHost).find(e=>e.tag==='details');namedInspection.open=true;namedInspection.listeners.toggle();
const sent=calls.length;future.onclick();
assert.equal(calls.length,sent+1);assert.equal(calls.at(-1)[1],'recover_pause');
assert.deepEqual({...calls.at(-1)[2]},{recovery_token:'named',recovery_action:'retry_future'},'An unfamiliar action runs by its id; the server reads its flags from fresh status');
bare.onclick();assert.equal(calls.length,sent+1,'A kind without named flags cannot submit a recovery command');
console.log('Recovery execution follows the card args, not a client list of action kinds.');

// A native summary click changes .open before its queued toggle event. Exercise
// the shipped disclosure and both renderers across that deferred event boundary.
function deferredRecoveryPage(){
 const queued=[],requests=[],messages=[];
 class Node{
  constructor(tag,text=''){this.tag=tag;this.textContent=text;this.children=[];this.dataset={};this.listeners={};this.parent=null;this._open=false;this.pendingToggle=false;}
  get isConnected(){return this.root===true||!!this.parent?.isConnected;}
  get childElementCount(){return this.children.length;}
  get open(){return this._open;}
  set open(value){
   value=!!value;if(value===this._open)return;this._open=value;
   if(this.pendingToggle)return;this.pendingToggle=true;
   queued.push(()=>{this.pendingToggle=false;this.ontoggle?.();for(const listener of this.listeners.toggle||[])listener();});
  }
  append(...nodes){for(const node of nodes){node.parent=this;this.children.push(node);}}
  replaceChildren(...nodes){for(const node of this.children)node.parent=null;this.children=[];this.append(...nodes);}
  setAttribute(key,value){this[key]=value;}
  addEventListener(kind,listener){(this.listeners[kind]||=[]).push(listener);}
  querySelector(selector){return all(this).slice(1).find(node=>selector==='summary'?node.tag==='summary':selector==='details[data-recovery-inspection]'&&node.tag==='details'&&node.dataset.recoveryInspection==='true')||null;}
  click(){if(this.disabled)return;if(this.tag==='summary')this.parent.open=!this.parent.open;this.onclick?.();}
 }
 const attention=new Node('div');attention.root=true;
 const page=vm.createContext({console,$:()=>attention,n:(tag,label)=>new Node(tag,label),card:()=>new Node('div'),
  button:(label,onclick)=>Object.assign(new Node('button',label),{onclick}),document:{createElement:tag=>new Node(tag)},
  detailsState:new Map(),latestRun:null,latestData:null,taskReadError:'',taskActionBusy:()=>false,unresolvedModelReplacement:()=>false,
  statusInfo:()=>({group:'stopped',label:'Paused',reason:'Retained checkpoint'}),renderDocument:value=>new Node('pre',JSON.stringify(value)),
  submitTaskAction:(...args)=>requests.push(args),dashboardNotice:message=>messages.push(message),activateTab:()=>{}});
 const range=(start,end)=>source.slice(source.indexOf(start),source.indexOf(end,source.indexOf(start)));
 vm.runInContext(range('function focusKey(', 'function captureControls(')
  +range('function disclosure(', 'async function api(')
  +range('function runProjectWorkspace(', 'function taskProjectWorkspace(')
  +range('function renderRecoveryCard(', 'async function sendChange('),page);
 return {requests,messages,render(current){page.latestRun=current;page.renderTaskAttention(current);},
  inspection:()=>attention.querySelector('details[data-recovery-inspection]'),
  resume:()=>all(attention).find(node=>node.dataset.recoveryAction==='resume'),
  flush(){while(queued.length)queued.shift()();}};
}
const pause={run:'/owned/deferred/run',workspace:'/owned/deferred',status:'PAUSED_REQUESTED',
 interventions:{mode:'capable',recovery:{...recovery,token:'pause-one',actions:[actions[0]]}}};
const deferred=deferredRecoveryPage();deferred.render(pause);
const firstInspection=deferred.inspection();firstInspection.querySelector('summary').click();
assert.equal(firstInspection.open,true,'Native opening precedes its toggle event');
assert.equal(deferred.resume().disabled,true,'The toggle event has deliberately not been delivered');
deferred.render(structuredClone(pause));
assert.notEqual(deferred.inspection(),firstInspection,'Refresh replaces the actual inspection node');
assert.equal(deferred.inspection().open,true,'Same-pause refresh retains an opening before its toggle event');
assert.equal(deferred.resume().disabled,false,'The rebuilt real renderer permits the inspected action');
deferred.resume().click();
assert.equal(deferred.requests.length,1);
assert.equal(deferred.requests[0][1],'recover_pause');
assert.deepEqual({...deferred.requests[0][2]},{recovery_token:'pause-one',recovery_action:'resume'});
deferred.flush();
assert.equal(deferred.inspection().open,true,'Detached opening events cannot close the replacement');
assert.equal(deferred.resume().disabled,false);
deferred.inspection().querySelector('summary').click();
assert.equal(deferred.inspection().open,false,'Native closing also precedes its toggle event');
deferred.render(structuredClone(pause));
assert.equal(deferred.inspection().open,false,'Same-pause refresh retains a deferred close');
assert.equal(deferred.resume().disabled,true);
deferred.resume().click();deferred.flush();
assert.equal(deferred.requests.length,1,'A closed inspection cannot run another recovery action');
assert.equal(deferred.inspection().open,false);
assert.equal(deferred.resume().disabled,true);

for(const current of [
 {...pause,interventions:{...pause.interventions,recovery:{...pause.interventions.recovery,token:'pause-two'}}},
 {...pause,run:'/owned/deferred/other-run'},
]){
 const fresh=deferredRecoveryPage();fresh.render(pause);
 const old=fresh.inspection();old.querySelector('summary').click();fresh.render(current);
 assert.equal(old.isConnected,false,'Refresh detaches the old inspection');
 assert.equal(fresh.inspection().open,false,'Another pause or run requires a fresh inspection');
 assert.equal(fresh.resume().disabled,true);fresh.resume().click();fresh.flush();
 assert.equal(fresh.inspection().open,false,'Detached opening events cannot inspect another pause or run');
 assert.equal(fresh.resume().disabled,true);assert.equal(fresh.requests.length,0);
}
console.log('Deferred opening/closing survives exact-pause refresh; fresh pauses, other runs and detached events stay gated.');

// A New conversation recovery click belongs to the original project, even
// when execution used a separate worktree. Drive the shipped callback, not
// only the project-identity helper, and observe its creation boundary.
const newConversationScopes=[
 {label:'explicit project identity',run:{...run,workspace:'/owned/execution-worktree',project_workspace:'/owned/project'},
  data:{conversations:[{attachment:{status:'linked',run:run.run,project_workspace:'/owned/other-project'}}]}},
 {label:'linked conversation identity',run:{...run,workspace:'/owned/execution-worktree'},
  data:{conversations:[{attachment:{status:'linked',run:run.run,project_workspace:'/owned/project'}}]}},
];
for(const {label,run:projectRun,data} of newConversationScopes){
 context.projectRun=projectRun;context.projectData=data;
 vm.runInContext('latestRun=projectRun;latestData=projectData;taskReadError="";',context);
 const target=new Element('div');
 context.renderRecoveryCard(target,projectRun,{...recovery,actions:[{id:'new',kind:'new_conversation',label:'New conversation',effect:'Keep old history.'}]});
 const before=calls.length;
 all(target).find(element=>element.dataset.recoveryAction==='new').onclick();
 assert.deepEqual(calls.slice(before),[['new','/owned/project']],label+' routes exactly one real recovery click to its parent project, not the execution worktree');
}
