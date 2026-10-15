const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('./dashboard_vm');

const source=fs.readFileSync(path.join(__dirname,'..','dashboard_app.js'),'utf8');
const script=source.slice(source.indexOf('/* Session checkpoints:'),source.indexOf('function renderConversation('));
const element=(tag,value='')=>({tag,value:String(value??''),children:[],dataset:{},hidden:false,disabled:false,
  textContent:'',title:'',className:'',append(...children){this.children.push(...children);},
  setAttribute(name,val){this['attr:'+name]=String(val);},focus(){},scrollIntoView(){}});
const button=(label,onclick,className='')=>({...element('button',label),label,onclick,className});
const composer={...element('textarea'),value:'',oninput:null};
const saved=new Map();
const checkpointSummaries=[],checkpointNavigation=[];
let phoneCheckpoint=false;
const context=vm.createContext({
  Date,JSON,
  $:()=>composer,
  n:element,
  card:(text,className='')=>({...element('div',text),className}),
  button,
  focusKey:(el,key)=>{el.dataset.focusKey=key;return el;},
  basename:value=>String(value).split('/').at(-1),
  concise:(text,limit=180)=>{const value=String(text||'').replace(/\s+/g,' ').trim();return value.length>limit?value.slice(0,limit-1)+'…':value;},
  stageSucceeded:stage=>!stage.rejected&&!stage.abandoned&&!stage.interrupted&&!stage.timed_out&&(stage.exit_code===0||(stage.stage==='orchestrator'&&stage.runner_owned===true&&stage.exit_code==null&&!!stage.finished_at)),
  taskActionBusy:()=>false,
  changeDrafts:new Map(),
  stored:(key,fallback='')=>saved.get(key)??fallback,
  persist:(key,value)=>saved.set(key,value),
  resizeComposer:()=>{},
  document:{querySelectorAll:()=>checkpointSummaries},
  matchMedia:()=>({matches:phoneCheckpoint}),
  openDetailsDrawer:()=>checkpointNavigation.push('drawer'),
  activateTab:tab=>checkpointNavigation.push(tab),
});
vm.runInContext(source.slice(source.indexOf('function messageTime('),source.indexOf('\n',source.indexOf('function messageTime('))),context);
vm.runInContext(script,context);

const stage=(name,iteration,at,extra={})=>({stage:name,role:name.split('_')[0],iteration,finished_at:at,exit_code:0,started_at:at,...extra});
const base={run:'/repo/.autocode/runs/demo',workspace:'/repo',task:'Demo',status:'PAUSED_INTERVENTION',
  goal:{approval_status:'approved',revision:4,approval_event:{actor:'You',at:'2026-09-20T10:00:00Z'},updated_at:'2026-09-20T10:00:00Z'},
  stages:[
    stage('terra',1,'2026-09-20T11:00:00Z',{changed_files:['src/a.js','src/b.js'],diff_ref:'x.diff',source_revision:'aaa123bbb'}),
    stage('terra',2,'2026-09-20T12:00:00Z',{changed_files:['src/c.js'],diff_ref:'y.diff',source_revision:'ccc123ddd'}),
    stage('sol',2,'2026-09-20T13:00:00Z',{diff_ref:'z.diff',source_revision:'ccc123ddd'}),
    stage('terra',3,'2026-09-20T14:00:00Z',{rejected:true}),
    stage('astra_review',3,'2026-09-20T15:00:00Z',{diff_ref:'w.diff'}),
  ],
  validation:{source_revision:'ccc123ddd',verdict:'PASS',criterion_results:[]},
  counts:{pass:3,fail:0,unknown:1},
  monitor:{findings_summary:{open:1,resolved:2,repeated:0,not_rechecked:0},findings:[{id:'F-1',finding:'Button contrast too low on mobile'}]}};

// Derivation: plan approval plus one built and one review checkpoint per
// iteration; a rejected build never becomes a candidate.
const checkpoints=context.sessionCheckpoints(base);
assert.deepEqual([...checkpoints.map(cp=>cp.id)],['plan','built-i1','built-i2','review-i2','review-i3']);
assert.deepEqual([...checkpoints.map(cp=>cp.label)],['Plan approved','Build completed','Build completed','Validation review','Completion review']);
assert.deepEqual([...checkpoints.map(cp=>cp.candidate)],[0,1,2,2,2]);
assert.equal(checkpoints[1].stageIndex,0);
assert.equal(checkpoints[4].stageIndex,4);
assert.deepEqual([...checkpoints[1].changedFiles],['src/a.js','src/b.js']);
assert.equal(checkpoints[1].sourceRevision,'aaa123bbb');

