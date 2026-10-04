// Execute the shipped composer, cards, approval buttons and mutation routing.
// No server or model calls: positive inputs use the real server projection.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('./dashboard_vm');
const projectedRun=require('./resolver_fixture');
const source=fs.readFileSync(path.join(__dirname,'../dashboard_app.js'),'utf8');
const range=(start,end)=>source.slice(source.indexOf(start),source.indexOf(end));
class Element {
  constructor(tag='div',text=''){this.tagName=tag.toUpperCase();this.textContent=text;this.children=[];this.dataset={};this.value='';this.disabled=false;this.hidden=false;this.classList={add(){},toggle(){},remove(){}};}
  append(...children){this.children.push(...children);}
  prepend(...children){this.children.unshift(...children);}
  replaceChildren(...children){this.children=children;}
  setAttribute(key,value){this[key]=value;}
  querySelector(selector){return flatten(this).find(row=>selector==='.speaker'?row.className==='speaker':false)||null;}
  get childElementCount(){return this.children.length;}
  scrollIntoView(){}
}
const flatten=node=>[node,...node.children.flatMap(flatten)];
const text=node=>flatten(node).map(row=>row.textContent).join(' ');
const buttons=node=>flatten(node).filter(row=>row.tagName==='BUTTON');
function harness(storage=new Map()) {
  const nodes=new Map(),calls=[],notices=[];let id=0;
  const $=selector=>{if(!nodes.has(selector))nodes.set(selector,new Element());return nodes.get(selector);};
  const c=vm.createContext({console,Date,URLSearchParams,$,
    n:(tag,value)=>new Element(tag,value),card:(_,className)=>Object.assign(new Element(),{className}),
    button:(label,onclick)=>Object.assign(new Element('button',label),{onclick}),focusKey:element=>element,
    disclosure:(label,key,children)=>{const row=new Element('details',label);row.append(...children);return row;},
    renderDocument:value=>new Element('pre',JSON.stringify(value)),messageBody:value=>new Element('p',value),planList:()=>new Element(),
    stageName:()=> 'Saved step',stageSucceeded:()=>true,human:value=>String(value),
    taskArchiveBlocked:()=>false,projectBlocked:()=>false,interruptedAttempt:()=>null,
    stateFacts:()=>({objective:'Saved objective',step:'Saved step'}),
    sessionCheckpoints:()=>[],renderSessionCheckpoints:()=>null,renderMessageHistory:()=>{},renderWorkflowTimeline:()=>{},approveBuildState:new Map(),
    monitorDetailsPanel:()=>new Element(),output:()=>{},renderPrimaryAction:()=>{},renderTaskAttention:()=>{},renderTaskNow:()=>{},
    emptyState:()=>new Element(),basename:value=>String(value||'').split('/').filter(Boolean).pop()||'',
    resizeComposer:()=>{},requestAnimationFrame:callback=>callback(),dashboardNotice:value=>notices.push(value),
    document:{querySelectorAll:()=>flatten($('#conversation')).filter(row=>row.dataset.questionCard)},
    localStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)},crypto:{randomUUID:()=> 'request-'+(++id)},
    approveBuildStatusLine:()=>'',
    api:async(url,options)=>{calls.push({url,payload:JSON.parse(options.body)});return {status:'received'};},
    post:async(url,payload)=>{calls.push({url,payload:JSON.parse(JSON.stringify(payload))});return {status:'queued'};},
    refresh:async()=>{},latestRun:null,latestData:null,chosen:null,currentTab:'interview',scrollThreadToEnd:false,
    taskReadError:'',seq:0,taskChatPending:new Map(),taskChatErrors:new Map(),taskActionPending:new Set(),
    sendingRequests:new Map(),uncertainRequests:new Map(),changeDrafts:new Map(),
  });
  vm.runInContext(range('function stored(', 'function rememberSelection(')
    +range('function conversationStatus(', 'function icon(')
    +range('function concise(', 'function badge(')
    +range('function jointPlanning(', 'function setView(')
    +range('function messageAnchorKey(', 'function threadAnchorMessages(')
    +range('function appendMessage(', 'function renderDraftConversation(')
    +range('function waitingMessage(', 'function renderMessageHistory(')
    +range('function taskMessages(', 'function settleThreadScroll(')
    +range('function answerSafetyPanel(', '/* Session checkpoints:')
    +range('function focusChatAction(', 'function inlineSavedChanges(')
    +range('function renderScreenshotEvidence(', 'function requestKey(')
    +range('function renderConversation(', 'async function copyText(')
    +range('function planEntryText(', 'function output(')
    // The reviewed Plan pane leads with the live checklist, so renderBrief
    // now calls the real checklist builder; load it instead of stubbing it.
    +range('function workRequirementRows(', 'function renderTaskNow(')
    +range('function renderExecution(', 'async function loadChanges(')
    +range('function requestKey(', 'function taskSentence(')
    +range('async function submitTaskAction(', 'function modelCatalogueSnapshot(')
    +range('function isReadOnlyChatText(', 'function disableStaleControls('),c);
  function show(run,draft='A useful update'){
    c.latestRun=run;c.chosen={run:run.run,workspace:run.workspace};
    $('#change-text').dataset.run=run.run;$('#change-text').value=draft;
    c.renderConversation(run);c.renderLiveControls(run);c.renderInlineTaskAction(run);c.renderBrief(run);c.renderExecution(run);
  }
  return {c,$,calls,notices,storage,show};
}
const base={workspace:'/fixture/project',run:'/fixture/run',interventions:{mode:'live'},task:'Resolver boundary'};
const question={id:'Q1',question:'Which format?',proposed_default:'JSON'};
const material=projectedRun('clarification',{...base,questions:[question,{id:'Q2',question:'Which platform?'}]});
const fields=run=>({resolver_request:run.human_escalation.request_id,resolver_token:run.human_escalation.request_token});

