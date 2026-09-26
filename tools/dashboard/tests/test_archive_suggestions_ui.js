const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const source=fs.readFileSync(path.join(__dirname,'..','dashboard_app.js'),'utf8');
const script=source.slice(source.indexOf('const ARCHIVE_SUGGESTION_AGE_MS='),source.indexOf('function statusInfo('));
const host={hidden:true,children:[],replaceChildren(){this.children=[];},append(...children){this.children.push(...children);}};
const saved=new Map();
let reviewed='';
const element=(tag,value='')=>({tag,value,children:[],append(...children){this.children.push(...children);}});
const now=Date.parse('2026-09-24T20:00:00Z');
class FixedDate extends Date { static now(){return now;} }
const context=vm.createContext({
  Date:FixedDate,JSON,
  $:()=>host,
  n:element,
  card:(_,className)=>({...element('div'),className}),
  button:(label,onclick)=>({label,onclick}),
  stored:(key,fallback)=>saved.get(key)??fallback,
  persist:(key,value)=>saved.set(key,value),
  taskTitle:run=>run.task,
  basename:value=>value.split('/').at(-1),
  reviewTaskArchive:run=>{reviewed=run.run;}
});
vm.runInContext(script,context);
const old='2026-09-20T20:00:00Z';
const recent='2026-09-24T19:00:00Z';
const run=(id,status,live,checkpoint=old)=>({run:'/repo/.autocode/runs/'+id,workspace:'/repo',task:id,status,
  monitor:{live:{state:live},checkpoint_updated:checkpoint},active_stage:{}});
const complete=run('complete','TASK_COMPLETE','none');
const exited=run('exited','RUNNING','exited');
const unrecorded=run('unrecorded','RUNNING','none');
const paused=run('paused','PAUSED_PROVIDER_UNCERTAIN','exited');
const alive=run('alive','RUNNING','alive');
const fresh=run('fresh','TASK_COMPLETE','none',recent);
const pending={...exited,run:'/repo/.autocode/runs/pending',questions:[{question:'Answer?'}]};
const completedWithOldQuestion={...complete,run:'/repo/.autocode/runs/old-question',questions:[{question:'Already answered'}]};
for(const item of [paused,alive,fresh,pending])assert.equal(context.archiveSuggestionKind(item,now),'');
assert.equal(context.archiveSuggestionKind(complete,now),'Completed');
assert.equal(context.archiveSuggestionKind(completedWithOldQuestion,now),'Completed');
assert.equal(context.archiveSuggestionKind(exited,now),'Recorded worker exited');
assert.match(context.archiveSuggestionKind(unrecorded,now),/No worker recorded/);
// The suggestion is read-only until its existing review dialog is opened.
context.renderArchiveSuggestions([complete,exited,unrecorded,paused,alive,fresh,pending]);
assert.equal(host.hidden,false);
assert.equal(host.children.length,5);
assert.equal(saved.size,0);
host.children[2].children[1].onclick();
assert.equal(reviewed,complete.run);
assert.equal(saved.size,0);
host.children[2].children[2].onclick();
assert.equal(host.children.length,4);
assert.equal(JSON.parse(saved.get('archive-suggestions-dismissed'))[complete.run],true);
console.log('Old-task archive suggestion eligibility and dismissal passed.');
