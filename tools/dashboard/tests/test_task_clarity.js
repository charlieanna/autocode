// Run with: node tests/test_task_clarity.js
// Classification uses real saved-state shapes; no task or provider is started.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../dashboard_app.js'), 'utf8');
const context = vm.createContext({URLSearchParams});
vm.runInContext(source.slice(source.indexOf('const basename ='), source.indexOf("document.addEventListener('focusin'")) +
  '\nlet taskFilter="all", projectFilter="", latestData=null;\n' +
  source.slice(source.indexOf('function concise('), source.indexOf('function badge(')) +
  source.slice(source.indexOf('function jointPlanning('), source.indexOf('function setView(')), context);
const classify = run => context.statusInfo(run);
const rejectedPlanning=classify({status:'PAUSED_INVALID_OUTPUT',active_stage:{stage:'astra_discovery_report_repair',role:'glm',exit_code:0},stop_reason:'A feedback-based decision needs an actual saved feedback event'});
assert.equal(rejectedPlanning.label,'Planning needs retry');
assert.equal(rejectedPlanning.action,'Retry planning');
assert.match(rejectedPlanning.reason,/no matching saved feedback event/);
assert.match(rejectedPlanning.reason,/final plan approval is still required/);
const question = {id: 'Q1', question: 'Which repository should this validate?'};

// Historical fields cannot make a completed or resumed task demand an answer.
assert.equal(classify({status: 'TASK_COMPLETE', questions: [question], user_request: {kind: 'permission'}}).group, 'complete');
assert.equal(classify({status: 'RUNNING', questions: [question], user_request: {decision_needed: 'Old question'}}).group, 'stopped');
const activity = {status: 'RUNNING', stage: 'terra', active_stage: {stage: 'terra', started_at: '2026-09-20T07:00:00Z'}};
const live = {...activity, monitor: {live: {state: 'alive'}, workflow_mode: 'glm_final_audit_v2', active_role: 'terra', objective: 'Finish validation'}};
assert.equal(classify(live).label, 'Running');
assert.equal(context.taskOverviewState(live).verified, true);
assert.equal(context.taskOverviewState(live).step, 'Current step · Builder · Implementing');
assert.equal(context.taskOverviewState(live).objective, 'Finish validation');
assert.equal(context.planningMode(live), 'Builder-led · Completion owner final audit');
const exited = {...live, monitor: {...live.monitor, live: {state: 'exited'}}};
assert.equal(classify(exited).group, 'stopped');
assert.equal(context.taskOverviewState(exited).active, false);
assert.equal(context.taskOverviewState({...live,status:'TASK_COMPLETE'}).verified, false);
assert.equal(context.taskOverviewState({...live,status:'PAUSED_INTERVENTION'}).verified, false);
assert.equal(context.taskOverviewState(activity).verified, false);
const savedPause={status:'PAUSED_INTERVENTION',stage:'astra_challenge',model_settings:{joint_planning:true},
  monitor:{next_stage:'astra_challenge',live:{state:'none'}},
  stages:[{stage:'astra_discovery',finished_at:'2026-09-21T19:09:12Z',exit_code:0}]};
assert.equal(context.taskOverviewState(savedPause).step,'Next step · Plan reviewer · Challenging the plan');
assert.equal(context.taskOverviewState({...savedPause,monitor:{live:{state:'none'}}}).step,'Last completed step · Requirements planner · Planning');
assert.equal(context.taskOverviewState({...savedPause,monitor:{},stages:[]}).step,'No active step');
assert.equal(context.taskOverviewState(exited).step,'Last reported active step · Builder · Implementing');
assert.equal(classify(activity).group, 'stopped');
assert.match(classify(activity).reason, /No live worker is confirmed/);
assert.equal(context.taskOverviewState(activity).active, false);
assert.match(context.taskGroups().find(group => group[0] === 'running')[2], /does not confirm.*process/);
for (const finished of [{finished_at: '2026-09-20T07:01:00Z'}, {exit_code: 0}, {exit_code: -15}]) {
  const result = classify({...activity, active_stage: {...activity.active_stage, ...finished}});
  assert.equal(result.group, 'stopped');
  assert.equal(result.label, 'Ready to continue');
}

