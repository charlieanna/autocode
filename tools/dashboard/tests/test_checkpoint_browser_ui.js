const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn,execFileSync}=require('node:child_process');
const root=path.resolve(__dirname,'../../..'),evidence=process.env.DASHBOARD_CHECKPOINT_EVIDENCE_DIR||path.join(root,'.autocode/evidence/checkpoint','run-'+process.pid);
fs.mkdirSync(evidence,{recursive:true});const session='checkpoint-ui-'+process.pid;let server;
const browser=(...args)=>execFileSync('agent-browser',['--session',session,...args],{cwd:root,encoding:'utf8',timeout:45000});
const data=fn=>JSON.parse(JSON.parse(browser('eval','JSON.stringify(('+fn+')())').trim()));
const wait=expr=>browser('wait','--fn',expr);
function click(selector){data('()=>{document.querySelector('+JSON.stringify(selector)+').scrollIntoView({block:"center",behavior:"instant"});return true}');browser('click',selector);}
function start(){return new Promise((resolve,reject)=>{server=spawn(process.env.AUTOCODE_TEST_PYTHON,[path.join(__dirname,'checkpoint_browser_fixture.py')],{cwd:root,env:{...process.env,AUTOCODE_FIXTURE_ROOT:path.join(evidence,'fixture')},stdio:['ignore','pipe','pipe']});let output='',errors='';server.stdout.on('data',c=>{output+=c;const line=output.split('\n').find(l=>l.startsWith('FIXTURE='));if(line)resolve(JSON.parse(line.slice(8)));});server.stderr.on('data',c=>errors+=c);server.on('error',reject);server.on('exit',code=>reject(Error('Fixture exited '+code+' '+errors)));});}
(async()=>{
 const fixture=await start(),captures=[];
 for(const [name,w,h] of [['desktop',1440,900],['tablet',1024,768],['mobile',390,844]]){
  const test=fixture.cases[name];browser('set','viewport',String(w),String(h));browser('open',test.url);
  wait('latestRun?.run==='+JSON.stringify(test.run)+'&&!!document.querySelector("[data-checkpoint-restore]")');
  assert.equal(data('()=>document.querySelector("[data-code-checkpoints]").closest("#conversation")!==null'),true);
  click('[data-code-checkpoint] button');wait('!document.querySelector("#checkpoint-comparison").hidden');
  assert.match(data('()=>document.querySelector("#checkpoint-comparison").textContent'),/later work must survive/);
  const comparison=path.join(evidence,name+'-compare.png');browser('screenshot',comparison);captures.push(comparison);
  if(w<1200)click('#details-drawer-close');
  click('[data-checkpoint-restore]');wait('!!document.querySelector("[data-confirm-checkpoint]")');
  assert.equal(data('()=>document.querySelector("[data-checkpoint-confirmation]").closest("#conversation")!==null'),true);
  const confirmation=data('()=>document.querySelector("[data-checkpoint-confirmation]").textContent');
  assert.match(confirmation,/current branch.*later work stay intact/s);assert.match(confirmation,/Plan approval stays valid/);assert.match(confirmation,/fresh verification/);
  assert.equal(data('()=>document.documentElement.scrollWidth>innerWidth'),false);
  const shot=path.join(evidence,name+'-confirm.png');browser('screenshot',shot);captures.push(shot);
  click('[data-confirm-checkpoint]');wait('!!checkpointActions.get(latestRun.run)?.result');
  const result=data('()=>checkpointActions.get(latestRun.run).result');
  assert.equal(execFileSync('git',['-C',test.workspace,'rev-parse','HEAD'],{encoding:'utf8'}).trim(),test.head);
  assert.equal(execFileSync('git',['-C',test.workspace,'write-tree'],{encoding:'utf8'}).trim(),test.index);
  assert.equal(fs.readFileSync(path.join(test.workspace,'app.txt'),'utf8'),'later work must survive\n');
  assert.equal(fs.readFileSync(path.join(result.workspace,'app.txt'),'utf8'),'checkpoint code\n');
  browser('reload');wait('latestRun?.interventions?.code_checkpoints?.restores?.length===1');
  assert.match(data('()=>document.querySelector("[data-code-checkpoints]").textContent'),/Restored branch saved/);
  click('.checkpoint-lineage button');wait('latestRun?.run==='+JSON.stringify(result.run_dir));
  assert.equal(data('()=>latestRun.status'),'PAUSED_REQUESTED');assert.equal(data('()=>latestRun.counts.pass'),0);
  assert.match(data('()=>document.querySelector("[data-code-checkpoints]").textContent'),/Fresh independent checks are required/);
 }
 const errors=browser('errors').trim();assert.ok(!errors||/^No (?:page )?errors\.?$/i.test(errors),errors);
 fs.writeFileSync(path.join(evidence,'manifest.json'),JSON.stringify({captures,viewports:[1440,1024,390],realCli:true,originalPreserved:true,reload:true,newCandidatePaused:true,errors},null,2));
 console.log('Checkpoint browser matrix passed: real compare, inline confirmation, preserved index and branch, reload, paused unverified continuation. '+evidence);
})().catch(e=>{console.error(e);try{console.error(JSON.stringify(data('()=>({view:currentView,hash:location.hash,notice:document.querySelector("#dashboard-notice").textContent,actions:[...checkpointActions.values()]})')));browser('screenshot',path.join(evidence,'failure.png'));}catch{}process.exitCode=1;}).finally(()=>{try{browser('close')}catch{}if(server)server.kill('SIGTERM');});
