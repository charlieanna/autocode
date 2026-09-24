const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../dashboard_app.js'), 'utf8');
class Element {
  constructor(tag, text = '') { this.tag = tag; this.textContent = text; this.children = []; }
  append(...children) { this.children.push(...children); }
  setAttribute(key, value) { this[key] = value; }
}
const context = vm.createContext({
  n: (tag, text) => new Element(tag, text),
  card: (_, cls) => Object.assign(new Element('div'), {className: cls}),
  human: value => value, statusInfo: () => ({group: 'running'}),
});
vm.runInContext(source.slice(source.indexOf('function jointPlanning('), source.indexOf('function setView(')), context);
const text = node => [node.textContent, ...node.children.map(text)].join(' ');
const run = {status: 'RUNNING', stage: 'terra', active_stage: {stage: 'terra', role: 'terra'},
  model_settings: {engine: 'codex', roles: {astra: 'sol-plan', terra: 'terra-next', sol: 'sol-review', completion: 'astra-complete'}, role_efforts: {terra: 'high'}},
  monitor: {live: {state: 'alive'}, orchestration: {enabled: true}, active_execution: {kind: 'model', model: 'terra-executing', reasoning_effort: 'medium'},
    stage_history: [{stage: 'astra_discovery', role: 'astra', finished_at: '2026-09-24T22:00:00Z', exit_code: 0, execution: {model: 'sol-at-launch'}},
      {stage: 'orchestrator', runner_owned: true, finished_at: '2026-09-24T22:01:00Z', execution: {kind: 'runner'}}]}};
let panel = context.workflowModelsPanel(run), rendered = text(panel);
for (const expected of ['Stages & models', 'Requirements Gatherer', 'Combined with discovery', 'no independent plan-review stage',
  'Plan reviewer', 'Not enabled for this run', 'Orchestrator', 'Runner · No model call', 'Builder', 'Validator', 'Completion owner', 'Resolver',
  'Configured: terra-next · high reasoning', 'Launch: terra-executing · medium reasoning', 'Last launch: sol-at-launch', 'Conditional · not used yet']) {
  assert.ok(rendered.includes(expected), expected);
}
assert.ok(!panel.children.some(child => child.tag === 'details'), 'Models must be visible without opening a disclosure');
let cards = panel.children.at(-1).children;
assert.equal(cards.filter(card => card.className.includes(' active')).length, 1);
assert.match(text(cards.find(card => card.className.includes(' active'))), /Builder/);
run.monitor.live.state = 'exited';
panel = context.workflowModelsPanel(run);
assert.equal(panel.children.at(-1).children.filter(card => card.className.includes(' active')).length, 0);
assert.match(text(panel), /Last reported active · worker unverified/);
const joint = {...run, stage: 'astra_challenge', active_stage: {stage: 'astra_challenge', role: 'astra', route_role: 'plan_reviewer'},
  model_settings: {joint_planning: true, roles: {requirements: 'requirements-model', glm: 'planner-model', plan_reviewer: 'reviewer-model', completion: 'completion-model'}},
  monitor: {...run.monitor, live: {state: 'alive'}, active_execution: {model: 'reviewer-model'}, stage_history: []}};
panel = context.workflowModelsPanel(joint); cards = panel.children.at(-1).children;
assert.match(text(cards[0]), /Configured: requirements-model/);
assert.match(text(cards[1]), /Configured: planner-model/);
assert.match(text(cards[2]), /Configured: reviewer-model.*Active now/);
assert.equal(cards.filter(card => card.className.includes(' active')).length, 1);
assert.ok(!text(panel).includes('no independent plan-review stage'));
assert.equal(context.stageName({stage: 'requirements_gather'}), 'Requirements Gatherer · Gathering requirements');
assert.equal(context.stageName({stage: 'astra_resolve'}), 'Resolver · Diagnosing a failure');
assert.equal(context.executionLabel({kind: 'model'}), 'Model not recorded');
console.log('Visible stage routes, missing roles, actual launches, and verified activity passed.');
