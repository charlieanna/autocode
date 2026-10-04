// Read-only rendering and stale-state behavior; no real server or provider.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('./dashboard_vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../dashboard_app.js'),'utf8');
class Element {
  constructor(tag,text=''){this.tag=tag;this.textContent=text;this.children=[];this.dataset={};this.disabled=false;this.hidden=false;this.classList={add:()=>{},remove:()=>{}};}
  append(...rows){this.children.push(...rows);}
  replaceChildren(...rows){this.children=rows;}
  setAttribute(key,value){this[key]=value;}
  querySelector(){return null;}
  querySelectorAll(){return [];}
}
const ids=new Map();
const $=id=>{if(!ids.has(id))ids.set(id,new Element('div'));return ids.get(id);};
const action=$('#continue-run'),retry=$('#retry-task'),tab=new Element('button'),input=$('#change-text'),restore=$('#restore-task');
action.id='continue-run';retry.id='retry-task';tab.dataset.tab='plan';input.id='change-text';
restore.id='restore-task';restore.dataset.staleSafe='true';
const controls=[action,retry,tab,input,restore];
const context=vm.createContext({console,Date,Number,$,n:(tag,text)=>new Element(tag,text),card:(_text,cls)=>Object.assign(new Element('div'),{className:cls}),human:x=>x,statusAge:()=> '3d ago',markMonitorStale:()=>{},document:{querySelectorAll:()=>controls}});
vm.runInContext('let taskReadError="",taskReadAt=Date.now(),latestRun={run:"/private/tmp/task"},chosen={run:"/tmp/task"};',context);
vm.runInContext(source.slice(source.indexOf('function disableStaleControls()'),source.indexOf('function showRemovedRun(')),context);
$('#now').append(new Element('p','Last known objective'));
context.unavailableRun('Selected run unavailable: offline');
assert.equal($('#now').children[0].textContent,'Last known objective');
assert.match($('#task-load-message').textContent,/last successful read.*Controls are disabled/);
assert.equal($('#sync-state').textContent,'Task status unavailable');
assert.equal(action.disabled,true);assert.equal(input.disabled,true);assert.equal(retry.disabled,false);assert.equal(tab.disabled,false);
assert.equal(restore.disabled,false,'restoration remains available when current verification is unavailable');
assert.equal(input.dataset.staleDisabled,'false');
assert.equal(action['aria-describedby'],'stale-mutation-reason','unsafe controls name the visible stale-state explanation');
context.disableStaleControls();assert.equal(input.dataset.staleDisabled,'false','repeat polls must preserve original enabled state');
vm.runInContext('latestRun=null;taskReadAt=null;',context);context.unavailableRun('offline');
assert.match($('#now').children[0].textContent,/has not loaded/);
vm.runInContext(source.slice(source.indexOf('function metricPanel('),source.indexOf('function renderTaskOverview(')),context);
const metric=context.metricPanel({label:'Mapping',done:4,total:12,updated:'2026-09-18T00:00:00Z',source:'counts.json',description:'Not mastery',areas:[{name:'trees',done:4,total:12}]});
const text=element=>[element.textContent,...element.children.map(text)].join(' ');
assert.match(text(metric),/4 \/ 12/);assert.match(text(metric),/Artifact snapshot/);assert.match(text(metric),/Not mastery/);assert.match(text(metric),/counts.json/);
const failed=context.metricPanel({label:'Mapping',error:'Unavailable'});
assert.match(text(failed),/Unavailable/);assert.doesNotMatch(text(failed),/0 \/ 0/);
// M3 chat-first Work hierarchy (422-1495): the compact Now-working lead stays
// small — the full saved-facts monitor panel is never nested inside it and the
// integrated checklist and facts section are appended as siblings below, per
// the accepted repair of findings F-fe93caa915/F-7555536361.
const taskNowSource=source.slice(source.indexOf('function renderTaskNow('),source.indexOf('function renderPrimaryAction('));
assert.match(taskNowSource,/host\.append\(workChecklistPanel\(run,true\)\)/);
assert.match(taskNowSource,/host\.append\(monitorPanel\(run\)\)/);
assert.doesNotMatch(taskNowSource,/lead\.append\(monitorPanel\(run\)\)/);
// Both panes retain the checklist, but only Work offers detail navigation.
context.workRequirementRows=()=>[{state:'unchecked',criterion:{id:'R1',criterion:'Keep messages readable'}}];
context.planEntryText=row=>row.criterion;
context.button=(label,click)=>Object.assign(new Element('button',label),{onclick:click});
context.appendWorkTasks=host=>host.append(new Element('details','Saved tasks'));
context.disclosure=(label,key,rows)=>{const element=new Element('details',label);element.append(...rows);return element;};
const navigated=[];context.openWorkDetail=(kind,id)=>navigated.push([kind,id]);
vm.runInContext(source.slice(source.indexOf('function workChecklistPanel('),source.indexOf('function renderTaskNow(')),context);
const descend=element=>[element,...element.children.flatMap(descend)];
const planChecklist=context.workChecklistPanel({});
assert.match(text(planChecklist),/Keep messages readable/);
assert.equal(descend(planChecklist).filter(row=>row.tag==='button'||row.tag==='details').length,0,'Plan checklist stays read-only and compact');
const workChecklist=context.workChecklistPanel({},true),links=descend(workChecklist).filter(row=>row.tag==='button');
assert.equal(links.length,1);links[0].onclick();
assert.deepEqual(navigated,[['requirement','R1']]);
assert.equal(descend(workChecklist).filter(row=>row.tag==='details').length,2,'Work includes saved tasks and expandable requirement evidence');

