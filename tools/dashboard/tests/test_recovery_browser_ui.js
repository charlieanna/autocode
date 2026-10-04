// Actual HTTP/page controls and native clicks over disposable recovery states.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn,execFileSync}=require('node:child_process');
const root=path.resolve(__dirname,'../../..'),session='recovery-cards-'+process.pid;
const evidence=path.join(root,'.autocode/evidence/recovery-cards','run-'+process.pid);fs.mkdirSync(evidence,{recursive:true});
const server=spawn(process.env.AUTOCODE_TEST_PYTHON||'python3',[path.join(__dirname,'recovery_browser_fixture.py')],{cwd:root,env:{...process.env,AUTOCODE_FIXTURE_ROOT:path.join(evidence,'fixture')},stdio:['ignore','pipe','pipe']});
const browser=(...args)=>execFileSync('agent-browser',['--session',session,...args],{cwd:root,encoding:'utf8',timeout:45000});
const data=fn=>JSON.parse(JSON.parse(browser('eval','JSON.stringify(('+fn+')())').trim()));
const wait=expression=>browser('wait','--fn',expression);
const click=selector=>{data('()=>{document.querySelector('+JSON.stringify(selector)+').scrollIntoView({block:"center"});return true}');wait('(()=>{const e=document.querySelector('+JSON.stringify(selector)+'),r=e?.getBoundingClientRect(),hit=r&&document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return e&&!e.disabled&&(hit===e||e.contains(hit))})()');browser('click',selector);};
function assertPollingKeepsActionReachable(selector,viewport){
  // A reader can leave focus on the inspection summary and scroll to its
  // action. A background refresh must preserve both focus and that scroll,
  // rather than pulling the button out from under the pointer.
  const pollScroll=JSON.parse(JSON.parse(browser('eval','(async()=>{const selector='+JSON.stringify(selector)+';const action=document.querySelector(selector),thread=document.querySelector("#interview");const probe=()=>{const e=document.querySelector(selector),r=e.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return {scroll:thread.scrollTop,focus:document.activeElement?.dataset?.focusKey,hit:hit===e||e.contains(hit)}};action.scrollIntoView({block:"center",behavior:"instant"});const before=probe();await refresh();return JSON.stringify({before,after:probe(),replaced:action!==document.querySelector(selector)})})()').trim()));
  assert.equal(pollScroll.before.hit,true,viewport+' recovery action is reachable before polling');
  assert.equal(pollScroll.replaced,true,viewport+' probe exercises a real detail refresh');
  assert.equal(pollScroll.after.focus,pollScroll.before.focus,viewport+' polling preserves the inspected control focus');
  assert.ok(Math.abs(pollScroll.after.scroll-pollScroll.before.scroll)<=1,viewport+' polling preserves the reader scroll: '+JSON.stringify(pollScroll));
  assert.equal(pollScroll.after.hit,true,viewport+' polling leaves the recovery action under the pointer');
}
const ready=new Promise((resolve,reject)=>{let output='',errors='';server.stdout.on('data',chunk=>{output+=chunk;const line=output.split('\n').find(line=>line.startsWith('FIXTURE='));if(line)resolve(JSON.parse(line.slice(8)));});server.stderr.on('data',chunk=>errors+=chunk);server.on('error',reject);server.on('exit',code=>reject(Error('Fixture exited '+code+': '+errors)));});
(async()=>{
 const fixture=await ready,results=[];
 const kept=path.join(fixture.workspace,'kept.txt'),originalWork=fs.readFileSync(kept);
 const button=id=>'#task-attention [data-recovery-action="'+id+'"]';
 const inspect='#task-attention details[data-recovery-inspection] summary';
 const refresh=()=>browser('eval','(async()=>{await refresh();return true})()');
 const assertIdentity=kind=>{
  assert.equal(data('()=>document.querySelector("#breadcrumb-title").textContent'),path.basename(fixture.workspace),'The breadcrumb keeps the project identity across task changes and polling');
  assert.equal(data('()=>document.querySelector("#task-title").textContent'),'Inspect '+kind+' recovery','The selected task retains its separate title');
 };
 for(const [viewport,width,height]of [['desktop',1440,1024],['tablet',1024,768],['mobile',390,844]]){
  browser('set','viewport',String(width),String(height));
  const open=kind=>{const name=kind+'-'+viewport;browser('open',fixture.scenarios[name]);wait('typeof latestRun!=="undefined"&&latestRun?.run?.endsWith('+JSON.stringify('/'+name)+')&&!taskReadError&&!!document.querySelector("#task-attention [data-recovery-action]")');assertIdentity(kind);};
  open('builder');
  const run=data('()=>latestRun.run'),file=path.join(run,'state.json'),before=fs.readFileSync(file);
  assert.ok(run.startsWith(path.join(evidence,'fixture')+path.sep));
  const content=data('()=>document.querySelector("#task-attention").textContent');
  for(const expected of ['What happened','What is retained','What you can do','configured attempts','Repeat','recorded attempt'])assert.ok(content.includes(expected),expected);
  assert.equal(data('()=>document.querySelector('+JSON.stringify(button('retry_builder:M1'))+').disabled'),true);
  click(inspect);
  wait('!document.querySelector('+JSON.stringify(button('retry_builder:M1'))+').disabled');
  refresh();assertIdentity('builder');assert.equal(data('()=>document.querySelector("#task-attention details[data-recovery-inspection]").open'),true,'Polling keeps inspected same-token evidence open');
  assert.deepEqual(fs.readFileSync(file),before,'Inspection does not change the task');
  const geometry=data('()=>{const e=document.querySelector('+JSON.stringify(button('retry_builder:M1'))+'),r=e.getBoundingClientRect();return {w:r.width,h:r.height,overflow:document.documentElement.scrollWidth>innerWidth}}');
  assert.ok(geometry.w>=44&&geometry.h>=44);assert.equal(geometry.overflow,false);
  browser('screenshot',path.join(evidence,viewport+'-builder.png'));
  const old=data('()=>({workspace:latestRun.workspace,run:latestRun.run,action:"recover_pause",recovery_token:latestRun.interventions.recovery.token,recovery_action:"retry_builder:M1"})');
  assertPollingKeepsActionReachable(button('retry_builder:M1'),viewport);
  assert.deepEqual(fs.readFileSync(file),before,'Polling and inspection preserve the saved task');
  click(button('retry_builder:M1'));
  wait('latestRun.actions?.some(row=>row.command?.includes("--retry-builder"))');
  const sent=data('()=>latestRun.actions.at(-1).command');
  assert.deepEqual(sent.slice(-3),['--resume-paused','--retry-builder','M1']);assert.equal(sent.includes('--approve-goal'),false);
  const after=JSON.parse(fs.readFileSync(file));const prior=JSON.parse(before);
  assert.deepEqual(after.settings,prior.settings);assert.deepEqual(after.goal_contract,prior.goal_contract);assert.equal(after.iteration,prior.iteration+1);
  assert.equal(data('()=>document.querySelector('+JSON.stringify(button('retry_builder:M1'))+').disabled'),true,'The new failure requires a new inspection');
  const rejected=JSON.parse(browser('eval','(async()=>{const r=await fetch("/api/action",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify('+JSON.stringify(old)+')});return JSON.stringify({status:r.status,text:await r.text()})})()').trim());
  const rejection=typeof rejected==='string'?JSON.parse(rejected):rejected;
  assert.equal(rejection.status,400);assert.match(rejection.text,/pause changed/);
  assert.deepEqual(JSON.parse(fs.readFileSync(file)),after,'A stale request cannot change a newer failure');

  open('interruption');assert.equal(data('()=>document.querySelector('+JSON.stringify(button('abandon'))+').disabled'),true);
  click(inspect);click(button('abandon'));wait('latestRun.status==="PAUSED_INTERVENTION"&&!!document.querySelector('+JSON.stringify(button('resume'))+')');
  const recovered=data('()=>latestRun.actions.at(-1).command');
  assert.equal(recovered.includes('--abandon-stage'),true);assert.equal(recovered.includes('--resume-paused'),false);
  assert.equal(data('()=>document.querySelector('+JSON.stringify(button('resume'))+').disabled'),true,'Recovery leaves a separate inspected Resume');
  click(inspect);assertPollingKeepsActionReachable(button('resume'),viewport);click(button('resume'));wait('latestRun.status==="RUNNING"');
  assert.equal(data('()=>latestRun.actions.at(-1).command.includes("--resume-paused")'),true);

  open('unknown');assert.deepEqual(data('()=>[...document.querySelectorAll("#task-attention [data-recovery-action]")].map(e=>e.dataset.recoveryAction)'),['inspect','feedback']);
  browser('fill','#change-text','Keep this unsent corrective draft');click(button('feedback'));
  assert.equal(data('()=>document.querySelector("#change-text").value'),'Keep this unsent corrective draft');
  browser('screenshot',path.join(evidence,viewport+'-unknown.png'));
  open('stale');
  const staleFile=path.join(data('()=>latestRun.run'),'state.json'),staleBefore=fs.readFileSync(staleFile);
  assert.equal(data('()=>latestRun.status'),'RUNNING');
  assert.match(data('()=>document.querySelector("#task-attention").textContent'),/worker was gone.*last status check/);
  assert.deepEqual(data('()=>[...document.querySelectorAll("#task-attention [data-recovery-action]")].map(e=>e.dataset.recoveryAction)'),['inspect','feedback']);
  assert.deepEqual(fs.readFileSync(staleFile),staleBefore,'An absent worker does not grant duplicate execution');
  browser('screenshot',path.join(evidence,viewport+'-stale-worker.png'));
  open('source');
  const sourceFile=path.join(data('()=>latestRun.run'),'state.json'),sourceBefore=fs.readFileSync(sourceFile);
  assert.deepEqual(data('()=>[...document.querySelectorAll("#task-attention [data-recovery-action]")].map(e=>e.dataset.recoveryAction)'),['inspect','feedback']);
  assert.match(data('()=>document.querySelector("#task-attention").textContent'),/original source identity/);
  click(inspect);refresh();assertIdentity('source');
  assert.equal(data('()=>latestRun.interventions.recovery.context.role'),'Investigator');
  assert.match(data('()=>document.querySelector("#task-attention").textContent'),/Investigator/);
  const sourceRefusal=JSON.parse(JSON.parse(browser('eval','(async()=>{const r=await fetch("/api/action",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({workspace:latestRun.workspace,run:latestRun.run,action:"recover_pause",recovery_token:latestRun.interventions.recovery.token,recovery_action:"resume"})});return JSON.stringify({status:r.status,text:await r.text()})})()').trim()));
  assert.equal(sourceRefusal.status,400);assert.match(sourceRefusal.text,/no longer offered/);
  assert.deepEqual(fs.readFileSync(sourceFile),sourceBefore,'Missing original identity never grants an execution attempt');
  assert.equal(data('()=>latestRun.actions?.length||0'),0);
  browser('screenshot',path.join(evidence,viewport+'-missing-source.png'));
  open('internal');
  const internalFile=path.join(data('()=>latestRun.run'),'state.json'),internalBefore=fs.readFileSync(internalFile);
  assert.match(data('()=>document.querySelector("#task-attention").textContent'),/Awaiting Resolver/);
  assert.equal(data('()=>!!document.querySelector("#task-attention [data-recovery-action=decision]")'),false);
  assert.equal(data('()=>[...document.querySelectorAll("[data-question-card]")].some(e=>!e.hidden)'),false);
  assert.deepEqual(fs.readFileSync(internalFile),internalBefore,'An unpublished question remains an internal checkpoint');
  open('stopped');assert.deepEqual(data('()=>[...document.querySelectorAll("#task-attention [data-recovery-action]")].map(e=>e.dataset.recoveryAction)'),['inspect','new_conversation']);
  assert.equal(data('()=>document.documentElement.scrollWidth>innerWidth'),false);
  assert.deepEqual(fs.readFileSync(kept),originalWork);
  results.push({viewport,geometry,builder_command:sent,recovery_command:recovered});
 }
 fs.writeFileSync(path.join(evidence,'manifest.json'),JSON.stringify(results,null,2));
 console.log('Exact recovery, separate resume, stale refusal and preserved work passed in three browser sizes. '+evidence);
})().catch(error=>{console.error(error);try{browser('screenshot',path.join(evidence,'failure.png'));}catch{}process.exitCode=1;}).finally(()=>{try{browser('close');}catch{}server.kill('SIGTERM');});
