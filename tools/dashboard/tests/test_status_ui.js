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
const action=$('#continue-run'),retry=$('#retry-task'),tab=new Element('button'),input=$('#change-text');
action.id='continue-run';retry.id='retry-task';tab.dataset.tab='plan';input.id='change-text';
const controls=[action,retry,tab,input];
const context=vm.createContext({console,Date,Number,$,n:(tag,text)=>new Element(tag,text),card:(_text,cls)=>Object.assign(new Element('div'),{className:cls}),human:x=>x,statusAge:()=> '3d ago',markMonitorStale:()=>{},document:{querySelectorAll:()=>controls}});
vm.runInContext('let taskReadError="",taskReadAt=Date.now(),latestRun={run:"/private/tmp/task"},chosen={run:"/tmp/task"};',context);
vm.runInContext(source.slice(source.indexOf('function disableStaleControls()'),source.indexOf('function showRemovedRun(')),context);
$('#now').append(new Element('p','Last known objective'));
context.unavailableRun('Selected run unavailable: offline');
assert.equal($('#now').children[0].textContent,'Last known objective');
assert.match($('#task-load-message').textContent,/last successful read.*Controls are disabled/);
assert.equal($('#sync-state').textContent,'Task status unavailable');
assert.equal(action.disabled,true);assert.equal(input.disabled,true);assert.equal(retry.disabled,false);assert.equal(tab.disabled,false);
assert.equal(input.dataset.staleDisabled,'false');
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
console.log('Unified Now monitor, artifact counters, stale retention, and disabled action checks passed.');
