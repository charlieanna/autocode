// Run with: node tests/test_planning_ui.js
// Exercise the shipped pure routing helpers without simulating browser state.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('./dashboard_vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../dashboard_app.js'),'utf8');
const projectedRun=require('./resolver_fixture');
const helpers=source.slice(source.indexOf('function jointPlanning('),source.indexOf('function setView('));
const context=vm.createContext({human:text=>String(text)});
vm.runInContext(helpers,context);
const joint={model_settings:{joint_planning:true}};
for(const stage of ['astra_discovery','astra_discovery_report_repair']) {
 assert.equal(context.stageName({...joint,stage}),'Planner · Planning');
 assert.equal(context.stageName({stage}),'Planner · Planning');
}
assert.equal(context.stageName({...joint,stage:'glm_revise_report_repair'}),'Planner · Revising the plan');
assert.equal(context.stageName({...joint,stage:'astra_challenge'}),'Plan Reviewer · Challenging the plan');
assert.equal(context.stageName({...joint,stage:'astra_finalize'}),'Plan Reviewer · Finalizing the plan');
assert.equal(context.stageName({...joint,stage:'astra_review'}),'Completion Reviewer · Deciding complete or rework');
assert.equal(context.planReady({...joint,status:'WAITING_FOR_USER',goal:{origin:'glm_draft'}}),false);
assert.equal(context.planReady({...joint,status:'AWAITING_GOAL_APPROVAL',goal:{origin:'glm_revise'}}),false);
assert.equal(context.planReady({...joint,status:'AWAITING_GOAL_APPROVAL',goal:{origin:'astra_finalize'}}),true);
// The Plan Reviewer can accept a draft during its challenge (adaptive review) instead of finalizing it.
assert.equal(context.planReady({...joint,status:'AWAITING_GOAL_APPROVAL',goal:{origin:'adaptive_review_approval'}}),true);
assert.equal(context.planReady({...joint,status:'AWAITING_GOAL_APPROVAL',goal:{origin:'plan_finalize'}}),true);
const gocodeJoint={model_settings:{joint_planning:true,engine:'gocode'}};
assert.equal(context.planningSpeaker({...joint,goal:{origin:'glm_draft'}}),'Planner');
assert.equal(context.planningSpeaker({...joint,goal:{origin:'astra_finalize'}}),'Plan Reviewer');
assert.equal(context.planningSpeaker({}),'Planner');
assert.equal(context.planningSpeaker({...gocodeJoint,goal:{origin:'glm_draft'}}),'Planner');
assert.equal(context.planningSpeaker({...gocodeJoint,goal:{origin:'astra_finalize'}}),'Plan Reviewer');
assert.equal(context.planningSpeaker(gocodeJoint),'Plan Reviewer');
const approvedGocode={...gocodeJoint,goal:{approval_status:'approved'},monitor:{roles:{
  glm:{model:'gocode-anthropic/claude-opus-5'},
  terra:{model:'gocode-openai/gpt-5.6-terra'},
  sol:{model:'gocode-anthropic/claude-opus-5'}
}}};
assert.deepEqual(JSON.parse(JSON.stringify(context.workflowRoleConfig(approvedGocode,{role:'sol',active:true,verified:true}))),[
 ['glm','Planner','Draft & revise'],
 ['astra','Planner','Plan & direct'],
 ['terra','Builder','Implement'],
 ['sol','Tester','Review']
]);
const approvedJoint={...joint,goal:{approval_status:'approved'}};
assert.deepEqual(JSON.parse(JSON.stringify(context.workflowConfig(approvedJoint,{role:'sol'}))).map(item=>item[1]),
 ['Requirements','Planner','Plan Reviewer','Orchestrator','Builder','Tester','Completion Reviewer','Resolver']);
assert.deepEqual(JSON.parse(JSON.stringify(context.workflowConfig({model_settings:{joint_planning:false}},{role:'sol'}))).map(item=>item[1]),
 ['Requirements','Planner','Plan Reviewer','Orchestrator','Builder','Tester','Completion Reviewer','Resolver']);
const done=(stage,role,extra={})=>({stage,role,finished_at:'2026-09-22T10:00:00Z',exit_code:0,...extra});
const idlePlanning={...joint,stages:[done('astra_discovery','glm'),done('astra_finalize','astra')]};
assert.equal(context.completedPlanningStep(idlePlanning,'glm').stage,'astra_discovery');
assert.equal(context.completedPlanningStep(idlePlanning,'astra').stage,'astra_finalize');
const migratedPlanning={...joint,goal:{approval_status:'approved'},stages:[done('astra_discovery','astra')]};
assert.equal(context.completedPlanningStep(migratedPlanning,'glm'),undefined,'approval does not invent a Planner step');
assert.equal(context.completedPlanningStep(migratedPlanning,'astra').stage,'astra_discovery');
assert.equal(context.completedPlanningStep({...joint,stages:[done('glm_revise','glm',{rejected:true})]},'glm'),undefined);
console.log('Planning UI routing and approval checks passed.');

