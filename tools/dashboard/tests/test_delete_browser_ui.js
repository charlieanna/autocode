const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn,execFileSync}=require('node:child_process');
const root=path.resolve(__dirname,'../../..'),evidence=process.env.DASHBOARD_DELETE_EVIDENCE_DIR||path.join(root,'.autocode/evidence/delete','run-'+process.pid);
fs.mkdirSync(evidence,{recursive:true});
const session='delete-ui-'+process.pid;
let server;
const browser=(...args)=>execFileSync('agent-browser',['--session',session,...args],{cwd:root,encoding:'utf8',timeout:45000});
const data=fn=>JSON.parse(JSON.parse(browser('eval','JSON.stringify(('+fn+')())').trim()));
const wait=expr=>browser('wait','--fn',expr);
function start(){return new Promise((resolve,reject)=>{
 server=spawn(process.env.AUTOCODE_TEST_PYTHON,[path.join(__dirname,'delete_browser_fixture.py')],{cwd:root,env:{...process.env,AUTOCODE_FIXTURE_ROOT:path.join(evidence,'fixture')},stdio:['ignore','pipe','pipe']});
 let output='',errors='';server.stdout.on('data',c=>{output+=c;const line=output.split('\n').find(l=>l.startsWith('FIXTURE='));if(line)resolve(JSON.parse(line.slice(8)));});server.stderr.on('data',c=>errors+=c);server.on('error',reject);server.on('exit',code=>reject(Error('Fixture exited '+code+' '+errors)));
});}
const git=(root,...args)=>execFileSync('git',['-C',root,...args],{encoding:'utf8'}).trim();
let navigation=0;
function open(c){console.log('Open fixture '+c.run);const url=new URL(c.url);url.searchParams.set('fixture_navigation',String(++navigation));browser('open',url.toString());wait('typeof latestRun!=="undefined"&&latestRun?.run==='+JSON.stringify(c.run)+'&&!taskReadError');}
function menu(){if(!data('()=>document.querySelector("#task-detail .context-menu").open'))browser('click','#task-detail summary[aria-label="Task settings"]');assert.equal(data('()=>document.querySelector("#task-detail .context-menu").open'),true);}
function review(){menu();browser('click','#delete-task');assert.equal(data('()=>document.querySelector("#task-delete-dialog").open'),true,'Delete click opens exact scope dialog');wait('!taskDeleteReview?.busy&&(taskDeleteReview?.preview?.preview_id||!document.querySelector("#task-delete-error").hidden)');}
function managed(){browser('click','#task-delete-worktree');wait('!taskDeleteReview?.busy&&taskDeleteReview?.preview?.include_worktree');browser('click','#task-delete-branch');wait('!taskDeleteReview?.busy&&taskDeleteReview?.preview?.include_branch');}
function clickConfirmation(){
 data('()=>{document.querySelector("#task-delete-confirm").scrollIntoView({block:"center",behavior:"instant"});return true}');
 const target=data('()=>{const b=document.querySelector("#task-delete-confirm"),r=b.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return {disabled:b.disabled,top:r.top,bottom:r.bottom,height:innerHeight,hit:hit?.closest("#task-delete-confirm")===b}}');
 assert.equal(target.disabled,false);assert.ok(target.top>=0&&target.bottom<=target.height,'Confirmation control is in the viewport');assert.equal(target.hit,true,'Confirmation control receives the pointer');
 browser('click','#task-delete-confirm');wait('!taskDeleteReview?.busy&&(!document.querySelector("#task-delete-dialog").open||!document.querySelector("#task-delete-error").hidden)');
}
function confirm(){const phrase=data('()=>taskDeleteReview.preview.confirmation');browser('fill','#task-delete-confirmation',phrase);assert.equal(data('()=>document.querySelector("#task-delete-confirmation").value'),phrase);clickConfirmation();}

