// Disposable browser verification of the shipped persistent-chat layout.
// No provider requests, real workspace writes, or runner launches.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {spawn,execFileSync}=require('node:child_process');
const root=path.resolve(__dirname,'../../..');
const evidence=process.env.DASHBOARD_CHAT_EVIDENCE_DIR||path.join(root,'.autocode','evidence','persistent-chat','run-'+process.pid);
fs.mkdirSync(evidence,{recursive:true});
const session='autocode-persistent-chat-'+process.pid;
const python=process.env.AUTOCODE_TEST_PYTHON||path.join(root,'.venv','bin','python');
const server=spawn(python,[path.join(__dirname,'unified_browser_fixture.py')],{cwd:root,env:{...process.env,AUTOCODE_FIXTURE_ROOT:path.join(evidence,'fixture')},stdio:['ignore','pipe','pipe']});
// Observe the original designated click's returned transport only. No retry,
// extra browser command or substitution of a CLI response for a DOM event.
const approvalClickTransport=[];
const browser=(...args)=>{
 const observe=args.length===2&&args[0]==='click'&&args[1]==='#brief-current .brief-approval > button',started=observe?performance.now():null;
 const retain=(stdout,error)=>{
  if(!observe)return;
  try{
   const bounded=value=>typeof value==='string'?value.slice(0,2048):Buffer.isBuffer(value)?value.toString('utf8').slice(0,2048):null;
   const row={command:args[0],selector:args[1],session,startedAt:started,returnedAt:performance.now(),normalReturn:!error,stdout:bounded(stdout),stdoutTruncated:typeof stdout==='string'?stdout.length>2048:Buffer.isBuffer(stdout)?stdout.toString('utf8').length>2048:null,error:error?{name:typeof error.name==='string'?error.name.slice(0,64):null,code:typeof error.code==='string'?error.code.slice(0,64):null,status:typeof error.status==='number'?error.status:null,signal:typeof error.signal==='string'?error.signal.slice(0,64):null,stderr:bounded(error.stderr)}:null};
   row.elapsedMs=row.returnedAt-row.startedAt;if(approvalClickTransport.length>=2)approvalClickTransport.shift();approvalClickTransport.push(row);
  }catch{}
 };
 try{const stdout=execFileSync('agent-browser',['--session',session,...args],{cwd:root,encoding:'utf8',timeout:45000});retain(stdout,null);return stdout;}
 catch(error){retain(error?.stdout,error);throw error;}
};
const evaluate=script=>JSON.parse(JSON.parse(browser('eval','JSON.stringify(('+script+')())').trim()));
// Project identity must be the label actually rendered in this viewport:
// nonzero geometry and a visible computed style come before readability.
const measureIdentity=selector=>evaluate(`()=>{const e=document.querySelector(${JSON.stringify(selector)}),r=e.getBoundingClientRect(),style=getComputedStyle(e);return {selector:${JSON.stringify(selector)},text:e.textContent,top:r.top,bottom:r.bottom,left:r.left,right:r.right,rectWidth:r.width,rectHeight:r.height,scroll:e.scrollWidth,width:e.clientWidth,display:style.display,visibility:style.visibility,visible:e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})};}`);
const assertVisibleIdentity=(identity,label)=>{
 assert.ok(identity.visible&&identity.display!=='none'&&identity.visibility==='visible'&&identity.rectWidth>0&&identity.rectHeight>0&&identity.width>0,label+' project identity '+identity.selector+' is not rendered: '+JSON.stringify(identity));
 assert.match(identity.text,/Example project/i);
};
const ready=new Promise((resolve,reject)=>{let output='',errors='';server.stdout.on('data',chunk=>{output+=chunk;const line=output.split('\n').find(l=>l.startsWith('FIXTURE='));if(line)resolve(JSON.parse(line.slice(8)));});server.stderr.on('data',chunk=>errors+=chunk);server.on('error',reject);server.on('exit',code=>reject(Error('Fixture exited '+code+': '+errors)));});
(async()=>{
 const fixture=await ready,results=[],textResize=[],navigation=[],attachedDrafts=[];
 for(const [label,width,height] of [['desktop',1440,1024],['tablet',1024,768],['mobile',390,844],['narrow',320,844]]){
  browser('set','viewport',String(width),String(height));
  for(const state of ['plan','running','pending-answer']){
   browser('open',fixture.scenarios[state]);
   browser('wait','--fn','typeof latestRun!=="undefined" && latestRun!==null && !taskReadError');
   evaluate('()=>{applyTheme("light");return true}');
   // At phone widths the approved M3 layout reaches the artifact pane through
   // the details drawer; the pane overlays the chat instead of stacking under
   // a capped transcript. Open it for the pane measurement, then close it so
   // the screenshot captures the chat-first narrow layout.
   if(width<760){browser('click','#details-drawer-toggle');browser('wait','--fn','document.querySelector(".app-shell").classList.contains("details-open")');}
   const snapshot=evaluate(`()=>{const rect=id=>{const r=document.querySelector(id).getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height,b:r.bottom,right:r.right};};return {width:innerWidth,height:innerHeight,tab:currentTab,chat:rect('#interview'),composer:rect('#change-text'),pane:rect('#context-pane'),approve:document.querySelector('#inline-task-action').innerText,overflow:document.documentElement.scrollWidth>innerWidth,conversationId:document.querySelector('#conversation').dataset.conversationId};}`);
   if(width<760)browser('click','#details-drawer-close');
   assert.equal(snapshot.width,width);assert.equal(snapshot.height,height);assert.equal(snapshot.tab,'now');assert.equal(snapshot.overflow,false,label+' '+state+' horizontal overflow');
   assert.ok(snapshot.chat.h>=100,label+' '+state+' chat retains useful height');assert.ok(snapshot.composer.h>=44,label+' '+state+' composer touch size');
   assert.ok(snapshot.composer.right<=width+1&&snapshot.composer.b<=height,label+' '+state+' composer stays in viewport');
   if(state==='plan')assert.match(snapshot.approve,/Approve & build revision 7/);
   assert.ok(snapshot.pane.w>0,label+' '+state+' artifact pane stays reachable beside chat');
   const image=path.join(evidence,label+'-'+state+'.png');browser('screenshot',image);results.push({label,state,...snapshot,image});
  }
 }
 // Follow the shipped cross-pane links with real browser clicks. These links
 // inspect artifacts or focus chat; they must never submit a human action.
 const questionNavigationSetup=()=>{
  const rows=[],limit=40,types=['pointerdown','pointerup','click','scroll'];let observer=null,finished=null;
  const node=e=>({tag:e?.tagName||null,id:e?.id||null,question:e?.closest?.('[data-question-id]')?.dataset.questionId||null});
  const snapshot=()=>{
   const button=document.querySelector('[data-question-id="question-2"] .work-question-link'),r=button?.getBoundingClientRect();
   return {question:document.querySelector('#question-target')?.value,drawer:document.querySelector('.app-shell')?.classList.contains('details-open'),active:node(document.activeElement),buttonConnected:button?.isConnected??null,buttonDisabled:button?.disabled??null,buttonRect:r?{x:r.x,y:r.y,width:r.width,height:r.height}:null,centerHit:r?node(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)):null};
  };
  const record=(phase,event)=>{if(rows.length===limit)rows.shift();rows.push({phase,type:event?.type||null,target:node(event?.target),composedPath:event?.composedPath?.().slice(0,12).map(node)??null,pointerHit:event&&Number.isFinite(event.clientX)&&Number.isFinite(event.clientY)?node(document.elementFromPoint(event.clientX,event.clientY)):null,x:event?.clientX??null,y:event?.clientY??null,at:performance.now(),...snapshot()});};
  const capture=event=>record('capture',event),bubble=event=>record('bubble',event);
  types.forEach(type=>{document.addEventListener(type,capture,true);if(type!=='scroll')document.addEventListener(type,bubble);});
  observer=new MutationObserver(records=>{if(records.some(row=>row.type==='childList'))record('Work DOM replaced');});
  observer.observe(document.querySelector('#now'),{childList:true,subtree:true});record('before original click');
  window.p4QuestionNavigationDiagnostic={finish(){
   if(finished)return finished;
   if(observer.takeRecords().some(row=>row.type==='childList'))record('pending Work DOM replacement');
   record('after original click');observer.disconnect();
   types.forEach(type=>{document.removeEventListener(type,capture,true);if(type!=='scroll')document.removeEventListener(type,bubble);});
   return finished={rows,passiveOnly:true};
  }};
 };
 const navigationState=(questionSetup=false)=>evaluate(`()=>{
  ${questionSetup?'('+questionNavigationSetup.toString()+')();':''}
  const visible=e=>{if(!e||e.hidden)return false;const r=e.getBoundingClientRect(),css=getComputedStyle(e);return r.width>0&&r.height>0&&r.right>0&&r.left<innerWidth&&r.bottom>0&&r.top<innerHeight&&css.visibility==='visible'&&css.display!=='none';};
  const exact=[...document.querySelectorAll('[data-focus-key]')].find(e=>e.dataset.focusKey==='approve:'+latestRun.run+':'+latestRun.goal_token);
  const question=document.querySelector('[data-question-card="question-2"]'),input=document.querySelector('#change-text');
  return {approvalDiagnostic:window.p4ApprovalNavigationDiagnostic?.finish()??null,questionDiagnostic:${questionSetup} ? null : window.p4QuestionNavigationDiagnostic?.finish()??null,tab:currentTab,drawer:document.querySelector('.app-shell').classList.contains('details-open'),inert:document.querySelector('.conversation-column').inert,paneVisible:visible(document.querySelector('#context-pane')),selectedPaneVisible:visible(document.querySelector('#'+currentTab)),planVisible:visible(document.querySelector('#brief-current')),legacyRailHidden:document.querySelector('#goal').hidden,legacyRailFocused:document.activeElement===document.querySelector('#goal'),revision:latestRun.goal?.revision,approval:latestRun.goal?.approval_status,approvalEvent:!!latestRun.goal?.approval_event,approvalOptionsOpen:exact?.closest('details')?.open??null,approvalFocused:document.activeElement===exact,question:document.querySelector('#question-target').value,questionVisible:visible(question),composerFocused:document.activeElement===input,draft:input.value,savedDraft:stored('task-draft:'+latestRun.run),pending:taskActionBusy(latestRun)||taskChatPending.has(latestRun.run),humanRequests:performance.getEntriesByType('resource').filter(e=>/\\/api\\/(?:action|chat|checkpoint\\/restore|conversations)(?:[/?]|$)/.test(new URL(e.name).pathname)).length};
 }`);
 const refreshed=()=>{evaluate('()=>{window.navigationRefreshDone=false;refresh().finally(()=>{window.navigationRefreshDone=true;});return true}');browser('wait','--fn','window.navigationRefreshDone && !refreshPromise');};
 const openNavigationFixture=(state,label)=>{const url=new URL(fixture.scenarios[state]);url.searchParams.set('navigation_fixture_viewport',label);url.searchParams.set('navigation_fixture_scenario',state);browser('open',url.toString());browser('wait','--fn','typeof latestRun!=="undefined" && latestRun?.run?.endsWith('+JSON.stringify('/'+state)+') && chosen?.run===latestRun.run && !taskReadError && !refreshPromise');};
 // Passive diagnostics use the existing pre-click text read, never an extra
 // browser operation or a replacement production event handler.
 const approvalNavigationSetup=`
  let observer=null,capture=null,bubble=null,toggle=null,scriptError=null,rejection=null;
  const typeOnly=error=>{try{return typeof error?.name==='string'?error.name.slice(0,64):'UnknownDiagnosticError';}catch{return 'UnknownDiagnosticError';}};
  const cleanup=()=>{
   const failures=[];
   for(const remove of [()=>observer?.disconnect(),...['pointerdown','pointerup','click'].map(type=>()=>document.removeEventListener(type,capture,true)),...['pointerdown','pointerup','click'].map(type=>()=>document.removeEventListener(type,bubble)),()=>document.removeEventListener('toggle',toggle,true),()=>window.removeEventListener('error',scriptError),()=>window.removeEventListener('unhandledrejection',rejection)]){
    try{remove();}catch(error){failures.push(typeOnly(error));}
   }
   return failures;
  };
  try{
   const limits={capture:8,bubble:8,toggle:4,error:4,mutation:16},buckets={capture:[],bubble:[],toggle:[],error:[],mutation:[]},overwritten={capture:0,bubble:0,toggle:0,error:0,mutation:0};let sequence=0,result=null;
   const key=e=>{try{return typeof e?.dataset?.focusKey==='string'?e.dataset.focusKey:null;}catch{return null;}};
   const nodeInfo=element=>({tag:element?.tagName||null,id:typeof element?.id==='string'?element.id.slice(0,128):null,key:key(element),connected:element?.isConnected??null});
   const rect=element=>{const value=element.getBoundingClientRect();return {x:value.x,y:value.y,width:value.width,height:value.height,left:value.left,top:value.top,right:value.right,bottom:value.bottom};};
   const hit=(x,y)=>Number.isFinite(x)&&Number.isFinite(y)?nodeInfo(document.elementFromPoint(x,y)):null;
   const geometry=element=>{
    if(!element)return null;
    const box=rect(element),style=getComputedStyle(element),x=box.x+box.width/2,y=box.y+box.height/2,ancestors=[];let parent=element.parentElement;
    while(parent&&ancestors.length<12){const css=getComputedStyle(parent);ancestors.push({...nodeInfo(parent),rect:rect(parent),hidden:parent.hidden,disabled:parent.disabled??null,inert:parent.inert??null,detailsOpen:parent.tagName==='DETAILS'?parent.open:null,overflowX:css.overflowX,overflowY:css.overflowY,display:css.display,visibility:css.visibility,pointerEvents:css.pointerEvents,scrollLeft:parent.scrollLeft,scrollTop:parent.scrollTop,clientWidth:parent.clientWidth,clientHeight:parent.clientHeight});parent=parent.parentElement;}
    return {rect:box,rectCount:element.getClientRects().length,display:style.display,visibility:style.visibility,pointerEvents:style.pointerEvents,position:style.position,zIndex:style.zIndex,hidden:element.hidden,disabled:element.disabled??null,inert:element.inert??null,onclickPresent:typeof element.onclick==='function',centre:{x,y,inViewport:x>=0&&y>=0&&x<innerWidth&&y<innerHeight,hit:hit(x,y)},ancestors,ancestorsTruncated:!!parent};
   };
   const runNow=()=>typeof latestRun!=='undefined'&&latestRun||{};
   const currentApproval=()=>{const run=runNow(),wanted=run.run&&run.goal_token?'approve:'+run.run+':'+run.goal_token:null;return wanted?[...document.querySelectorAll('[data-focus-key]')].find(e=>key(e)===wanted):null;};
   const initialPointer=document.querySelector('#brief-current .brief-approval > button'),initialApproval=currentApproval(),initialDetails=initialApproval?.closest('details');
   const snapshot=(includeGeometry=false)=>{
    try{
     const run=runNow(),approval=currentApproval(),details=approval?.closest('details'),pointer=document.querySelector('#brief-current .brief-approval > button'),identity=run.run+'\\0approval-only:'+run.goal_token;
     const approvals=[...document.querySelectorAll('[data-focus-key]')].filter(e=>key(e)?.startsWith('approve:')),shell=document.querySelector('.app-shell'),column=document.querySelector('.conversation-column');
     return {run:run.run||null,token:run.goal_token||null,chosenRun:typeof chosen!=='undefined'?chosen?.run||null:null,refreshing:typeof refreshPromise!=='undefined'&&!!refreshPromise,focusVersion:typeof focusVersion!=='undefined'?focusVersion:null,currentTab:typeof currentTab!=='undefined'?currentTab:null,drawer:!!shell?.classList.contains('details-open'),inert:column?.inert??null,pointerKey:key(pointer),pointerConnected:!!pointer?.isConnected,initialPointerConnected:!!initialPointer?.isConnected,pointerSameAsInitial:pointer===initialPointer,approvalKey:key(approval),approvalConnected:!!approval?.isConnected,approvalDisabled:approval?.disabled??null,approvalSameAsInitial:approval===initialApproval,initialApprovalConnected:!!initialApproval?.isConnected,detailsOpen:details?.open??null,detailsConnected:!!details?.isConnected,detailsSameAsInitial:details===initialDetails,initialDetailsConnected:!!initialDetails?.isConnected,detailsRun:details?.dataset.approvalRun||null,detailsToken:details?.dataset.approvalToken||null,savedDetailsPresent:typeof detailsState!=='undefined'&&detailsState.has(identity),savedDetailsOpen:typeof detailsState!=='undefined'?detailsState.get(identity)??null:null,activeKey:key(document.activeElement),approvalCount:approvals.length,approvalKeys:approvals.slice(0,4).map(key),...(includeGeometry?{pointerSelectorCount:document.querySelectorAll('#brief-current .brief-approval > button').length,viewport:{width:innerWidth,height:innerHeight,devicePixelRatio,visualViewport:window.visualViewport?{width:visualViewport.width,height:visualViewport.height,offsetLeft:visualViewport.offsetLeft,offsetTop:visualViewport.offsetTop,scale:visualViewport.scale}:null},pointerGeometry:geometry(pointer),approvalGeometry:geometry(approval)}:{})};
    }catch(error){return {diagnosticFailureType:typeOnly(error)};}
   };
   const push=(bucket,entry)=>{try{const rows=buckets[bucket];if(rows.length>=limits[bucket]){rows.shift();overwritten[bucket]++;}rows.push(entry);}catch{}};
   const diagnosticFailure=error=>{try{push('error',{kind:'diagnostic-failure',sequence:++sequence,failureType:typeOnly(error)});}catch{}};
   const record=(bucket,kind,event,mutation,failure)=>{
    try{push(bucket,{kind,sequence:++sequence,at:performance.now(),targetKey:key(event?.target),targetConnected:event?.target?.isConnected??null,targetTag:event?.target?.tagName||null,eventType:typeof event?.type==='string'?event.type:null,isTrusted:event?.isTrusted??null,eventPhase:event?.eventPhase??null,defaultPrevented:event?.defaultPrevented??null,button:event?.button??null,buttons:event?.buttons??null,clientX:event?.clientX??null,clientY:event?.clientY??null,pointerType:event?.pointerType??null,pointerId:event?.pointerId??null,closestKey:key(event?.target?.closest?.('[data-focus-key]')),composedPath:typeof event?.composedPath==='function'?event.composedPath().slice(0,8).map(nodeInfo):null,composedPathTruncated:typeof event?.composedPath==='function'?event.composedPath().length>8:null,coordinateHit:hit(event?.clientX,event?.clientY),toggleOpen:event?.target?.tagName==='DETAILS'?event.target.open:null,mutation:mutation||null,failure:failure||null,snapshot:snapshot()});}catch(error){diagnosticFailure(error);}
   };
   // A wrong hit target is evidence too. Observe every input in this narrow
   // pre-click-to-finish window without canceling or replacing any handler.
   capture=event=>{try{record('capture','approval-input-capture',event);}catch(error){diagnosticFailure(error);}};
   bubble=event=>{try{record('bubble','approval-input-bubble',event);}catch(error){diagnosticFailure(error);}};
   toggle=event=>{try{if(event.target?.dataset?.approvalOptions==='true')record('toggle','approval-native-toggle',event);}catch(error){diagnosticFailure(error);}};
   const errorInfo=(value,fallback)=>{try{return {name:typeof value?.name==='string'?value.name.slice(0,64):'Error',message:typeof value?.message==='string'?value.message.slice(0,256):typeof fallback==='string'?fallback.slice(0,256):'Unspecified script error'};}catch(error){return {diagnosticFailureType:typeOnly(error)};}};
   scriptError=event=>{try{record('error','window-error',null,null,errorInfo(event.error,event.message));}catch(error){diagnosticFailure(error);}};
   rejection=event=>{try{record('error','window-unhandledrejection',null,null,errorInfo(event.reason,'Non-Error rejection ('+typeof event.reason+')'));}catch(error){diagnosticFailure(error);}};
   const mutations=(changes,kind)=>{
    try{if(!changes.length)return;record('mutation',kind,null,{count:changes.length,openChanges:[...changes].filter(change=>change.type==='attributes'&&change.attributeName==='open').slice(-4).map(change=>({oldValue:change.oldValue,connected:!!change.target.isConnected,open:change.target.open,run:change.target.dataset.approvalRun||null,token:change.target.dataset.approvalToken||null})),childChanges:[...changes].filter(change=>change.type==='childList').length});}catch(error){diagnosticFailure(error);}
   };
   for(const type of ['pointerdown','pointerup','click']){document.addEventListener(type,capture,{capture:true,passive:true});document.addEventListener(type,bubble,{passive:true});}document.addEventListener('toggle',toggle,{capture:true,passive:true});
   window.addEventListener('error',scriptError,{passive:true});window.addEventListener('unhandledrejection',rejection,{passive:true});
   observer=new MutationObserver(changes=>mutations(changes,'approval-or-plan-DOM-change'));
   observer.observe(document.querySelector('#inline-task-action'),{childList:true,subtree:true,attributes:true,attributeFilter:['open'],attributeOldValue:true});observer.observe(document.querySelector('#brief-current'),{childList:true,subtree:true});
   const initial=snapshot(true);
   window.p4ApprovalNavigationDiagnostic={finish(){
    if(result)return result;
    let final,cleanupFailureTypes;
    try{mutations(observer.takeRecords(),'pending-approval-or-plan-DOM-change');final=snapshot(true);}catch(error){diagnosticFailure(error);final={diagnosticFailureType:typeOnly(error)};}finally{cleanupFailureTypes=cleanup();}
    try{result={initial,records:Object.values(buckets).flat().sort((a,b)=>a.sequence-b.sequence),overwritten,final,cleanupFailureTypes,passiveOnly:true};}catch(error){result={diagnosticFailureType:typeOnly(error),cleanupFailureTypes,passiveOnly:true};}
    return result;
   }};
  }catch(error){
   const failureType=typeOnly(error),cleanupFailureTypes=cleanup();
   try{window.p4ApprovalNavigationDiagnostic={finish(){return {diagnosticFailureType:failureType,cleanupFailureTypes,passiveOnly:true};}};}catch{}
  }
 `;
 const saveApprovalNavigationDiagnostic=(label,diagnostic)=>{
  try{fs.writeFileSync(path.join(evidence,label+'-approval-navigation-diagnostic.json'),JSON.stringify({label,diagnostic,clickTransport:approvalClickTransport.at(-1)||null},null,2)+'\n');}
  catch(error){let type='UnknownDiagnosticError';try{if(typeof error?.name==='string')type=error.name.slice(0,64);}catch{}try{process.stderr.write('Approval navigation diagnostic write failed: '+type+'\n');}catch{}}
 };
 for(const [label,width,height] of [['desktop',1440,1024],['mobile',390,844]]){
  browser('set','viewport',String(width),String(height));
  openNavigationFixture('plan',label);
  const before=navigationState();assert.equal(before.drawer,false);assert.equal(before.inert,false);
  assert.equal(evaluate('()=>document.querySelector("#inline-task-action .lifecycle-actions > button:first-child").textContent'),'View plan');
  browser('click','#inline-task-action .lifecycle-actions > button:first-child');
  const plan=navigationState();assert.equal(plan.tab,'plan');assert.equal(plan.paneVisible,true);assert.equal(plan.planVisible,true);assert.equal(plan.drawer,width<760);assert.equal(plan.inert,width<760);
  // Plan approval is in a collapsed chat disclosure. Open only the Plan's
  // read-only details, then follow its pointer to the exact chat control.
  assert.equal(plan.approvalOptionsOpen,false);
  browser('click','#brief-current .plan-document > details:last-of-type > summary');
  assert.equal(evaluate('()=>{'+approvalNavigationSetup+'return document.querySelector("#brief-current .brief-approval > button").textContent;/* approval diagnostic setup: preserve original text read */}'),'Review in conversation →');
  browser('click','#brief-current .brief-approval > button');
  const review=navigationState();saveApprovalNavigationDiagnostic(label,review.approvalDiagnostic);assert.equal(review.drawer,false);assert.equal(review.inert,false);assert.equal(review.approvalOptionsOpen,true);assert.equal(review.approvalFocused,true);assert.equal(review.approval,'draft');assert.equal(review.approvalEvent,false);assert.equal(review.pending,false);assert.equal(review.humanRequests,before.humanRequests);
  refreshed();const reviewedAgain=navigationState();assert.equal(reviewedAgain.drawer,false);assert.equal(reviewedAgain.approvalOptionsOpen,true);assert.equal(reviewedAgain.approvalFocused,true);assert.equal(reviewedAgain.humanRequests,before.humanRequests);
  // History selects the actual Plan pane at desktop as well as mobile; the
  // retired hidden plan rail must never receive focus.
  if(width<760)browser('click','#details-drawer-toggle');
  browser('click','[data-tab="overview"]');
  assert.equal(evaluate('()=>{const links=document.querySelectorAll("#task-overview .lifecycle-card > button.text-button");return links.length===1?links[0].textContent:null;}'),'View reviewed plan');
  browser('click','#task-overview .lifecycle-card > button.text-button');
  const history=navigationState();assert.equal(history.tab,'plan');assert.equal(history.planVisible,true);assert.equal(history.revision,7);assert.equal(history.legacyRailHidden,true);assert.equal(history.legacyRailFocused,false);assert.equal(history.humanRequests,before.humanRequests);
  openNavigationFixture('pending-answer',label);
  const unsent='Keep this unsent navigation answer';browser('fill','#change-text',unsent);const questionBefore=navigationState(true);
  if(width<760)browser('click','#details-drawer-toggle');
  browser('click','[data-question-id="question-2"] .work-question-link');
  const question=navigationState();fs.writeFileSync(path.join(evidence,label+'-question-navigation-diagnostic.json'),JSON.stringify(question.questionDiagnostic,null,2)+'\n');assert.equal(question.drawer,false);assert.equal(question.inert,false);assert.equal(question.tab,'now');assert.equal(question.question,'question-2');assert.equal(question.questionVisible,true);assert.equal(question.composerFocused,true);assert.equal(question.draft,unsent);assert.equal(question.savedDraft,unsent);assert.equal(question.humanRequests,questionBefore.humanRequests);
  refreshed();const questionAgain=navigationState();assert.equal(questionAgain.drawer,false);assert.equal(questionAgain.question,'question-2');assert.equal(questionAgain.composerFocused,true);assert.equal(questionAgain.draft,unsent);assert.equal(questionAgain.savedDraft,unsent);assert.equal(questionAgain.humanRequests,questionBefore.humanRequests);
  openNavigationFixture('completed',label);const completedBefore=navigationState(),completed=[];
  for(const [index,tab,text] of [[1,'preview','Open preview'],[2,'changes','View changes']]){
   const selector='#conversation .chat-ready .lifecycle-actions > button:nth-child('+index+')';
   assert.equal(evaluate('()=>document.querySelector('+JSON.stringify(selector)+').textContent'),text);browser('click',selector);
   const artifact=navigationState();assert.equal(artifact.tab,tab);assert.equal(artifact.paneVisible,true);assert.equal(artifact.selectedPaneVisible,true);assert.equal(artifact.drawer,width<760);assert.equal(artifact.humanRequests,completedBefore.humanRequests);completed.push({tab,visible:artifact.selectedPaneVisible,drawer:artifact.drawer});
   if(width<760)browser('click','#details-drawer-close');
  }
  navigation.push({label,width,height,plan,review,history,question,completed});
  // A synthetic draft travels through the real handoff and journal projection.
  // This exercises attached task presentation, without a provider or approval.
  openNavigationFixture('flow-attached-draft',label);
  const attachedBefore=navigationState();
  if(width<760)browser('click','#details-drawer-toggle');
  browser('click','[data-tab="overview"]');
  assert.equal(evaluate('()=>{const links=document.querySelectorAll("#task-overview .lifecycle-card > button.text-button");return links.length===1?links[0].textContent:null;}'),'Read draft in plan rail');
  browser('click','#task-overview .lifecycle-card > button.text-button');
  const readAttachedDraft=()=>evaluate('()=>({text:document.querySelector("#brief-current").innerText,buttons:document.querySelector("#brief-current").querySelectorAll("button").length,goalBody:!!latestRun.goal?.body,gate:latestRun.conversation.plan_gate,attachment:latestRun.conversation.attachment,attribution:latestRun.conversation.plan_drafts[0].attribution})');
  const attached=readAttachedDraft(),attachedPane=navigationState();
  assert.equal(attachedPane.tab,'plan');assert.equal(attachedPane.planVisible,true);assert.equal(attachedPane.drawer,width<760);assert.equal(attachedPane.legacyRailFocused,false);assert.equal(attachedPane.humanRequests,attachedBefore.humanRequests);
  assert.match(attached.text,/Draft the attached conversation before independent review/);assert.match(attached.text,/Keep human actions in this conversation/);assert.match(attached.text,/Preserve project-scoped saved work/);assert.match(attached.text,/Which existing behaviors must remain unchanged/);assert.match(attached.text,/Draft only\. Independent plan review and your approval are required/);assert.doesNotMatch(attached.text,/No saved plan yet|Approved contract/);
  assert.ok(attached.text.includes('Planner · '+attached.attribution.model+' · '+attached.attribution.reasoning_effort));assert.equal(attached.buttons,0);assert.equal(attached.goalBody,false);assert.equal(attached.gate.architect_reviewed,false);assert.equal(attached.gate.approved,false);assert.equal(attached.gate.token,null);assert.equal(attached.attachment.status,'linked');
  refreshed();assert.deepEqual(readAttachedDraft(),attached);const attachedAgain=navigationState();assert.equal(attachedAgain.tab,'plan');assert.equal(attachedAgain.planVisible,true);assert.equal(attachedAgain.drawer,width<760);assert.equal(attachedAgain.humanRequests,attachedBefore.humanRequests);
  attachedDrafts.push({label,width,height,...attached,visible:attachedAgain.planVisible,drawer:attachedAgain.drawer});
 }
 // Layout-only draft fixture exercises the rendered public conversation document
 // without contacting a provider or fabricating a planning receipt.
 const draftDocument = {"id": "local-browser-layout", "title": "A dashboard for every coding task", "status": "ready", "models": {"glm_model": "openai/gpt-6-sol"}, "messages": [{"id": "u1", "role": "user", "text": "Show and control every task in a single conversation.", "created_at": "2026-09-30T10:01:00Z"}, {"id": "a1", "role": "assistant", "speaker": "Requirements Gatherer", "model": "openai/gpt-6-sol", "reasoning_effort": "low", "text": "Should routine technical recovery run automatically, with permissions and product decisions returning to this chat?", "created_at": "2026-09-30T10:02:00Z"}, {"id": "u2", "role": "user", "text": "Yes. Keep me involved in product decisions.", "created_at": "2026-09-30T10:03:00Z"}], "plan_drafts": [{"revision": 3, "status": "current", "attribution": {"role":"planner","model":"openai/gpt-6-sol","reasoning_effort":"high"}, "goal": "Understand and control all coding work from one conversation.", "requirements": ["Monitor tasks", "Pause, resume and stop", "Automatically recover technical problems", "Ask for permissions", "Independent verification"], "milestones": [{"id": "M1", "objective": "Persistent conversation"}, {"id": "M2", "objective": "Live draft plan"}, {"id": "M3", "objective": "Review and approval gates"}], "parallelism": ["Chat and plan presentation"], "outstanding_questions": [{"question": "Should Stop accept an optional reason?"}], "freshness": {"state": "fresh", "updated_at": "2026-09-30T10:03:01Z"}}]};
 for(const [label,width,height] of [['desktop',1440,1024],['mobile',390,844]]){
  browser('set','viewport',String(width),String(height));
  browser('open',fixture.base_url);browser('wait','--fn','typeof latestData!=="undefined" && latestData!==null && !refreshPromise');
  for(const theme of ['light','dark']){
   evaluate('()=>{clearTimeout(refreshTimer);activeConversation="local-browser-layout";setView("draft-conversation");applyTheme('+JSON.stringify(theme)+');renderDraftConversation('+JSON.stringify(draftDocument)+');return true}');
   const draft=evaluate('()=>{const chat=document.querySelector("#draft-scroll").getBoundingClientRect(),composer=document.querySelector("#draft-text").getBoundingClientRect();return {chat:chat.height,composerBottom:composer.bottom,height:innerHeight,overflow:document.documentElement.scrollWidth>innerWidth,rail:document.querySelector("#quiet-plan").innerText};}');
   assert.ok(draft.chat>=100&&draft.composerBottom<=height,label+' draft leaves space for conversation and composer');assert.equal(draft.overflow,false);assert.match(draft.rail,/requirements/i);if(width>=1200)assert.match(draft.rail,/Planner · openai\/gpt-6-sol · high/,'Desktop draft shows its actual saved Planner attribution');
   if(width<1200){
    const question=evaluate('()=>{const element=document.querySelector(".compact-draft-question"),r=element.getBoundingClientRect();return {text:element.innerText,top:r.top,bottom:r.bottom,height:r.height};}');assert.match(question.text,/Should Stop accept an optional reason/);assert.ok(question.top>=0&&question.bottom<=height&&question.height>0,'Mobile open question is visible without scrolling');
    browser('click','.compact-draft-preview button');assert.equal(evaluate('()=>document.querySelector("#draft-plan-dialog").open'),true);assert.match(evaluate('()=>document.querySelector("#draft-plan-content").innerText'),/Should Stop accept an optional reason/);assert.match(evaluate('()=>document.querySelector("#draft-plan-content").innerText'),/Planner · openai\/gpt-6-sol · high/);browser('press','Escape');assert.equal(evaluate('()=>document.activeElement.textContent'),'View full draft plan','Closing plan returns focus to chat control');
   }
   browser('screenshot',path.join(evidence,label+'-draft-'+theme+'.png'));
  }
  browser('open',fixture.scenarios.plan);browser('wait','--fn','typeof latestRun!=="undefined" && latestRun?.run?.endsWith("/plan")');
  evaluate('()=>{applyTheme("dark");return true}');browser('screenshot',path.join(evidence,label+'-plan-dark.png'));
 }
 browser('set','viewport','320','844');
 // Permanent deletion uses the server preview for this fixture's completed task only.
 browser('open',fixture.scenarios.completed);browser('wait','--fn','typeof latestRun!=="undefined" && latestRun?.status==="TASK_COMPLETE" && latestRun?.run?.endsWith("/completed")');
 evaluate('()=>{document.querySelector("#task-detail .context-menu").open=true;document.querySelector("#delete-task").click();return true}');
 browser('wait','--fn','taskDeleteReview?.preview?.preview_id || !document.querySelector("#task-delete-error").hidden');
 const preview=evaluate('()=>({id:taskDeleteReview.preview?.preview_id,error:document.querySelector("#task-delete-error").textContent,scope:taskDeleteReview.preview?.scope,confirmDisabled:document.querySelector("#task-delete-confirm").disabled})');
 assert.ok(preview.id,'Deletion preview available: '+preview.error);assert.ok(preview.scope.length>0);assert.equal(preview.confirmDisabled,true);
 browser('screenshot',path.join(evidence,'narrow-delete-preview.png'));
 evaluate('()=>{document.querySelector("#task-delete-confirmation").value="wrong task";updateTaskDeleteConfirm();return true}');assert.equal(evaluate('()=>document.querySelector("#task-delete-confirm").disabled'),true);
 evaluate('()=>{document.querySelector("#task-delete-confirmation").value=taskDeleteReview.preview.confirmation;updateTaskDeleteConfirm();document.querySelector("#task-delete-confirm").click();return true}');
 browser('wait','--fn','!document.querySelector("#task-delete-dialog").open || !document.querySelector("#task-delete-error").hidden');
 assert.equal(evaluate('()=>document.querySelector("#task-delete-dialog").open'),false,'Deletion completed for the named disposable task');
 browser('open',fixture.scenarios.plan);browser('wait','--fn','typeof latestRun!=="undefined" && latestRun?.run?.endsWith("/plan")');
 // Changes of theme and tab remain available without losing the conversation.
 evaluate('()=>{applyTheme("dark");activateTab("plan");return true}');
 assert.match(evaluate('()=>document.querySelector("#brief-current").innerText'),/Plan revision/);
 evaluate('()=>{activateTab("interview");return true}');browser('screenshot',path.join(evidence,'narrow-dark-chat.png'));
 // Double actual computed text sizes in fresh documents (not page pixels).
 // Large approval controls may scroll; transcript and composer stay reachable.
 const assertProjectIdentityReadable=(identity,width,height)=>{
  assert.match(identity.text,/Example project/);assert.ok(identity.visible&&identity.width>0&&identity.height>0&&identity.scroll<=identity.width+1&&identity.diagnostic.title.scrollHeight<=identity.diagnostic.title.clientHeight+1&&identity.top>=0&&identity.bottom<=height&&identity.right<=width,"Project identity stays readable at 200% text");
 };
 for(const [label,width,height] of [['desktop',1440,1024],['mobile',390,844]]){
  browser('set','viewport',String(width),String(height));const url=new URL(fixture.scenarios.plan);url.searchParams.set('text_resize',label);browser('open',url.toString());browser('wait','--fn','typeof latestRun!=="undefined" && latestRun!==null && !taskReadError');
  evaluate('()=>{clearTimeout(refreshTimer);applyTheme("light");const sizes=[...document.querySelectorAll("body *")].map(e=>[e,getComputedStyle(e).fontSize]);for(const [e,size] of sizes)e.style.setProperty("font-size",(parseFloat(size)*2)+"px","important");return true}');
  const readIdentity=()=>evaluate(`()=>{
   const e=document.querySelector(innerWidth>=760?"#task-project":"#breadcrumb-title"),r=e.getBoundingClientRect(),css=getComputedStyle(e);
   const rect=r=>({top:r.top,bottom:r.bottom,left:r.left,right:r.right,width:r.width,height:r.height});
   const measure=node=>{if(!node)return null;const css=getComputedStyle(node);return {rect:rect(node.getBoundingClientRect()),scrollWidth:node.scrollWidth,clientWidth:node.clientWidth,scrollHeight:node.scrollHeight,clientHeight:node.clientHeight,styles:Object.fromEntries(["display","fontSize","lineHeight","fontFamily","fontWeight","whiteSpace","overflow","overflowX","overflowY","textOverflow","overflowWrap","width","minWidth","height","minHeight","flex","flexBasis","flexShrink","flexWrap","gap","paddingLeft","paddingRight","paddingTop","paddingBottom"].map(name=>[name,css[name]]))};};
   const range=document.createRange();range.selectNodeContents(e);
   return {selector:"#"+e.id,text:e.textContent,top:r.top,bottom:r.bottom,left:r.left,right:r.right,scroll:e.scrollWidth,width:e.clientWidth,height:r.height,visible:e.getClientRects().length>0&&r.width>0&&r.height>0&&css.display!=="none"&&css.visibility!=="hidden"&&css.visibility!=="collapse",diagnostic:{viewport:{width:innerWidth,height:innerHeight,devicePixelRatio,visualScale:visualViewport?.scale??null},fonts:document.fonts.status,title:measure(e),textRange:rect(range.getBoundingClientRect()),textLineRects:[...range.getClientRects()].map(rect),breadcrumb:measure(e.parentElement),topbar:measure(e.closest(".topbar")),projectsButton:measure(document.querySelector("#nav-toggle")),detailsButton:measure(document.querySelector("#details-drawer-toggle")),document:{scrollWidth:document.documentElement.scrollWidth,clientWidth:document.documentElement.clientWidth},predicates:{noIdentityHorizontalClipping:e.scrollWidth<=e.clientWidth+1,topInViewport:r.top>=0,bottomInViewport:r.bottom<=innerHeight,rightInViewport:r.right<=innerWidth}}};
  }`);
  const identity=readIdentity();
  fs.writeFileSync(path.join(evidence,label+'-text-200-identity.json'),JSON.stringify({label,width,height,identity},null,2));
  assertProjectIdentityReadable(identity,width,height);
  for(const mode of ['hidden','zero']){
   const savedStyle=evaluate('()=>document.querySelector(innerWidth>=760?"#task-project":"#breadcrumb-title").getAttribute("style")');
   try{
    evaluate(`()=>{const e=document.querySelector(innerWidth>=760?"#task-project":"#breadcrumb-title");if(${JSON.stringify(mode)}==="hidden")e.style.setProperty("display","none","important");else for(const [name,value] of Object.entries({display:"block",width:"0px",height:"0px","min-width":"0px","max-width":"0px","min-height":"0px","max-height":"0px",padding:"0px",border:"0px",overflow:"hidden",flex:"0 0 0px"}))e.style.setProperty(name,value,"important");return true}`);
    const rejected=readIdentity();
    assert.throws(()=>assertProjectIdentityReadable(rejected,width,height),/Project identity stays readable at 200% text/,label+' '+mode+' actual identity must fail readability');
   }finally{
    const restoredStyle=evaluate(`()=>{const e=document.querySelector(innerWidth>=760?"#task-project":"#breadcrumb-title"),saved=${JSON.stringify(savedStyle)};if(saved===null)e.removeAttribute("style");else e.setAttribute("style",saved);return e.getAttribute("style")}`);
    assert.equal(restoredStyle,savedStyle,label+' '+mode+' restores exact inline styles');
   }
   assertProjectIdentityReadable(readIdentity(),width,height);
  }
  const enlarged=evaluate('()=>({chat:document.querySelector("#interview").getBoundingClientRect().height,overflow:document.documentElement.scrollWidth>innerWidth})');assert.ok(enlarged.chat>=120,label+' enlarged text keeps readable transcript');assert.equal(enlarged.overflow,false);
  browser('screenshot',path.join(evidence,label+'-text-200.png'));
  evaluate('()=>{document.querySelector("#change-text").scrollIntoView({block:"center"});return true}');
  const composer=evaluate('()=>{const e=document.querySelector("#change-text"),r=e.getBoundingClientRect();return {top:r.top,bottom:r.bottom,height:r.height,viewport:innerHeight,scroll:e.scrollHeight,client:e.clientHeight};}');assert.ok(composer.top>=0&&composer.bottom<=height,'Enlarged composer is reachable');assert.ok(composer.scroll<=composer.client+2,'Empty composer placeholder fits after resize');
  browser('screenshot',path.join(evidence,label+'-text-200-composer.png'));textResize.push({label,...enlarged,composer,identity});
 }
 const errors=browser('errors').trim();assert.ok(!errors||/^No (?:page )?errors\.?$/i.test(errors),errors);
 const files=['dashboard_app.js','dashboard.css','dashboard.html'].map(file=>({file:'tools/dashboard/'+file,sha256:crypto.createHash('sha256').update(fs.readFileSync(path.join(root,'tools/dashboard',file))).digest('hex')}));
 fs.writeFileSync(path.join(evidence,'manifest.json'),JSON.stringify({files,results,navigation,attachedDrafts,textResize,errors},null,2));
 console.log('Persistent chat browser checks passed: 12 responsive states, live draft and approval in both themes, plus permanent deletion. '+evidence);
})().catch(error=>{try{if(approvalClickTransport.length)fs.writeFileSync(path.join(evidence,'approval-click-transport-diagnostic.json'),JSON.stringify({clickTransports:approvalClickTransport},null,2)+'\n');}catch{}console.error(error);process.exitCode=1;}).finally(()=>{try{browser('close');}catch{}server.kill('SIGTERM');});