// The Plan header uses saved task identity and a distinct saved outcome. A
// mock design's separate subtitle cannot be manufactured from identical text.
const planNodes=(tag,text='')=>({tagName:tag.toUpperCase(),textContent:text,children:[],append(...items){this.children.push(...items);}});
const planContext=vm.createContext({
 basename:value=>String(value||'').split('/').pop(),
 card:()=>planNodes('div'),n:planNodes,
 disclosure:(label,key,body)=>{const node=planNodes('details');node.append(planNodes('summary',label),...body);return node;},
 workflowModelsPanel:()=>planNodes('div','Recorded route details'),
});
vm.runInContext(source.slice(source.indexOf('function concise('),source.indexOf('function projectTitle(')),planContext);
vm.runInContext(source.slice(source.indexOf('function planHeaderText('),source.indexOf('function planDocumentSection(')),planContext);
const header=value=>JSON.parse(JSON.stringify(planContext.planHeaderText(value)));
assert.deepEqual(header({display_title:'Saved task title',task:'Original request',goal:{body:{intended_outcome:'The complete accepted outcome.'}}}),
 {title:'Saved task title',subtitle:'The complete accepted outcome.'});
assert.deepEqual(header({task:'Build the dashboard',goal:{body:{intended_outcome:'Build the dashboard'}}}),
 {title:'Build the dashboard',subtitle:''});
assert.deepEqual(header({task:'Build the dashboard',goal:{body:{intended_outcome:'  Build   the dashboard  '}}}),
 {title:'Build the dashboard',subtitle:''},'whitespace alone is not a distinct outcome');
assert.deepEqual(header({task:'Review code',goal:{body:{}}}),{title:'Review code',subtitle:''});
const planText=node=>[node.textContent,...node.children.map(planText)].join(' ');
const recorded=planContext.planModelDetails({run:'r',model_settings:{roles:{terra:'glm',sol:'mimo'}},monitor:{roles:{terra:{model:'glm'},requirements:{model:'flash'},completion:{reasoning_effort:'high'}}}});
assert.match(planText(recorded),/3 saved model routes/,'a repeated reported route is counted once and effort alone is not a model route');
assert.match(planText(recorded),/View models & effort.*Recorded route details/,'the existing read-only model panel remains inspectable');
assert.doesNotMatch(planText(recorded),/6 roles|Independent|PASS|Accepted/,'configured routes cannot invent independence or acceptance');
assert.match(planText(planContext.planModelDetails({run:'r'})),/No saved model routes recorded/,'missing routes remain explicitly unavailable');
console.log('Saved Plan title, distinct outcome and honest model-route inspection checks passed.');

// The visible Plan must show the actual current conversation draft selected
// before final review installs a goal contract. Viewing it grants no approval.
const draftNode=(tag,text='')=>({tagName:tag.toUpperCase(),textContent:text,children:[],dataset:{},
 append(...items){this.children.push(...items);},replaceChildren(...items){this.children=[...items];},setAttribute(name,value){this[name]=value;}});
const draftHost=draftNode('div');
const draftContext=vm.createContext({$:()=>draftHost,n:draftNode,card:()=>draftNode('div'),
 disclosure:(label,key,body)=>{const node=draftNode('details');node.append(draftNode('summary',label),...body);return node;},
 renderDocument:()=>draftNode('pre','Saved earlier draft'),planEntryText:row=>row.objective||row.id,
 taskTitle:run=>run.task,statusInfo:()=>({label:'Planning'})});
