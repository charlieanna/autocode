const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('./dashboard_vm');

const source=fs.readFileSync(path.join(__dirname,'../dashboard_app.js'),'utf8');
class Element {
  constructor(tag='div',text=''){this.tag=tag;this.textContent=text;this.children=[];this.dataset={};this.hidden=false;this.disabled=false;this.value='';this.className='';this.classList={toggle:()=>{}};}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=[...children];}
  setAttribute(name,value){this[name]=value;}
  removeAttribute(name){delete this[name];}
  get childElementCount(){return this.children.length;}
}
const text=node=>[node.textContent,...node.children.map(text)].join(' ');
const walk=(node,predicate)=>predicate(node)?node:node.children.map(child=>walk(child,predicate)).find(Boolean);
const nodes=new Map(),storage=new Map(),requests=[];
const $=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
let resolveRequest;
const context=vm.createContext({
  console,Date,Map,Set,JSON,
  $,
  n:(tag,value)=>new Element(tag,value),card:(_value,cls)=>Object.assign(new Element(),{className:cls}),
  button:(label,onclick,cls='')=>Object.assign(new Element('button',label),{onclick,className:cls}),
  human:value=>value[0].toUpperCase()+value.slice(1),
  stored:key=>storage.get(key)||'',persist:(key,value)=>storage.set(key,value),
  statusInfo:run=>({group:run.status==='TASK_COMPLETE'?'complete':'stopped'}),taskActionBusy:()=>false,
  dashboardNotice:()=>{},refresh:()=>Promise.resolve(),
  taskSentence:()=> 'Continue the saved task',primaryAction:()=>({kind:'continue',label:'Continue task'}),
  taskArchiveBlocked:()=>false,projectBlocked:()=>false,requestKey:(run,kind)=>run+':'+kind,
  mutationRequestId:()=> 'replace-request-1',
  api:(_url,options)=>{requests.push(JSON.parse(options.body));return new Promise(resolve=>resolveRequest=resolve);},
  document:{},
});
vm.runInContext(source.slice(source.indexOf('function roleDisplayName('),source.indexOf('function planReady(')),context);
vm.runInContext("let taskReadError='',modelCatalogue={models:['openai/new-model'],usable:true,loading:false,error:null},chosen={run:'/workspace/run'},latestRun=null,currentTab='now';const modelReplacementState=new Map(),taskChatPending=new Set(),sendingRequests=new Set();",context);
vm.runInContext(source.slice(source.indexOf('function modelCatalogueSnapshot()'),source.indexOf('function renderTaskReasoning(')),context);
vm.runInContext(source.slice(source.indexOf('function renderPrimaryAction('),source.indexOf('async function submitTaskAction(')),context);
vm.runInContext(source.slice(source.indexOf('function syncModelOptions('),source.indexOf('async function loadModels(')),context);

