// Exercise the shipped refresh action and source-attributed draft renderer.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('./dashboard_vm');
const source=fs.readFileSync(path.join(__dirname,'../dashboard_app.js'),'utf8');
class Element{
 constructor(tag,text=''){this.tag=tag;this.textContent=text;this.children=[];this.dataset={};}
 append(...rows){this.children.push(...rows);}
 replaceChildren(...rows){this.children=rows;}
 get childElementCount(){return this.children.length;}
 setAttribute(key,value){this[key]=value;}
}
const all=node=>[node,...node.children.flatMap(all)],text=node=>all(node).map(row=>row.textContent).join(' ');
const calls=[],notices=[],pending=new Set();let finish;
const doc={id:'conversation-one',status:'ready',draft_update:{held:true,can_refresh:true,
 requirements_revision:2,logical_turn_id:'turn-two',answers_since_update:1,answers_per_update:3}};
const ctx=vm.createContext({n:(tag,value)=>new Element(tag,value),card:()=>new Element('div'),
 button:(label,onclick)=>Object.assign(new Element('button',label),{onclick}),
 conversationPending:pending,conversationArchiveBlocked:d=>!!d.archived_at,activeConversation:doc.id,
 seq:0,latestConversation:doc,sendDraftMessage(){},retryConversation(){},savedRequest:()=>({id:'stable-request'}),persist(){},
 renderDraftConversation(){},refresh(){},dashboardNotice:message=>notices.push(message),
 api:(url,options)=>{calls.push({url,body:JSON.parse(options.body)});return new Promise(resolve=>finish=resolve);},
 disclosure:(label,key,nodes)=>{const result=new Element('details',label);result.append(...nodes);return result;},
 renderDocument:value=>new Element('pre',JSON.stringify(value)),planEntryText:row=>row.objective||''});
vm.runInContext(source.slice(source.indexOf('function conversationDeliveryBlocked('),source.indexOf('function draftSendBlocked(')),ctx);
vm.runInContext(source.slice(source.indexOf('function draftUpdateCard('),source.indexOf('function renderDraftConversation(')),ctx);
vm.runInContext(source.slice(source.indexOf('function planDraftPreviewCard('),source.indexOf('function renderQuietPlanPreview(')),ctx);
(async()=>{
 const card=ctx.draftUpdateCard(doc);assert.match(text(card),/3 messages/);assert.match(text(card),/does not approve/);
 const click=all(card).find(row=>row.tag==='button').onclick;
 const first=click();await click();assert.equal(calls.length,1,'Double clicks share one pending request');
 assert.equal(calls[0].url,'/api/conversation/refresh-draft');
 assert.deepEqual(calls[0].body,{id:doc.id,requirements_revision:2,logical_turn_id:'turn-two',request_id:'stable-request'});
 assert.equal(all(ctx.draftUpdateCard(doc)).find(row=>row.tag==='button').disabled,true);
 finish({...doc,draft_update:{...doc.draft_update,held:false,can_refresh:false}});await first;
 assert.equal(pending.size,0);
 for(const changed of [{archived_at:'now'},{task_archived:true},{project_removed:true},{draft_update:{can_refresh:false}}]){
  const blocked={...doc,...changed};await ctx.refreshConversationDraft(blocked);assert.equal(calls.length,1);
 }
 const problem=new Element('div');
 const failed={...doc,draft_update:null,pending_dispatch:{state:'REPLY_COMMITTED',retryable:false},
  planner_delivery:{state:'SAFE_NOT_DISPATCHED',retryable:true},plan_drafts:[{status:'failed'}]};
 ctx.renderDraftDeliveryProblem(problem,failed,null);
 assert.equal(problem.hidden,false);assert.match(text(problem),/Retry draft update/);
 for(const delivery of [{state:'UNCERTAIN',retryable:false},{state:'RESULT_CAPTURED',retryable:false}]){
  ctx.renderDraftDeliveryProblem(problem,{...failed,planner_delivery:delivery},null);
  assert.equal(problem.hidden,false);assert.equal(all(problem).filter(row=>row.tag==='button').length,0);
  assert.match(text(problem),/not safe to repeat/);
 }
 ctx.renderDraftDeliveryProblem(problem,{...failed,archived_at:'now'},null);assert.equal(problem.hidden,true);
 ctx.renderDraftDeliveryProblem(problem,doc,null);assert.equal(problem.hidden,true);
 const draft={revision:2,goal:'Use a database',freshness:{state:'fresh',source_messages:[
  {message_id:'message-two',excerpt:'Use SQLite <script>'}]},requirements:['Persist messages']};
 const preview=ctx.planDraftPreviewCard(draft);
 assert.match(text(preview),/Updated from your messages/);assert.match(text(preview),/Use SQLite <script>/);
 assert.equal(all(preview).some(row=>row.tag==='script'),false);
 assert.match(text(preview),/Independent plan review and your approval are required/);
 assert.equal(all(preview).filter(row=>row.tag==='button').length,0,'Draft artifact cannot approve or launch implementation');
 console.log('Batched-draft chat refresh, exact revision, duplicate guards and human source attribution passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