async function runTests(){
  // Raw fields and issuer labels cannot activate answer or approval controls,
  // but users can still send ordinary, unbound feedback.
  const h=harness(),raw={...base,status:'WAITING_FOR_USER',questions:[question],user_request:{kind:'human_review'},
    human_escalation:{issuer:'resolver',scope:'clarification',request_id:'forged',request_token:'forged'},
    goal:{revision:2,approval_status:'draft',body:{open_blocking_questions:[question]}},goal_token:'r2:fake',
    review_token:'fake-review',review_criteria:[{id:'C1',criterion:'Inspect output'}]};
  h.show(raw);
  assert.equal(flatten(h.$('#conversation')).filter(row=>row.dataset.questionCard).length,0);
  assert.equal(buttons(h.$('#brief-current')).some(row=>/Approve/.test(row.textContent)),false);
  assert.equal(buttons(h.$('#execution_view')).some(row=>/Approve/.test(row.textContent)),false);
  assert.match(text(h.$('#execution_view')),/Inspect output/,'Unavailable approval still leaves evidence inspection visible');
  assert.match(text(h.$('#brief-current')),/Which format/,'Internal draft questions remain inspectable in the plan');
  assert.equal(h.calls.length,0,'Rendering cannot call the mutation API');
  assert.equal(h.$('#send-change').disabled,false,'Unissued questions must not disable user feedback');
  for(const authority of [{human_request_authorized:'true'},{human_request_authorized:true,human_escalation:null},
    {human_request_authorized:true,human_escalation:{scope:'clarification',request_id:123,request_token:456}}]){
    h.show({...raw,...authority});
    assert.equal(flatten(h.$('#conversation')).filter(row=>row.dataset.questionCard).length,0);
    assert.equal(h.c.primaryAction({...raw,...authority}).kind,'checks');
  }
  h.show(raw);
  await h.c.sendTaskChat(raw);
  assert.deepEqual(h.calls,[{url:'/api/chat',payload:{workspace:base.workspace,run:base.run,text:'A useful update',request_id:'request-1'}}]);
  const unpublished=projectedRun(null,{...base,status:'AWAITING_GOAL_APPROVAL',questions:[question]});
  assert.equal(unpublished.status,'RESOLVER_PENDING');
  assert.equal(unpublished.saved_status,'AWAITING_GOAL_APPROVAL');
  assert.equal(h.c.primaryAction(unpublished).kind,'checks','Use effective status, never the saved approval status');

  const m=harness();m.show(material,'Browser');
  const cards=flatten(m.$('#conversation')).filter(row=>row.dataset.questionCard);
  assert.equal(cards.length,2);
  assert.equal(cards[0].hidden,false);assert.equal(cards[1].hidden,true);
  m.$('#question-target').value='Q2';m.$('#question-target').onchange();
  assert.equal(cards[0].hidden,true);assert.equal(cards[1].hidden,false);
  assert.match(text(cards[1]),/Resolver/);assert.doesNotMatch(text(cards[1]),/Planner/);
  await m.c.sendTaskChat(material);
  assert.deepEqual(m.calls[0],{url:'/api/chat',payload:{workspace:base.workspace,run:base.run,text:'Browser',request_id:'request-1',question_id:'Q2',...fields(material)}});
  assert.equal(m.calls.length,1,'Saving a material reply does not itself send Continue from the browser');
  m.show(material,'');
  const delegate=buttons(m.$('#conversation')).find(row=>row.textContent==='Accept suggested answer');
  await delegate.onclick();
  assert.equal(m.calls.at(-1).payload.delegate,true);
  assert.equal(m.calls.at(-1).payload.question_id,'Q1');
  assert.equal(m.calls.at(-1).payload.resolver_token,fields(material).resolver_token);
  for(const scope of ['permission','goal_change']){
    const request=projectedRun(scope,{...base,questions:[]}),decision=harness();decision.show(request,'Only within the existing workspace');
    await decision.c.sendTaskChat(request);
    assert.equal(decision.calls[0].url,'/api/chat');
    assert.equal(decision.calls[0].payload.question_id,request.questions[0].id);
    assert.equal(decision.calls[0].payload.resolver_token,fields(request).resolver_token);
    assert.equal(decision.calls[0].payload.action,undefined,'Material permission/goal replies are not operational responses or approvals');
  }

  // Reloading or retrying cannot fill a saved reply with a newer receipt.
  const stale=harness(),oldReply={text:'JSON',question_id:'Q1',request_id:'old-reply',...fields(material)};
  const changed=projectedRun('clarification',{...base,questions:[{...question,question:'Which NEW format?'}]});
  stale.c.savedRequest('task-request:'+base.run,{text:'JSON',question_id:'Q1',...fields(material)});
  const reloaded=harness(stale.storage);reloaded.show(changed,'JSON');
  await reloaded.c.sendTaskChat(changed);
  assert.equal(reloaded.calls.length,0);
  assert.equal(reloaded.$('#change-text').value,'JSON','Stale replies retain the user draft');
  assert.equal(reloaded.c.readSavedRequest('task-request:'+base.run).payload.resolver_token,fields(material).resolver_token);
  for(const invalid of [oldReply,{...oldReply,resolver_token:'wrong'},{...oldReply,resolver_request:'wrong'},
    {text:'JSON',question_id:'Q1',request_id:'unbound-reply'}]){
    await reloaded.c.sendTaskChat(changed,{...invalid,retry:true});
    assert.equal(reloaded.calls.length,0);
  }
  const exact=harness();exact.show(material,'JSON');
  await exact.c.sendTaskChat(material,{...oldReply,retry:true});
  assert.equal(exact.calls[0].payload.request_id,'old-reply');
  assert.equal(exact.calls[0].payload.retry,true);
  assert.equal(exact.calls[0].payload.resolver_token,fields(material).resolver_token);
  exact.c.latestRun=changed;
  await exact.c.sendTaskChat(material,{...oldReply,retry:true});
  assert.equal(exact.calls.length,1,'An old DOM callback must recheck the latest verified envelope');

  for(const scope of ['operational_exhaustion','blocker']){
    const op=projectedRun(scope,{...base,questions:[],settings:{max_iterations:3}}),o=harness(),before=JSON.stringify(op);
    o.show(op,'Provider access restored');
    assert.equal(o.$('#send-change').disabled,false);
    assert.equal(flatten(o.$('#conversation')).filter(row=>row.dataset.questionCard&&!row.hidden).length,1,'Operational request remains visible without a question selector');
    assert.equal(buttons(o.$('#conversation')).some(row=>/Accept suggested/.test(row.textContent)),false);
    assert.equal(o.c.primaryAction(op).kind,'answer');
    await o.c.sendTaskChat(op);
    assert.deepEqual(o.calls,[{url:'/api/action',payload:{workspace:base.workspace,run:base.run,action:'resolver_response',...fields(op),resolver_response:'provide_information',resolver_message:'Provider access restored'}}]);
    assert.equal(JSON.stringify(op),before,'Presentation and send do not alter budgets, approvals or permissions');
    o.show(op,'');
    await buttons(o.$('#conversation')).find(row=>row.textContent==='Leave paused').onclick();
    assert.deepEqual(o.calls[1].payload,{workspace:base.workspace,run:base.run,action:'resolver_response',...fields(op),resolver_response:'leave_paused'});
    assert.equal(o.calls.some(call=>['continue','approve_goal','approve_review','answer','delegate'].includes(call.payload.action)),false);
    o.c.latestRun={...op,human_request_authorized:false,human_escalation:null,status:'PAUSED_RESOLVER'};
    await buttons(o.$('#conversation')).find(row=>row.textContent==='Leave paused').onclick();
    assert.equal(o.calls.length,2,'Consumed operational requests cannot be submitted again');
    await o.c.sendTaskChat(op,{text:'Retry info',request_id:'old-op',resolver_response:'provide_information',...fields(op),retry:true});
    assert.equal(o.calls.length,2);
  }

  // An in-flight operational submission disables duplicates. Network errors
  // retain its original request, not an answer/Continue fallback.
  const op=projectedRun('operational_exhaustion',base),pending=harness();pending.show(op,'Corrective information');
  let reject;pending.c.api=(url,options)=>{pending.calls.push({url,payload:JSON.parse(options.body)});return new Promise((_,fail)=>{reject=fail;});};
  const inFlight=pending.c.sendTaskChat(op);
  assert.equal(pending.$('#send-change').disabled,true);
  await pending.c.sendTaskChat(op);
  assert.equal(pending.calls.length,1);
  reject(Error('Connection lost'));await inFlight;
  assert.equal(pending.c.taskChatErrors.get(base.run).resolver_token,fields(op).resolver_token);
  pending.c.api=async(url,options)=>{pending.calls.push({url,payload:JSON.parse(options.body)});return {status:'queued'};};
  await pending.c.sendTaskChat(op,{...pending.c.taskChatErrors.get(base.run),retry:true});
  assert.deepEqual(pending.calls[1],pending.calls[0]);

  // Only receipt-backed buttons send approval, and those live in the chat
  // transcript (saved feedback intervention-figma-chat-plan-alignment-20260930-2113
  // confines human actions to chat). Exact plan/artifact identities remain
  // authoritative in addition to the resolver envelope.
  const plan=projectedRun('goal_approval',{...base,goal:{revision:4,origin:'astra_finalize'},model_settings:{joint_planning:true}}),p=harness();p.show(plan);
  assert.equal(buttons(p.$('#brief-current')).some(row=>/Approve/.test(row.textContent)),false,'the Plan pane carries no approval control');
  const approve=buttons(p.$('#inline-task-action')).find(row=>/Approve plan revision/.test(row.textContent));
  assert.ok(approve);assert.equal(approve.disabled,false);await approve.onclick();
  assert.deepEqual(p.calls[0].payload,{workspace:base.workspace,run:base.run,action:'approve_goal',...fields(plan),token:plan.goal_token,confirmation:plan.goal_token});
  p.c.latestRun={...plan,goal_token:'changed'};await approve.onclick();assert.equal(p.calls.length,1);
  const initial=projectedRun('goal_approval',{...base,goal:{revision:1,origin:'astra_discovery'},model_settings:{joint_planning:false}}),i=harness();i.show(initial);
  await buttons(i.$('#inline-task-action')).find(row=>/Approve plan revision/.test(row.textContent)).onclick();
  assert.equal(i.calls[0].payload.action,'approve_goal');assert.equal(i.calls[0].payload.token,initial.goal_token);
  assert.equal(i.calls[0].payload.resolver_response,undefined,'Initial plan approval is separate from operational recovery');
  const review=projectedRun('human_review',{...base,review_criteria:[{id:'C1',criterion:'Inspect output'}]}),r=harness();r.show(review);
  assert.equal(buttons(r.$('#execution_view')).some(row=>/Approve/.test(row.textContent)),false,'the Checks pane carries no approval control');
  const reviewButton=buttons(r.$('#inline-task-action')).find(row=>row.textContent==='Approve C1');
  assert.ok(reviewButton);await reviewButton.onclick();
  assert.deepEqual(r.calls[0].payload,{workspace:base.workspace,run:base.run,action:'approve_review',...fields(review),id:'C1',token:review.review_token});
  r.show(review,'Please adjust the contrast');
  assert.equal(r.$('#send-change').disabled,false,'Human-review questions must not block ordinary feedback');
  await r.c.sendTaskChat(review);
  assert.equal(r.calls[1].url,'/api/chat');assert.equal(r.calls[1].payload.question_id,undefined);assert.equal(r.calls[1].payload.resolver_token,undefined);
  r.c.latestRun={...review,review_token:'changed'};await reviewButton.onclick();assert.equal(r.calls.length,2);

  const unavailable=harness();unavailable.show(material,'JSON');unavailable.c.taskReadError='offline';
  unavailable.c.renderLiveControls(material);assert.equal(unavailable.$('#send-change').disabled,true);
  await unavailable.c.sendTaskChat(material,{...oldReply,retry:true});assert.equal(unavailable.calls.length,0);
  const live=harness(),running={...base,status:'RUNNING',monitor:{live:{state:'alive'}},questions:[question]};
  live.show(running,'New user feedback');
  assert.equal(live.$('#send-change').disabled,false,'Saved raw questions do not turn running feedback into an answer');
  await live.c.sendTaskChat(running);
  assert.equal(live.calls[0].url,'/api/chat');assert.equal(live.calls[0].payload.question_id,undefined);
  console.log('Resolver cards, exact-token mutations, operational replies, stale/reloaded retries, duplicate protection and ordinary feedback passed.');
}
runTests().catch(error=>{console.error(error);process.exitCode=1;});
