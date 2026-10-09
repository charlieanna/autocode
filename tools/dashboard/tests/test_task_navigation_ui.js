// node tests/test_task_navigation_ui.js — keep status navigation compact.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'dashboard.html'), 'utf8');
const app = fs.readFileSync(path.join(root, 'dashboard_app.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'dashboard.css'), 'utf8');

assert.match(html, /id="task-status-filter"/);
assert.doesNotMatch(html, /data-task-filter/);
assert.doesNotMatch(html, /data-filter=/);
assert.match(app, /\$\('#task-status-filter'\)\.onchange = event => filterTasks\(event\.target\.value\)/);
assert.match(app, /\$\('#task-status-filter'\)\.value=taskFilter/);
assert.match(app, /workspaceFilterDefinitions=\[/);
for (const label of ['Waiting on you', 'In progress', 'Paused \/ issues', 'Completed']) {
  assert.match(app, new RegExp("['\"]" + label + "['\"]"));
}
assert.match(app, /item\.dataset\.workspaceFilter=key/);
assert.match(app, /orderedWorkspaceRuns\(filtered\)/);
assert.match(css, /summary-filter/);
console.log('Task navigation has four labeled aggregate controls and priority-ordered rows.');
