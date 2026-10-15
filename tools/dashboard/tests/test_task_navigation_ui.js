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
// Exercise the shipped cross-pane navigation. Selecting a tab during a
// refresh must stay passive; an explicit artifact link must reveal its mobile
// drawer, and returning to a chat action must remove inertness and expand the
// exact approval disclosure without submitting that action.
const paneNodes=new Map(),paneClasses=new Set(),paneFocus=[],paneScroll=[];
const paneNode=id=>({hidden:false,inert:false,disabled:false,isConnected:true,
  classList:{add(){},remove(){},contains(){return false;}},setAttribute(){},
  focus(){for(let p=this.parentElement;p;p=p.parentElement)if(p.inert||(p.tagName==='DETAILS'&&!p.open))return;paneFocus.push(id);},
  scrollIntoView(){paneScroll.push(id);}});
for(const id of ['#details-pane-title','#task-overview','#detail-grid','#now','#plan','#execution','#changes','#preview','#live-controls','#goal','#task-detail','#details-drawer-toggle','#details-drawer-close','#details-scrim','.conversation-column','#change-text','#question-target','#conversation'])paneNodes.set(id,paneNode(id));
paneNodes.set('.app-shell',{classList:{contains:name=>paneClasses.has(name),add:name=>paneClasses.add(name),remove:name=>paneClasses.delete(name)}});
const questionCard=Object.assign(paneNode('question:q2'),{dataset:{questionCard:'q2'}});
paneNodes.get('#conversation').querySelectorAll=()=>[questionCard];
paneNodes.get('#change-text').parentElement=paneNodes.get('.conversation-column');
paneNodes.get('#change-text').value='Keep this unsent answer';
paneNodes.get('#question-target').value='q1';
let questionChanges=0;paneNodes.get('#question-target').onchange=()=>questionChanges++;
const approvalDetails={tagName:'DETAILS',open:false,parentElement:paneNodes.get('.conversation-column')};
const approval=Object.assign(paneNode('approve:run:token'),{dataset:{focusKey:'approve:run:token'},parentElement:approvalDetails});
const paneContext=vm.createContext({$:selector=>paneNodes.get(selector),latestRun:{run:'run'},currentTab:'now',chosen:null,
  taskReadError:'',WORKSPACE_PANES:['now','plan','preview','changes','execution','overview'],
  matchMedia:()=>({matches:true}),stopPreview(){},renderPrimaryAction(){},settleThreadScroll(){},renderPreviewState(){},
  document:{querySelectorAll:selector=>selector==='[data-focus-key]'?[approval]:[]}});
vm.runInContext(shipped('function activateTab(', 'function openRun(')
  +shipped('function openDetailsDrawer(', "$('#nav-toggle').onclick")
  +shipped('function focusChatAction(', 'function renderInlineTaskAction(')
  +shipped('function questionCardById(', 'function workQuestionsPanel('),paneContext);
const paneFailures=[];
const paneCase=(label,check)=>{try{check();}catch(error){paneFailures.push(label+': '+error.message);}};
paneCase('passive tab selection',()=>{
  vm.runInContext("activateTab('plan')",paneContext);
  assert.equal(paneClasses.has('details-open'),false,'Polling a pane must not open its drawer');
});
paneCase('explicit mobile artifact navigation',()=>{
  vm.runInContext("activateTab('preview',true)",paneContext);
  assert.equal(paneClasses.has('details-open'),true,'The explicit Preview link reveals mobile Details');
  assert.equal(paneNodes.get('#preview').hidden,false);
  assert.equal(paneNodes.get('.conversation-column').inert,true);
});
paneCase('Work question returns to conversation',()=>{
  paneClasses.add('details-open');paneNodes.get('.conversation-column').inert=true;
  vm.runInContext("focusQuestionCard(latestRun,{id:'q2'})",paneContext);
  assert.equal(paneClasses.has('details-open'),false,'The question link closes the artifact drawer');
  assert.equal(paneNodes.get('.conversation-column').inert,false);
  assert.equal(paneNodes.get('#question-target').value,'q2');assert.equal(questionChanges,1);
  assert.equal(paneNodes.get('#change-text').value,'Keep this unsent answer');
  assert.equal(paneFocus.at(-1),'#change-text');assert.ok(paneScroll.includes('question:q2'));
});
paneCase('exact collapsed approval target is revealed',()=>{
  paneClasses.add('details-open');paneNodes.get('.conversation-column').inert=true;approvalDetails.open=false;
  vm.runInContext("focusChatAction('approve:run:token')",paneContext);
  assert.equal(paneClasses.has('details-open'),false);
  assert.equal(approvalDetails.open,true,'The exact approval ancestor disclosure is opened');
  assert.equal(paneFocus.at(-1),'approve:run:token');
});
assert.deepEqual(paneFailures,[],'Cross-pane navigation must reveal its actual destination');
console.log('Task navigation ordering, drawer Escape/focus and cross-pane destination visibility passed.');
