// Preview preferences are scoped to the project; renders preserve a live iframe.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../dashboard_app.js'),'utf8'),storage=new Map(),ids=new Map();
class Element{
 constructor(tag,text=''){this.tag=tag;this.textContent=text;this.children=[];this.dataset={};}
 append(...items){this.children.push(...items);}replaceChildren(...items){this.children=items;}
 setAttribute(key,value){this[key]=value;}querySelector(tag){return this.children.find(child=>child.tag===tag);}
}
const $=id=>{if(!ids.has(id))ids.set(id,new Element('div'));return ids.get(id);};
const context=vm.createContext({URL,URLSearchParams,$,n:(tag,text)=>new Element(tag,text),card:()=>new Element('div'),
 stored:key=>storage.get(key)||'',persist:(key,value)=>storage.set(key,value),location:{href:'http://127.0.0.1:8765/'},loadChanges:()=>{},taskTitle:run=>run.task||run.run,chosen:{run:'/a/one',workspace:'/a'},latestRun:null});
vm.runInContext(source.slice(source.indexOf('function stageSucceeded('),source.indexOf('function taskOverviewState('))+source.slice(source.indexOf('function localPreviewUrl('),source.indexOf('function requestKey(')),context);
const frame=()=>$('#preview-content').querySelector('iframe');
context.renderPreviewState();assert.equal(frame(),undefined);assert.match($('#preview-empty-note').textContent,/Start your app/);
$('#preview-url').value='http://127.0.0.1:3210/demo';$('#preview-form').onsubmit({preventDefault(){}});
assert.equal(storage.get('preview-project:/a'),'http://127.0.0.1:3210/demo');
const initial=frame();context.renderPreviewState();assert.equal(frame(),initial,'Polling does not reload an unchanged app');
context.chosen={run:'/a/two',workspace:'/worktree/two',project_workspace:'/a'};context.renderPreviewState();
assert.equal(frame(),initial,'Conversations in one project share the address and live frame');
context.latestRun={...context.chosen,active_stage:{stage:'terra'}};context.renderPreviewState();assert.equal(frame(),initial,'An in-progress Builder does not refresh the app');
for(const failure of [{exit_code:1},{timed_out:true},{interrupted:true},{rejected:true}]){
 context.latestRun.stages=[{stage:'terra',finished_at:'failed',changed_files:['app.js'],source_revision:'failed',...failure}];
 context.renderPreviewState();assert.equal(frame(),initial,'A failed step is not a newly landed candidate');
}
context.latestRun.stages=[{stage:'terra',exit_code:0,finished_at:'saved',changed_files:['app.js'],source_revision:'v2'}];context.renderPreviewState();
assert.notEqual(frame(),initial,'A saved code-changing step refreshes the app');const changed=frame();context.renderPreviewState();assert.equal(frame(),changed);
context.stopPreview();assert.equal(frame(),undefined);context.renderPreviewState();assert.equal(frame().src,'http://127.0.0.1:3210/demo','Returning to Preview remounts its saved address');
context.chosen={run:'/b/one',workspace:'/b'};context.latestRun=null;context.renderPreviewState();assert.equal(frame(),undefined,'Other projects never inherit the address');
context.chosen={run:'/legacy/one',workspace:'/legacy'};storage.set('preview:/legacy/one','http://localhost:4321/');context.renderPreviewState();assert.equal(frame().src,'http://localhost:4321/');assert.equal(storage.get('preview:/legacy/one'),'http://localhost:4321/','Legacy preference is preserved');
storage.set('preview-project:/legacy','https://unrelated.example/');context.renderPreviewState();assert.equal(frame(),undefined);assert.match($('#preview-error').textContent,/local app address/);
console.log('Per-project preview persistence, legacy preference, source-change refresh, isolation and URL boundaries passed.');

context.chosen={run:'/blocked/one',workspace:'/blocked'};context.latestRun=null;context.persist=()=>{};
$('#preview-url').value='http://localhost:4567/';$('#preview-form').onsubmit({preventDefault(){}});
assert.equal(frame()?.src,'http://localhost:4567/','Storage denial must not prevent opening an explicitly supplied URL');
assert.match($('#preview-empty-note').textContent,/browser session/);
context.renderPreviewState();assert.equal(frame()?.src,'http://localhost:4567/');
