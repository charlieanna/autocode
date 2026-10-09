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
// Execute the shipped drawer functions and actual document key handler. A
// custom drawer consumes its own Escape, while an ordinary page keeps the
// browser's native Escape behavior and does not steal focus.
const vm = require('./dashboard_vm');
const classes = new Set(), focused = [];
const nodes = new Map();
for (const id of ['#nav-toggle', '#drawer-scrim', '#primary-navigation', '#details-drawer-toggle', '#details-scrim', '#details-drawer-close', '.main-area', '.topbar', '.conversation-column']) {
  nodes.set(id, {hidden: false, inert: false, setAttribute() {}, focus() { focused.push(id); }});
}
nodes.set('.app-shell', {classList: {contains: name => classes.has(name), add: name => classes.add(name), remove: name => classes.delete(name)}});
const menus = [{open: false}], listeners = [];
const context = vm.createContext({$: selector => nodes.get(selector), matchMedia: () => ({matches: true}),
  document: {addEventListener: (name, handler) => listeners.push({name, handler}), querySelectorAll: () => menus.filter(menu => menu.open)}});
function shipped(start, end) {
  const first=app.indexOf(start),last=app.indexOf(end,first);
  assert.ok(first>=0&&last>first,'Missing shipped drawer source: '+start);
  return app.slice(first,last);
}
vm.runInContext(shipped('function syncNavigationInert(', '// The details drawer')
  +shipped('function closeDetailsDrawer(', "$('#nav-toggle').onclick")
  +shipped("document.addEventListener('keydown',event=>{", "$('.detail-tabs').addEventListener"), context);
assert.equal(listeners.length,1);
const escape = () => {let prevented=0;listeners[0].handler({key:'Escape',preventDefault() {prevented++;}});return prevented;};
assert.equal(escape(),0,'Closed drawers leave native Escape untouched');
assert.deepEqual(focused,[],'Closed drawers do not move focus');
classes.add('nav-open');nodes.get('.main-area').inert=true;nodes.get('.topbar').inert=true;
assert.equal(escape(),1,'Open custom navigation consumes exactly its Escape');
assert.equal(classes.has('nav-open'),false);assert.equal(nodes.get('.main-area').inert,false);assert.equal(nodes.get('.topbar').inert,false);
assert.deepEqual(focused,['#nav-toggle'],'Only navigation returns focus to its trigger');
focused.length=0;classes.add('details-open');nodes.get('.conversation-column').inert=true;
assert.equal(escape(),1,'Open artifact drawer consumes exactly its Escape');
assert.equal(classes.has('details-open'),false);assert.equal(nodes.get('.conversation-column').inert,false);
assert.deepEqual(focused,['#details-drawer-toggle'],'Only the artifact drawer returns focus to its trigger');
focused.length=0;menus[0].open=true;
assert.equal(escape(),1,'An open custom context menu consumes its Escape');assert.equal(menus[0].open,false);
assert.deepEqual(focused,[],'Closing only a context menu does not move unrelated drawer focus');
assert.equal(escape(),0,'After closure the ordinary page again leaves Escape untouched');
console.log('Task navigation ordering and actual open/closed drawer Escape consumption/focus passed.');
