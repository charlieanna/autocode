// A number typed at a bound pause (#2044): the recovery card shows the one number the status view's
// needs.command_inputs leaves to a person and sends it, with the inspected pause, to the backend.
// Real renderer with a controlled DOM; no server or model calls.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('./dashboard_vm');
const source=fs.readFileSync(path.join(__dirname,'../dashboard_app.js'),'utf8');
class Element{
 constructor(tag,text=''){this.tag=tag;this.textContent=text;this.children=[];this.dataset={};this.open=false;this.hidden=false;this.listeners={};this.value='';}
 append(...rows){this.children.push(...rows);}
 setAttribute(key,value){this[key]=value;}
 addEventListener(kind,fn){this.listeners[kind]=fn;}
 querySelector(){return null;}
}
const calls=[],notices=[];
const context=vm.createContext({console,$:()=>new Element('div'),n:(tag,text)=>new Element(tag,text),card:()=>new Element('div'),
 button:(label,onclick)=>Object.assign(new Element('button',label),{onclick}),focusKey:element=>element,
 disclosure:(label,key,content)=>{const e=new Element('details',label);e.append(...content);return e;},
 statusInfo:()=>({group:'stopped',label:'Paused',reason:'A limit you set was reached.'}),
 renderDocument:value=>new Element('pre',JSON.stringify(value)),taskActionBusy:()=>false,unresolvedModelReplacement:()=>false,
 dashboardNotice:message=>notices.push(message),submitTaskAction:async(...args)=>{calls.push(args);return {id:'action-1'};},
 document:{createElement:tag=>new Element(tag),querySelectorAll:()=>[]}});
vm.runInContext('let taskReadError="",latestRun=null,latestData=null;',context);
vm.runInContext(source.slice(source.indexOf('function renderRecoveryCard('),source.indexOf('function renderTaskAttention(')),context);
const all=root=>[root,...root.children.flatMap(all)],text=root=>all(root).map(e=>e.textContent).join(' ');

const row={id:'max_cost_usd',kind:'integer',flag:'--max-cost-usd',placeholder:'N',arg_index:2,label:'Cost cap (whole USD)',
 current:2,used:2.37,minimum:3,maximum:Number.MAX_SAFE_INTEGER};
const recovery={version:1,token:'exact',status:'RESOLVER_PENDING',title:'Limit reached',what_happened:'The cost cap stopped the run.',
 retained:'Saved work remains.',failure_groups:[],actions:[{id:'inspect',kind:'inspect',label:'Inspect saved work',effect:'Read only.'}]};
const run={run:'/owned/fixture/run',workspace:'/owned/fixture',status:recovery.status,interventions:{mode:'capable',recovery},
 status_view:{needs:{kind:'resume',command_inputs:[{...row,command:'AUTOCODE_EXPECTED_RECOVERY_TOKEN=exact autocode --resume-paused --max-cost-usd N --expected-recovery-token -'}]}}};
context.fixture=run;vm.runInContext('latestRun=fixture',context);

const host=new Element('div');context.renderRecoveryCard(host,run,recovery);
const field=all(host).find(e=>e.dataset.pauseNumber==='max_cost_usd'),submit=all(host).find(e=>e.dataset.pauseNumberSubmit==='max_cost_usd');
const inspection=all(host).find(e=>e.tag==='details');
assert.ok(field&&submit,'the pause offers its number');
assert.equal(submit.textContent,'Raise limit and continue');
for(const words of ['Cost cap (whole USD)','Saved: 2','Used so far: 2.37','at least 3','no plan is approved'])assert.ok(text(host).includes(words),words);
const type=value=>{field.value=value;field.listeners.input();};
type('5');assert.equal(submit.disabled,true,'the saved pause is inspected first, like every recovery action');
inspection.open=true;inspection.listeners.toggle();
for(const value of ['','2','0','-1','3.5','5e2','9007199254740992']){type(value);assert.equal(submit.disabled,true,value);}
type('5');assert.equal(submit.disabled,false);
(async()=>{
 await submit.onclick();
 assert.equal(calls.at(-1)[1],'set_pause_number');
 assert.deepEqual(JSON.parse(JSON.stringify(calls.at(-1)[2])),{recovery_token:'exact',input_id:'max_cost_usd',value:'5'});
 // A click on an offer whose pause changed sends nothing.
 context.fresh={...run,interventions:{...run.interventions,recovery:{...recovery,token:'new'}}};vm.runInContext('latestRun=fresh',context);
 type('6');await submit.onclick();assert.equal(calls.length,1);assert.match(notices.at(-1),/pause changed/);
 // A draft survives a re-render of the same pause.
 vm.runInContext('latestRun=fixture',context);type('7');
 const again=new Element('div');context.renderRecoveryCard(again,run,recovery);
 assert.equal(all(again).find(e=>e.dataset.pauseNumber==='max_cost_usd').value,'7');
 // No row, two rows or a malformed row: no number entry.
 for(const needs of [{kind:'resume'},{kind:'resume',command_inputs:[row,row]},{kind:'resume',command_inputs:[{...row,kind:'text'}]}]){
  const target=new Element('div');context.renderRecoveryCard(target,{...run,status_view:{needs}},recovery);
  assert.equal(all(target).some(e=>e.dataset.pauseNumber),false,JSON.stringify(needs));
 }
 // A recovery grant keeps its own wording.
 const grant=new Element('div');
 context.renderRecoveryCard(grant,{...run,status_view:{needs:{kind:'resume',command_inputs:[{id:'grant_recovery',kind:'integer',label:'Additional recoveries',current:3,used:3,minimum:1,maximum:3}]}}},recovery);
 assert.equal(all(grant).find(e=>e.dataset.pauseNumberSubmit).textContent,'Grant and continue');
 assert.ok(text(grant).includes('Enter 1 to 3 additional recoveries'));
 console.log('A pause number is offered from the status view, gated on inspection and bound to the inspected pause.');
})().catch(error=>{console.error(error);process.exit(1);});
