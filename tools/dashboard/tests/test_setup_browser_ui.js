const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn,execFileSync}=require('node:child_process');
const root=path.resolve(__dirname,'../../..'),evidence=process.env.DASHBOARD_SETUP_EVIDENCE_DIR||path.join(root,'.autocode/evidence/setup','run-'+process.pid);
fs.mkdirSync(evidence,{recursive:true});const session='setup-ui-'+process.pid;let server;
const browser=(...args)=>execFileSync('agent-browser',['--session',session,...args],{cwd:root,encoding:'utf8',timeout:45000});
const data=fn=>JSON.parse(JSON.parse(browser('eval','JSON.stringify(('+fn+')())').trim()));
const wait=expr=>browser('wait','--fn',expr);
function click(selector){data('()=>{document.querySelector('+JSON.stringify(selector)+').scrollIntoView({block:"center",behavior:"instant"});return true}');browser('click',selector);}
function start(){return new Promise((resolve,reject)=>{server=spawn(process.env.AUTOCODE_TEST_PYTHON,[path.join(__dirname,'setup_browser_fixture.py')],{cwd:root,env:{...process.env,AUTOCODE_FIXTURE_ROOT:path.join(evidence,'fixture')},stdio:['ignore','pipe','pipe']});let output='',errors='';server.stdout.on('data',c=>{output+=c;const line=output.split('\n').find(l=>l.startsWith('FIXTURE='));if(line)resolve(JSON.parse(line.slice(8)));});server.stderr.on('data',c=>errors+=c);server.on('error',reject);server.on('exit',code=>reject(Error('Fixture exited '+code+' '+errors)));});}
(async()=>{
 const fixture=await start(),captures=[];browser('open',fixture.url);wait('!!document.querySelector("#workspace-setup-card")&&!!setupReport&&!setupCheckBusy');
 assert.equal(data('()=>document.querySelector("#workspace-setup-card").closest("#draft-messages")!==null'),true);
 for(const [name,w,h] of [['desktop',1440,1024],['tablet',1024,768],['mobile',390,844]]){
  browser('set','viewport',String(w),String(h));
   if(name!=='desktop'){if(w<700)click('#nav-toggle');click('#workspace-setup');wait('!!document.querySelector("#workspace-setup-card")&&!!setupReport&&!setupCheckBusy');}
  assert.match(data('()=>document.querySelector("#setup-checklist").textContent'),/Python.*Missing.*Git.*Missing.*OpenCode.*Missing/s);
  assert.match(data('()=>document.querySelector("#setup-checklist").textContent'),/accounts.*Not verified/s);
  assert.equal(data('()=>document.body.innerText.includes("SECRET_UI_fixture_token")'),false);
  assert.equal(data('()=>document.documentElement.scrollWidth>innerWidth'),false);
  browser('select','#setup-project-mode','new');browser('fill','#setup-project-path',path.join(fixture.root,name));
  if(!data('()=>document.querySelector("#setup-model-settings details").open'))click('#setup-model-settings summary');
  browser('select','#terra-reasoning-effort','high');
  const shot=path.join(evidence,name+'-setup.png');browser('screenshot',shot);captures.push(shot);
  click('#setup-project-submit');wait('!!latestConversation?.project_workspace&&latestConversation.project_workspace==='+JSON.stringify(path.join(fixture.root,name)));
  assert.equal(data('()=>latestConversation.models.terra_reasoning_effort'),'high');assert.equal(data('()=>latestConversation.messages.length'),0);
  const head=execFileSync('git',['-C',path.join(fixture.root,name),'rev-parse','--verify','HEAD'],{encoding:'utf8'}).trim();assert.match(head,/^[a-f0-9]{40}$/);
  assert.equal(execFileSync('git',['-C',path.join(fixture.root,name),'ls-tree','--name-only','HEAD'],{encoding:'utf8'}),'');
  browser('reload');wait('latestConversation?.project_workspace==='+JSON.stringify(path.join(fixture.root,name)));
  // Pre-upgrade document: explicit folder confirmation belongs in chat and
  // must not submit the draft or launch a model.
  const id=data('()=>latestConversation.id'),docFile=path.join(fixture.root,'dashboard/conversations/continuous',id+'.json');
  const legacy=JSON.parse(fs.readFileSync(docFile,'utf8'));delete legacy._project_identity;fs.writeFileSync(docFile,JSON.stringify(legacy));
  browser('reload');wait('!!latestConversation?.project_scope_confirmation');
  const scopeAction=data('()=>{const b=[...document.querySelectorAll("#draft-problem button")].find(b=>b.textContent==="Confirm this project folder");if(!b)return null;b.id="confirm-project-folder";const r=b.getBoundingClientRect();return {inChat:!!b.closest("#draft-conversation"),width:r.width,height:r.height}}');
  assert.ok(scopeAction?.inChat&&scopeAction.width>=44&&scopeAction.height>=44,JSON.stringify(scopeAction));
  assert.equal(data('()=>document.querySelector("#draft-send").disabled'),true);
   data('()=>{const b=[...document.querySelectorAll("#draft-problem button")].find(b=>b.textContent==="Confirm this project folder");b.click();return true}');wait('latestConversation&&!latestConversation.project_scope_error');
  assert.equal(data('()=>latestConversation.messages.length'),0,'confirmation is not a user message or provider dispatch');
   browser('reload');wait('latestConversation&&!latestConversation.project_scope_confirmation');

   click('#draft-conversation [data-view="tasks"]');click('[data-new-task]');
   data('()=>{showNewTask("");return true}');wait('currentView==="new-task"&&!document.querySelector("#new-task").hidden');
   fs.writeFileSync(fixture.transport,JSON.stringify({status:'ok',data:{version:'2.0.20+SECRET-UI-fixture-token'}}));
   data('()=>{loadModels(true);return true}');wait('conversationTransport.transport==="unsupported"&&!modelCatalogue.loading');
   assert.equal(data('()=>document.querySelector("#create-submit").disabled'),true);
   assert.equal(data('()=>document.querySelector("#create-model-catalogue-gate").hidden'),false);
   assert.equal(data('()=>currentView'),'new-task','admission is checked in the visible form, not hidden controls');
   assert.match(data('()=>document.querySelector("#create-model-catalogue-status").textContent'),/OpenCode 1.x/);
   assert.equal(data('()=>document.body.innerText.includes("SECRET-UI-fixture-token")'),false);
   fs.writeFileSync(fixture.transport,JSON.stringify({status:'ok',data:{version:'1.18.33'}}));
   data('()=>{document.querySelector("#retry-models").click();return true}');wait('conversationTransport.transport==="available"&&!document.querySelector("#create-submit").disabled');
   assert.equal(data('()=>conversationModelReadiness().authentication'),'unknown');
   fs.writeFileSync(fixture.catalogue,'import sys\nprint("SECRET_UI_fixture_token",file=sys.stderr)\nraise SystemExit(1)\n');
   data('()=>{loadModels(true);return true}');wait('!!modelCatalogue.error&&!modelCatalogue.loading');
   assert.equal(data('()=>document.body.innerText.includes("SECRET_UI_fixture_token")'),false);
   assert.equal(data('()=>document.querySelector("#create-submit").disabled'),true);
   fs.writeFileSync(fixture.catalogue,'print('+JSON.stringify(fixture.models.join('\n'))+')\n');
   data('()=>{document.querySelector("#retry-models").click();return true}');wait('!document.querySelector("#create-submit").disabled');
   data('()=>{showNewTask("");document.querySelector("#create-submit").scrollIntoView({block:"center",behavior:"instant"});return true}');
   assert.equal(data('()=>currentView'),'new-task');
   assert.equal(data('()=>document.documentElement.scrollWidth>innerWidth'),false);
   const admission=path.join(evidence,name+'-admission.png');browser('screenshot',admission);captures.push(admission);

 }
 browser('set','viewport','1440','1024');click('#workspace-setup');wait('!!document.querySelector("#workspace-setup-card")');
 click('#draft-conversation [data-view="tasks"]');click('[data-new-task]');
 assert.equal(data('()=>document.querySelector("#opencode-role-settings").closest("#create")!==null'),true,'Existing model controls return to new conversation form after leaving setup');
 assert.equal(data('()=>document.querySelector("#terra-reasoning-effort").value'),'high');
 click('#workspace-setup');wait('!!document.querySelector("#workspace-setup-card")');browser('select','#setup-project-mode','existing');browser('fill','#setup-project-path',fixture.dirty);click('#setup-project-submit');wait('!setupProjectBusy&&!!setupError');assert.match(data('()=>setupError'),/uncommitted/);assert.equal(fs.readFileSync(path.join(fixture.dirty,'keep.txt'),'utf8'),'human changes remain');
 // Recheck uses new diagnostics and never turns a version check into an account proof.
 fs.writeFileSync(fixture.report,JSON.stringify({ok:true,checks:['python','psutil','git','engine:opencode','engine:codex','engine','workspace'].map(name=>({name,status:'ok'}))}));
 click('#setup-recheck');wait('!setupCheckBusy&&setupReport?.checks?.find(x=>x.id==="git")?.status==="ok"');assert.match(data('()=>document.querySelector("#setup-checklist").textContent'),/Git.*Found/);assert.match(data('()=>document.querySelector("#setup-checklist").textContent'),/accounts.*Not verified/);
 assert.equal(data('()=>JSON.stringify(localStorage).includes("SECRET_UI_fixture_token")'),false);
 const inspect=directory=>{for(const entry of fs.readdirSync(directory,{withFileTypes:true})){const file=path.join(directory,entry.name);if(entry.isDirectory())inspect(file);else if(file.endsWith('.json'))assert.ok(!fs.readFileSync(file,'utf8').includes('SECRET_UI_fixture_token'),file);}};inspect(path.join(fixture.root,'dashboard'));
 const errors=browser('errors').trim();assert.ok(!errors||/^No (?:page )?errors\.?$/i.test(errors),errors);
 fs.writeFileSync(path.join(evidence,'manifest.json'),JSON.stringify({captures,viewports:[1440,1024,390],missingDependencies:true,dirtyRefused:true,credentialsNotStored:true,newProjectsCommitted:true,modelSettingsPreserved:true,errors},null,2));
 console.log('Setup browser matrix passed: missing prerequisites, committed projects, scoped chats, model settings, dirty refusal and no credentials. '+evidence);
})().catch(e=>{console.error(e);try{console.error(JSON.stringify(data('()=>({view:currentView,error:setupError,notice:document.querySelector("#dashboard-notice").textContent,hash:location.hash})')));browser('screenshot',path.join(evidence,'failure.png'));}catch{}process.exitCode=1;}).finally(()=>{try{browser('close')}catch{}if(server)server.kill('SIGTERM');});