vm.runInContext(source.slice(source.indexOf('function currentConversationDraft('),source.indexOf('function renderQuietPlanPreview(')),draftContext);
vm.runInContext(source.slice(source.indexOf('function renderBrief('),source.indexOf('function output(')),draftContext);
const attachedDraft={revision:8,status:'current',goal:'The current attached draft',requirements:['Preserve all work'],milestones:[{id:'M1',objective:'Verify the visible Plan'}],outstanding_questions:[{question:'Which saved checkpoint?'}],attribution:{model:'zai-coding-plan/glm-5.3',reasoning_effort:'high'},freshness:{state:'fresh',updated_at:'2026-10-12T03:00:00Z'}};
const draftRun={run:'attached-run',task:'Original task',conversation:{id:'attached-conversation',plan_drafts:[attachedDraft],plan_gate:{architect_reviewed:false}}};
draftContext.renderBrief(draftRun);
assert.match(planText(draftHost),/The current attached draft/,'An attached current draft must be visible before a goal contract exists');
assert.match(planText(draftHost),/glm-5.3 · high/);assert.match(planText(draftHost),/Preserve all work/);assert.match(planText(draftHost),/Which saved checkpoint/);
assert.match(planText(draftHost),/Draft only.*Independent plan review and your approval are required/);
assert.doesNotMatch(planText(draftHost),/No saved plan yet|Plan not ready yet|Reviewed & approved/);
draftContext.renderBrief({...draftRun,goal:{revision:7,origin:'glm_draft',approval_status:'draft',body:{intended_outcome:'An older saved contract'}}});
assert.match(planText(draftHost),/The current attached draft/);assert.doesNotMatch(planText(draftHost),/An older saved contract/);
const reviewedGoal={revision:9,approval_status:'approved',body:{intended_outcome:'The reviewed contract',requirements:['Exact approved scope']}};
assert.equal(draftContext.runPlanDraft({...draftRun,goal:reviewedGoal}).goal,'The reviewed contract','An approved contract remains authoritative without a pending product change');
assert.equal(draftContext.runPlanDraft({...draftRun,goal:reviewedGoal,conversation:{...draftRun.conversation,plan_gate:{pending_product_change:true}}}),attachedDraft,'A pending product change keeps the current draft separate from old approval');
assert.equal((function buttons(node){return (node.tagName==='BUTTON'?1:0)+node.children.reduce((total,child)=>total+buttons(child),0);})(draftHost),0,'Draft inspection offers no approval or build action');
console.log('Visible attached draft and unchanged reviewed-contract selection checks passed.');

// Payload boundaries: chatting needs no project; answering never invents an
// approval or switches the selected question. These are exercised functions.
const payloadSource=source.slice(source.indexOf('function conversationStatus('),source.indexOf('function icon('));
const payloadContext=vm.createContext({});
vm.runInContext(payloadSource,payloadContext);
const plain=value=>JSON.parse(JSON.stringify(value));
assert.deepEqual(plain(payloadContext.conversationPayload('  Build a tracker  ',{glm_model:'zai-coding-plan/glm-5.3'},'request-1')),
 {text:'Build a tracker',models:{glm_model:'zai-coding-plan/glm-5.3'},request_id:'request-1'});
const question=projectedRun('clarification',{workspace:'/repo',run:'/repo/run',questions:[{id:'Q2',question:'Private or public?'}]});
assert.deepEqual(plain(payloadContext.chatPayload(question,'Private','Q2','request-2')),
 {workspace:'/repo',run:'/repo/run',text:'Private',question_id:'Q2',request_id:'request-2',resolver_request:question.human_escalation.request_id,resolver_token:question.human_escalation.request_token});
assert.throws(()=>payloadContext.chatPayload({workspace:'/repo',run:'/repo/run',questions:question.questions},'Private','Q2','request-2'),/current Resolver request/);
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
// refresh(includeList,includeDetail) then re-arms both background reads: the
// selected task at 2 s and the task list at 10 s.
async function pollingCheck() {
 const scheduled=[],deferred=[];let calls=0,active=0,maxActive=0;
 const pollContext=vm.createContext({
   clearTimeout:()=>{},setTimeout:(fn,ms)=>{scheduled.push(ms);return 1;},
   refreshOnce:()=>{calls++;active++;maxActive=Math.max(maxActive,active);return new Promise(resolve=>deferred.push(()=>{active--;resolve();}));}
 });
 vm.runInContext('let refreshPromise=null,refreshAgain=false,refreshListAgain=false,refreshDetailAgain=false,refreshTimer=null,refreshListTimer=null;\n'+source.slice(source.indexOf('async function refresh('),source.indexOf('function restoreSelection()')),pollContext);
 const first=pollContext.refresh();const second=pollContext.refresh();const third=pollContext.refresh();
 assert.equal(calls,1);assert.equal(scheduled.length,0);
 deferred.shift()();await new Promise(resolve=>setImmediate(resolve));
 assert.equal(calls,2);assert.equal(scheduled.length,0);assert.equal(maxActive,1);
 deferred.shift()();await Promise.all([first,second,third]);
 assert.equal(calls,2);assert.deepEqual(scheduled,[2000,10000]);assert.equal(maxActive,1);
}
pollingCheck().then(()=>console.log('Project-free payloads, question targeting, and slow-response polling passed.')).catch(error=>{console.error(error);process.exitCode=1;});
