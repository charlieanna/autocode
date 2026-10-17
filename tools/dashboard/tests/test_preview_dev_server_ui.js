// Preview shows the run's own development server (view.dev_server, #730) and keeps a pasted address as the fallback.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('./dashboard_vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../dashboard_app.js'),'utf8'),storage=new Map(),ids=new Map();
class Element{
 constructor(tag,text=''){this.tag=tag;this.textContent=text;this.children=[];this.dataset={};}
 append(...items){this.children.push(...items);}replaceChildren(...items){this.children=items;}
 setAttribute(key,value){this[key]=value;}querySelector(tag){return this.children.find(child=>child.tag===tag);}
}
const $=id=>{if(!ids.has(id))ids.set(id,new Element('div'));return ids.get(id);};
const context=vm.createContext({URL,URLSearchParams,$,n:(tag,text)=>new Element(tag,text),card:()=>new Element('div'),
 stored:key=>storage.get(key)||'',persist:(key,value)=>storage.set(key,value),location:{href:'http://127.0.0.1:8765/'},loadChanges:()=>{},taskTitle:run=>run.task||run.run,chosen:null,latestRun:null});
vm.runInContext(source.slice(source.indexOf('function stageSucceeded('),source.indexOf('function taskOverviewState('))+source.slice(source.indexOf('function localPreviewUrl('),source.indexOf('function requestKey(')),context);
const frame=()=>$('#preview-content').querySelector('iframe');
const text=element=>[element.textContent||'',...(element.children||[]).map(text)].join(' ');
const shown=()=>text($('#preview-source'));
const server=(fields={})=>({configured:true,status:'running',url:'http://127.0.0.1:41001/',command:'npm run dev -- --port {port}',waiting:false,
 port:41001,ready_at:'2026-10-16T10:00:00Z',restarts:0,code_checkpoint:'cp-2',reason:null,exit_code:null,...fields});
const open=(dev_server,{run='/a/.autocode/runs/one',workspace='/a',rows=[{id:'cp-1'},{id:'cp-2'}]}={})=>{
 context.chosen={run,workspace};
 context.latestRun={run,workspace,status_view:{dev_server,code_checkpoints:{rows}}};
 context.renderPreviewState();
};

open(server());
assert.equal(frame()?.src,'http://127.0.0.1:41001/','A running server of the run is previewed without a pasted address');
assert.match(shown(),/this run’s development server/i);assert.match(shown(),/127\.0\.0\.1:41001/);
assert.match(shown(),/latest saved step/);
assert.equal($('#preview-setup').open,false,'The setup help stays closed while the run serves its app');
assert.equal($('#preview-reload').disabled,false);assert.equal($('#preview-open').hidden,false);assert.equal($('#preview-open').href,'http://127.0.0.1:41001/');
const live=frame();context.renderPreviewState();assert.equal(frame(),live,'Polling an unchanged server keeps its frame');

storage.set('preview-project:/a','http://127.0.0.1:3000/');context.renderPreviewState();
assert.equal(frame(),live,'The run’s server wins over a pasted address while it runs');
assert.match(shown(),/saved address.*127\.0\.0\.1:3000.*when it is not running/s);

open(server({ready_at:'2026-10-16T10:05:00Z',restarts:1,code_checkpoint:'cp-3'}),{rows:[{id:'cp-1'},{id:'cp-2'},{id:'cp-3'}]});
assert.notEqual(frame(),live,'A restart on new code reloads the preview');assert.equal(frame().src,'http://127.0.0.1:41001/');
const restarted=frame();context.renderPreviewState();assert.equal(frame(),restarted);

open(server({code_checkpoint:'cp-2'}),{rows:[{id:'cp-1'},{id:'cp-2'},{id:'cp-3'}]});
assert.match(shown(),/before the run’s latest saved step/,'A server on older code says so');

$('#preview-reload').onclick();assert.ok(frame(),'Reload remounts the run’s server');
const beforeReload=frame();$('#preview-reload').onclick();assert.notEqual(frame(),beforeReload);

open(server({status:'starting',url:null,ready_at:null}));
assert.equal(frame(),undefined,'A starting server is waited for, not replaced by the pasted address');
assert.match(shown(),/starting/);

open(server({status:'exited',url:null,reason:'the server exited',exit_code:3,waiting:true}));
assert.equal(frame()?.src,'http://127.0.0.1:3000/','The pasted address is the fallback');
assert.match(shown(),/the server exited/);assert.match(shown(),/exit code 3/);
assert.match(shown(),/starts again after the run’s next code change/);assert.match(shown(),/saved address/);

storage.delete('preview-project:/a');
open(server({status:'stopped',url:null,reason:'stopped by autocode serve stop'}));
assert.equal(frame(),undefined);assert.match(shown(),/stopped by autocode serve stop/);
assert.match(shown(),/autocode serve start --workspace \/a --run-dir \/a\/\.autocode\/runs\/one/);
open(server({status:'stopped',url:null,reason:'stopped by autocode serve stop',exit_code:3}));
assert.doesNotMatch(shown(),/exit code/,'A stop after a crash does not show the crash’s exit code as the stop’s');

open(server({status:'lost',url:null}),{run:'/my app/.autocode/runs/one',workspace:'/my app'});
assert.match(shown(),/no longer answers/);
assert.match(shown(),/autocode serve start --workspace '\/my app' --run-dir '\/my app\/\.autocode\/runs\/one'/,'Paths are shell-quoted');

open(server({status:'off',url:null,ready_at:null,code_checkpoint:null}));
assert.match(shown(),/not running/);assert.match(shown(),/autocode serve start/);

open({...server({status:'off',url:null}),configured:false});
assert.equal(shown().trim(),'','A run without a serve command shows only the pasted-address preview');
assert.match($('#preview-empty-note').textContent,/No app address/);

for(const url of ['http://example.com:41001/','http://127.0.0.1:8765/']){
 open(server({url}));
 assert.equal(frame(),undefined,'Only a loopback address on another port is embedded: '+url);
 assert.match(shown(),/cannot be embedded.*local app address/s);
 assert.equal($('#preview-error').textContent||'','','A refused server address does not overwrite the address form’s own error');
}
context.chosen={run:'/b/.autocode/runs/x',workspace:'/b'};context.latestRun={...context.chosen,status_view:null};context.renderPreviewState();
assert.equal(frame(),undefined);assert.equal(shown().trim(),'','Without a status view only the pasted address is used');
console.log('Preview from the run’s development server: running, restarted, older code, starting, ended, lost, unconfigured and refused addresses passed.');
