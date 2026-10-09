// Real-browser chat intent and confirmation checks against disposable saved tasks.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {spawn,execFileSync}=require('node:child_process');
const root=path.resolve(__dirname,'../../..');
const evidence=process.env.DASHBOARD_INTENT_EVIDENCE_DIR||path.join(root,'.autocode/evidence/chat-intent','run-'+process.pid);
fs.mkdirSync(evidence,{recursive:true});
const session='chat-intent-'+process.pid;
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
  const url=fixture.scenarios['flow-chat-intent-'+viewport];
  browser('open',url);
  wait('typeof latestRun!=="undefined"&&latestRun?.run?.includes("flow-chat-intent-")&&!taskReadError');
  const run=data('()=>latestRun.run'),stateFile=path.join(run,'state.json'),before=fs.readFileSync(stateFile);
  const send=(text,count)=>{
   browser('fill','#change-text',text);browser('press','Enter');
   wait('(latestRun.chat_messages||[]).length==='+count+'&&!taskChatPending.has(latestRun.run)');
  };
  send("Why is this still open?",1);
  assert.equal(data('()=>latestRun.chat_messages[0].kind'),'question');
  assert.match(data('()=>document.querySelector("#conversation").innerText'),/Saved task status/);
  for(const [index,text]of ['yes','approve','stop'].entries())send(text,index+2);
  assert.deepEqual(data('()=>latestRun.chat_messages.map(row=>row.kind)'),['question','approval','approval','control']);
  assert.deepEqual(fs.readFileSync(stateFile),before,viewport+' status and typed controls leave saved state byte-identical');
  send('Actually, use SMS instead of email',5);
  assert.equal(data('()=>latestRun.chat_messages[4].status'),'awaiting_confirmation');
  assert.deepEqual(fs.readFileSync(stateFile),before,viewport+' unconfirmed change leaves saved state byte-identical');
  assert.equal(data('()=>document.querySelectorAll("#conversation .chat-intent button").length'),2);
  data('()=>{document.querySelector("#conversation .chat-intent button").scrollIntoView({block:"center"});return true}');
  const bounds=data('()=>({overflow:document.documentElement.scrollWidth>innerWidth,buttons:[...document.querySelectorAll("#conversation .chat-intent button")].map(e=>({text:e.textContent,height:e.getBoundingClientRect().height}))})');
  assert.equal(bounds.overflow,false);
  assert.ok(bounds.buttons.every(row=>row.height>=44),viewport+' confirmation targets remain usable');
  const screenshot=path.join(evidence,viewport+'-confirmation.png');browser('screenshot',screenshot);
  click('#conversation .chat-intent button:last-child');
  wait('latestRun.chat_messages[4].confirmation?.status==="declined"&&!taskChatPending.has(latestRun.run)');
  assert.deepEqual(fs.readFileSync(stateFile),before,viewport+' declined correction makes no runner mutation');
  send('Use SMS instead of email',6);
  click('#conversation .chat-intent button:first-of-type');
  wait('latestRun.chat_messages[5].confirmation?.status==="confirmed"&&latestRun.chat_messages[5].status==="received"&&!taskChatPending.has(latestRun.run)');
  const saved=JSON.parse(fs.readFileSync(stateFile,'utf8')),receipt=data('()=>latestRun.chat_messages[5]');
  assert.equal(saved._fixture_interventions.entries.length,1);
  assert.equal(receipt.kind,'correction');assert.equal(receipt.receipt.durable,true);
  assert.equal(receipt.receipt.text,'Use SMS instead of email');
  assert.equal(saved.goal_contract.approval_status,JSON.parse(before).goal_contract.approval_status,'queued delivery is not fake re-planning or approval');
  browser('reload');wait('typeof latestRun!=="undefined"&&latestRun?.chat_messages?.length===6');
  assert.equal(data('()=>latestRun.chat_messages[5].confirmation.status'),'confirmed');
  const errors=browser('errors').trim();assert.ok(/^(?:|\[\]|No (?:page )?errors\.?)$/i.test(errors),errors);
  browser('open',fixture.scenarios['pending-answer']);
  wait('typeof latestRun!=="undefined"&&latestRun?.run?.endsWith("/pending-answer")&&!taskReadError');
  const pendingFile=path.join(data('()=>latestRun.run'),'state.json'),pendingBefore=fs.readFileSync(pendingFile);
  let count=data('()=>latestRun.chat_messages.length');
  const questionsBefore=data('()=>latestRun.questions.length');
  for(const text of ['How is it going?','stop']){
   send(text,++count);
   assert.deepEqual(fs.readFileSync(pendingFile),pendingBefore,viewport+' status/control cannot answer the selected question');
   assert.equal(data('()=>latestRun.questions.length'),questionsBefore);
  }
  results.push({viewport,width,height,screenshot,bounds,receipt,questions_preserved:questionsBefore});
 }
 const files=['dashboard_app.js','dashboard_chat.py','dashboard_chat_intent.py'].map(file=>({file,sha256:crypto.createHash('sha256').update(fs.readFileSync(path.join(root,'tools/dashboard',file))).digest('hex')}));
 fs.writeFileSync(path.join(evidence,'manifest.json'),JSON.stringify({files,results},null,2));
 console.log('Chat intent real-browser checks passed at desktop, tablet and mobile. '+evidence);
})().catch(error=>{console.error(error);try{console.error(JSON.stringify(data('()=>({run:latestRun?.run,chat:latestRun?.chat_messages,errors:[...taskChatErrors],pending:[...taskChatPending],buttons:[...document.querySelectorAll("#conversation .chat-intent button")].map(e=>({text:e.textContent,disabled:e.disabled,bounds:e.getBoundingClientRect().toJSON()}))})')));browser('screenshot',path.join(evidence,'failure.png'));}catch{}process.exitCode=1;}).finally(()=>{try{browser('close');}catch{}server.kill('SIGTERM');});
