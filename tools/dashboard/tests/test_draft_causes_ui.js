// The draft card names the human answers behind each draft (#21), a
// coalesced update waits visibly without offering a second Planner launch, and
// the composer accepts the next answer while a Planner draft is running.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('./dashboard_vm');
const source=fs.readFileSync(path.join(__dirname,'../dashboard_app.js'),'utf8');
class Element{
 constructor(tag,text=''){this.tag=tag;this.textContent=text;this.children=[];this.dataset={};}
 append(...rows){this.children.push(...rows);}
 replaceChildren(...rows){this.children=rows;}
 setAttribute(key,value){this[key]=value;}
}
const all=node=>[node,...node.children.flatMap(all)];
// Real textContent concatenates text nodes without separators.
const text=node=>node.textContent+node.children.map(text).join('');
const ctx=vm.createContext({n:(tag,value)=>new Element(tag,value),card:()=>new Element('div'),
 button:(label,onclick)=>Object.assign(new Element('button',label),{onclick}),
 conversationPending:new Set(),conversationArchiveBlocked:()=>false,
 disclosure:(label,key,nodes)=>{const result=new Element('details',label);result.append(...nodes);return result;},
 renderDocument:value=>new Element('pre',JSON.stringify(value)),planEntryText:row=>row.objective||''});
vm.runInContext(source.slice(source.indexOf('function draftUpdateCard('),source.indexOf('function renderDraftConversation(')),ctx);
vm.runInContext(source.slice(source.indexOf('function planDraftPreviewCard('),source.indexOf('function renderQuietPlanPreview(')),ctx);
vm.runInContext(source.slice(source.indexOf('function currentConversationDraft('),source.indexOf('function runPlanDraft(')),ctx);

const long='Use SQLite for every saved note, including attachments, tags, and the full revision history of each note';
const coalesced={revision:3,status:'current',goal:'Build a notes app',requirements:['Persist notes'],
 attribution:{role:'planner',model:'zai/glm-5.3'},
 freshness:{state:'fresh',reason:'coalesced_update',structured_result:true,updated_at:'2026-10-04T10:00:00Z',source_messages:[
  {message_id:'m1',requirements_revision:1,excerpt:'Build a notes app'},
  {message_id:'m2',requirements_revision:2,excerpt:long},
  {message_id:'m3',requirements_revision:3,excerpt:'Export to CSV'}]}};
const card=ctx.planDraftPreviewCard(coalesced),cardText=text(card);
assert.match(cardText,/Updated after your answers: “Build a notes app” · “Use SQLite for every saved note, including attachments, tags, and the full…” · “Export to CSV” · /);
assert.doesNotMatch(cardText,/Updated with your latest message/,'The generic text is replaced by the causes');
assert.deepEqual(all(card).filter(row=>row.dataset.sourceMessage).map(row=>row.dataset.sourceMessage),['m1','m2','m3']);
const causes=all(card).find(row=>row.className==='quiet-plan-freshness quiet-plan-causes');
assert.ok(causes,'One cause line on the card');
assert.equal(all(card).filter(row=>/quiet-plan-freshness/.test(row.className||'')).length,1,'No separate generic status line');

const single={...coalesced,freshness:{...coalesced.freshness,source_messages:[coalesced.freshness.source_messages[2]]}};
assert.match(text(ctx.planDraftPreviewCard(single)),/Updated after your answer: “Export to CSV”/);

// While the next update waits behind the draft in progress, the current draft
// keeps its causes and the status says what happens next.
const waiting=text(ctx.planDraftPreviewCard(coalesced,{updateState:'pending',updateReason:'coalesced_behind_draft_in_flight'}));
assert.match(waiting,/Updated after your answers: “Build a notes app”/);
assert.match(waiting,/Answers saved · one draft update follows the one in progress/);
assert.match(text(ctx.planDraftPreviewCard(coalesced,{updateState:'pending',updateReason:'batching_answers'})),/draft update batched/);

// A draft the Planner has not produced yet claims no causes.
const pending={revision:4,status:'pending',goal:'Build a notes app',freshness:{state:'pending',source_messages:coalesced.freshness.source_messages}};
const pendingText=text(ctx.planDraftPreviewCard(pending));
assert.doesNotMatch(pendingText,/Updated after/);assert.match(pendingText,/Planner is updating the draft/);
// A pending placeholder that newer input superseded is 'stale' too, yet the
// Planner never produced it: while the first draft runs (or after a failed
// follow-up) the card shows it and must not claim an update happened.
const placeholder={revision:2,status:'superseded',goal:'Build a notes app',requirements:[],milestones:[],
 freshness:{state:'stale',reason:'invalidated_by_newer_requirements_input',updated_at:'2026-10-04T10:00:00Z',
  source_messages:coalesced.freshness.source_messages.slice(0,2)}};
const pendingFollowUp={revision:3,status:'pending',goal:'Build a notes app',
 freshness:{state:'pending',reason:'coalesced_behind_draft_in_flight',updated_at:'2026-10-04T10:00:01Z',source_messages:coalesced.freshness.source_messages}};