// Recovery and runner pauses are separate from actual unanswered questions.
for (const status of ['PAUSED_PROVIDER_UNCERTAIN', 'PAUSED_UNCERTAIN_STAGE']) {
  const result = classify({status, questions: [question], active_stage: {stage: 'terra'}});
  assert.equal(result.group, 'stopped');
  assert.equal(result.label, 'Interrupted');
}
const paused = classify({status: 'PAUSED_COMPLETION_REVIEW', stop_reason: 'Request completion instead of another implementation batch'});
assert.equal(paused.group, 'stopped');
assert.equal(paused.action, 'View pause reason');
assert.match(paused.reason, /Request completion/);
assert.equal(classify({status: 'RUNNING', error: 'Project is unavailable'}).group, 'stopped');
assert.equal(classify({status: 'WAITING_FOR_USER', state_error: 'Malformed checkpoint'}).label, 'Unavailable');

// A joint draft only needs approval after Astra finalizes it and a token exists.
const finalized = {status: 'AWAITING_GOAL_APPROVAL', model_settings: {joint_planning: true},
  goal_token: 'goal:1:hash', goal: {origin: 'astra_finalize', approval_status: 'draft', body: {intended_outcome: 'Build an exercise tracker'}}};
assert.equal(classify(finalized).label, 'Approve plan');
assert.equal(classify(finalized).reason, 'Build an exercise tracker');
assert.equal(classify({...finalized, goal_token: ''}).group, 'stopped');
assert.equal(classify({...finalized, goal: {...finalized.goal, origin: 'glm_draft'}}).group, 'stopped');
assert.equal(classify({...finalized, goal: {...finalized.goal, approval_status: 'approved'}}).group, 'stopped');
assert.equal(classify({...finalized, questions: [question]}).action, 'Answer 1 question');
const multiple = classify({status: 'WAITING_FOR_USER', questions: [question, {id: 'Q2', question: 'What should pass?'}]});
assert.equal(multiple.group, 'attention');
assert.equal(multiple.action, 'Answer 2 questions');
assert.equal(multiple.reason, question.question);

// Review requirements can exist throughout implementation. Only a current
// human-review request should divert a waiting question to the output tab.
const review = {status: 'WAITING_FOR_USER', review_token: 'artifact:new',
  review_criteria: [{id: 'C1'}, {id: 'C2'}], human_reviews: {C1: {token: 'artifact:new'}, C2: {token: 'artifact:old'}}};
const output = classify({...review, user_request: {kind: 'human_review', decision_needed: 'Review the interface'}});
assert.equal(output.label, 'Review output');
assert.equal(output.action, 'Review 1 item');
assert.equal(output.tab, 'execution');
const blocker = classify({...review, questions: [question], user_request: {kind: 'blocker', decision_needed: question.question}});
assert.equal(blocker.action, 'Answer 1 question');
assert.equal(blocker.tab, 'interview');
assert.equal(classify({status: 'BLOCKED_HUMAN', questions: [question], user_request: {decision_needed:'Choose the deployment target'}}).reason, 'Choose the deployment target');
assert.equal(classify({status: 'DRY_RUN', questions: [question]}).group, 'other');
assert.equal(classify({status: 'UNRECOGNIZED_STATE'}).group, 'other');

// Compact titles retain a useful task identity; time fallback is deterministic.
assert.equal(context.concise('  Build\n\t a tracker  '), 'Build a tracker');
assert.equal(context.taskTitle({task: 'Build the tracker. Add only its core workflow.'}), 'Build the tracker.');
assert.equal(context.taskTitle({task: 'Continue the tutor through all work in\nPLAN.md. Stay within scope.'}), 'Continue the tutor through all work in PLAN.md.');
assert.ok(context.taskTitle({task: 'Design a durable local task queue '.repeat(12)}).length <= 110);
assert.equal(context.taskStarted({created_at: '2026-09-20T08:00:00Z', stages: [{started_at: '2026-09-20T09:00:00Z'}]}), Date.parse('2026-09-20T08:00:00Z'));
assert.equal(context.taskStarted({stages: [{started_at: 'invalid'}, {started_at: '2026-09-20T09:00:00Z'}], active_stage: {started_at: '2026-09-20T08:00:00Z'}}), Date.parse('2026-09-20T08:00:00Z'));
assert.match(context.taskIdentity({run: '/repo/.autocode/runs/20260920-080000-first-task'}), /^Saved task · 20260920-080000/);
assert.notEqual(context.taskIdentity({created_at: '2026-09-20T08:00:01Z'}), context.taskIdentity({created_at: '2026-09-20T08:00:02Z'}));