// Completion adds the final candidate checkpoint, stamped by the saved completion time.
const done={...base,status:'TASK_COMPLETE',completed_at:'2026-09-20T16:00:00Z'};
const finished=context.sessionCheckpoints(done);
assert.equal(finished.at(-1).id,'final');
assert.equal(finished.at(-1).label,'Final candidate');
assert.equal(finished.at(-1).candidate,2);
assert.equal(finished.at(-1).at,'2026-09-20T16:00:00Z');

// A late re-approval keeps checkpoints in recorded time order.
const reapproved={...base,goal:{...base.goal,approval_event:{actor:'You',at:'2026-09-20T12:30:00Z'},updated_at:'2026-09-20T12:30:00Z'}};
assert.deepEqual([...context.sessionCheckpoints(reapproved).map(cp=>cp.id)],['built-i1','built-i2','plan','review-i2','review-i3']);

// Nothing meaningful saved yet: no checkpoints and no timeline section.
assert.deepEqual([...context.sessionCheckpoints({run:'x',stages:[],goal:{}})],[]);
assert.equal(context.renderSessionCheckpoints({run:'x',status:'RUNNING'},[]),null);

// Rendering: each checkpoint opens in place and answers the four questions
// from saved state only.
const host=context.renderSessionCheckpoints(base,checkpoints);
const list=host.children.at(-1);
assert.equal(list.className,'checkpoint-list');
assert.equal(list.value,'');
assert.equal(list.children.length,5);
const built1=list.children[1],toggle=built1.children[0],detail=built1.children[1];
assert.equal(built1.className,'checkpoint-item checkpoint-built');
assert.equal(built1.value,'');
assert.equal(detail.hidden,true);
assert.equal(toggle['attr:aria-expanded'],'false');
toggle.onclick();
assert.equal(detail.hidden,false);
assert.equal(toggle['attr:aria-expanded'],'true');
toggle.onclick();
assert.equal(detail.hidden,true);
const answers=detail.children[0];
assert.deepEqual([...answers.children.filter(child=>child.tag==='dt').map(child=>child.value)],
  ['What changed?','What was verified?','What findings existed?','Which candidate was this?']);
const answer=index=>answers.children.filter(child=>child.tag==='dd')[index].value;
assert.equal(answer(0),'src/a.js · src/b.js');
assert.match(answer(1),/No independent verification report is saved for this candidate/);
assert.match(answer(2),/Earlier per-candidate finding history is not saved/);
assert.equal(answer(3),'Candidate 1 · iteration 1 · source aaa123bbb.');
// The latest review carries the saved findings ledger; a checkpoint whose step
// recorded no source revision honestly reports no saved verification.
const latest=list.children[4].children[1];
const latestAnswers=latest.children[0].children.filter(child=>child.tag==='dd').map(child=>child.value);
assert.match(latestAnswers[1],/No independent verification report is saved for this candidate/);
assert.match(latestAnswers[2],/1 open · 2 resolved/);
assert.match(latestAnswers[2],/F-1 · Button contrast too low on mobile/);
// Checkpoints whose source revision matches the saved verification report it.
const reviewed=list.children[3].children[1];
const reviewedAnswers=reviewed.children[0].children.filter(child=>child.tag==='dd').map(child=>child.value);
assert.match(reviewedAnswers[1],/verdict PASS/);
assert.match(reviewedAnswers[1],/3 criteria passed/);
// Built checkpoint two matches the saved verification revision.
const built2Answers=list.children[2].children[1].children[0].children.filter(child=>child.tag==='dd').map(child=>child.value);
assert.match(built2Answers[1],/verdict PASS/);
// Plan and diff-less checkpoints expose only their answers.
const plan=list.children[0].children[1];
assert.equal(plan.children.length,1);

