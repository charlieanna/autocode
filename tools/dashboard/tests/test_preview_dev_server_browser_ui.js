// Real browser acceptance of Preview following the run's own development server (#730): the real
// `autocode serve start|stop` serves a real Git run, the dashboard reads it through `autocode --status`.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {spawn,execFileSync}=require('node:child_process');
const root=path.resolve(__dirname,'../../..'),session='preview-dev-server-'+process.pid,python=process.env.AUTOCODE_TEST_PYTHON||'python3';
const evidence=process.env.DASHBOARD_PREVIEW_SERVER_EVIDENCE_DIR||path.join(root,'.autocode/evidence/preview-dev-server','run-'+process.pid);
fs.mkdirSync(evidence,{recursive:true});let fixtureProcess,serve;
const browser=(...args)=>execFileSync('agent-browser',['--session',session,...args],{cwd:root,encoding:'utf8',timeout:45000});
const data=fn=>JSON.parse(JSON.parse(browser('eval','JSON.stringify(('+fn+')())').trim()));
const wait=expr=>browser('wait','--fn',expr);
const q=JSON.stringify,sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function click(selector){data('()=>{document.querySelector('+q(selector)+').scrollIntoView({block:"center",behavior:"instant"});return true}');browser('click',selector);}
const lines=[];let waiting=null;
function start(){return new Promise((resolve,reject)=>{fixtureProcess=spawn(python,[path.join(__dirname,'preview_dev_server_browser_fixture.py')],{cwd:root,env:{...process.env,AUTOCODE_FIXTURE_ROOT:path.join(evidence,'fixture')},stdio:['pipe','pipe','pipe']});let output='',errors='';fixtureProcess.stdout.on('data',c=>{output+=c;const parts=output.split('\n');output=parts.pop();for(const line of parts){if(line.startsWith('FIXTURE='))resolve(JSON.parse(line.slice(8)));else if(line.startsWith('STEPPED=')){lines.push(JSON.parse(line.slice(8)));waiting?.();}}});fixtureProcess.stderr.on('data',c=>errors+=c);fixtureProcess.on('error',reject);fixtureProcess.on('exit',code=>reject(Error('Fixture exited '+code+' '+errors)));});}
function step(broken){const count=lines.length;return new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('The fixture saved no step')),30000);waiting=()=>{if(lines.length>count){clearTimeout(timer);waiting=null;resolve(lines.at(-1));}};fixtureProcess.stdin.write(broken?'step broken\n':'step\n');});}
const autocode=(fixture,...args)=>[path.join(root,'tools/autocode.py'),...args,'--workspace',fixture.workspace,'--run-dir',fixture.run];
// Refresh as polling does until the run's status view shows what the test waits for.
async function refreshUntil(predicate,label){
 const deadline=Date.now()+60000;
 while(Date.now()<deadline){browser('eval','(async()=>{await refresh();return true})()');if(data('()=>{const server=latestRun?.status_view?.dev_server||{};return !!('+predicate+')}'))return;await sleep(250);}
 throw Error('Timed out waiting for '+label+': '+JSON.stringify(data('()=>latestRun?.status_view?.dev_server')));
}
const frameSrc=()=>data('()=>document.querySelector("#preview-content iframe")?.src||""');
const source=()=>data('()=>document.querySelector("#preview-source").textContent');
const page=async url=>(await fetch(url)).text();
function preview(width){if(width<760&&!data('()=>document.querySelector(".app-shell").classList.contains("details-open")'))click('#details-drawer-toggle');click('[data-tab="preview"]');wait('currentTab==="preview"');}
const noOverflow=()=>assert.equal(data('()=>document.documentElement.scrollWidth>innerWidth'),false,'no horizontal overflow');
async function shot(name){await sleep(800);const file=path.join(evidence,name+'.png');browser('screenshot',file);return file;}
(async()=>{
 const fixture=await start(),captures=[],stateFile=path.join(fixture.run,'state.json');
 const head=execFileSync('git',['-C',fixture.workspace,'rev-parse','HEAD'],{encoding:'utf8'}).trim();
 const command='autocode serve start --workspace '+fixture.workspace+' --run-dir '+fixture.run;
 browser('set','viewport','1440','1024');browser('open',fixture.url);
 wait('latestRun?.run==='+q(fixture.run)+'&&!!latestRun.status_view?.dev_server');
 preview(1440);

 // Configured but not started: say so and name the command that starts it.
 assert.equal(frameSrc(),'');assert.match(source(),/This run’s development server is not running\./);assert.ok(source().includes(command),source());
 captures.push(await shot('desktop-not-running'));

 // A pasted address is the fallback while the run's server is not running.
 browser('fill','#preview-url',fixture.pasted_url);click('#preview-form button[type="submit"]');
 wait('document.querySelector("#preview-content iframe")?.src==='+q(fixture.pasted_url));
 assert.match(source(),/Showing your saved address instead/);

 // `autocode serve start` runs it: Preview switches to the run's own server without being told its address.
 serve=spawn(python,autocode(fixture,'serve','start'),{cwd:root,stdio:['ignore','pipe','pipe']});let served='';serve.stdout.on('data',c=>served+=c);serve.stderr.on('data',c=>served+=c);
 await refreshUntil('server.status==="running"','the server to answer');
 const url=data('()=>latestRun.status_view.dev_server.url');
 assert.match(url,/^http:\/\/127\.0\.0\.1:\d+\/$/);assert.notEqual(url,fixture.pasted_url);
 wait('document.querySelector("#preview-content iframe")?.src==='+q(url));
 assert.match(await page(url),/Greeting app · version 1/);
 const running=source();
 assert.ok(running.includes('Showing this run’s development server · '+url),running);
 assert.match(running,/started on the run’s latest saved step/);
 assert.ok(running.includes('Your saved address ('+fixture.pasted_url+') is used when it is not running.'),running);
 assert.equal(data('()=>document.querySelector("#preview-open").href'),url);assert.equal(data('()=>document.querySelector("#preview-setup").open'),false);
 noOverflow();captures.push(await shot('desktop-running'));

 // Polling an unchanged server keeps the live frame.
 data('()=>{window.liveFrame=document.querySelector("#preview-content iframe");return true}');
 browser('eval','(async()=>{await refresh();await refresh();return true})()');
 assert.equal(data('()=>window.liveFrame===document.querySelector("#preview-content iframe")'),true,'polling keeps the frame');

 // The run saves a code-changing step: the server restarts on the same address and the preview reloads.
 const second=await step(false);
 await refreshUntil('server.status==="running"&&server.restarts===1&&server.code_checkpoint==='+q(second.checkpoint),'the restart on the new step');
 wait('!!document.querySelector("#preview-content iframe")&&window.liveFrame!==document.querySelector("#preview-content iframe")');
 assert.equal(frameSrc(),url,'the restarted server keeps its address');
 assert.match(await page(url),/Greeting app · version 2/);
 assert.match(source(),/started on the run’s latest saved step/);

 for(const [name,w,h] of [['tablet',1024,768],['mobile',390,844]]){
  browser('set','viewport',String(w),String(h));browser('open',fixture.url);
  wait('latestRun?.run==='+q(fixture.run)+'&&latestRun.status_view?.dev_server?.status==="running"');
  preview(w);wait('document.querySelector("#preview-content iframe")?.src==='+q(url));
  assert.ok(source().includes('Showing this run’s development server · '+url));
  assert.equal(data('()=>document.querySelector("#preview-content iframe").getBoundingClientRect().width<=innerWidth'),true);
  noOverflow();captures.push(await shot(name+'-running'));
 }

 // A step that breaks the app: the server exits, waits for the next change, and the pasted address shows meanwhile.
 browser('set','viewport','1440','1024');browser('open',fixture.url);
 wait('latestRun?.run==='+q(fixture.run)+'&&!!latestRun.status_view?.dev_server');preview(1440);
 const broken=await step(true);
 await refreshUntil('server.status==="exited"&&server.waiting&&server.code_checkpoint==='+q(broken.checkpoint),'the broken app to exit');
 wait('document.querySelector("#preview-content iframe")?.src==='+q(fixture.pasted_url));
 const crashed=source();
 assert.match(crashed,/not running: the server exited before it answered \(exit code 3\)/);
 assert.match(crashed,/It starts again after the run’s next code change\./);assert.match(crashed,/Showing your saved address instead/);
 assert.ok(!crashed.includes('autocode serve start'),'a waiting server needs no start command');
 captures.push(await shot('desktop-crashed'));

 // `autocode serve stop` ends it; Preview names the command that starts it again.
 execFileSync(python,autocode(fixture,'serve','stop'),{cwd:root,encoding:'utf8',timeout:60000});
 const exit=await new Promise(resolve=>serve.exitCode!==null?resolve(serve.exitCode):serve.on('exit',resolve));
 assert.equal(exit,0,served);assert.match(served,/Serving http:\/\/127\.0\.0\.1:\d+\/ again after a code change/);
 await refreshUntil('server.status==="stopped"','the server to stop');
 const stopped=source();
 assert.match(stopped,/not running: stopped by autocode serve stop\./);assert.doesNotMatch(stopped,/exit code/);assert.ok(stopped.includes(command),stopped);
 browser('set','viewport','390','844');browser('open',fixture.url);
 wait('latestRun?.run==='+q(fixture.run)+'&&latestRun.status_view?.dev_server?.status==="stopped"');preview(390);
 assert.ok(source().includes(command));noOverflow();captures.push(await shot('mobile-stopped'));

 const stateNow=crypto.createHash('sha256').update(fs.readFileSync(stateFile)).digest('hex');
 assert.equal(stateNow,broken.state_sha256,'Preview and autocode serve never write run state');
 assert.equal(execFileSync('git',['-C',fixture.workspace,'rev-parse','HEAD'],{encoding:'utf8'}).trim(),head);
 const errors=browser('errors').trim();assert.ok(!errors||/^No (?:page )?errors\.?$/i.test(errors),errors);
 fs.writeFileSync(path.join(evidence,'manifest.json'),JSON.stringify({captures,viewports:[1440,1024,390],realCli:true,url,restarts:2,readOnly:true,errors},null,2));
 console.log('Preview dev-server browser matrix passed: not running, pasted fallback, real serve start, restart on a new step, crash and wait, serve stop, read-only. '+evidence);
})().catch(e=>{console.error(e);try{console.error(JSON.stringify(data('()=>({tab:currentTab,server:latestRun?.status_view?.dev_server,source:document.querySelector("#preview-source")?.textContent,frame:document.querySelector("#preview-content iframe")?.src})')));browser('screenshot',path.join(evidence,'failure.png'));}catch{}process.exitCode=1;}).finally(()=>{try{browser('close')}catch{}if(serve&&serve.exitCode===null)serve.kill('SIGTERM');if(fixtureProcess)fixtureProcess.kill('SIGTERM');});
