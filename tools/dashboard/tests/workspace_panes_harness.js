// VM harness for the chat-and-pane workspace (M2). Driven per case by
// tools/dashboard/tests/test_workspace_panes_ui.py; not a standalone gate.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'dashboard_app.js'), 'utf8');
const page = fs.readFileSync(path.join(__dirname, '..', 'dashboard.html'), 'utf8');

const AUTOCODE = '/work/AutoCode';

class Element {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.attributes = {};
    this.dataset = {};
    this.style = {};
    this.hidden = false;
    this.value = '';
    this.textContent = '';
    this.tabIndex = 0;
    this.title = '';
    this.disabled = false;
    this.parentElement = null;
    this.style.setProperty = (name, value) => { this.style[name] = String(value); };
    const set = new Set();
    this.classList = {
      add: (...names) => names.forEach(name => set.add(name)),
      remove: (...names) => names.forEach(name => set.delete(name)),
      toggle: (name, force) => {
        const next = force === undefined ? !set.has(name) : !!force;
        if (next) set.add(name); else set.delete(name);
        return next;
      },
      contains: name => set.has(name),
    };
  }
  append(...nodes) { for (const node of nodes) if (node != null) { node.parentElement = this; this.children.push(node); } }
  prepend(...nodes) { for (const node of [...nodes].reverse()) if (node != null) { node.parentElement = this; this.children.unshift(node); } }
  replaceChildren(...nodes) { this.children = [...nodes.filter(node => node != null)]; for (const node of this.children) node.parentElement = this; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return name in this.attributes ? this.attributes[name] : null; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(type, handler) { (this._listeners ??= {})[type] ??= []; this._listeners[type].push(handler); }
  focus() { document.activeElement = this; }
  blur() { if (document.activeElement === this) document.activeElement = null; }
  click() { if (typeof this.onclick === 'function') this.onclick({preventDefault() {}}); }
  contains(node) { for (let current = node; current; current = current.parentElement) if (current === this) return true; return false; }
  scrollIntoView() {}
  requestSubmit() { if (typeof this.onsubmit === 'function') this.onsubmit({preventDefault() {}, target: this}); }
  showModal() { this.attributes.open = ''; }
  close() { delete this.attributes.open; }
  getBoundingClientRect() { return {width: 0, height: 0, top: 0, left: 0, right: 0, bottom: 0}; }
  matches(selector) { return elementMatches(this, selector); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  querySelectorAll(selector) {
    const found = [];
    for (const node of generate(this)) if (elementMatches(node, selector)) found.push(node);
    return found;
  }
  get childElementCount() { return this.children.length; }
  get firstChild() { return this.children[0] || null; }
  get isConnected() { return true; }
  get id() { return this.attributes.id || this._id || ''; }
  set id(value) { this._id = String(value); }
  // ---- synthetic layout, so transcript scrolling is observable -----------------
  // Leaves are 60px tall; a container is as tall as its children; a scroller's
  // scrollHeight is its content height. offsetTop accumulates sibling heights
  // up to the nearest .thread-scroll, mirroring a positioned scroll container.
  get offsetHeight() { if (this.hidden) return 0; return this.children.length ? this.children.reduce((sum, child) => sum + child.offsetHeight, 0) : 60; }
  get offsetTop() {
    let top = 0, node = this;
    while (node && node.parentElement) {
      const parent = node.parentElement;
      for (const sibling of parent.children) { if (sibling === node) break; top += sibling.offsetHeight; }
      if (parent.classList && parent.classList.contains('thread-scroll')) break;
      node = parent;
    }
    return top;
  }
  get scrollHeight() { return this.children.reduce((sum, child) => sum + child.offsetHeight, 0); }
  get clientHeight() { return this._clientHeight ?? 240; }
  set clientHeight(value) { this._clientHeight = value; }
  get clientWidth() { return this._clientWidth ?? 320; }
  set clientWidth(value) { this._clientWidth = value; }
  get offsetParent() { return this.parentElement; }
  get scrollTop() { return this._scrollTop || 0; }
  set scrollTop(value) {
    this._scrollTop = Number(value) || 0;
    for (const handler of [...((this._listeners && this._listeners.scroll) || [])]) handler({target: this});
  }
}
function* generate(node) { yield node; for (const child of node.children || []) yield* generate(child); }
function elementMatches(node, selector) {
  const selectors = String(selector).split(',').map(item => item.trim()).filter(Boolean);
  return selectors.some(item => {
    if (item.startsWith('.')) return hasClass(node, item.slice(1));
    if (item.startsWith('#')) return node.attributes && node.attributes.id === item.slice(1);
    if (item.startsWith('[')) {
      const match = item.match(/^\[([^\]=]+)(?:=([\"']?)(.*?)\2)?\]$/);
      if (!match || !node.attributes) return false;
      const dataName = match[1].startsWith('data-')
        ? match[1].slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase()) : null;
      const present = match[1] in node.attributes ||
        (dataName != null && node.dataset && node.dataset[dataName] !== undefined);
      if (match[3] === undefined) return present;
      const value = match[1] in node.attributes ? node.attributes[match[1]]
        : dataName != null ? String(node.dataset[dataName]) : undefined;
      return value === match[3];
    }
    return node.tagName === item.toUpperCase();
  });
}

const storage = new Map();
const localStorage = {
  getItem: key => (storage.has(key) ? storage.get(key) : null),
  setItem: (key, value) => { storage.set(key, String(value)); },
  removeItem: key => { storage.delete(key); },
};
let document = createDocument();
let app = null;

function createDocument() {
  const cache = new Map();
  return {
    createElement: tag => new Element(tag),
    createElementNS: (ns, tag) => new Element(tag),
    createTextNode: text => { const node = new Element('#text'); node.textContent = String(text); return node; },
    querySelector: selector => {
      if (!cache.has(selector)) cache.set(selector, new Element('div'));
      return cache.get(selector);
    },
    querySelectorAll: () => [],
    addEventListener: () => {},
    body: new Element('body'),
    documentElement: new Element('html'),
    activeElement: null,
  };
}

// ---- fake dashboard server -------------------------------------------------------
// Cases install a snapshot in serverState.data; the run detail read serves the
// matching saved run record, so every case drives the app's real fetch paths.
const serverState = {data: {workspaces: [AUTOCODE], workspace_ids: {}, runs: [], conversations: [],
                            removed_projects: [], archived_tasks: [], archived_conversations: [], registry: {}},
                     evidence: {}, onAction: null};
const fetchLog = [];
let createdCount = 0;
const fetch = async (url, options = {}) => {
  fetchLog.push({url, options});
  if (url === '/api/runs') return {ok: true, json: async () => serverState.data};
  if (url === '/api/models') return {ok: true, json: async () => ({usable: true, models: [], loading: false})};
  if (url.startsWith('/api/run?')) {
    const query = new URLSearchParams(url.slice('/api/run?'.length));
    const runPath = query.get('run') || '';
    const base = (serverState.data.runs || []).find(row => row.run === runPath)
      || {workspace: query.get('workspace') || '', run: runPath, task: 'Missing task'};
    return {ok: true, json: async () => JSON.parse(JSON.stringify(base))};
  }
  if (url.startsWith('/api/evidence?')) {
    const query = new URLSearchParams(url.slice('/api/evidence?'.length));
    const runPath = query.get('run') || '';
    const saved = serverState.evidence[runPath] || {stages: []};
    return {ok: true, json: async () => JSON.parse(JSON.stringify(saved))};
  }
  if (url.startsWith('/api/conversation?')) {
    const id = new URLSearchParams(url.slice('/api/conversation?'.length)).get('id');
    const doc = (serverState.data.conversations || []).find(row => row.id === id);
    if (!doc) return {ok: false, json: async () => ({error: 'Conversation unavailable'})};
    return {ok: true, json: async () => doc};
  }
  if (url === '/api/action') {
    const body = JSON.parse(options.body || '{}');
    if (serverState.onAction) serverState.onAction(body);
    return {ok: true, json: async () => ({id: 'action-' + (++createdCount), status: 'finished', exit_status: 0})};
  }
  return new Promise(() => {});
};

let uuidCount = 0;
// Boot the shipped page. reloadApp() re-runs the same source in a fresh
// context that keeps the saved localStorage state and lands on the remembered
// selection, which is what a browser reload of the same URL does.
// Mirror the containment the shipped page declares statically: the message
// hosts live inside their transcript scrollers, and the inline action area
// sits inside the #interview transcript. The fake document hands out every
// id-addressed element separately, so scroller math needs this wiring.
function attachStaticLayout() {
  document.querySelector('#draft-scroll').append(document.querySelector('#draft-messages'));
  const interview = document.querySelector('#interview');
  interview.append(document.querySelector('#conversation'));
  interview.append(document.querySelector('#inline-task-action'));
  interview.append(document.querySelector('#thread-checkpoint'));
  interview.append(document.querySelector('#task-attention'));
  interview.append(document.querySelector('#change-history'));
}
function bootApp(hash) {
  document = createDocument();
  const context = vm.createContext({
    document,
    console,
    fetch,
    URLSearchParams,
    localStorage,
    crypto: {randomUUID: () => 'uuid-' + (++uuidCount)},
    history: {replaceState: () => {}},
    location: {hash},
    matchMedia: () => ({matches: false}),
    addEventListener: () => {},
    removeEventListener: () => {},
    window: {addEventListener: () => {}, scrollTo: () => {}},
    requestAnimationFrame: callback => { callback(); return 0; },
    ResizeObserver: class { observe() {} unobserve() {} disconnect() {} },
    AbortSignal: {timeout: () => ({})},
    navigator: {},
    setTimeout, clearTimeout,
  });
  vm.runInContext(source, context);
  attachStaticLayout();
  return vm.runInContext(`(() => {
  return {
    setData: value => { latestData = value; },
    openRun: value => openRun(value),
    openConversation: value => openConversation(value),
    activateTab: value => activateTab(value),
    refresh: () => refresh(),
    state: () => ({view: currentView, tab: currentTab, activeConversation: activeConversation || null, chosen: chosen && {workspace: chosen.workspace, run: chosen.run}}),
  };
})()`, context);
}
function reloadApp() {
  const selection = storage.get('autocode:selection') || 'tasks';
  app = bootApp('#' + selection);
  return app;
}
app = bootApp('#tasks');

// ---- shared inspection helpers -------------------------------------------------
function hasClass(node, name) {
  return !!(node && node.classList && node.classList.contains(name)) ||
    String(node && node.className || '').split(/\s+/).includes(name);
}
function findAll(root, name) { const found = []; for (const node of generate(root)) if (hasClass(node, name)) found.push(node); return found; }
function findFirst(root, name) { return findAll(root, name)[0] || null; }
function textOf(node) { let text = ''; for (const item of generate(node)) text += String(item.textContent || ''); return text; }
function buttonsIn(node) { return [...generate(node)].filter(item => item.tagName === 'BUTTON'); }
function questionCards(root) { return [...generate(root)].filter(item => item.dataset && item.dataset.questionCard !== undefined); }
// The fake document keeps every id-addressed element separate, so a pane's
// text is the union of the content hosts the app renders into.
const PANE_HOSTS = {plan: ['#brief-current', '#plan-full'], changes: ['#changes-content'],
                    execution: ['#execution_view', '#actions'], preview: ['#preview-empty-note', '#preview-content']};
function paneText(key) { return (PANE_HOSTS[key] || ['#' + key]).map(id => textOf(pane(id))).join(' '); }
const pane = id => document.querySelector(id);
const tick = () => new Promise(resolve => setTimeout(resolve, 0));

// The pane switch strip the shipped page renders inside the right context pane.
function paneNavMarkup() {
  const at = page.indexOf('pane-switches');
  if (at < 0) return '';
  return page.slice(at, page.indexOf('</nav>', at));
}

// A saved run whose saved records carry every Work-pane fact AC10 names.
// The monitor block mirrors the real backend projection
// (dashboard_monitor.snapshot with detailed=True, the read behind /api/run):
// roles always reflect settings.roles, while actual executions live only in
// stage_history[].execution and active_execution.
function savedWorkRun() {
  return {workspace: AUTOCODE, run: AUTOCODE + '/.autocode/runs/login-timeout', task: 'Fix login timeout',
          display_title: 'Fix login timeout',
          status: 'PAUSED_BUDGET', stop_reason: 'Iteration budget exhausted before verification',
          stage: 'terra', iteration: 3, active_stage: {},
          questions: [], answers: {}, user_request: null,
          criteria: [{id: 'AC1', criterion: 'Login completes within the timeout'},
                     {id: 'AC2', criterion: 'Session refresh keeps the user signed in'},
                     {id: 'AC3', criterion: 'Timeout errors are logged with a request id'}],
          counts: {pass: 2, fail: 0, unknown: 1},
          validation: {criterion_results: [{id: 'AC1', status: 'pass'}, {id: 'AC2', status: 'pass'}]},
          monitor: {live: {state: 'none'},
                    objective: 'Re-run the login timeout test after raising the budget',
                    roles: {terra: {model: 'gpt-5.6-terra', reasoning_effort: 'medium'},
                            sol: {model: 'gpt-5.6-sol', reasoning_effort: 'high'}},
                    active_execution: null,
                    stage_history: [
                      {stage: 'terra', role: 'terra', route_role: 'terra',
                       finished_at: '2026-09-30T11:30:00Z', exit_code: 0,
                       execution: {kind: 'model', model: 'gpt-5.6-terra', reasoning_effort: 'medium'}},
                      {stage: 'sol', role: 'sol', route_role: 'sol',
                       finished_at: '2026-09-30T11:45:00Z', exit_code: 0,
                       execution: {kind: 'model', model: 'gpt-5.6-sol', reasoning_effort: 'high'}}]},
          model_settings: {roles: {terra: 'gpt-5.6-terra', sol: 'gpt-5.6-sol'},
                           role_efforts: {terra: 'medium', sol: 'high'}},
          stages: [{stage: 'terra', role: 'terra', finished_at: '2026-09-30T11:30:00Z', exit_code: 0},
                   {stage: 'sol', role: 'sol', finished_at: '2026-09-30T11:45:00Z', exit_code: 0}],
          progress_messages: [], chat_messages: [], plan: [], astra_plan: null, goal: null,
          interventions: {entries: []},
          created_at: '2026-09-30T10:00:00Z', updated_at: '2026-09-30T12:00:00Z'};
}

// The same saved run, with a transcript to keep visible across pane switches.
function transcriptRun() {
  const run = savedWorkRun();
  run.progress_messages = [{role: 'assistant', speaker: 'Builder', text: 'The login timeout reproduces on staging.',
                            created_at: '2026-09-30T11:00:00Z', status: 'received'}];
  return run;
}

// A saved run whose worker is verified alive, so the conversation view shows
// its run controls (pause first, resume/continue by saved state).
function runningWorkRun() {
  const run = savedWorkRun();
  run.status = 'RUNNING';
  run.stop_reason = '';
  run.active_stage = {stage: 'terra', started_at: '2026-09-30T11:50:00Z'};
  run.monitor = {live: {state: 'alive'}, objective: 'Raise the login timeout budget',
                 active_execution: {kind: 'model', model: 'gpt-5.6-terra', reasoning_effort: 'medium'},
                 stage_history: run.monitor.stage_history};
  return run;
}

// A saved transcript long enough that a middle message can be scrolled to.
function taskTranscriptMessages(count = 8) {
  const messages = [];
  for (let index = 1; index <= count; index++) messages.push({role: 'assistant', speaker: 'Builder',
                                                               text: 'Task transcript message ' + index,
                                                               created_at: '2026-09-30T11:00:' + String(index).padStart(2, '0') + 'Z',
                                                               status: 'received'});
  return messages;
}

// A project-scoped saved conversation with a scrollable history.
function scopedConversationDoc() {
  const messages = [];
  for (let index = 1; index <= 8; index++) messages.push({id: 'dm' + index, role: index % 2 ? 'assistant' : 'user',
                                                          speaker: index % 2 ? 'Requirements planner' : 'You',
                                                          text: 'Scoped draft message ' + index,
                                                          created_at: '2026-09-30T09:00:' + String(index).padStart(2, '0') + 'Z',
                                                          status: 'saved'});
  return {id: 'c-scoped', title: 'Scoped refresh survival', status: 'ready', error: null, attachment: null,
          project_workspace: AUTOCODE, messages, updated_at: '2026-09-30T10:00:00Z'};
}

// The message currently at the top of a transcript viewport.
function topmostMessageKey(scroller) {
  const messages = [...scroller.querySelectorAll('[data-message-key]')];
  if (!messages.length) return '';
  let top = messages[0];
  for (const element of messages) if ((element.offsetTop || 0) <= (scroller.scrollTop || 0) + 2) top = element;
  return top.dataset.messageKey || '';
}

// The revision-bound approve control, wherever the shipped page hosts it. The
// guard cases must pass on the original code too, so they scan the hosts both
// layouts use instead of assuming one placement.
function approvalControl() {
  for (const id of ['#inline-task-action', '#brief-current', '#conversation']) {
    const control = buttonsIn(pane(id)).find(candidate => String(candidate.textContent || '').startsWith('Approve plan revision'));
    if (control) return control;
  }
  return null;
}

function baseData(runs) {
  return {workspaces: [AUTOCODE], workspace_ids: {}, runs,
          conversations: [], removed_projects: [], archived_tasks: [], archived_conversations: [], registry: {}};
}

async function openRun(run) {
  serverState.data = baseData([run]);
  app.setData(serverState.data);
  app.openRun(run);
  await app.refresh();
}

// ---- acceptance cases (AC10-AC13 and the AC25 guard) ----------------------------
const CASES = {
  async ac10() {
    const run = savedWorkRun();
    await openRun(run);
    // The shipped page exposes pane switches for Work, Plan, Preview, Changes
    // and Checks inside the right context pane; chat is no longer a tab.
    const nav = paneNavMarkup();
    assert.ok(nav, 'the page renders a pane switch strip inside the context pane');
    for (const [key, label] of [['now', 'Work'], ['plan', 'Plan'], ['preview', 'Preview'],
                                ['changes', 'Changes'], ['execution', 'Checks']]) {
      assert.ok(nav.includes('data-tab="' + key + '"'), 'the ' + label + ' pane switch exists');
      assert.ok(nav.includes('>' + label + '<'), 'the switch is labeled ' + label);
    }
    assert.ok(!page.includes('data-tab="interview"'), 'chat is always visible, not a tab that hides it');
    // The right pane defaults to Work when the conversation opens.
    assert.equal('now', app.state().tab, 'Work is the default pane on open');
    assert.equal(false, pane('#now').hidden, 'the Work pane is visible by default');
    assert.equal(true, pane('#plan').hidden, 'the Plan pane is not the default');
    assert.equal(true, pane('#execution').hidden, 'the Checks pane is not the default');
    assert.equal(false, pane('#interview').hidden, 'the chat transcript stays visible beside the Work pane');
    const work = textOf(pane('#now'));
    // Every displayed value comes from the saved run record.
    assert.ok(work.includes('Fix login timeout'), 'the Work pane shows the saved current task');
    assert.ok(work.includes('gpt-5.6-terra'), 'the Work pane shows the saved Builder model attribution');
    assert.ok(work.includes('gpt-5.6-sol'), 'the Work pane shows the saved Validator model attribution');
    assert.ok(work.includes('Iteration budget exhausted before verification'),
      'the Work pane shows the saved blocker note');
    assert.ok(work.includes('Re-run the login timeout test after raising the budget'),
      'the Work pane shows the saved next step');
    assert.ok(work.includes('Login completes within the timeout'), 'checklist rows name the saved requirements');
  },
  async ac11() {
    const run = transcriptRun();
    await openRun(run);
    const input = pane('#change-text');
    assert.ok(!pane('#live-controls').hidden, 'the composer is part of the conversation view');
    input.value = 'hold on';
    assert.equal('function', typeof input.oninput, 'the composer persists its draft');
    input.oninput();
    const transcript = textOf(pane('#conversation'));
    assert.ok(transcript.includes('The login timeout reproduces on staging.'),
      'the saved transcript rendered before switching panes');
    for (const key of ['now', 'plan', 'preview', 'changes', 'execution']) {
      app.activateTab(key);
      await tick();
      assert.ok(!pane('#interview').hidden, 'the chat transcript stays visible in the ' + key + ' pane');
      assert.ok(!pane('#live-controls').hidden, 'the composer stays visible in the ' + key + ' pane');
      assert.equal(transcript, textOf(pane('#conversation')), 'the transcript is unchanged in the ' + key + ' pane');
      assert.equal('hold on', input.value, 'the draft text survives switching to the ' + key + ' pane');
    }
  },
  async ac12() {
    const run = savedWorkRun();
    await openRun(run);
    app.activateTab('now');
    await tick();
    const work = pane('#now');
    const counts = findFirst(work, 'work-checklist-counts');
    assert.ok(counts, 'the Work pane renders its checklist counts from the saved record');
    assert.ok(textOf(counts).includes('2 checked'), 'the saved checklist shows exactly 2 checked');
    assert.ok(textOf(counts).includes('1 unchecked'), 'the saved checklist shows exactly 1 unchecked');
    const rowStates = findAll(work, 'work-check-row').map(row => String(row.className || '').split(/\s+/));
    assert.equal(2, rowStates.filter(classes => classes.includes('checked')).length, 'two requirement rows render as checked');
    assert.equal(1, rowStates.filter(classes => classes.includes('unchecked')).length, 'one requirement row renders as unchecked');
    assert.equal(0, rowStates.filter(classes => classes.includes('failed')).length, 'no requirement row is failed in the saved record');
    const text = textOf(work);
    assert.ok(!text.includes('%'), 'no percentage is displayed for a record that stores none');
    assert.ok(!/\b\d+\s*percent\b/i.test(text), 'no percentage wording is displayed either');
  },
  async ac13() {
    const other = {workspace: AUTOCODE, run: AUTOCODE + '/.autocode/runs/other-artifacts',
                   task: 'Artifact rich task', status: 'TASK_COMPLETE',
                   criteria: [{id: 'C1', criterion: 'Other conversation criterion'}],
                   validation: {source_revision: 'deadbeef', criterion_results: [{id: 'C1', status: 'pass'}]},
                   goal: {revision: 4, approval_status: 'approved', body: {intended_outcome: 'Other outcome'}},
                   stages: [{stage: 'terra', finished_at: '2026-09-30T10:30:00Z', exit_code: 0}],
                   counts: {pass: 1, fail: 0, unknown: 0}, questions: [], answers: {},
                   monitor: {live: {state: 'none'}}, interventions: {entries: []},
                   created_at: '2026-09-30T10:00:00Z', updated_at: '2026-09-30T10:30:00Z'};
    const empty = {workspace: AUTOCODE, run: AUTOCODE + '/.autocode/runs/quiet-corner',
                   task: 'Quiet corner', status: 'PAUSED_INTERVENTION', active_stage: {},
                   criteria: [], validation: null, goal: null, stages: [], counts: {pass: 0, fail: 0, unknown: 0},
                   questions: [], answers: {}, monitor: {live: {state: 'none'}}, interventions: {entries: []},
                   model_settings: {}, created_at: '2026-09-30T10:00:00Z', updated_at: '2026-09-30T10:05:00Z'};
    serverState.evidence[other.run] = {stages: [{index: 0, stage: 'terra', finished_at: '2026-09-30T10:30:00Z',
                                                 changed_files: ['other_file.py']}]};
    serverState.evidence[empty.run] = {stages: []};
    serverState.data = baseData([empty, other]);
    app.setData(serverState.data);
    app.openRun(empty);
    await app.refresh();
    const panes = {plan: pane('#plan'), changes: pane('#changes'), execution: pane('#execution'), preview: pane('#preview')};
    for (const [key, host] of Object.entries(panes)) {
      app.activateTab(key);
      await tick();
      await tick();
      assert.equal(false, host.hidden, 'the ' + key + ' pane opened');
      const text = paneText(key);
      assert.ok(text.includes('Quiet corner'), 'the ' + key + ' empty state names this conversation');
      assert.ok(/no saved|not been saved|nothing|yet/i.test(text), 'the ' + key + ' pane states its emptiness truthfully');
      assert.ok(!text.includes('Artifact rich task'), 'the ' + key + ' pane shows no other conversation title');
      assert.ok(!text.includes('other-artifacts'), 'the ' + key + ' pane links no other conversation run');
      assert.ok(!text.includes('Other conversation criterion'), 'the ' + key + ' pane shows no other conversation criteria');
    }
  },
  // A run whose only model facts are configured future routes: no executed
  // stage and no active execution. The monitor block keeps the real backend
  // projection shape (dashboard_monitor.snapshot projects settings.roles into
  // monitor.roles even with zero recorded stages), so the configured Builder
  // route is present in the record and must still never surface as an actual
  // role/model attribution in the Work pane.
  async ac10_configured() {
    const configured = savedWorkRun();
    configured.monitor = {live: {state: 'none'},
                          objective: 'Re-run the login timeout test after raising the budget',
                          roles: {terra: {model: 'configured-but-never-run', reasoning_effort: 'high'},
                                  sol: {model: 'configured-sol-route', reasoning_effort: 'low'}},
                          active_execution: null, stage_history: []};
    configured.model_settings = {roles: {terra: 'configured-but-never-run', sol: 'configured-sol-route'},
                                 role_efforts: {terra: 'high', sol: 'low'}};
    configured.stages = [];
    await openRun(configured);
    app.activateTab('now');
    await tick();
    const roles = findFirst(pane('#now'), 'work-roles');
    assert.ok(roles, 'the Work pane renders its role attribution summary');
    const text = textOf(roles);
    assert.ok(!text.includes('configured-but-never-run'),
      'a configured-only Builder route is not listed as an actual role/model attribution');
    assert.ok(!text.includes('configured-sol-route'),
      'a configured-only Validator route is not listed as an actual role/model attribution');
    assert.ok(!/Builder ·|Validator ·/.test(text),
      'no attribution line is fabricated for a role that never ran');
    // Configured future routes stay inspectable, separately labeled, never as
    // actual stage attributions.
    const stages = textOf(findFirst(pane('#now'), 'workflow-models-panel'));
    assert.ok(stages.includes('Configured: '), 'configured routes remain labeled as configured');
    assert.ok(stages.includes('configured-but-never-run'), 'the configured Builder route stays inspectable');
    assert.ok(stages.includes('Not started'), 'the configured route is marked not started');
    // The saved executed stage/model/effort facts still render when they exist.
    await openRun(savedWorkRun());
    app.activateTab('now');
    await tick();
    const executed = textOf(findFirst(pane('#now'), 'work-roles'));
    assert.ok(executed.includes('gpt-5.6-terra'), 'the saved executed Builder attribution still renders');
    assert.ok(executed.includes('gpt-5.6-sol'), 'the saved executed Validator attribution still renders');
    assert.ok(executed.includes('medium reasoning'), 'the saved executed reasoning effort still renders');
  },
  // The same negative case, driven by the REAL dashboard_monitor.snapshot
  // projection supplied by the Python test (PANES_MONITOR_JSON): the
  // projection genuinely carries settings.roles with zero recorded stages.
  async ac10_projection_configured() {
    const projection = JSON.parse(process.env.PANES_MONITOR_JSON || 'null');
    assert.ok(projection && projection.roles, 'this case runs with the real monitor projection injected');
    assert.ok(projection.roles.terra && projection.roles.terra.model,
      'the injected real projection carries the configured terra route');
    const run = savedWorkRun();
    run.monitor = projection;
    run.stages = [];
    await openRun(run);
    app.activateTab('now');
    await tick();
    const roles = findFirst(pane('#now'), 'work-roles');
    assert.ok(roles, 'the Work pane renders its role attribution summary');
    const text = textOf(roles);
    assert.ok(!text.includes(String(projection.roles.terra.model)),
      'a settings-derived route from the real projection is not an actual role/model attribution: ' + text);
    assert.ok(!text.includes(String(projection.roles.sol?.model || '')),
      'a settings-derived Validator route from the real projection is not an actual attribution');
    assert.ok(!/Builder ·|Validator ·|Planner/.test(text),
      'no attribution line is fabricated for a role that never ran');
    assert.ok(/No saved role and model attributions/.test(text),
      'with zero recorded executions the summary states that truthfully');
    const configuredView = textOf(findFirst(pane('#now'), 'workflow-models-panel'));
    assert.ok(configuredView.includes('Configured: '), 'configured routes remain labeled as configured');
    assert.ok(configuredView.includes(String(projection.roles.terra.model)),
      'the configured route stays inspectable in the separately labeled configured view');
    assert.ok(configuredView.includes('Not started'), 'the configured route is marked not started');
  },
  // Recorded launches from the real projection render as the actual
  // attributions: a finished stage launch and the active execution. The
  // configured future routes differ from the recorded models, so a
  // settings-derived line can never satisfy these assertions.
  async ac10_projection_executed() {
    const projection = JSON.parse(process.env.PANES_MONITOR_JSON || 'null');
    assert.ok(projection && projection.stage_history?.length && projection.active_execution,
      'this case runs with the real monitor projection injected');
    const run = savedWorkRun();
    run.monitor = projection;
    run.stages = [];  // raw stage rows carry no execution facts of their own
    await openRun(run);
    app.activateTab('now');
    await tick();
    const text = textOf(findFirst(pane('#now'), 'work-roles'));
    assert.ok(text.includes('Builder · gpt-5.6-terra · medium reasoning'),
      'the recorded Builder stage launch renders as the actual attribution: ' + text);
    assert.ok(text.includes('Validator · gpt-5.6-sol · high reasoning'),
      'the recorded active Validator launch renders as the actual attribution: ' + text);
    assert.ok(!text.includes('configured-future-terra'),
      'the settings-derived future Builder route is never presented as an execution');
    assert.ok(!text.includes('configured-future-sol'),
      'the settings-derived future Validator route is never presented as an execution');
    const configuredView = textOf(findFirst(pane('#now'), 'workflow-models-panel'));
    assert.ok(configuredView.includes('Configured: ') && configuredView.includes('configured-future-terra'),
      'the differing configured routes stay visible only in the configured view');
  },
  // Human approvals are actionable only in the chat transcript: Plan and
  // Checks show read-only saved status plus a focus path, while the
  // revision/token-bound approval controls live in the chat column.
  async pane_exclusivity() {
    const planRun = Object.assign(savedWorkRun(), {
      status: 'AWAITING_GOAL_APPROVAL', goal_token: 'r7:plan-token',
      human_request_authorized: true,
      human_escalation: {request_id: 'plan-req', request_token: 'plan-tok', scope: 'goal_approval', request: {criteria: []}},
      questions: [], stages: [], active_stage: {},
      monitor: {live: {state: 'none'}, active_execution: null, stage_history: []},
      goal: {revision: 7, approval_status: 'draft', origin: 'astra_finalize',
             body: {intended_outcome: 'Fix the login timeout', requirements: ['Keep sessions signed in'],
                    constraints: [], implementation_sequence: ['Raise the budget', 'Re-run the test'],
                    acceptance_criteria: [{id: 'AC1', criterion: 'Login completes within the timeout'}],
                    accepted_assumptions: []}}});
    await openRun(planRun);
    app.activateTab('plan');
    await tick();
    const planButtons = buttonsIn(pane('#brief-current'));
    assert.ok(planButtons.every(control => !/approve/i.test(String(control.textContent || ''))),
      'the Plan pane carries no approval control');
    const planText = textOf(pane('#brief-current'));
    assert.ok(/ready for your review/i.test(planText), 'the Plan pane shows read-only saved approval status');
    const planFocus = planButtons.filter(control => /conversation/i.test(String(control.textContent || '')));
    assert.ok(planFocus.length === 1, 'the Plan pane offers exactly one focus path to the chat action');
    assert.equal('function', typeof planFocus[0].onclick, 'the focus path navigates instead of submitting');
    const chat = pane('#inline-task-action');
    assert.equal(false, chat.hidden, 'the chat inline action area is visible');
    const chatText = textOf(chat);
    assert.ok(chatText.includes('Approve plan revision 7'),
      'the revision-bound approve control is in the chat transcript');
    assert.ok(chatText.includes('Approve & build revision 7'),
      'the combined approve-and-build control stays in the chat transcript');
    const approve = buttonsIn(chat).find(control => String(control.textContent || '').startsWith('Approve plan revision'));
    assert.ok(approve, 'the chat approve-only control renders');
    assert.equal(false, approve.disabled, 'the chat approve control is actionable');
    await approve.onclick();
    const actions = fetchLog.filter(entry => entry.url === '/api/action').map(entry => JSON.parse(entry.options.body || '{}'));
    const approval = actions.find(body => body.action === 'approve_goal');
    assert.ok(approval, 'submitting the chat approval posted the approve_goal action');
    assert.equal('r7:plan-token', approval.token, 'the approval binds to the saved revision token');
    assert.equal('r7:plan-token', approval.confirmation, 'the confirmation repeats the saved token');
    assert.equal('plan-req', approval.resolver_request, 'the approval stays bound to its saved escalation');
    assert.equal('plan-tok', approval.resolver_token, 'the approval carries the escalation token');

    const reviewRun = Object.assign(savedWorkRun(), {
      status: 'WAITING_FOR_USER', human_request_authorized: true,
      human_escalation: {request_id: 'rev-req', request_token: 'rev-tok', scope: 'human_review', request: {criteria: ['C1']}},
      review_token: 'review-token-1', human_reviews: {},
      review_criteria: [{id: 'C1', criterion: 'Login completes within the timeout'}],
      questions: [], stages: [], active_stage: {}, goal: null,
      monitor: {live: {state: 'none'}, active_execution: null, stage_history: []}});
    await openRun(reviewRun);
    app.activateTab('execution');
    await tick();
    const checksButtons = buttonsIn(pane('#execution_view'));
    assert.ok(checksButtons.every(control => !/approve/i.test(String(control.textContent || ''))),
      'the Checks pane carries no approval control');
    assert.ok(textOf(pane('#execution_view')).includes('Login completes within the timeout'),
      'the Checks pane still shows the saved review request read-only');
    assert.ok(checksButtons.some(control => /conversation/i.test(String(control.textContent || ''))),
      'the Checks pane offers a focus path to the chat action');
    const reviewApprove = buttonsIn(pane('#inline-task-action'))
      .find(control => String(control.textContent || '') === 'Approve C1');
    assert.ok(reviewApprove, 'the criterion approval control is in the chat transcript only');
    assert.equal(false, reviewApprove.disabled, 'the chat review control is actionable');
    await reviewApprove.onclick();
    const reviewActions = fetchLog.filter(entry => entry.url === '/api/action').map(entry => JSON.parse(entry.options.body || '{}'));
    const review = reviewActions.find(body => body.action === 'approve_review');
    assert.ok(review, 'submitting the chat review posted the approve_review action');
    assert.equal('C1', review.id, 'the review binds to the saved criterion');
    assert.equal('review-token-1', review.token, 'the review binds to the saved review token');
    assert.equal('rev-req', review.resolver_request, 'the review stays bound to its saved escalation');
    assert.equal('rev-tok', review.resolver_token, 'the review carries the escalation token');
    // The AC25 permission question card keeps rendering in the chat transcript.
    const questionRun = Object.assign(savedWorkRun(), {
      status: 'WAITING_FOR_USER', human_request_authorized: true,
      human_escalation: {request_id: 'perm-req', request_token: 'perm-tok', scope: 'permission',
                         request: {criteria: []}, questions: [{id: 'q9', question: 'May AutoCode write to the sessions table?'}]},
      questions: [{id: 'q9', question: 'May AutoCode write to the sessions table?', why: 'The migration changes a shared table.',
                   proposed_default: 'Yes, during this step only'}],
      answers: {}, stages: [], active_stage: {}, goal: null});
    await openRun(questionRun);
    const cards = questionCards(pane('#conversation'));
    assert.equal(1, cards.length, 'the unanswered permission question still renders in the transcript');
    assert.ok(buttonsIn(cards[0]).length >= 1, 'its answer control stays in the transcript');
  },
  async ac25() {
    const question = 'May AutoCode write to the sessions table during this step?';
    const run = {workspace: AUTOCODE, run: AUTOCODE + '/.autocode/runs/session-permission',
                 task: 'Session migration permission', status: 'WAITING_FOR_USER',
                 human_request_authorized: true,
                 human_escalation: {request_id: 'env-perm', request_token: 'token-perm', scope: 'permission',
                                    questions: [{id: 'q9', question}]},
                 questions: [{id: 'q9', question, why: 'The migration changes a shared table.',
                              proposed_default: 'Yes, during this step only'}],
                 answers: {}, user_request: null, criteria: [], validation: null, goal: null, stages: [],
                 counts: {pass: 0, fail: 0, unknown: 0}, active_stage: {},
                 monitor: {live: {state: 'none'}}, interventions: {entries: []}, model_settings: {},
                 created_at: '2026-09-30T10:00:00Z', updated_at: '2026-09-30T10:05:00Z'};
    await openRun(run);
    const chat = pane('#interview');
    assert.equal(false, chat.hidden, 'the chat transcript is visible');
    const transcript = pane('#conversation');
    const cards = questionCards(transcript);
    assert.equal(1, cards.length, 'the unanswered permission question renders as a question card in the transcript');
    const card = cards[0];
    assert.equal(false, card.hidden, 'the permission question card is visible');
    assert.ok(textOf(card).includes(question), 'the permission question text is shown in the transcript');
    const controls = buttonsIn(card);
    assert.ok(controls.length >= 1, 'the question card carries its answer control in the transcript');
    assert.ok(textOf(controls[0]).includes('Accept suggested answer'),
      'the answer control remains the in-chat suggested-answer action');
    assert.ok(!pane('#live-controls').hidden, 'the chat composer answer control stays visible');
    assert.ok(String(pane('#change-text').placeholder).includes('Answer'),
      'the composer remains the transcript answer surface');
  },
  // AC14: selection, unsent draft, scroll anchor and project scope survive an
  // ordinary reload on BOTH transcript surfaces — the intake scroller
  // #draft-scroll and the task-linked scroller #interview.
  async ac14() {
    // Surface 1: the intake conversation (#draft-scroll).
    const doc = scopedConversationDoc();
    serverState.data = baseData([]);
    serverState.data.conversations = [doc];
    app.setData(serverState.data);
    app.openConversation(doc);
    await app.refresh();
    const draft = pane('#draft-text');
    draft.value = 'please continue';
    assert.equal('function', typeof draft.oninput, 'the intake composer persists its draft');
    draft.oninput({target: draft});
    const scroller = pane('#draft-scroll');
    const messages = [...scroller.querySelectorAll('[data-message-key]')];
    assert.ok(messages.length >= 6, 'the saved intake transcript is long enough to scroll away from its ends');
    const middle = messages[4];
    scroller.scrollTop = middle.offsetTop;
    assert.equal(middle.dataset.messageKey, topmostMessageKey(scroller),
      'the intake transcript is scrolled to a middle message before the reload');
    assert.equal(middle.dataset.messageKey, storage.get('autocode:conversation-scroll:' + doc.id),
      'the per-conversation scroll anchor is saved for the intake surface');
    const draftAnchor = middle.dataset.messageKey;
    reloadApp();
    await app.refresh();
    assert.equal(doc.id, app.state().activeConversation, 'the same conversation is selected after the reload');
    assert.equal('please continue', pane('#draft-text').value, 'the unsent draft is restored after the reload');
    const reloadedScroller = pane('#draft-scroll');
    assert.equal(draftAnchor, topmostMessageKey(reloadedScroller),
      'the intake transcript returns to the same middle message after the reload');
    assert.ok(reloadedScroller.scrollTop < reloadedScroller.scrollHeight - 1,
      'the reloaded intake transcript is not forced to its end');
    assert.equal('New conversation in AutoCode', pane('#new-task-scope-label').textContent,
      'the selected project scope is restored after the reload');

    // Surface 2: the task-linked conversation (#interview).
    const run = transcriptRun();
    run.progress_messages = taskTranscriptMessages(8);
    serverState.data = baseData([run]);
    serverState.data.conversations = [doc];
    app.openRun(run);
    await app.refresh();
    const composer = pane('#change-text');
    composer.value = 'please continue';
    assert.equal('function', typeof composer.oninput, 'the task composer persists its draft');
    composer.oninput();
    const thread = pane('#interview');
    const threadMessages = [...thread.querySelectorAll('[data-message-key]')];
    assert.ok(threadMessages.length >= 6, 'the saved task transcript is long enough to scroll away from its ends');
    const threadMiddle = threadMessages[4];
    thread.scrollTop = threadMiddle.offsetTop;
    assert.equal(threadMiddle.dataset.messageKey, topmostMessageKey(thread),
      'the task transcript is scrolled to a middle message before the reload');
    assert.equal(threadMiddle.dataset.messageKey, storage.get('autocode:task-scroll:' + run.run),
      'the per-run scroll anchor is saved for the task-linked surface');
    const threadAnchor = threadMiddle.dataset.messageKey;
    reloadApp();
    await app.refresh();
    const selected = app.state().chosen;
    assert.ok(selected && selected.run === run.run, 'the same task-linked conversation is selected after the reload');
    assert.equal('please continue', pane('#change-text').value, 'the task draft is restored after the reload');
    const reloadedThread = pane('#interview');
    assert.equal(threadAnchor, topmostMessageKey(reloadedThread),
      'the task-linked transcript returns to the same middle message after the reload');
    assert.ok(reloadedThread.scrollTop < reloadedThread.scrollHeight - 1,
      'the reloaded task transcript is not forced to its end');
  },
  // Non-credited composer-adjacency case (the credited AC15 proof lives in
  // tests/test_autocode_stop.py against the real runner and dashboard
  // adapter): the run controls and the model-route controls sit beside the
  // chat composer inside the conversation view, and Stop is a real, separate
  // control next to Pause. The placement ancestry is markup the page ships,
  // so the ancestry proof lives in test_workspace_panes_ui.py; this case
  // proves the relocated controls render and stay actionable for a running
  // conversation.
  async composer_run_controls() {
    const run = runningWorkRun();
    run.interventions = {...run.interventions, stop_capable: true};
    await openRun(run);
    const section = pane('#live-controls');
    assert.equal(false, section.hidden, 'the composer section is part of the conversation view');
    const pause = pane('#pause-run');
    assert.equal(false, pause.hidden, 'the pause control shows for a running conversation');
    assert.equal('Pause after current step', pause.textContent,
      'the beside-composer pause control keeps its after-current-step label');
    assert.equal(false, pause.disabled, 'the beside-composer pause control is actionable');
    const stop = pane('#stop-run');
    assert.equal(false, stop.hidden, 'the stop control shows for a running conversation');
    assert.equal('Stop', stop.textContent, 'the beside-composer stop control is labeled Stop');
    assert.ok(stop !== pause, 'Stop is a separate control, not a relabeled Pause');
    assert.equal(false, stop.disabled, 'the beside-composer stop control is actionable');
    const resume = pane('#continue-run');
    assert.ok(resume, 'the Continue/Resume control is present in the beside-composer run controls while the conversation runs');
    assert.equal(true, resume.hidden,
      'while the conversation runs the primary slot shows Pause and Stop; Continue waits hidden beside them, never removed');
    await stop.onclick();
    const stopActions = fetchLog.filter(entry => entry.url === '/api/action').map(entry => JSON.parse(entry.options.body || '{}'));
    assert.ok(stopActions.some(body => body.action === 'stop'),
      'activating the stop control posts the durable stop request');
    assert.ok(!stopActions.some(body => ['terminate', 'kill'].includes(body.action)),
      'the stop control posts no kill or terminate fallback');
    // The model-route entry must be visibly usable beside the composer, not
    // merely populated markup: its hosts stay unhidden in the running view,
    // the shipped page declares it as an openable <details> disclosure with a
    // summary toggle (not shipped hidden), and the controls it hosts render
    // their true running state. The synthetic VM cannot compute stylesheet
    // visibility or open a real <details>; the credited AC15 case adds a
    // browser-level check for exactly that.
    const modelsHost = pane('#composer-models');
    assert.ok(modelsHost, 'the beside-composer model-route entry exists');
    assert.equal(false, modelsHost.hidden,
      'the model-route entry host is not hidden beside the composer while the conversation runs');
    const modelsTagAt = page.indexOf('<details id="composer-models"');
    assert.ok(modelsTagAt > 0, 'the shipped page renders the model entry as a details disclosure');
    const modelsTag = page.slice(modelsTagAt, page.indexOf('>', modelsTagAt) + 1);
    assert.ok(!/\bhidden\b/.test(modelsTag), 'the model entry is not shipped hidden');
    const summaryAt = page.indexOf('<summary', modelsTagAt);
    assert.ok(summaryAt > 0 && summaryAt < page.indexOf('</details>', modelsTagAt),
      'the model entry ships its summary toggle inside the disclosure');
    const models = pane('#task-model-settings');
    assert.equal(false, models.hidden, 'the model settings panel inside the entry is not hidden');
    assert.ok(models.children.length, 'the beside-composer model controls render');
    assert.ok(textOf(models).includes('gpt-5.6-terra'),
      'the beside-composer model controls read the saved model configuration');
    assert.equal(true, pane('#save-task-reasoning').disabled,
      'while a step is active the reasoning overrides wait for the step boundary (real running-state gating, not a decorative form)');
    assert.ok(String(pane('#task-reasoning-status').textContent).includes('current step'),
      'the model entry explains its running-state gating');
    const composer = pane('#change-text');
    assert.equal(false, composer.hidden, 'the chat composer stays rendered in the same conversation view');
    // A paused conversation (a pause stands, no stop) keeps the resume control
    // itself beside the composer: visible and actionable.
    run.status = 'PAUSED_INTERVENTION';
    run.interventions = {...run.interventions, pause_intent: {request_ids: ['p1'], applied_at: '2026-10-01T09:00:00Z', acknowledged_at: null}, entries: []};
    await app.refresh();
    assert.equal(false, resume.hidden, 'a paused conversation shows its Continue/Resume control beside the composer');
    assert.equal(false, resume.disabled, 'the Continue/Resume control is actionable for a paused conversation');
    assert.ok(/resume|start building/i.test(resume.textContent),
      'the paused control offers resume semantics: ' + resume.textContent);
    assert.equal(true, pane('#pause-run').hidden, 'the pause control itself hides once the pause is applied');
    // While an applied stop stands, Continue/Resume stay suppressed; Stop is
    // reported as applied and never offers a resume.
    run.interventions = {...run.interventions, stop_intent: {request_ids: ['stop-1'], applied_at: '2026-10-01T10:00:00Z'}, entries: []};
    run.status = 'PAUSED_INTERVENTION';
    run.stop_reason = 'Durable stop applied at a saved boundary; this run launches no further stages.';
    await app.refresh();
    assert.equal('Stopped', pane('#stop-run').textContent, 'the control reports the applied stop');
    assert.equal(true, pane('#stop-run').disabled, 'the stop control is disabled once applied');
    const stoppedResume = pane('#continue-run');
    assert.ok(!/resume|continue|start building|finish task/i.test(stoppedResume.textContent),
      'no working Continue, Resume, Start building or Finish task control is offered for a stopped run: ' + stoppedResume.textContent);
    assert.ok(textOf(pane('#change-history')).includes('Stopped after the current step finished and saved.'),
      'the live region announces the applied durable stop');
  },
  // AC16 guard: the pause labels predate the relayout and must survive it.
  async ac16() {
    const run = runningWorkRun();
    await openRun(run);
    const pause = pane('#pause-run');
    assert.equal(false, pause.hidden, 'a running conversation shows its pause control');
    assert.equal('Pause after current step', pause.textContent,
      'the control labels the pause as taking effect after the current step');
    await pause.onclick();
    const actions = fetchLog.filter(entry => entry.url === '/api/action').map(entry => JSON.parse(entry.options.body || '{}'));
    assert.ok(actions.some(body => body.action === 'pause'), 'activating the control posted the pause request');
    assert.ok(!actions.some(body => ['stop', 'terminate', 'kill'].includes(body.action)),
      'the in-flight step is not interrupted mid-step; the pause waits for the current step to finish');
    run.interventions = {pause_intent: {requested_at: '2026-09-30T11:51:00Z'}, entries: []};
    await app.refresh();
    assert.equal('Pause requested', pane('#pause-run').textContent,
      'the control reports the pending pause');
    assert.ok(textOf(pane('#change-history')).includes('Pause requested. The current step will finish first.'),
      'the live region announces that the current step will finish first');
    assert.equal(true, pane('#pause-run').disabled, 'the pause control is disabled once requested');
  },
  // AC17 guard: revision/token binding and stale refusal predate the relayout.
  async ac17() {
    const run = Object.assign(savedWorkRun(), {
      status: 'AWAITING_GOAL_APPROVAL', goal_token: 'r2:token',
      human_request_authorized: true,
      human_escalation: {request_id: 'r2-req', request_token: 'r2-rtok', scope: 'goal_approval', request: {criteria: []}},
      questions: [], stages: [], active_stage: {},
      monitor: {live: {state: 'none'}, active_execution: null, stage_history: []},
      goal: {revision: 2, approval_status: 'draft', origin: 'astra_finalize',
             body: {intended_outcome: 'Fix the login timeout', requirements: ['Keep sessions signed in'],
                    constraints: [], implementation_sequence: ['Raise the budget', 'Re-run the test'],
                    acceptance_criteria: [{id: 'AC1', criterion: 'Login completes within the timeout'}],
                    accepted_assumptions: []}}});
    const recorded = [];
    serverState.onAction = body => {
      if (body.action === 'approve_goal') {
        recorded.push({revision: run.goal.revision, token: body.token});
        // The approval lands against r2; the planner then publishes revision 3
        // under a fresh token, exactly as a requirement change would.
        run.goal_token = 'r3:token';
        run.goal = {...run.goal, revision: 3, approval_status: 'draft', approval_event: undefined};
      }
    };
    try {
      await openRun(run);
      const approve = approvalControl();
      assert.ok(approve, 'the revision-bound approval control renders for the pending plan revision');
      await approve.onclick();
      const approvals = () => fetchLog.filter(entry => entry.url === '/api/action')
        .map(entry => JSON.parse(entry.options.body || '{}')).filter(body => body.action === 'approve_goal');
      assert.equal(1, approvals().length, 'the approval was submitted exactly once');
      assert.equal('r2:token', approvals()[0].token, 'the approval binds to the pending revision token');
      assert.equal('r2:token', approvals()[0].confirmation, 'the confirmation repeats the pending revision token');
      assert.equal('r2-req', approvals()[0].resolver_request, 'the approval stays bound to its saved escalation');
      assert.equal('r2-rtok', approvals()[0].resolver_token, 'the approval carries the escalation token');
      assert.deepEqual([{revision: 2, token: 'r2:token'}], recorded,
        'the approval was recorded against exactly revision 2');
      await app.refresh();
      assert.equal(3, run.goal.revision, 'the saved record moved on to revision 3 under a fresh token');
      // Resubmitting the same captured action still carries revision 2's token.
      await approve.onclick();
      assert.equal(1, approvals().length, 'the stale submission posted no second approval');
      assert.ok(String(pane('#dashboard-notice').textContent)
        .includes('The AutoResolver request changed. Refresh and review the current request before responding.'),
        'the stale submission is refused with the stale-refusal message');
    } finally {
      serverState.onAction = null;
    }
  },
  // AC24: the active project scope survives reload and away-and-back
  // navigation: header, composer and the top new-conversation control.
  async ac24() {
    const scoped = scopedConversationDoc();
    const other = {id: 'c-other', title: 'Unscoped neighbor', status: 'ready', error: null, attachment: null,
                   messages: [], updated_at: '2026-09-30T10:30:00Z'};
    serverState.data = baseData([]);
    serverState.data.conversations = [scoped, other];
    app.setData(serverState.data);
    app.openConversation(scoped);
    await app.refresh();
    const header = () => String(pane('#draft-subtitle').textContent);
    const composer = () => String(pane('#draft-text').placeholder);
    const topControl = () => pane('#new-task-scope-label').textContent;
    assert.ok(header().includes('AutoCode'), 'the header shows the active project scope');
    assert.ok(composer().includes('AutoCode'), 'the composer shows the active project scope');
    assert.equal('New conversation in AutoCode', topControl(),
      'the top new-conversation control reads the active project scope');
    reloadApp();
    await app.refresh();
    assert.equal(scoped.id, app.state().activeConversation, 'the same conversation is selected after the reload');
    assert.ok(header().includes('AutoCode'), 'the header still shows AutoCode after the reload');
    assert.ok(composer().includes('AutoCode'), 'the composer still shows AutoCode after the reload');
    assert.equal('New conversation in AutoCode', topControl(),
      'the top new-conversation control still reads New conversation in AutoCode after the reload');
    app.openConversation(other);
    await app.refresh();
    app.openConversation(scoped);
    await app.refresh();
    assert.equal(scoped.id, app.state().activeConversation, 'returning reselects the AutoCode conversation');
    assert.ok(header().includes('AutoCode'), 'the header still shows AutoCode after away-and-back navigation');
    assert.ok(composer().includes('AutoCode'), 'the composer still shows AutoCode after away-and-back navigation');
    assert.equal('New conversation in AutoCode', topControl(),
      'the top new-conversation control still reads New conversation in AutoCode after away-and-back navigation');
  },
};

const name = process.argv[2];
if (!name || !CASES[name]) {
  console.error('usage: node workspace_panes_harness.js <' + Object.keys(CASES).join('|') + '>');
  process.exit(2);
}
Promise.resolve().then(() => CASES[name]()).then(
  () => { console.log('PASS ' + name); process.exit(0); },
  error => { console.error('FAIL ' + name + ': ' + (error && error.stack || error)); process.exit(1); });
