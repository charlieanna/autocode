// Exercise the shipped chat interactions without a provider or a real task.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('./dashboard_vm');
const source = fs.readFileSync(path.join(__dirname, '../dashboard_app.js'), 'utf8');

function functionSource(name) {
  const start = source.indexOf('function ' + name + '(');
  assert.notEqual(start, -1, name + ' must exist in the shipped frontend');
  const brace = source.indexOf('{', start);
  let depth = 1;
  for (let at = brace + 1; at < source.length; at++) {
    if (source[at] === '{') depth++;
    if (source[at] === '}' && --depth === 0) return source.slice(start, at + 1);
  }
  assert.fail('Unterminated function: ' + name);
}

const helpers = vm.createContext({});
vm.runInContext(functionSource('composerShouldSend') + '\n' + functionSource('conversationDeliveryBlocked') + '\n' + functionSource('conversationRetryAllowed') + '\n' + functionSource('draftSendBlocked'), helpers);

assert.equal(helpers.composerShouldSend({key: 'Enter'}), true);
assert.equal(helpers.composerShouldSend({key: 'Enter', ctrlKey: true}), true);
assert.equal(helpers.composerShouldSend({key: 'Enter', metaKey: true}), true);
for (const event of [
  {key: 'a'},
  {key: 'Enter', shiftKey: true},
  {key: 'Enter', altKey: true},
  {key: 'Enter', isComposing: true},
  {key: 'Enter', ctrlKey: true, shiftKey: true},
]) assert.equal(helpers.composerShouldSend(event), false);

const ready = {id: 'conversation-one', status: 'ready'};
assert.equal(helpers.draftSendBlocked(ready, false, ' Revise the examples '), false);
for (const text of ['', '  ', '\n\t']) assert.equal(helpers.draftSendBlocked(ready, false, text), true);
assert.equal(helpers.draftSendBlocked(null, false, 'A reply'), true);
assert.equal(helpers.draftSendBlocked(ready, true, 'A reply'), true);
for (const state of [
  {project_removed: true},
  {task_archived: true},
  {archived_at: '2026-09-21T08:00:00Z'},
  {status: 'thinking'},
  {pending_dispatch:{state:'UNCERTAIN',retryable:false}},
  {planner_delivery:{state:'PROCESS_STARTED',retryable:false}},
  {status: 'error'},
  {attachment: {status: 'starting'}},
  {attachment: {status: 'linked', run: '/fixture/run'}},
  {attachment: {status: 'failed'}},
]) assert.equal(helpers.draftSendBlocked({...ready, ...state}, false, 'A reply'), true);

assert.equal(helpers.draftSendBlocked({...ready,pending_dispatch:{state:'REPLY_COMMITTED',retryable:false},planner_delivery:{state:'REPLY_COMMITTED',retryable:false}},false,'Next turn'),false);
assert.equal(helpers.conversationRetryAllowed({pending_dispatch:{state:'REPLY_COMMITTED',retryable:false},planner_delivery:{state:'SAFE_NOT_DISPATCHED',retryable:true}}),true);
assert.equal(helpers.conversationRetryAllowed({pending_dispatch:{state:'UNCERTAIN',retryable:false},planner_delivery:{state:'SAFE_NOT_DISPATCHED',retryable:true}}),false);

class Element {
  constructor(tag, text = '') {
    this.tagName = tag.toUpperCase();
    this.textContent = text;
    this.children = [];
    this.dataset = {};
  }
  append(...children) { this.children.push(...children); }
  prepend(...children) { this.children.unshift(...children); }
}
const formatting = vm.createContext({
  n: (tag, text) => new Element(tag, text),
  card: (_, className) => Object.assign(new Element('div'), {className}),
  human: text => String(text),
  roleDisplayName: text => ({GLM: 'Requirements planner', Astra: 'Plan Reviewer', Terra: 'Builder', Sol: 'Tester', Planner: 'Planner', 'Plan Reviewer': 'Plan Reviewer'})[String(text)] || String(text),
  concise: (text, limit) => String(text).slice(0, limit),
  messageTime: () => 0,
  messageBody: text => Object.assign(new Element('div', text), {className: 'message-body'}),
  receiptLabel: message => message.status,
  disclosure: (label, key, children) => {
    const node = Object.assign(new Element('details'), {label, key});
    node.append(...children);
    return node;
  },
  chosen: null,
  activeConversation: 'conversation-one',
  latestRun: null,
});
vm.runInContext(functionSource('messageAnchorKey') + '\n' + functionSource('appendMessage') + '\n' + functionSource('answerHistoryItems') + '\n' + functionSource('renderMessageHistory'), formatting);

