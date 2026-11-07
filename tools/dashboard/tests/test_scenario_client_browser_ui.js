// #730 acceptance: one catalog scenario driven end to end through the chat client against the
// scripted provider, then each requirement's changes read beside it in the Checks pane.
// parallel-diamond builds A, then B and C as one parallel step, then D. Each requirement shows
// its reference solution's lines as added hunks, and only its own files of the shared step.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn,execFileSync}=require('node:child_process');
const root=path.resolve(__dirname,'../../..'),session='scenario-client-'+process.pid;
const evidence=process.env.DASHBOARD_SCENARIO_CLIENT_EVIDENCE_DIR||path.join(root,'.autocode/evidence/scenario-client','run-'+process.pid);
fs.mkdirSync(evidence,{recursive:true});let server;
const browser=(...args)=>execFileSync('agent-browser',['--session',session,...args],{cwd:root,encoding:'utf8',timeout:45000});
const data=fn=>JSON.parse(JSON.parse(browser('eval','JSON.stringify(('+fn+')())').trim()));
const wait=expr=>browser('wait','--fn',expr);
const q=JSON.stringify,sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
// The build itself outlasts one browser wait: poll the page's own state until the deadline.
async function until(expr,what,seconds=420){
 const end=Date.now()+seconds*1000;
 while(Date.now()<end){if(data('()=>!!('+expr+')'))return;await sleep(1000);}
 throw Error('Timed out waiting for '+what+': '+JSON.stringify(data('()=>({view:currentView,status:latestRun?.status,phase:latestRun?.phase,stop:latestRun?.status_view?.stop_reason,notice:document.querySelector("#dashboard-notice")?.textContent})')));
}
function click(selector){data('()=>{document.querySelector('+q(selector)+').scrollIntoView({block:"center",behavior:"instant"});return true}');browser('click',selector);}
function start(){return new Promise((resolve,reject)=>{server=spawn(process.env.AUTOCODE_TEST_PYTHON||'python3',[path.join(__dirname,'scenario_client_browser_fixture.py')],{cwd:root,env:{...process.env,AUTOCODE_FIXTURE_ROOT:path.join(evidence,'fixture')},stdio:['ignore','pipe','pipe']});let output='',errors='';server.stdout.on('data',c=>{output+=c;const line=output.split('\n').find(l=>l.startsWith('FIXTURE='));if(line)resolve(JSON.parse(line.slice(8)));});server.stderr.on('data',c=>errors+=c);server.on('error',reject);server.on('exit',code=>reject(Error('Fixture exited '+code+' '+errors)));});}
const row=id=>'#execution [data-work-id="'+id+'"]',changes=id=>row(id)+' [data-criterion-changes="'+id+'"]';
function openRequirement(id){if(!data('()=>document.querySelector('+q(row(id)+' .check-detail')+').open'))click(row(id)+' .check-detail>summary');}
function openChanges(id){openRequirement(id);if(!data('()=>document.querySelector('+q(changes(id)+'>details')+').open'))click(changes(id)+'>details>summary');}
const text=selector=>data('()=>document.querySelector('+q(selector)+').textContent');
(async()=>{
 const fixture=await start(),captures=[],ids=Object.keys(fixture.expected).sort();
 const conversation=new URLSearchParams(new URL(fixture.url).hash.slice(1)).get('conversation');
 browser('set','viewport','1440','1024');browser('open',fixture.url);
 wait('typeof latestConversation!=="undefined"&&latestConversation?.id==='+q(conversation)+'&&latestConversation.status==="ready"&&document.querySelector("#draft-text").dataset.conversation==='+q(conversation));

 // The scenario's brief, as a person would type it, then the plan draft and the project attach.
 // Typed through an input event: the brief is longer than a command line agent-browser accepts on every host.
 data('()=>{const e=document.querySelector("#draft-text");e.focus();e.value='+q(fixture.brief)+';e.dispatchEvent(new Event("input",{bubbles:true}));return true}');
 browser('click','#draft-send');
 await until('latestConversation?.status==="ready"&&latestConversation?.plan_drafts?.at(-1)?.status==="current"&&latestConversation.plan_drafts.at(-1).freshness?.state==="fresh"','the plan draft',60);
 if(!data('()=>document.querySelector("#attach-disclosure").open'))browser('click','#attach-disclosure summary');
 browser('select','#workspaces',fixture.project);
 wait('!document.querySelector("#attach-submit").disabled');browser('click','#attach-submit');
 await until('currentView==="task-detail"&&latestRun?.conversation?.attachment?.status==="linked"&&!taskReadError','the attached task',120);
 const run=data('()=>latestRun.run'),taskUrl=data('()=>location.href');

 // The reviewed plan comes back to chat; one click approves it and starts the build.
 await until('[...document.querySelectorAll("#inline-task-action button")].some(b=>/^Approve & build revision /.test(b.textContent)&&!b.disabled)','the plan approval',300);
 const approval=path.join(evidence,'desktop-approve.png');browser('screenshot',approval);captures.push(approval);
 data('()=>{const b=[...document.querySelectorAll("#inline-task-action button")].find(b=>/^Approve & build revision /.test(b.textContent));b.dataset.approveBuild="1";b.scrollIntoView({block:"center",behavior:"instant"});return true}');
 browser('click','[data-approve-build]');
 await until('latestRun?.run==='+q(run)+'&&latestRun.status_view?.status==="TASK_COMPLETE"&&(latestRun.status_view?.changes_by_criterion?.criteria||[]).length===4','the build to complete',420);
 assert.deepEqual(data('()=>latestRun.status_view.changes_by_criterion.criteria.map(row=>row.id).sort()'),ids);
 const stateFile=path.join(run,'state.json'),before=fs.readFileSync(stateFile);

 for(const [name,w,h] of [['desktop',1440,1024],['tablet',1024,768],['mobile',390,844]]){
  browser('set','viewport',String(w),String(h));browser('open',taskUrl);
  wait('latestRun?.run==='+q(run)+'&&!!latestRun.status_view?.changes_by_criterion');
  if(w<760)click('#details-drawer-toggle');
  click('[data-tab="execution"]');wait('currentTab==="execution"&&!!document.querySelector('+q(changes(ids[0]))+')');
  for(const id of ids){
   openChanges(id);wait('!!document.querySelector('+q(changes(id)+' .diff-output')+')');
   const files=data('()=>[...document.querySelectorAll('+q(changes(id)+' .criterion-file')+')].map(e=>e.textContent)');
   const added=data('()=>[...document.querySelectorAll('+q(changes(id)+' .diff-line.added')+')].map(e=>e.textContent.slice(1))');
   for(const [file,lines] of Object.entries(fixture.expected[id])){
    assert.ok(files.includes(file),id+' shows '+file+': '+files);
    for(const line of lines)assert.ok(added.includes(line),id+' shows the reference line '+q(line)+' of '+file);
   }
   // A requirement never shows another requirement's reference files, even from a shared step.
   for(const other of ids.filter(other=>other!==id))for(const file of Object.keys(fixture.expected[other]))assert.ok(!files.includes(file),id+' does not show '+file+' of '+other);
  }
  // B and C were built in one parallel step: each names both milestones and what it leaves out.
  // The step's note lists every left-out file once, comma-separated, so read that list rather
  // than expecting each file to open the sentence (only a one-file milestone could).
  const leftOutPrefix='Not shown, changed in this step for another milestone: ';
  for(const [id,other] of [['C_B','C_C'],['C_C','C_B']]){
   const shown=text(changes(id));
   assert.match(shown,/milestones B, C/);
   const leftOut=data('()=>[...document.querySelectorAll('+q(changes(id)+' .field-note')+')].map(e=>e.textContent).filter(t=>t.startsWith('+q(leftOutPrefix)+')).flatMap(t=>t.slice('+leftOutPrefix.length+').split(", "))');
   for(const file of Object.keys(fixture.expected[other]))assert.ok(leftOut.includes(file),id+' names '+file+' as left out: '+JSON.stringify(leftOut));
   for(const file of Object.keys(fixture.expected[id]))assert.ok(!leftOut.includes(file),id+' never names its own '+file+' as left out');
  }
  assert.equal(data('()=>document.documentElement.scrollWidth>innerWidth'),false);
  data('()=>{document.querySelector('+q(changes('C_B'))+').scrollIntoView({block:"start",behavior:"instant"});return true}');
  const shot=path.join(evidence,name+'-shared-step.png');browser('screenshot',shot);captures.push(shot);
 }
 assert.deepEqual(fs.readFileSync(stateFile),before,'Reading diffs never writes run state');
 const errors=browser('errors').trim();assert.ok(!errors||/^No (?:page )?errors\.?$/i.test(errors),errors);
 fs.writeFileSync(path.join(evidence,'manifest.json'),JSON.stringify({scenario:'parallel-diamond',run,captures,viewports:[1440,1024,390],errors},null,2));
 console.log('Scenario client acceptance passed: brief in chat, attach, approve & build, each requirement beside its reference hunks, shared parallel step split by requirement. '+evidence);
})().catch(e=>{console.error(e);try{console.error(JSON.stringify(data('()=>({view:currentView,tab:typeof currentTab==="undefined"?null:currentTab,status:latestRun?.status,notice:document.querySelector("#dashboard-notice")?.textContent,checks:document.querySelector("#execution")?.textContent.slice(0,3000)})')));browser('screenshot',path.join(evidence,'failure.png'));}catch{}process.exitCode=1;}).finally(()=>{try{browser('close')}catch{}if(server)server.kill('SIGTERM');});
