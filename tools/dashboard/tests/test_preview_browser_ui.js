// Actual page/HTTP preview and recorded screenshot cards, using disposable tasks.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {spawn,execFileSync}=require('node:child_process');
const root=path.resolve(__dirname,'../../..'),session='project-preview-'+process.pid;
const evidence=path.join(root,'.autocode/evidence/project-preview','run-'+process.pid);fs.mkdirSync(evidence,{recursive:true});
const server=spawn(process.env.AUTOCODE_TEST_PYTHON||'python3',[path.join(__dirname,'unified_browser_fixture.py')],{cwd:root,env:{...process.env,AUTOCODE_FIXTURE_ROOT:path.join(evidence,'fixture'),AUTOCODE_PREVIEW_FIXTURE:'1'},stdio:['ignore','pipe','pipe']});
const browser=(...args)=>execFileSync('agent-browser',['--session',session,...args],{cwd:root,encoding:'utf8',timeout:45000});
const data=fn=>JSON.parse(JSON.parse(browser('eval','JSON.stringify(('+fn+')())').trim()));
const wait=expression=>browser('wait','--fn',expression);
const click=selector=>{data('()=>{document.querySelector('+JSON.stringify(selector)+').scrollIntoView({block:"center"});return true}');wait('(()=>{const e=document.querySelector('+JSON.stringify(selector)+'),r=e?.getBoundingClientRect(),hit=r&&document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return e&&!e.disabled&&(hit===e||e.contains(hit))})()');browser('click',selector);};
const ready=new Promise((resolve,reject)=>{let output='',errors='';server.stdout.on('data',chunk=>{output+=chunk;const line=output.split('\n').find(line=>line.startsWith('FIXTURE='));if(line)resolve(JSON.parse(line.slice(8)));});server.stderr.on('data',chunk=>errors+=chunk);server.on('error',reject);server.on('exit',code=>reject(Error('Fixture exited '+code+': '+errors)));});
(async()=>{
 const fixture=await ready,results=[];
 for(const [viewport,width,height]of [['desktop',1440,1024],['tablet',1024,768],['mobile',390,844]]){
  browser('set','viewport',String(width),String(height));
  browser('open',fixture.preview_url);const captured=path.join(evidence,viewport+'-app.png');browser('screenshot',captured);
  // Record this real demo-app capture as evidence in our disposable tasks.
  for(const key of ['flow-preview-first','flow-preview-second']){
   const params=new URLSearchParams(new URL(fixture.scenarios[key]).hash.slice(1)),run=params.get('run');
   assert.ok(run.startsWith(path.join(evidence,'fixture')+path.sep));
   const file=path.join(run,'state.json'),state=JSON.parse(fs.readFileSync(file));
   fs.copyFileSync(captured,path.join(run,'screen.png'));
   state.validation.evidence_hashes[path.join(run,'screen.png')]=crypto.createHash('sha256').update(fs.readFileSync(captured)).digest('hex');
   fs.writeFileSync(file,JSON.stringify(state));
  }
  const open=key=>{browser('open',fixture.scenarios[key]);wait('typeof latestRun!=="undefined"&&latestRun?.run?.endsWith('+JSON.stringify('/'+key)+')&&!taskReadError');};
  const preview=()=>{if(width<760&&!data('()=>document.querySelector(".app-shell").classList.contains("details-open")'))click('#details-drawer-toggle');click('[data-tab="preview"]');};
  open('flow-preview-first');
  data('()=>{localStorage.removeItem("autocode:"+previewStorageKey(latestRun));return true}');
  const run=data('()=>latestRun.run'),file=path.join(run,'state.json');
  const shot='.screenshot-evidence img';
  data('()=>{document.querySelector('+JSON.stringify(shot)+').scrollIntoView({block:"center"});return true}');
  wait('document.querySelector('+JSON.stringify(shot)+').naturalWidth>0');
  assert.match(data('()=>document.querySelector(".screenshot-evidence").textContent'),/Saved screenshot · C1.*Recorded result: FAIL.*fixture-screen-source/s);
  assert.match(data('()=>document.querySelector(".screenshot-evidence").textContent'),/does not approve it/);
  browser('screenshot',path.join(evidence,viewport+'-evidence.png'));
  click('.screenshot-evidence button');assert.equal(data('()=>currentTab'),'execution');assert.equal(data('()=>document.activeElement.dataset.workId'),'C1');
  if(width<760)click('#details-drawer-close');
  preview();assert.equal(data('()=>!!document.querySelector("#preview-content iframe")'),false);
  browser('fill','#preview-url',fixture.preview_url);click('#preview-form button');
  wait('!!document.querySelector("#preview-content iframe")');assert.equal(data('()=>document.querySelector("#preview-content iframe").src'),fixture.preview_url);
  data('()=>{window.recordedPreviewFrame=document.querySelector("#preview-content iframe");return true}');
  browser('eval','(async()=>{await refresh();return true})()');assert.equal(data('()=>recordedPreviewFrame===document.querySelector("#preview-content iframe")'),true,'Polling preserves the live iframe');
  // A newly saved code-changing step refreshes once; later polls retain it.
  const modified=JSON.parse(fs.readFileSync(file));
  modified.stages.push({stage:'terra',exit_code:0,finished_at:'fixture-'+viewport,changed_files:['app.js'],source_revision:'new-'+viewport});
  fs.writeFileSync(file,JSON.stringify(modified));
  browser('eval','(async()=>{await refresh();return true})()');
  assert.equal(data('()=>recordedPreviewFrame===document.querySelector("#preview-content iframe")'),false,'Saved code change refreshes the preview');
  data('()=>{window.recordedPreviewFrame=document.querySelector("#preview-content iframe");return true}');
  browser('eval','(async()=>{await refresh();return true})()');
  assert.equal(data('()=>recordedPreviewFrame===document.querySelector("#preview-content iframe")'),true);
  const afterFixtureChange=fs.readFileSync(file);
  browser('screenshot',path.join(evidence,viewport+'-preview.png'));
  open('flow-preview-second');preview();assert.equal(data('()=>document.querySelector("#preview-content iframe").src'),fixture.preview_url,'A sibling conversation reuses the project address');
  browser('reload');wait('typeof latestRun!=="undefined"&&!!latestRun&&!taskReadError');
  if(data('()=>currentTab')!=='preview')preview();
  wait('!!document.querySelector("#preview-content iframe")');
  assert.equal(data('()=>document.querySelector("#preview-url").value'),fixture.preview_url,'Reload preserves the project address');
  open('flow-preview-other');preview();assert.equal(data('()=>!!document.querySelector("#preview-content iframe")'),false,'Another project starts empty');
  assert.match(data('()=>document.querySelector("#preview-empty-note").textContent'),/No app address/);
  // Denied persistent storage still permits an explicit current-session preview.
  data('()=>{window.originalStorageSet=Storage.prototype.setItem;Storage.prototype.setItem=function(){throw new DOMException("Storage blocked","SecurityError")};return true}');
  browser('fill','#preview-url',fixture.preview_url);click('#preview-form button');
  assert.equal(data('()=>document.querySelector("#preview-content iframe").src'),fixture.preview_url);
  assert.match(data('()=>document.querySelector("#preview-empty-note").textContent'),/browser session only/);
  data('()=>{Storage.prototype.setItem=window.originalStorageSet;return true}');
  browser('reload');wait('typeof latestRun!=="undefined"&&!!latestRun&&!taskReadError');
  if(data('()=>currentTab')!=='preview')preview();
  assert.equal(data('()=>!!document.querySelector("#preview-content iframe")'),false,'A session-only address does not claim to survive reload');

  assert.deepEqual(fs.readFileSync(file),afterFixtureChange,'Inspection and preview never change the runner checkpoint');
  assert.equal(data('()=>document.documentElement.scrollWidth>innerWidth'),false);
  results.push({viewport,preview_url:fixture.preview_url,screenshot:path.join(evidence,viewport+'-preview.png')});
 }
 fs.writeFileSync(path.join(evidence,'manifest.json'),JSON.stringify(results,null,2));
 console.log('Project preview persistence, cross-project isolation and real screenshot cards passed at three sizes. '+evidence);
})().catch(error=>{console.error(error);try{browser('screenshot',path.join(evidence,'failure.png'));}catch{}process.exitCode=1;}).finally(()=>{try{browser('close');}catch{}server.kill('SIGTERM');});
