// Run with: node tests/test_planning_ui.js
// Exercise the shipped pure routing helpers without simulating browser state.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../dashboard_app.js'),'utf8');
const helpers=source.slice(source.indexOf('function jointPlanning('),source.indexOf('function setView('));
const context=vm.createContext({human:text=>String(text)});
vm.runInContext(helpers,context);
const joint={model_settings:{joint_planning:true}};
for(const stage of ['astra_discovery','astra_discovery_report_repair']) {
 assert.equal(context.stageName({...joint,stage}),'Planner · Planning');
 assert.equal(context.stageName({stage}),'Planner · Requirements & planning');
}
assert.equal(context.stageName({...joint,stage:'glm_revise_report_repair'}),'Planner · Revising the plan');
assert.equal(context.stageName({...joint,stage:'astra_challenge'}),'Plan reviewer · Challenging the plan');
assert.equal(context.stageName({...joint,stage:'astra_finalize'}),'Plan reviewer · Finalizing the plan');
assert.equal(context.stageName({...joint,stage:'astra_review'}),'Completion owner · Deciding complete or rework');
assert.equal(context.planReady({...joint,status:'WAITING_FOR_USER',goal:{origin:'glm_draft'}}),false);
assert.equal(context.planReady({...joint,status:'AWAITING_GOAL_APPROVAL',goal:{origin:'glm_revise'}}),false);
assert.equal(context.planReady({...joint,status:'AWAITING_GOAL_APPROVAL',goal:{origin:'astra_finalize'}}),true);
const gocodeJoint={model_settings:{joint_planning:true,engine:'gocode'}};
assert.equal(context.planningSpeaker({...joint,goal:{origin:'glm_draft'}}),'Planner');
assert.equal(context.planningSpeaker({...joint,goal:{origin:'astra_finalize'}}),'Plan reviewer');
assert.equal(context.planningSpeaker({}),'Planner');
assert.equal(context.planningSpeaker({...gocodeJoint,goal:{origin:'glm_draft'}}),'GLM');
assert.equal(context.planningSpeaker({...gocodeJoint,goal:{origin:'astra_finalize'}}),'Astra');
assert.equal(context.planningSpeaker(gocodeJoint),'Astra');
const approvedGocode={...gocodeJoint,goal:{approval_status:'approved'},monitor:{roles:{
  glm:{model:'gocode-anthropic/claude-opus-5'},
  terra:{model:'gocode-openai/gpt-5.6-terra'},
  sol:{model:'gocode-anthropic/claude-opus-5'}
}}};
assert.deepEqual(JSON.parse(JSON.stringify(context.workflowRoleConfig(approvedGocode,{role:'sol',active:true,verified:true}))),[
 ['glm','GLM','Draft & revise'],
 ['astra','Astra','Plan & direct'],
 ['terra','Terra','Implement'],
 ['sol','Sol','Review']
]);
const approvedJoint={...joint,goal:{approval_status:'approved'}};
assert.deepEqual(JSON.parse(JSON.stringify(context.workflowConfig(approvedJoint,{role:'sol'}))).map(item=>item[1]),
 ['Requirements Gatherer','Planner','Plan reviewer','Orchestrator','Builder','Validator','Completion owner','Resolver']);
assert.deepEqual(JSON.parse(JSON.stringify(context.workflowConfig({model_settings:{joint_planning:false}},{role:'sol'}))).map(item=>item[1]),
 ['Requirements Gatherer','Planner','Plan reviewer','Orchestrator','Builder','Validator','Completion owner','Resolver']);
const done=(stage,role,extra={})=>({stage,role,finished_at:'2026-09-22T10:00:00Z',exit_code:0,...extra});
const idlePlanning={...joint,stages:[done('astra_discovery','glm'),done('astra_finalize','astra')]};
assert.equal(context.completedPlanningStep(idlePlanning,'glm').stage,'astra_discovery');
assert.equal(context.completedPlanningStep(idlePlanning,'astra').stage,'astra_finalize');
const migratedPlanning={...joint,goal:{approval_status:'approved'},stages:[done('astra_discovery','astra')]};
assert.equal(context.completedPlanningStep(migratedPlanning,'glm'),undefined,'approval does not invent a Planner step');
assert.equal(context.completedPlanningStep(migratedPlanning,'astra').stage,'astra_discovery');
assert.equal(context.completedPlanningStep({...joint,stages:[done('glm_revise','glm',{rejected:true})]},'glm'),undefined);
console.log('Planning UI routing and approval checks passed.');

// Payload boundaries: chatting needs no project; answering never invents an
// approval or switches the selected question. These are exercised functions.
const payloadSource=source.slice(source.indexOf('function conversationStatus('),source.indexOf('function icon('));
const payloadContext=vm.createContext({});
vm.runInContext(payloadSource,payloadContext);
const plain=value=>JSON.parse(JSON.stringify(value));
assert.deepEqual(plain(payloadContext.conversationPayload('  Build a tracker  ',{glm_model:'zai-coding-plan/glm-5.3'},'request-1')),
 {text:'Build a tracker',models:{glm_model:'zai-coding-plan/glm-5.3'},request_id:'request-1'});
