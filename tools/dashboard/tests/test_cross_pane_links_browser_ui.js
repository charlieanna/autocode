// Real browser acceptance of explicit links between the conversation and the
// artifact pane (#1054). At phone widths the pane is the Details drawer and the
// conversation is inert while it is open: a chat link must reveal the pane it
// selects, and a Work or Plan link must close the drawer and focus its exact
// chat target. The links only navigate; nothing is submitted, and ordinary
// polling never reopens the drawer or moves focus.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn,execFileSync}=require('node:child_process');
const {holdBackgroundRefreshExpression}=require('./browser_readiness');
const root=path.resolve(__dirname,'../../..'),session='cross-pane-'+process.pid;
const evidence=path.join(root,'.autocode/evidence/cross-pane-links','run-'+process.pid);fs.mkdirSync(evidence,{recursive:true});
const server=spawn(process.env.AUTOCODE_TEST_PYTHON||'python3',[path.join(__dirname,'unified_browser_fixture.py')],{cwd:root,env:{...process.env,AUTOCODE_FIXTURE_ROOT:path.join(evidence,'fixture')},stdio:['ignore','pipe','pipe']});
const browser=(...args)=>execFileSync('agent-browser',['--session',session,...args],{cwd:root,encoding:'utf8',timeout:45000});
const data=fn=>JSON.parse(JSON.parse(browser('eval','JSON.stringify(('+fn+')())').trim()));
const wait=expression=>browser('wait','--fn',expression);
const refresh=()=>browser('eval','(async()=>{await refresh();return true})()');
// Tag one button by its container and exact label, then drive it as a user
// would: a real pointer click, or keyboard focus and Enter. A source link in a
// collapsed pane section (the Plan's "Saved plan details") is expanded first,
// as the reader would.
const tag=(scope,label)=>{
 data('()=>{document.querySelectorAll("[data-cross-pane]").forEach(e=>delete e.dataset.crossPane);const b=[...document.querySelectorAll('+JSON.stringify(scope+' button')+')].find(e=>e.textContent.trim()==='+JSON.stringify(label)+');if(!b)throw Error("Missing "+'+JSON.stringify(label)+');for(let node=b.parentElement;node;node=node.parentElement)if(node.tagName==="DETAILS")node.open=true;b.dataset.crossPane="1";b.scrollIntoView({block:"center",behavior:"instant"});return true}');
 return '[data-cross-pane="1"]';
};
const click=(scope,label)=>{
 const selector=tag(scope,label);
 wait('(()=>{const e=document.querySelector('+JSON.stringify(selector)+');if(!e||e.disabled)return false;const r=e.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return e===hit||e.contains(hit)})()');
 browser('click',selector);
};
const press=(scope,label)=>{browser('focus',tag(scope,label));browser('press','Enter');};
const shell=()=>data(`()=>{
 const visible=selector=>{const e=document.querySelector(selector);if(!e)return false;const r=e.getBoundingClientRect(),s=getComputedStyle(e);return !e.hidden&&s.display!=="none"&&s.visibility!=="hidden"&&r.width>0&&r.height>0};
 const active=document.activeElement;
 return {tab:currentTab,open:document.querySelector(".app-shell").classList.contains("details-open"),
  chatInert:document.querySelector(".conversation-column").inert,pane:visible("#context-pane"),
  active:active===document.body?"body":active.id||active.dataset.focusKey||active.textContent.trim(),
  overflow:document.documentElement.scrollWidth>innerWidth,posts:window.__crossPanePosts.slice()}}`);
const watchPosts=()=>data('()=>{window.__crossPanePosts=[];if(!window.__crossPaneFetch){window.__crossPaneFetch=window.fetch;window.fetch=(url,options={})=>{if(String(options.method||"GET").toUpperCase()!=="GET")window.__crossPanePosts.push(String(url));return window.__crossPaneFetch(url,options);};}return true}');
const ready=new Promise((resolve,reject)=>{let output='',errors='';server.stdout.on('data',chunk=>{output+=chunk;const line=output.split('\n').find(line=>line.startsWith('FIXTURE='));if(line)resolve(JSON.parse(line.slice(8)));});server.stderr.on('data',chunk=>errors+=chunk);server.on('error',reject);server.on('exit',code=>reject(Error('Fixture exited '+code+': '+errors)));});

