// Real browser question-card answers, suggestion provenance and re-asked requests.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn,execFileSync}=require('node:child_process');
const root=path.resolve(__dirname,'../../..'),session='question-cards-'+process.pid;
const evidence=path.join(root,'.autocode/evidence/question-cards','run-'+process.pid);fs.mkdirSync(evidence,{recursive:true});
const python=process.env.AUTOCODE_TEST_PYTHON||'python3';
const server=spawn(python,[path.join(__dirname,'unified_browser_fixture.py')],{cwd:root,env:{...process.env,AUTOCODE_FIXTURE_ROOT:path.join(evidence,'fixture')},stdio:['ignore','pipe','pipe']});
const browser=(...args)=>execFileSync('agent-browser',['--session',session,...args],{cwd:root,encoding:'utf8',timeout:45000});
const data=fn=>JSON.parse(JSON.parse(browser('eval','JSON.stringify(('+fn+')())').trim()));
const wait=expression=>browser('wait','--fn',expression);
const click=selector=>{
 data('()=>{document.querySelector('+JSON.stringify(selector)+').scrollIntoView({block:"center"});return true}');
 wait('(()=>{const e=document.querySelector('+JSON.stringify(selector)+');if(!e||e.disabled)return false;const r=e.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return e===hit||e.contains(hit)})()');
 browser('click',selector);
};