assert.deepEqual(plain(payloadContext.chatPayload({workspace:'/repo',run:'/repo/run'},'Private','Q2','request-2')),
 {workspace:'/repo',run:'/repo/run',text:'Private',question_id:'Q2',request_id:'request-2'});
assert.deepEqual(plain(payloadContext.chatPayload({workspace:'/repo',run:'/repo/run'},'Revise the plan',null,'request-3')),
 {workspace:'/repo',run:'/repo/run',text:'Revise the plan',request_id:'request-3'});
assert.equal(payloadContext.conversationStatus({status:'thinking'}),'Thinking…');
assert.equal(payloadContext.conversationStatus({status:'ready',attachment:{status:'starting'}}),'Connecting project');
assert.equal(payloadContext.conversationStatus({status:'error'}),'Needs attention');
assert.deepEqual(plain(payloadContext.orderedMessages([
 {text:'Plan Reviewer final plan',created_at:'2026-09-20T12:03:00Z'},
 {text:'Planner question',created_at:'2026-09-20T12:00:00Z'},
 {text:'My answer',created_at:Date.parse('2026-09-20T12:01:00Z')/1000}
])).map(message=>message.text),['Planner question','My answer','Plan Reviewer final plan']);

// Lost POST responses retain their idempotency key across page lifetimes.
const storage=new Map();let ids=0;
const persistence=vm.createContext({localStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)},crypto:{randomUUID:()=>String(++ids)}});
vm.runInContext(source.slice(source.indexOf('function stored('),source.indexOf('function rememberSelection(')),persistence);
const saved=persistence.savedRequest('create-request',{text:'An idea'});
assert.equal(persistence.savedRequest('create-request',{text:'An idea'}).id,saved.id);
assert.equal(persistence.readSavedRequest('create-request').id,saved.id);
assert.notEqual(persistence.savedRequest('create-request',{text:'A revised idea'}).id,saved.id);

// Assistant formatting remains text, even when a provider returns HTML.
class Element {
 constructor(tag,text=''){this.tagName=tag.toUpperCase();this.textContent=text;this.children=[];}
 append(...children){this.children.push(...children);}
}
const formatting=vm.createContext({card:(_,style)=>new Element('div'),n:(tag,text)=>new Element(tag,text),document:{createTextNode:text=>new Element('#text',text)}});
vm.runInContext(source.slice(source.indexOf('function inlineText('),source.indexOf('function appendMessage(')),formatting);
const formatted=formatting.messageBody('## The plan\n\n- **One step**\n- `<script>`\n\n<img src=x onerror=alert(1)>');
const flatten=node=>[node,...node.children.flatMap(flatten)];
assert.equal(flatten(formatted).some(node=>node.tagName==='IMG'||node.tagName==='SCRIPT'),false);
assert.equal(flatten(formatted).some(node=>node.textContent==='<img src=x onerror=alert(1)>'),true);
assert.equal(flatten(formatted).some(node=>node.tagName==='STRONG'&&node.textContent==='One step'),true);

for (const text of [
 'Plan revision ',
 'Origin: ',
 'Requirements',
 'Constraints',
 'Implementation sequence',
 'Verification criteria',
 'Assumptions',
 'Earlier-revision disclosure',
 'Approval records this revision. Starting work is a separate action.',
 'Approve plan revision ',
 'Request changes',
 'Start building',
]) assert.match(source, new RegExp(text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));

// Simulate a response slower than the two-second refresh cadence. Multiple
// callers share the in-flight fetch, and one follow-up applies fresh state.
async function pollingCheck() {
 const scheduled=[],deferred=[];let calls=0,active=0,maxActive=0;
 const pollContext=vm.createContext({
   clearTimeout:()=>{},setTimeout:(fn,ms)=>{scheduled.push(ms);return 1;},
   refreshOnce:()=>{calls++;active++;maxActive=Math.max(maxActive,active);return new Promise(resolve=>deferred.push(()=>{active--;resolve();}));}
 });
 vm.runInContext('let refreshPromise=null,refreshAgain=false,refreshTimer=null;\n'+source.slice(source.indexOf('async function refresh(){'),source.indexOf('function restoreSelection()')),pollContext);
 const first=pollContext.refresh();const second=pollContext.refresh();const third=pollContext.refresh();
 assert.equal(calls,1);assert.equal(scheduled.length,0);
 deferred.shift()();await new Promise(resolve=>setImmediate(resolve));
 assert.equal(calls,2);assert.equal(scheduled.length,0);assert.equal(maxActive,1);
 deferred.shift()();await Promise.all([first,second,third]);
 assert.equal(calls,2);assert.deepEqual(scheduled,[2000]);assert.equal(maxActive,1);
}
pollingCheck().then(()=>console.log('Project-free payloads, question targeting, and slow-response polling passed.')).catch(error=>{console.error(error);process.exitCode=1;});