assert.match(source,/if\(!selected\).*Task list checked just now/);
const chip=new Element('p','Running · worker verified'),step=new Element('h2','Tester · Reviewing');
const nextHeading=new Element('strong','Let the current step finish');
const nextDescription=new Element('p','The worker is active.');
const next={querySelector:selector=>selector==='strong'?nextHeading:selector==='p:not(.monitor-kicker)'?nextDescription:null};
const freshness=new Element('div','Checked just now');
const stalePanel={classList:{add:()=>{}},querySelector:selector=>({'.monitor-status-chip':chip,'.monitor-state h2':step,'.monitor-next':next,'.monitor-freshness':freshness})[selector]||null,querySelectorAll:()=>[]};
context.document.querySelectorAll=selector=>selector==='.monitor-panel'?[stalePanel]:controls;
vm.runInContext(source.slice(source.indexOf('function markMonitorStale('),source.indexOf('function renderProjectOverview(')),context);
context.markMonitorStale();
assert.equal(chip.textContent,'Status unverified');
assert.equal(chip.className,'monitor-status-chip stale');
assert.equal(step.textContent,'Last reported · Tester · Reviewing');
assert.equal(nextHeading.textContent,'Refresh task status');
assert.doesNotMatch(nextDescription.textContent,/worker is active/i);
context.markMonitorStale();
assert.equal(step.textContent,'Last reported · Tester · Reviewing','repeat failures must not stack stale labels');
vm.runInContext(source.slice(source.indexOf('function orchestrationPanel('),source.indexOf('function monitorPanel(')),context);
const batch={id:'batch-1',status:'BUILDING',workers:[{milestone_id:'M1',status:'RUNNING',workspace:'/repo/builder-1',run_dir:'/repo/run/worker-1'},{milestone_id:'M2',status:'BUILT',workspace:'/repo/builder-2',run_dir:'/repo/run/worker-2'}]};
const batchText=text(context.orchestrationPanel(batch));
for(const expected of ['Current Builder batch · batch-1','BUILDING','M1','RUNNING','M2','BUILT','/repo/builder-1','/repo/run/worker-2'])assert.ok(batchText.includes(expected),expected);
assert.match(batchText,/not live process checks/);
assert.match(batchText,/still requires combined validation and acceptance/);
assert.match(text(context.orchestrationPanel({...batch,status:'INTEGRATED'},true)),/Saved Builder batch.*INTEGRATED/);
assert.match(text(context.orchestrationPanel({id:'preparing',status:'PREPARING'})),/No workers recorded yet/);
console.log('Unified Now monitor, artifact counters, stale retention, and disabled action checks passed.');
