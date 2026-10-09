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
const browser=(...args)=>execFileSync('agent-browser',['--session',session,...args],{cwd:root,encoding:'utf8',timeout:45000});
const evaluate=script=>JSON.parse(JSON.parse(browser('eval','JSON.stringify(('+script+')())').trim()));
const ready=new Promise((resolve,reject)=>{let output='',errors='';server.stdout.on('data',chunk=>{output+=chunk;const line=output.split('\n').find(l=>l.startsWith('FIXTURE='));if(line)resolve(JSON.parse(line.slice(8)));});server.stderr.on('data',chunk=>errors+=chunk);server.on('error',reject);server.on('exit',code=>reject(Error('Fixture exited '+code+': '+errors)));});
(async()=>{
 const fixture=await ready,results=[],textResize=[];
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
 for(const [label,width,height] of [['desktop',1440,1024],['mobile',390,844]]){
  browser('set','viewport',String(width),String(height));const url=new URL(fixture.scenarios.plan);url.searchParams.set('text_resize',label);browser('open',url.toString());browser('wait','--fn','typeof latestRun!=="undefined" && latestRun!==null && !taskReadError');
  evaluate('()=>{clearTimeout(refreshTimer);applyTheme("light");const sizes=[...document.querySelectorAll("body *")].map(e=>[e,getComputedStyle(e).fontSize]);for(const [e,size] of sizes)e.style.setProperty("font-size",(parseFloat(size)*2)+"px","important");return true}');
  const identity=evaluate('()=>{const e=document.querySelector("#breadcrumb-title"),r=e.getBoundingClientRect();return {text:e.textContent,top:r.top,bottom:r.bottom,left:r.left,right:r.right,scroll:e.scrollWidth,width:e.clientWidth};}');assert.match(identity.text,/Example project/);assert.ok(identity.scroll<=identity.width+1&&identity.top>=0&&identity.bottom<=height&&identity.right<=width,"Project identity stays readable at 200% text");
  const enlarged=evaluate('()=>({chat:document.querySelector("#interview").getBoundingClientRect().height,overflow:document.documentElement.scrollWidth>innerWidth})');assert.ok(enlarged.chat>=120,label+' enlarged text keeps readable transcript');assert.equal(enlarged.overflow,false);
  browser('screenshot',path.join(evidence,label+'-text-200.png'));
  evaluate('()=>{document.querySelector("#change-text").scrollIntoView({block:"center"});return true}');
  const composer=evaluate('()=>{const e=document.querySelector("#change-text"),r=e.getBoundingClientRect();return {top:r.top,bottom:r.bottom,height:r.height,viewport:innerHeight,scroll:e.scrollHeight,client:e.clientHeight};}');assert.ok(composer.top>=0&&composer.bottom<=height,'Enlarged composer is reachable');assert.ok(composer.scroll<=composer.client+2,'Empty composer placeholder fits after resize');
  browser('screenshot',path.join(evidence,label+'-text-200-composer.png'));textResize.push({label,...enlarged,composer,identity});
 }
 const errors=browser('errors').trim();assert.ok(!errors||/^No (?:page )?errors\.?$/i.test(errors),errors);
 const files=['dashboard_app.js','dashboard.css','dashboard.html'].map(file=>({file:'tools/dashboard/'+file,sha256:crypto.createHash('sha256').update(fs.readFileSync(path.join(root,'tools/dashboard',file))).digest('hex')}));
 fs.writeFileSync(path.join(evidence,'manifest.json'),JSON.stringify({files,results,textResize,errors},null,2));
 console.log('Persistent chat browser checks passed: 12 responsive states, live draft and approval in both themes, plus permanent deletion. '+evidence);
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(()=>{try{browser('close');}catch{}server.kill('SIGTERM');});
