// VM harness for the M3 lifecycle workspace (AC18). Driven per case by
// tools/dashboard/tests/test_workspace_lifecycle_ui.py; not a standalone gate.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('./dashboard_vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'dashboard_app.js'), 'utf8');

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
const serverState = {data: {workspaces: [AUTOCODE], workspace_ids: {}, runs: [], conversations: [],
                            removed_projects: [], archived_tasks: [], archived_conversations: [], registry: {}},
                     evidence: {}};
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
    const saved = serverState.evidence[query.get('run') || ''] || {stages: []};
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
    return {ok: true, json: async () => ({id: 'action-' + (++createdCount), status: 'finished', exit_status: 0})};
  }
  return new Promise(() => {});
};

let uuidCount = 0;
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
    activateTab: value => activateTab(value),
    refresh: () => refresh(),
    state: () => ({view: currentView, tab: currentTab}),
  };
})()`, context);
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
const pane = id => document.querySelector(id);
const tick = () => new Promise(resolve => setTimeout(resolve, 0));
// Every host the five artifact panes render into; approval and answer controls
// must appear in none of them.
const PANE_HOSTS = ['#now', '#brief-current', '#plan-full', '#preview-content', '#preview-empty-note',
                    '#changes-content', '#execution_view'];
function paneControls() {
  const found = [];
  for (const id of PANE_HOSTS) for (const control of buttonsIn(pane(id))) found.push({pane: id, control});
  return found;
}
const HUMAN_ACTION = /approve|answer|accept suggested|reply|respond|send answer/i;

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

// Saved lifecycle runs. Each carries only saved record facts; the Work pane
// must render exactly these values for its state.
function lifecycleBase() {
  return {workspace: AUTOCODE, run: AUTOCODE + '/.autocode/runs/lifecycle', task: 'A clearer agent workspace',
          display_title: 'A clearer agent workspace', status: 'PAUSED_BUDGET', stop_reason: '',
          stage: 'terra', iteration: 3, active_stage: {}, questions: [], answers: {}, user_request: null,
          criteria: [{id: 'AC1', criterion: 'Chat stays visible in every pane'}],
          counts: {pass: 0, fail: 0, unknown: 1}, validation: null,
          progress_messages: [], chat_messages: [], plan: [], astra_plan: null, goal: null,
          interventions: {entries: []}, model_settings: {},
          created_at: '2026-09-30T10:00:00Z', updated_at: '2026-09-30T12:00:00Z'};
}
function requirementsRun() {
  const run = lifecycleBase();
  run.run = AUTOCODE + '/.autocode/runs/requirements';
  run.status = 'WAITING_FOR_USER';
  run.human_request_authorized = true;
  run.questions = [
    {id: 'q1', question: 'Should the sidebar keep the archive list visible by default?', why: 'It decides the navigation height.', proposed_default: 'Yes, collapsed'},
    {id: 'q2', question: 'Can the work pane pin the current step?', why: 'It decides the layout order.', proposed_default: 'Yes, at the top'},
  ];
  // The real backend projection copies the questions into the escalation
  // envelope; renderQuestion binds cards to that envelope.
  run.human_escalation = {request_id: 'req-1', request_token: 'rtok-1', scope: 'clarification', questions: run.questions};
  return run;
}
function planReviewRun() {
  const run = lifecycleBase();
  run.run = AUTOCODE + '/.autocode/runs/plan-review';
  run.status = 'AWAITING_GOAL_APPROVAL';
  run.goal_token = 'r7:plan-token';
  run.human_request_authorized = true;
  run.human_escalation = {request_id: 'req-2', request_token: 'rtok-2', scope: 'goal_approval', request: {criteria: []}};
  run.goal = {revision: 7, approval_status: 'draft', origin: 'astra_finalize',
              body: {intended_outcome: 'Ship the approved chat workspace', requirements: ['Keep chat visible'],
                     constraints: [], implementation_sequence: ['Sidebar', 'Panes', 'Drawers'],
                     acceptance_criteria: [{id: 'AC1', criterion: 'Chat stays visible in every pane'}],
                     accepted_assumptions: []}};
  return run;
}
function buildingRun() {
  const run = lifecycleBase();
  run.run = AUTOCODE + '/.autocode/runs/building';
  run.status = 'RUNNING';
  run.stop_reason = '';
  run.active_stage = {stage: 'terra', role: 'terra', started_at: '2026-09-30T11:50:00Z'};
  run.monitor = {live: {state: 'alive'}, objective: 'Implement the pane switches without hiding chat',
                 active_role: 'terra',
                 active_execution: {kind: 'model', model: 'gpt-5.6-terra', reasoning_effort: 'medium'},
                 stage_history: []};
  run.progress_messages = [{role: 'assistant', speaker: 'Builder', text: 'Navigation is complete. I’m adding the context panel.',
                            created_at: '2026-09-30T11:00:00Z', status: 'received'}];
  return run;
}
function recoveryRun() {
  const run = lifecycleBase();
  run.run = AUTOCODE + '/.autocode/runs/recovering';
  run.status = 'RUNNING';
  run.active_stage = {stage: 'astra_resolve', role: 'resolver', started_at: '2026-09-30T11:55:00Z'};
  run.monitor = {live: {state: 'alive'},
                 objective: 'The preview port is already in use. Saved work and the approved plan are preserved while the startup configuration is checked.',
                 active_role: 'resolver',
                 active_execution: {kind: 'model', model: 'gpt-6-sol', reasoning_effort: 'high'},
                 stage_history: []};
  run.progress_messages = [{role: 'assistant', speaker: 'Resolver',
                            text: 'Technical recovery in progress. What happened: the preview port is already in use. Saved work is preserved.',
                            created_at: '2026-09-30T11:55:30Z', status: 'received'}];
  return run;
}
function readyRun() {
  const run = lifecycleBase();
  run.run = AUTOCODE + '/.autocode/runs/ready';
  run.status = 'TASK_COMPLETE';
  run.completed_at = '2026-09-30T12:30:00Z';
  run.validation = {source_revision: 'abc123', criterion_results: [{id: 'AC1', status: 'pass'}]};
  run.counts = {pass: 1, fail: 0, unknown: 0};
  run.criteria = [{id: 'AC1', criterion: 'Chat stays visible in every pane'}];
  return run;
}

// ---- AC18: every lifecycle state renders its saved content, actions stay in chat --
const CASES = {
  async ac18() {
    // Requirements: the open question list is a saved-status summary in Work
    // pointing to the conversation; the answer controls live only in chat.
    const requirements = requirementsRun();
    await openRun(requirements);
    app.activateTab('now');
    await tick();
    const workQuestions = findFirst(pane('#now'), 'work-questions');
    assert.ok(workQuestions, 'the Work pane renders the open question list for the requirements state');
    const questionsText = textOf(workQuestions);
    for (const question of requirements.questions)
      assert.ok(questionsText.includes(question.question),
        'the Work pane question list includes the saved question: ' + question.question);
    assert.ok(/conversation/i.test(questionsText),
      'the question summary points to the conversation for the answers');
    const chatCards = questionCards(pane('#conversation'));
    assert.equal(requirements.questions.length, chatCards.length,
      'each saved question renders as a card in the chat transcript');
    for (const card of chatCards) assert.ok(buttonsIn(card).length >= 1,
      'the answer control for each question stays in the chat transcript');

    // Truthful per-question summary: every row reports its saved question as
    // open, and each row's own control targets that question's chat card.
    const questionRows = findAll(workQuestions, 'work-question-row');
    assert.equal(requirements.questions.length, questionRows.length,
      'each saved question renders its own Work row');
    for (const [index, row] of questionRows.entries()) {
      assert.equal(String(row.dataset.questionId), String(requirements.questions[index].id),
        'Work question row ' + index + ' carries its saved question id');
      const status = textOf(findFirst(row, 'work-question-status'));
      assert.match(status, /open/i,
        'Work question row ' + index + ' truthfully reports the question as open');
      assert.doesNotMatch(status, /answered/i,
        'Work question row ' + index + ' never claims an unanswered question was answered');
      const rowControl = buttonsIn(row)[0];
      assert.ok(rowControl && /conversation/i.test(String(rowControl.textContent)),
        'Work question row ' + index + ' has its own conversation control');
    }
    const rowControls = questionRows.map(row => buttonsIn(row)[0]);
    const targetSelect = pane('#question-target');
    assert.equal(String(targetSelect.value), String(requirements.questions[0].id),
      'the composer targets the first question before any row control is used');
    const scrolled = [];
    for (const card of chatCards) card.scrollIntoView = () => { scrolled.push(String(card.dataset.questionCard)); };
    rowControls[1].click();
    assert.equal(String(targetSelect.value), String(requirements.questions[1].id),
      'the second row control selects its own question in the composer');
    assert.deepEqual(scrolled, [String(requirements.questions[1].id)],
      'the second row control scrolls to its own chat question card, not the first');
    assert.equal(String(chatCards.find(card => hasClass(card, 'selected-question')).dataset.questionCard),
      String(requirements.questions[1].id),
      'the second row control reveals its own chat question card');
    assert.equal(document.activeElement, pane('#change-text'),
      'the row control hands focus to the chat composer where the answer is sent');
    rowControls[0].click();
    assert.deepEqual(scrolled, [String(requirements.questions[1].id), String(requirements.questions[0].id)],
      'the first row control scrolls to the first question card');
    assert.equal(String(targetSelect.value), String(requirements.questions[0].id),
      'the first row control selects the first question in the composer');

    // Plan review: the pending approval summary names the current plan
    // revision; approve controls appear only in the chat transcript.
    const review = planReviewRun();
    await openRun(review);
    app.activateTab('now');
    await tick();
    const reviewWork = textOf(pane('#now'));
    assert.ok(/revision 7/.test(reviewWork),
      'the Work pane approval summary names the current plan revision');
    assert.ok(/conversation/i.test(reviewWork),
      'the approval summary points into the conversation for the action');
    const chatApproval = textOf(pane('#inline-task-action'));
    assert.ok(chatApproval.includes('Approve plan revision 7'),
      'the revision-bound approval control renders in the chat transcript');

    // Building: the current build step with the actual roles and models.
    const building = buildingRun();
    await openRun(building);
    app.activateTab('now');
    await tick();
    const buildWork = pane('#now');
    const stepFact = [...findAll(buildWork, 'monitor-state'), ...findAll(buildWork, 'monitor-status-chip')];
    const stepText = stepFact.map(textOf).join(' ');
    assert.ok(/Builder · Implementing/.test(textOf(buildWork)),
      'the Work pane shows the current build step');
    assert.ok(/Running/.test(stepText), 'the building state reads as running');
    assert.ok(textOf(findFirst(buildWork, 'work-roles')).includes('Builder · gpt-5.6-terra · medium reasoning'),
      'the building state shows the actual Builder role and model from the saved execution');

    // Recovery: the autonomous recovery explanation, with no human request.
    const recovery = recoveryRun();
    await openRun(recovery);
    app.activateTab('now');
    await tick();
    const recoveryWork = pane('#now');
    assert.ok(/Resolver · Diagnosing a failure/.test(textOf(recoveryWork)),
      'the recovery state shows the resolver step');
    const recoverySummary = findFirst(recoveryWork, 'work-recovery');
    assert.ok(recoverySummary, 'the Work pane renders the recovery explanation panel');
    const recoveryText = textOf(recoverySummary);
    assert.ok(recoveryText.includes(recovery.monitor.objective),
      'the recovery explanation matches the saved record');
    assert.ok(/no action is needed from you/i.test(recoveryText),
      'the autonomous recovery asks for no human input');

    // Ready: the completion summary from the saved record.
    const ready = readyRun();
    await openRun(ready);
    app.activateTab('now');
    await tick();
    const readyWork = textOf(pane('#now'));
    assert.ok(/COMPLETION RECORDED/.test(readyWork),
      'the ready state renders its completion summary');
    assert.ok(/No reply needed/.test(readyWork),
      'the completion summary states that no reply is needed');
    assert.ok(/source abc123/.test(readyWork),
      'the completion evidence reads the saved validation source');
    const record = findFirst(pane('#now'), 'completion-record');
    assert.ok(record, 'the ready state renders the completion record');
    assert.equal('recorded', record.dataset.completionRecordState,
      'the completion record reads the saved completion timestamp');
    assert.ok(new Date(ready.completed_at).toLocaleString(undefined, {dateStyle: 'medium', timeStyle: 'medium'})
      === textOf([...generate(record)].find(node => node.dataset && node.dataset.completionTimestamp !== undefined)),
      'the completion timestamp is exactly the saved value');

    // No pane in any state carries an approval or answer control. Re-open the
    // human-input states (requirements, plan review) and scan every pane host.
    for (const run of [requirementsRun(), planReviewRun(), recoveryRun(), readyRun(), buildingRun()]) {
      await openRun(run);
      for (const key of ['now', 'plan', 'preview', 'changes', 'execution']) {
        app.activateTab(key);
        await tick();
        await tick();
        const offenders = paneControls().filter(entry => HUMAN_ACTION.test(String(entry.control.textContent || '')));
        assert.deepEqual([], offenders.map(entry => entry.pane + ':' + entry.control.textContent),
          'no approval or answer control appears in the ' + key + ' pane for ' + run.task);
      }
    }
  },
};

const name = process.argv[2];
if (!name || !CASES[name]) {
  console.error('usage: node workspace_lifecycle_harness.js <' + Object.keys(CASES).join('|') + '>');
  process.exit(2);
}
Promise.resolve().then(() => CASES[name]()).then(
  () => { console.log('PASS ' + name); process.exit(0); },
  error => { console.error('FAIL ' + name + ': ' + (error && error.stack || error)); process.exit(1); });
