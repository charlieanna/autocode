// Run with: node tests/test_task_clarity.js
// Classification uses real saved-state shapes; no task or provider is started.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../dashboard_app.js'), 'utf8');
const context = vm.createContext({URLSearchParams});
vm.runInContext(source.slice(source.indexOf('const basename ='), source.indexOf("document.addEventListener('focusin'")) +
  '\nlet taskFilter="all", projectFilter="";\n' +
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
assert.equal(context.taskOverviewState(live).step, 'Current step · GLM · Implementing');
assert.equal(context.taskOverviewState(live).objective, 'Finish validation');
assert.equal(context.planningMode(live), 'GLM builds · Astra final audit');
const exited = {...live, monitor: {...live.monitor, live: {state: 'exited'}}};
assert.equal(classify(exited).group, 'stopped');
assert.equal(context.taskOverviewState(exited).active, false);
assert.equal(context.taskOverviewState({...live,status:'TASK_COMPLETE'}).verified, false);
assert.equal(context.taskOverviewState({...live,status:'PAUSED_INTERVENTION'}).verified, false);
assert.equal(context.taskOverviewState(activity).verified, false);
assert.equal(classify(activity).group, 'running');
assert.match(classify(activity).reason, /Terra.*recorded/);
assert.ok(classify(activity).reason.includes(new Date(activity.active_stage.started_at).toLocaleString()));
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
assert.equal(classify({status: 'BLOCKED_HUMAN', stop_reason: 'Choose the deployment target'}).reason, 'Choose the deployment target');
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
console.log('Task classification, concise identity, and project/filter route checks passed.');
