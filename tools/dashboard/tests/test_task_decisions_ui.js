// Current checkpoint authority and chronological history, exercised with saved-state scenarios.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../dashboard_app.js'),'utf8');
const context=vm.createContext({URL,URLSearchParams});
vm.runInContext(source.slice(source.indexOf('const basename ='),source.indexOf("document.addEventListener('focusin'"))+source.slice(source.indexOf('function concise('),source.indexOf('function badge('))+source.slice(source.indexOf('function jointPlanning('),source.indexOf('function setView('))+source.slice(source.indexOf('function interruptedAttempt('),source.indexOf('function renderPrimaryAction('))+source.slice(source.indexOf('function messageTime('),source.indexOf('function icon('))+source.slice(source.indexOf('function taskMessages('),source.indexOf('function settleThreadScroll(')),context);
const approved={run:'/fixture/current',status:'PAUSED_REQUESTED',stage:'terra',iteration:16,goal:{revision:4,approval_status:'approved'},stages:[{stage:'terra'}],model_settings:{roles:{terra:'zai-coding-plan/glm-5.3'}},monitor:{next_stage:'terra',limits_known:true,iteration_limit:18,live:{state:'none'}},planning_messages:[{text:'Older draft needs approval',created_at:'2026-09-19T08:00:00Z'}]};
assert.equal(context.taskDecision(approved).title,'No approval pending');
assert.equal(context.taskDecision(approved).action.label,'Resume task');
assert.match(context.taskDecision(approved).after,/already|approved plan/);
assert.equal(context.taskPhase(approved),'implementation');
assert.equal(context.taskPosition(approved),'Plan r4 · approved  /  Iteration 16 of 18');
assert.equal(context.stageName(approved),'Builder · Implementing');
const running={...approved,status:'RUNNING',active_stage:{stage:'terra'},monitor:{...approved.monitor,live:{state:'alive'}}};
assert.equal(context.taskDecision(running).required,false);
assert.equal(context.taskDecision(running).action.kind,'pause');
const ready={status:'AWAITING_GOAL_APPROVAL',goal_token:'r5:current',goal:{revision:5,origin:'astra_finalize',approval_status:'draft'},model_settings:{joint_planning:true}};
assert.equal(context.taskDecision(ready).title,'Approve plan revision 5');
assert.equal(context.taskDecision(ready).required,true);
assert.match(context.taskDecision(ready).after,/records approval; Start building or Resume task/);
assert.equal(context.taskPhase(ready),'approval');
assert.equal(context.taskDecision({...ready,status:'RUNNING',stage:'glm_revise',active_stage:{stage:'glm_revise'},monitor:{live:{state:'alive'}},goal:{...ready.goal,origin:'glm_revise'}}).required,false);
assert.equal(context.taskDecision({...ready,status:'PAUSED_REQUESTED'}).required,false);
const question={...ready,status:'WAITING_FOR_USER',questions:[{id:'q1',question:'Which scope?'}]};
assert.equal(context.taskDecision(question).title,'Answer 1 question');
assert.equal(context.taskDecision(question).action.kind,'answer');
assert.equal(context.taskDecision({...ready},true).required,false);
const review={...approved,status:'WAITING_FOR_USER',user_request:{kind:'human_review'},review_token:'new-result',review_criteria:[{id:'R1',criterion:'Inspect output'}],human_reviews:{R1:{token:'old-result'}}};
assert.equal(context.taskDecision(review).title,'Review the finished work');
assert.equal(context.taskDecision({...review,human_reviews:{R1:{token:'new-result'}}}).title,'Your review is saved');
assert.equal(context.taskDecision({...approved,status:'TASK_COMPLETE'}).required,false);
// Runner-owned orchestration has no provider PID or active_stage. A retained
// batch reports activity, while a bare next_stage is only a saved checkpoint.
const orchestration={...approved,stage:'orchestrator',stages:[],status:'RUNNING',
  monitor:{next_stage:'orchestrator',live:{state:'none'},orchestration:{enabled:true,max_parallel:2}}};
