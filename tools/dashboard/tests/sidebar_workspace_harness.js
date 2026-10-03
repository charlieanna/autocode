// VM harness for the project-grouped sidebar workspace (M1). Driven per case
// by tools/dashboard/tests/test_sidebar_workspace_ui.py; not a standalone gate.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('./dashboard_vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'dashboard_app.js'), 'utf8');
const page = fs.readFileSync(path.join(__dirname, '..', 'dashboard.html'), 'utf8');

const AUTOCODE = '/work/AutoCode';
const IDLECAMPUS = '/work/IdleCampus';

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
      if (match[3] === undefined) return match[1] in node.attributes;
      return node.attributes[match[1]] === match[3];
    }
    return node.tagName === item.toUpperCase();
  });
}

const cache = new Map();
const storage = new Map();
const localStorage = {
  getItem: key => (storage.has(key) ? storage.get(key) : null),
  setItem: (key, value) => { storage.set(key, String(value)); },
  removeItem: key => { storage.delete(key); },
};
const document = {
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

// ---- fake dashboard server -------------------------------------------------------
// Cases install a snapshot in serverState.data; reads serve it, creations and
// task actions mutate it, so every case drives the app's real fetch paths.
const serverState = {data: {workspaces: [], workspace_ids: {}, runs: [], conversations: [],
                            removed_projects: [], archived_tasks: [], archived_conversations: [], registry: {}},
                     onAction: null};
const fetchLog = [];
let createdCount = 0;
const fetch = async (url, options = {}) => {
  fetchLog.push({url, options});
  if (url === '/api/runs') return {ok: true, json: async () => serverState.data};
  if (url === '/api/models') return {ok: true, json: async () => ({usable: true, models: [], loading: false})};
  if (url.startsWith('/api/conversation?')) {
    const id = new URLSearchParams(url.slice('/api/conversation?'.length)).get('id');
    const doc = (serverState.data.conversations || []).find(row => row.id === id);
    if (!doc) return {ok: false, json: async () => ({error: 'Conversation unavailable'})};
    return {ok: true, json: async () => doc};
  }
  if (url.startsWith('/api/run?')) {
    const query = new URLSearchParams(url.slice('/api/run?'.length));
    const runPath = query.get('run') || '';
    const base = (serverState.data.runs || []).find(row => row.run === runPath)
      || {workspace: query.get('workspace') || '', run: runPath, task: 'Missing task'};
    return {ok: true, json: async () => JSON.parse(JSON.stringify(base))};
  }
  if (url === '/api/conversations') {
    const body = JSON.parse(options.body || '{}');
    const empty = body.empty === true && !String(body.text || '').trim();
    const doc = {id: 'created-' + (++createdCount),
                 title: empty ? 'New conversation' : String(body.text || '').split(/\s+/).join(' ').slice(0, 40),
                 status: 'ready', error: null, messages: [], attachment: null,
                 updated_at: '2026-09-30T12:00:00Z'};
    if (body.workspace) doc.project_workspace = body.workspace;
    serverState.data.conversations.push(doc);
    serverState.lastCreation = doc;
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
const context = vm.createContext({
  document,
  console,
  fetch,
  URLSearchParams,
  localStorage,
  crypto: {randomUUID: () => 'uuid-' + (++uuidCount)},
  history: {replaceState: () => {}},
  location: {hash: '#tasks'},
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

const app = vm.runInContext(`(() => {
  return {
    setData: value => { latestData = value; },
    renderTasks: value => renderTasks(value),
    renderDraft: value => renderDraftConversation(value),
    openRun: value => openRun(value),
    openConversation: value => openConversation(value),
    showNewTask: value => showNewTask(value),
    setActiveProject: value => { try { activeProject = value; } catch (error) { throw Error('activeProject is not implemented: ' + error.message); } },
    readActiveProject: () => { try { return activeProject; } catch { return undefined; } },
    refresh: () => refresh(),
    state: () => ({view: currentView, activeConversation, chosen: chosen && {workspace: chosen.workspace, run: chosen.run}}),
  };
})()`, context);

// ---- shared inspection helpers -------------------------------------------------
function hasClass(node, name) {
  return !!(node && node.classList && node.classList.contains(name)) ||
    String(node && node.className || '').split(/\s+/).includes(name);
}
function findAll(root, name) { const found = []; for (const node of generate(root)) if (hasClass(node, name)) found.push(node); return found; }
function findFirst(root, name) { return findAll(root, name)[0] || null; }
function sidebarGroups() { return findAll(document.querySelector('#projects'), 'project-group'); }
function groupToggle(group) { return findFirst(group, 'project-group-toggle'); }
function groupCreate(group) { return findFirst(group, 'project-group-new'); }
function groupRows(group) { const list = findFirst(group, 'project-group-rows'); return list ? findAll(list, 'conversation-nav-row') : []; }
function rowTitle(row) { const title = findFirst(row, 'conversation-nav-title'); return title ? title.textContent : ''; }
function textOf(node) { let text = ''; for (const item of generate(node)) text += String(item.textContent || ''); return text; }
function buttonsIn(node) { return [...generate(node)].filter(item => item.tagName === 'BUTTON'); }

function projectDoc(id, title, project) {
  return {id, title, status: 'ready', error: null, attachment: null,
          updated_at: '2026-09-30T10:00:00Z', ...(project ? {project_workspace: project} : {})};
}
function baseData(overrides = {}) {
  return Object.assign({
    workspaces: [AUTOCODE, IDLECAMPUS], workspace_ids: {}, runs: [],
    conversations: [
      projectDoc('c-dash', 'Dashboard redesign', AUTOCODE),
      projectDoc('c-planner', 'Planner backend', AUTOCODE),
      projectDoc('c-lesson', 'Lesson navigation', IDLECAMPUS),
      projectDoc('c-free', 'Free-form idea', ''),
    ],
    removed_projects: [], archived_tasks: [], archived_conversations: [], registry: {},
  }, overrides);
}
function attentionRuns() {
  return [
    {workspace: AUTOCODE, run: AUTOCODE + '/.autocode/runs/approval', task: 'Approval task', status: 'AWAITING_GOAL_APPROVAL',
     human_request_authorized: true,
     human_escalation: {request_id: 'env-approval', request_token: 'token-approval', scope: 'goal_approval'},
     goal_token: 'r2:abc', goal: {revision: 2, hash: 'abc', approval_status: 'draft', body: {intended_outcome: 'Ship the sidebar'}},
     questions: []},
    {workspace: AUTOCODE, run: AUTOCODE + '/.autocode/runs/question', task: 'Question task', status: 'WAITING_FOR_USER',
     human_request_authorized: true,
     human_escalation: {request_id: 'env-question', request_token: 'token-question', scope: 'clarification'},
     questions: [{id: 'q1', question: 'Which database should the planner assume?'}], answers: {}},
    {workspace: AUTOCODE, run: AUTOCODE + '/.autocode/runs/building', task: 'Building task', status: 'RUNNING',
     active_stage: {stage: 'terra'}, monitor: {live: {state: 'alive'}}},
    {workspace: AUTOCODE, run: AUTOCODE + '/.autocode/runs/recovering', task: 'Recovering task', status: 'RUNNING',
     active_stage: {stage: 'astra_resolve'}, monitor: {live: {state: 'alive'}}},
  ];
}
async function submitCreation(text) {
  document.querySelector('#new-goal').value = text;
  const form = document.querySelector('#create');
  await form.onsubmit({preventDefault() {}});
  const request = [...fetchLog].reverse().find(entry => entry.url === '/api/conversations');
  assert.ok(request, 'creating a conversation posted to /api/conversations');
  return {request, body: JSON.parse(request.options.body || '{}')};
}

// ---- acceptance cases (AC1-AC9) -------------------------------------------------
const CASES = {
  async ac1() {
    const data = baseData();
    serverState.data = data;
    app.setData(data);
    app.renderTasks(data);
    const groups = sidebarGroups();
    assert.equal(3, groups.length, 'two project groups plus the No project group render');
    const headers = groups.map(group => textOf(groupToggle(group)));
    assert.ok(headers.some(title => title.includes('AutoCode')), 'AutoCode group header names the project');
    assert.ok(headers.some(title => title.includes('IdleCampus')), 'IdleCampus group header names the project');
    assert.ok(headers.some(title => title.includes('No project')), 'a visible No project group exists in the same sidebar');
    const autocode = groups.find(group => textOf(groupToggle(group)).includes('AutoCode'));
    const idle = groups.find(group => textOf(groupToggle(group)).includes('IdleCampus'));
    const free = groups.find(group => textOf(groupToggle(group)).includes('No project'));
    assert.deepEqual(['Dashboard redesign', 'Planner backend'], groupRows(autocode).map(rowTitle),
      'project conversations render as indented rows inside their project group');
    assert.deepEqual(['Lesson navigation'], groupRows(idle).map(rowTitle));
    const freeRows = groupRows(free);
    assert.equal(1, freeRows.length);
    assert.equal('Free-form idea', rowTitle(freeRows[0]));
    assert.ok(findFirst(autocode, 'project-group-rows'), 'rows live in a nested rows container inside the group');
    const toggle = groupToggle(autocode);
    assert.equal('true', toggle.getAttribute('aria-expanded'), 'groups expose their expanded state');
    toggle.onclick();
    let collapsed = sidebarGroups().find(group => textOf(groupToggle(group)).includes('AutoCode'));
    assert.equal(true, findFirst(collapsed, 'project-group-rows').hidden, 'toggling collapses the group');
    assert.equal('false', groupToggle(collapsed).getAttribute('aria-expanded'));
    groupToggle(collapsed).onclick();
    const expanded = sidebarGroups().find(group => textOf(groupToggle(group)).includes('AutoCode'));
    assert.equal(false, findFirst(expanded, 'project-group-rows').hidden, 'toggling again expands the group');
    assert.equal(2, groupRows(expanded).length, 'the expanded group still shows its conversation rows');
  },
  async ac2() {
    const data = baseData();
    serverState.data = data;
    app.setData(data);
    app.renderTasks(data);
    const search = document.querySelector('#conversation-search');
    assert.equal('function', typeof search.oninput, 'the sidebar has a conversation search field');
    search.oninput({target: {value: 'lesson'}});
    let groups = sidebarGroups();
    assert.equal(1, groups.length, 'only the matching project group remains');
    assert.ok(textOf(groupToggle(groups[0])).includes('IdleCampus'), 'the matching project group header remains visible');
    const rows = groupRows(groups[0]);
    assert.equal(1, rows.length);
    assert.equal('Lesson navigation', rowTitle(rows[0]));
    assert.equal(false, findFirst(groups[0], 'project-group-rows').hidden, 'the matching row is visible while searching');
    search.oninput({target: {value: ''}});
    groups = sidebarGroups();
    assert.equal(3, groups.length, 'clearing the search restores all groups');
    assert.equal(4, groups.reduce((total, group) => total + groupRows(group).length, 0),
      'clearing the search restores all conversation rows');
  },
  async ac3() {
    const data = baseData();
    serverState.data = data;
    app.setData(data);
    app.renderTasks(data);
    const autocode = sidebarGroups().find(group => textOf(groupToggle(group)).includes('AutoCode'));
    const create = groupCreate(autocode);
    assert.ok(create, 'the AutoCode group has its own New conversation control');
    assert.ok((create.getAttribute('aria-label') || create.title || '').includes('AutoCode'),
      'the per-project control names its project');
    const opening = create.onclick();
    assert.ok(opening && typeof opening.then === 'function',
      'activating the control opens the new conversation through the saved-conversation request');
    await opening;
    const creations = fetchLog.filter(entry => entry.url === '/api/conversations');
    assert.equal(1, creations.length, 'exactly one saved conversation was created on activation');
    const body = JSON.parse(creations[0].options.body || '{}');
    assert.equal(AUTOCODE, body.workspace, 'the saved record is attached to the AutoCode workspace');
    assert.equal(true, body.empty, 'the conversation is created empty, before any message is sent');
    assert.ok(!String(body.text || '').trim(), 'no message text is submitted by activating the control');
    const state = app.state();
    assert.equal('draft-conversation', state.view, 'a conversation is open, not the creation form');
    const created = serverState.data.conversations.find(row => row.title === 'New conversation');
    assert.ok(created, 'the persisted empty conversation appears in the dashboard data');
    assert.equal(created.id, state.activeConversation, 'the opened conversation is the saved record');
    assert.deepEqual([], created.messages, 'the saved record has no messages yet');
    assert.equal(AUTOCODE, created.project_workspace, 'the saved record carries the AutoCode workspace');
    assert.ok(textOf(document.querySelector('#draft-subtitle')).includes('AutoCode'),
      'the conversation header shows AutoCode before any message is sent');
    assert.ok(String(document.querySelector('#draft-text').placeholder).includes('AutoCode'),
      'the composer shows AutoCode before any message is sent');
    await app.refresh();
    const reopened = sidebarGroups().find(group => textOf(groupToggle(group)).includes('AutoCode'));
    assert.ok(groupRows(reopened).some(row => rowTitle(row) === 'New conversation'),
      'the new empty conversation appears inside the AutoCode group on the next sidebar update');
  },
  async ac4() {
    const data = baseData({runs: [{workspace: AUTOCODE, run: AUTOCODE + '/.autocode/runs/live', task: 'Open task', status: 'RUNNING',
                                   active_stage: {stage: 'terra'}, monitor: {live: {state: 'alive'}}}]});
    serverState.data = data;
    app.setData(data);
    app.setActiveProject(AUTOCODE);
    app.renderTasks(data);
    assert.equal('New conversation in AutoCode', document.querySelector('#new-task-scope-label').textContent,
      'the top control shows the active project scope before submission');
    app.setActiveProject('');
    app.showNewTask();
    const {body} = await submitCreation('Unscoped idea');
    assert.equal(false, 'workspace' in body, 'with no active project the top control creates an unscoped conversation');
    // The earlier fabricated c-created snapshot replacement was removed for a
    // goal-consistent reason: a test-made record cannot prove the criterion's
    // creation-to-sidebar result. Follow the creation response's actual saved
    // record instead, and refresh the existing server snapshot without
    // substituting a different conversation.
    const created = serverState.lastCreation;
    assert.ok(created && created.id, 'the creation response returned the actual saved record');
    assert.ok(!('project_workspace' in created) && !created.attachment,
      'the actual saved record carries no project scope');
    assert.equal(created.id, app.state().activeConversation,
      'the opened conversation is the record the creation response saved');
    await app.refresh();
    const free = sidebarGroups().find(group => textOf(groupToggle(group)).includes('No project'));
    assert.ok(free, 'the unscoped conversation appears in the No project group');
    const row = groupRows(free).find(candidate => candidate.dataset.conversationKey === 'doc:' + created.id);
    assert.ok(row, 'the row shown in No project is the actual created record');
    assert.equal('Unscoped idea', rowTitle(row));
    assert.ok(serverState.data.conversations.includes(created),
      'the server snapshot still holds the created record, never a replacement');
  },
  async ac5() {
    const doc = {id: 'c-free', title: 'Free-form idea', status: 'ready', error: null, attachment: null,
                 messages: [{id: 'm1', role: 'user', speaker: 'You', text: 'Explore the idea', created_at: '2026-09-30T09:00:00Z', status: 'saved'},
                            {id: 'm2', role: 'assistant', speaker: 'Requirements Gatherer', text: 'Who is it for?', created_at: '2026-09-30T09:00:05Z', status: 'received'}],
                 updated_at: '2026-09-30T10:00:00Z'};
    const data = baseData({conversations: [projectDoc('c-dash', 'Dashboard redesign', AUTOCODE), doc]});
    serverState.data = data;
    app.setData(data);
    app.renderTasks(data);
    const before = JSON.stringify(doc);
    assert.equal(0, fetchLog.filter(entry => entry.url.startsWith('/api/conversation?')).length,
      'the saved conversation has not been read yet');
    const free = sidebarGroups().find(group => textOf(groupToggle(group)).includes('No project'));
    assert.ok(free, 'the No project group is present');
    const row = groupRows(free).find(candidate => rowTitle(candidate) === 'Free-form idea');
    assert.ok(row, 'the project-free conversation is openable from the No project group');
    row.onclick();
    const state = app.state();
    assert.equal('c-free', state.activeConversation, 'opening selects the conversation');
    assert.equal('draft-conversation', state.view);
    await app.refresh();
    const reads = fetchLog.filter(entry => entry.url.startsWith('/api/conversation?'));
    assert.equal(1, reads.length, 'opening the row issued exactly one conversation read request');
    assert.equal('/api/conversation?id=c-free', reads[0].url, 'the saved record was fetched by its id');
    const host = document.querySelector('#draft-messages');
    assert.ok(host.children.length >= 2,
      'its transcript loads from the fetched saved history, not from test-supplied rendering');
    assert.ok(textOf(host).includes('Explore the idea') && textOf(host).includes('Who is it for?'),
      'the saved history is intact in the rendered transcript');
    assert.equal(before, JSON.stringify(doc), 'the saved record is unchanged by opening');
    assert.equal(null, doc.attachment, 'the record still has no project attached');
    assert.ok(!('project_workspace' in doc), 'no project scope was attached by opening');
  },
  async ac6() {
    const empty = {id: 'c-empty', title: 'New conversation', status: 'ready', error: null, attachment: null,
                   messages: [], updated_at: '2026-09-30T11:00:00Z'};
    const failed = {id: 'c-error', title: 'Delivery failed', status: 'error',
                    error: 'The Requirements Gatherer could not reply. Your message is saved; check the provider connection and retry.',
                    attachment: null,
                    messages: [{id: 'm0', role: 'user', speaker: 'You', text: 'Start', created_at: '2026-09-30T11:30:00Z', status: 'error'}],
                    updated_at: '2026-09-30T11:30:00Z'};
    const data = baseData({runs: attentionRuns(), conversations: [empty, failed]});
    serverState.data = data;
    app.setData(data);
    app.renderTasks(data);
    const markers = findAll(document.querySelector('#projects'), 'attention-marker');
    assert.equal(2, markers.length, 'only the pending-approval and pending-reply rows carry the amber marker');
    const projects = document.querySelector('#projects');
    const rowFor = title => findAll(projects, 'conversation-nav-row').find(row => rowTitle(row) === title);
    assert.ok(rowFor('Approval task') && findFirst(rowFor('Approval task'), 'attention-marker'), 'the zero-question approval row is marked');
    assert.ok(rowFor('Question task') && findFirst(rowFor('Question task'), 'attention-marker'), 'the pending reply row is marked');
    for (const title of ['Building task', 'Recovering task', 'New conversation', 'Delivery failed']) {
      const row = rowFor(title);
      assert.ok(row, title + ' renders as a conversation row');
      assert.equal(null, findFirst(row, 'attention-marker'), title + ' shows no attention marker');
    }
    const marked = markers.map(marker => marker.parentElement);
    assert.ok(marked.every(holder => findAll(projects, 'conversation-nav-row').some(row => row === holder || [...generate(row)].includes(holder))),
      'markers sit inside their conversation rows');
  },
  async ac6_intake() {
    // The doc side of the pending-reply state: an intake conversation whose
    // store-verified question is unanswered carries the amber marker, while an
    // answered question, a delivery error and a forged or foreign projection
    // stay unmarked. The summary projection mirrors what ConversationStore.list
    // publishes after verifying the saved receipt.
    const awaiting = {id: 'c-await', title: 'Intake awaiting reply', status: 'ready', error: null, attachment: null,
                      human_request: {kind: 'intake', decision_needed: 'Which database should the planner assume?'},
                      messages: [{id: 'm1', role: 'user', speaker: 'You', text: 'Plan the planner', created_at: '2026-09-30T11:00:00Z', status: 'saved'},
                                 {id: 'm2', role: 'assistant', speaker: 'AutoResolver', text: 'Which database should the planner assume?',
                                  created_at: '2026-09-30T11:00:05Z', status: 'received', human_request_authorized: true,
                                  human_escalation: {request_id: 'req-await', request_token: 'token-await', scope: 'intake',
                                                     request: {kind: 'intake', decision_needed: 'Which database should the planner assume?', options: []}}}],
                      updated_at: '2026-09-30T11:00:05Z'};
    const answered = {id: 'c-answered', title: 'Intake answered', status: 'ready', error: null, attachment: null,
                      human_request: null,
                      messages: [{id: 'm3', role: 'user', speaker: 'You', text: 'Use SQLite', created_at: '2026-09-30T11:10:00Z', status: 'saved'}],
                      updated_at: '2026-09-30T11:10:00Z'};
    const failed = {id: 'c-error', title: 'Delivery failed', status: 'error',
                    error: 'AutoResolver could not reply. Your message is saved; check the provider connection and retry.',
                    attachment: null, human_request: null,
                    messages: [{id: 'm0', role: 'user', speaker: 'You', text: 'Start', created_at: '2026-09-30T11:30:00Z', status: 'error'}],
                    updated_at: '2026-09-30T11:30:00Z'};
    const forged = {id: 'c-forged', title: 'Forged projection', status: 'ready', error: null, attachment: null,
                    human_request: {kind: 'operational', decision_needed: 'Looks like a request'},
                    messages: [{id: 'm4', role: 'assistant', speaker: 'AutoResolver', text: 'Looks like a request',
                                created_at: '2026-09-30T11:40:00Z', status: 'received'}],
                    updated_at: '2026-09-30T11:40:00Z'};
    const data = baseData({conversations: [awaiting, answered, failed, forged]});
    serverState.data = data;
    app.setData(data);
    app.renderTasks(data);
    const projects = document.querySelector('#projects');
    const markers = findAll(projects, 'attention-marker');
    assert.equal(1, markers.length, 'only the verified unanswered intake question carries the amber marker');
    const rowFor = title => findAll(projects, 'conversation-nav-row').find(row => rowTitle(row) === title);
    const row = rowFor('Intake awaiting reply');
    assert.ok(row, 'the awaiting intake conversation renders as a row');
    const marker = findFirst(row, 'attention-marker');
    assert.ok(marker, 'the awaiting intake row carries the amber marker');
    assert.equal('Awaiting your reply', marker.getAttribute('aria-label'),
      'the marker exposes its descriptive text');
    assert.equal('Awaiting your reply', marker.title);
    assert.ok(String(row.getAttribute('aria-label') || '').includes('Awaiting your reply'),
      'the row announces the pending reply');
    for (const title of ['Intake answered', 'Delivery failed', 'Forged projection']) {
      const other = rowFor(title);
      assert.ok(other, title + ' renders as a conversation row');
      assert.equal(null, findFirst(other, 'attention-marker'), title + ' shows no attention marker');
    }
    row.onclick();
    assert.equal('c-await', app.state().activeConversation, 'opening selects the intake conversation');
    await app.refresh();
    assert.ok(textOf(document.querySelector('#draft-messages')).includes('Which database should the planner assume?'),
      'the open conversation shows the saved question in its transcript');
    const reopened = findAll(projects, 'conversation-nav-row')
      .find(candidate => candidate.dataset.conversationKey === 'doc:c-await');
    assert.ok(reopened && findFirst(reopened, 'attention-marker'),
      'the marker is still shown while the conversation is open');
  },
  async ac7() {
    const data = baseData({runs: attentionRuns(), conversations: []});
    serverState.data = data;
    app.setData(data);
    app.renderTasks(data);
    const projects = document.querySelector('#projects');
    const approvalRow = findAll(projects, 'conversation-nav-row').find(row => rowTitle(row) === 'Approval task');
    const questionRow = findAll(projects, 'conversation-nav-row').find(row => rowTitle(row) === 'Question task');
    const approvalMark = findFirst(approvalRow, 'attention-marker');
    const questionMark = findFirst(questionRow, 'attention-marker');
    assert.ok(approvalMark && questionMark, 'both pending rows carry the amber marker');
    questionMark.focus();
    assert.equal(questionMark, document.activeElement, 'the marker received focus');
    assert.equal('Awaiting your reply', questionMark.getAttribute('aria-label'),
      'focusing the marker exposes its descriptive text');
    assert.equal('Awaiting your reply', questionMark.title, 'hovering the marker exposes its descriptive text');
    approvalMark.focus();
    assert.equal('Approval requested', approvalMark.getAttribute('aria-label'));
    assert.equal('Approval requested', approvalMark.title);
    questionRow.onclick();
    const state = app.state();
    assert.equal(questionRun(), state.chosen && state.chosen.run, 'opening the conversation selects it');
    await app.refresh();
    document.querySelector('#task-back').onclick();
    assert.equal('tasks', app.state().view, 'the user returned to the sidebar');
    const reopened = findAll(projects, 'conversation-nav-row').find(row => rowTitle(row) === 'Question task');
    assert.ok(reopened && findFirst(reopened, 'attention-marker'),
      'the marker is still shown after opening the conversation');
    assert.equal('Awaiting your reply', findFirst(reopened, 'attention-marker').getAttribute('aria-label'));
  },
  async ac8() {
    const runs = attentionRuns();
    const data = baseData({runs, conversations: []});
    serverState.data = data;
    // Resolving the approval in chat records it against the saved token, then
    // the run advances; every later sidebar update renders the saved state.
    serverState.onAction = body => {
      const approval = runs.find(run => run.task === 'Approval task');
      if (body.action === 'approve_goal') {
        const approvedToken = approval.goal_token;
        approval.goal = {...approval.goal, approval_status: 'approved',
                         approval_event: {token: approvedToken, actor: 'user_cli', at: '2026-09-30T12:05:00Z'}};
        approval.goal_token = '';
      }
      if (body.action === 'continue') {
        approval.status = 'RUNNING';
        approval.active_stage = {stage: 'terra'};
        approval.monitor = {live: {state: 'alive'}};
      }
    };
    app.setData(data);
    app.renderTasks(data);
    const projects = document.querySelector('#projects');
    const approvalRow = () => findAll(projects, 'conversation-nav-row').find(row => rowTitle(row) === 'Approval task');
    assert.ok(findFirst(approvalRow(), 'attention-marker'), 'the row starts with the amber marker');
    approvalRow().onclick();
    await app.refresh();
    const approve = buttonsIn(document.querySelector('#inline-task-action'))
      .find(button => String(button.textContent || '').startsWith('Approve & build'));
    assert.ok(approve, 'the conversation view offers the approval action for the pending revision');
    await approve.onclick();
    const actions = fetchLog.filter(entry => entry.url === '/api/action').map(entry => JSON.parse(entry.options.body || '{}'));
    const approvalAction = actions.find(body => body.action === 'approve_goal');
    assert.ok(approvalAction, 'submitting the approval posted the approve_goal chat action');
    assert.equal('r2:abc', approvalAction.token, 'the approval was submitted with the saved goal token');
    assert.equal('r2:abc', approvalAction.confirmation, 'the confirmation repeats the saved token');
    assert.equal('env-approval', approvalAction.resolver_request, 'the request stays bound to its saved escalation');
    assert.ok(actions.some(body => body.action === 'continue'), 'the approved revision was started as a separate event');
    await app.refresh();
    const after = approvalRow();
    assert.ok(after, 'the conversation row is not deleted or archived after resolution');
    assert.equal(null, findFirst(after, 'attention-marker'), 'the amber marker disappears on the next sidebar update');
    assert.equal(0, fetchLog.filter(entry => entry.url === '/api/conversation/archive').length,
      'resolving the request never archives the conversation');
    assert.equal(0, fetchLog.filter(entry => entry.url === '/api/tasks' || entry.url === '/api/tasks/delete').length,
      'resolving the request never archives or deletes the task');
  },
  async ac9() {
    const data = baseData({runs: [{workspace: AUTOCODE, run: AUTOCODE + '/.autocode/runs/done', task: 'Completed task',
                                   status: 'TASK_COMPLETE', completed_at: '2026-09-30T09:30:00Z'}]});
    serverState.data = data;
    app.setData(data);
    app.renderTasks(data);
    const row = findAll(document.querySelector('#projects'), 'conversation-nav-row').find(candidate => rowTitle(candidate) === 'Completed task');
    assert.ok(row, 'the completed conversation renders as a row');
    assert.ok(findFirst(row, 'complete-tick'), 'the completed row shows a green tick');
    assert.equal(null, findFirst(row, 'attention-marker'), 'the completed row shows no amber marker');
    assert.ok(!page.includes('Needs you'), 'the sidebar contains no Needs you navigation entry');
    assert.ok(!page.includes('data-view="inbox"'), 'no global inbox navigation entry exists');
    assert.ok(!page.includes('id="inbox"'), 'no global inbox page exists');
  },
};

function questionRun() { return AUTOCODE + '/.autocode/runs/question'; }

const name = process.argv[2];
if (!name || !CASES[name]) {
  console.error('usage: node sidebar_workspace_harness.js <' + Object.keys(CASES).join('|') + '>');
  process.exit(2);
}
Promise.resolve().then(() => CASES[name]()).then(
  () => { console.log('PASS ' + name); process.exit(0); },
  error => { console.error('FAIL ' + name + ': ' + (error && error.stack || error)); process.exit(1); });
