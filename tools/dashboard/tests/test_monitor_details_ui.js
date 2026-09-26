// Preserve current master's monitoring data alongside the approved dashboard.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../dashboard_app.js'), 'utf8');
function element(tag, text = '') {
  return {tag, textContent: text, children: [], append(...nodes) {this.children.push(...nodes);}};
}
const context = vm.createContext({
  n: element, card: () => element('div'), human: value => String(value || ''),
  disclosure: (label, key, children) => {const node = element('details', label); node.append(...children); return node;},
  workflowCards: () => element('div'), taskOverviewState: () => ({}),
  metricPanel: metric => element('div', metric.label), button: label => element('button', label),
});
vm.runInContext(source.slice(source.indexOf('function executionCheckpoints('), source.indexOf('function operationalPreview(')), context);
const text = node => [node.textContent, ...node.children.map(text)].join(' ');
const panel = context.monitorDetailsPanel({run: '/fixture/run', monitor: {
  checkpoints: {milestone_id:'M2',paused:true,rows:[
    {label:'Independent validation',status:'not_verified'},
    {label:'Builder tool activity',status:'recorded',completed_tools:21}]},
  orchestration_batch: {id: 'batch-current', status: 'validating', workers: [
    {milestone_id: 'M2', status: 'integrated', workspace: '/fixture/M2', run_dir: '/fixture/logs'},
  ]},
  orchestration_history: [{id: 'batch-prior', status: 'complete'}],
  activity: [{label: 'Dashboard tests', status: 'completed', exit_code: 0, test_summary: '193 tests passed'}],
  findings_summary: {open: 1, resolved: 2, repeated: 1, not_rechecked: 1},
  findings: [{id: 'F1', finding: 'Preserve saved review evidence', severity: 'high', source: 'astra',
    milestone: 'M2', assigned_task: 'repair-1', times_reported: 2, not_rechecked: true}],
}});
const rendered = text(panel);
for (const expected of ['batch-current', 'batch-prior', '/fixture/M2', '/fixture/logs',
  'saved reports, not live process checks', '193 tests passed', '1 open · 2 resolved',
  'F1 · Preserve saved review evidence', 'raised by astra', 'fix: repair-1', 'not rechecked',
  'Milestone checkpoints · M2','21 completed tool events','not_verified',
  'recorded implementation is not acceptance','completed work and evidence retained']) {
  assert.ok(rendered.includes(expected), 'Missing monitoring detail: ' + expected);
}
assert.match(text(context.monitorDetailsPanel({monitor: {}})), /No open findings recorded\. This is not proof of completion/);
console.log('Builder batches, activity, and shared reviewer findings survive dashboard integration.');
