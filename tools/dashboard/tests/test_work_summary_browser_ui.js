// Real browser acceptance of the saved progress strip and read-only detail links.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn,execFileSync}=require('node:child_process');
const root=path.resolve(__dirname,'../../..'),session='work-summary-'+process.pid;
const evidence=path.join(root,'.autocode/evidence/work-summary','run-'+process.pid);fs.mkdirSync(evidence,{recursive:true});
const server=spawn(process.env.AUTOCODE_TEST_PYTHON||'python3',[path.join(__dirname,'unified_browser_fixture.py')],{cwd:root,env:{...process.env,AUTOCODE_FIXTURE_ROOT:path.join(evidence,'fixture')},stdio:['ignore','pipe','pipe']});
const browser=(...args)=>execFileSync('agent-browser',['--session',session,...args],{cwd:root,encoding:'utf8',timeout:45000});
const data=fn=>JSON.parse(JSON.parse(browser('eval','JSON.stringify(('+fn+')())').trim()));
const wait=expression=>browser('wait','--fn',expression);
const ready=new Promise((resolve,reject)=>{let output='',errors='';server.stdout.on('data',chunk=>{output+=chunk;const line=output.split('\n').find(line=>line.startsWith('FIXTURE='));if(line)resolve(JSON.parse(line.slice(8)));});server.stderr.on('data',chunk=>errors+=chunk);server.on('error',reject);server.on('exit',code=>reject(Error('Fixture exited '+code+': '+errors)));});
(async()=>{
 const fixture=await ready,results=[];
 for(const [viewport,width,height]of [['desktop',1440,1024],['tablet',1024,768],['mobile',390,844]]){
  browser('set','viewport',String(width),String(height));
  for(const state of ['running','waiting','complete']){
   browser('open',fixture.scenarios['flow-work-progress-'+state]);
   wait('typeof latestRun!=="undefined"&&latestRun?.run?.endsWith("/flow-work-progress-'+state+'")&&!taskReadError');
   const run=data('()=>latestRun.run'),stateFile=path.join(run,'state.json'),before=fs.readFileSync(stateFile);
   const strip=data('()=>document.querySelector("#task-progress-summary").textContent');
   assert.match(strip,state==='complete'?/2 of 2 tasks complete/:/1 of 2 tasks complete/);
   assert.match(strip,state==='complete'?/6 of 6 requirements checked/:/1 of 6 requirements checked · 1 failed · 4 unchecked/);
   if(state==='waiting')assert.match(strip,/questions to answer/);
   assert.doesNotMatch(strip,/%/);
   browser('click','#task-progress-summary');
   const tasks=data('()=>[...document.querySelectorAll("#now .work-task-rows li")].map(row=>row.dataset.taskState)');
   assert.deepEqual(tasks,state==='complete'?['done','done']:state==='waiting'?['done','waiting']:['done','working']);
   wait('(()=>{const r=document.querySelector("#now .work-task-rows li").getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight})()');
   browser('screenshot',path.join(evidence,viewport+'-'+state+'-work.png'));
   if(state!=='complete'){
    data('()=>{const button=[...document.querySelectorAll("#now button")].find(e=>e.textContent==="Error message is hidden");button.closest("details").open=true;return true}');
    browser('get','text','#now');
    data('()=>{[...document.querySelectorAll("#now button")].find(e=>e.textContent==="Error message is hidden").click();return true}');
    assert.equal(data('()=>currentTab'),'execution');
    browser('eval','(async()=>{await refresh();return true})()');
    assert.equal(data('()=>document.activeElement.dataset.workId'),'F7');
    assert.match(data('()=>document.activeElement.textContent'),/Reported by Validator/);
    data('()=>{activateTab("now");return true}');
   }
   data('()=>{document.querySelector("#now .work-check-row button").click();return true}');
   assert.equal(data('()=>currentTab'),'execution');
    browser('eval','(async()=>{await refresh();return true})()');
   assert.equal(data('()=>document.activeElement.dataset.workId'),'C1');
   assert.deepEqual(fs.readFileSync(stateFile),before,'Inspecting saved tasks, problems and checks is read-only');
   if(viewport==='mobile')browser('click','#details-drawer-close');
   const geometry=data('()=>({overflow:document.documentElement.scrollWidth>innerWidth,strip:document.querySelector("#task-progress-summary").getBoundingClientRect().height,chat:document.querySelector("#interview").getBoundingClientRect().height,composer:document.querySelector("#change-text").getBoundingClientRect().bottom})');
   assert.equal(geometry.overflow,false);assert.ok(geometry.strip>=44);assert.ok(geometry.chat>=100);assert.ok(geometry.composer<=height);
   const screenshot=path.join(evidence,viewport+'-'+state+'.png');browser('screenshot',screenshot);results.push({viewport,state,strip,tasks,geometry,screenshot});
  }
 }
 fs.writeFileSync(path.join(evidence,'manifest.json'),JSON.stringify(results,null,2));
 console.log('Saved Work summaries and detail links passed at three sizes and three lifecycle states. '+evidence);
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(()=>{try{browser('close');}catch{}server.kill('SIGTERM');});