const run={workspace:'/workspace',run:'/workspace/run',status:'PAUSED',active_stage:{},interventions:{mode:'legacy'},actions:[],model_settings:{engine:'opencode',roles:{astra:'openai/retired-model'},role_engines:{astra:'opencode'},role_efforts:{astra:'high'}}};
context.renderTaskModelSettings(run);
let host=$('#task-model-settings');
assert.match(text(host),/Saved model unavailable: openai\/retired-model/);
assert.match(text(host),/It remains the saved value until a replacement is explicitly confirmed/);
let select=walk(host,node=>node.id==='task-astra-replacement');
assert(select);assert.equal(select.disabled,false);assert.equal(run.model_settings.roles.astra,'openai/retired-model');
select.value='openai/new-model';select.onchange({target:select});
assert.equal(run.model_settings.roles.astra,'openai/retired-model','selection must not silently replace the saved model');
assert.equal(context.readModelReplacement(run,'astra').state,'selected-unconfirmed');
host=$('#task-model-settings');let confirm=walk(host,node=>node.tag==='button'&&node.textContent==='Confirm model replacement');
(async()=>{
  assert(confirm);const pending=confirm.onclick();
  assert.equal(requests.length,1);assert.equal(requests[0].action,'set_model');assert.equal(requests[0].model,'openai/new-model');
  await context.confirmModelReplacement(run,'astra');assert.equal(requests.length,1,'confirmation must not duplicate while pending');
  resolveRequest({id:'receipt-model-1'});await pending;
  assert.equal(context.readModelReplacement(run,'astra').state,'confirming');
  const confirmed={...run,actions:[{id:'receipt-model-1',status:'finished',finished_at:1}],model_settings:{...run.model_settings,roles:{astra:'openai/new-model'}}};
  context.renderTaskModelSettings(confirmed);
  assert.equal(context.readModelReplacement(confirmed,'astra').state,'confirmed');
  assert.match(text($('#task-model-settings')),/Confirmed replacement: openai\/new-model/);
  assert.match(text($('#task-model-settings')),/receipt-model-1/);

  vm.runInContext("modelCatalogue={models:[],usable:true,loading:false,error:null};",context);
  const unsupported={...run,run:'/workspace/unsupported',model_settings:{...run.model_settings,roles:{astra:'openai/retired-model'}}};
  context.renderTaskModelSettings(unsupported);
  const unsupportedSelect=walk($('#task-model-settings'),node=>node.id==='task-astra-replacement');
  assert(unsupportedSelect);assert.equal(unsupportedSelect.disabled,true);
  assert.equal(unsupportedSelect['aria-describedby'],'task-astra-replacement-reason','the disabled selector names its adjacent explanation');
  assert.match(text($('#task-model-settings')),/No compatible replacement is available/);

  // A catalogue response changes both model controls and task admission in
  // the same render. No periodic status refresh should be needed to block it.
  context.catalogueRun=unsupported;
  vm.runInContext('latestRun=catalogueRun;',context);
  context.syncModelOptions({usable:true,models:['openai/retired-model']});
  context.renderPrimaryAction(unsupported);
  assert.equal($('#continue-run').disabled,false);
  context.syncModelOptions({usable:true,models:['openai/new-model']});
  assert.equal($('#continue-run').disabled,true,'catalogue removal immediately blocks Continue');
  assert.equal($('#continue').disabled,true,'the alternate Continue action is blocked too');
  assert.equal($('#continue-run')['aria-describedby'],'task-model-gate');
  assert.match($('#task-model-gate').textContent,/saved Planner model openai\/retired-model is unavailable/);
  assert.equal(unsupported.model_settings.roles.astra,'openai/retired-model','refresh preserves the saved model');
  context.syncModelOptions({usable:true,models:[]});
  assert.equal($('#continue-run').disabled,true,'an empty catalogue retains the unavailable-model gate');
  context.syncModelOptions({usable:true,models:['openai/retired-model']});
  assert.equal($('#continue-run').disabled,false,'a restored route immediately clears the gate');
  assert.equal($('#continue').disabled,false);
  assert.equal($('#task-model-gate').hidden,true);
  assert.equal($('#continue-run')['aria-describedby'],undefined);
  vm.runInContext('latestRun=null;',context);

  context.saveModelReplacement(run,'astra',{state:'unconfirmed',saved:'openai/retired-model',proposed:'openai/new-model',request_id:'replace-uncertain-1'});
  vm.runInContext("modelCatalogue={models:['openai/new-model'],usable:true,loading:false,error:null};",context);
  context.renderTaskModelSettings(run);
  const reconcile=walk($('#task-model-settings'),node=>node.tag==='button'&&node.textContent==='Retry status');
  assert(reconcile);assert.equal(reconcile.dataset.staleSafe,'true','reconciliation remains a safe status read during a stale view');
  assert.match(text($('#task-model-settings')),/Refresh status and reconcile this request before retrying/);
  console.log('Explicit model replacement preserves saved values, prevents duplicate confirmation, records a receipt, and exposes unsupported controls safely.');
})().catch(error=>{console.error(error);process.exitCode=1;});
