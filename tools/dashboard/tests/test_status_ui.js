// Read-only rendering and stale-state behavior; no real server or provider.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
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
assert.match(source.slice(source.indexOf('function renderTaskNow('),source.indexOf('function renderPrimaryAction(')),/host.append\(monitorPanel\(run\)\)/);
assert.match(source,/if\(!selected\).*Task list checked just now/);
const chip=new Element('p','Running · worker verified'),step=new Element('h2','Validator · Reviewing');
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
assert.equal(step.textContent,'Last reported · Validator · Reviewing');
assert.equal(nextHeading.textContent,'Refresh task status');
assert.doesNotMatch(nextDescription.textContent,/worker is active/i);
context.markMonitorStale();
assert.equal(step.textContent,'Last reported · Validator · Reviewing','repeat failures must not stack stale labels');
console.log('Unified Now monitor, artifact counters, stale retention, and disabled action checks passed.');
