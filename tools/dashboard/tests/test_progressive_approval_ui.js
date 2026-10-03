// Run from the repository root: node tools/dashboard/tests/test_progressive_approval_ui.js
// Execute shipped rendering and action routing; only DOM and network I/O are fake.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('./dashboard_vm');
const {execFileSync}=require('node:child_process');
const projectedRun=require('./resolver_fixture');
const root=path.resolve(__dirname,'../../..');
const python=fs.existsSync(path.join(root,'.venv/bin/python'))?path.join(root,'.venv/bin/python'):'python3';
const source=fs.readFileSync(path.join(__dirname,'../dashboard_app.js'),'utf8');
function range(start,end){
  const first=source.indexOf(start),last=source.indexOf(end,first);
  assert.ok(first>=0&&last>first,`Missing shipped function range: ${start}`);
  return source.slice(first,last);
}

// Generate disclosure anew, before the real Resolver fixture hashes the body.
// No implementation_sequence, saved details state or nested UI-only map is supplied.
const proposal={version:1,needed_because:'Lessons, exercises and recommendations need useful end-to-end slices.',
  shared_decisions:['Persist progress in the existing repository storage.'],outstanding_criteria:['C3'],done_slices:[],slices:[
    {id:'S1',intended_result:'Open a lesson, answer an exercise and persist progress',criterion_ids:['C1'],
      depends_on:[],paths:['src/lessons','tests/lessons'],tentative:false,
      checks:[{id:'K1',relation:'contributes_to',criterion_ids:['C1'],method:'python -m unittest tests.test_lessons'}]},
    {id:'S2',intended_result:'Recommend the next lesson from saved progress',criterion_ids:['C2'],
      depends_on:['S1'],paths:['src/recommendations','tests/recommendations'],tentative:true,
      checks:[{id:'K2',relation:'fully_verify',criterion_ids:['C2'],method:'python -m unittest tests.test_recommendations'}]},
  ]};
const generated=JSON.parse(execFileSync(python,['-B','-c',String.raw`
import json,sys
sys.path.insert(0,sys.argv[1])
from tools.autocode_progressive_plan import disclosure,plan_identity
proposal=json.load(sys.stdin)
print(json.dumps({'fields':disclosure(proposal,['C1','C2','C3']),'identity':plan_identity(proposal)}))
`,root],{input:JSON.stringify(proposal),encoding:'utf8',timeout:15000}));
const retirement='Progressive check retirement: '+JSON.stringify({check_hash:'a'.repeat(64),
  check_id:'legacy-author-preview',removes:'Replace the old preview demonstration with repository-backed author publishing'});
const run=projectedRun('goal_approval',{workspace:'/fixture/project',run:'/fixture/progressive',
  model_settings:{joint_planning:true},goal:{revision:1,origin:'astra_finalize',body:{
    intended_outcome:'Deliver lessons, exercises, recommendations and authoring.',
    constraints:['Keep the existing storage.',...generated.fields.constraints,retirement],
    scope_exclusions:[retirement],
    technical_approach:generated.fields.technical_approach,
    acceptance_criteria:[{id:'C1',criterion:'Lessons and exercises persist progress'},
      {id:'C2',criterion:'Recommendations use progress'},{id:'C3',criterion:'Authors publish lessons'}],
  }}});
class Element{
  constructor(tag='div',text=''){this.tagName=tag.toUpperCase();this.textContent=text;this.children=[];this.dataset={};this.open=false;this.disabled=false;this.classList={add:()=>{}};}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  get childElementCount(){return this.children.length;}
}
const flatten=node=>[node,...node.children.flatMap(flatten)];
const visible=node=>[node,...(node.tagName==='DETAILS'&&!node.open?[]:node.children.flatMap(visible))];
const buttons=node=>flatten(node).filter(row=>row.tagName==='BUTTON');
function harness(){
  const host=new Element(),planHost=new Element(),calls=[],notices=[];
  const nodes=new Map([['#inline-task-action',host],['#brief-current',planHost]]);
  const context=vm.createContext({console,Date,URLSearchParams,
    $:selector=>nodes.get(selector)||new Element(),n:(tag,text)=>new Element(tag,text),card:()=>new Element(),
    button:(label,onclick)=>Object.assign(new Element('button',label),{onclick}),
    document:{createElement:tag=>new Element(tag)},detailsState:new Map(),
    renderDocument:value=>new Element('pre',JSON.stringify(value)),human:value=>String(value),
    taskArchiveBlocked:()=>false,projectBlocked:()=>false,interruptedAttempt:()=>null,
    renderLiveControls(){},renderExecution(){},renderTaskNow(){},
    dashboardNotice:message=>notices.push(message),refresh:async()=>{},
    latestRun:run,chosen:{run:run.run},taskReadError:'',seq:0,
    taskActionPending:new Set(),taskChatPending:new Map(),
    post:async(url,payload)=>{calls.push({url,payload:JSON.parse(JSON.stringify(payload))});return {id:'approval-1',status:'queued'};},
  });
  vm.runInContext(range('function conversationStatus(', 'function icon(')
    +range('function focusKey(', 'function captureControls(')
    +range('function disclosure(', 'async function api(')
    +range('function concise(', 'function badge(')
    +range('function jointPlanning(', 'function setView(')
    +range('const approveBuildState=', 'function currentConversationDraft(')
    +range('function approveBuildStatusLine(', 'function renderInlineTaskAction(')
    +range('function renderInlineTaskAction(', 'function planEntryText(')
    +range('function planEntryText(', 'function output(')
    +range('function workRequirementRows(', 'function renderTaskNow(')
    +range('function requestKey(', 'function taskSentence(')
    +range('async function submitTaskAction(', 'function modelCatalogueSnapshot('),context);
  context.renderBrief(run);
  context.renderInlineTaskAction(run);
  return {context,host,planHost,calls,notices};
}
function approval(h,label){
  const button=buttons(h.host).find(row=>row.textContent===label);
  assert.ok(button,`Missing shipped approval button: ${label}`);assert.equal(button.disabled,false);
  return button;
}
const envelope={resolver_request:run.human_escalation.request_id,resolver_token:run.human_escalation.request_token};
const approvalPayload={workspace:run.workspace,run:run.run,action:'approve_goal',...envelope,
  token:run.goal_token,confirmation:run.goal_token};
