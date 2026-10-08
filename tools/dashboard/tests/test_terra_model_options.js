// Exercise the shipped picker and admission boundary without provider calls.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('./dashboard_vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../dashboard_app.js'),'utf8');
const profile=JSON.parse(require('node:child_process').execFileSync(process.env.AUTOCODE_TEST_PYTHON||'python3',['-B','-c','import json; from dashboard.dashboard_setup import conversation_model_fields; print(json.dumps(conversation_model_fields()))'],{cwd:path.resolve(__dirname,'../..'),encoding:'utf8'}));
class Element {
 constructor(tag,text=''){this.tag=tag;this.textContent=text;this.children=[];this.dataset={};this.value='';this.hidden=false;this.disabled=false;this.attributes=new Map();this.classList={toggle(){}};}
 append(...nodes){this.children.push(...nodes);}
 replaceChildren(...nodes){this.children=[...nodes];}
 querySelectorAll(){return this.children.flatMap(child=>child.tag==='option'?[child]:child.querySelectorAll());}
 setAttribute(name,value){this.attributes.set(name,String(value));}
 getAttribute(name){return this.attributes.get(name)||null;}
 removeAttribute(name){this.attributes.delete(name);}
}
const nodes=new Map(),roles={};
for(const role of ['glm','plan_reviewer','astra','terra','sol','completion']){
 roles[role]=new Element('select');roles[role].dataset.userSelected='true';nodes.set('#'+role+'-model',roles[role]);
 if(role!=='glm')nodes.set('#'+role+'-reasoning-effort',new Element('select'));
}
for(const id of ['model-catalogue-status','create-model-catalogue-gate','create-model-catalogue-status','retry-models','create-submit','create','new-goal','create-error'])nodes.set('#'+id,new Element('div'));
nodes.set('.models-disclosure summary span',new Element('span'));
nodes.set('#create-conversation-label',Object.assign(new Element('span'),{nextElementSibling:new Element('small')}));
const context=vm.createContext({$:id=>nodes.get(id),n:(tag,text)=>new Element(tag,text),human:value=>value});
vm.runInContext('let modelCatalogue={models:[],usable:false},conversationCatalogue={models:[],usable:false},conversationProfile=null,conversationTransport={transport:"unknown",version:null};',context);
vm.runInContext(source.slice(source.indexOf('function supportedReasoningLevels('),source.indexOf('async function loadModels(')),context);
const full=[...new Set(profile.conversation_required_roles.map(role=>profile.conversation_routes[role].model))];
const sync=(extra={})=>context.syncModelOptions({usable:true,models:full,...profile,...extra});
sync();assert.equal(nodes.get('#create-submit').disabled,false);
for(const [role,select] of Object.entries(roles))assert.equal(select.children[0].textContent,'Default · '+profile.conversation_defaults[role]);
assert.equal(nodes.get('#terra-reasoning-effort').value,profile.conversation_efforts.terra);
nodes.get('#terra-reasoning-effort').dataset.userSelected='true';nodes.get('#terra-reasoning-effort').value='max';sync();assert.equal(nodes.get('#terra-reasoning-effort').value,'max');
sync({models:[profile.conversation_routes.planner.model]});assert.equal(nodes.get('#create-submit').disabled,true,'partial catalogue cannot authorize required defaults');
sync();roles.terra.value='vendor/removed';context.syncConversationReadiness();assert.equal(nodes.get('#create-submit').disabled,true);
sync();assert.equal(roles.terra.value,'vendor/removed','unavailable selection remains visible');
roles.terra.value='';roles.astra.value='gpt-5.6-sol';context.syncConversationReadiness();assert.equal(nodes.get('#create-submit').disabled,true,'eligible alias must be listed');
sync({models:[...full,'openai/gpt-5.6-sol']});assert.equal(nodes.get('#create-submit').disabled,false);roles.astra.value='';
roles.terra.value='gpt-5.6-sol';sync({models:[...full,'openai/gpt-5.6-sol']});assert.equal(nodes.get('#create-submit').disabled,true,'Builder does not accept bare aliases');roles.terra.value='';
sync({conversation_visual:true});assert.equal(nodes.get('#create-submit').disabled,true,'visual route is conditional');sync();assert.equal(nodes.get('#create-submit').disabled,false);
sync({provider:'custom-terminal',usable:false,error:'SECRET_fixture_token',models:[],conversation_catalogue:{usable:true,models:full}});assert.equal(nodes.get('#create-submit').disabled,false,'terminal failure does not block builtin conversation');
assert.ok(!nodes.get('#model-catalogue-status').textContent.includes('SECRET_fixture_token'));
context.syncModelOptions({error:'SECRET_fixture_token'});assert.ok(!nodes.get('#model-catalogue-status').textContent.includes('SECRET_fixture_token'));assert.ok(!nodes.get('#create-model-catalogue-status').textContent.includes('SECRET_fixture_token'));
roles.terra.value='https://user:SECRET_fixture_token@example.invalid/model';sync();assert.equal(nodes.get('#create-submit').disabled,true);assert.ok(!JSON.stringify(roles.terra).includes('SECRET_fixture_token'));roles.terra.value='';
roles.terra._savedModel='https://user:SECRET_fixture_token@example.invalid/model';sync();assert.equal(nodes.get('#create-submit').disabled,true);assert.ok(!JSON.stringify(roles.terra).includes('SECRET_fixture_token'));roles.terra.value='';
sync({conversation_readiness:{transport:'unsupported',version:'2.0.20'}});assert.equal(nodes.get('#create-submit').disabled,true);assert.match(nodes.get('#create-model-catalogue-status').textContent,/OpenCode 1.x/);
context.syncConversationTransport({transport:'missing',version:null});assert.equal(nodes.get('#create-submit').disabled,true);
context.syncConversationTransport({transport:'available',version:'1.18.33'});assert.equal(nodes.get('#create-submit').disabled,false);assert.equal(context.conversationModelReadiness().authentication,'unknown');
vm.runInContext('let creatingConversation=false,conversationRequest=null,newTaskProject="";',context);
const requests=[];context.savedRequest=()=>({id:'controlled-submit'});context.persist=()=>{};context.conversationPayload=(text,models,request_id)=>({text,models,request_id});context.api=async(_url,options)=>{requests.push(JSON.parse(options.body));throw Error('Controlled failure');};
vm.runInContext(source.slice(source.indexOf("$('#create').onsubmit=async event=>"),source.indexOf("\n$('#new-goal').value=stored(")),context);
nodes.get('#new-goal').value='Verify the effective routes';
(async()=>{
 sync({models:[profile.conversation_routes.planner.model]});await nodes.get('#create').onsubmit({preventDefault(){}});assert.equal(requests.length,0);
 sync({provider:'custom-terminal',usable:false,error:'SECRET_fixture_token',models:[],conversation_catalogue:{usable:true,models:full}});await nodes.get('#create').onsubmit({preventDefault(){}});assert.equal(requests.length,1);assert.equal(nodes.get('#create-submit').disabled,false,'finally uses builtin readiness');
 context.syncConversationTransport({transport:'unsupported',version:'2.0.20'});await nodes.get('#create').onsubmit({preventDefault(){}});assert.equal(requests.length,1);
 console.log('Required defaults, explicit/alias/visual routes, separate terminal provider, safe diagnostics, transport and presubmit/finally guards passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