const shown=ctx.currentConversationDraft({plan_drafts:[{...placeholder,revision:1},placeholder,pendingFollowUp]});
assert.equal(shown.revision,2,'With no current draft the card falls back to the superseded placeholder');
const placeholderText=text(ctx.planDraftPreviewCard(shown,{updateState:'pending',updateReason:'coalesced_behind_draft_in_flight'}));
assert.doesNotMatch(placeholderText,/Updated after/);
assert.match(placeholderText,/Answers saved · one draft update follows the one in progress/);
assert.doesNotMatch(text(ctx.planDraftPreviewCard({...placeholder,freshness:{...placeholder.freshness,state:'failed'}})),/Updated after/);
// A produced draft that newer answers made stale keeps the answers it covered.
const staleProduced={...coalesced,status:'superseded',freshness:{...coalesced.freshness,state:'stale',reason:'newer_requirements_input'}};
assert.match(text(ctx.planDraftPreviewCard(staleProduced)),/Updated after your answers: “Build a notes app”/);
// Legacy drafts without saved causes keep the generic label.
assert.match(text(ctx.planDraftPreviewCard({revision:1,goal:'Old',freshness:{state:'fresh'}})),/Updated with your latest message/);

const update=ctx.draftUpdateCard({id:'c1',draft_update:{held:true,coalesced:true,can_refresh:false,answers_since_update:2,answers_per_update:3}});
assert.match(text(update),/2 of your latest messages are saved\. The Planner is finishing the draft it already started; one update covering all of them starts as soon as it finishes\./);
assert.equal(all(update).filter(row=>row.tag==='button').length,0,'A coalesced update offers no second launch');
// Once the draft ahead has stopped reporting progress, the human may update now.
const stalled=ctx.draftUpdateCard({id:'c1',draft_update:{held:true,coalesced:true,stalled:true,can_refresh:true,answers_since_update:5,answers_per_update:3}});
assert.match(text(stalled),/5 of your latest messages are saved\. The draft they were waiting for has stopped reporting progress/);
const unstick=all(stalled).filter(row=>row.tag==='button');
assert.equal(unstick.length,1);assert.equal(unstick[0].textContent,'Update draft now');assert.ok(!unstick[0].disabled,'Update draft now is enabled');
assert.equal(all(ctx.draftUpdateCard({id:'c1',draft_update:{held:true,coalesced:false,can_refresh:true,answers_since_update:1,answers_per_update:3}})).filter(row=>row.tag==='button').length,1);

// The shipped composer sends the next answer while the latest turn's Planner
// draft is running (status ready, Planner PROCESS_STARTED), and the server's
// coalesced reply renders the waiting card in chat.
(async()=>{
 const posted=[],rendered=[],input={value:'Use SQLite',dataset:{conversation:'c1'},focus(){}};
 const composer=vm.createContext({$:selector=>selector==='#draft-text'?input:{},conversationPending:new Set(),conversationRetries:new Map(),
  conversationArchiveBlocked:()=>false,taskArchiveBlocked:()=>false,projectBlocked:()=>false,conversationWorkspace:()=>'',
  savedRequest:()=>({id:'send-1'}),persist(){},seq:0,activeConversation:'c1',latestConversation:null,refresh(){},
  requestAnimationFrame(){},scrollDraftToEnd(){},renderDraftConversation:doc=>rendered.push(doc),
  api:async(url,options)=>{posted.push({url,body:JSON.parse(options.body)});return {...drafting,draft_update:{held:true,coalesced:true,stalled:false,can_refresh:false,
   requirements_revision:2,logical_turn_id:'turn-2',answers_since_update:1,answers_per_update:3}};}});
 const helpers=['conversationDeliveryBlocked','conversationRetryAllowed','conversationSendBlocked','draftSendBlocked'];
 vm.runInContext(source.slice(source.indexOf('function '+helpers[0]+'('),source.indexOf('function resizeComposer(')),composer);
 vm.runInContext(source.slice(source.indexOf('async function sendDraftMessage('),source.indexOf('async function retryConversation(')),composer);
 const drafting={id:'c1',status:'ready',messages:[{role:'user',text:'Build a notes app'}],
  pending_dispatch:{state:'REPLY_COMMITTED',retryable:false},planner_delivery:{state:'PROCESS_STARTED',retryable:false}};
 assert.equal(composer.draftSendBlocked(drafting,false,input.value),false,'Send stays enabled while the draft runs');
 await composer.sendDraftMessage(drafting);
 assert.deepEqual(posted.map(row=>[row.url,row.body.text]),[['/api/conversation/message','Use SQLite']]);
 assert.equal(input.value,'','The sent answer leaves the composer');
 const waiting=ctx.draftUpdateCard(rendered.find(doc=>doc.draft_update?.coalesced));
 assert.match(text(waiting),/one update covering all of them starts as soon as it finishes/);
 assert.match(text(waiting),/You can keep answering/);
 // An uncertain Planner delivery still holds the composer.
 await composer.sendDraftMessage({...drafting,planner_delivery:{state:'UNCERTAIN',retryable:false}});
 assert.equal(posted.length,1);
 console.log('Draft cause lines, coalesced and stalled waiting states, sending while a draft runs, and legacy fallback passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