function approvedRun(){return {...run,status:'READY',human_request_authorized:false,human_escalation:null,
  goal:{...run.goal,approval_status:'approved',approval_event:{token:run.goal_token,actor:'user'}},
  actions:[{id:'approval-1',status:'finished',exit_status:0}]};}

async function tests(){
  const h=harness(),shown=visible(h.planHost);
  assert.equal(h.calls.length,0,'Rendering never submits approval');
  assert.ok(flatten(h.planHost).some(row=>row.tagName==='DETAILS'),'Exercise the real collapsed detail panels');
  assert.ok(flatten(h.planHost).filter(row=>row.tagName==='DETAILS').every(row=>row.open===false),'Fresh state has no open details');
  assert.equal(run.goal.body.implementation_sequence,undefined);
  assert.match(generated.fields.constraints[0],new RegExp(generated.identity));
  assert.ok(shown.some(row=>row.tagName==='LI'&&row.textContent===retirement),
    'A proposed check retirement is visible before approval without opening complete-plan details');
  for(const [field,heading] of [['constraints','Constraints'],['technical_approach','Implementation sequence']]){
    const section=shown.find(row=>row.children.some(child=>child.tagName==='H3'&&child.textContent===heading));
    assert.ok(section,`${heading} must be outside closed details`);
    for(const line of generated.fields[field]){
      assert.ok(visible(section).some(row=>row.tagName==='LI'&&row.textContent===line),
        `${field} generated disclosure must be visible outside closed details: ${line}`);
    }
  }
  for(const fragment of ['Product changes, new permissions','2 plan-review calls','5400 stage-seconds','43200',
    'entire cumulative required-check set','S1','S2','criteria C1','criteria C2','depends on S1',
    'K1 [python -m unittest tests.test_lessons]','K2 [python -m unittest tests.test_recommendations]','C3']){
    assert.ok(shown.some(row=>String(row.textContent).includes(fragment)),`Missing visible delegation/map obligation: ${fragment}`);
  }
  const original=JSON.stringify(run),approve=approval(h,'Approve plan revision 1');
  await approve.onclick();
  assert.deepEqual(h.calls,[{url:'/api/action',payload:approvalPayload}]);
  assert.equal(JSON.stringify(run),original,'Presentation/approval must not rewrite the original goal token or body');
  h.context.latestRun={...run,goal_token:'r2:changed'};
  await approve.onclick();assert.equal(h.calls.length,1,'Old DOM button cannot approve a newer token');
  h.context.latestRun={...run,human_escalation:{...run.human_escalation,request_token:'changed'}};
  await approve.onclick();assert.equal(h.calls.length,1,'Old DOM button cannot acquire a newer Resolver envelope');

  const combined=harness(),fresh=approvedRun();
  combined.context.api=async()=>({...run,actions:[{id:'approval-1',status:'running'}]});
  const building=approval(combined,'Approve & build revision 1').onclick();
  await new Promise(setImmediate);
  assert.deepEqual(combined.calls,[{url:'/api/action',payload:approvalPayload}],
    'Queued approval cannot authorize build before a finished approval receipt');
  combined.context.latestRun=fresh;
  combined.context.settleApprovalReceipt(fresh);
  await building;
  assert.deepEqual(combined.calls,[{url:'/api/action',payload:approvalPayload},{url:'/api/action',payload:{
    workspace:run.workspace,run:run.run,action:'continue',token:run.goal_token,
    confirmation:run.goal_token,expected_goal_token:run.goal_token,
  }}],'Approve-and-build remains two distinct requests for the same authorized goal');
  combined.context.renderBrief(fresh);
  combined.context.renderInlineTaskAction(fresh);
  assert.equal(buttons(combined.host).some(row=>/^Approve/.test(row.textContent)),false,
    'Approved contracts must not acquire a second approval action');

  const changed=harness();
  changed.context.api=async()=>({...fresh,goal_token:'r2:changed'});
  await approval(changed,'Approve & build revision 1').onclick();
  assert.deepEqual(changed.calls,[{url:'/api/action',payload:approvalPayload}],
    'A changed goal after approval must not start a build');
  assert.match(changed.notices.join(' '),/plan changed/);
  console.log('Shipped progressive disclosure, ordinary approval envelope/token, stale rejection and separate approve/build actions passed.');
}
tests().catch(error=>{console.error(error);process.exitCode=1;});
