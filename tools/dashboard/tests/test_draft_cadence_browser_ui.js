// Real HTTP and browser checks with offline provider callbacks only.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn,execFileSync}=require('node:child_process');
const root=path.resolve(__dirname,'../../..'),session='draft-cadence-'+process.pid;
const evidence=path.join(root,'.autocode/evidence/draft-cadence','run-'+process.pid);fs.mkdirSync(evidence,{recursive:true});
const server=spawn(process.env.AUTOCODE_TEST_PYTHON||'python3',[path.join(__dirname,'draft_cadence_browser_fixture.py')],{cwd:root,env:{...process.env,AUTOCODE_FIXTURE_ROOT:path.join(evidence,'fixture')},stdio:['ignore','pipe','pipe']});
const browser=(...args)=>execFileSync('agent-browser',['--session',session,...args],{cwd:root,encoding:'utf8',timeout:45000});
const data=fn=>JSON.parse(JSON.parse(browser('eval','JSON.stringify(('+fn+')())').trim()));
const wait=expression=>browser('wait','--fn',expression);
const ready=new Promise((resolve,reject)=>{let output='',errors='';server.stdout.on('data',chunk=>{output+=chunk;const line=output.split('\n').find(line=>line.startsWith('FIXTURE='));if(line)resolve(JSON.parse(line.slice(8)));});server.stderr.on('data',chunk=>errors+=chunk);server.on('error',reject);server.on('exit',code=>reject(Error('Fixture exited '+code+': '+errors)));});
(async()=>{
 const fixture=await ready,results=[];
 for(const [viewport,width,height]of [['desktop',1440,1024],['tablet',1024,768],['mobile',390,844]]){
  browser('set','viewport',String(width),String(height));browser('open',fixture.urls[viewport]);
  const expectedId=new URLSearchParams(new URL(fixture.urls[viewport]).hash.slice(1)).get('conversation');
  wait('typeof latestConversation!=="undefined"&&latestConversation?.id==='+JSON.stringify(expectedId)+'&&latestConversation.status==="ready"&&document.querySelector("#draft-text").dataset.conversation==='+JSON.stringify(expectedId));
  console.log('Opened '+viewport+' conversation '+expectedId);
  browser('fill','#draft-text','Build a chat workspace for '+viewport);
  const beforeSend=data('()=>({active:activeConversation,doc:latestConversation.id,text:document.querySelector("#draft-text").value,disabled:document.querySelector("#draft-send").disabled,dialogs:[...document.querySelectorAll("dialog[open]")].map(d=>d.id)})');
  console.log('Before first send '+JSON.stringify(beforeSend));assert.equal(beforeSend.text,'Build a chat workspace for '+viewport);assert.equal(beforeSend.disabled,false);assert.deepEqual(beforeSend.dialogs,[]);
  browser('click','#draft-send');
  wait('latestConversation?.status==="ready"&&latestConversation?.plan_drafts?.at(-1)?.freshness?.state==="fresh"');
  const first=data('()=>latestConversation.plan_drafts.at(-1).revision');
  browser('fill','#draft-text','Use SQLite for '+viewport);browser('click','#draft-send');
  wait('latestConversation?.status==="ready"&&latestConversation?.draft_update?.held');
  const saved=data('()=>latestConversation.messages'),turn=data('()=>latestConversation.draft_update.logical_turn_id');
  assert.equal(JSON.parse(fs.readFileSync(fixture.calls)).filter(row=>row.role==='planner'&&row.turn===turn).length,0,'A small answer does not immediately spend another Planner call');
  assert.match(data('()=>document.querySelector("#quiet-plan").textContent'),/draft update batched/);
  assert.equal(data('()=>document.querySelector("#attach-submit").disabled'),true,'A stale draft cannot be handed off as current');
  const button='#draft-messages [data-draft-update] button';
  data('()=>{document.querySelector('+JSON.stringify(button)+').scrollIntoView({block:"center"});return true}');
  assert.equal(data('()=>!!document.querySelector("#quiet-plan [data-draft-update]")'),false,'Required human action lives in chat');
  browser('fill','#draft-text','Keep this unsent thought');
  const bounds=data('()=>{const r=document.querySelector('+JSON.stringify(button)+').getBoundingClientRect();return {height:r.height,overflow:document.documentElement.scrollWidth>innerWidth}}');
  assert.ok(bounds.height>=44);assert.equal(bounds.overflow,false);
  const before=path.join(evidence,viewport+'-batched.png');browser('screenshot',before);
  data('()=>{const b=document.querySelector('+JSON.stringify(button)+');b.click();b.click();return true}');
  wait('!conversationPending.has(activeConversation)&&latestConversation?.plan_drafts?.at(-1)?.freshness?.state==="fresh"');
  assert.deepEqual(data('()=>latestConversation.messages'),saved,'Refreshing does not fabricate a human answer');
  assert.equal(data('()=>document.querySelector("#draft-text").value'),'Keep this unsent thought');
  assert.equal(JSON.parse(fs.readFileSync(fixture.calls)).filter(row=>row.role==='planner'&&row.turn===turn).length,1,'Duplicate clicks dispatch exactly once');
  assert.equal(data('()=>latestConversation.plan_drafts.at(-1).freshness.source_messages[0].excerpt'),'Use SQLite for '+viewport);
  assert.ok(data('()=>latestConversation.plan_drafts.at(-1).revision')>first);
  assert.equal(data('()=>latestConversation.attachment'),null,'Draft refresh neither attaches nor starts a run');
  browser('reload');wait('latestConversation?.plan_drafts?.at(-1)?.freshness?.state==="fresh"');
  assert.equal(data('()=>document.querySelector("#draft-text").value'),'Keep this unsent thought');
  if(viewport==='mobile'||viewport==='tablet'){
   data('()=>{[...document.querySelectorAll("#quiet-plan button")].find(b=>b.textContent==="View full draft plan").click();return true}');
   assert.match(data('()=>document.querySelector("#draft-plan-dialog").textContent'),/Updated from your messages/);
  }else assert.match(data('()=>document.querySelector("#quiet-plan").textContent'),/Updated from your messages/);
  const after=path.join(evidence,viewport+'-updated.png');browser('screenshot',after);
  results.push({viewport,bounds,before,after});
  if(viewport==='desktop'){
   for(const label of ['Safe draft failure','Ambiguous draft failure']){
    browser('fill','#draft-text',label);browser('click','#draft-send');
    wait('latestConversation?.status==="ready"&&latestConversation?.draft_update?.held');
    browser('click',button);
    wait('latestConversation?.plan_drafts?.at(-1)?.status==="failed"&&!conversationPending.has(activeConversation)');
    const failedTurn=data('()=>latestConversation.plan_drafts.at(-1).logical_turn_id');
    const history=data('()=>latestConversation.messages');
    assert.equal(data('()=>document.querySelector("#draft-problem").hidden'),false);
    assert.match(data('()=>document.querySelector("#quiet-plan").textContent'),/needs recovery/);
    if(label==='Safe draft failure'){
     assert.equal(data('()=>document.querySelector("#draft-problem button").textContent'),'Retry draft update');
     browser('click','#draft-problem button');
     wait('latestConversation?.plan_drafts?.at(-1)?.freshness?.state==="fresh"&&!conversationPending.has(activeConversation)');
     assert.equal(JSON.parse(fs.readFileSync(fixture.calls)).filter(row=>row.role==='planner'&&row.turn===failedTurn).length,2);
     assert.deepEqual(data('()=>latestConversation.messages'),history);
    }else{
     assert.equal(data('()=>document.querySelectorAll("#draft-problem button").length'),0,'An uncertain provider call cannot be replayed');
     browser('reload');wait('latestConversation?.planner_delivery?.state==="UNCERTAIN"');
     assert.equal(JSON.parse(fs.readFileSync(fixture.calls)).filter(row=>row.role==='planner'&&row.turn===failedTurn).length,1);
     assert.equal(data('()=>document.querySelector("#draft-send").disabled'),true);
    }
   }
  }

 }
 fs.writeFileSync(path.join(evidence,'manifest.json'),JSON.stringify(results,null,2));
 console.log('Batched drafts, chat refresh, provenance and reload passed at three sizes. '+evidence);
})().catch(error=>{console.error(error);try{browser('screenshot',path.join(evidence,'failure.png'));}catch{}process.exitCode=1;}).finally(()=>{try{browser('close');}catch{}server.kill('SIGTERM');});