// Historical metadata has no immutable source snapshot; it offers inspection only.
assert.equal(detail.children.find(child=>child.label==='Restore to here'),undefined);
assert.ok(detail.children.find(child=>child.label==='Open saved changes →'));
// A completed task offers no restore path.
const doneHost=context.renderSessionCheckpoints(done,finished);
const doneBuilt=doneHost.children.at(-1).children[1].children[1];
assert.equal(doneBuilt.children.find(child=>child.label==='Restore to here'),undefined);

// History is read-only inspection: the complete saved timeline remains there,
// while real source restoration is a separate, source-bound chat action.
const inspection=context.renderSessionCheckpoints(base,checkpoints,false);
const allNodes=node=>[node,...(node.children||[]).flatMap(allNodes)];
assert.equal(allNodes(inspection).filter(node=>String(node.className).includes('checkpoint-restore')).length,0);
assert.equal(inspection.children.at(-1).children.length,checkpoints.length);
console.log('Session checkpoint derivation, inspection answers, and truthful historical inspection passed.');

// A genuine successful saved build belongs between the actual message times.
// User objects/text are retained; current activity is not a checkpoint.
const transcript=[
 {id:'user',role:'user',text:'Keep every word <script>unchanged</script>',created_at:'2026-09-20T10:30:00Z'},
 {id:'builder',role:'assistant',speaker:'Builder',text:'Saved build update',created_at:'2026-09-20T10:45:00Z'},
 {id:'tester',role:'assistant',speaker:'Tester',text:'Testing the candidate',created_at:'2026-09-20T11:15:00Z'},
];
const merged=context.conversationTimeline(transcript,[checkpoints[1],{...checkpoints[2],at:''}]);
assert.deepEqual([...merged.map(item=>item.saved_checkpoint?.id||item.id)],['user','builder','built-i1','tester']);
assert.strictEqual(merged[0],transcript[0]);assert.strictEqual(merged[1],transcript[1]);assert.strictEqual(merged[3],transcript[2]);
assert.equal(transcript.length,3,'rendering must not rewrite the saved transcript');
const noBuilt=context.sessionCheckpoints({...base,stages:[stage('terra',9,'2026-09-20T11:00:00Z',{rejected:true}),stage('terra',10,'',{source_revision:'saved-but-no-time'})]});
assert.deepEqual([...context.conversationTimeline(transcript,noBuilt).map(item=>item.id)],['user','builder','tester']);
const recovering=context.conversationTimeline(transcript,[checkpoints[1]],[{recovery_notice:true,at:'2026-09-20T11:05:00Z'}]);
assert.deepEqual([...recovering.map(item=>item.saved_checkpoint?.id||(item.recovery_notice?'recovery':item.id))],['user','builder','built-i1','recovery','tester']);
const compact=context.savedCheckpointCard(base,checkpoints[1]);
assert.equal(compact.dataset.savedCheckpoint,'built-i1');
assert.match(compact.children[1].value,/Saved revision aaa123bb/);
assert.doesNotMatch(compact.children.map(item=>item.value).join(' '),/PASS|accepted|verified/,'a build checkpoint cannot fabricate independent acceptance');
// The lookup must retain the exact focus key, including its NUL separator.
// Replacing it with the CSS replacement character targets a different node.
const exactKey='details:'+base.run+'\0diff-stage:'+checkpoints[1].stageIndex;
let checkpointScrolled=0;
const matchingDiff={open:false,scrollIntoView(){checkpointScrolled++;}},replacementDiff={open:false};
checkpointSummaries.push({dataset:{focusKey:exactKey.replace('\0','\uFFFD')},parentElement:replacementDiff},
  {dataset:{focusKey:exactKey},parentElement:matchingDiff});
compact.onclick();
assert.deepEqual(checkpointNavigation,['changes'],'desktop checkpoint inspection selects Changes without a drawer');
assert.equal(matchingDiff.open,true);assert.equal(checkpointScrolled,1);
assert.equal(replacementDiff.open,false,'a differently encoded focus key never receives expansion');
phoneCheckpoint=true;checkpointNavigation.length=0;matchingDiff.open=false;
compact.onclick();
assert.deepEqual(checkpointNavigation,['drawer','changes'],'phone drawer opens before the matching diff is revealed');
assert.equal(matchingDiff.open,true);assert.equal(checkpointScrolled,2);
checkpointNavigation.length=0;matchingDiff.open=false;
context.savedCheckpointCard(base,{...checkpoints[1],hasDiff:false}).onclick();
assert.deepEqual(checkpointNavigation,['drawer','overview'],'a phone checkpoint without a saved diff opens History');
assert.equal(matchingDiff.open,false,'History fallback never expands an unrelated diff');
console.log('Chronological genuine checkpoint rendering retains user content and refuses rejected/unstamped boundaries.');