const earlierText = 'Earlier draft. ' + 'Detail. '.repeat(180);
const latestText = 'The revised plan. ' + 'Detail. '.repeat(180) + '\nWhich option do you prefer?';
const thread = new Element('div');
formatting.renderMessageHistory(thread, [
  {role: 'assistant', speaker: 'Planner', text: earlierText},
  {role: 'user', text: 'Please revise the plan.'},
  {role: 'assistant', speaker: 'Planner', text: latestText},
], 'conversation-one');
const earlier = thread.children[0];
const latest = thread.children[2];
assert.equal(earlier.children.some(node => node.tagName === 'DETAILS'), true, 'Older long replies remain expandable');
assert.equal(latest.children.some(node => node.tagName === 'DETAILS'), false, 'The newest reply must not hide its questions');
assert.equal(latest.children.some(node => node.className === 'message-body' && node.textContent === latestText), true);

// A user reply must not collapse the last assistant message on the next refresh.
const afterReply = new Element('div');
formatting.renderMessageHistory(afterReply, [
  {role: 'assistant', speaker: 'Planner', text: latestText},
  {role: 'user', text: 'Use the first option.'},
], 'conversation-one');
assert.equal(afterReply.children[0].children.some(node => node.tagName === 'DETAILS'), false);

assert.match(source, /Planning continues only if the controller returns to Running with no pending request\./);
assert.doesNotMatch(source, /After the final answer is saved, planning continues automatically\./);
assert.match(source, /This does not approve a plan or start implementation\./);
assert.match(source, /Refresh status and reconcile this request ID before retrying to avoid a duplicate mutation\./);
assert.doesNotMatch(functionSource('requestRow').split("else if(entry.status==='failed'")[0], /Retry same request/,
  'an uncertain receipt must be reconciled before any retry is offered');

console.log('Chat keyboard handling, blocked sends, and visible latest assistant reply passed.');

const answerHistory=new Element('div');
formatting.renderMessageHistory(answerHistory,[
 {id:'a1',role:'user',question_id:'Q1',text:'JSON',status:'received',delegate:true},
 {id:'a2',role:'user',question_id:'Q2',text:'Local',status:'received',delegate:false},
 {id:'a3',role:'user',question_id:'Q1',text:'A revised answer',status:'received'},
 {id:'a4',role:'user',question_id:'Q3',text:'Retained failed draft',status:'error',error:'Delivery failed'}
],'fixture-history');
assert.equal(answerHistory.children[0].label,'You answered 2 questions · 1 used the suggestion');
assert.equal(answerHistory.children[1].label,'You answered 1 question · 0 used the suggestion');
assert.notEqual(answerHistory.children[2].tagName,'DETAILS','Failed delivery remains visible outside accepted answer history');

// Unknown legacy provenance is not merged into a newly confirmed answer group.
const mixedAnswers=formatting.answerHistoryItems([
 {id:'old',role:'user',question_id:'old',status:'received',provenance:'unrecorded',display_source:'answer'},
 {id:'new1',role:'user',question_id:'q1',status:'received',display_source:'receipt'},
 {id:'new2',role:'user',question_id:'q2',status:'received',display_source:'receipt'}]);
assert.equal(mixedAnswers[0].id,'old');
assert.equal(mixedAnswers[1].answers.length,2);
