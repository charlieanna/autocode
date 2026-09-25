const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const source=fs.readFileSync(path.join(__dirname,'..','dashboard_app.js'),'utf8');
const script=source.slice(source.indexOf('/* Session checkpoints:'),source.indexOf('function renderConversation('));
const element=(tag,value='')=>({tag,value:String(value??''),children:[],dataset:{},hidden:false,disabled:false,
  textContent:'',title:'',className:'',append(...children){this.children.push(...children);},
  setAttribute(name,val){this['attr:'+name]=String(val);},focus(){},scrollIntoView(){}});
const button=(label,onclick,className='')=>({...element('button',label),label,onclick,className});
const composer={...element('textarea'),value:'',oninput:null};
const saved=new Map();
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
  activateTab:()=>{},
});
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
assert.equal(list.children.length,5);
const built1=list.children[1],toggle=built1.children[0],detail=built1.children[1];
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

// Restore drafts an explicit request in the composer; it never mutates directly.
const restore=detail.children.find(child=>typeof child.onclick==='function'&&child.label==='Restore to here');
const openChanges=detail.children.find(child=>typeof child.onclick==='function'&&child.label==='Open saved changes →');
assert.ok(restore&&openChanges);
restore.onclick();
assert.match(composer.value,/Restore the workspace to the saved checkpoint/);
assert.match(composer.value,/candidate 1, iteration 1/);
assert.match(composer.value,/source revision aaa123bbb/);
assert.equal(saved.get('task-draft:'+base.run),composer.value);
assert.equal(restore.disabled,true);
assert.match(restore.textContent,/drafted below/);
// A completed task offers no restore path.
const doneHost=context.renderSessionCheckpoints(done,finished);
const doneBuilt=doneHost.children.at(-1).children[1].children[1];
assert.equal(doneBuilt.children.find(child=>child.label==='Restore to here'),undefined);

console.log('Session checkpoint derivation, inspection answers, and restore drafting passed.');
