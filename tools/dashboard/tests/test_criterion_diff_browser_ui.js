// Real browser acceptance of each requirement's saved-step diffs in the Checks pane (#730).
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn,execFileSync}=require('node:child_process');
const root=path.resolve(__dirname,'../../..'),session='criterion-diff-'+process.pid;
const evidence=process.env.DASHBOARD_CRITERION_DIFF_EVIDENCE_DIR||path.join(root,'.autocode/evidence/criterion-diff','run-'+process.pid);
fs.mkdirSync(evidence,{recursive:true});let server;
const browser=(...args)=>execFileSync('agent-browser',['--session',session,...args],{cwd:root,encoding:'utf8',timeout:45000});
const data=fn=>JSON.parse(JSON.parse(browser('eval','JSON.stringify(('+fn+')())').trim()));
const wait=expr=>browser('wait','--fn',expr);
const q=JSON.stringify;
function click(selector){data('()=>{document.querySelector('+q(selector)+').scrollIntoView({block:"center",behavior:"instant"});return true}');browser('click',selector);}
function start(){return new Promise((resolve,reject)=>{server=spawn(process.env.AUTOCODE_TEST_PYTHON||'python3',[path.join(__dirname,'criterion_diff_browser_fixture.py')],{cwd:root,env:{...process.env,AUTOCODE_FIXTURE_ROOT:path.join(evidence,'fixture')},stdio:['ignore','pipe','pipe']});let output='',errors='';server.stdout.on('data',c=>{output+=c;const line=output.split('\n').find(l=>l.startsWith('FIXTURE='));if(line)resolve(JSON.parse(line.slice(8)));});server.stderr.on('data',c=>errors+=c);server.on('error',reject);server.on('exit',code=>reject(Error('Fixture exited '+code+' '+errors)));});}
const row=id=>'#execution [data-work-id="'+id+'"]',changes=id=>row(id)+' [data-criterion-changes="'+id+'"]';
function openRequirement(id){if(!data('()=>document.querySelector('+q(row(id)+' .check-detail')+').open'))click(row(id)+' .check-detail>summary');}
function openChanges(id){openRequirement(id);if(!data('()=>document.querySelector('+q(changes(id)+'>details')+').open'))click(changes(id)+'>details>summary');}
const text=selector=>data('()=>document.querySelector('+q(selector)+').textContent');
(async()=>{
 const fixture=await start(),captures=[],[first,,third]=fixture.steps,stateFile=path.join(fixture.run,'state.json'),before=fs.readFileSync(stateFile);
 const head=execFileSync('git',['-C',fixture.workspace,'rev-parse','HEAD'],{encoding:'utf8'}).trim();
 for(const [name,w,h] of [['desktop',1440,1024],['tablet',1024,768],['mobile',390,844]]){
  browser('set','viewport',String(w),String(h));browser('open',fixture.url);
  wait('latestRun?.run==='+q(fixture.run)+'&&!!latestRun.status_view?.changes_by_criterion');
  // Count the browser's own reads: one request serves every requirement and survives a refresh.
  data('()=>{if(!window.criterionDiffReads){window.criterionDiffReads=0;const original=window.fetch;window.fetch=(url,...rest)=>{if(String(url).startsWith("/api/criterion-diffs"))window.criterionDiffReads++;return original(url,...rest)}}return true}');
  if(w<760)click('#details-drawer-toggle');
  click('[data-tab="execution"]');wait('currentTab==="execution"&&!!document.querySelector('+q(changes('C1'))+')');

  // A requirement with its own step: only its diff, labelled as attribution, not proof.
  assert.equal(data('()=>document.querySelector('+q(changes('C1'))+').dataset.overlapping'),'false');
  assert.doesNotMatch(text(changes('C1')+'>details>summary'),/overlap/i);
  openChanges('C1');wait('!!document.querySelector('+q(changes('C1')+' [data-criterion-step="'+first+'"] .diff-output')+')');
  const own=text(changes('C1'));
  assert.match(own,/Greet by name · milestone M1/);assert.match(own,/greet\.py/);assert.match(own,/not proof/);
  assert.match(own,/\+    return 'Hello, ' \+ name/);assert.match(own,/-    return name/);assert.doesNotMatch(own,/cli\.py|sys\.argv/);
  assert.equal(data('()=>[...document.querySelectorAll('+q(changes('C1')+' .diff-line.added')+')].some(line=>line.textContent.includes("Hello, "))'),true);

  // Requirements whose steps changed the same file say so before they are opened.
  for(const id of ['C2','C3']){assert.equal(data('()=>document.querySelector('+q(changes(id))+').dataset.overlapping'),'true');assert.match(text(changes(id)+'>details>summary'),/overlaps another requirement/);}
  openChanges('C3');wait('!!document.querySelector('+q(changes('C3')+' [data-criterion-step="'+third+'"] .diff-output')+')');
  const overlap=text(changes('C3'));
  assert.match(overlap,/Reject an empty name · milestone M3/);assert.match(overlap,/\+if not sys\.argv\[1:\]:/);assert.match(overlap,/test_cli\.py/);assert.match(overlap,/\+# fixture/);
  assert.match(overlap,/Another requirement’s steps also changed/);assert.doesNotMatch(overlap,/Hello, /);
  const shot=path.join(evidence,name+'-overlap.png');data('()=>{document.querySelector('+q(changes('C3'))+').scrollIntoView({block:"start",behavior:"instant"});return true}');browser('screenshot',shot);captures.push(shot);

  // A refresh re-renders the open diffs from the one cached read.
  browser('eval','(async()=>{await refresh();return true})()');
  wait('!!document.querySelector('+q(changes('C3')+' [data-criterion-step="'+third+'"] .diff-output')+')');
  assert.equal(data('()=>window.criterionDiffReads'),1);

  // No attributed step: say why and offer the run-level changes instead.
  assert.equal(data('()=>document.querySelector('+q(changes('C4'))+').dataset.criterionChangesState'),'none');
  openRequirement('C4');assert.match(text(changes('C4')),/No saved Builder step is attributed to this requirement yet/);
  click(changes('C4')+' button');wait('currentTab==="changes"&&document.querySelectorAll("#changes-content .diff-stage").length===3');

  assert.equal(data('()=>document.documentElement.scrollWidth>innerWidth'),false);
  const whole=path.join(evidence,name+'-changes-fallback.png');browser('screenshot',whole);captures.push(whole);
 }
 assert.deepEqual(fs.readFileSync(stateFile),before,'Reading diffs never writes run state');
 assert.equal(execFileSync('git',['-C',fixture.workspace,'rev-parse','HEAD'],{encoding:'utf8'}).trim(),head);
 const errors=browser('errors').trim();assert.ok(!errors||/^No (?:page )?errors\.?$/i.test(errors),errors);
 fs.writeFileSync(path.join(evidence,'manifest.json'),JSON.stringify({captures,viewports:[1440,1024,390],realCli:true,oneRead:true,readOnly:true,errors},null,2));
 console.log('Criterion diff browser matrix passed: own and overlapping steps, attribution label, one cached read across a refresh, run-level fallback, read-only. '+evidence);
})().catch(e=>{console.error(e);try{console.error(JSON.stringify(data('()=>({tab:currentTab,hash:location.hash,notice:document.querySelector("#dashboard-notice")?.textContent,checks:document.querySelector("#execution")?.textContent.slice(0,2000)})')));browser('screenshot',path.join(evidence,'failure.png'));}catch{}process.exitCode=1;}).finally(()=>{try{browser('close')}catch{}if(server)server.kill('SIGTERM');});