(async()=>{
 const fixture=await ready,results=[];
 const open=key=>{
  browser('open',fixture.scenarios[key]);
  wait('typeof latestRun!=="undefined"&&latestRun?.run?.endsWith("/'+key+'")&&!taskReadError');
  // The 2 s poll rebuilds the Plan pane and the chat approval card. Hold the
  // timer so a rebuild cannot detach a tagged control mid-click or undo a
  // check's synthetic disclosure or disabled state; every check runs
  // refresh(), the function that timer calls, explicitly instead.
  data(holdBackgroundRefreshExpression());refresh();
  watchPosts();
  const run=data('()=>latestRun.run');return {run,stateFile:path.join(run,'state.json'),before:fs.readFileSync(path.join(run,'state.json'))};
 };
 // The drawer and the conversation stay where the user left them through an
 // ordinary refresh, and so does focus.
 const steady=(viewport,label)=>{const before=shell();refresh();const after=shell();
  assert.deepEqual({tab:after.tab,open:after.open,chatInert:after.chatInert,active:after.active},{tab:before.tab,open:before.open,chatInert:before.chatInert,active:before.active},viewport+' ordinary refresh keeps the drawer and focus after '+label);return after;};
 for(const [viewport,width,height]of [['desktop',1440,1024],['tablet',1024,768],['mobile',390,844]]){
  browser('set','viewport',String(width),String(height));
  const phone=width<760;

  // Chat completion actions reveal the artifact they select.
  let task=open('completed');
  for(const [label,tab,pane]of [['Open preview','preview','#preview'],['View changes','changes','#changes']]){
   assert.equal(shell().open,false);
   click('#conversation .chat-ready',label);
   let state=shell();
   assert.equal(state.tab,tab,viewport+' '+label+' selects its pane');
   assert.equal(state.pane,true,viewport+' '+label+' leaves its pane visible');
   assert.equal(data('()=>{const r=document.querySelector('+JSON.stringify(pane)+').getBoundingClientRect();return r.width>0&&r.height>0}'),true,viewport+' '+label+' renders '+pane);
   assert.equal(state.open,phone,viewport+' '+label+' opens Details only at phone widths');
   if(phone){assert.equal(state.chatInert,true);assert.equal(state.active,'details-drawer-close','The opened drawer takes focus through its existing path');}
   browser('screenshot',path.join(evidence,viewport+'-chat-'+tab+'.png'));
   steady(viewport,label);
   if(phone){browser('click','#details-drawer-close');state=steady(viewport,'closing Details');assert.equal(state.open,false,'Polling never reopens a closed drawer');assert.equal(state.active,'details-drawer-toggle');}
  }
  assert.deepEqual(shell().posts,[],viewport+' chat-to-artifact links submit nothing');
  assert.deepEqual(fs.readFileSync(task.stateFile),task.before);

  // Plan: the chat's View plan reveals the plan; the plan's Review in
  // conversation closes Details and focuses the exact revision-bound approval.
  task=open('plan');
  const token=data('()=>latestRun.goal_token'),approveKey='approve:'+task.run+':'+token;
  click('#inline-task-action','View plan');
  let state=shell();
  assert.equal(state.tab,'plan');assert.equal(state.pane,true);assert.equal(state.open,phone);
  steady(viewport,'View plan');
  click('#brief-current','Review in conversation →');
  wait('document.activeElement?.dataset?.focusKey==='+JSON.stringify(approveKey));
  state=shell();
  assert.equal(state.open,false,viewport+' Review in conversation closes Details');
  assert.equal(state.chatInert,false,viewport+' the conversation is no longer inert');
  assert.equal(state.active,approveKey,viewport+' focus lands on the exact approval control');
  assert.equal(data('()=>document.activeElement.closest("#inline-task-action")!==null'),true,'The target is the chat control, not a pane copy');
  wait('(()=>{const e=document.activeElement,r=e.getBoundingClientRect(),v=document.querySelector("#interview").getBoundingClientRect();return r.top>=v.top-1&&r.bottom<=v.bottom+1})()');
  browser('screenshot',path.join(evidence,viewport+'-plan-approval.png'));
  state=steady(viewport,'Review in conversation');

  // A collapsed disclosure around the chat approval control (the shape of
  // "Approve without building") is opened so the same control takes focus.
  const reveal=()=>{if(phone)browser('click','#details-drawer-toggle');else data('()=>{activateTab("plan");return true}');};
  reveal();
  data('()=>{const control=[...document.querySelectorAll("#inline-task-action [data-focus-key]")].find(e=>e.dataset.focusKey==='+JSON.stringify(approveKey)+');const box=document.createElement("details");box.dataset.crossPaneDisclosure="1";box.append(Object.assign(document.createElement("summary"),{textContent:"Approve without building"}));control.replaceWith(box);box.append(control);return box.open===false}');
  click('#brief-current','Review in conversation →');
  wait('document.activeElement?.dataset?.focusKey==='+JSON.stringify(approveKey));
  state=shell();
  assert.equal(data('()=>document.querySelector("[data-cross-pane-disclosure]").open'),true,viewport+' the collapsed disclosure opens');
  assert.equal(state.open,false);assert.equal(state.chatInert,false);assert.equal(state.active,approveKey);

  // A disabled target cannot take focus; the link still reveals the
  // conversation and focus is never dropped to the page body.
  reveal();
  data('()=>{[...document.querySelectorAll("#inline-task-action [data-focus-key]")].find(e=>e.dataset.focusKey==='+JSON.stringify(approveKey)+').disabled=true;return true}');
  click('#brief-current','Review in conversation →');
  state=shell();
  assert.equal(state.open,false);assert.equal(state.chatInert,false);
  assert.notEqual(state.active,'body',viewport+' a disabled chat target leaves focus on a live control');
  assert.notEqual(state.active,approveKey);
  refresh();

  // A stale read disables the links with every other mutation-capable
  // control; the next successful read restores them.
  data('()=>{unavailableRun("Fixture read failed.");return true}');
  assert.deepEqual(data('()=>[["#brief-current","Review in conversation →"],["#inline-task-action","View plan"]].map(([scope,label])=>[...document.querySelectorAll(scope+" button")].find(e=>e.textContent.trim()===label).disabled)'),[true,true]);
  refresh();wait('!taskReadError');
  assert.deepEqual(data('()=>[["#brief-current","Review in conversation →"],["#inline-task-action","View plan"]].map(([scope,label])=>[...document.querySelectorAll(scope+" button")].find(e=>e.textContent.trim()===label).disabled)'),[false,false]);
  if(phone&&shell().open)browser('click','#details-drawer-close');
  assert.deepEqual(shell().posts,[],viewport+' plan links submit no approval');
  assert.deepEqual(fs.readFileSync(task.stateFile),task.before);
  assert.equal(JSON.parse(fs.readFileSync(task.stateFile)).goal_contract.approval_status,'draft');

  // Work: a question's Open in conversation, activated from the keyboard,
  // closes Details, selects that question and focuses the composer.
  task=open('pending-answer');
  if(phone){browser('click','#details-drawer-toggle');wait('document.querySelector(".app-shell").classList.contains("details-open")');}
  assert.equal(shell().tab,'now');
  press('#now .work-question-row[data-question-id="question-2"]','Open in conversation →');
  wait('document.activeElement?.id==="change-text"');
  state=shell();
  assert.equal(state.open,false,viewport+' Open in conversation closes Details');
  assert.equal(state.chatInert,false);assert.equal(state.active,'change-text');
  assert.equal(data('()=>document.querySelector("#question-target").value'),'question-2');
  assert.equal(data('()=>{const e=document.querySelector("[data-question-card=\\"question-2\\"]");return !!e&&!e.hidden}'),true,viewport+' the selected question card is shown');
  browser('screenshot',path.join(evidence,viewport+'-work-question.png'));
  state=steady(viewport,'Open in conversation');
  assert.equal(state.overflow,false,viewport+' has no horizontal page scroll');
  assert.deepEqual(state.posts,[],viewport+' the Work link answers nothing');
  assert.deepEqual(fs.readFileSync(task.stateFile),task.before);
  results.push({viewport,width,height});
 }
 fs.writeFileSync(path.join(evidence,'manifest.json'),JSON.stringify(results,null,2));
 console.log('Cross-pane links passed at three sizes: chat links reveal their pane, pane links reach the exact chat target, nothing is submitted. '+evidence);
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(()=>{try{browser('close');}catch{}server.kill('SIGTERM');});