(async()=>{
 let fixture=await start();const captures=[];
 for(const [name,w,h] of [['desktop',1440,1024],['tablet',1024,768],['mobile',390,844]]){
  const c=fixture.cases[name];browser('set','viewport',String(w),String(h));open(c);menu();browser('click','#archive-task');
  assert.match(data('()=>document.querySelector("#task-archive-dialog").textContent'),/files|history/i);
  browser('click','#task-archive-cancel');assert.ok(fs.existsSync(c.run));
  browser('click','#archive-task');browser('click','#task-archive-confirm');wait('!document.querySelector("#task-archive-dialog").open&&!archivePending.size');assert.ok(fs.existsSync(c.run));
  browser('click','#archive-notice button:first-of-type');wait('!archivePending.size&&!archivedTask('+JSON.stringify(c.run)+')');
  open(c);review();assert.equal(data('()=>document.querySelector("#task-delete-confirm").disabled'),true);
  browser('click','#task-delete-cancel');assert.ok(fs.existsSync(c.run));
  // The settings menu stays open after closing its dialog.
  browser('click','#delete-task');wait('!!taskDeleteReview?.preview?.preview_id&&!taskDeleteReview.busy');managed();
  const scope=data('()=>taskDeleteReview.preview.scope');assert.deepEqual(scope.map(x=>x.kind),['run','worktree','branch']);assert.equal(scope[0].path,c.run);assert.equal(scope[1].path,c.workspace);assert.equal(scope[2].path,c.branch);
  browser('fill','#task-delete-confirmation','delete something else');assert.equal(data('()=>document.querySelector("#task-delete-confirm").disabled'),true);
  assert.equal(data('()=>document.documentElement.scrollWidth>innerWidth'),false);
  const shot=path.join(evidence,name+'-delete-preview.png');browser('screenshot',shot);captures.push(shot);confirm();
  wait('!document.querySelector("#task-delete-dialog").open');assert.equal(fs.existsSync(c.workspace),false);assert.equal(git(fixture.project,'for-each-ref','--format=%(refname)','refs/heads/'+c.branch),'');
  assert.equal(fs.readFileSync(path.join(fixture.project,'keep.txt'),'utf8'),'project must survive');assert.ok(fs.existsSync(fixture.cases.sibling.run));
  browser('reload');wait('typeof latestData!=="undefined"&&!!latestData');assert.equal(data('()=>latestData.runs.some(r=>r.run==='+JSON.stringify(c.run)+')'),false);
 }
 // A stale preview refuses newly written files without deleting anything.
 browser('set','viewport','1440','1024');open(fixture.cases.stale);review();fs.writeFileSync(path.join(fixture.cases.stale.run,'new-work'),'preserve');confirm();
 assert.match(data('()=>document.querySelector("#task-delete-error").textContent'),/changed/);assert.equal(fs.readFileSync(path.join(fixture.cases.stale.run,'new-work'),'utf8'),'preserve');browser('click','#task-delete-cancel');
 for(const name of ['live','unknown']){open(fixture.cases[name]);review();assert.equal(data('()=>document.querySelector("#task-delete-confirm").disabled'),true);assert.match(data('()=>document.querySelector("#task-delete-error").textContent'),/worker|reconcile|uncertain/i);browser('click','#task-delete-cancel');assert.ok(fs.existsSync(fixture.cases[name].run));}
 // Registry cleanup fails once after files/branch removal. Restart the server
 // and browser: the durable request must remain reachable in Archived.
 const c=fixture.cases.partial;open(c);review();managed();confirm();assert.match(data('()=>document.querySelector("#task-delete-error").textContent'),/discovery cleanup failure/);assert.equal(fs.existsSync(c.workspace),false);assert.equal(git(fixture.project,'for-each-ref','--format=%(refname)','refs/heads/'+c.branch),'');browser('click','#task-delete-cancel');
 server.kill('SIGTERM');await new Promise(resolve=>server.once('exit',resolve));fixture=await start();
 browser('open',fixture.base_url+'/#archived');wait('typeof latestData!=="undefined"&&latestData?.pending_deletions?.length===1');data('()=>{setView("archived");return true}');browser('click','#pending-deletions button');wait('!!taskDeleteReview?.preview?.preview_id');
 assert.equal(data('()=>document.querySelector("#task-delete-confirm").textContent'),'Retry same deletion');
 git(fixture.project,'branch',c.branch,c.head);clickConfirmation();assert.equal(data('()=>document.querySelector("#task-delete-confirm").disabled'),true);assert.match(data('()=>document.querySelector("#task-delete-error").textContent'),/recreated/);assert.equal(git(fixture.project,'rev-parse','refs/heads/'+c.branch),c.head);
 const blocked=path.join(evidence,'recreated-branch-retained.png');browser('screenshot',blocked);captures.push(blocked);browser('click','#task-delete-cancel');browser('reload');wait('typeof latestData!=="undefined"&&latestData?.pending_deletions?.[0]?.retry_blocked');data('()=>{setView("archived");return true}');browser('click','#pending-deletions button');assert.equal(data('()=>document.querySelector("#task-delete-confirm").disabled'),true);
 const removed=['desktop','tablet','mobile'].map(name=>fixture.cases[name]);
 const rows=data('()=>latestData.runs');
 for(const gone of removed){
  assert.equal(rows.some(row=>row.run===gone.run),false,'Deleted run does not reappear');
  assert.equal(rows.some(row=>row.workspace===gone.workspace),false,'Deleted workspace does not reappear as a diagnostic');
 }
 const errors=browser('errors').trim();assert.ok(!errors||/^No (?:page )?errors\.?$/i.test(errors),errors);
 fs.writeFileSync(path.join(evidence,'manifest.json'),JSON.stringify({captures,viewports:[1440,1024,390],archiveRestored:true,restartPreservedReceipt:true,recreatedBranchRetained:true,errors},null,2));
 console.log('Archive/delete browser matrix passed: exact scope, cancellation, preserved siblings, stale/live/unknown refusal, restart and recreated-branch protection. '+evidence);
})().catch(e=>{console.error(e);try{console.error(JSON.stringify(data('()=>({hash:location.hash,view:currentView,error:taskReadError,notice:document.querySelector("#dashboard-notice").textContent,deleteError:document.querySelector("#task-delete-error").textContent,run:latestRun?.run})')));browser('screenshot',path.join(evidence,'failure.png'))}catch{}process.exitCode=1;}).finally(()=>{try{browser('close')}catch{}if(server)server.kill('SIGTERM');});
