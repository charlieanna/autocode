// Approval is two ordered, revision-bound requests; uncertain outcomes never start work.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('./dashboard_vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../dashboard_app.js'),'utf8');
const cleanup=vm.createContext({});vm.runInContext(source.slice(source.indexOf('function expiredTemporaryEntry('),source.indexOf('function dashboardProjects(')),cleanup);
assert.equal(cleanup.expiredTemporaryEntry({workspace:'/tmp/old',error:'workspace_missing'}),false,'Error strings do not prove absence or stopped workers');
assert.equal(cleanup.expiredTemporaryEntry({workspace:'/tmp/old',workspace_cleanup:{eligible:true,reason:'confirmed_missing'}}),true);
assert.equal(cleanup.expiredTemporaryEntry({workspace:'/tmp/old',workspace_cleanup:{eligible:false,reason:'worker_unknown'}}),false);
const approvalSource=source.slice(source.indexOf('function approvalReceiptState('),source.indexOf('function currentConversationDraft('))+source.slice(source.indexOf('async function approveAndBuild('),source.indexOf('function renderInlineTaskAction('));
function harness({receipt={status:'finished',id:'approve-1'},fresh={},started={status:'queued'},rejectRead=false}={}){
 const calls=[],notices=[],run={workspace:'/fixture',run:'/fixture/run',goal_token:'r7:abc',goal:{revision:7,hash:'abc',approval_status:'draft'}},state=new Map();
 const ctx=vm.createContext({approveBuildState:state,approvalReceiptWaiters:new Map(),taskActionBusy:()=>false,taskReadError:'',statusInfo:()=>({label:'Approve plan'}),latestRun:run,renderBrief(){},renderInlineTaskAction(){},dashboardNotice:m=>notices.push(m),refresh:async()=>{},encodeURIComponent,
   submitTaskAction:async(r,action,extra)=>{calls.push({action,extra});return action==='approve_goal'?receipt:started;},
   api:async()=>{if(rejectRead)throw Error('Read unavailable');return {run:'/fixture/run',goal_token:'r7:abc',goal:{revision:7,hash:'abc',approval_status:'approved',approval_event:{token:'r7:abc',at:'2026-09-30T00:00:00Z'}},...fresh};}});
 vm.runInContext(approvalSource,ctx);return {run,calls,notices,ctx,state};
}
(async()=>{
 const valid=harness();await valid.ctx.approveAndBuild(valid.run);
 assert.deepEqual(valid.calls.map(c=>c.action),['approve_goal','continue']);
 assert.equal(valid.calls[0].extra.token,'r7:abc');assert.equal(valid.calls[1].extra.expected_goal_token,'r7:abc');assert.equal(valid.state.size,0);
 const cleared=harness({fresh:{goal_token:''}});await cleared.ctx.approveAndBuild(cleared.run);assert.deepEqual(cleared.calls.map(c=>c.action),['approve_goal','continue'],'A saved approval starts the same revision after its display token clears');
 for(const config of [{receipt:null},{rejectRead:true},{fresh:{goal_token:'r8:new'}},{fresh:{goal:{approval_status:'draft'}}},{fresh:{goal:{approval_status:'approved'}}},{fresh:{state_error:'unreadable'}},{fresh:{conversation:{plan_gate:{pending_product_change:true}}}}]){
   const h=harness(config);await h.ctx.approveAndBuild(h.run);assert.deepEqual(h.calls.map(c=>c.action),['approve_goal'],'Unconfirmed or stale approval never sends start');assert.equal(h.notices.length,1);assert.equal(h.state.size,0);
 }
 const uncertain=harness({started:{status:'uncertain'}});await uncertain.ctx.approveAndBuild(uncertain.run);assert.match(uncertain.notices[0],/could not be confirmed/);assert.equal(uncertain.calls.length,2,'No start replay');
 const queued=harness({receipt:{id:'approve-1',status:'queued'},fresh:{goal:{approval_status:'draft'},actions:[{id:'approve-1',status:'running'}]}});const waiting=queued.ctx.approveAndBuild(queued.run);await new Promise(setImmediate);assert.equal(queued.calls.length,1,'Queued approval does not start work');queued.ctx.settleApprovalReceipt({run:queued.run.run,goal_token:'r7:abc',goal:{revision:7,hash:'abc',approval_status:'approved',approval_event:{token:'r7:abc'}},actions:[{id:'approve-1',status:'finished',exit_status:0}]});await waiting;assert.deepEqual(queued.calls.map(call=>call.action),['approve_goal','continue']);
 const failed=harness({receipt:{id:'approve-1',status:'queued'},fresh:{goal:{approval_status:'draft'},actions:[{id:'approve-1',status:'running'}]}});const failedWait=failed.ctx.approveAndBuild(failed.run);await new Promise(setImmediate);failed.ctx.settleApprovalReceipt({run:failed.run.run,goal_token:'r7:abc',actions:[{id:'approve-1',status:'failed'}]});await failedWait;assert.equal(failed.calls.length,1,'Failed approval never starts work');
 const cancelled=harness({receipt:{id:'approve-1',status:'queued'},fresh:{actions:[{id:'approve-1',status:'queued'}]}});const cancelWait=cancelled.ctx.approveAndBuild(cancelled.run);await new Promise(setImmediate);cancelled.ctx.cancelApprovalReceipts();await cancelWait;assert.equal(cancelled.calls.length,1,'Navigation cancels pending start');
 const busy=harness();busy.state.set(busy.run.run,{});await busy.ctx.approveAndBuild(busy.run);assert.equal(busy.calls.length,0);
 // Exercise the actual toolbar callback and action sender. First build carries
 // all three revision pins; a later resume keeps the legacy continuation shape.
 const nodes=new Map(),posts=[];const node=id=>{if(!nodes.has(id))nodes.set(id,{hidden:false,disabled:false,textContent:'',classList:{toggle(){}},setAttribute(){},removeAttribute(){}});return nodes.get(id);};
 const initial={run:'/fixture/first',workspace:'/fixture',goal_token:'r7:abc',goal:{revision:7,hash:'abc',approval_status:'approved',approval_event:{token:'r7:abc'}},stages:[],monitor:{}};
 const sender=vm.createContext({$:node,primaryAction:r=>({kind:'continue',label:r.stages.length?'Resume task':'Start building'}),taskSentence:()=>'',taskActionBusy:()=>false,taskArchiveBlocked:()=>false,projectBlocked:()=>false,currentTab:'now',taskChatPending:new Set(),sendingRequests:new Set(),requestKey:()=>'',taskReadError:'',latestRun:initial,taskActionPending:new Set(),seq:0,chosen:initial,
   renderLiveControls(){},renderBrief(){},renderExecution(){},renderTaskNow(){},renderInlineTaskAction(){},dashboardNotice:m=>{throw Error(m);},post:async(endpoint,payload)=>{posts.push(payload);return {status:'queued'};}});
 vm.runInContext(approvalSource,sender);
 vm.runInContext(source.slice(source.indexOf('function renderPrimaryAction('),source.indexOf('function modelCatalogueSnapshot(')),sender);
 sender.renderPrimaryAction(initial);node('#continue-run').onclick();await new Promise(setImmediate);
 assert.equal(posts[0].token,'r7:abc');assert.equal(posts[0].confirmation,'r7:abc');assert.equal(posts[0].expected_goal_token,'r7:abc');
 await sender.submitTaskAction(initial,'continue',{expected_goal_token:'r7:abc'});assert.equal(posts[1].confirmation,'r7:abc','Standalone plan/card build records the named confirmation');
 const resumed={...initial,stages:[{stage:'terra'}]};sender.latestRun=resumed;sender.renderPrimaryAction(resumed);node('#continue-run').onclick();await new Promise(setImmediate);assert.equal(posts[2].expected_goal_token,undefined,'Legacy resume remains distinct from first build');
 const clearedStart={...initial,run:'/fixture/cleared',goal_token:''};sender.latestRun=clearedStart;sender.renderPrimaryAction(clearedStart);node('#continue-run').onclick();await new Promise(setImmediate);assert.equal(posts[3].expected_goal_token,'r7:abc','Standalone Start building uses the saved approved revision after its display token clears');
 console.log('Continuous-chat exact approval, revision races, uncertain start and duplicate controls passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