assert.equal(context.stageName(orchestration),'Orchestrator · Coordinating Builders');
assert.equal(context.taskPhase(orchestration),'orchestration');
assert.equal(context.statusInfo(orchestration).label,'Ready to continue');
const batch={id:'batch-1',status:'BUILDING',workers:[{milestone_id:'M1',status:'RUNNING',workspace:'/repo/builder-1',run_dir:'/repo/run/worker-1'},{milestone_id:'M2',status:'BUILT',workspace:'/repo/builder-2',run_dir:'/repo/run/worker-2'}]};
const building={...orchestration,monitor:{...orchestration.monitor,orchestration_batch:batch}};
assert.equal(context.statusInfo(building).label,'Activity reported');
assert.equal(context.taskOverviewState(building).verified,false);
assert.equal(context.taskOverviewState(building).step,'Last reported step · Orchestrator · Coordinating Builders');
assert.equal(context.taskSentence(building),'Last reported: Orchestrator is coordinating independent Builders');
assert.equal(context.primaryAction(building).kind,'pause');
assert.deepEqual(Array.from(context.workflowConfig(building,{}),row=>row[0]),['astra','astra','orchestrator','terra','sol','completion']);
assert.equal(context.hasOrchestration(approved),false);
assert.equal(context.workflowConfig(approved,{}).some(row=>row[0]==='orchestrator'),false);
for(const status of ['PAUSED_ORCHESTRATOR_WORKER','PAUSED_ORCHESTRATOR_STALE','PAUSED_ORCHESTRATOR_OVERLAP']){
  const paused={...building,status,stop_reason:'Inspect retained worktrees and logs'};
  assert.equal(context.statusInfo(paused).group,'stopped');
  assert.equal(context.statusInfo(paused).reason,paused.stop_reason);
  assert.equal(context.primaryAction(paused).label,'Resume task');
  assert.equal(context.taskOverviewState(paused).verified,false);
}
const integrated={...orchestration,stage:'sol',stages:[{stage:'orchestrator',runner_owned:true,finished_at:'2026-09-23T01:00:00Z'}],monitor:{...orchestration.monitor,next_stage:'sol',orchestration_history:[{...batch,status:'INTEGRATED'}]}};
assert.equal(context.taskPhase(integrated),'review');
assert.notEqual(context.statusInfo(integrated).group,'complete');
assert.equal(context.primaryAction(integrated).label,'Resume task');
assert.equal(context.taskOverviewState({...integrated,monitor:{}}).step,'Last completed step · Orchestrator · Coordinating Builders');
for(const failure of [{exit_code:1},{interrupted:true},{timed_out:true},{rejected:true}])assert.equal(context.stageSucceeded({...integrated.stages[0],...failure}),false);
assert.equal(context.taskPhase({...building,status:'TASK_COMPLETE'}),'complete');
assert.equal(context.taskPhase({...ready,monitor:orchestration.monitor}),'approval');
// Render the workflow itself to check the added step and legacy layout.
class Element{
  constructor(tag,text=''){this.tag=tag;this.textContent=text;this.children=[];this.dataset={};this.classList={add:()=>{}};}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  setAttribute(key,value){this[key]=value;}
}
const now=new Element('div');
Object.assign(context,{$:()=>now,n:(tag,text)=>new Element(tag,text),card:()=>new Element('div'),button:label=>new Element('button',label),taskActionBusy:()=>false,monitorPanel:()=>new Element('div')});
context.renderTaskNow(building);
let workflow=now.children.find(row=>row.tag==='ol');
assert.deepEqual(workflow.children.map(row=>row.textContent),['Plan','Your approval','Orchestrator','Build','Review','Complete']);
assert.equal(workflow.children.find(row=>row['aria-current']==='step').textContent,'Orchestrator');
context.renderTaskNow(approved);
workflow=now.children.find(row=>row.tag==='ol');
assert.equal(workflow.children.length,5);
assert.equal(workflow.children.find(row=>row['aria-current']==='step').textContent,'Build');
const messages=context.taskMessages({draft_messages:[{text:'Initial idea',created_at:'2026-09-19T01:00:00Z'}],planning_messages:[{text:'Draft',created_at:'2026-09-19T02:00:00Z'},{text:'Revision',created_at:'2026-09-19T04:00:00Z'}],answers:{q1:{text:'Saved answer',at:'2026-09-19T03:00:00Z',question:{question:'Which scope?'}},q2:{text:'Duplicate receipt',at:'2026-09-19T05:00:00Z'}},chat_messages:[{text:'Latest answer',question_id:'q2',created_at:'2026-09-19T05:00:00Z'}]});
assert.deepEqual(Array.from(messages,m=>m.text),['Initial idea','Draft','Saved answer','Revision','Latest answer']);
assert.equal(messages[2].question_text,'Which scope?');
assert.equal(messages[1].planning_history,true);
console.log('Current approval authority, resume decisions, workflow phase, model identity, and conversation ordering passed.');

const progress=context.taskMessages({planning_messages:[{text:'Plan',created_at:'2026-09-19T01:00:00Z'}],progress_messages:[{id:'progress-1',role:'assistant',speaker:'Builder',text:'Fix routing',created_at:'2026-09-19T02:00:00Z'},{id:'progress-2',role:'assistant',speaker:'Validator',text:'Blocked: test failed',created_at:'2026-09-19T03:00:00Z'}]});
assert.deepEqual(Array.from(progress,m=>m.text),['Plan','Fix routing','Blocked: test failed']);
assert.equal(progress[2].speaker,'Validator');
assert.match(source.slice(source.indexOf('function renderConversation('),source.indexOf('function renderConversation(')+500),/progress_messages/);