// New dashboard links use only the stable run suffix. The full legacy route
// remains available whenever a short key cannot identify exactly one run.
const shortA={workspace:'/work/a',run:'/work/a/.autocode/runs/20260921-132731-task-deadbeef'};
const shortB={workspace:'/work/b',run:'/work/b/.autocode/runs/20260921-132732-task-cafebabe'};
assert.equal(context.shortRunKey(shortA),'r-deadbeef');
assert.equal(context.runSelection(shortA,[shortA,shortB]),'run=r-deadbeef');
assert.equal(context.matchingShortRuns('r-cafebabe',[shortA,shortB]).length,1);
const collision={workspace:'/work/c',run:'/work/c/.autocode/runs/20260921-132733-other-deadbeef'};
assert.equal(context.runSelection(shortA,[shortA,collision]),'task='+encodeURIComponent(shortA.workspace)+'&run='+encodeURIComponent(shortA.run));
assert.equal(context.matchingShortRuns('r-deadbeef',[shortA,collision]).length,2);
assert.equal(context.runSelection({workspace:'/work/old',run:'/work/old/.autocode/runs/legacy'},[shortA]),'task=%2Fwork%2Fold&run=%2Fwork%2Fold%2F.autocode%2Fruns%2Flegacy');

// Project and status filter both survive route serialization and restoration.
const project = '/Users/dev/workspace/Research & Design';
const route = context.taskSelection('attention', project);
const params = new URLSearchParams(route);
assert.equal(params.get('filter'), 'attention');
assert.equal(params.get('project'), project);
assert.equal(context.taskSelection('all', ''), 'tasks');
context.testProject = project;
vm.runInContext('projectFilter=testProject; taskFilter="stopped";', context);
assert.equal(context.taskSelection(), context.taskSelection('stopped', project));
assert.equal(context.taskScopeTitle(), 'Research & Design');
vm.runInContext('projectFilter=""; taskFilter="attention";', context);
assert.equal(context.taskScopeTitle(), 'Waiting on you');
const restored = [];
context.location = {hash: '#' + route};
context.stored = () => 'new';
context.filterTasks = (...args) => restored.push(args);
vm.runInContext(source.slice(source.indexOf('function restoreSelection()'), source.indexOf("window.addEventListener('hashchange'")), context);
context.restoreSelection();
assert.deepEqual(restored.pop(), ['attention', project]);
context.location.hash = '#tasks&filter=invalid&project=' + encodeURIComponent(project);
context.restoreSelection();
assert.deepEqual(restored.pop(), ['all', project]);
context.location={hash:'#run=r-deadbeef'};
context.latestData=null;
context.pendingShortRun='';
const shortOpened=[];
context.openRun=run=>shortOpened.push(run);
context.restoreSelection();
assert.equal(context.pendingShortRun,'r-deadbeef');
assert.equal(shortOpened.length,0);
assert.equal(context.restorePendingShortRun({runs:[shortA,shortB]}),true);
assert.equal(shortOpened[0].run,shortA.run);
console.log('Task classification, concise identity, and project/filter route checks passed.');

// Fresh files and active-looking events are never evidence of a live process.
for (const state of ['unknown','none','exited']) {
  const saved={...activity,monitor:{live:{state},checkpoint_updated:new Date().toISOString(),log_updated:new Date().toISOString(),activity:[{status:'running'}]}};
  assert.equal(classify(saved).group,'stopped');
  assert.equal(classify(saved).stateLabel,'Stopped at a checkpoint');
}
assert.equal(classify(live).stateLabel,'Worker confirmed running');
assert.equal(classify({status:'WAITING_FOR_USER',questions:[question]}).stateLabel,'Waiting for your decision');
assert.equal(classify({status:'WAITING_FOR_USER',questions:[],user_request:null}).group,'stopped');
assert.equal(classify({status:'TASK_COMPLETE'}).stateLabel,'Complete');
for(const status of ['PAUSED_INVALID_OUTPUT','PAUSED_REPORT_REPAIR_LIMIT','PAUSED_PERMISSION_RECONCILIATION']) {
  const blocked=classify({status,stage:'terra',questions:[question],user_request:{kind:'permission'},stop_reason:'Report schema rejected: missing summary'});
  assert.equal(blocked.group,'stopped');
  assert.equal(blocked.stateLabel,'Internally blocked');
  assert.equal(blocked.reason,'Report schema rejected: missing summary');
  assert.equal(blocked.action,'Inspect failure');
}