// Numeric transcript timestamps use the same seconds/milliseconds rule as
// rendered message time, so saved boundaries do not jump ahead of messages.
const numeric=[{id:'seconds',role:'user',created_at:Date.parse('2026-09-20T10:30:00Z')/1000},
 {id:'milliseconds',role:'assistant',created_at:Date.parse('2026-09-20T11:15:00Z')}];
const numericTimeline=context.conversationTimeline(numeric,[checkpoints[1]]);
assert.deepEqual([...numericTimeline.map(item=>item.saved_checkpoint?.id||item.id)],['seconds','built-i1','milliseconds']);
assert.strictEqual(numericTimeline[0],numeric[0]);assert.strictEqual(numericTimeline[2],numeric[1]);
assert.equal(context.eligibleConversationCheckpoint({...checkpoints[1],at:'not-a-timestamp'}),false);
assert.equal(context.eligibleConversationCheckpoint({...checkpoints[1],at:''}),false);
assert.equal(context.eligibleConversationCheckpoint({...checkpoints[1],at:NaN}),false);
assert.equal(context.eligibleConversationCheckpoint(checkpoints[1]),true);

// A recent final/recovery transcript does not acquire older saved build
// boundaries. The original saved records remain available to History/Changes.
const recentFinal=[{id:'final-tester',role:'assistant',created_at:'2026-09-20T13:00:00Z'},
 {id:'completion-reviewer',role:'assistant',created_at:'2026-09-20T15:00:00Z'}];
assert.deepEqual([...context.conversationTimeline(recentFinal,[checkpoints[1],checkpoints[2]]).map(item=>item.id)],['final-tester','completion-reviewer']);
assert.strictEqual(context.conversationTimeline(recentFinal,checkpoints)[0],recentFinal[0]);
assert.ok(context.sessionCheckpoints(base).some(cp=>cp.id==='built-i1'),'Earlier checkpoints remain in saved History');
const recentRecovery=[{id:'latest-builder',role:'assistant',created_at:'2026-09-20T12:28:00Z'},
 {id:'latest-resolver',role:'assistant',created_at:'2026-09-20T12:30:00Z'}];
assert.deepEqual([...context.conversationTimeline(recentRecovery,[checkpoints[1],checkpoints[2]],
 [{recovery_notice:true,at:'2026-09-20T12:29:00Z'}]).map(item=>item.recovery_notice?'recovery':item.id)],['latest-builder','recovery','latest-resolver']);
// An invalid/absent first timestamp or no transcript invents no lower bound.
for(const at of [undefined,'', 'not-a-timestamp', NaN]){
 const unknown=[{id:'unknown-first',role:'user',created_at:at},transcript.at(-1)];
 const timeline=context.conversationTimeline(unknown,[checkpoints[1]]);
 assert.ok(timeline.some(item=>item.saved_checkpoint?.id==='built-i1'));
 assert.strictEqual(timeline[0],unknown[0]);assert.strictEqual(timeline.at(-1),unknown[1]);
}
assert.equal(context.conversationTimeline([],[checkpoints[1]])[0].saved_checkpoint.id,'built-i1');
console.log('Recorded final/recovery transcript intervals retain all messages and preserve older saved checkpoints in History.');

// A genuine new saved build is visible immediately after its Builder message;
// no later Tester message is needed to invent an upper time boundary.
const justBuilt=[{id:'builder-before-save',role:'assistant',speaker:'Builder',created_at:'2026-09-20T12:24:00Z'}];
const justSaved={...checkpoints[2],at:'2026-09-20T12:25:00Z'};
const afterBuilder=context.conversationTimeline(justBuilt,[justSaved]);
assert.deepEqual([...afterBuilder.map(item=>item.saved_checkpoint?.id||item.id)],['builder-before-save','built-i2']);
assert.strictEqual(afterBuilder[0],justBuilt[0]);assert.strictEqual(afterBuilder[1].saved_checkpoint,justSaved);