const ready=new Promise((resolve,reject)=>{let output='',errors='';server.stdout.on('data',chunk=>{output+=chunk;const line=output.split('\n').find(line=>line.startsWith('FIXTURE='));if(line)resolve(JSON.parse(line.slice(8)));});server.stderr.on('data',chunk=>errors+=chunk);server.on('error',reject);server.on('exit',code=>reject(Error('Fixture exited '+code+': '+errors)));});
(async()=>{
 const fixture=await ready,results=[];
 for(const [viewport,width,height]of [['desktop',1440,1024],['tablet',1024,768],['mobile',390,844]]){
  browser('set','viewport',String(width),String(height));
  for(const mode of ['partial','suggested']){
   const key='flow-answer-cards-'+mode+'-'+viewport;
   browser('open',fixture.scenarios[key]);
   wait('typeof latestRun!=="undefined"&&latestRun?.run?.endsWith('+JSON.stringify('/'+key)+')&&!taskReadError');
   const run=data('()=>latestRun.run'),stateFile=path.join(run,'state.json');
   assert.ok(stateFile.startsWith(path.join(evidence,'fixture')+path.sep),'Only this disposable fixture may be mutated');
   const initial=data('()=>resolverResponseFields(latestRun)');
   const card=id=>'[data-question-card="'+id+'"]';
   const suggestion=id=>click(card(id)+'>button');
   if(mode==='partial'){
    browser('fill','#change-text','Continue?');
    browser('fill',card('card-1')+' textarea','Continue?');
    browser('eval','(async()=>{await refresh();return true})()');
    assert.equal(data('()=>document.querySelector('+JSON.stringify(card('card-1')+' textarea')+').value'),'Continue?');
    browser('reload');wait('typeof latestRun!=="undefined"&&latestRun?.questions?.length===2');
    assert.equal(data('()=>document.querySelector('+JSON.stringify(card('card-1')+' textarea')+').value'),'Continue?');
    click(card('card-1')+' .question-answer-submit');
   }else suggestion('card-1');
   wait('latestRun.questions.length===1&&!taskChatPending.has(latestRun.run)');
   if(mode==='partial')assert.equal(data('()=>document.querySelector("#change-text").value'),'Continue?','A card answer preserves the independent composer draft, even with identical text');
   let saved=JSON.parse(fs.readFileSync(stateFile));
   assert.equal(saved.status,'WAITING_FOR_USER');assert.equal(saved._fixture_continue_count||0,0);
   assert.equal(saved.answers['card-1'].kind,mode==='partial'?'answer':'delegated');
   assert.equal(saved.answers['card-1'].text,mode==='partial'?'Continue?':'Continue');
   assert.equal(saved.answers['card-1'].actor,'user_cli');assert.ok(saved.answers['card-1'].at);
   assert.match(data('()=>document.querySelector("#task-progress-summary").textContent'),/1 question to answer/);
   suggestion('card-2');
   wait('latestRun.questions.length===0&&!taskChatPending.has(latestRun.run)');
   saved=JSON.parse(fs.readFileSync(stateFile));
   assert.equal(saved.answers['card-2'].kind,'delegated');assert.equal(saved.answers['card-2'].text,'Local workspace');
   assert.equal(saved.goal_contract.approval_status,'draft','Answers do not approve a plan');
   const summary='You answered 2 questions · '+(mode==='partial'?1:2)+' used the suggestion';
   wait('[...document.querySelectorAll(".answer-history>summary")].some(e=>e.textContent==='+JSON.stringify(summary)+')');
   browser('reload');wait('typeof latestRun!=="undefined"&&latestRun?.questions?.length===0');
   assert.ok(data('()=>[...document.querySelectorAll(".answer-history>summary")].map(e=>e.textContent)').includes(summary));
   data('()=>{const group=document.querySelector(".answer-history");group.open=true;group.scrollIntoView({block:"center"});return true}');
   assert.match(data('()=>document.querySelector(".answer-history").textContent'),/You accepted the suggested answer/);
   if(mode==='partial')assert.match(data('()=>document.querySelector(".answer-history").textContent'),/You wrote this answer/);
   const screenshot=path.join(evidence,viewport+'-'+mode+'.png');browser('screenshot',screenshot);
   assert.equal(data('()=>document.documentElement.scrollWidth>innerWidth'),false);
   if(mode==='partial'){
    // Simulate a new provider request in this fixture only. A stale card must
    // fail through the real HTTP adapter while leaving the new request intact.
    const reask=`import json,sys\nfrom pathlib import Path\nfrom unittest.mock import patch\nsys.path.insert(0,sys.argv[2])\nfrom unified_browser_fixture import publish_human_request,resolver_human,FIXTURE_SOURCE\np=Path(sys.argv[1]);s=json.loads(p.read_text());s['status']='WAITING_FOR_USER';s['phase']='DISCOVERING';s['pending_questions']=[{'id':'card-1','question':'What label after the correction?','why':'New requirement','options':[],'proposed_default':'Save'}];s['answers'].pop('card-1',None)\nwith patch.object(resolver_human.support,'snapshot',return_value={'revision':FIXTURE_SOURCE}):publish_human_request(s)\ntmp=p.with_suffix('.next');tmp.write_text(json.dumps(s));tmp.replace(p)\n`;
    execFileSync(python,['-c',reask,stateFile,__dirname],{cwd:root});
    const before=fs.readFileSync(stateFile);
    const old={workspace:data('()=>latestRun.workspace'),run,question_id:'card-1',text:'Old answer',explicit_answer:true,request_id:'old-card-'+viewport,...initial};
    const response=JSON.parse(browser('eval','(async()=>{const r=await fetch("/api/chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify('+JSON.stringify(old)+')});return JSON.stringify({status:r.status,body:await r.json()})})()').trim());
    const result=typeof response==='string'?JSON.parse(response):response;
    assert.equal(result.status,400);assert.deepEqual(fs.readFileSync(stateFile),before);
    browser('eval','(async()=>{await refresh();return true})()');
    wait('latestRun.questions[0]?.question==="What label after the correction?"');
    assert.equal(data('()=>document.querySelector('+JSON.stringify(card('card-1')+' textarea')+').value'),'');
    browser('fill',card('card-1')+' textarea','Save after review');
    click(card('card-1')+' .question-answer-submit');
    wait('latestRun.questions.length===0&&!taskChatPending.has(latestRun.run)');
    assert.equal(JSON.parse(fs.readFileSync(stateFile)).answers['card-1'].text,'Save after review');
    assert.equal(data('()=>document.querySelectorAll(".answer-history").length'),2,'Re-asked IDs stay distinct in saved history');
   }
   results.push({viewport,mode,summary,screenshot});
  }
 }
 fs.writeFileSync(path.join(evidence,'manifest.json'),JSON.stringify(results,null,2));
 console.log('Question cards, partial/all-suggested answers, reload and re-asked guards passed at three sizes. '+evidence);
})().catch(error=>{console.error(error);try{console.error(JSON.stringify(data('()=>({run:latestRun?.run,status:latestRun?.status,questions:latestRun?.questions,chatError:[...taskChatErrors],pending:[...taskChatPending],cards:[...document.querySelectorAll(".question-answer-form")].map(e=>({text:e.textContent,input:e.querySelector("textarea").value,disabled:e.querySelector("button").disabled})),errors:document.querySelector("#change-history")?.textContent})')));browser('screenshot',path.join(evidence,'failure.png'));}catch{}process.exitCode=1;}).finally(()=>{try{browser('close');}catch{}server.kill('SIGTERM');});
