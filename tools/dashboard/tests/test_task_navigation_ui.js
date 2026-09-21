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
assert.match(app, /button\('',\(\)=>filterTasks\(key\),'summary-filter'\)/);
assert.match(css, /summary-filter/);
console.log('Task navigation has one clickable status-count control per status.');
